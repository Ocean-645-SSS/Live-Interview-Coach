# LiveRAG API 文档

本文档面向前端联调。管理 API 统一访问：

```text
http://127.0.0.1:9821
```

内部 RAG Core Service 的 `/v1/*` 只给后端使用，前端不要直接调用。

---

## 通用约定

### Envelope 格式

RAG、模型配置、history、overview 接口使用统一 envelope：

```json
{
  "request_id": "...",
  "status": "ok",
  "data": {},
  "metrics": {},
  "error": null
}
```

失败时：

```json
{
  "request_id": "...",
  "status": "error",
  "data": null,
  "metrics": {},
  "error": {"type": "ErrorType", "message": "错误说明"}
}
```

Prompt 和 session 读取接口保持轻量返回，不包 envelope。

### Interview API 错误约定

Interview API (`/api/interviews/*`) 不使用 envelope，直接返回业务数据或 HTTP 错误：

| HTTP 状态码 | 场景 |
|------------|------|
| 404 | 业务资源不存在 |
| 409 | 并发冲突 / 重复事件 |
| 422 | 非法业务操作 / 参数校验失败 |
| 502 | LLM Provider 调用失败 |
| 503 | 服务未就绪 |

---

## 核心规则（通用语音助手）

- 一次语音通话只锁定一个 `kb_id`。
- 通话开始后不能切换知识库。
- 不支持多知识库同时查询，不支持 `kb_ids`。
- 通话开始前一次性渲染 `session_system_prompt.md`。
- 通话中只使用固定 instructions、当前 messages、当前用户输入和可选 tool result。
- 挂断后把本次 messages 压缩为当前知识库的一条 `history.jsonl`，随后清空 messages。
- `rag_tool_mode` 只支持 `auto` 和 `never`。

---

## 一、健康与运行态

### GET /health

返回管理 API 是否可用。

```json
{"status":"ok"}
```

### GET /runtime/state

返回当前运行状态、当前知识库、RAG 模式、最近回答长度和 active session。

关键字段：

```json
{
  "active_session": {
    "started_at": "...",
    "ended_at": null,
    "job_id": "AJ_xxx",
    "room_id": "RM_xxx",
    "voice": {},
    "knowledge_base": {"kb_id": "kb_xxx", "name": "考研资料"},
    "session_prompt_chars": 4200,
    "history_count": 6,
    "knowledge_overview": {"generated": false, "fallback": false}
  },
  "rag_tool_mode": "auto",
  "last_assistant_chars": 80,
  "last_tts_text_chars": 80,
  "last_answer_too_long": false,
  "knowledge_base": {
    "configured": {"kb_id": "kb_xxx", "name": "考研资料"},
    "active_session": {"kb_id": "kb_xxx", "name": "考研资料"},
    "locked": true,
    "pending_reconnect": false
  }
}
```

---

## 二、语音模型配置

语音模型配置写入 `~/.LiveRAG/model/config.json`。修改后当前通话不热切，挂断重连后生效。

### GET /model/config

读取下次通话使用的 STT、LLM、TTS 配置，并返回前端模型选择需要的 `options`。密钥只返回掩码和是否已设置。

### GET /model/options

只读取模型选择页选项，不读取当前配置。

返回内容：
- `stt.providers`：已适配的语音识别 provider（当前：`volcengine_bigmodel`）
- `tts.providers`：已适配的语音合成 provider（`minimax`、`dashscope_realtime`）
- 每个 provider 都包含 `models`、`voices`、`config_fields`、默认值和 `verified` 标记

### PUT /model/config

局部更新语音模型配置。

```json
{
  "voice": {
    "stt": {
      "provider": "volcengine_bigmodel",
      "model": "bigmodel",
      "app_id": "...",
      "access_token": "..."
    },
    "tts": {
      "provider": "dashscope_realtime",
      "model": "qwen3-tts-flash-realtime",
      "voice": "Cherry",
      "api_key": "sk-..."
    },
    "llm": {
      "model": "gemma-4-e4b-it-4bit",
      "base_url": "http://127.0.0.1:8000/v1",
      "api_key": "..."
    }
  }
}
```

前端如果回填后端返回的掩码值（如 `sk*****abcd1234`），原样提交不会覆盖真实密钥。

### GET /model/effective-state

返回下次通话配置、当前或最近通话实际生效配置、是否需要重连。

---

## 三、Context Model 配置

Context Model 独立于语音 LLM，用于：
- 生成 `knowledge_overview.md`
- 挂断后压缩 `history.jsonl`

配置写入 `~/.LiveRAG/model/context_config.json`。

### GET /model/context-config

