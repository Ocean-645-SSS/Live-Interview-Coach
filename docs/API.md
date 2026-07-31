# LiveRAG API 文档

本文档面向前端联调。默认只访问管理 API 单端口：

```text
http://127.0.0.1:9821
```

内部 RAG Core Service 的 `/v1/*` 只给后端使用，前端不要直接调用。

## 通用约定

RAG、模型配置、history、overview 接口使用统一 envelope：

```json
{
  "request_id": "...",
  "status": "ok",
  "data": {},
  "metrics": {},
  "error": null
}
```

失败时：

```json
{
  "request_id": "...",
  "status": "error",
  "data": null,
  "metrics": {},
  "error": {"type": "ErrorType", "message": "错误说明"}
}
```

Prompt 和 session 读取接口保持轻量返回，不包 envelope。

## 核心规则

- 一次语音通话只锁定一个 `kb_id`。
- 通话开始后不能切换知识库。
- 不支持多知识库同时查询，不支持 `kb_ids`。
- 通话开始前一次性渲染 `session_system_prompt.md`。
- 通话中只使用固定 instructions、当前 messages、当前用户输入和可选 tool result。
- 挂断后把本次 messages 压缩为当前知识库的一条 `history.jsonl`，随后清空 messages。
- `rag_tool_mode` 只支持 `auto` 和 `never`。

## 健康与运行态

### GET /health

返回管理 API 是否可用。

```json
{"status":"ok"}
```

### GET /runtime/state

返回当前运行状态、当前知识库、RAG 模式、最近回答长度和 active session。

关键字段：

```json
{
  "active_session": {
    "started_at": "...",
    "ended_at": null,
    "job_id": "AJ_xxx",
    "room_id": "RM_xxx",
    "voice": {},
    "knowledge_base": {"kb_id": "kb_xxx", "name": "考研资料"},
    "session_prompt_chars": 4200,
    "history_count": 6,
    "knowledge_overview": {"generated": false, "fallback": false}
  },
  "rag_tool_mode": "auto",
  "last_assistant_chars": 80,
  "last_tts_text_chars": 80,
  "last_answer_too_long": false,
  "knowledge_base": {
    "configured": {"kb_id": "kb_xxx", "name": "考研资料"},
    "active_session": {"kb_id": "kb_xxx", "name": "考研资料"},
    "locked": true,
    "pending_reconnect": false
  }
}
```

## 语音模型配置

语音模型配置写入 `~/.LiveRAG/model/config.json`。修改后当前通话不热切，挂断重连后生效。

### GET /model/config

读取下次通话使用的 STT、LLM、TTS 配置，并返回前端模型选择需要的 `options`。密钥只返回掩码和是否已设置。

STT 和 TTS 的固定 endpoint 不返回给前端。前端只展示后端返回的 provider、model、voice 和 provider 自己的配置字段。

### GET /model/options

只读取模型选择页选项，不读取当前配置。

返回内容：

- `stt.providers`：已适配的语音识别 provider。
- `tts.providers`：已适配的语音合成 provider。
- 每个 provider 都包含 `models`、`voices`、`config_fields`、默认值和 `verified` 标记。
- `voices[]` 固定包含 `id`、`label`、`verified`，并尽量包含 `name`、`description`、`language`、`description_source`。
- `description_source=official` 表示描述来自 provider 官方文档或官方 API；`derived` 表示官方没有返回该 voice 的描述，后端只做可读化说明。
- `llm` 保持手动配置模式，继续使用 `model`、`base_url`、`api_key`。

`voices[]` 示例：

```json
{
  "id": "Cherry",
  "label": "芊悦（Cherry）",
  "verified": true,
  "name": "芊悦",
  "description": "阳光积极、亲切自然小姐姐（女性）",
  "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
  "description_source": "official"
}
```

### PUT /model/config

局部更新语音模型配置。

