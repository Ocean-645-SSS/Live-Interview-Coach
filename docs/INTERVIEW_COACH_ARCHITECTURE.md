# Interview Coach 目标架构

## 1. 产品定位

Interview Coach 是一个可部署到公网、支持多个独立用户使用的模拟面试应用。它需要可靠的面试状态管理、异步准备任务和用户数据隔离，但当前阶段不建设完整的多租户 SaaS。

目标能力包括：

- 用户身份认证，以及简历、知识库、面试、回答和报告的数据归属校验。
- 基于候选人资料、JD 和面经生成个性化 Interview Plan。
- 通过 LiveKit Interview Agent 完成可暂停、可恢复的实时语音面试。
- 异步完成资料结构化、面经聚合、计划生成和面试报告生成。
- 记录 Token、音频时长和模型调用量，为限流、配额和成本分析提供依据。

当前范围不包括组织/团队租户、复杂 RBAC、支付、套餐、Kafka、Kubernetes 或微服务拆分。

## 2. 总体架构

```text
Next.js
   │
   ▼
FastAPI API / Application Layer
   ├── SQLAlchemy + Alembic
   │      └── SQLite（开发）/ PostgreSQL（部署）
   │
   ├── Redis
   │      ├── 任务队列
   │      ├── 分布式锁 / 快速幂等检查
   │      ├── 短期状态
   │      └── 限流 / 配额
   │
   ├── Background Worker
   │      ├── 简历与 JD 结构化
   │      ├── 牛客 MCP 面经聚合
   │      ├── Interview Plan 生成
   │      └── 面试报告生成
   │
   ├── LiveKit
   │      └── Interview Agent + 面试状态机
   │
   └── LightRAG
          └── 按 user_id / knowledge_base_id 隔离候选人资料
```

## 3. 组件职责

### 3.1 Next.js

- 提供登录、资料管理、面试准备、实时面试和报告页面。
- 通过服务端 BFF/API route 访问 FastAPI，并在签发 LiveKit connection details 前校验用户身份。
- 只投影服务端状态，不在浏览器中维护第二套权威面试状态机。

### 3.2 FastAPI API / Application Layer

- 作为前端唯一业务后端入口，处理身份、授权、输入校验和资源归属校验。
- 编排数据库、Redis、Background Worker、LiveKit 和 LightRAG，不把业务规则直接写进路由函数。
- 创建异步 Job 后尽快返回 `job_id`，由查询接口或服务端事件向前端报告进度。
- 所有按 ID 读取或修改的资源都必须同时校验 `user_id`，禁止仅凭可猜测的资源 ID 访问数据。

### 3.3 SQLAlchemy + Alembic

- SQLAlchemy 是应用统一的数据访问层，不并存第二套面向生产的原生 `sqlite3` Store。
- Alembic 管理版本化 Schema，迁移必须同时兼容开发和部署数据库。
- SQLite 用于本地开发和轻量测试；PostgreSQL 是正式部署的权威业务数据库。
- CI 至少运行一组 PostgreSQL 集成测试，覆盖事务、唯一约束、时间类型和并发更新，避免 SQLite/PostgreSQL 行为漂移。

PostgreSQL/SQLite 保存：

- 用户及认证关联信息。
- 候选人资料元数据、JD、面试配置和冻结的 Interview Plan。
- Interview Session、Attempt、状态事件、回答、评价和报告。
- Background Job 状态、重试次数和错误信息。
- Token、音频时长、模型、请求类型和关联业务 ID 等用量记录。

数据库是业务状态的唯一真相来源。面试状态、报告结果和永久幂等记录不能只存在 Redis 中。

### 3.4 Redis

Redis 只承担可过期、可重建的协调能力：

- Background Worker 的任务队列。
- 防止同一准备任务或报告任务同时执行的短期分布式锁。
- 事件或请求的快速幂等检查；最终幂等仍由数据库唯一约束保证。
- 短期进度、Agent 心跳和连接状态。
- 按用户/IP/接口进行限流，以及基于已记录用量的短期配额计数。

Redis 故障不能导致已完成面试、回答、报告或永久用量记录丢失。

### 3.5 Background Worker

