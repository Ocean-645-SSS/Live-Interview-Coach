# LiveRAG 从零复现实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从空目录按依赖顺序复现一个可管理单知识库、可进行实时语音 RAG 通话、可记录证据和长期 history 的 LiveRAG。

**Architecture:** 后端拆为运行时基础设施、单知识库隔离 RAG Core、上下文系统、统一管理 API 和 LiveKit 语音 Agent；前端作为相邻 Next.js 项目，只访问统一管理 API 与 LiveKit。先完成无外部模型也能测试的本地模块，再接在线模型和实时语音，避免同时调试存储、检索、音频和 UI。

**Tech Stack:** Python 3.10–3.14、uv、FastAPI、SQLite、LightRAG、LiveKit Agents、火山 STT、OpenAI-compatible LLM、MiniMax/DashScope TTS、Next.js 15、React 19、TypeScript、Docker Compose。

## Global Constraints

- 所有用户数据只写入 `~/.LiveRAG/`，不得写入项目源码目录。
- 每个 `knowledge_base` 使用独立 LightRAG workspace；一次通话只锁定一个 `kb_id`。
- 前端只访问 `liverag/api/` 的 `9821` 端口，不直接访问 RAG Core 的 `/v1/*`。
- `rag_tool_mode` 只实现 `auto` 和 `never`，不得恢复 `always`。
- 不恢复旧 `src/`、旧入口、本地 STT/TTS 服务或 `memory.md` 体系。
- LiveKit 的 STT、LLM、TTS、VAD、打断和 endpointing 参数只能在功能闭环后做有数据依据的时延调优。
- 新增注释、docstring、文档和环境变量示例注释使用中文。
- 每个任务都先写测试或契约样例，再做最小实现；每个任务独立提交。

---

## 复现顺序总览

```text
项目骨架
  → runtime/config/logging
  → SQLite 元数据与单库目录
  → 文档解析
  → LightRAG Engine 与 EngineManager
  → RAG Core HTTP 服务
  → ContextStore 与固定 Prompt
  → Context Model（overview/history）
  → 统一管理 API
  → Agent 的 RagClient
  → 实时语音 Agent
  → Next.js 前端
  → Docker、端到端与时延验收
```

推荐按四个可运行里程碑推进：

1. M1 本地数据层：任务 1–3，无在线密钥也能验收。
2. M2 文本 RAG：任务 4–6，可通过 HTTP 上传和查询。
3. M3 后端产品闭环：任务 7–9，可通过唯一管理 API 完成知识库与上下文操作。
4. M4 完整语音产品：任务 10–13，可通过网页完成实时语音问答。

---

### Task 1: 建立项目骨架与可重复验证基线

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `liverag/__init__.py`
- Create: `tests/conftest.py`
- Create: `pyrightconfig.json`

**Interfaces:**
- Produces: `liverag-agent`、`liverag-api`、`liverag-rag-service` 三个命令入口的包结构。
- Produces: 测试统一使用临时 `LIVERAG_USER_DATA_DIR`，不污染真实用户目录。

- [ ] 固定 Python 范围为 `>=3.10,<3.15`，按现有 `pyproject.toml` 加入运行和开发依赖。
- [ ] 在 `tests/conftest.py` 提供 `tmp_path` 驱动的用户数据目录 fixture，并用 `monkeypatch.setenv` 覆盖 `LIVERAG_USER_DATA_DIR`。
- [ ] 执行 `uv sync`，预期成功生成虚拟环境并安装锁定依赖。
- [ ] 执行 `uv run python -c "import liverag"`，预期退出码为 0。
- [ ] 执行 `uv run ruff check liverag tests` 与 `uv run python -m compileall liverag`，预期均通过。
- [ ] 提交：`chore: scaffold liverag project`。

**验收门槛:** 新环境只需 `uv sync` 即可导入包；测试不会创建项目目录内的用户数据。

### Task 2: 复现运行时路径、默认文件、配置和事件日志

