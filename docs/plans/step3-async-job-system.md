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

## 3.2 迁移：简历解析 / Profile 生成 / Plan 生成 / Report 生成

> **状态：** ⏳ 待开始

将现有同步执行的耗时准备流程迁移为后台异步任务，通过 `?async=true` 参数实现渐进式迁移（旧同步 API 继续可用）。

### 3.2.1 简历解析任务（`resume_parse`）

**目标：** 将 `artifact_reader.py` 中的文档解析逻辑封装为后台任务。

- [ ] 注册 `resume_parse` 任务 handler
- [ ] 输入：`kb_id`、文档 ID 列表
- [ ] 输出：结构化 `CandidateProfile` 候选字段
- [ ] 幂等键：`{kb_id}:{document_hash}`
- [ ] 任务流程：
  1. 通过 RagGateway 获取候选人文档 evidence
  2. LLM 解析 → Pydantic 校验 → 结构化字段
  3. 结果写入 `CandidateProfile`
- [ ] API：`POST /api/interviews/{id}/prepare?async=true` → 返回 `job_id`

### 3.2.2 Profile 生成任务（`profile_generation`）

**目标：** 将 `profile_service.py` 的画像生成逻辑封装为后台任务。

- [ ] 注册 `profile_generation` 任务 handler
- [ ] 子类型：`candidate_profile` / `job_profile`
- [ ] 输入：`interview_id`、`kb_id`（候选人资料库 + 岗位资料库）
- [ ] 输出：`CandidateProfile` + `JobProfile`（冻结快照存入 InterviewPlan）
- [ ] 幂等键：`{interview_id}:{profile_type}:{kb_hash}`
- [ ] 任务流程：
  1. 从 RAG 检索结构化证据
  2. LLM 生成画像 → Pydantic 校验
  3. 持久化画像 + 关联到 InterviewPlan
- [ ] API：`POST /api/interviews/{id}/prepare?async=true` → 返回 `job_id`
- [ ] API：`GET /api/interviews/{id}/preparation` → 返回进度/降级信息

### 3.2.3 Plan 生成任务（`plan_generation`）

**目标：** 将 `planner.py` 的面试计划生成逻辑封装为后台任务。

- [ ] 注册 `plan_generation` 任务 handler
- [ ] 输入：`interview_id`、`candidate_profile_id`、`job_profile_id`、`config`
- [ ] 输出：`InterviewPlan`（含 section、question 选择、时长分配、rationale）
- [ ] 幂等键：`{interview_id}:plan:{config_hash}`
- [ ] 任务流程：
  1. 加载 CandidateProfile + JobProfile + CompanyInterviewProfile（如有）
  2. 加载题库候选题目
  3. LLM 生成计划 → Pydantic 校验 → 时长/题数程序复核
  4. 持久化 InterviewPlan（状态 `PREPARING` → `READY`）
- [ ] API：`POST /api/interviews/{id}/prepare?async=true` → 返回 `job_id`

### 3.2.4 Report 生成任务（`report_generation`）

**目标：** 将 `report.py` 的报告生成逻辑封装为后台任务。

- [ ] 注册 `report_generation` 任务 handler
- [ ] 输入：`session_id`
- [ ] 输出：`InterviewReport`（含 summary、skill_scores、strengths、weaknesses、recommendations、evidence_refs）
- [ ] 幂等键：`{session_id}:report`
- [ ] 任务流程：
  1. 加载 InterviewPlan + 逐题 Answer + Evaluation
  2. LLM 生成报告 → Pydantic 校验 → 程序计算加权分
  3. 持久化 InterviewReport + 更新 SkillProgress
- [ ] API：`POST /api/interview-sessions/{id}/complete?async=true` → 返回 `job_id`

### 3.2.5 渐进式 API 改造

- [ ] `POST /api/interviews/{id}/prepare` 增加 `?async=true` 参数
- [ ] `POST /api/interview-sessions/{id}/complete` 增加 `?async=true` 参数
- [ ] async 模式：创建 Job → 入队 → 立即返回 `{job_id, status: "PENDING"}`
- [ ] sync 模式（默认）：保持原有同步执行行为
- [ ] `GET /api/interviews/{id}/preparation` 返回准备进度和各子任务状态
- [ ] `GET /api/interviews/jobs/{job_id}` 复用 3.1 通用查询端点

### 3.2.6 测试

- [ ] 各 task handler 单元测试（fake RAG/LLM）
- [ ] Worker 执行 profile → plan 依赖链测试
- [ ] 幂等：同一 interview_id 重复投递不重复执行
- [ ] 降级：LLM 失败 → Job FAILED → API 返回错误不影响其他 interview 功能
- [ ] 渐进式：`async=true` 和 `async=false` 两种路径均通过

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