后台任务系统只使用一套队列和 Worker 实现，具体库在实现阶段根据异步 SDK 兼容性确定，不同时引入多套任务框架。

首批任务类型：

| 任务 | 输入 | 持久化输出 |
|---|---|---|
| 简历/JD 结构化 | 用户资料、JD 文本 | Candidate Profile、Job Profile |
| 牛客 MCP 面经聚合 | 公司、岗位、地区、轮次 | 带来源和时间的面经快照 |
| Interview Plan 生成 | 候选人、岗位、面经、题库 | 冻结且版本化的 Interview Plan |
| 面试报告生成 | 回答、评价、用量和事件 | 结构化 Interview Report |

每个任务必须有数据库 Job 记录、稳定幂等键、有限重试、超时和明确的失败状态。MCP 不进入实时语音主链路；MCP 不可用时，系统应基于用户资料、JD 和题库降级生成计划。

### 3.6 LiveKit 与 Interview Agent

- LiveKit 负责房间、音频传输、STT/LLM/TTS 以及 Agent dispatch。
- Interview Agent 只加载当前用户已冻结的 Interview Plan，并通过应用服务提交状态事件。
- 实时链路处理提问、收听、轻量决策、状态推进和 TTS，不经过后台任务队列。
- Session 与 LiveKit room/attempt 分离，用户断线后可以创建新 attempt 恢复同一业务 Session。

### 3.7 LightRAG

- 简历、项目介绍、README 和技术材料进入候选人资料知识库。
- 每次索引和查询都携带经过授权校验的 `user_id` 与 `knowledge_base_id`。
- 知识库元数据必须归属于用户；底层 workspace 至少按 `knowledge_base_id` 物理隔离，并校验其所属用户。
- 固定题库、rubric、状态机规则、评分权重和结构化报告不进入 RAG。

## 4. 同步与异步边界

实时主链路保持低延迟：

```text
用户语音
  -> LiveKit / STT
  -> Interview Agent
  -> 面试状态机与数据库事务
  -> LLM / TTS
  -> 用户
```

耗时或允许稍后完成的工作进入后台队列：

```text
FastAPI 创建 Job 并入队
  -> Background Worker 执行
  -> 持久化结果与用量
  -> 更新 Job 状态
  -> 前端查询或接收进度
```

不得为了“异步化”把每个实时状态迁移都放入 Redis 队列。回答落库和状态迁移应先可靠完成，深度评价与最终报告可以异步执行。

## 5. 面试状态与可靠性

`InterviewStateMachine` 继续负责：

- 校验当前状态是否允许业务事件。
- 计算题目游标、追问次数、暂停恢复状态和开始/结束时间。
- 拒绝终态复活和非法迁移。

可靠性约束：

- 面试 Session 使用 `version` 乐观锁，防止并发事件覆盖。
- Background Job 等少量存在并发竞争的记录使用乐观锁或条件更新，不给所有表机械增加版本字段。
- `event_id`、任务幂等键和关键业务唯一键由数据库约束兜底。
- 状态快照与事件日志在同一数据库事务中提交。
- Redis 锁用于减少重复工作，不承担业务正确性的最终保证。

## 6. 用户隔离与权限边界

当前采用“一个账号对应一个独立用户空间”的简单模型：

- 核心资源直接关联 `user_id`，不引入 Organization、Team 或通用 Tenant 层。
- API、后台任务和 LiveKit token 签发都必须验证资源所有权。
- Job payload 只传业务 ID；Worker 重新从数据库读取并验证关联关系，不信任客户端提供的路径或归属字段。
- LightRAG 查询不能接受未经服务端解析的任意 workspace 路径。
- 日志和错误信息不得输出访问令牌、完整简历、模型密钥或不属于当前用户的资源信息。

## 7. 用量、限流与配额

首期只记录和约束资源使用，不做商业计费：

- LLM 输入/输出 Token。
- Embedding、结构化和报告模型调用次数。
- STT/TTS 音频秒数。
- 面试时长、并发面试数和异步任务数。
- 失败、重试和降级调用。

永久用量明细写入数据库；Redis 保存短周期计数，用于快速限流和并发控制。暂不实现支付、订阅、套餐购买或复杂账单系统。

## 8. 部署基线

