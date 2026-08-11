# Interview Coach 运行架构

## 服务拓扑

```text
Browser ──HTTP──> Next.js ──HTTP──> FastAPI ──> PostgreSQL
   │                                  ├──────> Redis
   │                                  └──────> RAG Core ──> LightRAG
   └──WebRTC/WebSocket──> LiveKit <── interview-agent
                                      │
                                      ├── STT
                                      ├── LLM evaluator
                                      └── TTS

interview-worker ──> Redis + PostgreSQL + RAG Core
```

默认外部端口为前端 `3001`、API `9821`、LiveKit `7880/TCP`、`7881/TCP` 和 `7882/UDP`。RAG Core 的 `9721` 仅供 Compose 内部服务调用。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `liverag/interview/` | 计划、状态机、题库、评价、报告、能力画像、持久化和任务 |
| `liverag/agent/` | Interview Agent 共用的 STT、TTS、模型供应商、热词与转向检测 |
| `liverag/rag/` | 文档解析、知识库元数据、LightRAG 生命周期、索引与查询 |
| `liverag/api/` | Interview HTTP API、知识库网关和资料来源适配 |
| `liverag/config/` | 环境与模型配置 |
| `alembic/` | PostgreSQL schema 迁移 |
| `LiveRAG-Fronted/agent-starter-react/` | Interview Coach Web 客户端 |

## 数据边界

- PostgreSQL 是面试、计划、Session、Attempt、答案、评价、报告和任务结果的持久事实来源。
- Redis 仅负责队列、分布式锁和短期缓存，不替代数据库事实。
- 候选人与岗位资料按 `kb_id` 隔离；API 和 Worker 统一通过 RAG Core 访问 LightRAG。
- 前端只获得短期 LiveKit token，不持有 LiveKit Secret 或模型密钥。
- 每场面试冻结计划及相关配置；进行中的面试不受后续题库或配置变更影响。

详细时序见[系统架构与数据流](系统架构与数据流.md)，接口见 [API 文档](API.md)。