**Files:**
- Create: `liverag/runtime/paths.py`
- Create: `liverag/context/defaults.py`
- Create: `liverag/config/settings.py`
- Create: `liverag/logging/setup.py`
- Create: `liverag/logging/events.py`
- Test: `tests/runtime/test_paths.py`
- Test: `tests/config/test_settings.py`
- Test: `tests/logging/test_events.py`

**Interfaces:**
- Produces: `build_runtime_paths(user_data_dir: Path | None) -> RuntimePaths`。
- Produces: `load_app_settings() -> AppSettings`、`load_voice_settings()`、`load_rag_client_settings()`、`load_context_model_settings()`。
- Produces: JSONL 事件日志和秘密字段掩码逻辑。

- [ ] 测试 `RuntimePaths` 能派生 `liverag.db`、prompts、history、context、session、model、rag 和 logs 的全部路径。
- [ ] 实现 `ensure_runtime_dirs()`，只创建 `~/.LiveRAG/` 下的运行目录。
- [ ] 测试默认 prompt、`SOUL.md`、overview/history prompt 首次读取时创建，已有文件不会被覆盖。
- [ ] 测试环境变量与运行时 JSON 配置的优先级，并覆盖无效整数、布尔值和 URL。
- [ ] 测试 `RagToolMode = Literal["auto", "never"]`，任何其他值回退或报错，不得接受 `always`。
- [ ] 测试 API 输出会掩码密钥，前端回传掩码值时不会覆盖真实密钥。
- [ ] 实现统一日志初始化和 `EventLogger`，每行包含时间、事件名、session/agent 关联字段。
- [ ] 执行对应 pytest、ruff、compileall；提交：`feat: add runtime configuration and logging`。

**验收门槛:** 使用临时目录启动配置层后，目录和默认文件完整；日志可解析；密钥不出现在公开配置中。

### Task 3: 复现 SQLite 产品元数据与知识库物理隔离

**Files:**
- Create: `liverag/rag/metadata_store.py`
- Create: `liverag/rag/knowledge_base.py`
- Test: `tests/rag/test_metadata_store.py`
- Test: `tests/rag/test_knowledge_base.py`

**Interfaces:**
- Produces: `MetadataStore(db_path, knowledge_bases_dir)`。
- Produces: `KnowledgeBaseMeta.storage_dir/sources_dir/logs_dir`。
- Produces: knowledge base、document、ingest job、session config CRUD。

- [ ] 测试首次 `initialize()` 创建四张业务表、索引和不可删除的 `default` 知识库。
- [ ] 测试 `kb_id` 只允许字母、数字、下划线和连字符，阻止路径穿越。
- [ ] 测试每个知识库创建独立的 `storage/`、`sources/`、`logs/`。
- [ ] 测试文档状态从 `pending → parsed → processing → processed/failed` 的转换与错误信息保留。
- [ ] 测试 ingest job 与其文档关联、计数和完成状态。
- [ ] 测试 session 只保存一个待选 `kb_id`，并能读取当前配置。
- [ ] 测试删除非默认知识库时只影响目标库；测试默认库删除被拒绝。
- [ ] 执行对应 pytest；提交：`feat: add isolated knowledge base metadata store`。

**验收门槛:** 创建两个知识库后目录互不重叠，SQLite 查询不会串库，默认知识库始终存在。

### Task 4: 复现文档解析层

**Files:**
- Create: `liverag/rag/doc_parser.py`
- Test: `tests/rag/test_doc_parser.py`
- Test fixtures: `tests/fixtures/documents/`

**Interfaces:**
- Produces: `parse_file_content(file_bytes: bytes, extension: str, **kwargs) -> str`。
- Supports: txt/md/json/csv、PDF、DOCX、PPTX、XLSX；PDF 可选 Docling 路径。

- [ ] 为每种格式制作最小 fixture，测试解析后的关键文字而非易变的完整排版。
- [ ] 测试扩展名标准化、空文件、损坏文件、加密 PDF 和不支持格式的稳定错误。
- [ ] 先实现纯文本格式，再依次实现 PDF、DOCX、PPTX、XLSX，保持解析函数无数据库副作用。
- [ ] 对 PDF 的 Docling 可选依赖使用协议/延迟导入，未安装时回退到 pypdf。
- [ ] 执行 `uv run pytest tests/rag/test_doc_parser.py -v`；提交：`feat: add document parsers`。

