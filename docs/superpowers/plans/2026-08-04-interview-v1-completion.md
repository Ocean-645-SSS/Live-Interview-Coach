# Interview Coach V1 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 Interview Coach 第一步范围内的单机闭环：创建、实时面试、逐题评价、断线 Attempt、报告读取、独立 LiveKit Worker、Compose 和三个最小前端页面。

**Architecture:** FastAPI 是管理入口，`InterviewService` 是业务入口，SQLAlchemy/SQLite 保存权威状态，LiveKit Interview Worker 只通过 Service 推进面试。Next.js 通过同源 BFF 创建 Interview/Session/Attempt，并在 token 中固定调度 `interview-agent` 与 job metadata。

**Tech Stack:** Python 3.10+、FastAPI、SQLAlchemy 2、SQLite、LiveKit Agents 1.4、OpenAI-compatible evaluation、Next.js 15、React 19、TypeScript、Docker Compose。

## Global Constraints

- 只完成第一步，不引入 Alembic、PostgreSQL、Redis、Background Worker、登录或多用户体系。
- 不恢复旧 `sqlite3` Store，所有 Interview 数据只经过 SQLAlchemy Repository。
- Worker 不自由生成面试问题；问题来自冻结的 `InterviewPlan`。
- final transcript、Event、Answer 和 Session 继续使用现有原子事务与乐观锁。
- 当前工作区已有用户改动，不覆盖或删除无关文件。
- 本轮不自动创建 Git commit。

---

### Task 1: 补全 Attempt 和读取 API

**Files:**
- Modify: `liverag/interview/service.py`
- Modify: `liverag/api/interview_routes.py`
- Modify: `tests/api/test_interview_routes.py`

**Interfaces:**
- Consumes: `InterviewRepository.create_attempt()`、`get_session()`、`get_report_by_session()`。
- Produces: `InterviewService.create_attempt()`、Session/Attempt/Report 查询路由。

- [ ] 增加 Service 用例，创建唯一 `interview-<session>-<suffix>` 房间并返回 Attempt。
- [ ] 增加 `POST /api/interviews/sessions/{session_id}/attempts`。
- [ ] 增加 Session、Attempt、事件、回答和 Report 的 GET 接口，供 Live 与报告页面轮询。
- [ ] 测试不存在资源返回 404、创建 Attempt 返回固定 Session 归属。
- [ ] 运行 `python -m pytest tests/api/test_interview_routes.py -q`，预期全部通过。

### Task 2: 固化 Worker 任务契约和断线恢复

**Files:**
- Modify: `liverag/interview_main.py`
- Modify: `liverag/interview/controller.py`
- Modify: `tests/interview/test_interview_worker.py`
- Modify: `tests/interview/test_services.py`

**Interfaces:**
- Consumes: job metadata `{session_id, attempt_id}` 和 `InterviewAgentController`。
- Produces: `interview-agent` 独立 worker、重复连接可诊断的 Attempt 状态。

- [ ] 校验 Attempt 必须属于 Session 和当前 room。
- [ ] 对 READY/INTRODUCTION/活动状态定义清楚的首次进入与恢复行为。
- [ ] 确保 TTS 播放完成后才记录 `QUESTION_ASKED`/`FOLLOW_UP_ASKED`。
- [ ] 确保关闭、失败分别写入 `DISCONNECTED`、`FAILED`。
- [ ] 运行 Worker 与 Interview 全量测试。

### Task 3: Compose 和命令入口

**Files:**
- Modify: `pyproject.toml`
- Modify: `docker-compose.yml`
- Modify: `.env.example`

**Interfaces:**
- Produces: `liverag-interview-agent` 命令和 Compose 服务。

- [ ] 增加 `liverag-interview-agent = "liverag.interview_main:main"`。
- [ ] Compose 增加独立 Worker，共享 `/data` SQLite，并使用 `interview-agent`。
- [ ] 补充前端 `INTERVIEW_AGENT_NAME` 和管理 API 配置说明。
- [ ] 执行 Compose 配置解析，预期服务配置有效。

### Task 4: Next.js 创建、Live、报告页面

**Files:**
- Create: `app/interviews/page.tsx`
- Create: `app/interviews/[sessionId]/live/page.tsx`
- Create: `app/interviews/[sessionId]/report/page.tsx`
- Create: `components/interview/interview-create.tsx`
- Create: `components/interview/interview-live.tsx`
- Create: `components/interview/interview-report.tsx`
- Create: `types/interview.ts`
- Modify: `app/api/connection-details/route.ts`
- Modify: `app/page.tsx`

**Interfaces:**
- Consumes: FastAPI Interview API、Attempt 返回值、LiveKit job metadata。
- Produces: 创建、Live、报告三个最小页面。

- [ ] 创建页面提交 InterviewConfig 和冻结的最小计划，然后创建 Session。
- [ ] Live 页面先创建 Attempt，再签发只能进入该 room 且固定调度 `interview-agent` 的 token。
- [ ] connection-details 在 `RoomAgentDispatch.metadata` 中写入 Session/Attempt ID。
- [ ] Report 页面轮询 Session，完成后读取结构化报告。
- [ ] 运行 `pnpm lint`、`pnpm typecheck`、`pnpm build`，预期全部通过。

### Task 5: 文档、回归与 V1 验收

**Files:**
- Modify: `docs/plans/interview-coach-plan.md`
- Modify: `docs/INTERVIEW_COACH_ARCHITECTURE.md`
- Modify: `docs/API.md`

**Interfaces:**
- Produces: 与实际实现一致的 V1 状态和启动说明。

- [ ] 更新 API、Worker metadata、Compose 和页面路径说明。
- [ ] 只勾选已经由代码和测试证实的第一步条目。
- [ ] 运行 Ruff、Interview/API 全量 pytest、前端 lint/typecheck/build。
- [ ] 检查 V1 不含 Alembic/PostgreSQL/Redis/用户体系依赖。

## Self-Review

- Spec coverage：覆盖后端闭环、独立 Worker、Attempt、Compose 和三个最小前端页面；LightRAG 继续作为现有候选人材料基础设施，不在实时逐轮链路新增阻塞查询。
- Placeholder scan：无 TBD、TODO 或未定义接口。
- Type consistency：前端使用 `session_id`、`attempt_id`、`room_name`；Worker metadata 使用相同字段。
