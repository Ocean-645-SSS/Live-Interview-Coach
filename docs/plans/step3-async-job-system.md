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
5. **复核（增强）：**
   - 总题数 = `config.question_count`
   - 总时长 ≤ `config.duration_minutes`
   - 难度分布与 `config.difficulty` 匹配
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
- `Interview.state = READY` 在 Preparation Workflow 成功收尾时设置
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

#### 不需要的改动

- **不需要新增 DB 表：** `CandidateFacts` 作为 Pydantic schema 存储在 Job `result_json` 中
- **不需要修改 `BackgroundJobModel`：** 现有表结构已满足需求（`payload_json` 存储 stage 元数据，`result_json` 存储最终结果）
- **不需要新增 `JobStatus` 枚举值：** 现有 PENDING/QUEUED/RUNNING/COMPLETED/FAILED 已足够，stage 信息在 payload 内部管理

---


**阶段门槛：**
- 3.2-B 依赖 3.2-A（需要 Preparation Workflow 骨架）
- 3.2-C 依赖 3.2-B（需要 CandidateFacts）
- 3.2-D 依赖 3.2-C（需要 CandidateProfile + JobProfile）
- 3.2-E 可与 3.2-B/C/D 并行（Report 独立于 Preparation）
- 3.2-F 依赖 3.2-D（需要 Plan Generation 就绪），同时依赖 3.3 的 MCP Client 实现
- 3.2-G 依赖 3.2-A 至 3.2-F
- 3.2-H 在以上全部完成后执行

---

## 3.3 MCP Client + Nowcoder Spider Adapter + 降级策略

> **状态：** ⏳ 待开始
>
> **定位：** 在 `interview_preparation` 的 `COMPANY_INTELLIGENCE` stage 中，通过本地 stdio MCP Server 接入牛客公开面经数据，为 `InterviewPlanner` 提供可选的公司面试情报增强。
>
> **核心原则：**
>
> * 牛客面经增强属于 optional enrichment，不是 Interview Plan 的必要依赖。如果mcp调用出错，降级策略为只依赖 CandidateProfile + JobProfile 作为生成 Plan 的依据。
> * 只在 `PREPARING` 阶段调用，实时 LiveKit 面试链路禁止调用 MCP。
> * 不把第三方 Spider 的数据结构、文件路径或实现细节泄漏到业务 Service。
> * 不直接运行第三方项目原始 `mcp_server.py`，仅参考/复用牛客抓取核心逻辑，重新提供适配 Interview Coach 的结构化 MCP Tool。
> * 第三方帖子正文属于不可信外部数据，不直接进入 Planner。
> * Redis 只承担缓存职责，不作为 Company Intelligence 的权威业务存储。

---
### 3.3.0 架构决策记录

#### 决策 1：数据来源不是“牛客官方 MCP”
当前接入来源为社区项目 `interview-experience-spider` 中实现的牛客 Spider。

实际链路：
```text
Nowcoder
   ↓ HTTP
Nowcoder Spider
   ↓
Interview Coach MCP Server
   ↓ stdio MCP
NowcoderSpiderProvider
   ↓
IntelligenceService
```

因此 Provider 命名使用：
```text
community_nowcoder_spider
```

数据原始来源仍标记：
```text
source = "nowcoder"
```
避免让系统或项目介绍误认为牛客官方提供 MCP 服务。

#### 决策 2：不直接使用第三方原始 MCP Tool

原项目的 `search_nowcoder` Tool：

```text
Spider 抓取
    ↓
写 output/nowcoder.json
    ↓
MCP 返回：
“抓到多少篇 + 前几个标题 + 文件路径”
```
不适合 Interview Coach。

原因：
1. MCP 返回的是文本摘要，而不是结构化面经数据；
2. Interview Coach 若再次读取 JSON 文件，会形成 filesystem coupling；
3. 多 Interview 并发时共享 `output/nowcoder.json` 存在覆盖风险；
4. MCP Server 与 Worker 将被迫共享文件目录；
5. 无法直接做 Pydantic schema 校验。

因此新增专用 Tool：
```text
search_nowcoder_experiences
```
直接通过 MCP 返回结构化结果，不通过中间 JSON 文件交换数据。

#### 决策 3：V1 只支持 stdio MCP

