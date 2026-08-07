# 第三步：Redis + Background Worker + MCP — 异步任务系统

> **关联文档：** [Interview Coach 总体实施计划](./interview-coach-plan.md) 第 13 节第三步
>
> **状态：** 3.1 已完成 ✅ | 3.2 待开始 | 3.3 待开始

**目标：** 在 PostgreSQL 成为可靠业务数据库后，引入 Redis 和一套 Background Worker，把耗时准备与报告任务移出 FastAPI 请求和实时 LiveKit 主链路，并在后台准备任务中接入可降级的牛客 MCP 面经增强。

**全局约束（来自总体计划）：**
- 不因为 Interview Coach 重构整个 LiveRAG 存储层
- Redis 不保存权威业务状态
- MCP 不进入实时语音问答主链路
- MCP 失败不得阻止基础模拟面试
- 所有 LLM 结构化输出必须经过 Pydantic 校验

---

## 3.1 异步基础设施（Job 表 + Redis Queue + Worker + 任务轮询）

> **状态：** ✅ 已完成（2026-08-05）

先证明异步系统跑通：一条 demo job 从 API → PostgreSQL → Redis → Worker → PostgreSQL COMPLETED 的全链路。

### 3.1.1 Redis 依赖 + Docker Compose + Settings

- [x] `pyproject.toml` 添加 `redis>=5.0`、`hiredis>=2.0` 运行时依赖
- [x] `pyproject.toml` 添加 `fakeredis[lua]>=2.20` 开发依赖
- [x] `pyproject.toml` 添加 `liverag-interview-worker` entry point
- [x] `docker-compose.yml` 添加 Redis 7 服务（含健康检查 `redis-cli ping`）
- [x] `docker-compose.yml` 添加 `liverag-interview-worker` 服务（依赖 postgres + redis + liverag-rag）
- [x] `liverag/config/settings.py` 添加 `RedisSettings` 数据类（url、queue_timeout、lock_ttl）
- [x] `liverag/config/settings.py` 添加 `WorkerSettings` 数据类（poll_timeout、max_retries、task_timeout、concurrency）
- [x] `.env.example` 添加 Redis/Worker 环境变量说明

### 3.1.2 BackgroundJob ORM 模型 + Alembic 迁移

- [x] `liverag/interview/records.py` 添加 `JobStatus` 枚举（PENDING / QUEUED / RUNNING / COMPLETED / FAILED）
- [x] `liverag/interview/records.py` 添加 `BackgroundJobRecord` 冻结数据类
- [x] `liverag/interview/persistence/models.py` 添加 `BackgroundJobModel` ORM 模型
  - 表名 `interview_background_jobs`
  - 字段：id、job_type、idempotency_key、status、business_resource_id、payload_json、result_json、error_message、attempt、max_attempts、started_at、completed_at
  - 唯一约束：`(job_type, idempotency_key)`
  - 索引：`(status, created_at)`、`(job_type, business_resource_id)`
  - Check 约束：`attempt >= 0`、`max_attempts >= 1`
- [x] Alembic 迁移脚本（手动清理，仅包含 `interview_background_jobs` 建表）

### 3.1.3 JobRepository + RedisQueue

- [x] `liverag/interview/jobs/repository.py` — `JobRepository` 类
  - `create_job()` — 创建 PENDING Job，返回 `BackgroundJobRecord`
  - `get_job()` — 按 ID 查询
  - `find_by_idempotency()` — 幂等键查找
  - `get_job_by_resource()` — 按业务资源 ID 查找最新一条
  - `list_pending_jobs()` — 列出待处理 Job
  - `mark_queued()` / `mark_running()` / `mark_completed()` / `mark_failed()` — 状态流转
  - `retry_job()` — 重试（未达最大次数回 PENDING，否则抛错）
  - BackgroundJobModel ↔ BackgroundJobRecord 转换
- [x] `liverag/interview/jobs/queue.py` — `RedisQueue` 类
  - `enqueue()` — RPUSH 到 `interview:jobs:{job_type}` 列表
  - `dequeue()` — BLPOP 阻塞出队，超时返回 None
  - `queue_length()` — LLEN 队列长度
  - `acquire_lock()` — SETNX 加锁（TTL 自动过期）
  - `release_lock()` / `lock_exists()` — 锁管理
  - 锁 Key 格式：`interview:lock:{job_type}:{resource_id}`

### 3.1.4 BackgroundWorker + 任务注册表 + Demo 任务

- [x] `liverag/interview/jobs/tasks.py` — 任务注册表
  - `@register(job_type)` 装饰器模式
  - `demo` 任务：`asyncio.sleep(delay)` → 返回 `{"message": "hello async", "job_id": ..., "slept_seconds": ...}`
  - `get_handler()` / `registered_types()` 访问函数
- [x] `liverag/interview/jobs/worker.py` — `BackgroundWorker` 类
  - `run()` — 主循环，直到收到 shutdown 信号
  - `_poll_and_execute()` — 先 backfill → 再 BLPOP → 执行
  - `_backfill_pending_jobs()` — PG 兜底扫描 PENDING → 重新入队（Redis 重启恢复）
  - `_execute_job()` — 幂等检查 → 获取 handler → `asyncio.wait_for()` 超时 → 更新 PG 状态
  - 重试机制：失败时如果 attempt < max_attempts → retry_job → 回到 PENDING
  - 已完成/失败 Job 跳过执行

### 3.1.5 Worker 进程入口

- [x] `liverag/interview/jobs/worker_main.py`
  - 创建 DB engine → `JobRepository`
  - 创建 Redis 连接 → `RedisQueue`
  - 创建 `BackgroundWorker` 并注册 SIGINT/SIGTERM 处理
  - 运行 worker loop

### 3.1.6 Job 状态 API + 现有接口适配

- [x] `liverag/api/interview_routes.py` 添加依赖注入
  - `configure_job_dependencies()` — 注入 `JobRepository` + `RedisQueue`
  - `get_job_repository()` / `get_redis_queue()` — FastAPI Depends
- [x] 添加 API 端点
  - `POST /api/interviews/jobs/demo` — 创建 demo Job → 入队 → 返回 `{job_id, status}`
  - `GET /api/interviews/jobs/{job_id}` — 返回完整 Job 状态、结果、错误、时间戳
