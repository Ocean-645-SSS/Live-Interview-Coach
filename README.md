# Interview-Coach

面向技术岗位求职者的可部署实时模拟面试系统，基于简历、岗位 JD、版本化题库、公司面经与历史能力画像生成冻结的个性化 InterviewPlan，通过实时语音 Agent 完成受控提问、结构化评分、动态追问与训练报告闭环。

## 功能概览

技术栈： Python、FastAPI、LiveKit、LightRAG、MCP、SQLAlchemy、PostgreSQL、Redis、Docker。
- **个性化面试准备**：融合候选人/岗位画像、版本化题库、可选公司面经与历史能力画像生成并冻结InterviewPlan；通过LightRAG Mix 检索项目证据，并双侧校验岗位技能与候选人项目后定向改写题目，以题量、难度和追问上限约束实时Agent；将准备流程拆为异步阶段，通过Redis Worker、幂等键和阶段持久化支持断点恢复与降级。
- **结构化评分与训练闭环**：设计按题型加权的Answer Evaluator，通过Pydantic约束结构化输出、服务端依据 rubric 重算评分并保存可审计的 ASR 局部修正；基于Evaluation Evidence幂等聚合历史均分、30 天衰减分、最新分与置信度，将弱项复测反馈至下一场 InterviewPlan，形成持续训练闭环。
- **测评驱动模型优化**：围绕空泛回答误判、追问相关性等失败样本迭代评分Prompt，建立 Answer、Behavior与RAG 三类离线评测；50 组人工标注样本上评分 Pearson 达 0.925，追问决策准确率 98%、追问质量达 4.92/5，RAG Recall@5 达 0.958、Faithfulness 达 4.96/5。

## 架构与文档

| 文档 | 内容 |
| --- | --- |
| [系统架构与数据流](docs/系统架构与数据流.md) | 部署拓扑、两条业务链路及数据边界 |
| [API 文档](docs/API.md) | 管理 API 与 Interview Coach API 索引 |
| [RAG 说明](docs/RAG.md) | 知识库隔离、索引、检索和证据约定 |
| [记忆机制](docs/MEMORY.md) | 会话记录与跨会话历史 |
| [Interview Coach](docs/INTERVIEW_COACH_ARCHITECTURE.md) | 面试流程、状态机、评价与异步准备 |
| [热词词表](docs/HOT_WORDS.md) | 可维护的 ASR 热词格式 |
| [部署 LiveKit](docs/LiveKit-Server部署.md) | Compose 与生产部署注意事项 |
| [发布边界](docs/KNOWN_ISSUE.md) | 已知限制与上线前检查项 |

## 快速开始

### 前置条件

- Python 3.10–3.14
- [uv](https://docs.astral.sh/uv/)
- Docker 与 Docker Compose（推荐，用于全栈依赖）
- 可用的 LLM、Embedding、火山引擎 STT 与 DashScope TTS 凭据

### 配置

在项目根目录创建本地环境文件：

```powershell
Copy-Item .env.example .env.local
```

至少填写以下变量：

```dotenv
LIVERAG_RAG_LLM_MODEL=
LIVERAG_RAG_LLM_BASE_URL=
LIVERAG_RAG_LLM_API_KEY=
LIVERAG_RAG_EMBEDDING_MODEL=
LIVERAG_RAG_EMBEDDING_BASE_URL=
LIVERAG_RAG_EMBEDDING_API_KEY=
VOLCENGINE_STT_APP_ID=
VOLCENGINE_STT_ACCESS_TOKEN=
VOICE_LLM_API_KEY=
DASHSCOPE_API_KEY=
```

`VOICE_LLM_API_KEY` 同时用于语音 Agent 和面试回答评价，`DASHSCOPE_API_KEY` 是默认 TTS 凭据。需要跨会话历史时，还应配置 `CONTEXT_MODEL_API_KEY`；可选 Context Model 参数见 [记忆机制](docs/MEMORY.md)。不要提交 `.env.local`。

### 以 Docker Compose 启动

```powershell
docker compose --env-file .env.local up --build -d
docker compose ps
```

默认服务入口：

- 管理 API：`http://127.0.0.1:9821`（交互式 OpenAPI：`/docs`）
- LiveKit：`ws://127.0.0.1:7880`
- 前端：`http://127.0.0.1:3001`

查看运行日志：

```powershell
docker compose logs -f liverag-api liverag-rag liverag-agent
```

### 本地开发

安装后端依赖：

```powershell
uv sync --group dev
```

在不同终端启动所需进程：

```powershell
uv run liverag-rag-service
uv run liverag-api
uv run liverag-agent dev
uv run liverag-interview-agent dev
uv run liverag-interview-worker
```

本地 Interview Coach 使用 PostgreSQL 时，先设置 `INTERVIEW_DATABASE_URL`，再运行迁移：

```powershell
uv run alembic upgrade head
```

## 验证

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## 发布前注意

`docker-compose.yml` 的前端构建上下文为 `LiveRAG-Fronted/agent-starter-react`，但根目录 `.gitignore` 当前忽略整个 `LiveRAG-Fronted/` 目录。因此，若要向 GitHub 发布**可直接构建的完整前端栈**，请先决定是否将该前端源码纳入版本控制；否则克隆后的 Compose 前端构建会缺少上下文。其余后端、文档与部署配置均以本仓库为准。

## 许可证

当前仓库未声明许可证。对外发布前请补充明确的 `LICENSE` 文件。