首个可部署版本以单机 Docker Compose 为目标：

- Next.js
- FastAPI
- PostgreSQL
- Redis
- Background Worker
- LiveKit Server
- LiveKit Interview Agent Worker
- LightRAG Core

开发环境允许使用 SQLite 和本地服务；部署环境使用 PostgreSQL、持久化卷和独立密钥。当前不要求 Kubernetes、多区域部署、Kafka或服务网格。

## 9. 实施顺序

1. 保留现有题库、状态机和 SQLite 实现，完成最小面试领域闭环。
2. 引入用户身份与 `user_id` 数据归属，并补充越权访问测试。
3. 使用 SQLAlchemy 重构 Interview 数据访问，接入 Alembic；保持 SQLite 开发，增加 PostgreSQL 部署和集成测试。
4. 引入 Redis、Background Worker 和持久化 Job 模型，迁移资料准备、MCP、计划与报告任务。
5. 接入独立 Interview Agent worker、断线恢复、限流和用量记录。
6. 用 Docker Compose 完成多用户部署验收和端到端测试。

## 10. 非目标

在有明确产品需求前不实现：

- 组织、团队、成员邀请和复杂 RBAC。
- 支付、订阅、套餐、优惠券和账单系统。
- Kafka、事件总线或多套任务队列。
- Kubernetes、跨区域容灾和自动伸缩平台。
- LangGraph、AutoGen、CrewAI、CAMEL 等通用 Agent 编排框架。
- 为追求形式上的“微服务”拆分可在单个 FastAPI/Worker 进程中清晰维护的业务模块。
# V1 当前落地状态（2026-08-09）

## 已完成步骤

### 第一步：单机闭环 ✅

FastAPI、SQLAlchemy/SQLite、版本化题库、状态机、逐题评价、追问、报告、独立 LiveKit Interview Worker 和三个 Next.js 页面已经接通。

实时调用链如下：

```text
Next.js 创建页
  -> POST /api/interviews/prepared
  -> Interview + Plan + Session
  -> POST /sessions/{session_id}/attempts
  -> Next.js 签发限定 room 的 LiveKit token
  -> interview-agent（metadata: session_id + attempt_id）
  -> InterviewAgentController
  -> InterviewService / Orchestrator
  -> SQLAlchemy Repository / SQLite
  -> AnswerEvaluation / Follow-up / Report
```

断线只结束 Attempt，不删除或重建 Session。重新连接时 Worker 根据 Session 状态恢复当前题目或最近一次追问。

### 第二步：PostgreSQL + Alembic ✅

SQLAlchemy 重构完成，Alembic 管理迁移。SQLite（开发）/ PostgreSQL 16（生产）双数据库支持。7 张 ORM 表含完整约束和索引。

### 第三步：异步任务系统 ✅

Redis + Background Worker + MCP 面经增强全面落地。核心交付：

**3.1 异步基础设施：**
- `BackgroundJobModel` ORM（`interview_background_jobs` 表）— PG 权威 Job 状态存储
- `JobRepository` — 完整的 CRUD + 状态流转（PENDING→QUEUED→RUNNING→COMPLETED/FAILED）+ 自动重试
- `RedisQueue` — Redis List 队列（RPUSH/BLPOP）+ SETNX 幂等锁（TTL 300s）
- `BackgroundWorker` — 主循环（兜底扫描 PENDING → BLPOP → 执行 → 写回），SIGINT/SIGTERM 优雅关闭
- 任务注册表 — `@register(job_type)` 装饰器模式
- 独立 Worker 进程入口 — `python -m liverag.interview.jobs.worker_main`
- Docker Compose: `redis`（7-alpine）+ `liverag-interview-worker` 服务
- 配置：`RedisSettings`、`WorkerSettings`、`InterviewIntelligenceSettings`
- 5 种已注册 Job 类型：`demo`、`resume_parse`、`profile_generation`、`interview_preparation`、`report_generation`
- 19 个测试（5 类：ORM 模型、状态流转、Redis 队列、Worker 端到端、任务注册表）

