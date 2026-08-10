# 当前运行架构说明

本文档描述 LiveRAG 项目当前运行时的模块划分、数据流和组件职责。面向公网、多用户、支持异步任务的 Interview Coach 目标架构见 [Interview Coach 目标架构](./INTERVIEW_COACH_ARCHITECTURE.md)。

---

## 1. 总体结构

```text
liverag/
  main.py                 # 通用语音 Agent LiveKit Worker 入口
  interview_main.py       # 面试教练 LiveKit Worker 入口
  agent/                  # LiveKit 语音 Agent、provider、工具调用、语音链路指标
  context/                # SessionSystemPrompt、SOUL、per-KB history、知识库概览
  rag/                    # LightRAG Core Service，按 knowledge_base 物理隔离
  api/                    # 前端管理 API，唯一对外后端入口
  interview/              # Interview Coach 完整业务域
    jobs/                 # 后台异步任务系统
  config/                 # 全局配置和运行时模型配置
  logging/                # 全局事件日志
  runtime/                # ~/.LiveRAG 路径和运行状态
```

---

## 2. 模块详解

### 2.1 liverag/agent/ — 语音 Agent 层

负责 LiveKit Worker 的语音管线装配和实时对话处理：

| 文件 | 职责 |
|------|------|
| `providers.py` | `build_agent_session()` 工厂，装配 STT/VAD/TTS/LLM/TurnDetector |
| `assistant.py` | 通用 `VoiceAssistant`，处理 LLM 推理 + RAG tool call |
| `interview_assistant.py` | `LiveKitInterviewAgent`，面试专用 Agent，关闭默认 LLM 自由对话 |
| `volcengine_stt.py` | 火山引擎 BigModel STT 集成 |
| `dashscope_tts.py` | DashScope 实时 TTS 集成 |
| `turn_detector.py` | 基于语义的说话结束检测 |
| `hot_words.py` | 热词/关键词识别 |
| `metrics_hooks.py` | STT/TTS/LLM 延迟指标收集 |
| `tool/rag_client.py` | RAG 查询工具，供 LLM function call 使用 |

通用和面试 Agent 复用同一套 provider 装配（STT→Volcengine, TTS→DashScope, VAD→Silero），但面试 Agent 关闭了 LLM 自由回答节点，所有要说的话由 `InterviewAgentController` 决定。

### 2.2 liverag/api/ — 管理 API 层

FastAPI 应用（端口 9821），前端唯一后端入口：

| 文件 | 职责 |
|------|------|
| `server.py` | FastAPI app 创建、所有路由注册、依赖组装 |
| `main.py` | Uvicorn 启动入口 |
| `interview_routes.py` | `/api/interviews/*` 路由，含异常→HTTP 转换 |
| `interview_profile_source.py` | Profile Service 的 RAG 数据源适配 |
| `rag_gateway.py` | RAG 代理网关，封装对 RAG Core 的 HTTP 调用 |

通用 LiveRAG 路由（知识库、模型配置、Session、文档、查询等）直接在 `server.py` 中定义。

### 2.3 liverag/interview/ — 面试业务域

完整的 Interview Coach 业务实现，按层分目录组织：

```text
liverag/interview/
  schemas.py               # Pydantic 数据契约
  records.py               # 不可变 dataclass 记录（含 JobStatus 枚举、BackgroundJobRecord）
  state_machine.py         # InterviewStateMachine（纯计算）
  follow_up.py             # FollowUpPolicy 规则化决策
  application/             # 应用层 —— 用例编排
    service.py             # InterviewService
    orchestrator.py        # InterviewOrchestrator
    controller.py           # InterviewAgentController
    evaluator.py           # AnswerEvaluator + Provider
    planner.py             # InterviewPlanner
    profile_service.py     # InterviewProfileService
    report.py              # InterviewReportBuilder
    resume_parser.py       # ResumeParser（简历事实抽取）
  persistence/             # 持久化层 —— 数据库访问
    models.py              # SQLAlchemy ORM 模型（8 张表，含 interview_background_jobs）
    repository.py          # InterviewRepository + JobRepository Protocol
    sqlalchemy_repository.py # SQLAlchemy 实现
    db.py                  # Engine/Session 工厂
  jobs/                    # 后台异步任务系统（第三步新增）
    repository.py          # JobRepository — PostgreSQL CRUD
    queue.py               # RedisQueue — Redis 队列与幂等锁
    tasks.py               # 任务注册表 + 全部 handler（demo / resume_parse / profile_generation / interview_preparation / report_generation）
    worker.py              # BackgroundWorker — 异步主循环
    worker_main.py         # 独立 Worker 进程入口
  question_bank/           # 题库子系统
    catalog.py             # 题库目录与选题
    builder.py             # 题库组装
    converter.py           # v1→v2 格式迁移
    enricher.py            # LLM rubric 生成
    cli.py                 # CLI 入口
    data/                  # question_bank.v2.reviewed.json
  prompts/                 # Prompt 模板
    evaluation_prompts.py  # 回答评价 LLM system prompt
  intelligence/            # 公司面经情报
    provider.py            # 领域契约 Protocol + ProviderError
    service.py             # IntelligenceService — 编排 + 降级
    cache.py               # Redis fresh/stale cache
    nowcoder_provider.py   # NowcoderSpiderProvider — Query↔MCP↔RawExperience
    normalizer.py          # 确定性规范化（公司名/岗位别名统一）
    extractor.py           # LLM 从帖子正文提取问题/主题
    aggregator.py          # 聚合为 CompanyInterviewProfile
    mcp/                   # MCP Client/Server
```