```json
{
  "voice": {
    "stt": {
      "provider": "volcengine_bigmodel",
      "model": "bigmodel",
      "app_id": "...",
      "access_token": "..."
    },
    "tts": {
      "provider": "dashscope_realtime",
      "model": "qwen3-tts-flash-realtime",
      "voice": "Cherry",
      "api_key": "sk-..."
    },
    "llm": {
      "model": "gemma-4-e4b-it-4bit",
      "base_url": "http://127.0.0.1:8000/v1",
      "api_key": "385496906Qwe"
    }
  }
}
```

前端如果回填后端返回的 `sk*****abcd1234` 掩码值，原样提交不会覆盖真实密钥。

当前后端已适配的 STT provider：

- `volcengine_bigmodel`：model 只返回 `bigmodel`。

`voice.tts.provider` 可选：

- `minimax`：默认 MiniMax `speech-02-turbo` 链路。
- `dashscope_realtime`：阿里 DashScope `qwen3-tts-flash-realtime` WebSocket 链路。

TTS model 和 voice 必须从 `/model/options` 返回列表中选择。提交不在列表里的 model 或 voice 会返回 `422`。

切换 TTS provider 不会热切当前通话，用户挂断后重新接通生效。

### GET /model/effective-state

返回下次通话配置、当前或最近通话实际生效配置、是否需要重连。

## Context Model 配置

Context Model 独立于语音 LLM，用于：

- 生成 `knowledge_overview.md`。
- 挂断后压缩 `history.jsonl`。

配置写入 `~/.LiveRAG/model/context_config.json`。

### GET /model/context-config

响应：

```json
{
  "status": "ok",
  "data": {
    "context_model": {
      "model": "qwen-max",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": "sk*****abcd1234",
      "api_key_masked": "sk*****abcd1234",
      "api_key_set": true,
      "temperature": 0,
      "max_tokens": 2000,
      "max_session_chars": 16000,
      "history_reference_limit": 8,
      "timeout_ms": 15000,
      "effective": "next_session"
    }
  }
}
```

### PUT /model/context-config

局部更新。密钥掩码原样提交表示不修改密钥。

```json
{
  "model": "qwen-max",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "api_key": "sk-xxx",
  "temperature": 0,
  "max_tokens": 2000,
  "max_session_chars": 16000,
  "history_reference_limit": 8,
  "timeout_ms": 15000
}
```

## Prompt

前端只暴露 `SOUL.md`，不暴露系统提示词模板。`system_prompt_template.md` 是后端内部模板文件，不能通过 API 读取或修改。

### GET /prompt/soul

读取 `~/.LiveRAG/prompts/SOUL.md`。如果文件不存在，后端会初始化默认人设。

```json
{"content":"..."}
```

### PUT /prompt/soul

覆盖用户定义的 Agent 人设。后端不会自动修改 SOUL。

```json
{"content":"新的 SOUL 内容"}
```

## 会话数据

### GET /session/{session_id}/messages

读取当前通话 messages。挂断后后端会清空 messages，因此该接口主要用于通话中调试。

查询参数：

- `limit`: 可选，返回最近 N 条。

消息字段：

```json
{
  "timestamp": "...",
  "role": "user",
  "content": "用户问题",
  "turn_index": 1,
  "metadata": {}
}
```

assistant 消息 metadata：

```json
{
  "char_count": 86,
  "tts_text_chars": 86,
  "tts_text_chars_source": "assistant_text",
  "too_long": false,
  "used_rag": true,
  "rag_tool_mode": "auto"
}
```

### GET /session/{session_id}/rag-context

读取当前通话 RAG 工具调用事实日志。

单条记录示例：

