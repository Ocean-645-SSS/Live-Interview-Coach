# Interview Coach V1 Real Environment Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在本机真实 Docker、LiveKit、LightRAG、STT、TTS 和评价模型配置下验证 Interview Coach V1 完整链路。

**Architecture:** Docker Compose 启动 LiveKit、RAG Core、FastAPI、通用 Agent、Interview Agent 和 Next.js。验收先检查服务与日志，再通过真实 API 创建面试和 Attempt，最后在浏览器完成语音、评价、追问、报告及断线恢复。

**Tech Stack:** Docker Compose、LiveKit、FastAPI、SQLite、LightRAG、Next.js、Volcengine STT、DashScope-compatible LLM/TTS。

## Global Constraints

- 不输出 `.env.local` 中的任何密钥原文。
- 不引入 Alembic、PostgreSQL、Redis 或用户体系。
- 不删除现有数据库和用户文件。
- 发现故障时先保存日志和复现证据，再做最小修复。
- 本轮不自动创建 Git commit。

---

### Task 1: 配置预检

**Files:**
- Inspect: `.env.local`
- Inspect: `docker-compose.yml`

**Interfaces:**
- Produces: 密钥是否存在、Compose 是否有效、端口是否冲突的脱敏结果。

- [ ] 用布尔值检查 LiveKit、STT、TTS、LLM、RAG LLM 和 Embedding 配置是否存在。
- [ ] 运行 `docker compose config --quiet`，预期退出码为 0。
- [ ] 检查 3001、7880、9721、9821 端口占用和现有容器状态。

### Task 2: 启动真实服务

**Files:**
- Inspect: Docker service logs

**Interfaces:**
- Produces: 六个可运行服务及健康状态。

- [ ] 运行 `docker compose up -d --build`。
- [ ] 等待 RAG healthcheck 和 API `/health` 成功。
- [ ] 检查 `liverag-agent` 与 `liverag-interview-agent` 已向 LiveKit 注册。
- [ ] 检查前端 `/interviews` 返回 HTTP 200。

### Task 3: API 和数据库 happy path

**Files:**
- Inspect: `/data/liverag.db` through API only

**Interfaces:**
- Produces: READY Interview、Session、CREATED Attempt 和限定 room。

- [ ] 调用 `POST /api/interviews/prepared` 创建一题面试。
- [ ] 调用 Attempt API 并核对 Session、Attempt 和 room 关系。
- [ ] 查询 Session/Attempt/Event/Answer/Report 接口，确认初始状态一致。

### Task 4: 浏览器实时语音验收

**Files:**
- Inspect: browser and container logs

**Interfaces:**
- Produces: final transcript、Answer、Evaluation、状态迁移和 Report。

- [ ] 打开 `/interviews/{session_id}/live`，允许麦克风和音频播放。
- [ ] 验证开场白、问题、最终转写、评价后的追问或结束语。
- [ ] 中途断开并重新连接，确认恢复当前题或最近追问。
- [ ] 完成最后一题并打开报告页，确认分数、优点和改进项存在。

### Task 5: 结果记录和回归

**Files:**
- Create: `docs/INTERVIEW_V1_ACCEPTANCE.md`
- Modify only if defects are found: affected source and tests

**Interfaces:**
- Produces: PASS/FAIL 验收记录、复现信息和修复验证。

- [ ] 记录每个服务、API、语音步骤和断线恢复的实际结果。
- [ ] 对发现的缺陷增加自动化测试并做最小修复。
- [ ] 重新运行后端 pytest、前端 lint/typecheck/build 和 Compose 配置检查。

## Self-Review

- Spec coverage：覆盖真实配置、服务启动、API、语音、评价、报告和断线恢复。
- Placeholder scan：所有步骤都有明确命令或可观察结果。
- Type consistency：全程使用 `session_id`、`attempt_id`、`room_name` 和 `interview-agent`。