- [x] `liverag/api/server.py` 添加初始化逻辑
  - try/except 包裹 Redis 连接创建 + 依赖注入
  - Redis 不可用时 API 返回错误但不影响其他 interview 功能

### 3.1.7 测试

- [x] `tests/interview/test_background_jobs.py` — 19 个测试，5 个测试类
  - `TestBackgroundJobModel` — ORM 模型 CRUD、幂等唯一约束、按资源查找
  - `TestJobStatusTransitions` — 完整生命周期、失败重试、最大重试限制、PENDING 列表
  - `TestRedisQueue` — 入队出队、空队列超时、锁获取/释放/重入
  - `TestWorkerEndToEnd` — 端到端执行、超时失败、跳过已完成 Job、幂等约束
  - `TestTaskRegistry` — demo 注册验证、未知类型返回 None

### 链路验证

```
POST /api/interviews/jobs/demo
  → JobRepository.create_job() → PG 写入 PENDING
  → RedisQueue.enqueue() → Redis RPUSH
  → 返回 {job_id, status: "PENDING"}

BackgroundWorker.run():
  → _backfill_pending_jobs() → PG 兜底扫描
  → RedisQueue.dequeue() → Redis BLPOP
  → JobRepository.mark_running() → PG RUNNING
  → demo_task() → asyncio.sleep() → {"message": "hello async"}
  → JobRepository.mark_completed() → PG COMPLETED

GET /api/interviews/jobs/{job_id}
  → JobRepository.get_job() → {status: "COMPLETED", result: {...}}
```

---

## 3.2 Background Business Workflows（修订版）

> **状态：** ⏳ 待开始
>
> **修订说明（2026-08-07）：** 原 3.2 设计存在三个核心问题需要调整：
> 1. `resume_parse` 与 `profile_generation` 职责重叠（都生成 `CandidateProfile`）
> 2. 缺少 Preparation Orchestrator，前端需要理解多个独立 Job 的依赖关系
> 3. 幂等键仅绑定 `interview_id`/`session_id`，未绑定业务输入快照（input fingerprint）
>
> 本修订版在保留 3.1 基础设施和第三步总体目标的前提下，重新设计 3.2 全部内容。

---

### 3.2.0 架构决策记录

在展开详细设计之前，先记录几个关键架构决策及其理由。

#### 决策 1：CandidateFacts 存储方式 — artifact 方式

**现状分析：**
- 当前 `CandidateProfile`（schemas.py:96）字段为 `kb_id`、`summary`、`skills`、`projects`、`evidence_refs`
- 该模型是「混合型」：`summary` 带推理色彩，`skills`/`projects` 是事实型
- 没有独立的「纯事实」模型

**方案B：**

| 维度 | B: Pydantic schema + Job result_json（选定） |
|---|---|---|
| 新增 DB 迁移 | 不需要 |
| 可查询性 | 需通过 Job result 间接读取 |
| 改动量 |  小（仅新增 Pydantic schema） |
| 与现有架构一致性 | 复用 Job 结果存储模式 |

**选择当前方案的理由：**
1. V1/V2 项目规模不需要独立查询 `CandidateFacts`——它始终作为 `profile_generation` 的输入被消费
2. 零 DB 迁移成本，`result_json` 字段已就绪
3. `CandidateFacts` 定义为 Pydantic schema，享受完整的类型安全和校验
4. 后续如有跨面试复用需求（"同一份简历不重复解析"），`resume_parse` Job 的幂等键已能保证复用，无需独立表

#### 决策 2：Preparation Orchestrator 实现方式 — 选择方案 B（单一 Preparation Job + 内部 stage）

**两种方案对比：**

| 维度 | A: Parent + 多个 Child Job | B: 单一 Job + stage 元数据（选定） |
|---|---|---|
| Job 数量 | 1 parent + N children | 1 |
| 阶段可观测性 | 每个 child 独立状态 | stage 字段 + completed_steps 列表 |
| 独立重试 | 每个 child 可独立 retry | Worker handler 内部 stage 级重试 |
| 实现复杂度 | 需 parent-child 关系管理 | 单一 handler 内状态机 |
| 前端理解成本 | 需理解多个 job_id | 一个 job_id + stage 枚举 |

**选择方案 B 的理由：**
1. Preparation 的依赖链是严格线性的（`A → B → C → D → E`），不是 DAG——不需要 child Job 的并行执行能力
2. 整个流程的输入（interview_id）是统一的，不需要 child Job 的独立输入分发
3. 实现简单：一个 `interview_preparation` job_type + 一个 handler 函数内的 stage 循环
4. 前端只需轮询一个 `job_id`，通过 `stage` 和 `completed_steps` 即可展示进度
5. 若未来确实需要 DAG（例如并行执行 candidate_profile 和 job_profile），可平滑升级为方案 A——两者不互斥

#### 决策 3：现有 InterviewPlan 已内嵌 Profile 快照

**现状：** `InterviewPlan`（schemas.py:249）已包含 `candidate_profile: CandidateProfile | None` 和 `job_profile: JobProfile | None` 字段。

这意味着「冻结快照」的存储结构已存在，无需新增字段。需要强化的只是：
- 保存 profile 的 **version/hash**（用于 input_fingerprint 计算和可审计性）
- `plan_generation` handler 中**明确记录**快照来源（哪个 Job 产出了该 profile）

#### 决策 4：Worker handler 保持薄层

所有 Worker handler 必须遵循：
```
handler = 解析 payload → 调用 Application Service → 更新 Job 状态
```
不在 handler 中重写业务逻辑。`InterviewProfileService`、`InterviewPlanner`、`InterviewReportBuilder` 应同时服务于 sync（FastAPI）和 async（Worker）两条路径。

现有代码中 `resume_parse_task` 和 `profile_generation_task` 已经部分在 handler 中实现了业务逻辑（LLM 调用、RAG 检索）。这部分需要抽取到 Application Service 层。

---

### 3.2.1 Preparation Workflow / Orchestrator

#### 设计目标

将面试准备的完整流程封装为一个可观测、可恢复的后台 Workflow：

```
POST /api/interviews/{id}/prepare?async=true
  → 创建 interview_preparation Job
  → 返回 {job_id, status: "PENDING"}
  → Worker 异步执行全部 stage
  → 前端通过 GET /api/interviews/{id}/preparation 轮询进度
```

