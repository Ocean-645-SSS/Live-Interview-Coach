# RAG 说明

## 服务定位

`liverag/rag/` 是基于 `lightrag-hku` Core API 的内置 RAG Core Service，不使用官方 WebUI。Agent 和管理 API 启动时都会自动检查并拉起该服务。

## 存储模型

LiveRAG 使用 SQLite 保存产品元数据，使用每知识库独立目录保存原文件和 LightRAG 派生索引。

```text
~/.LiveRAG/
  liverag.db
  rag/knowledge_bases/
    default/
      sources/{document_id}/{original_filename}
      storage/
      logs/
    kb_xxx/
      sources/{document_id}/{original_filename}
      storage/
      logs/
```

SQLite 只保存元数据，不保存原文件二进制、向量、图谱或 chunk 原文。

## 单知识库隔离

- 每个知识库对应一个独立 LightRAG workspace。
- 一次语音通话只锁定一个 `kb_id`。
- 查询不会同时访问多个知识库。
- 选择知识库发生在通话前；通话中禁止切换。
- 速度依赖预热后的 per-KB engine cache，不做多库 fan-out 或结果合并。

## 上传流程

文件上传后，后端按以下顺序处理：

1. 生成 `document_id`。
2. 保存原文件到 `sources/{document_id}/`。
3. 计算大小、SHA256、扩展名和 content type。
4. 写入 SQLite，状态为 `parse_status=pending`、`index_status=pending`。
5. 解析原文件。
6. 解析成功后写入 LightRAG，进入异步索引队列。
7. 解析或索引失败时保留原文件，并把失败原因写入 `error_msg`。
8. 文档变更成功后，标记 `context/{kb_id}/knowledge_overview_meta.json.stale=true`。

## 知识库概览

每个知识库有一份固定概览：

```text
~/.LiveRAG/context/{kb_id}/knowledge_overview.md
~/.LiveRAG/context/{kb_id}/knowledge_overview_meta.json
```

概览由独立 Context Model 生成，输入包括：

- 知识库 metadata。
- 文档列表。
- LightRAG 提供的 topics、entities、relations、documents overview。
- 当前 RAG 查询参数：`query_mode`、`top_k`、`chunk_top_k`、`context_max_chars`、`enable_rerank`。

概览不会在通话启动时生成，也不提供前端手动触发接口。固定生成时机是：文件索引任务完成，并且至少有一个新文档进入 `processed` 状态。

前端上传后应轮询：

```text
GET /rag/knowledge-bases/{kb_id}/jobs/{job_id}
```

当任务完成时，管理 API 会在后台调用 Context Model 生成 `knowledge_overview.md`，并在 meta 中写入 `source_job_id`。生成失败时写入降级概览，不阻断文档管理和后续通话。

## 语音查询参数

语音链路默认使用低延迟参数：

```bash
LIGHTRAG_QUERY_MODE=naive
LIGHTRAG_TOP_K=4
LIGHTRAG_CHUNK_TOP_K=4
LIGHTRAG_CONTEXT_MAX_CHARS=1800
LIGHTRAG_VOICE_ENABLE_RERANK=false
```

如果需要更高召回，可以提高 `top_k`、`chunk_top_k` 或切换查询模式，但会增加延迟。

## RAG 工具模式

只支持两种：

- `auto`：通话前把工具说明渲染进 `session_system_prompt.md`，并向 LLM 提供 `search_knowledge_base` 工具。
- `never`：通话前写入禁用说明，不向 LLM 提供知识库工具。

不支持强制每轮检索。

## 前端接口

前端只对接管理 API：

```text
GET /rag/knowledge-bases
POST /rag/knowledge-bases
GET /rag/knowledge-bases/{kb_id}/documents
POST /rag/knowledge-bases/{kb_id}/documents/files
POST /rag/knowledge-bases/{kb_id}/documents/text
GET /rag/knowledge-bases/{kb_id}/context/overview
POST /rag/knowledge-bases/{kb_id}/query/context
POST /rag/session-query/context
```

完整字段见 `docs/API.md`。