第三方 Spider 属于本地进程型工具，因此 V1 不实现：

```text
Streamable HTTP
SSE
远程 MCP Server URL
HTTP MCP connection pool
OAuth
```

统一：
```text
Interview Worker
      ↓
stdio
      ↓
Nowcoder MCP subprocess
```
MCP Server 由项目内部固定模块启动，例如：

```text
python -m liverag.interview.intelligence.nowcoder_mcp_server
```

API 请求不能动态指定：
```text
command
script_path
server_url
transport
tool_name
```
避免任意命令执行和 SSRF 风险。

#### 决策 4：MCP 只负责“传输协议”，不负责 Agent 自主决策

不让 LLM 自由决定：

```text
调用哪个 MCP Tool
传什么任意参数
是否调用 collect_all / report 等其他 Tool
```

调用关系固定：
```text
IntelligenceService
       ↓
NowcoderSpiderProvider
       ↓
search_nowcoder_experiences
```
这是数据集成链路，不是 Agent Tool Planning。

#### 决策 5：原始数据与业务理解结果分层

真实 Spider 返回的主要数据实际上是：
```text
title
content
url
query
source
```

而：
```text
company
role
round
topics
questions
```
都需要 Interview Coach 自己进一步理解。

因此废弃“`InterviewExperienceSource` 同时装原始字段和推断字段”的设计，拆成：
```text
RawInterviewExperience
        ↓
Normalizer / Extractor
        ↓
NormalizedInterviewExperience
        ↓
Aggregator
        ↓
CompanyInterviewProfile
```

#### 决策 6：V1 不使用 `freshness_days`

当前 Spider 没有稳定返回帖子发布时间，因此 V1 中：
```text
published_at: datetime | None
```
保留为可选字段，但不使用其进行强过滤。
等后续确认能够稳定获取发布时间，再增加基于发布时间的 freshness 策略。

#### 决策 7：缓存采用 Fresh → Provider → Stale Fallback

由于 Spider 一次查询需要：
```text
搜索
+
逐篇拉取正文
```
调用成本明显高于普通 API，因此缓存属于 P0 能力，而不是单纯性能优化。

策略：
```text
请求
 ↓
Fresh Cache?
 ├─ YES → 直接返回
 │
 └─ NO
      ↓
   调用 Provider
      │
      ├─ 成功 → 更新缓存 → 返回
      │
      └─ 失败
           ↓
       Stale Cache?
       ├─ YES → stale fallback
       └─ NO  → degraded
```

默认建议：
```text
fresh TTL = 24h
stale TTL = 7d
```
Redis Key 的实际 TTL 使用 stale TTL；缓存对象内部记录 `fresh_until`。
Redis 丢失缓存不会影响业务正确性。
---

### 3.3.1 Provider 领域契约（`intelligence/provider.py`）

#### `InterviewRound`

复用/新增标准轮次枚举：

```python
class InterviewRound(str, Enum):
    FIRST = "first" #一面
    SECOND = "second" #二面
    THIRD = "third" #三面
    FINAL = "final" #终面
    HR = "hr" #HR面
```
不能识别的轮次使用 `None`，不强行推断。

---

#### `InterviewIntelligenceQuery`

描述：
> “业务希望查询什么面经”。

字段：
```text
company: str
role: str
region: str | None
interview_round: InterviewRound | None
limit: int = 10 #约束：默认捕捉10条数据，最多20条
```

不包含：
```text
timeout
retry
MCP tool name
pages
delay
```
这些属于 Provider / Spider 执行策略，而不是领域查询条件。

---

#### `RawInterviewExperience`

表示 Provider 真正获得的原始面经。

字段：
```text
provider
source
source_id
source_type
title
content
source_url
matched_query
published_at
retrieved_at
content_hash
```

建议语义：
```text
provider = "community_nowcoder_spider"
source = "nowcoder"
source_id
    = 牛客 uuid / content_id
source_type
    = feed / discuss
content_hash
    = normalize(title + content) 后计算 SHA-256
```

`content_hash` 用于：
* 内容指纹；
* 精确去重；
* 内容版本判断；
* 缓存审计；
* Plan 输入快照追踪。

`published_at`：
```text
datetime | None
```
当前无法稳定获取时保持 `None`。

---

#### `NormalizedInterviewExperience`

表示经过 Interview Coach 理解后的业务数据。