前端不需要理解内部有几个步骤——它只看到一个 `job_id` 和一个 `stage` 枚举。

#### Stage 定义

新增 `PreparationStage` 枚举（在 `records.py` 或 `schemas.py` 中）：

```python
class PreparationStage(str, Enum):
    PENDING = "PENDING"                             # 初始状态
    RESUME_PARSING = "RESUME_PARSING"               # 简历文档事实抽取
    CANDIDATE_PROFILE_GENERATION = "CANDIDATE_PROFILE_GENERATION"  # 候选人画像生成
    JOB_PROFILE_GENERATION = "JOB_PROFILE_GENERATION"              # 岗位画像生成
    COMPANY_INTELLIGENCE = "COMPANY_INTELLIGENCE"    # 公司面经情报（可降级）
    PLAN_GENERATION = "PLAN_GENERATION"             # 面试计划生成
    READY = "READY"                                 # 准备完成
```

#### Job 类型

新增 `interview_preparation` job_type，注册到任务注册表。

**payload_json 结构：**
```json
{
  "interview_id": "interview_xxx",
  "current_stage": "RESUME_PARSING",
  "completed_steps": [],
  "degraded": false,
  "degradation_reasons": [],
  "stage_results": {
    "resume_parse": null,
    "candidate_profile": null,
    "job_profile": null,
    "company_intelligence": null,
    "plan": null
  }
}
```

**result_json 结构（最终完成时）：**
```json
{
  "status": "READY",
  "completed_steps": ["RESUME_PARSE", "CANDIDATE_PROFILE", "JOB_PROFILE", "COMPANY_INTELLIGENCE", "PLAN_GENERATION"],
  "degraded": false,
  "degradation_reasons": [],
  "plan_id": "plan_xxx"
}
```

#### Stage 执行流程

`interview_preparation` handler 内部是一个顺序 stage 循环：

```
1. RESUME_PARSING
   → 调用 resume_parse 的内部函数（不创建独立 Job）
   → 获取 CandidateFacts
   → 写入 stage_results.resume_parse
   → 推进到 CANDIDATE_PROFILE_GENERATION

2. CANDIDATE_PROFILE_GENERATION
   → 加载 CandidateFacts + KB evidence
   → 调用 InterviewProfileService.build_candidate_profile()
   → 获取 CandidateProfile
   → 写入 stage_results.candidate_profile
   → 推进到 JOB_PROFILE_GENERATION

3. JOB_PROFILE_GENERATION
   → 加载 JD KB evidence
   → 调用 InterviewProfileService.build_job_profile()
   → 获取 JobProfile
   → 写入 stage_results.job_profile
   → 推进到 COMPANY_INTELLIGENCE

4. COMPANY_INTELLIGENCE（可降级）
   → try:
       → 调用 IntelligenceService（3.3 实现）
       → 获取 CompanyInterviewProfile
     except (timeout, unavailable):
       → degraded = true
       → degradation_reasons.append("NOWCODER_MCP_UNAVAILABLE")
       → CompanyInterviewProfile = None
   → 写入 stage_results.company_intelligence
   → 推进到 PLAN_GENERATION（无论是否降级）

5. PLAN_GENERATION
   → 加载 CandidateProfile + JobProfile + CompanyInterviewProfile
   → 调用 InterviewPlanner.build()
   → 获取 InterviewPlan
   → Pydantic 校验 + 程序级校验
   → 持久化 InterviewPlan（含 profile snapshots）
   → 更新 Interview.state: PREPARING → READY
   → 写入 stage_results.plan
   → 推进到 READY

6. READY
   → 标记 Job COMPLETED
```

#### Stage 级重试与恢复

- 每个 stage 执行前，检查 `stage_results` 中是否已有该 stage 的结果
- 如果有 → 跳过（幂等恢复）
- 如果某个 stage 失败 → Job 进入 FAILED，`current_stage` 停留在失败的 stage
- Worker 的通用重试机制（`attempt < max_attempts`）负责整体重试
- 重试时已完成 stage 自动跳过，仅重新执行失败的 stage

#### Interview 状态联动

Preparation Workflow 管理两个独立的状态体系：

| 体系 | 状态值 | 含义 |
|---|---|---|
| `Job.status` | PENDING / QUEUED / RUNNING / COMPLETED / FAILED | Worker 执行生命周期 |
| `Interview.state` | CREATED / PREPARING / READY / IN_PROGRESS / … | 业务面试阶段 |

**状态转换规则：**
- `POST /prepare?async=true` → Interview.state: `CREATED → PREPARING`
- Preparation Job SUCCEEDED → Interview.state: `PREPARING → READY`
- Preparation Job FAILED（max attempts reached）→ Interview.state 保持 `PREPARING`，API 返回错误信息

#### API

**`POST /api/interviews/{id}/prepare?async=true`**

```json
// Request: (body 可复用现有 CreatePreparedInterviewRequest)
{
  "title": "阿里 Java 后端模拟面试",
  "config": { ... },
  "target_kb_id": "kb_alibaba_jd",
  "target_company": "阿里巴巴",
  "target_role": "Java 后端开发"
}

// Response:
{
  "job_id": "job_abc123",
  "status": "PENDING"
}
```

**`GET /api/interviews/{id}/preparation`**

```json
{
  "job_id": "job_abc123",
  "status": "RUNNING",
  "stage": "PLAN_GENERATION",
  "completed_steps": [
    "RESUME_PARSE",
    "CANDIDATE_PROFILE",
    "JOB_PROFILE",
    "COMPANY_INTELLIGENCE"
  ],
  "degraded": true,
  "degradation_reasons": ["NOWCODER_MCP_UNAVAILABLE"],
  "started_at": "2026-08-07T10:00:00+00:00",
  "updated_at": "2026-08-07T10:02:30+00:00",
  "error": null
}
```

此端点通过 `JobRepository.get_job_by_resource(job_type="interview_preparation", business_resource_id=interview_id)` 查询最新的 Preparation Job，将其 payload/result 中的 stage 元数据反序列化后返回。

---

### 3.2.2 Resume Facts Extraction（`resume_parse`）

#### 职责重新定义