```json
{
  "status": "ok",
  "data": {
    "context_model": {
      "model": "qwen-max",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": "sk*****abcd1234",
      "api_key_masked": "sk*****abcd1234",
      "api_key_set": true,
      "temperature": 0,
      "max_tokens": 2000,
      "max_session_chars": 16000,
      "history_reference_limit": 8,
      "timeout_ms": 15000,
      "effective": "next_session"
    }
  }
}
```

### PUT /model/context-config

局部更新。密钥掩码原样提交表示不修改密钥。

---

## 四、Prompt

前端只暴露 `SOUL.md`，不暴露系统提示词模板。`system_prompt_template.md` 是后端内部模板文件，不能通过 API 读取或修改。

### GET /prompt/soul

读取 `~/.LiveRAG/prompts/SOUL.md`。如果文件不存在，后端会初始化默认人设。

```json
{"content":"..."}
```

### PUT /prompt/soul

覆盖用户定义的 Agent 人设。后端不会自动修改 SOUL。

```json
{"content":"新的 SOUL 内容"}
```

---

## 五、会话数据（通用语音助手）

### GET /session/{session_id}/messages

读取当前通话 messages。挂断后后端会清空 messages。

查询参数：`limit`（可选，返回最近 N 条）。

### GET /session/{session_id}/rag-context

读取当前通话 RAG 工具调用事实日志。包含 evidence documents、chunks、metrics 等。

### GET /session/turns

按 `turn_index` 聚合 messages 和 RAG 依据。通话中推荐使用。

`rag.status` 取值：`not_queried` / `hit` / `miss` / `failed`

### DELETE /session/{session_id}

清空当前 session messages、rag_context、session_system_prompt 和 runtime state。

---

## 六、会话知识库选择

### GET /session/knowledge-base

返回下次通话配置和当前通话锁定知识库。

### PUT /session/knowledge-base

设置下次通话使用的知识库。只能在没有 active call 时调用。

```json
{"kb_id":"kb_xxx"}
```

当前通话未结束时返回 `409 KnowledgeBaseLocked`。

---

## 七、RAG 配置

### GET /rag/config

读取语音链路 RAG 查询配置。关键字段：`enabled`、`query_mode`、`top_k`、`chunk_top_k`、`context_max_chars`、`cache_ttl_s`、`enable_rerank`、`rag_tool_mode`。

### PUT /rag/config

局部更新 RAG 查询配置。

`rag_tool_mode` 只允许 `auto` 或 `never`。提交 `always` 返回 `422`。

---

## 八、知识库管理

### GET /rag/ready

检查内部 RAG Core Service 是否 ready。

### GET /rag/knowledge-bases

返回全部知识库摘要（名称、文档数、chunk 数等）。

### POST /rag/knowledge-bases

创建知识库。

```json
{"name":"考研资料","description":""}
```

### GET /rag/knowledge-bases/{kb_id}

读取单个知识库详情和统计。

### PATCH /rag/knowledge-bases/{kb_id}

更新知识库名称和描述，不重建索引。

### DELETE /rag/knowledge-bases/{kb_id}

删除知识库 metadata、原文件、LightRAG storage 和 logs。

约束：
- `default` 禁止删除。
- 当前通话锁定的知识库禁止删除。

### GET /rag/knowledge-bases/{kb_id}/ready

预热指定知识库 engine。

---

## 九、知识库上下文

### GET /rag/knowledge-bases/{kb_id}/context/overview

读取当前知识库固定概览。如果文件不存在，后端会创建默认 `knowledge_overview.md` 和 meta。

### PUT /rag/knowledge-bases/{kb_id}/context/overview

手动覆盖当前知识库的 `knowledge_overview.md`。不是重新生成，生成时机只在索引任务完成后由后端触发。

---

## 十、文档管理

### GET /rag/knowledge-bases/{kb_id}/documents

读取指定知识库文档列表。查询参数：`page`（默认 1）、`page_size`（默认 50）。

### GET /rag/knowledge-bases/{kb_id}/documents/{document_id}

读取文档详情、解析内容、chunks 和原始状态。

### GET /rag/knowledge-bases/{kb_id}/documents/{document_id}/source

读取原文件。查询参数：`disposition=inline`（预览）/ `disposition=attachment`（下载）。

### POST /rag/knowledge-bases/{kb_id}/documents/text

导入文本并保存为原始文本文件。

```json
{"text": "内容", "file_source": "manual-note.md", "document_id": "可选"}
```

### POST /rag/knowledge-bases/{kb_id}/documents/files

上传一个或多个文件。multipart 字段：`files`。

### GET /rag/knowledge-bases/{kb_id}/jobs/{job_id}

查询文档解析和索引任务状态。

### DELETE /rag/knowledge-bases/{kb_id}/documents/{document_id}

