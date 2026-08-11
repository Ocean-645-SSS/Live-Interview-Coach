# 发布验收与测试范围

本文替代第一版开发期的问题清单，记录当前发布版本应验证的行为边界。它不是线上故障台账；仍存在的产品/部署限制见 [发布边界](KNOWN_ISSUE.md)。

## 自动化测试覆盖

测试目录覆盖以下模块：

| 范围 | 代表性验证 |
| --- | --- |
| `tests/agent/` | Agent 配置、热词选择与注入、STT 生命周期、转向检测、RAG 客户端 |
| `tests/api/` | 健康检查、配置 API、会话管理、知识库选择、RAG 网关、Interview 路由 |
| `tests/context/` | 消息/审计存储、追问改写、历史压缩与渲染 |
| `tests/rag/` | 文件名处理、解析、元数据、知识库、服务就绪与配置 |
| `tests/interview/` | 状态机、计划、题库、评价、PostgreSQL 持久化、后台任务、能力画像 |

发布前执行：

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## 端到端验收清单

### 通用语音助手

- [ ] Compose 服务均处于健康或运行状态，`GET /health` 返回 `ok`，`GET /rag/ready` 显示 RAG 已就绪。
- [ ] 创建知识库、上传文本或文件、等待任务完成后，文档列表显示原始文件名与索引状态。
- [ ] 绑定知识库后发起语音会话，能听到 TTS 回答并在会话 API 中看到消息与检索审计记录。
- [ ] 查询库外事实时，回答不会声称该事实来自当前知识库。
- [ ] 修改语音模型配置后，新会话使用新配置，已有会话保持启动时的配置快照。

### Interview Coach

- [ ] 在已迁移的 PostgreSQL 与可用 Redis 上创建 prepared interview，生成计划、Session 和 Attempt。
- [ ] 前端可加入 Attempt 对应的 LiveKit 房间，Agent 在 `LISTENING` 状态接收当前题目的回答。
- [ ] 提交回答后，状态进入评价并按结果追问或进入下一题；重复事件不产生重复数据。
- [ ] 完成面试后可读取报告，并在有评价证据时查询能力画像。
- [ ] 包含常见专业术语的回答会保留原始转写；若发生规范化，纠正记录能重放得到规范化文本。

## 不在自动化测试中替代的检查

以下依赖真实外部凭据、设备或网络，应在目标部署环境手工确认：麦克风权限与 WebRTC 连通性、火山引擎 STT、DashScope TTS/LLM、Embedding 服务、外部公司情报源，以及公网 LiveKit 的 UDP/TLS 配置。