| 维度 | 旧设计 | 新设计 |
|---|---|---|
| 定位 | LLM 解析 → `CandidateProfile` | LLM 结构化事实抽取 → `CandidateFacts` |
| 是否负责画像推理 | 是（summary 字段含推理） | 否——仅提取文档中明确存在的事实 |
| 是否输出 strengths/weaknesses | 否（旧版也没有） | 明确禁止——这些属于 profile_generation |

**新定位：** 文档事实抽取任务。只提取候选人资料中明确存在的客观事实，不做任何推理、评价或面试建议。

#### 新增 Pydantic Schema：`CandidateFacts`

在 `liverag/interview/schemas.py` 中新增：

```python
class WorkExperienceFact(StrictModel):
   
class ProjectFact(StrictModel):

class CandidateFacts(StrictModel):
```

注意：**没有** `summary`（带推理）、`strengths`、`weaknesses`、`interview_focus`、`risk_areas`、`experience_level` 等字段。这些都属于 `CandidateProfile`（profile_generation 产出）。

#### Job 定义

- **job_type:** `resume_parse`
- **输入（payload_json）：**
  ```json
  {
    "kb_id": "default",
    "document_ids": ["doc_001", "doc_002"],
    "document_hashes": ["sha256_abc", "sha256_def"]
  }
  ```
- **输出（result_json）：** `CandidateFacts.model_dump()`
- **幂等键：** `resume_parse:{kb_id}:{documents_snapshot_hash}`
  - `documents_snapshot_hash = sha256("+".join(sorted(document_hashes)))`
  - 文档不变 → 可复用已解析结果
  - 任意文档变化 → 自动产生新的输入 fingerprint

#### 任务流程

1. 计算 `documents_snapshot_hash`
2. 通过 `RagGateway`（即 `KnowledgeContextSource`）获取候选人资料 evidence
3. LLM 调用（使用更新后的 prompt：`RESUME_FACTS_EXTRACTION_PROMPT`）→ 结构化事实抽取
4. Pydantic 校验 → `CandidateFacts`
5. 保存到 Job `result_json`
6. 保留 `raw_evidence_refs` 供后续审计追溯

#### Prompt 更新

现有 `resume_parse_prompts.py` 中的 `RESUME_PARSE_SYSTEM_PROMPT` 需要更新为事实抽取模式：
- 输出字段改为 `CandidateFacts` 结构
- 删除 `summary` 的推理要求
- 增加明确指令：「不得推断候选人的强项、弱项、面试重点或技能水平」
- 增加 `education`、`work_experience`、`certifications`、`achievements` 的提取规则

#### 与现有代码的关系

- **复用：** `KnowledgeContextSource.retrieve()` — RAG 检索
- **复用：** `_clean_json_response()` — LLM 输出清理
- **重构：** 将 `resume_parse_task` handler 中的 LLM 调用逻辑抽取到新的 `ResumeParser` 类（Application Service 层）
- **重构：** handler 变为薄层：`ResumeParser.parse(kb_id, doc_ids) → CandidateFacts`

---

### 3.2.3 Profile Generation（`candidate_profile` / `job_profile`）

#### CandidateProfile 扩展

在现有 `CandidateProfile`（schemas.py:96）基础上增加推理字段。所有新增字段均为可选（带默认值），保持向后兼容：

```python
class CandidateProfile(StrictModel):
    """从候选人资料和结构化事实中生成的面试用途画像。"""

    # === 现有字段（保持不变）===
    kb_id: NonEmptyText = "default"
    summary: str = ""
    skills: list[NonEmptyText] = Field(default_factory=list)
    projects: list[NonEmptyText] = Field(default_factory=list)
    evidence_refs: list[NonEmptyText] = Field(default_factory=list)

    # === 新增字段（可选，带默认值，向后兼容）===
    experience_level: str = ""      # 匹配 schemas.py 中的InterviewDifficulty字段
```

**设计原则：**
- 推断字段必须可追溯到 `CandidateFacts` 或 KB evidence（通过 `evidence_refs`）
- 所有新增字段均为可选——现有代码（`create_prepared_interview`、`InterviewPlan` 等）无需修改即可工作

#### candidate_profile 子类型

- **job_type:** `profile_generation`（复用现有注册），subtype = `candidate_profile`
- **输入（payload_json）：**
  ```json
  {
  "profile_type": "candidate_profile",
  "interview_id": "...",
  "kb_id": "...",
  "candidate_facts_job_id": "..."
  }
  ```
- **输出：** `CandidateProfile.model_dump()`
- **幂等键：** `candidate_profile:{interview_id}`

#### job_profile 子类型

- **job_type:** `profile_generation`，subtype = `job_profile`
- **输入（payload_json）：**
  ```json
  {
  "profile_type":"job_profile",
  "interview_id":"...",
  "kb_id":"...",
  "company":"...",
  "role":"..."
  }
  ```
- **输出：** `JobProfile.model_dump()`
- **幂等键：** `job_profile:{interview_id}`

关于 JD 是否需要独立事实抽取：当前项目规模下，`JobProfile` 的数据结构已经足够轻量（`required_skills`、`summary`、`role`），JD 文本的结构化程度通常也高于简历。**不建议**为 JD 单独创建 `job_artifact_parse` 任务——由 `profile_generation(job_profile)` 内部完成轻量结构化即可。如果后续出现复杂的 JD 解析需求（多轮面试流程、结构化能力模型等），再考虑拆分。

#### 与现有代码的关系

- **复用：** @profile_service.py `InterviewProfileService.build_candidate_profile()` 和 `build_job_profile()` —— 作为核心服务
- **重构：** `profile_generation_task` handler 变为薄层，调 `InterviewProfileService`
- **保持向后兼容：** 现有的同步 `create_prepared_interview()` 流程（无 `CandidateFacts` 输入）继续使用规则匹配模式

---

### 3.2.4 Company Interview Intelligence

> **注：** 此部分详细实现属于 3.3（MCP Client + Nowcoder Adapter）。3.2 只定义 Preparation Workflow 中该 stage 的集成方式。

#### 在 Preparation Workflow 中的位置

```
JOB_PROFILE_GENERATION
        ↓
COMPANY_INTELLIGENCE  ← 可降级步骤
        ↓
PLAN_GENERATION       ← 无论是否降级都继续
```

#### 降级逻辑
Company Intelligence 属于可选增强 stage。
- 调用失败
- 超时
- MCP provider 不可用
- 返回数据无法通过 schema 校验
均不会阻塞 Interview Preparation。