**验收门槛:** 所有支持格式均能得到非空文本；解析失败不会残留半成品索引状态。

### Task 5: 复现单知识库 LightRAG Engine

**Files:**
- Create: `liverag/rag/schemas.py`
- Create: `liverag/rag/settings.py`
- Create: `liverag/rag/engine.py`
- Create: `liverag/rag/engine_manager.py`
- Test: `tests/rag/test_schemas.py`
- Test: `tests/rag/test_engine.py`
- Test: `tests/rag/test_engine_manager.py`

**Interfaces:**
- Produces: `QueryRequest`、`QueryOptions`、`ConversationOptions` 和统一 envelope。
- Produces: `RagEngine` 的 initialize、insert、query_context、query_data、query_answer、document status/delete/clear/overview 能力。
- Produces: `RagEngineManager.get(kb_id)`，缓存严格以 `kb_id` 为键。

- [ ] 测试 voice profile 固定低延迟默认值：`naive`、top_k 4、chunk_top_k 4、1800 字符、关闭 rerank。
- [ ] 用 fake LightRAG 测试 Engine 输入输出映射、references/chunks 保留、无命中与异常路径。
- [ ] 测试短追问只在提供 conversation history 时重写，并返回 `effective_query`、`rewritten`。
- [ ] 测试 manager 为两个 `kb_id` 创建不同 working directory 和不同 engine 实例。
- [ ] 测试并发首次访问同一 `kb_id` 只初始化一次；预热后复用缓存。
- [ ] 禁止实现多库查询、fan-out、后过滤或 `kb_ids` 参数。
- [ ] 使用测试密钥或 fake provider 跑完单元测试；提交：`feat: add per-kb lightrag engine`。

**验收门槛:** 同一查询在两个测试知识库中只能返回各自 workspace 的内容。

### Task 6: 复现内部 RAG Core HTTP 服务

**Files:**
- Create: `liverag/rag/server.py`
- Create: `liverag/rag/service.py`
- Create: `liverag/rag/cli.py`
- Test: `tests/rag/test_server.py`
- Test: `tests/rag/test_service.py`

**Interfaces:**
- Produces: 内部 `/v1/healthz`、`/v1/readyz`、knowledge base、documents、jobs、query、overview 路由。
- Produces: `wait_for_rag_ready()` 和嵌入式服务拉起/复用逻辑。

- [ ] 用 FastAPI TestClient 测试 knowledge base CRUD 和统一成功/错误 envelope。
- [ ] 测试文本/文件上传顺序：保存原文、写 pending 元数据、解析、提交 LightRAG、同步 job 状态。
- [ ] 测试原文件路径固定为 `sources/{document_id}/{safe_filename}`，并拒绝危险文件名。
- [ ] 测试 job 轮询会同步 LightRAG 状态、chunks_count 和失败原因。
- [ ] 测试 query context/data/answer 只接受 URL 中的单个 `kb_id`。
- [ ] 测试删除文档、清空知识库、删除知识库时，元数据与对应 workspace 一致；实现时逐项处理明确路径，禁止批量递归删除命令。
- [ ] 启动 `uv run liverag-rag-service`，验证 `GET http://127.0.0.1:9721/v1/readyz`。
- [ ] 提交：`feat: expose internal rag core service`。

**验收门槛:** 不经过 Agent/API，curl 已能完成“建库→上传→轮询→查询→删除”的文本 RAG 闭环。

### Task 7: 复现会话上下文存储与固定系统提示词

**Files:**
- Create: `liverag/context/store.py`
- Create: `liverag/context/renderer.py`
- Test: `tests/context/test_store.py`
- Test: `tests/context/test_renderer.py`

**Interfaces:**
- Produces: `ContextStore` 对 messages、rag_context、history、SOUL、overview、runtime state 的读写。
- Produces: `SessionPromptRenderer.render(kb_id, kb_name, rag_tool_mode)`。