字段：
```text
provider
source
source_id
source_url
company
role
region
interview_round
topics
questions
published_at
retrieved_at
content_hash
```

注意：

```text
topics
questions
interview_round
```
属于 Normalizer / Extractor 的产物，不再宣称是 Provider 原始字段。

该模型不再携带完整 `content`，避免后续 Planner 意外接触第三方原文。

---

#### `ProviderSearchResult`

Provider 的统一返回 Envelope：
```text
items: list[RawInterviewExperience]

provider

fetched_at
latency_ms

discovered_count
collected_count
failed_count

partial
```

其中：
```text
partial = failed_count > 0 and collected_count > 0
```
例如：
```text
搜索发现 18 篇
成功抓取 14 篇
4 篇正文请求失败
→ collected_count = 14
→ failed_count = 4
→ partial = true
```
部分失败时不丢弃已经获得的有效数据！

---

#### `InterviewIntelligenceProvider`

定义：

```python
class InterviewIntelligenceProvider(Protocol):
    async def search_experiences(
        self,
        query: InterviewIntelligenceQuery,
    ) -> ProviderSearchResult:
        ...
```
业务层只依赖 Protocol。

---

#### `ProviderCapability`

只记录 Provider 的静态能力：
```text
provider_name
transport
tool_name
schema_version
supports_partial_results
```

V1：
```text
provider_name = community_nowcoder_spider
transport = stdio
tool_name = search_nowcoder_experiences
```
不为未来未知 Provider 设计复杂 capability framework。

---

#### `ProviderError`

统一屏蔽：
```text
MCP exception
subprocess exception
HTTP Spider exception
Pydantic validation error
```
上层只处理：
```text
ProviderError
```

建议错误码：
```text
TIMEOUT：超时
UNAVAILABLE：provider整体不可用
TOOL_NOT_FOUND：MCP Server 没有预期 Tool
CONTRACT_MISMATCH：：接口契约和代码不一致
INVALID_RESPONSE：返回数据不合法
RATE_LIMITED：被限流
NO_USABLE_DATA：没有可用数据
```

字段：
```text
code
provider
message
retryable
```

---

### 3.3.2 牛客 Spider 提取与改造

#### 可参考/复用的第三方逻辑

从 `interview-experience-spider/scrape_nowcoder.py` 中仅提取以下能力：

```text
html_to_text()

search()
    → 牛客搜索 API
    → 获取 uuid / content_id

fetch_feed()
    → Feed 帖子正文

fetch_discuss()
    → Discuss 帖子正文
```

不复用：
```text
CLI argparse
config.json 查询列表
output/*.json 文件交换
generate_report
run_all
小红书 Spider
小红书签名代码
HTML/Markdown 报告生成
```

---

#### 新增 `intelligence/nowcoder/spider.py`

负责：
```text
Nowcoder HTTP
       ↓
搜索帖子
       ↓
拉取详情
       ↓
RawNowcoderPost[]
```

新增内部模型：
```text
RawNowcoderPost:
source_id
source_type
title
content
url
matched_query
```

必须保留原项目搜索阶段已经拿到但最终丢失的：
```text
uuid
content_id
```
作为 `source_id`。

---

#### 查询数量限制

原 Spider 基于：
```text
max_pages
```
控制数量，不适合 Preparation。

增加：
```text
max_results
```
一旦获得足够的有效面经：
```text
len(collected) >= max_results
```
立即停止继续抓取。

建议默认：
```text
max_pages = 1
max_results = 10
hard max_results = 20
```
避免默认抓取数十甚至上百篇帖子。

---

#### HTTP 请求级失败处理

单篇帖子失败：
```text
记录失败
继续下一篇
```
不使整个 Spider 调用失败。
可以对明确属于瞬时网络错误的单次 HTTP 请求进行最多一次有限重试。

*禁止：*
```text
整个 search_nowcoder_experiences 自动重复执行多次
```
否则已经成功抓取的帖子会被再次请求。

---

### 3.3.3 Interview Coach MCP Server（`intelligence/mcp/server.py`）

新增 Interview Coach 自己维护的薄 MCP Server。

职责只有：
```text
暴露 Nowcoder Spider 为一个结构化 MCP Tool
```

不包含：
```text
Planner
LLM
Cache
Normalizer
Aggregator
Interview 状态
```
---