Workflow:
- 记录 degraded=true
- 保存 degradation_reason
- 跳过 CompanyInterviewProfile
- 继续 PLAN_GENERATION

- 如果不存在 company 信息，则跳过 Company Intelligence，并记录 degraded reason:COMPANY_NOT_PROVIDED
- Planner 在缺少 CompanyInterviewProfile 时，仅使用：CandidateProfile + JobProfile + RAG evidence + QuestionBank 生成计划。

#### CompanyInterviewProfile 模型引用

`CompanyInterviewProfile` ：基于外部面经来源归纳得到的公司面试参考信息。
具体 Pydantic 模型定义在 3.3 中完成（`liverag/interview/intelligence/provider.py`）。3.2 只需要知道：
- 它是一个可选输入，传入 `InterviewPlanner.build()`
- 包含公司常见面试题、技术重点、面试风格等面经信息
- 不参与决定是否生成 Plan，仅影响题目选择、重点分布和追问策略。
- 为 `None` 时 Planner 正常降级工作

---

### 3.2.5 Interview Plan Generation

#### 输入（重新明确）

```
interview_id
candidate_profile: CandidateProfile
job_profile: JobProfile
company_interview_profile: CompanyInterviewProfile | None  (optional)
planner_config: InterviewConfig
question_bank: QuestionBank
```

注意：不再使用 `candidate_profile_id` 和 `job_profile_id`（ID 引用）作为间接输入。Preparation Workflow 中 profile 已直接在内存中，直接传递对象。

#### 流程

1. **加载冻结输入：** 所有 profile 对象已在前面 stage 中获得
2. **从题库加载 candidate questions：** `QuestionBank.select_questions()`
3. **程序级选题（现有逻辑）：** `InterviewPlanner.build()` —— 基于画像权重调整选题
4. **Pydantic 校验：** `InterviewPlan` 自带 validator
5. **程序级复核（增强）：**
   - 总题数 = `config.question_count`
   - section 分布合理（技术知识 + 项目深入 + 系统设计 ≥ 总题数的 60%）
   - 总时长 ≤ `config.duration_minutes`
   - 单题时长在 `30-1800s` 范围内
   - 难度分布与 `config.difficulty` 匹配，和CandidateProfile中的experience_level 匹配
   - 必要 section 存在（TECHNICAL_KNOWLEDGE + PROJECT_DEEP_DIVE 至少各 1 题）
6. **持久化 InterviewPlan：**
   - 写入 `InterviewModel.plan_json`（`InterviewPlan.model_dump_json()`）
   - `InterviewPlan` 内已嵌入 `candidate_profile` 和 `job_profile` 快照
   - 额外存储 snapshot 元数据（见下方）
7. **更新 Interview.state：** `PREPARING → READY`


#### Job 定义

在 Preparation Workflow 中，`plan_generation` 不作为独立 Job，而是 `interview_preparation` Job 的一个 stage。这样可以避免为单个步骤创建额外 Job。

但如果未来需要独立触发 Plan 重新生成（不重新执行整个 Preparation），可以注册一个独立的 `plan_generation` job_type：

- **job_type:** `plan_generation`（独立使用时）
- **幂等键：** `plan:{interview_id}`

#### 两个状态体系的明确区分

| 体系 | 状态 | 变更者 |
|---|---|---|
| `Job.status` | PENDING → QUEUED → RUNNING → COMPLETED / FAILED | Worker / JobRepository |
| `Interview.state` | CREATED → PREPARING → READY → … → COMPLETED | Application Service |

**关键规则：**
- `Interview.state = PREPARING` 在 Preparation Job 创建时设置
- `Interview.state = READY` 在 PLAN_GENERATION stage 成功完成后设置
- `Job.status = COMPLETED` 在所有 stage 完成后设置
- 两者不混用，API 返回时分别展示

---

### 3.2.6 Report Generation Workflow

#### 独立性

`report_generation` **不属于** Preparation Workflow。它在面试 Session 完成后独立触发：

```
POST /api/interview-sessions/{id}/complete?async=true
  → 创建 report_generation Job
  → 返回 {job_id, status: "PENDING"}
  → Worker 异步生成报告
```

#### Job 定义

- **job_type:** `report_generation`
- **输入（payload_json）：**
  ```json
  {
    "session_id": "session_xxx",
  }
  ```
- **输出（result_json）：**
```json
  {
    "report_id": "report_xxx",
    "session_id": "session_xxx",
    "state": "COMPLETED"
  }
  ```
- **最终业务结果：**
InterviewReport.content_json 保存结构化报告 JSON：
  - summary
  - skill_scores
  - strengths
  - weaknesses
  - recommendations
  - evidence_refs
- **幂等键：** `report:{session_id}`
  - 旧幂等键 `{session_id}:report` 的问题：Evaluation 修正后无法重新生成

#### 流程

1. 加载 InterviewPlan + 逐题 Answer + Evaluation
2. LLM 生成报告（summary、strengths、weaknesses、recommendations）→ Pydantic 校验
3. 代码计算：`weighted_score`、`aggregate_score`、统计数据、evidence mapping
4. DB 状态转换：`InterviewReportModel.state = COMPLETED`

**职责划分：**

| 计算者 | 负责 |
|---|---|
| LLM | summary、strengths、weaknesses、recommendations、qualitative skill analysis |
| 代码 | weighted_score、aggregate_score、deterministic statistics、evidence mapping、DB state transition |

#### 与现有代码的关系

- **复用：** `InterviewReportBuilder.build()` —— 作为核心服务
- **重构：** `generate_report()` handler 变为薄层，调 `InterviewReportBuilder`

---

### 3.2.7 Progressive API Migration

#### 共享 Application Service 架构

```
              ┌─────────────────────────┐
              │  Application Service     │
              │  (ProfileService /       │
              │   Planner / ReportBuilder)│
              └───────────┬─────────────┘
                          │
              ┌───────────┴───────────┐
              │                       │
        FastAPI (sync)          Worker (async)
        ?async=false            ?async=true
        (默认)                   (渐进启用)
```

**关键原则：** Worker handler 只负责加载 Job → 调 Application Service → 更新 Job 状态。不在 handler 中重新实现业务逻辑。

#### API 端点修改

**`POST /api/interviews/{id}/prepare`**