删除单个文档、原文件和派生索引。

### DELETE /rag/knowledge-bases/{kb_id}/documents

清空该知识库全部文档、原文件和索引，但保留知识库本身。

---

## 十一、查询接口

### POST /rag/knowledge-bases/{kb_id}/query/context

只查询指定知识库上下文，用于调试检索质量。

### POST /rag/knowledge-bases/{kb_id}/query/data

只查询指定知识库结构化检索数据。

### POST /rag/session-query/context

按当前 active session 锁定知识库查询上下文。不接收 `kb_id`。

### POST /rag/session-query/data

按当前 active session 锁定知识库查询结构化数据。不接收 `kb_id`。

---

## 十二、Interview Coach V1 API

Interview API 使用 `/api/interviews` 前缀。第一步使用 SQLite，第二步起支持 PostgreSQL。不需要 Redis 或用户身份。

### 12.1 创建面试

#### POST /api/interviews

创建 Interview 草稿（仅 config）。

```json
{"title": "阿里 Java 面试", "config": {...}}
```

#### POST /api/interviews/prepared

从版本化题库选题，同时创建 Interview、冻结的 Plan 和 Session。返回完整的 `PreparedInterviewResult`。

```json
{
  "title": "阿里 Java 面试",
  "config": {
    "duration_minutes": 30,
    "difficulty": "INTERMEDIATE",
    "question_count": 8,
    "max_follow_ups_per_question": 2,
    "target_kb_id": "kb_xxx",
    "target_company": "阿里巴巴",
    "target_role": "Java 后端开发"
  }
}
```

### 12.2 面试查询

#### GET /api/interviews/{interview_id}

获取 Interview 聚合状态。

#### GET /api/interviews/reports?target_kb_id=xxx

按目标岗位资料库列出历史面试报告。

### 12.3 面试计划

#### PUT /api/interviews/{interview_id}/plan

冻结面试计划。

```json
{"plan": {...}, "expected_version": 1}
```

### 12.4 Session 管理

#### POST /api/interviews/{interview_id}/sessions

创建业务面试 Session。

#### GET /api/interviews/sessions/{session_id}

读取当前状态、题目位置和恢复信息。

#### GET /api/interviews/sessions/{session_id}/events

返回面试状态变化事件列表。

#### GET /api/interviews/sessions/{session_id}/answers

返回已提交的最终回答列表。

### 12.5 实时连接

#### POST /api/interviews/sessions/{session_id}/attempts

创建 LiveKit room Attempt，返回唯一 room name 和 attempt ID。

#### GET /api/interviews/attempts/{attempt_id}

读取连接状态（CREATED / CONNECTED / DISCONNECTED / FAILED）。

Next.js 的 `/api/connection-details` 接收 `sessionId`，调用 Attempt API 后签发仅能加入该 room 的 token，并固定调度 `interview-agent`。Worker metadata 格式：

```json
{"session_id":"session_xxx","attempt_id":"attempt_xxx","participant_identity":"user_xxx"}
```

### 12.6 状态事件

#### POST /api/interviews/sessions/{session_id}/events

提交普通状态迁移事件。

```json
{"event_id": "event_xxx", "event_type": "START", "payload": {}}
```

#### POST /api/interviews/sessions/{session_id}/answers

提交最终回答并完成 ANSWER_RECEIVED 状态迁移。

```json
{
  "attempt_id": "attempt_xxx",
  "event_id": "event_xxx",
  "transcript": "我认为...",
  "answer_number": 1,
  "started_at": "2026-08-06T10:00:00+00:00",
  "ended_at": "2026-08-06T10:02:00+00:00"
}
```

### 12.7 评价与报告

#### POST /api/interviews/answers/{answer_id}/evaluation

异步生成回答的结构化评价（四维评分 + 追问决策）。

#### POST /api/interviews/sessions/{session_id}/report

生成面试报告。支持同步和异步两种模式。

**查询参数：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `async` | `false` | `true` 时创建后台 Job 异步生成，立即返回 `{job_id, status}` |

```json
// async=false（默认）— 同步返回报告：
{
  "report_id": "report_xxx",
  "session_id": "session_xxx",
  "state": "COMPLETED",
  "content": { ... }
}

// async=true — 异步返回 Job 引用：
{
  "job_id": "job_abc123",
  "status": "PENDING"
}
```

Worker 后台执行流程：加载 InterviewPlan + Answers + Evaluations → `InterviewReportBuilder` 聚合生成 → 持久化 `InterviewReportModel.state = COMPLETED`。

**幂等键：** `report:{session_id}` — 同一 session 重复调用不会产生重复报告。

#### GET /api/interviews/sessions/{session_id}/report