#### 唯一业务 Tool

```text
search_nowcoder_experiences
```

输入：
```text
queries: list[str]
max_results: int
```
`max_pages`、HTTP timeout、delay 等运行参数由 Server Settings 控制，不允许上游请求任意修改。
---

#### MCP 输出模型

定义：
```text
NowcoderSearchResult:

items: list[RawNowcoderPost]

discovered_count
collected_count
failed_count
partial
```
Tool 直接返回 Pydantic / typed structured result。

禁止：
```text
返回 JSON 文件路径
让 Interview Coach 再读取 output 文件
```

---

#### MCP Server 输出约束

stdio 的 stdout 属于 MCP 协议通道。

Spider 内：
```text
禁止 print() 普通日志到 stdout
```

统一使用：
```text
logging
→ stderr
```
避免破坏 MCP 消息流。

---

### 3.3.4 MCP Client（`intelligence/mcp/mcp_client.py`）

实现一个轻量、安全受控的 stdio MCP Client。

职责：

```text
启动固定 MCP subprocess
        ↓
验证 Tool contract
        ↓
调用固定 Tool
        ↓
读取 structured result
        ↓
本地再次 Pydantic 校验
```

不自行实现 MCP JSON-RPC 协议。
使用官方 Python MCP SDK。

---

#### Transport

```text
stdio only
```
不实现 Transport 抽象工厂。
未来确有远程 Provider 后再扩展 Streamable HTTP。

---

#### Server 启动

固定为项目内部模块。
概念上：

```text
sys.executable
-m
liverag.interview.intelligence.nowcoder_mcp_server
```
请求参数不得控制 executable / module path。

---

#### Tool Contract 校验

Client 第一次调用时获取 Tool metadata，并验证：

```text
存在 search_nowcoder_experiences
input schema 符合预期
支持结构化输出
```

若不符合：
```text
ProviderError(CONTRACT_MISMATCH)
```
Tool discovery 只用于契约验证，不用于让 LLM 动态选择工具。

---

#### 结构化结果

优先且正常路径只消费：

```text
structuredContent
```

随后再次执行本地：
```text
NowcoderSearchResult.model_validate(...)
```

文本 content 仅用于：
```text
错误诊断 / 兼容日志
```
不作为正式数据源。

---

#### 生命周期

当前版本不实现：
```text
MCP session pool
MCP process pool
connection pool
```
一次 Company Intelligence Provider 查询使用一次受控 MCP Client 生命周期即可。
如果后续 profiling 证明 subprocess 启动成本明显，再考虑 Worker 级长生命周期 Client。

---

#### Timeout

定义：
```text
INTELLIGENCE_PROVIDER_TIMEOUT_SECONDS
```
作为整个 MCP Provider stage 的时间预算。

推荐初始值：
```text
20～30 秒
```

并要求：
```text
provider timeout < Worker 整体 task timeout
```

超时：
```text
ProviderError(TIMEOUT)
```
交由 Intelligence Service 做 stale fallback。

---

### 3.3.5 Nowcoder Spider Provider（`intelligence/nowcoder_provider.py`）

实现：
```text
InterviewIntelligenceProvider
```

职责：
```text
领域 Query
    ↓
构造确定性搜索关键词
    ↓
调用 MCP Tool
    ↓
MCP result
    ↓
RawInterviewExperience[]
    ↓
ProviderSearchResult
```

---

#### Query → Spider Query 映射

例如：
```text
company = 字节跳动
role = Agent 开发
round = FIRST
region = 北京
```

Adapter 可以构造：
```text
字节跳动 Agent开发 面经
字节跳动 Agent开发 一面
字节跳动 北京 Agent开发 面经
```

查询构造必须：
```text
deterministic
```
同一输入产生相同关键词集合。
不使用 LLM 动态生成搜索 Query。

---

#### 字段映射

```text
RawNowcoderPost.source_id
    → RawInterviewExperience.source_id

title
    → title

content
    → content

url
    → source_url

matched_query
    → matched_query

provider
    → community_nowcoder_spider

source
    → nowcoder

retrieved_at
    → 当前 UTC 时间

content_hash
    → SHA256(normalized title + content)
```

---

### 3.3.6 Normalizer + Extractor