| 参数 | 行为 |
|---|---|
| `?async=true` | 创建 `interview_preparation` Job → 入队 → 返回 `{job_id, status: "PENDING"}` |
| `?async=false` 或默认 | 保持现有同步行为：`create_prepared_interview()` 直接返回 `PreparedInterviewResult` |

sync 和 async 共享同一个 `InterviewProfileService` 和 `InterviewPlanner` 实例。

**`POST /api/interview-sessions/{id}/complete`**

| 参数 | 行为 |
|---|---|
| `?async=true` | 创建 `report_generation` Job → 入队 → 返回 `{job_id, status: "PENDING"}` |
| `?async=false` 或默认 | 保持现有同步行为：`generate_report()` 直接返回 `InterviewReportRecord` |

**`GET /api/interviews/{id}/preparation`**（新增）

返回 Preparation Workflow 的当前进度（见 3.2.1 API 设计）。

**`GET /api/interviews/jobs/{job_id}`**（复用 3.1）

通用的 Job 状态查询端点，`report_generation` Job 可直接复用。

#### 渐进式迁移路径

```
Phase 1（当前）:
  async=false（默认）→ 同步执行
  async=true → 创建 Job → 后台执行

Phase 2（稳定后）:
  async=true 成为默认
  async=false 保留为 fallback

Phase 3（长期）:
  移除同步入口（如确认不再需要）
```

---

### 3.2.8 Idempotency

#### PostgreSQL 唯一约束

现有 `UNIQUE(job_type, idempotency_key)`（`uq_background_jobs_idempotency`）直接满足需求。

- 所有幂等键由 job_type + 业务唯一标识组成，存入 idempotency_key 列。
- 幂等键整体存入 `idempotency_key` 列
- PostgreSQL 唯一约束是最终幂等保证

#### Redis 锁的职责

Redis 锁只负责「尽量减少相同 Job 在短时间内重复投递」：

- Key 格式：`lock:job:{job_type}:{business_id}`
- TTL：60 秒（有限过期）
- 获取方式：`SETNX`
- Redis 锁失效、Redis 重启都不破坏业务一致性

**流程：**
```
API: 创建 Job 前
  1. find_by_idempotency(job_type,idempotency_key)
     → 已有 COMPLETED Job → 直接返回已有 job_id
     → 已有 PENDING/RUNNING Job → 直接返回已有 job_id
  2. Redis acquire_lock(job_type, ttl=60)
     → 获取失败 → 返回已有 job_id（短暂重复投递保护）
  3. create_job(idempotency_key=idempotency_key) → PG 唯一约束最终防重
  4. enqueue → Worker 消费
```

---

### 3.2.9 文件修改清单

#### 新增文件

| 文件 | 说明 |
|---|---|
| `liverag/interview/application/resume_parser.py` | ResumeParser 类（从 resume_parse_task 抽取的业务逻辑） |
| `liverag/interview/prompts/resume_facts_prompts.py` | CandidateFacts 抽取专用 prompt（替换原 resume_parse_prompts） |
| `alembic/versions/xxxx_add_preparation_stage.py` | 无需新增表；仅需确保 Interview.state 枚举值含 PREPARING |

#### 修改文件

| 文件 | 说明 |
|---|---|
| `liverag/interview/schemas.py` | +`CandidateFacts`、+`EducationFact`、+`WorkExperienceFact`、+`ProjectFact`、+`PreparationStage`；扩展 `CandidateProfile`（新增可选推理字段）；扩展 `InterviewPlan`（新增 snapshot 元数据字段） |
| `liverag/interview/records.py` | +`PreparationStage` 枚举（或放在 schemas.py） |
| `liverag/interview/application/profile_service.py` | 增强 `InterviewProfileService`：新增 LLM 增强路径（接收 `CandidateFacts` 参数，生成推理字段） |
| `liverag/interview/application/planner.py` | 增强 `InterviewPlanner.build()`：接收 `CompanyInterviewProfile` 可选参数；新增程序级校验 |
| `liverag/interview/application/report.py` | 增强 `InterviewReportBuilder`：新增 LLM 生成 qualitative 部分 |
| `liverag/interview/application/service.py` | 新增 `prepare_interview_async()` 方法（创建 Preparation Job）；重构 `generate_report()` 支持 async 路径 |
| `liverag/interview/jobs/tasks.py` | 新增 `interview_preparation` handler；重构 `resume_parse_task` 为薄层（调 ResumeParser）；重构 `profile_generation_task` 为薄层（调 InterviewProfileService）；新增 `report_generation` handler |
| `liverag/interview/prompts/resume_parse_prompts.py` | 更新 prompt 为事实抽取模式（或新增 `resume_facts_prompts.py` 后废弃此文件） |
| `liverag/api/interview_routes.py` | 新增 `POST /{id}/prepare?async=true`；新增 `GET /{id}/preparation`；新增 `POST /sessions/{id}/complete?async=true` |
| `liverag/interview/persistence/repository.py` | 可能需要新增 `update_interview_state()` 方法 |
| `liverag/interview/persistence/sqlalchemy_repository.py` | 实现 `update_interview_state()` |
| `tests/interview/test_background_jobs.py` | 扩展：Preparation Workflow 测试、幂等测试、降级测试、snapshot 测试 |

#### 不需要的改动

- **不需要新增 DB 表：** `CandidateFacts` 作为 Pydantic schema 存储在 Job `result_json` 中
- **不需要修改 `BackgroundJobModel`：** 现有表结构已满足需求（`payload_json` 存储 stage 元数据，`result_json` 存储最终结果）
- **不需要新增 `JobStatus` 枚举值：** 现有 PENDING/QUEUED/RUNNING/COMPLETED/FAILED 已足够，stage 信息在 payload 内部管理

---

### 3.2.10 测试计划

#### Preparation Workflow

- [ ] `interview_preparation` handler：resume_parse → profile → plan 正常串行执行
- [ ] 某个 stage 失败后的有限 retry（attempt < max_attempts → 重试）
- [ ] Worker 在中间 stage 重启后可以恢复（已完成 stage 不重复执行）
- [ ] Preparation Job 最终正确进入 COMPLETED
- [ ] Interview.state 最终正确进入 READY
- [ ] `GET /api/interviews/{id}/preparation` 返回正确的 stage 和 completed_steps