- [ ] 测试 messages 和 rag_context 使用 JSONL，损坏单行被跳过而不破坏其余记录。
- [ ] 测试 `turn_index` 能把 user、assistant 与多条 RAG 证据聚合成 session turns。
- [ ] 测试每个 KB 的 `history.jsonl` 和 `.cursor` 独立递增。
- [ ] 测试 prompt 精确替换 SOUL、history、overview、RAG 工具说明、KB ID/名称。
- [ ] 测试 `auto` 提供工具说明，`never` 提供禁用说明；渲染结果写入 `session_system_prompt.md`。
- [ ] 测试通话开始后修改 SOUL/history/overview 不会改变已渲染 prompt。
- [ ] 提交：`feat: add fixed session context rendering`。

**验收门槛:** 给定同一组输入可确定性生成固定 SessionSystemPrompt；通话中无需再次读取长期上下文。

### Task 8: 复现 Context Model 的 overview 与挂断后 history

**Files:**
- Create: `liverag/context/overview.py`
- Create: `liverag/context/history.py`
- Test: `tests/context/test_overview.py`
- Test: `tests/context/test_history.py`

**Interfaces:**
- Produces: `KnowledgeOverviewGenerator.generate(kb_id, ...)`。
- Produces: `HistoryCompactor.compact(kb_id, ...)`。

- [ ] 用 fake OpenAI-compatible client 测试 overview 输入包含 KB metadata、文档列表、LightRAG overview 和当前查询参数。
- [ ] 测试索引 job 完成且存在新 processed 文档时才生成 overview；上传后先标记 stale。
- [ ] 测试模型失败时写入降级 overview 和错误 meta，不阻断查询或通话。
- [ ] 测试 history 压缩输入包含本次 messages、SOUL、当前 KB overview、最近 history。
- [ ] 测试正常摘要追加一条带 cursor 的 history；`NO_HISTORY` 不追加；失败不清除已有 history。
- [ ] 测试压缩结束后清空本次 messages，并把结果写入 runtime state。
- [ ] 提交：`feat: add context overview and history compaction`。

**验收门槛:** overview 和 history 都使用独立 Context Model；失败只降级，不影响下一通会话启动。

### Task 9: 复现唯一对外管理 API

**Files:**
- Create: `liverag/api/rag_gateway.py`
- Create: `liverag/api/server.py`
- Create: `liverag/api/main.py`
- Test: `tests/api/test_server.py`
- Test: `tests/api/test_rag_gateway.py`
- Update: `docs/API.md`

**Interfaces:**
- Produces: `9821` 端口上的 health/runtime、model、prompt、session 和 `/rag/*` 路由。
- Consumes: 任务 6 的内部 RAG Core，但对前端隐藏 `/v1/*`。

- [ ] 测试 gateway 自动等待/拉起 RAG Core、透传状态码、统一超时和文件流响应。
- [ ] 测试模型配置公开读取、更新、校验、密钥掩码和“下次通话生效”状态。
- [ ] 测试 SOUL、session messages/rag context/turns、session KB 选择与清理接口。
- [ ] 测试通话 active 时禁止切换 `kb_id`，idle 时仅允许选择存在的单一知识库。
- [ ] 测试 knowledge base、documents、jobs、query 路由均代理到单个目标 KB。
- [ ] 测试 completed job 后后台生成 overview，失败不改变原 job 成功状态。
- [ ] 启动 `uv run liverag-api`，验证 `/health`、`/rag/ready`、`/rag/knowledge-bases`。
- [ ] 更新 `docs/API.md` 并提交：`feat: expose unified management api`。

**验收门槛:** 产品后端的所有管理操作都可经 `9821` 完成；关闭 `9721` 的外部访问不影响前端设计。

### Task 10: 复现 Agent 侧低延迟 RAG Client 与证据记录

**Files:**
- Create: `liverag/agent/tool/rag_client.py`
- Create: `liverag/agent/tool/__init__.py`
- Create: `liverag/context/manager.py`
- Test: `tests/agent/test_rag_client.py`
- Test: `tests/context/test_manager.py`