```json
{
  "timestamp": "...",
  "source": "tool",
  "tool_name": "search_knowledge_base",
  "kb_id": "kb_xxx",
  "kb_name": "考研资料",
  "turn_index": 3,
  "query": "Claude 的 Agent Memory 怎么做？",
  "original_query": "Claude 的 Agent Memory 怎么做？",
  "effective_query": "Claude 的 Agent Memory 怎么做？",
  "rewritten": false,
  "hit": true,
  "has_context": true,
  "request_id": "...",
  "metrics": {"latency_ms": 352.8, "cache_hit": false},
  "error": null,
  "context_preview": "【知识库检索上下文】...",
  "evidence_documents": [
    {
      "kb_id": "kb_xxx",
      "kb_name": "考研资料",
      "document_id": "doc_xxx",
      "file_path": "demo.md",
      "title": "demo.md",
      "chunk_count": 2
    }
  ],
  "evidence_chunks": [
    {
      "kb_id": "kb_xxx",
      "kb_name": "考研资料",
      "chunk_id": "chunk_xxx",
      "document_id": "doc_xxx",
      "file_path": "demo.md",
      "tokens": 128,
      "score": 0.82,
      "content_preview": "片段摘要"
    }
  ],
  "evidence_count": 2,
  "no_evidence_reason": null
}
```

### GET /session/turns

按 `turn_index` 聚合 messages 和 RAG 依据。通话中推荐使用，挂断后 messages 会被清空。

`rag.status` 取值：

- `not_queried`
- `hit`
- `miss`
- `failed`

### DELETE /session/{session_id}

清空当前 session messages、rag_context、session_system_prompt 和 runtime state。

## 会话知识库选择

### GET /session/knowledge-base

返回下次通话配置和当前通话锁定知识库。

```json
{
  "configured": {"kb_id": "kb_xxx", "name": "考研资料"},
  "active_session": {"kb_id": "kb_xxx", "name": "考研资料", "locked_at": "..."},
  "locked": true,
  "pending_reconnect": false
}
```

### PUT /session/knowledge-base

设置下次通话使用的知识库。只能在没有 active call 时调用。

```json
{"kb_id":"kb_xxx"}
```

当前通话未结束时返回 `409 KnowledgeBaseLocked`。

## RAG 配置

### GET /rag/config

读取语音链路 RAG 查询配置。

关键字段：

```json
{
  "enabled": true,
  "base_url": "http://127.0.0.1:9721",
  "api_key": "sk*****xxx",
  "api_key_masked": "sk*****xxx",
  "api_key_set": true,
  "query_mode": "naive",
  "timeout_ms": 900,
  "top_k": 4,
  "chunk_top_k": 4,
  "context_max_chars": 1800,
  "cache_ttl_s": 45,
  "enable_rerank": false,
  "rag_tool_mode": "auto"
}
```

### PUT /rag/config

局部更新 RAG 查询配置。

`rag_tool_mode` 只允许：

- `auto`: 通话开始前注入工具规则，并提供 `search_knowledge_base` 工具。
- `never`: 通话开始前注入禁用说明，不提供知识库工具。

提交 `always` 会返回 `422`。

## 知识库管理

### GET /rag/ready

检查内部 RAG Core Service 是否 ready。

### GET /rag/knowledge-bases

返回全部知识库摘要。

```json
{
  "knowledge_bases": [
    {
      "kb_id": "default",
      "name": "默认知识库",
      "description": "",
      "document_count": 3,
      "chunk_count": 24,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total": 1
}
```

### POST /rag/knowledge-bases

创建知识库。

```json
{"name":"考研资料","description":""}
```

### GET /rag/knowledge-bases/{kb_id}

读取单个知识库详情和统计。

### PATCH /rag/knowledge-bases/{kb_id}

更新知识库名称和描述，不重建索引。

### DELETE /rag/knowledge-bases/{kb_id}

删除知识库 metadata、原文件、LightRAG storage 和 logs。

约束：

- `default` 禁止删除。
- 当前通话锁定的知识库禁止删除。

### GET /rag/knowledge-bases/{kb_id}/ready

预热指定知识库 engine。

## 知识库上下文