#### 幂等

- [ ] 相同文档 snapshot 重复 prepare → 不重复生成 CandidateFacts（复用已有 `resume_parse` Job 结果）
- [ ] 简历内容变化后（document_hash 变化）→ 产生新的 CandidateFacts
- [ ] CandidateFacts 变化后 → 产生新的 CandidateProfile
- [ ] CandidateProfile 变化后 → 重新生成 Plan
- [ ] Evaluation 变化后 → 重新生成 Report
- [ ] Redis 锁失效时 PostgreSQL 唯一约束仍能防止最终重复提交
- [ ] `find_by_idempotency()` 返回已有 COMPLETED Job 时 API 直接返回已有 job_id

#### MCP 降级

- [ ] Nowcoder 正常 → CompanyInterviewProfile 可用，Plan 含面经标记
- [ ] Nowcoder timeout → 降级，degraded=true，Plan 正常生成（仅用 CandidateProfile + JobProfile）
- [ ] Nowcoder unavailable → 降级，degraded=true，Plan 正常生成
- [ ] capability discovery 失败 → 降级
- [ ] schema/contract 不符合要求 → 降级
- [ ] 以上所有降级场景都不能阻塞 Preparation Workflow → 最终进入 READY


#### 实时链路（回归）

确保 LiveKit realtime interview：
- [ ] 不访问 Redis Queue
- [ ] 不调用 MCP
- [ ] 不等待 Preparation Worker
- [ ] final transcript 和实时状态迁移保持同步业务逻辑

#### 渐进式 API

- [ ] `?async=true` → 返回 `{job_id, status: "PENDING"}`，后台执行
- [ ] `?async=false`（默认）→ 同步执行，直接返回结果
- [ ] 两种路径使用相同的 Application Service 实例
- [ ] Redis 不可用时 `?async=true` 返回 503 错误，`?async=false` 正常工作

---

### 3.2.11 推荐实施顺序

```
3.2-A: PreparationStage 枚举 + interview_preparation Job 骨架
       ├── 新增 PreparationStage 枚举
       ├── 注册 interview_preparation handler（空 stage 循环）
       ├── POST /{id}/prepare?async=true API
       ├── GET /{id}/preparation API
       └── 测试：空 stage 循环 + Job 状态流转

3.2-B: Resume Facts Extraction（resume_parse）
       ├── 新增 CandidateFacts Pydantic schema
       ├── 新增 ResumeParser Application Service
       ├── 更新 resume_parse prompt → 事实抽取模式
       ├── 重构 resume_parse_task handler → 薄层
       ├── 集成到 interview_preparation stage 1
       └── 测试：CandidateFacts 抽取 + 幂等 + input_fingerprint

3.2-C: Profile Generation 增强
       ├── 扩展 CandidateProfile（新增可选推理字段）
       ├── 增强 InterviewProfileService（LLM 增强路径）
       ├── 重构 profile_generation_task handler → 薄层
       ├── 集成到 interview_preparation stage 2 + 3
       └── 测试：推理字段生成 + 溯源 + input_fingerprint

3.2-D: Plan Generation 增强
       ├── 扩展 InterviewPlan（snapshot 元数据字段）
       ├── 增强 InterviewPlanner（程序级校验 + CompanyInterviewProfile 输入）
       ├── Interview.state: PREPARING → READY
       ├── 集成到 interview_preparation stage 4 + 5
       └── 测试：Plan 生成 + snapshot 冻结 + Interview state 联动

3.2-E: Report Generation 独立 Workflow
       ├── 增强 InterviewReportBuilder（LLM qualitative 部分）
       ├── 注册 report_generation handler
       ├── POST /sessions/{id}/complete?async=true API
       └── 测试：Report 生成 + 幂等 + input_fingerprint

3.2-F: MCP Intelligence Integration（与 3.3 联动）
       ├── COMPANY_INTELLIGENCE stage 集成
       ├── 降级逻辑实现
       └── 测试：正常 + timeout + unavailable + schema error

3.2-G: 渐进式 API 完善 + 前端轮询
       ├── sync/async 共享 Application Service 确认
       ├── GET /{id}/preparation 完善
       └── 端到端测试：sync + async 路径

3.2-H: 幂等 / 恢复 / 回归测试完善
       ├── PostgreSQL 唯一约束测试
       ├── Redis 锁失效测试
       ├── Worker 重启恢复测试
       └── 实时链路回归测试
```

**阶段门槛：**
- 3.2-B 依赖 3.2-A（需要 Preparation Workflow 骨架）
- 3.2-C 依赖 3.2-B（需要 CandidateFacts）
- 3.2-D 依赖 3.2-C（需要 CandidateProfile + JobProfile）
- 3.2-E 可与 3.2-B/C/D 并行（Report 独立于 Preparation）
- 3.2-F 依赖 3.2-D（需要 Plan Generation 就绪），同时依赖 3.3 的 MCP Client 实现
- 3.2-G 依赖 3.2-A 至 3.2-F
- 3.2-H 在以上全部完成后执行

---

## 3.3 MCP Client + Nowcoder Adapter + 降级策略

> **状态：** ⏳ 待开始

在后台准备任务中接入可降级的牛客 MCP 面经增强，仅在 `PREPARING` 阶段调用，不影响实时语音链路。

### 3.3.1 依赖倒置层（`intelligence/provider.py`）

- [ ] 定义 `InterviewIntelligenceQuery` 查询对象
  - 字段：company、role、region、round、limit、timeout
- [ ] 定义 `InterviewExperienceSource` 结果对象
  - 字段：id、company、role、interview_round、source、published_time、topics、questions、summary、confidence、content_hash
- [ ] 定义 `InterviewIntelligenceProvider` Protocol
  - `search_experiences(query) -> list[InterviewExperienceSource]`
- [ ] 定义 `ProviderCapability` 和 `ProviderError` 类型

### 3.3.2 通用 MCP Client（`intelligence/mcp_client.py`）

- [ ] MCP transport 管理（优先 Streamable HTTP）
- [ ] Server 初始化 + capability discovery
- [ ] Tool discovery + schema 读取
- [ ] Tool 调用 + 超时 + 重试
- [ ] 优先消费 `structuredContent`；文本结果经 schema 校验
- [ ] 连接池/生命周期管理
- [ ] 配置白名单：允许的 MCP server、transport、tools