**Interfaces:**
- Produces: `RagClient.query(query, kb_id, ...) -> RagQueryResult`。
- Produces: 只面向当前锁定 KB 的 `search_knowledge_base` 上下文能力。

- [ ] 用 fake HTTP 服务测试超时、TTL cache、query 参数和单 `kb_id` URL。
- [ ] 测试结果保留 request_id、hit、context、metrics、references、chunks 和 no_evidence_reason。
- [ ] 测试证据归一化为 `evidence_documents`、`evidence_chunks`，并记录 `turn_index`。
- [ ] 测试无命中、超时和 5xx 都写入 `rag_context.jsonl`，但返回可供语音模型继续回答的稳定结果。
- [ ] 测试 `never` 模式不注册/调用 RAG 工具。
- [ ] 提交：`feat: add voice rag client and evidence tracking`。

**验收门槛:** 一次工具调用能与本轮 user/assistant 消息按 `turn_index` 对齐，且不会访问其他知识库。

### Task 11: 复现实时语音 Agent

**Files:**
- Create: `liverag/agent/providers.py`
- Create: `liverag/agent/dashscope_tts.py`
- Create: `liverag/agent/assistant.py`
- Create: `liverag/agent/metrics_hooks.py`
- Create: `liverag/main.py`
- Test: `tests/agent/test_providers.py`
- Test: `tests/agent/test_assistant.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `build_agent_session(settings) -> AgentSession`。
- Produces: LiveKit 注册名固定为 `my-agent`，与前端 token dispatch 一致。

- [ ] 先用 fake STT/LLM/TTS 测试 provider 选择与缺失密钥错误，再进行真实 provider 联调。
- [ ] 测试 Assistant 在 user committed 时写 user message，在 assistant 完成时写 assistant message及字符数。
- [ ] 测试 `auto` 暴露知识库工具，`never` 不暴露工具；默认语音回答遵守 1–3 句短回答策略。
- [ ] 测试 job 启动顺序：解析单个 KB→预热 engine→清空临时会话→渲染固定 prompt→连接房间→启动 session。
- [ ] 测试挂断顺序：停止探针→读取本次消息→history compact→更新 runtime state→清空临时消息。
- [ ] 注册 STT/LLM/TTS/首 token/首音频/RAG 延迟指标；先保持现有 endpointing 参数，不做提前优化。
- [ ] 启动 LiveKit Server 与 `uv run python -m liverag.main dev`，验证 worker 注册成功。
- [ ] 提交：`feat: add livekit voice agent`。

**验收门槛:** 不启用 RAG 时能完成可打断语音对话；启用 RAG 时只查询通话开始前锁定的 KB；挂断后 history 正确落盘。

### Task 12: 复现 Next.js 前端

**Files (相邻仓库 `LiveRAG-Fronted/agent-starter-react`):**
- Create: `types/liverag.ts`
- Create: `lib/api/client.ts`
- Create: `lib/api/server.ts`
- Create: `app/api/liverag/[...path]/route.ts`
- Create: `app/api/connection-details/route.ts`
- Create: `components/knowledge/*`
- Create: `components/voice/*`
- Create: `app/page.tsx`
- Create: `app/knowledge/page.tsx`

**Interfaces:**
- Consumes: 管理 API `9821` 与 LiveKit WebSocket；浏览器不持有 LiveKit API secret。
- Produces: `/` 实时语音页与 `/knowledge` 单知识库管理页。

- [ ] 先定义与 `docs/API.md` 一致的 TypeScript 类型和统一错误模型。
- [ ] 实现同源 `/api/liverag/*` 服务端代理，禁止浏览器直接配置内部 RAG URL。
- [ ] 实现 `/api/connection-details`：服务端签发 15 分钟、单随机房间、显式 dispatch `my-agent` 的 token。
- [ ] 先完成知识库 CRUD、选择、文本/文件上传、job 轮询、文档列表/删除，再开发语音 UI。
- [ ] 实现语音连接、麦克风控制、转录、状态条和通话前 KB 选择；连接后锁定选择器。
- [ ] 展示每轮 `not_queried/hit/miss/failed`、RAG 延迟、cache hit、命中文档和折叠片段。
- [ ] 执行 `corepack pnpm lint`、`corepack pnpm typecheck`、`corepack pnpm build`。
- [ ] 提交：`feat: add liverag web interface`。

**验收门槛:** 用户能从浏览器完成建库、上传、等待索引、选择 KB、发起语音通话并查看回答依据。

### Task 13: 部署、端到端测试与性能验收

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Update: `README.md`
- Create: `tests/e2e/test_reproduction_checklist.md`

**Interfaces:**
- Produces: `livekit`、`liverag-rag`、`liverag-api`、`liverag-agent`、`liverag-frontend` 五服务部署。

- [ ] 构建共享后端镜像，运行时只持久化 `/data`，并确保三个 Python 进程复用同一数据卷。
- [ ] 为 RAG Core 增加 ready healthcheck，让 API/Agent 等待其就绪；前端只依赖 API 与 LiveKit。
- [ ] 做两库隔离测试：两库分别写入互斥事实，连续两通电话切换 KB，确认无交叉命中。
- [ ] 做上传闭环测试：txt、PDF、DOCX、PPTX、XLSX 各一份，记录解析、索引、overview、查询、删除结果。
- [ ] 做 7 问语音测试：3 个库内问题、2 个库外问题、2 个闲聊；对齐 messages、rag_context 与 UI turn。
- [ ] 做 history 测试：第一通产生长期信息，第二通确认按同一 KB 注入；另一个 KB 不得读取该 history。
- [ ] 记录至少 20 轮数据，分别统计无 RAG 和有 RAG 的 P50/P95 首音频延迟；目标参考值约 900ms/1500ms，不为达标盲改时延参数。
- [ ] 执行后端 ruff、compileall、pytest，前端 lint、typecheck、build，以及 `docker compose config`。
- [ ] 更新 README 的本地与 Docker 启动说明；提交：`docs: finalize reproduction and acceptance guide`。

**验收门槛:** 全新机器按 README 配置密钥后可启动全部服务；单库隔离、证据可观测、固定 prompt、挂断压缩和实时语音全部通过。

---

## 推荐时间安排

| 阶段 | 范围 | 建议投入 | 阶段产物 |
| --- | --- | ---: | --- |
| 第 1 阶段 | 任务 1–3 | 1–2 天 | 可测试的数据和配置底座 |
| 第 2 阶段 | 任务 4–6 | 2–4 天 | 可用 curl 操作的单库 RAG Core |
| 第 3 阶段 | 任务 7–9 | 2–3 天 | 完整管理 API 与上下文闭环 |
| 第 4 阶段 | 任务 10–11 | 2–4 天 | 可工作的实时语音 RAG Agent |
| 第 5 阶段 | 任务 12 | 2–4 天 | 知识库与语音 WebUI |
| 第 6 阶段 | 任务 13 | 1–2 天 | 部署、回归与性能报告 |

单人熟悉 Python/TypeScript/LiveKit 时，完整复现约 10–19 个工作日；第一次接触 LiveKit 或 LightRAG 时应为外部服务联调额外预留 3–5 天。

## 最容易踩坑的顺序错误

1. 不要先写语音 Agent：否则检索、上下文、音频、网络四类问题会混在一起。
2. 不要先写前端页面：API envelope、job 状态和 session 锁库规则尚未稳定时返工最多。
3. 不要把 Context Model 与 Voice LLM 合并：两者延迟目标、提示词和故障策略不同。
4. 不要在通话中动态重建大 prompt：固定 prompt 是当前时延与行为稳定性的核心。
5. 不要用一个 LightRAG workspace 加 `kb_id` 后过滤：物理隔离必须在 engine working directory 层完成。
6. 不要在功能未闭环前调 VAD/endpointing：先用指标确定瓶颈属于 STT、LLM、RAG 还是 TTS。

## 最小成功路径

如果目标是最快看到结果，可先只做任务 1–7，并临时省略 PDF/DOCX/PPTX/XLSX、overview 自动生成、前端和语音。此时用纯文本上传与 `/v1/.../query/context` 即可验证核心设计。随后按 8→9→10→11→12 补齐，避免更改模块边界。
