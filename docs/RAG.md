# RAG 说明

## 服务定位

`liverag-rag-service` 是知识库的唯一服务入口，负责 LightRAG 的生命周期、文档解析、索引、查询与元数据管理。管理 API 和语音 Agent 都通过 HTTP 调用它，而不直接操作 LightRAG 存储。

## 知识库模型

每个知识库由稳定的 `kb_id` 标识，并拥有独立的：

- 元数据与文档清单；
- 原始上传文件；
- 解析、分块和索引结果；
- 概览（overview）与跨会话历史；
- 查询和会话审计记录。

会话开始后其 `kb_id` 被固定；切换默认知识库只影响之后创建的会话。该约束防止不同知识库的上下文、缓存和长期记忆互相污染。

## 导入流程

1. 通过管理 API 创建知识库。
2. 调用文本导入接口或文件上传接口；文件类型由解析器处理，受密码保护的 PDF 可通过 `pdf_password` 提供密码。
3. RAG Core 创建索引任务，保存原始文件名、来源信息和状态。
4. 调用方轮询 `/rag/knowledge-bases/{kb_id}/jobs/{job_id}`，确认文档可用后再查询。

上传接口返回任务而非“立即可检索”的承诺。任务状态、文档详情和原始文件均可通过管理 API 查询。

## 查询模式

| 接口 | 用途 |
| --- | --- |
| `query/context` | 返回检索上下文和证据，供 Agent 或上层 LLM 使用 |
| `query/data` | 返回结构化命中数据，适合调试、前端展示和审计 |
| `query/answer` | 由 RAG Core 基于检索结果直接生成答案 |
| `session-query/*` | 使用通用语音会话当前冻结的知识库查询 |

查询结果会携带命中与证据相关信息。涉及库内事实的回答应以这些证据为依据：无上下文或无命中时，明确说明当前知识库未提供相关信息，而不是补充未经检索支持的内容。

## 通用语音助手中的工具调用

通用 Agent 在 `rag_tool_mode=auto` 时可调用 `search_knowledge_base`。`ContextManager` 会记录用户问题、补全短追问的查询语境、调用 RAG Core，并按 `session_id` 和轮次保存命中、来源与错误记录。`rag_tool_mode=never` 时工具被禁用。

## 配置

RAG 模型和连接配置由 `.env.local` 提供，主要包括：

```dotenv
LIVERAG_RAG_LLM_MODEL=
LIVERAG_RAG_LLM_BASE_URL=
LIVERAG_RAG_LLM_API_KEY=
LIVERAG_RAG_EMBEDDING_MODEL=
LIVERAG_RAG_EMBEDDING_BASE_URL=
LIVERAG_RAG_EMBEDDING_API_KEY=
LIGHTRAG_TIMEOUT_MS=120000
```

运行时也可通过 `GET/PUT /rag/config` 管理客户端侧的查询模式、超时、`top_k`、重排和上下文长度等设置。密钥不会以明文返回。
