# LiveRAG Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在相邻目录交付可连接 LiveRAG 管理 API 与 LiveKit 的 Next.js 前端，并完成工程、构建、Compose 和浏览器验证。

**Architecture:** 浏览器只请求 Next.js 同源 `/api/liverag/*` 代理和 `/api/connection-details`；服务端代理转发至管理 API，token 路由仅在 Node.js 服务端读取 LiveKit Secret 并签发短期 token。语音页通过 LiveKit React hooks 管理房间和媒体，知识库页通过类型化 API client 管理单库 CRUD、文档上传与任务轮询。

**Tech Stack:** Next.js App Router、React、TypeScript、Tailwind CSS、Lucide React、livekit-client、@livekit/components-react、livekit-server-sdk。

## Global Constraints

- 前端最终位置固定为 `../LiveRAG-Fronted/agent-starter-react`，兼容现有 Compose build context。
- 浏览器只访问 `liverag/api/` 管理 API，不直接访问内部 `/v1/*`。
- 一次通话只锁定一个 `kb_id`，通话期间禁止切库。
- `LIVEKIT_API_SECRET` 只能由 Next.js 服务端读取，不得进入 `NEXT_PUBLIC_*` 或浏览器响应。
- 不使用 mock 数据伪装后端成功；离线、空、失败和处理中状态必须可见。
- 不修改后端业务代码和用户 `.env.local`。

---

### Task 1: 工程骨架、类型与同源边界

**Files:**
- Create: `frontend-staging/agent-starter-react/package.json`
- Create: `frontend-staging/agent-starter-react/{tsconfig.json,next.config.ts,postcss.config.mjs,eslint.config.mjs,Dockerfile,.dockerignore,.gitignore,.env.example,README.md}`
- Create: `frontend-staging/agent-starter-react/app/{layout.tsx,globals.css}`
- Create: `frontend-staging/agent-starter-react/types/liverag.ts`
- Create: `frontend-staging/agent-starter-react/lib/api/{client.ts,server.ts}`
- Create: `frontend-staging/agent-starter-react/app/api/liverag/[...path]/route.ts`

**Interfaces:**
- Produces: `apiRequest<T>(path, options): Promise<T>`，统一解包 envelope 并抛出 `ApiError`。
- Produces: `GET/POST/PUT/PATCH/DELETE /api/liverag/*` 同源代理，容器内读取 `LIVERAG_API_BASE`。

- [ ] 写入工程配置与严格 TypeScript 类型，脚本包含 `lint`、`typecheck`、`build`。
- [ ] 实现代理对 JSON、multipart 和文件响应的流式透传，不记录 Authorization 或请求体。
- [ ] 运行 `pnpm lint && pnpm typecheck`，预期无错误。

### Task 2: 安全 LiveKit 连接信息

**Files:**
- Create: `frontend-staging/agent-starter-react/app/api/connection-details/route.ts`
- Create: `frontend-staging/agent-starter-react/lib/livekit/connection.ts`

**Interfaces:**
- Produces: `POST /api/connection-details`，响应 `{serverUrl, roomName, participantName, participantToken}`。
- Consumes: 服务端环境变量 `LIVEKIT_URL`、`LIVEKIT_API_KEY`、`LIVEKIT_API_SECRET`、`AGENT_NAME`。

- [ ] 校验同源请求与必需环境变量，为房间和参与者生成不可预测名称。
- [ ] 使用 `RoomAgentDispatch`/`RoomConfiguration` 将命名 Agent 调度到房间，token 仅授予对应房间并设置短期 TTL。
- [ ] 确认响应与客户端 bundle 均不包含 API Secret。

### Task 3: 实时语音页面

**Files:**
- Create: `frontend-staging/agent-starter-react/app/page.tsx`
- Create: `frontend-staging/agent-starter-react/components/voice/{voice-experience.tsx,audio-orb.tsx,transcript-panel.tsx,control-dock.tsx,knowledge-picker.tsx,status-strip.tsx}`
- Create: `frontend-staging/agent-starter-react/hooks/{use-backend-status.ts,use-session-transcript.ts}`