**领域层（`interview/` 根目录，纯数据与规则）：**

| 文件 | 职责 |
|------|------|
| `schemas.py` | Pydantic 数据契约：InterviewPlan、InterviewConfig、AnswerEvaluation 等，均严格校验 |
| `records.py` | 不可变 dataclass 记录，用于业务层传递 |
| `state_machine.py` | `InterviewStateMachine`：14 状态 + 14 事件，纯计算不操作 DB |
| `follow_up.py` | `FollowUpPolicy`：规则化追问决策 |

**应用层（`application/`，用例编排）：**

| 文件 | 职责 |
|------|------|
| `service.py` | `InterviewService`：创建面试、提交回答、评价、生成报告 |
| `orchestrator.py` | `InterviewOrchestrator`：状态机 + 持久化的原子操作入口 |
| `controller.py` | `InterviewAgentController`：LiveKit 语音事件 ↔ 业务服务的翻译层 |
| `evaluator.py` | `AnswerEvaluator` + `OpenAIAnswerEvaluationProvider`：LLM 评价 |
| `planner.py` | `InterviewPlanner`：从题库 + 画像生成面试计划 |
| `profile_service.py` | `InterviewProfileService`：通过 RAG 检索生成候选人/岗位画像 |
| `report.py` | `InterviewReportBuilder`：汇总生成面试报告 |

**持久化层（`persistence/`，数据库访问）：**

| 文件 | 职责 |
|------|------|
| `models.py` | SQLAlchemy ORM 模型（7 张表），含完整约束和关系 |
| `repository.py` | `InterviewRepository` Protocol（接口），含共享异常类型 |
| `sqlalchemy_repository.py` | `SQLAlchemyInterviewRepository`：完整 SQLAlchemy 实现 |
| `db.py` | Engine/Session 工厂，SQLite WAL 配置，事务边界 |

**题库子系统（`question_bank/`）：**

| 文件 | 职责 |
|------|------|
| `catalog.py` | 预加载的题库目录与选题逻辑 |
| `builder.py` | 从配置组装题库 |
| `converter.py` | v1→v2 题库格式迁移 |
| `enricher.py` | LLM 生成 rubric 丰富题库 |
| `cli.py` | `liverag-build-question-bank` CLI 入口 |
| `data/` | `question_bank.v2.reviewed.json` (约 1.85 MB) |

**Prompt 模板（`prompts/`）：**

| 文件 | 职责 |
|------|------|
| `evaluation_prompts.py` | 回答评价的 LLM system prompt |

**后台任务系统（`jobs/`）：**

| 文件 | 职责 |
|------|------|
| `repository.py` | `JobRepository`：PostgreSQL 中 BackgroundJob 的 CRUD、状态流转（PENDING→QUEUED→RUNNING→COMPLETED/FAILED）|
| `queue.py` | `RedisQueue`：基于 Redis List 的 FIFO 队列（RPUSH/BLPOP）+ SETNX 幂等锁 |
| `tasks.py` | 任务注册表（`@register` 装饰器）+ 5 个 handler：`demo`、`resume_parse`、`profile_generation`、`interview_preparation`、`report_generation` |
| `worker.py` | `BackgroundWorker`：主循环（兜底扫描→BLPOP→执行→写回），SIGINT/SIGTERM 优雅关闭 |
| `worker_main.py` | 独立 Worker 进程入口：加载配置→建立 PG/Redis 连接→组装依赖→启动循环 |

**公司情报子系统（`intelligence/`）：**

