# 当前运行架构说明

LiveRAG 由一个浏览器前端、三个 Python 长驻服务、一个 RAG 服务以及面试数据基础设施组成。运行时以 `docker-compose.yml` 为部署基线。

## 服务拓扑

```text
Browser
  ├─ HTTP ───────────────► liverag-frontend ──► liverag-api
  └─ WebRTC / WebSocket ─► LiveKit ◄────────── liverag-agent
                                           └── liverag-interview-agent

liverag-api ──HTTP──► liverag-rag ──► LightRAG / 文件与索引存储
liverag-api ─────────► PostgreSQL（Interview Coach）
liverag-api ─────────► Redis（异步任务与情报缓存）
liverag-interview-worker ─► PostgreSQL + Redis + liverag-rag
```

默认对外端口为：前端 `3001`、管理 API `9821`、LiveKit `7880/TCP`、`7881/TCP`、`7882/UDP`。RAG Core 仅在 Compose 网络中通过 `9721` 被调用。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `liverag/api/` | FastAPI 管理 API、RAG 网关、运行时配置和 Interview HTTP 路由 |
| `liverag/agent/` | 通用 LiveKit 语音 Agent、火山引擎 STT、DashScope TTS、热词注入与工具调用 |
| `liverag/rag/` | 知识库元数据、文件解析、LightRAG 生命周期、索引与查询 HTTP 服务 |
| `liverag/context/` | 通用会话消息、检索审计、知识库概览和跨会话历史 |
| `liverag/interview/` | 面试计划、状态机、评价、报告、题库、能力画像、持久化与异步任务 |
| `liverag/config/` | 环境变量与可持久化运行时配置 |
| `alembic/` | Interview Coach 的 PostgreSQL schema 迁移 |

## 关键边界

- **知识库隔离**：每个语音会话在创建时冻结 `kb_id`；检索、概览和历史均按知识库分区。
- **配置生效时机**：语音模型和 Context Model 的管理 API 修改后，对下一场会话生效；正在运行的会话保持启动快照。
- **RAG 责任边界**：API 层不直接访问 LightRAG，统一经 RAG Core；RAG Core 不可用不会阻止管理 API 启动。
- **面试可靠性**：HTTP API 写入业务状态，Redis 只负责队列与短期协调，PostgreSQL 是面试、评价、报告和任务结果的持久事实来源。
- **密钥边界**：前端不持有 LiveKit API Secret 或模型密钥；管理接口只返回掩码密钥。

## 运行时数据

通用助手的本地状态根目录由 `LIVERAG_USER_DATA_DIR` 指定，Compose 中为 `/data`。其中包含知识库、会话 JSONL、运行态、概览与历史。Interview Coach 的事务数据由 `INTERVIEW_DATABASE_URL` 指向的数据库管理，异步队列由 `INTERVIEW_REDIS_URL` 管理。

更详细的时序见 [系统架构与数据流](系统架构与数据流.md)，接口见 [API 文档](API.md)。