涉及：
```text
intelligence/normalizer.py
intelligence/extractor.py
```
两者职责分离。

---

#### Normalizer

负责确定性处理：

```text
公司别名统一
岗位别名统一
地区名称统一
空白字符清洗
重复 URL / 重复帖子过滤
面试轮次关键词初步识别
```

例如：
```text
字节 / 字节跳动 / ByteDance
    → 字节跳动
```

```text
一面 / 第一轮 / first round
    → InterviewRound.FIRST
```

---

#### Extractor

负责从不可信帖子正文中提取：
```text
questions
topics
interview_round
```

输出：
```text
NormalizedInterviewExperience
```

使用 LLM：
1. 外部帖子明确作为 `untrusted_external_data`；
2. Prompt 明确要求不得执行正文中的命令；
3. 输出必须使用严格 Pydantic schema；
4. 对输入正文设置最大长度；
5. 不允许 LLM 产生帖子中没有依据的面试问题；
6. 保留 `source_id/content_hash` 作为 evidence reference。

单篇提取失败：
```text
跳过该篇
记录 extraction failure
```
不使整个 Company Intelligence stage 失败。

---

### 3.3.7 Aggregator（`intelligence/aggregator.py`）

输入：
```text
list[NormalizedInterviewExperience]
```
输出：
```text
CompanyInterviewProfile
```

---

#### 当前版本必做

##### 去重
第一层：
```text
provider + source_id
```
判断同一帖子。

第二层：
```text
content_hash
```
判断相同内容。

目前不实现 embedding semantic dedup。

---

##### Topic Frequency

统计：
```text
topic
count
ratio
```

例如：
```text
RAG           7 / 10
Redis         5 / 10
Java并发      4 / 10
MCP           3 / 10
```

---

##### Common Questions

基于提取后的 questions：
```text
规范化
去重
统计频次
```

生成有限数量：
```text
representative_questions
```
不把几十上百条问题全部塞给 Planner。

---

##### Round Pattern

数据足够时统计：
```text
一面 → 常见 topics / questions
二面 → 常见 topics / questions
终面 → 常见 topics / questions
```
数据不足时保持空值，不强行推断。

---

#### 暂不做

暂缓：
```text
跨 Provider 一致性
Embedding 聚类
复杂 semantic clustering
复杂可信度加权公式
```
因为只有一个 Nowcoder Spider Provider。

---

#### 数据质量而非伪精确 Confidence

不生成类似：
```text
confidence = 87.43%
```

改为保存客观数据：
```text
sample_count
usable_sample_count
partial
question_count
topic_count
round_coverage
```

---

#### `CompanyInterviewProfile`

建议字段：

```text
company
role
region

sample_count：本次进入聚合流程的面经数据样本量
usable_sample_count：真正结构化、可统计的面经数据量

top_topics：高频技术主题
representative_questions：代表性高频题目
round_patterns：不同面试轮次常见的考察内容：基础知识/项目拷打

evidence_refs：帖子来源，只保存provider + source_id + content_hash作为识别

generated_at：画像生成时间
snapshot_hash：内容指纹
```

不直接嵌入第三方面经全文。

---

### 3.3.8 Intelligence Service（`intelligence/service.py`）

Service 是 Company Intelligence 的业务入口。

3.2 Preparation Workflow 不直接操作：
```text
MCP Client
Spider
Redis cache
Normalizer
Aggregator
```

只调用：
```python
await intelligence_service.get_company_profile(query)
```

---

#### Service 返回模型

新增：
```text
IntelligenceEnrichmentResult
```

字段：
```text
status
profile: CompanyInterviewProfile | None
provider: str | None
degraded: bool  是否降级
degradation_reasons: list[str] | None  降级理由
snapshot_hash: str | None
cache_age_seconds: int | None
```

---

#### `IntelligenceStatus`

建议：
```text
DISABLED：feature flag未开启
SKIPPED：无company等不满足查询条件
CACHE_HIT：fresh cache直接命中
FRESH：provider成功获取并且生成新profile
PARTIAL：provider部分失败，但有效数据足够生成profile
STALE_FALLBACK：provider失败，使用过期但仍在stale window的缓存
DEGRADED：provider失败且没有可用缓存，采用降级策略
```

---

#### Feature Flag

作用：控制是否启用 牛客MCP 增强