| 文件 | 职责 |
|------|------|
| `provider.py` | `InterviewIntelligenceProvider` Protocol、`ProviderError`、`RawInterviewExperience` 等契约模型 |
| `service.py` | `IntelligenceService`：缓存检查→Provider调用→规范化→提取→聚合→写缓存，完整降级编排 |
| `cache.py` | Redis fresh/stale 双层缓存：fresh TTL 默认 1h，stale TTL 默认 24h |
| `nowcoder_provider.py` | `NowcoderSpiderProvider`：领域 Query→搜索词→MCP Tool→`RawInterviewExperience[]` |
| `normalizer.py` | 确定性规范化：公司别名统一、岗位别名统一、轮次识别、空白清洗 |
| `extractor.py` | LLM 从不可信帖子正文提取 questions/topics/interview_round，输出 `NormalizedInterviewExperience` |
| `aggregator.py` | 去重→主题频率→代表性题目→轮次模式→`CompanyInterviewProfile` |
| `mcp/` | MCP stdio Client + Nowcoder MCP Server（暴露 `search_nowcoder_experiences` Tool）|

### 2.4 liverag/rag/ — RAG 核心层

基于 LightRAG 的知识库服务（端口 9721）：

| 文件 | 职责 |
|------|------|
| `cli.py` | RAG Core Service 独立启动入口 |
| `server.py` | RAG Core FastAPI 服务 |
| `engine.py` | LightRAG engine 封装 |
| `engine_manager.py` | 多知识库 engine 管理与缓存 |
| `knowledge_base.py` | 知识库 CRUD 逻辑 |
| `doc_parser.py` | 文档解析（PDF、DOCX、PPTX、XLSX、MD 等） |
| `metadata_store.py` | SQLite 元数据存储 |
| `service.py` | RAG 服务层 |
| `filenames.py` | 文件名安全处理 |
| `schemas.py` | RAG API 数据契约 |

### 2.5 liverag/context/ — 上下文与记忆层

通用 LiveRAG 助手的 session prompt、history 和 overview 管理：

| 文件 | 职责 |
|------|------|
| `store.py` | `ContextStore`：文件型 session 存储 |
| `manager.py` | `ContextManager`：消息和 RAG context 写入 |
| `renderer.py` | `SessionPromptRenderer`：渲染固定 system prompt |
| `history.py` | `HistoryCompactor`：挂断后压缩 history |
| `overview.py` | `KnowledgeOverviewGenerator`：知识库概览生成 |
| `defaults.py` | 默认 SOUL/overview 模板 |

### 2.6 liverag/config/ — 配置层

| 文件 | 职责 |
|------|------|
| `settings.py` | `AppSettings`：语音、RAG、Context、Interview DB、功能开关等全部配置 |

### 2.7 liverag/runtime/ — 运行时层

| 文件 | 职责 |
|------|------|
| `paths.py` | `~/.LiveRAG` 下各目录和文件的路径解析 |

---

## 3. 通用语音助手数据流

### 3.1 通话开始前

```text
LiveKit job 创建
  → 读取下次通话配置的 kb_id
  → 预热该知识库 engine
  → 清空上一通话 messages / rag_context / session_system_prompt
  → 读取 knowledge_overview.md（缺失时使用降级说明，不在启动时生成）
  → 读取 system_prompt_template.md
  → 读取 SOUL.md
  → 读取 history/{kb_id}/history.jsonl 最近 N 条
  → 根据 rag_tool_mode 渲染 RAG_TOOL_DESCRIPTION
  → 写入 session/session_system_prompt.md
  → VoiceAssistant 使用固定 instructions 启动
```

### 3.2 通话中

```text
用户语音
  → LiveKit STT
  → messages.jsonl 追加 user
  → LLM 基于固定 instructions + 当前通话 messages 推理
  → auto 模式下模型可调用 search_knowledge_base
  → RAG 工具只查询当前锁定 kb_id
  → rag_context.jsonl 写入查询事实和证据
  → LLM 输出回复
  → messages.jsonl 追加 assistant
  → TTS 播放
```

通话中不再读取或拼接：`history.jsonl`、`knowledge_overview.md`、最近消息摘要、动态 system prompt、`memory.md`。

### 3.3 挂断后

```text
通话结束
  → 读取本次 messages.jsonl
  → 读取 SOUL.md、当前 KB overview、当前 KB 最近 history
  → Context Model 压缩成本次长期 history 内容
  → 追加到 history/{kb_id}/history.jsonl
  → cursor 自增
  → 清空 messages.jsonl
  → runtime_state.json 写入 ended_at 和 history_compaction 结果
```