**Interfaces:**
- Consumes: `/health`、`/runtime/state`、`/session/knowledge-base`、`/rag/knowledge-bases`、LiveKit token route。
- Produces: 真实 Room 连接、麦克风/摄像头/屏幕共享、音频播放、字幕与断开流程。

- [ ] 在连接前并行加载健康、运行态和知识库，锁定时禁用切换。
- [ ] 使用 LiveKit hooks 读取 participant/track 音频状态，驱动五柱可视化并提供 reduced-motion 降级。
- [ ] 展示 YOU/AI 最近消息，优先使用 LiveKit transcription，使用后端 session turns 补充并去重。
- [ ] 实现浮动控制栏、设备 disabled 状态、开始/结束和错误反馈。

### Task 4: 知识库管理页面

**Files:**
- Create: `frontend-staging/agent-starter-react/app/knowledge/page.tsx`
- Create: `frontend-staging/agent-starter-react/components/knowledge/{knowledge-workspace.tsx,knowledge-sidebar.tsx,knowledge-header.tsx,document-toolbar.tsx,document-grid.tsx,document-card.tsx,knowledge-dialogs.tsx,job-progress.tsx}`
- Create: `frontend-staging/agent-starter-react/hooks/use-job-poller.ts`
- Create: `frontend-staging/agent-starter-react/components/ui/{dialog.tsx,toast.tsx,empty-state.tsx}`

**Interfaces:**
- Consumes: 所有需求列出的知识库、文档、ready、job 和 session lock API。
- Produces: CRUD、文本导入、multipart 多文件上传、索引状态轮询、搜索、网格/列表切换和危险操作确认。

- [ ] 按 envelope 真实字段渲染统计、状态、错误和 metrics，不构造假数据。
- [ ] 上传 FormData 的 `files` 数组；文本请求发送 `{text,file_source}`。
- [ ] 从成功响应提取 `job_id` 并轮询到 `processed`、`partial_failed` 或 `failed`，卸载时中止。
- [ ] 删除知识库、清空和删除文档均经过明确确认并防止重复提交。

### Task 5: 设计系统、响应式与可访问性

**Files:**
- Modify: `frontend-staging/agent-starter-react/app/globals.css`
- Modify: 所有页面组件。

**Interfaces:**
- Produces: 纸白 `#fbfbfa`、墨黑 `#111111`、细线 `#e7e7e3`、状态绿 `#237a4b`、危险红 `#d93838` 的克制视觉系统。

- [ ] 精确实现桌面大留白、圆角工作台、五柱声波和底部胶囊控制栏。
- [ ] 实现移动端侧栏折叠、控制栏换行、文档单列和可点击目标尺寸。
- [ ] 添加键盘焦点、aria-label、对比度、`prefers-reduced-motion` 和非颜色状态提示。

### Task 6: 构建、容器与浏览器验证

**Files:**
- Modify: 前述文件，仅针对验证发现的问题。
- Move: `frontend-staging/agent-starter-react` -> `../LiveRAG-Fronted/agent-starter-react`。

**Interfaces:**
- Produces: 可由现有 `docker-compose.yml` 构建的 standalone Next.js 镜像。

- [ ] 运行 `pnpm install`、`pnpm lint`、`pnpm typecheck`、`pnpm build`，全部退出码为 0。
- [ ] 构建 Dockerfile（Docker 可用时），运行 `docker compose --env-file .env.local config`。
- [ ] 启动前端，以后端离线和在线可用状态分别检查 `/` 与 `/knowledge`，截图审视桌面和手机布局。
- [ ] 扫描 tracked/source 文件中的 `.env`、API Key 和 LiveKit Secret；只允许示例占位符。
- [ ] 更新 README 写明本地与 Compose 命令、密钥边界和待后端运行后验证项。

