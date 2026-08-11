# LiveRAG API 文档

管理 API 默认监听 `http://127.0.0.1:9821`。运行服务后可在 `/docs` 查看由 FastAPI 生成的完整 OpenAPI 定义；本文是提交到仓库的稳定接口索引。

## 通用约定

- RAG 网关接口使用统一 envelope：`request_id`、`status`、`data`、`metrics`、`error`。
- 配置接口中的密钥只会以掩码形式返回；提交掩码值不会覆盖现有密钥。
- `404` 表示资源不存在，`409` 表示并发冲突或不允许的当前状态，`422` 表示请求或状态迁移不合法。
- Interview Coach 的实体接口直接返回领域记录；模型调用失败可能返回 `502`，依赖的异步基础设施不可用时可能返回 `503`。

## 运行与语音配置

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | API 存活检查 |
| GET | `/runtime/state?session_id=` | 指定通用语音会话的运行态与知识库绑定 |
| GET / PUT | `/model/config` | 下一场会话的 STT、LLM、TTS 配置 |
| GET | `/model/options` | 前端可选择的语音模型与音色 |
| GET | `/model/effective-state/{session_id}` | 配置值与当前/最近会话实际生效值 |
| GET / PUT | `/model/context-config` | 会话历史压缩使用的 Context Model 配置 |
| GET / PUT | `/prompt/soul` | 通用语音助手的角色提示词 |

## 通用语音会话

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/sessions` | 列出本地会话 |
| GET | `/sessions/{session_id}` | 读取会话运行状态 |
| GET | `/sessions/{session_id}/messages` | 对话消息，可选 `limit` |
| GET | `/sessions/{session_id}/turns` | 含 RAG 使用状态的轮次记录 |
| GET | `/sessions/{session_id}/rag-context` | 检索审计记录，可选 `limit` |
| POST | `/sessions/{session_id}/end` | 显式结束会话 |
| GET | `/sessions/{session_id}/export` | 导出会话数据 |
| DELETE | `/sessions/{session_id}` | 删除已结束会话；活动会话会被拒绝 |
| GET / PUT | `/session/knowledge-base` | 读取或设置下一场通话的默认知识库 |
| GET | `/sessions/{session_id}/knowledge-base` | 查询指定会话冻结的知识库 |

## 知识库与文档

所有 `/rag/*` 接口由管理 API 转发到内部 RAG Core；RAG 未就绪时会返回 `503`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/rag/ready` | RAG Core 就绪状态 |
| GET / PUT | `/rag/config` | RAG 客户端配置 |
| GET / POST | `/rag/knowledge-bases` | 列出或创建知识库 |
| GET / PATCH / DELETE | `/rag/knowledge-bases/{kb_id}` | 查询、更新或删除知识库 |
| GET | `/rag/knowledge-bases/{kb_id}/ready` | 指定知识库是否可查询 |
| GET / PUT | `/rag/knowledge-bases/{kb_id}/context/overview` | 知识库概览 |
| GET | `/rag/knowledge-bases/{kb_id}/documents` | 文档列表 |
| POST | `/rag/knowledge-bases/{kb_id}/documents/text` | 导入文本；请求体为文档内容与元数据 |
| POST | `/rag/knowledge-bases/{kb_id}/documents/files` | `multipart/form-data` 上传文件；可附 `pdf_password` |
| GET | `/rag/knowledge-bases/{kb_id}/documents/{document_id}` | 文档详情及分块信息 |
| GET | `/rag/knowledge-bases/{kb_id}/documents/{document_id}/source` | 下载/预览原始文件 |
| DELETE | `/rag/knowledge-bases/{kb_id}/documents/{document_id}` | 删除一份文档 |
| GET | `/rag/knowledge-bases/{kb_id}/jobs/{job_id}` | 查询索引任务 |
| POST | `/rag/knowledge-bases/{kb_id}/query/context` | 只取检索上下文和证据 |
| POST | `/rag/knowledge-bases/{kb_id}/query/data` | 查询结构化检索数据 |
| POST | `/rag/knowledge-bases/{kb_id}/query/answer` | 由 RAG Core 生成答案 |
| POST | `/rag/session-query/context` | 以当前会话绑定的知识库查询上下文 |
| POST | `/rag/session-query/data` | 以当前会话绑定的知识库查询数据 |

查询请求遵循 RAG Core 的 `QueryRequest`。回答涉及知识库事实时，调用方应使用 `context` 或 `data` 中的证据，不应把无命中的模型推断描述为知识库结论。

## Interview Coach

Interview Coach 的路由前缀为 `/api/interviews`。`InterviewConfig` 约束面试时长、难度、题量、追问上限、回答超时、候选人知识库与目标岗位等配置。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/interviews` | 创建待配置的面试 |
| POST | `/api/interviews/prepared` | 按候选人资料、岗位与题库创建已准备面试 |
| GET | `/api/interviews/{interview_id}` | 查询面试与计划状态 |
| PUT | `/api/interviews/{interview_id}/plan` | 保存版本化面试计划，需携带 `expected_version` |
| POST | `/api/interviews/{interview_id}/sessions` | 创建一次面试 Session |
| POST | `/api/interviews/{interview_id}/prepare` | 异步执行简历、岗位、情报与计划准备 |
| GET | `/api/interviews/{interview_id}/preparation` | 查询准备工作状态 |
| GET | `/api/interviews/sessions/{session_id}` | 查询面试会话和状态 |
| POST | `/api/interviews/sessions/{session_id}/attempts` | 创建 LiveKit 连接尝试 |
| GET | `/api/interviews/attempts/{attempt_id}` | 查询连接尝试 |
| POST / GET | `/api/interviews/sessions/{session_id}/events` | 写入或读取状态事件 |
| POST / GET | `/api/interviews/sessions/{session_id}/answers` | 写入或读取逐题回答 |
| POST | `/api/interviews/answers/{answer_id}/evaluation` | 触发单题评价 |
| POST / GET | `/api/interviews/sessions/{session_id}/report` | 创建或获取最终报告 |
| GET | `/api/interviews/reports?target_kb_id=` | 按目标知识库列出报告历史 |
| GET | `/api/interviews/skill-progress?candidate_kb_id=` | 获取长期能力画像与训练建议 |
| GET | `/api/interviews/skill-progress/{skill_key}` | 获取单项能力详情 |
| POST / GET | `/api/interviews/jobs/demo`、`/api/interviews/jobs/{job_id}` | 创建示例异步任务或查询任务状态 |

实时语音通道由 LiveKit 承载，不经由 HTTP 直接传音频。面试 Agent 只接受与当前 `session_id`、`attempt_id` 和 `question_id` 匹配的提交控制消息。