继续保留：
```text
INTERVIEW_INTELLIGENCE_ENABLED=false
```
默认关闭。

Feature flag 关闭属于：
```text
DISABLED
```
不认为是系统故障。

---

#### Query 缺 company

如果：

```text
company is None / blank
```

返回：
```text
status = SKIPPED
profile = None
```

建议同步修正 3.2：
```text
COMPANY_NOT_PROVIDED
```
不再标记整个 Preparation 为 `degraded=true`，因为这是业务输入缺失导致主动跳过，并非系统降级。

---

#### Cache Key

基于规范化后的：

```text
provider
company
role
region
interview_round
schema_version
adapter_version
```
构造 canonical JSON，再 SHA-256：

```text
interview:intelligence:v1:{fingerprint}
```
不要直接把很长的 company/role 字符串拼成 Redis Key。

---

#### Cache Envelope

缓存：

```text
profile
provider
fetched_at
fresh_until
stale_until
snapshot_hash
```

Redis TTL：
```text
stale_until - now
```

---

#### Provider 成功

流程：

```text
ProviderSearchResult
       ↓
Normalizer
       ↓
Extractor
       ↓
NormalizedInterviewExperience[]
       ↓
Aggregator
       ↓
CompanyInterviewProfile
       ↓
写 Cache
       ↓
返回 FRESH / PARTIAL
```

---

#### Provider 失败

可降级错误，见 3.3.1 ProviderError类：

```text
TIMEOUT：超时
UNAVAILABLE：provider整体不可用
TOOL_NOT_FOUND：MCP Server 没有预期 Tool
CONTRACT_MISMATCH：：接口契约和代码不一致
INVALID_RESPONSE：返回数据不合法
RATE_LIMITED：被限流
NO_USABLE_DATA：没有可用数据
```

处理：
```text
有 stale cache
    → STALE_FALLBACK

无 stale cache
    → DEGRADED
    → profile = None
```
均不阻止后续 Plan。

---

### 3.3.9 与 3.2 Preparation Workflow 集成

`COMPANY_INTELLIGENCE` stage 改为：
```text
JOB_PROFILE_GENERATION
        ↓
COMPANY_INTELLIGENCE
        ↓
PLAN_GENERATION
```

流程：
```text
1. company 不存在
   → status = SKIPPED
   → profile = None
   → 继续 PLAN_GENERATION

2. feature flag disabled
   → status = DISABLED
   → profile = None
   → 继续 PLAN_GENERATION

3. feature flag enabled
   → IntelligenceService.get_company_profile()

4. 获得 IntelligenceEnrichmentResult

5. stage_results.company_intelligence
   保存完整 enrichment metadata

6. 如果：
   PARTIAL / STALE_FALLBACK / DEGRADED
   → workflow.degraded = true
   → degradation_reasons 追加原因

7. PLAN_GENERATION
   输入：
   CandidateProfile
   + JobProfile
   + CompanyInterviewProfile | None
```

---

#### Planner 输入原则

基础路径：
```text
CandidateProfile
+
JobProfile
+
QuestionBank
        ↓
InterviewPlan
```

增强路径：
```text
CandidateProfile
+
JobProfile
+
QuestionBank
+
CompanyInterviewProfile
        ↓
InterviewPlan
```

CompanyInterviewProfile 只能用于：
```text
调整 topic 权重
调整题目优先级
选择 representative questions
调整不同轮次重点
辅助追问策略
```

不能：
```text
覆盖 CandidateProfile
覆盖 JobProfile
让面经中未验证的信息成为硬性事实
```

---

#### Plan 审计元数据

建议给 `InterviewPlan` 增加字段：
```text
intelligence_status:IntelligenceStatus | None = None
```

全部带默认值，保持旧 Plan 兼容。
完整 `CompanyInterviewProfile` 不必再次复制进入 Plan；
统一保存在 Preparation stage metadata 中。后续如出现独立审计/重放需求再扩展

---

### 3.3.10 安全、隐私与外部数据边界

#### 不可信数据隔离

数据链必须是：
```text
Nowcoder Post
    ↓
RawInterviewExperience
    ↓
Sanitize / Extract
    ↓
NormalizedInterviewExperience
    ↓
Aggregator
    ↓
CompanyInterviewProfile
    ↓
Planner
```

