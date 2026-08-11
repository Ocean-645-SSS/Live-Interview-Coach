# 发布验收清单

## 自动化检查

```powershell
uv run pytest
uv run ruff check liverag tests
uv run ruff format --check liverag tests

Set-Location LiveRAG-Fronted/agent-starter-react
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm build
```

测试范围包括 Interview 状态机、计划与题库、评价和术语纠正、PostgreSQL 持久化、后台任务、能力画像、RAG 文档与网关、LiveKit Agent 配置及 STT/TTS 适配。

## 端到端验收

- [ ] Compose 服务健康，`GET /health` 正常且 `/rag/ready` 已就绪。
- [ ] 数据库已执行 `alembic upgrade head`。
- [ ] 可创建候选人和岗位知识库，导入文档并等待索引完成。
- [ ] prepared interview 能生成冻结计划、Session 和 Attempt。
- [ ] 浏览器可加入 Attempt 对应房间，Agent 能提问、收音、追问并推进下一题。
- [ ] 重复事件和过期版本不会产生重复数据或覆盖新状态。
- [ ] 面试完成后可读取报告、历史报告和能力画像。
- [ ] 专业术语纠正保留原始转写，且纠正记录可重放到规范化文本。
- [ ] 前端知识库、岗位目标、面试、报告和进度页面均可从全新克隆仓库构建。

真实供应商凭据、麦克风权限、WebRTC 网络、模型配额和公网 LiveKit UDP/TLS 必须在目标环境另行验收。