前端只暴露当前知识库的 `knowledge_overview.md`。`history.jsonl` 是后端内部长期历史文件，不提供前端读取、清空或手动压缩接口。

### GET /rag/knowledge-bases/{kb_id}/context/overview

读取当前知识库固定概览。如果文件不存在，后端会创建默认 `knowledge_overview.md` 和 meta。

```json
{
  "kb_id": "kb_xxx",
  "content": "# 知识库概览\n...",
  "meta": {
    "kb_id": "kb_xxx",
    "updated_at": "...",
    "stale": false,
    "reason": "index_completed",
    "source": "context_model",
    "source_job_id": "insert_xxx"
  }
}
```

### PUT /rag/knowledge-bases/{kb_id}/context/overview

手动覆盖当前知识库的 `knowledge_overview.md`。这不是重新生成；生成时机仍只在索引任务完成后由后端触发。

```json
{"content":"# 知识库概览\n..."}
```

写入后 meta：

```json
{
  "stale": false,
  "reason": "manual_update",
  "source": "manual"
}
```

文档上传、删除、清空后，后端会把该知识库 overview 标记为 `stale=true`。新文件构建完成后会重新生成并写入 `source_job_id`。

## 文档管理

### GET /rag/knowledge-bases/{kb_id}/documents

读取指定知识库文档列表。

查询参数：

- `page`: 默认 `1`
- `page_size`: 默认 `50`

文档字段：

```json
{
  "document_id": "doc_xxx",
  "kb_id": "kb_xxx",
  "kb_name": "考研资料",
  "original_filename": "demo.md",
  "file_path": "demo.md",
  "source_file_path": "...",
  "source_file_exists": true,
  "source_file_size": 1200,
  "source_sha256": "...",
  "content_type": "text/markdown",
  "extension": ".md",
  "parse_status": "parsed",
  "index_status": "processed",
  "status": "processed",
  "chunks_count": 3,
  "content_summary": "",
  "content_length": 1200,
  "error_msg": null,
  "created_at": "...",
  "updated_at": "..."
}
```

### GET /rag/knowledge-bases/{kb_id}/documents/{document_id}

读取文档详情、解析内容、chunks 和原始状态。

### GET /rag/knowledge-bases/{kb_id}/documents/{document_id}/source

读取原文件，用于预览或下载。

查询参数：

- `disposition=inline`: 默认，浏览器内联预览。
- `disposition=attachment`: 下载。

### POST /rag/knowledge-bases/{kb_id}/documents/text

导入文本并保存为原始文本文件。

```json
{
  "text": "内容",
  "file_source": "manual-note.md",
  "document_id": "可选"
}
```

### POST /rag/knowledge-bases/{kb_id}/documents/files

上传一个或多个文件。

multipart 字段：

- `files`: 文件数组。

### GET /rag/knowledge-bases/{kb_id}/jobs/{job_id}

查询文档解析和索引任务状态。

### DELETE /rag/knowledge-bases/{kb_id}/documents/{document_id}

删除单个文档、原文件和派生索引。

### DELETE /rag/knowledge-bases/{kb_id}/documents

清空该知识库全部文档、原文件和索引，但保留知识库本身。

## 查询接口

### POST /rag/knowledge-bases/{kb_id}/query/context

只查询指定知识库上下文，用于调试检索质量。

```json
{
  "query": "xxx",
  "profile": "voice",
  "mode": "naive",
  "top_k": 4,
  "chunk_top_k": 4,
  "include_references": true,
  "include_chunk_content": true,
  "context_max_chars": 1800
}
```

### POST /rag/knowledge-bases/{kb_id}/query/data

只查询指定知识库结构化检索数据。

### POST /rag/session-query/context

按当前 active session 锁定知识库查询上下文。不接收 `kb_id`。

### POST /rag/session-query/data

按当前 active session 锁定知识库查询结构化数据。不接收 `kb_id`。
