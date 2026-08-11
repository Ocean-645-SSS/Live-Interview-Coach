# 发布边界与已知限制

本文记录当前版本已知且需在部署或产品层面明确处理的边界，不包含已被实现替代的开发期问题。

## 1. 完整前端源码未被 Git 跟踪

`docker-compose.yml` 需要 `LiveRAG-Fronted/agent-starter-react` 作为前端构建上下文，但根目录 `.gitignore` 忽略整个 `LiveRAG-Fronted/` 目录，当前 Git 索引中没有该目录。直接克隆仓库后，后端可以使用，但 Compose 的前端镜像构建缺少上下文。

**发布处理**：在公开发布完整栈前，将前端源码纳入版本控制或调整 Compose 为已发布的前端镜像/独立仓库；确认后再把 README 中的全栈启动命令作为对外承诺。

## 2. 生产环境必须显式完成数据库迁移

Interview Coach 使用 PostgreSQL + Alembic。Compose 会启动数据库和业务服务，但生产发布流程仍应在部署前或发布任务中执行 `alembic upgrade head`，并对迁移版本进行记录。不要依赖空数据库自动具备面试表结构。

## 3. 外部模型与实时媒体依赖

实时语音和评价依赖外部模型供应商与网络：火山引擎 STT、DashScope TTS/LLM、RAG LLM、Embedding 服务及 LiveKit。仓库可验证配置和内部逻辑，但不能替代目标网络、配额、地区可用性或供应商 API 变更的验收。

**上线前检查**：使用生产凭据进行一次真实的“入房 → 说话 → 检索 → 回答 → 挂断”测试，并记录错误日志与模型耗时。



