# Live Interview Coach

面向中文技术面试的实时 AI 模拟面试系统。项目将 LiveKit 实时音频、火山引擎 STT、DashScope TTS/LLM、LightRAG、PostgreSQL 与 Redis 组合为一套可本地部署的完整产品。

## 核心能力

- 根据候选人资料、目标岗位和版本化题库生成面试计划。
- 在 LiveKit 房间中完成逐题提问、回答采集、追问和状态推进。
- 保存原始转写、可审计的术语纠正、结构化评价与最终报告。
- 使用候选人和岗位知识库增强准备流程，并保留检索证据。
- 聚合历史评价，形成长期能力画像与训练建议。
- 提供面试管理、知识库、岗位目标、报告和能力进度前端页面。

## 系统组成

| 服务 | 作用 |
| --- | --- |
| `liverag-api` | Interview Coach API 与知识库网关 |
| `liverag-rag` | 文档解析、LightRAG 索引和检索 |
| `liverag-interview-agent` | LiveKit 实时面试 Agent |
| `liverag-interview-worker` | 面试准备等异步任务 |
| PostgreSQL | 面试、答案、评价和报告的事实数据源 |
| Redis | 任务队列、锁和短期缓存 |
| Next.js frontend | Interview Coach Web 界面 |

## 快速开始

要求：Python 3.10–3.14、[uv](https://docs.astral.sh/uv/)、Docker 与 Docker Compose。

```powershell
Copy-Item .env.example .env.local
uv sync --group dev
docker compose --env-file .env.local up --build -d
uv run alembic upgrade head
```

在 `.env.local` 中至少配置 RAG LLM/Embedding、火山引擎 STT、`VOICE_LLM_API_KEY` 和 `DASHSCOPE_API_KEY`。不要提交本地密钥文件。

默认入口：

- 前端：`http://127.0.0.1:3001`
- API：`http://127.0.0.1:9821`，OpenAPI：`/docs`
- LiveKit：`ws://127.0.0.1:7880`

查看日志：

```powershell
docker compose logs -f liverag-api liverag-rag liverag-interview-agent liverag-interview-worker
```

本地分别启动服务：

```powershell
uv run liverag-rag-service
uv run liverag-api
uv run liverag-interview-agent dev
uv run liverag-interview-worker
```

前端开发：

```powershell
Set-Location LiveRAG-Fronted/agent-starter-react
corepack pnpm install
corepack pnpm dev
```

## 验证

```powershell
uv run pytest
uv run ruff check liverag tests
uv run ruff format --check liverag tests

Set-Location LiveRAG-Fronted/agent-starter-react
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm build
```

真实语音链路仍需在目标网络中使用有效供应商凭据、麦克风权限和可访问的 LiveKit 服务进行验收。

## 文档

- [架构说明](docs/ARCHITECTURE.md)
- [系统数据流](docs/系统架构与数据流.md)
- [API 索引](docs/API.md)
- [Interview Coach 领域设计](docs/INTERVIEW_COACH_ARCHITECTURE.md)
- [RAG 说明](docs/RAG.md)
- [LiveKit 部署](docs/LiveKit-Server部署.md)
- [ASR 热词](docs/HOT_WORDS.md)
- [发布验收](docs/FIRST_VERSION_TEST_FINDINGS.md)
- [已知限制](docs/KNOWN_ISSUE.md)

## 安全说明

前端不保存 LiveKit Secret 或模型密钥。所有凭据仅通过服务端环境变量注入；提交前请确认 `.env.local`、运行日志、上传文件和本地索引均未进入版本控制。