禁止：
```text
Nowcoder raw content
        ↓
InterviewPlanner
```

---

#### Prompt Injection

帖子中的：

```text
命令
System Prompt
“忽略之前要求”
链接
代码
工具调用指令
```
全部作为待分析文本。

任何第三方正文都不能改变：
```text
System Prompt
Tool 权限
MCP 配置
Planner 控制逻辑
```

---

#### PII
不主动保留：
```text
用户名
联系方式
手机号
邮箱
个人主页
其他无关身份信息
```

CompanyInterviewProfile 只保存：
```text
公司
岗位
轮次
技术主题
问题
统计信息
source evidence reference
```

---

#### MCP 执行安全

固定：
```text
stdio
固定 module
固定 Tool
固定参数 schema
```

禁止：
```text
用户传 command
用户传 script path
用户传 MCP URL
用户传 tool name
用户传任意 transport
```
---

#### 日志安全

日志允许：
```text
query fingerprint
provider
status
cache hit/miss
latency
discovered_count
collected_count
failed_count
snapshot_hash
ProviderErrorCode
```

日志禁止：
```text
帖子全文
Authorization
Cookie
Secret
大量个人信息
```

---

#### 爬虫约束
* 仅用于个人学习和面试训练用途；
* 设置 `max_results` / `max_pages`；
* 设置请求间隔；
* 使用缓存减少重复抓取；
* 不进行高频并发爬取；
* 遵守目标平台规则和第三方项目许可证。

---

### 3.3.11 Settings

在 `liverag/config/settings.py` 新增：
```text
InterviewIntelligenceSettings
```

---

### 3.3.12 测试

#### Spider 单元测试

测试：

```text
search response 解析
Feed 类型解析
Discuss 类型解析
HTML → text
source_id 保留
max_results 提前停止
帖子详情部分失败
content 为空
```

所有测试使用 mock HTTP，不访问真实牛客。

---

#### MCP Server 契约测试

验证：

```text
search_nowcoder_experiences Tool 存在
input schema 正确
structured output schema 正确
返回 NowcoderSearchResult
不会依赖 output JSON 文件
```

---

#### MCP Client 测试

测试：

```text
正常 stdio 调用
Tool 不存在
Tool schema 不匹配
structuredContent 非法
subprocess 启动失败
provider timeout
```

---

#### Provider 测试

测试：

```text
InterviewIntelligenceQuery
    ↓
deterministic search queries

NowcoderSearchResult
    ↓
RawInterviewExperience

source_id
source_type
content_hash
retrieved_at
partial
```

---

#### Normalizer / Extractor 测试

测试：

```text
公司别名
岗位别名
轮次识别
空内容
恶意 prompt injection 文本
questions extraction
topics extraction
Pydantic validation failure
单篇失败不影响其他面经
```

---

#### Aggregator 测试

测试：

```text
source_id 去重
content_hash 去重
topic frequency
representative questions
round pattern
sample_count
partial data
snapshot_hash 稳定性
```

相同输入必须生成相同 snapshot hash。

---

#### Cache 测试

使用 fakeredis：

```text
fresh cache hit
fresh expired → provider
provider success → refresh
provider failure + stale → stale fallback
provider failure + no stale → degraded
cache key fingerprint
schema version 改变 → 不命中旧 cache
```

---

#### Intelligence Service 测试

覆盖：

```text
feature disabled
company missing
fresh success
partial success
cache hit
stale fallback
provider timeout
invalid response
no usable data
```

所有预期 Provider 故障都不得抛到 Preparation Workflow 阻断 Plan。

---

#### Preparation Integration Test

验证：

```text
CandidateProfile ✅
JobProfile ✅
MCP ❌
      ↓
CompanyInterviewProfile = None
      ↓
InterviewPlan 仍生成
      ↓
Interview.state = READY
```

以及：

```text
MCP ✅
↓
CompanyInterviewProfile
↓
Planner
↓
InterviewPlan
↓
intelligence_snapshot_hash 已记录
```

---

#### 实时链路回归测试

验证：

```text
LiveKit Session
Question Selection
Answer Evaluation
Follow-up
TTS/STT
```

调用日志中：

```text
0 次 MCP / Nowcoder Spider 调用
```

---

#### 新增文件对应的职责：