压缩失败不阻断下一次启动，只写日志和 runtime state。

---

## 4. Interview Coach 数据流

详见 [系统架构与数据流](./系统架构与数据流.md) 第 4 节和 [Interview Coach 目标架构](./INTERVIEW_COACH_ARCHITECTURE.md)。

核心要点：

- 面试通过 `POST /api/interviews/prepared`（同步）或 `POST /api/interviews/{id}/prepare?async=true`（异步）创建
- 异步模式下，后台 Worker 按 stage 执行：简历解析→候选人画像→岗位画像→公司情报→计划生成
- 实时语音由独立 `liverag-interview-agent` Worker 处理
- 状态机控制全流程：ASKING→LISTENING→EVALUATING→(FOLLOW_UP|NEXT_QUESTION|FINISH)
- 所有数据通过 SQLAlchemy Repository 持久化，SQLite 开发 / PostgreSQL 生产
- 后台任务通过 `interview_preparation` Job + Redis 队列异步执行，前端通过 `GET /api/interviews/{id}/preparation` 轮询进度
- 公司面经情报通过 stdio MCP 接入牛客 Spider，仅在 PREPARING 阶段调用，失败自动降级
- 断线只结束 Attempt，Session 可恢复

---

## 5. Docker Compose 服务拓扑

```text
livekit (:7880-7882)          ← WebRTC 房间 + Agent dispatch
liverag-rag (:9721)           ← LightRAG 核心，healthcheck 后其他服务才启动
liverag-api (:9821)           ← FastAPI 管理面，依赖 rag healthy
liverag-agent                 ← 通用语音 Worker，依赖 livekit + rag
liverag-interview-agent       ← 面试 Worker，依赖 livekit + rag
liverag-interview-worker      ← 后台任务 Worker，依赖 postgres + redis + rag（第三步新增）
liverag-frontend (:3001→3000) ← Next.js，依赖 livekit + api
postgres (:5432)              ← PostgreSQL 16，面试生产数据库
redis (:6379)                 ← Redis 7，任务队列 + 分布式锁（第三步新增）
```

所有后端服务共享 `liverag-data` 卷（`/data`），PostgreSQL 使用独立 `liverag-postgres-data` 卷，Redis 使用独立 `liverag-redis-data` 卷。

---

## 6. 模块边界约定

- `liverag/agent/` 只负责 LiveKit hooks、工具调用、语音模型装配和链路指标。通用和面试 Agent 各自独立。
- `liverag/context/` 负责通用助手的提示词模板、SOUL、history、知识库概览和 SessionSystemPrompt 渲染。不涉及面试结构化数据。
- `liverag/rag/` 负责知识库 CRUD、文档原文件、LightRAG workspace、检索和索引。Interview Coach 通过 RagGateway 复用此层来构建 CandidateProfile/JobProfile。
- `liverag/api/` 是前端唯一后端入口，负责模型配置、session、SOUL、knowledge_overview、RAG 包装接口和 Interview 路由。不暴露系统提示词模板和 history。
- `liverag/interview/` 是 Interview Coach 完整业务域，不修改通用 LiveRAG 的核心逻辑。
- `liverag/interview/jobs/` 是后台异步任务系统，负责 Job 持久化（PostgreSQL）、任务队列（Redis）、Worker 主循环和任务注册表。Worker handler 保持薄层——只解析 payload → 调用 Application Service → 更新 Job 状态。
- `liverag/interview/intelligence/` 是公司面经情报子系统，通过 stdio MCP 接入牛客 Spider，经过规范化→提取→聚合后产出 `CompanyInterviewProfile`。仅在 PREPARING 阶段调用，不进入实时 LiveKit 主链路。任何环节失败均降级跳过，不阻止 Plan 生成。
- `liverag/interview/skill_progress/` 将已持久化 `AnswerEvaluation` 映射到版本化两级 taxonomy，生成可重建的 Evidence 与 `SkillProgress`；90 天半衰期评分、置信度和弱点合并全部由确定性规则完成。
- Planner 在岗位相关题不少于 50% 的硬约束下，叠加薄弱项复测、证据补充和已掌握技能抽查；同步与异步准备路径读取同一候选人画像。
- `/api/interviews/skill-progress` 与 `/progress` 提供总览、趋势来源和训练建议，浏览器继续通过 Next.js BFF 访问 FastAPI。
- `liverag/config/` 负责环境变量和运行时配置文件读取，同时承载 Interview 和通用 LiveRAG 的配置（含 `RedisSettings`、`WorkerSettings`、`InterviewIntelligenceSettings`）。
