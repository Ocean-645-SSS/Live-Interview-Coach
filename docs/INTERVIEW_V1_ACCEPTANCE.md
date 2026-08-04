# Interview Coach V1 真实环境验收报告

验收日期：2026-08-04

## 结论

Interview Coach V1 的后端、数据库、LiveKit 房间调度、实时 TTS、回答评价、状态机和报告链路已在 Docker 真实环境中跑通。

当前结论：**核心业务验收通过**。浏览器麦克风授权、真人语音 STT 准确率和实际听感仍需人工体验验收。

## 已通过链路

1. Docker Compose 启动 FastAPI、LightRAG、LiveKit、普通 Agent、Interview Agent 和 Next.js。
2. FastAPI `/health` 返回 `ok`，OpenAPI 包含 12 条 Interview 路由。
3. 从版本化题库创建 Interview、InterviewPlan 和 Session，初始状态为 `READY`。
4. Next.js 创建 Attempt、专属房间、受限 token，并定向调度 `interview-agent`。
5. RTC 客户端加入房间后，Interview Agent 成功加入并注册远端音频轨道。
6. DashScope TTS 实际输出 3,756 个音频帧，Session 自动推进到 `LISTENING`。
7. RTC 断开后 Attempt 最终更新为 `DISCONNECTED`，并记录断开时间。
8. 回答、事件与 Session 状态在同一业务事务中持久化。
9. 真实 LLM 完成回答评价，后端计算加权总分 92.5。
10. 单题面试按追问上限进入 `COMPLETING`，报告生成后进入 `COMPLETED`。
11. 报告查询返回总分、优点、改进项和逐题评价；报告页面返回 HTTP 200。

## 联调中修复的问题

- 前端 Dockerfile 构建阶段改为继承依赖阶段，避免 Corepack 重复联网下载 pnpm。
- DashScope TTS 等待服务端 `session.created` 后再发送配置。
- 同一条 DashScope WebSocket 只发送一次 `session.update`，后续语音复用会话。
- LLM 返回的 `weighted_score` 不再作为可信输入，由后端根据四维分数和 rubric 统一计算。

## 自动化回归

- 后端：352 passed，2 条第三方弃用警告。
- Interview：54 passed。
- Agent：51 passed。
- Ruff：通过。
- 前端 ESLint：通过。
- 前端 TypeScript：通过。
- Next.js production build：通过。

## 尚需人工体验

- Chrome/Edge 麦克风授权是否顺畅。
- 真人中文语音经过火山引擎 STT 后的准确率和断句表现。
- 开场白、题目、追问与结束语的实际音质和播放节奏。
- 页面在桌面端和移动端的交互与视觉体验。

这些项目不阻塞 V1 核心业务闭环，但应在正式对外演示前完成。