```text
provider.py
    → domain contract / Protocol / ProviderError

nowcoder_spider.py
    → 牛客 HTTP 搜索和帖子抓取

mcp/server.py
    → stdio MCP Server + structured Tool

mcp_client.py
    → 安全受控 MCP stdio Client

nowcoder_provider.py
    → Query ↔ MCP ↔ RawExperience Adapter

normalizer.py
    → deterministic normalization

extractor.py
    → untrusted raw text → structured experience

aggregator.py
    → experience[] → CompanyInterviewProfile

cache.py
    → Redis fresh/stale cache

service.py
    → 完整编排 + degradation
```

---

#### 修改

```text
pyproject.toml
    → MCP SDK 依赖

liverag/config/settings.py
    → InterviewIntelligenceSettings

.env.example
    → Intelligence 配置

liverag/interview/schemas.py
    → CompanyInterviewProfile / Intelligence metadata
       （如果 provider.py 不统一定义 schema）

liverag/interview/jobs/tasks.py
    → COMPANY_INTELLIGENCE stage 调 IntelligenceService

liverag/interview/application/profile/planner 相关代码
    → Planner 接收 CompanyInterviewProfile | None

InterviewPlan schema
    → 可选 intelligence_status /
       intelligence_provider /
       intelligence_snapshot_hash
```
不新增数据库表。

---


### 3.3.15 验收标准

完成 3.3 必须满足：

```text
1. 牛客数据只能在 PREPARING 阶段访问。

2. Interview Coach 不依赖第三方 Spider 的 output JSON 文件。

3. MCP Tool 返回结构化数据并通过 Pydantic 校验。

4. Provider 可正确保留牛客 source_id 和 content_hash。

5. 单篇帖子抓取失败不会导致整批失败。

6. Provider 超时不会阻止 Interview Plan 生成。

7. Provider 完全不可用时：
   CandidateProfile + JobProfile + QuestionBank
   仍可生成有效 Plan。

8. Redis fresh cache 命中时不调用 Spider。

9. Provider 失败且存在 stale cache 时能够继续使用旧 Profile。

10. 外部帖子全文不会直接进入 InterviewPlanner。

11. LLM 无权决定 MCP executable、transport 或 tool。

12. MCP/Spider 不出现在 LiveKit 实时链路日志中。

13. 相同 CompanyInterviewProfile 输入生成稳定 snapshot_hash。

14. Preparation 最终可记录：
    intelligence_status
    provider
    snapshot_hash
    degradation reason

15. Redis 缓存丢失不会破坏 PostgreSQL 中已有 Interview / Plan 数据。
```

---

### 3.3 最终链路

```text
POST /interviews/{id}/prepare?async=true
                 ↓
       interview_preparation
                 ↓
       RESUME_PARSING
                 ↓
     CANDIDATE_PROFILE
                 ↓
        JOB_PROFILE
                 ↓
      COMPANY_INTELLIGENCE
                 │
                 ▼
       IntelligenceService
                 │
          ┌──────┴──────┐
          │             │
      Fresh Cache      Miss
          │             │
          │             ▼
          │    NowcoderSpiderProvider
          │             │
          │             ▼
          │       MCP stdio Client
          │             │
          │             ▼
          │      Nowcoder MCP Server
          │             │
          │             ▼
          │       Nowcoder Spider
          │             │
          │             ▼
          │          Nowcoder
          │             │
          │             ▼
          │    RawInterviewExperience[]
          │             │
          │             ▼
          │      Normalizer / Extractor
          │             │
          │             ▼
          │ NormalizedInterviewExperience[]
          │             │
          │             ▼
          │        Aggregator
          │             │
          └─────────────┤
                        ▼
             CompanyInterviewProfile
                        │
                        ▼
                PLAN_GENERATION
                        │
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
CandidateProfile    JobProfile    CompanyInterviewProfile?
        └───────────────┼────────────────┘
                        ▼
               InterviewPlanner
                        ↓
                 InterviewPlan
                        ↓
                      READY
```

**核心降级保证：**
```text
Nowcoder / Spider / MCP / Cache
任何一个外部增强环节失败

                ↓

CompanyInterviewProfile = None

                ↓

CandidateProfile
+
JobProfile
+
QuestionBank

                ↓

InterviewPlan 正常生成
```

---


## 整体实施顺序

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
