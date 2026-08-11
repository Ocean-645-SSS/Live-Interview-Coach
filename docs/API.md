# Interview Coach API

API 默认监听 `http://127.0.0.1:9821`，完整且可执行的接口定义以 `/docs` 生成的 OpenAPI 为准。

## 基础接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | API 存活检查 |
| GET | `/rag/ready` | RAG Core 就绪状态 |

## 知识库网关

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `/rag/knowledge-bases` | 列出或创建知识库 |
| GET / PATCH / DELETE | `/rag/knowledge-bases/{kb_id}` | 查询、更新或删除知识库 |
| GET | `/rag/knowledge-bases/{kb_id}/ready` | 查询索引就绪状态 |
| GET | `/rag/knowledge-bases/{kb_id}/documents` | 文档列表 |
| POST | `/rag/knowledge-bases/{kb_id}/documents/text` | 导入文本 |
| POST | `/rag/knowledge-bases/{kb_id}/documents/files` | 上传文件 |
| GET | `/rag/knowledge-bases/{kb_id}/documents/{document_id}` | 文档详情 |
| GET | `/rag/knowledge-bases/{kb_id}/documents/{document_id}/source` | 获取原始文件 |
| DELETE | `/rag/knowledge-bases/{kb_id}/documents/{document_id}` | 删除文档 |
| GET | `/rag/knowledge-bases/{kb_id}/jobs/{job_id}` | 索引任务状态 |
| POST | `/rag/knowledge-bases/{kb_id}/query/context` | 获取检索上下文与证据 |

这些接口由 API 转发至内部 RAG Core。RAG 未就绪时返回 `503`。

## 面试接口

所有路径以 `/api/interviews` 开头。

| 方法 | 相对路径 | 说明 |
| --- | --- | --- |
| POST | `/` | 创建面试 |
| POST | `/prepared` | 根据资料、岗位与题库创建已准备面试 |
| GET | `/{interview_id}` | 获取面试和计划状态 |
| PUT | `/{interview_id}/plan` | 按预期版本保存计划 |
| POST | `/{interview_id}/prepare` | 启动异步准备 |
| GET | `/{interview_id}/preparation` | 查询准备进度 |
| POST | `/{interview_id}/sessions` | 创建面试 Session |
| GET | `/sessions/{session_id}` | 获取 Session |
| POST | `/sessions/{session_id}/attempts` | 创建 LiveKit 连接 Attempt |
| GET | `/attempts/{attempt_id}` | 获取 Attempt |
| GET / POST | `/sessions/{session_id}/events` | 读取或写入状态事件 |
| GET / POST | `/sessions/{session_id}/answers` | 读取或提交回答 |
| POST | `/answers/{answer_id}/evaluation` | 触发单题评价 |
| POST / GET | `/sessions/{session_id}/report` | 生成或读取报告 |
| GET | `/reports` | 按目标知识库查询历史报告 |
| GET | `/skill-progress` | 获取长期能力画像 |
| GET | `/skill-progress/{skill_key}` | 获取单项能力详情 |
| POST | `/jobs/demo` | 创建示例后台任务 |
| GET | `/jobs/{job_id}` | 查询后台任务 |

## 状态码与一致性

- `404`：资源不存在。
- `409`：乐观锁冲突、重复事件或当前状态不允许操作。
- `422`：输入或状态迁移不合法。
- `502`：外部模型调用失败。
- `503`：数据库、队列或 RAG 等必要依赖不可用。

计划与状态写入使用版本约束；重试同一业务事件时必须复用幂等标识，不能用新请求覆盖既有事实记录。