### 3.3.3 牛客 Adapter（`intelligence/nowcoder_mcp.py`）

- [ ] 实现 `InterviewIntelligenceProvider` Protocol
- [ ] 牛客工具名/参数 → 领域模型映射
- [ ] 牛客返回结构 → `InterviewExperienceSource` 转换
- [ ] 不泄漏牛客内部 schema 到 service 层
- [ ] 默认关闭，需 feature flag 显式启用
- [ ] 具体 tool name/schema 以实际 server capability discovery + 契约测试为准

### 3.3.4 标准化（`intelligence/normalizer.py`）

- [ ] 公司名/岗位名/地区别名标准化
- [ ] 面试轮次映射（一面/二面/终面 → 标准枚举）
- [ ] 话题/问题类型归类
- [ ] 来源标注（provider、source_id、抓取时间、内容哈希）

### 3.3.5 聚合（`intelligence/aggregator.py`）

- [ ] 去重（跨来源相同面经）
- [ ] 话题频次统计
- [ ] 公司/岗位常见问题聚类
- [ ] 面试风格特征提取
- [ ] 可信度计算（来源可靠性 × 样本量 × 时效性 × 跨来源一致性 × 字段完整性）
- [ ] 生成 `CompanyInterviewProfile`

### 3.3.6 Intelligence Service（`intelligence/service.py`）

- [ ] Provider 选择与编排（可扩展多个 provider）
- [ ] 结果缓存（键：company/role/region/round/provider；过期后可后台刷新）
- [ ] Provider 不可用时自动降级
- [ ] 缓存命中时直接返回，不触发外部调用
- [ ] 审计日志：查询参数、provider、抓取时间、内容摘要哈希、规范化快照
- [ ] Feature flag 控制：`INTERVIEW_INTELLIGENCE_ENABLED`

### 3.3.7 与 3.2 Plan 生成集成

- [ ] `plan_generation` 任务在 feature flag 开启时调用 Intelligence Service
- [ ] 将 `CompanyInterviewProfile` 作为 Planner 的附加输入
- [ ] 计划中标记 `intelligence_degraded=true/false`
- [ ] MCP 调用超时/失败不影响基础 Plan 生成（题库 + 候选人/JD 画像已足够）

### 3.3.8 安全策略

- [ ] 外部面经中的指令、链接和代码一律视作数据，不拼入 system prompt
- [ ] 不保存不必要的个人信息
- [ ] MCP server URL 和 transport 配置白名单
- [ ] 实时面试链路不得调用 MCP（代码层 + 监控双重保障）

### 3.3.9 测试

- [ ] MCP mock：capability discovery、tool call、structuredContent、超时、非法 schema
- [ ] 牛客 adapter 契约测试（mock MCP server）
- [ ] Normalizer 单元测试（别名映射、边界情况）
- [ ] Aggregator 单元测试（去重、聚类、可信度计算）
- [ ] Intelligence Service 降级测试：provider 不可用 → 计划仍生成成功
- [ ] 集成测试：Plan 生成标记 `intelligence_degraded`
- [ ] 回归测试：实时语音链路日志中无 MCP 调用记录

---

## 文件变更清单（第三步全部）

### 修改文件

| 文件 | 阶段 | 说明 |
|---|---|---|
| `pyproject.toml` | 3.1 | +redis、hiredis、fakeredis、worker entry |
| `docker-compose.yml` | 3.1 | +Redis 服务、+Worker 服务 |
| `.env.example` | 3.1 | +Redis/Worker 环境变量 |
| `liverag/config/settings.py` | 3.1 | +RedisSettings、+WorkerSettings |
| `liverag/interview/records.py` | 3.1 | +JobStatus、+BackgroundJobRecord |
| `liverag/interview/persistence/models.py` | 3.1 | +BackgroundJobModel |
| `liverag/api/interview_routes.py` | 3.1/3.2 | +Job API、+async 参数 |
| `liverag/api/server.py` | 3.1 | +Job 依赖初始化 |
| `liverag/interview/application/service.py` | 3.2 | 异步任务创建替代同步执行 |
| `tests/interview/test_models.py` | 3.1 | 更新 EXPECTED_TABLES |

### 新增文件

| 文件 | 阶段 | 说明 |
|---|---|---|
| `alembic/versions/75e3f27927f0_*.py` | 3.1 | BackgroundJob 迁移 |
| `liverag/interview/jobs/__init__.py` | 3.1 | 包文档 |
| `liverag/interview/jobs/repository.py` | 3.1 | JobRepository |
| `liverag/interview/jobs/queue.py` | 3.1 | RedisQueue |
| `liverag/interview/jobs/tasks.py` | 3.1/3.2 | 任务注册表 + 所有 handler |
| `liverag/interview/jobs/worker.py` | 3.1 | BackgroundWorker |
| `liverag/interview/jobs/worker_main.py` | 3.1 | Worker 进程入口 |
| `liverag/interview/intelligence/__init__.py` | 3.3 | 包文档 |
| `liverag/interview/intelligence/provider.py` | 3.3 | Provider Protocol |
| `liverag/interview/intelligence/mcp_client.py` | 3.3 | 通用 MCP Client |
| `liverag/interview/intelligence/nowcoder_mcp.py` | 3.3 | 牛客 Adapter |
| `liverag/interview/intelligence/normalizer.py` | 3.3 | 标准化 |
| `liverag/interview/intelligence/aggregator.py` | 3.3 | 聚合 |
| `liverag/interview/intelligence/service.py` | 3.3 | Intelligence Service |
| `tests/interview/test_background_jobs.py` | 3.1 | 19 个测试 |

---

## 实施顺序

```
3.1（必须先做）→ 证明异步系统跑通
  ↓
3.2 → 迁移耗时流程到后台
  ↓
3.3 → MCP 面经增强（依赖 3.2 的 plan_generation 任务）
```

**阶段门槛：** 3.1 全部测试通过后才能开始 3.2；3.2 的 `plan_generation` 任务稳定后才能开始 3.3。

**验收标准：**
- FastAPI 重启后 Job 状态仍在 PostgreSQL
- Redis 重启不会丢失已完成业务结果
- MCP 不出现在实时调用日志
- Worker 故障不破坏基础实时面试
- `?async=true` 和 `?async=false` 两种路径均可用