**3.2 业务 Workflow：**
- `interview_preparation` Job — 5 个 stage 顺序执行：简历解析→候选人画像→岗位画像→公司情报→计划生成
- Stage 级幂等恢复 — Worker 重启后已完成 stage 自动跳过
- `resume_parse` Job — 独立简历事实抽取，产出 `CandidateFacts`（纯事实，无推理）
- `profile_generation` Job — 候选人画像/岗位画像双模式（`profile_type` 分流）
- `report_generation` Job — 异步报告生成，幂等键 `report:{session_id}`
- 共享 Application Service 架构 — Worker handler 保持薄层，复用 `InterviewProfileService`、`InterviewPlanner`、`InterviewReportBuilder`
- `?async=true` / `?async=false` 双路径支持

**3.3 MCP 面经增强：**
- `IntelligenceService` — Fresh→Provider→Stale Fallback 缓存策略 + 完整降级编排
- `NowcoderSpiderProvider` — 领域 Query→搜索词→MCP Tool→`RawInterviewExperience[]`
- MCP stdio Client + Nowcoder MCP Server — 暴露 `search_nowcoder_experiences` 结构化 Tool
- Normalizer → Extractor → Aggregator 管线 — 帖子正文→结构化→聚合→`CompanyInterviewProfile`
- 只在 PREPARING 阶段调用 MCP，实时 LiveKit 链路零 MCP 调用
- Feature flag `INTERVIEW_INTELLIGENCE_ENABLED` 默认关闭

**异步任务全链路：**

```text
API 请求 (POST /api/interviews/{id}/prepare?async=true)
  → JobRepository.find_by_idempotency() [幂等检查]
  → RedisQueue.acquire_lock() [并发互斥]
  → JobRepository.create_job() [PG: PENDING]
  → RedisQueue.enqueue() [Redis: RPUSH]
  → Interview.state → PREPARING
  → 返回 {job_id, status: "PENDING"}

BackgroundWorker 消费:
  → _backfill_pending_jobs() [PG 兜底扫描，Redis 重启恢复]
  → BLPOP 获取 job_id
  → _execute_job()
    → 幂等检查（COMPLETED/FAILED 跳过）
    → mark_running() [PG: RUNNING]
    → handler(job, **deps)
      → interview_preparation_task:
        RESUME_PARSING → CANDIDATE_PROFILE → JOB_PROFILE
        → COMPANY_INTELLIGENCE (可降级)
        → PLAN_GENERATION → 持久化 InterviewPlan
    → mark_completed() [PG: COMPLETED]
    → 失败 → mark_failed() → retry_job() [自动重试]

前端轮询:
  → GET /api/interviews/{id}/preparation
  → {stage, completed_steps, degraded, degradation_reasons}
```

**降级保证：**
```text
牛客 / Spider / MCP / Cache 任意环节失败
  → CompanyInterviewProfile = None
  → CandidateProfile + JobProfile + QuestionBank
  → InterviewPlan 正常生成
```

### 第四步：长期能力画像 ✅

稳定的 `candidate_profiles` 聚合根由个人资料库 `kb_id` 确定；每场 `InterviewPlan` 仍冻结当时的 Candidate/Job 快照。评价写入后生成不可变 `skill_progress_evidence`，再按 `candidate_profile_id + skill_key` 全量重算 `skill_progress`，数据库唯一约束保证重复评价、报告重试和 rebuild 不会重复累计。

```text
AnswerEvaluation
  → taxonomy(category, subcategory) → skill_key
  → SkillProgressEvidence
  → average/current/latest/confidence/weak_points
  → 下一场 InterviewPlanner
  → WEAK_RETEST / EVIDENCE_GAP / MASTERY_AUDIT
```

`current_score` 使用相对最近评价的 90 天半衰期；`confidence` 由尝试数、Session 覆盖、时间跨度和一致性确定。Planner 保证岗位核心题的硬配额，训练意图只作为可与岗位相关性重叠的软目标。画像应用与报告后对账都是 best-effort，失败不回滚评价、状态机或已完成报告，可通过 rebuild 恢复。

只读 API 为 `/api/interviews/skill-progress` 和 `/api/interviews/skill-progress/{skill_key}`。Next.js `/progress` 页面展示三个分数、置信度、弱点、评价趋势和训练建议；趋势点可追溯到 evaluation、question、session、interview 与 rubric version。