读取已生成的报告；尚未生成时返回 `null`。

### 12.8 异步 Job 管理

#### POST /api/interviews/jobs/demo

创建 Demo 后台任务，验证异步链路。

```json
// Request:
{"delay_seconds": 3.0}

// Response:
{"job_id": "job_xxx", "status": "PENDING"}
```

执行流程：`asyncio.sleep(delay)` → 返回 `{"message": "hello async", "job_id": "...", "slept_seconds": ...}`。

#### GET /api/interviews/jobs/{job_id}

查询任意后台任务的完整状态。

```json
{
  "job_id": "job_xxx",
  "job_type": "demo",
  "status": "COMPLETED",
  "attempt": 1,
  "max_attempts": 3,
  "result": {"message": "hello async", "job_id": "job_xxx", "slept_seconds": 3.0},
  "error": null,
  "created_at": "2026-08-05T10:00:00+00:00",
  "started_at": "2026-08-05T10:00:01+00:00",
  "completed_at": "2026-08-05T10:00:04+00:00"
}
```

`status` 取值：`PENDING` / `QUEUED` / `RUNNING` / `COMPLETED` / `FAILED`。

### 12.9 异步面试准备

#### POST /api/interviews/{interview_id}/prepare?async=true

触发面试异步准备 Workflow，创建 `interview_preparation` Job 并入队，立即返回 Job 引用。

**前置条件：** Interview 已创建（含 `config`）。

**查询参数：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `async` | `false` | 必须传 `true`（`async=false` 暂未实现，请使用 `POST /api/interviews/prepared`） |

```json
// Response:
{"job_id": "job_abc123", "status": "PENDING"}
```

**幂等机制：**
1. 按 `idempotency_key = "interview_preparation:{interview_id}"` 查询已有 Job
2. 已有 COMPLETED → 直接返回已有 `job_id`
3. 已有 PENDING/QUEUED/RUNNING → 直接返回已有 `job_id`
4. Redis 锁（TTL 60s）→ 短暂重复投递保护
5. PostgreSQL 唯一约束 → 最终幂等保证

**状态联动：** Job 创建成功后，`Interview.state` 从 `CREATED` 转为 `PREPARING`。

**Worker 内部执行 5 个 stage（顺序不可跳过）：**

| Stage | 内部步骤名 | 职责 | 可降级 |
|-------|-----------|------|--------|
| `RESUME_PARSING` | `RESUME_PARSE` | RAG 检索 → LLM 抽取 `CandidateFacts` | ❌ |
| `CANDIDATE_PROFILE_GENERATION` | `CANDIDATE_PROFILE` | `CandidateFacts` → `InterviewProfileService` → `CandidateProfile` | ❌ |
| `JOB_PROFILE_GENERATION` | `JOB_PROFILE` | JD KB evidence → `InterviewProfileService` → `JobProfile` | ❌ |
| `COMPANY_INTELLIGENCE` | `COMPANY_INTELLIGENCE` | 调用 `IntelligenceService` → `CompanyInterviewProfile`（牛客 MCP 面经） | ✅ |
| `PLAN_GENERATION` | `PLAN_GENERATION` | `CandidateProfile` + `JobProfile` + `CompanyInterviewProfile?` → `InterviewPlanner` → 持久化 `InterviewPlan` → `Interview.state = READY` | ❌ |

**降级说明：**
- 未提供 `target_company` → `COMPANY_INTELLIGENCE` 跳过（不算降级）
- `IntelligenceService` 未注入或调用失败 → `degraded=true`，继续 `PLAN_GENERATION`
- 牛客 MCP / Spider / Cache 任意环节失败 → `CompanyInterviewProfile = None`，Plan 仍基于 CandidateProfile + JobProfile 正常生成

#### GET /api/interviews/{interview_id}/preparation

查询面试准备的当前进度，前端通过此端点轮询。

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
  "degradation_reasons": ["COMPANY_INTELLIGENCE: NOWCODER_MCP_UNAVAILABLE"],
  "started_at": "2026-08-07T10:00:00+00:00",
  "updated_at": "2026-08-07T10:02:30+00:00",
  "error": null
}
```

此端点通过 `JobRepository.get_job_by_resource(job_type="interview_preparation", business_resource_id=interview_id)` 查询最新 Preparation Job，将其 payload 中的 stage 元数据反序列化返回。

---

## 十三、前端 BFF 代理

Next.js 通过 `/api/liverag/[...path]` 将所有管理请求代理到 FastAPI。通用语音的 LiveKit connection details 由 `/api/connection-details` 签发。

Interview Coach 页面也沿用此 BFF 模式，通过 `/api/interview/connection-details` 签发面试专用 token 并调度 `interview-agent` Worker。
