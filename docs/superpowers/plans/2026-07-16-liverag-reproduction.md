# LiveRAG 分阶段源码复现实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan milestone-by-milestone. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以学习 LiveRAG 核心原理为优先，在不改变现有模块边界的前提下，先复现“单库文本 RAG”，再接入 Agent、上下文和长期 history，最后补齐管理层与生产化能力。

**Architecture:** 继续保留 `runtime/config → rag → context → agent → api → frontend/deploy` 的总体架构，但改为四个可独立验收的里程碑。M1 只解决文本 RAG，M2 解决智能体与上下文，M3 再做产品管理，M4 复用已有前端并完成部署与生产化。

**Tech Stack:** Python 3.10–3.14、uv、Pydantic Settings、FastAPI、SQLite、LightRAG、LiveKit Agents、火山 STT、OpenAI-compatible LLM、MiniMax/DashScope TTS、Next.js 15、React 19、TypeScript、Docker Compose。

## Global Constraints

- 所有用户数据只写入 `~/.LiveRAG/`，不得写入项目源码目录。
- 每个 `knowledge_base` 使用独立 LightRAG workspace；一次查询或通话只允许一个 `kb_id`。
- 禁止实现多库 fan-out、统一 workspace 后过滤或 `kb_ids` 多选查询。
- `rag_tool_mode` 只实现 `auto` 和 `never`，不得恢复 `always`。
- 不恢复旧 `src/`、旧入口、本地 STT/TTS 服务、`memory.md` 或旧 context bootstrap 体系。
- 原始 session 消息必须保留；`history.jsonl` 是摘要层，不是原始消息的替代品。
- M1/M2 只使用 `.env → Pydantic Settings`；运行时 JSON 配置、网页改模型和密钥掩码回填延后到 M3。
- M1/M2 不实现 RAG TTL cache；先保证 timeout、错误处理、evidence 记录和 `kb_id` 隔离。
- M1/M2 只记录 `session_id`、`kb_id`、`turn_index`、`duration`；TTFT、首音频、EOU 和完整 metrics hooks 延后到 M4。
- API 和 Agent 不得自动启动 RAG Core；`wait_for_ready` 只等待健康状态，开发环境分别启动进程，生产环境由 Compose 管理。
- LiveKit 的 STT、LLM、TTS、VAD、打断和 endpointing 参数属于时延调优项，M2 先保持最小可用配置，M4 再基于指标调整。
- 新增注释、docstring、文档和 `.env.example` 注释使用中文。
- 每个工作包独立测试、独立验收、独立提交；未通过当前里程碑验收前不进入下一里程碑。

---

## 里程碑总览

```text
M1 核心文本 RAG
项目骨架 → RuntimePaths/Settings → SQLite → txt/md → LightRAG → RAG Core HTTP

M2 Agent 与上下文
Session 存档 → 固定 Prompt → RagClient → ContextManager → LiveKit Agent → History

M3 产品管理层
统一管理 API → 动态配置 → Session/Job 管理 → Overview → 多格式解析

M4 前端与生产化
适配已有前端 → Docker Compose → 健康检查 → 指标 → 端到端测试
```

| 里程碑 | 核心学习问题 | 可交付结果 | 建议时间 |
| --- | --- | --- | ---: |
| M1 | 文档怎样被隔离、索引、检索并返回证据？ | 可用 HTTP 操作的单库文本 RAG | 5–10 个工作日 |
| M2 | RAG 怎样成为 Agent 工具，上下文怎样跨会话工作？ | 可通话、可检索、可生成 history 的 Agent | 5–8 个工作日 |
| M3 | 核心能力怎样包装成可管理产品？ | 统一管理 API 与完整文档管理 | 4–7 个工作日 |
| M4 | 怎样复用 UI 并稳定部署和观测？ | 完整 Web 产品与五服务部署 | 4–7 个工作日 |

M1 必须控制在 1–2 周。若时间不足，只减少测试样本文档数量，不把 M3/M4 功能提前塞入 M1。

---

# M1：核心文本 RAG 闭环

## 阶段目标

从空项目复现最小但真实的文本 RAG：创建知识库、上传 txt/md、保存原文、索引、查询并返回结构化证据。该阶段不依赖 LiveKit Agent、管理 API、前端、Docker、动态配置、查询缓存或复杂指标。

## M1 文件范围

```text
pyproject.toml
.env.example
liverag/runtime/paths.py
liverag/config/settings.py
liverag/rag/settings.py
liverag/rag/schemas.py
liverag/rag/metadata_store.py
liverag/rag/knowledge_base.py
liverag/rag/doc_parser.py
liverag/rag/engine.py
liverag/rag/engine_manager.py
liverag/rag/server.py
liverag/rag/service.py
liverag/rag/cli.py
tests/runtime/
tests/config/
tests/rag/
```

## M1 工作包 A：项目骨架、路径和静态配置

**Produces:**
- `build_runtime_paths(user_data_dir: Path | None = None) -> RuntimePaths`
- `Settings`：从 `.env` 读取用户目录、RAG 端口、LightRAG LLM/Embedding 和查询参数。
- `liverag-rag-service = liverag.rag.cli:main`

- [√] 建立 `pyproject.toml`、`liverag/` 包和 pytest/ruff 基线，固定 Python `>=3.10,<3.15`。
- [√] 在 `tests/conftest.py` 中用 `tmp_path` 和 `monkeypatch` 隔离 `LIVERAG_USER_DATA_DIR`。
- [√] 测试并实现 `RuntimePaths` 的 `db_file`、`rag_knowledge_bases_dir` 和基础日志目录。
- [√] 使用 `pydantic-settings` 实现只读环境配置；缺失必需模型配置时给出明确校验错误。
- [√] 不创建 runtime model JSON，不实现密钥掩码，不实现前端可写配置。
- [√] 运行 `uv run pytest tests/runtime tests/config -v`，预期全部通过。
- [√] 提交：`chore: scaffold minimal text rag runtime`。

**学习重点:** 环境配置与运行数据路径解耦；理解为什么用户数据不能进入源码目录。

## M1 工作包 B：SQLite 元数据与物理隔离

**Produces:**
- `MetadataStore(db_path: Path, knowledge_bases_dir: Path)`
- `KnowledgeBaseMeta.storage_dir/sources_dir/logs_dir`
- knowledge base、document、最小 ingest job 元数据。

- [√] 测试 `initialize()` 创建 `knowledge_bases`、`documents`、`ingest_jobs` 和任务文档关联表。
- [√] 测试首次初始化创建不可删除的 `default` 知识库。
- [√] 测试 `kb_id` 只允许字母、数字、下划线和连字符，阻止 `../` 等路径穿越。
- [√] 测试两个知识库分别拥有独立的 `sources/`、`storage/`、`logs/`。
- [√] 实现文档最小状态机：`pending → parsed → processing → processed/failed`。
- [√] 只实现 M1 HTTP 闭环需要的 CRUD，不实现 session 配置和产品级统计。
- [√] 运行 `uv run pytest tests/rag/test_metadata_store.py tests/rag/test_knowledge_base.py -v`。
- [√] 提交：`feat: add isolated knowledge base metadata`。

**学习重点:** SQLite 只保存产品元数据；原文件、向量、图谱和 chunk 由各自 workspace 保存。

## M1 工作包 C：最小文档解析

**Produces:**
- `parse_file_content(file_bytes: bytes, extension: str) -> str`
- 仅支持 `.txt` 与 `.md`。

- [√] 为 UTF-8 txt、Markdown、空文件、非法编码和不支持扩展名编写测试样例。
- [√] 实现 `.txt/.md` 解码、空文本拒绝和稳定错误类型。
- [√] 保持解析函数无数据库、网络和 LightRAG 副作用。
- [√] 将 PDF、DOCX、PPTX、XLSX 明确留到 M3，不在 M1 引入对应解析依赖和分支。
- [√] 运行 `uv run pytest tests/rag/test_doc_parser.py -v`。
- [√] 提交：`feat: parse txt and markdown documents`。

**学习重点:** 先把“文件解析”和“索引提交”拆开，便于定位失败发生在哪一层。

## M1 工作包 D：LightRAG Engine 与 EngineManager

**Produces:**
- `QueryOptions`、`QueryRequest`、`EvidenceDocument`、`EvidenceChunk`。
- `RagEngine.initialize/enqueue_documents/query_context/query_answer`。
- `RagEngineManager.get(kb_id: str) -> RagEngine`。

- [√] 先用 fake LightRAG 测试输入输出映射，再用真实在线 LLM/Embedding 做一条集成测试。
- [√] 测试 `EngineManager` 以 `kb_id` 为唯一缓存键，并将 working directory 指向该库的 `storage/`。
- [√] 测试两个 KB 返回不同 engine 实例，同一个 KB 重用已初始化实例。
- [√] 查询结果必须返回 `kb_id`、`hit`、`has_context`、`references`、`chunks`、`duration`。
- [√] 无证据时 `hit=false`、`has_context=false`；`query_answer` 返回“知识库中没有足够依据”，禁止让模型自由补全事实。
- [√] 只实现 timeout 和异常映射，不实现 TTL cache、query rewrite、rerank 调优和复杂 voice profile。
- [√] 禁止接受 `kb_ids`，禁止跨 workspace 合并结果。
- [√] 运行 `uv run pytest tests/rag/test_engine.py tests/rag/test_engine_manager.py -v`。
- [√] 提交：`feat: add per-kb lightrag engine`。

**学习重点:** LightRAG 的 working directory 就是隔离边界；evidence 是判断回答可信度的依据。

## M1 工作包 E：RAG Core HTTP API

**Produces:**
- `GET /v1/healthz`、`GET /v1/readyz`
- knowledge base 创建/列表/详情
- txt/md 上传、job 查询
- `POST /v1/knowledge-bases/{kb_id}/query/context`
- `POST /v1/knowledge-bases/{kb_id}/query/answer`
- `wait_for_rag_ready()`：只等待，不启动子进程。

- [√] 接入SQLite 状态编排和返回结果中request_id
- [√] 用 FastAPI TestClient 测试统一成功/错误 envelope。
- [√] 实现上传顺序：生成 document_id → 保存原文件 → 写 pending 元数据 → 解析 → 插入 LightRAG → 更新状态。
- [√] 安全文件名固定保存到 `sources/{document_id}/{original_filename}`，拒绝路径穿越。
- [√] 查询 URL 必须包含单个 `kb_id`，返回结构化 evidence 和 `duration`。
- [√] `liverag-rag-service` 可独立启动；`service.py` 也可通过守护线程内嵌启动，并通过端口和线程状态避免重复启动。
- [√] 启动 `uv run liverag-rag-service`，用 HTTP 完成建库、上传、轮询、查询。
- [√] 运行 `uv run ruff check liverag tests`、`uv run python -m compileall liverag` 和 M1 全部 pytest。
- [√] 提交：`feat: expose minimal rag core api`。

## M1 验收标准

- [√] HTTP 完成：创建知识库 → 上传 txt/md → 索引 → 查询 → 返回证据。
- [√] 建立 `kb_alpha`、`kb_beta`，分别写入互斥事实；物理目录和查询结果完全隔离。
- [√] 对不存在的信息，接口返回 `hit=false`、空 evidence 和明确的“依据不足”，不得生成貌似正确的答案。
- [√] RAG Core 独立启动，不依赖 Agent、管理 API、前端或 Docker。
- [√] 配置只来自 `.env`；没有 runtime JSON、TTL cache、自动进程拉起和复杂 metrics。
- [√] M1 在 5–10 个工作日内完成。

## M1 建议日程

| 时间 | 内容 | 当日可验证结果 |
| --- | --- | --- |
| 第 1–2 天 | 工作包 A、B | 能创建隔离的 KB 目录和 SQLite 记录 |
| 第 3 天 | 工作包 C | txt/md 能稳定解析 |
| 第 4–6 天 | 工作包 D | 两个 workspace 可独立插入和查询 |
| 第 7–8 天 | 工作包 E | HTTP 文本 RAG 闭环 |
| 第 9–10 天 | 隔离、无命中、异常回归 | M1 验收报告 |

---

# M2：Agent 与上下文闭环

## 阶段目标

把 M1 的 RAG Core 接入真正的 LiveKit Agent，并复现固定 SessionSystemPrompt、工具调用、evidence 对齐和跨会话 history。M2 不做统一管理 API、Knowledge Overview 自动生成、动态配置、TTL cache 或完整性能指标。

## M2 文件范围

```text
liverag/context/defaults.py
liverag/context/store.py
liverag/context/renderer.py
liverag/context/manager.py
liverag/context/history.py
liverag/agent/tool/rag_client.py
liverag/agent/providers.py
liverag/agent/assistant.py
liverag/main.py
tests/context/
tests/agent/
```

## M2 工作包 A：不可变原始 Session 存档

**Runtime layout:**

```text
~/.LiveRAG/
  sessions/
    {session_id}/
      messages.jsonl 用户和助手的原始消息
      rag_context.jsonl 每次RAG查询以及evidence
      runtime.json 会话状态
      session_system_prompt.md 本次会话实际使用的prompt
  history/
    {kb_id}/
      history.jsonl
      .cursor
```

**Produces:**
- `ContextStore.start_session(session_id, kb_id)`
- `append_message(session_id, role, content, turn_index)`
- `append_rag_context(session_id, record)`
- `end_session(session_id, state)`

- [√] 测试每次通话生成独立 `session_id` 目录，不再复用全局 `session/messages.jsonl`。
- [√] 测试 messages 和 rag_context 使用追加式 JSONL，包含 `session_id`、`kb_id`、`turn_index`、时间和 `duration`。
- [√] 测试挂断只把 `runtime.json` 标记为 ended，不清空或覆盖 messages/rag_context。
- [√] 测试损坏的单行 JSONL 被跳过，其余审计记录仍可读取。
- [√] 实现保留策略元数据：默认原始 session 永久保留；M2 不运行自动清理。
- [√] 提交：`feat: preserve immutable session records`。

**保留策略:** 学习阶段 `cleanup_enabled=false`。M3 可提供按明确 `session_id` 导出/删除能力；压缩失败、存在 RAG 错误或被标记用于审计的 session 永不自动删除。不得用批量递归删除命令清理 sessions。

## M2 工作包 B：固定 SessionSystemPrompt

**Produces:**
- `SessionPromptRenderer.render(session_id, kb_id, kb_name, rag_tool_mode)`

- [√] 从 system template、SOUL、当前 KB 最近 history、静态 overview fallback 和 RAG 工具规则渲染 prompt。
- [√] 将渲染结果写入当前 session 的 `session_system_prompt.md`，便于事后审计。
- [√] 测试 `auto` 暴露工具说明，`never` 不暴露工具。
- [√] 测试通话开始后即使 SOUL/history 改变，本次 session prompt 也保持不变。
- [√] M2 不生成 Knowledge Overview；缺失时使用稳定降级文本，自动生成留到 M3。
- [√] 提交：`feat: render auditable session prompts`。

## M2 工作包 C：RagClient 与 ContextManager

**Produces:**
- `RagClient.query(query, kb_id, session_id, turn_index) -> RagQueryResult`
- `ContextManager.search_knowledge_base(query, turn_index)`

- [√] 用 fake HTTP 服务测试超时、4xx、5xx、无命中和正常 evidence。
- [√] RagClient 只访问显式 `kb_id` 的 M1 查询路径，不维护 TTL cache。
- [√] 将 request_id、query、hit、has_context、evidence documents/metrics 写入当前 session 的 `rag_context.jsonl`。
- [√] 测试 user message、一次或多次 RAG 调用、assistant message 可通过相同 `turn_index` 聚合。
- [√] 无命中或超时时返回稳定工具结果，明确告诉 LLM 不得伪造知识库依据。
- [√] `never` 模式不注册知识库工具，也不产生伪造的查询记录。
- [√] 提交：`feat: connect agent context to rag evidence`。

## M2 工作包 D：最小 LiveKit Agent

**Produces:**
- `build_agent_session(settings) -> AgentSession`
- LiveKit worker entry：`uv run python -m liverag.main dev`

- [√] 先用 fake STT/LLM/TTS 测试消息、工具和挂断顺序，再接真实在线 provider。
- [√] 启动时解析单个 kb_id，调用 wait_for_rag_ready()；RAG 未启动时按源项目逻辑启动内置服务，最终未就绪则报错退出。
- [√] 创建 session 存档、渲染固定 prompt、连接房间并启动 AgentSession。
- [√] user committed 时追加原始 user message；assistant committed 时追加原始 assistant message。
- [√] 保持当前 STT/LLM/TTS/VAD/打断/endpointing 的最小可用参数，不在 M2 做性能优化。
- [√] 开发环境分别运行 LiveKit Server、RAG Core、Agent 三个进程。
- [√] 提交：`feat: add minimal livekit rag agent`。

## M2 工作包 E：HistoryCompactor

**Produces:**
- `HistoryCompactor.compact(session_id, kb_id) -> HistoryCompactionResult`

- [√] history 记录增加 `source_session_id`，可追溯到原始 session。
- [√] 挂断后读取该 session 的原始 messages、SOUL、当前 KB 最近 history 和 overview fallback。
- [√] Context Model 输出长期摘要时，追加到 `history/{kb_id}/history.jsonl`，记录 cursor 与 `source_session_id`。
- [√] 模型输出 `NO_HISTORY` 时不追加摘要，但仍保留原始 session。
- [√] 压缩失败时把错误写入该 session 的 `runtime.json`，不得删除原始 session，也不得阻断下一通会话。
- [√] 下一次同 KB 会话的 SessionPromptRenderer 读取最近 history；不同 KB 不可读取该摘要。
- [√] 提交：`feat: compact sessions without deleting originals`。

## M2 验收标准

- [√] Agent 能根据问题在 `auto` 模式调用当前 KB 的 RAG。
- [√] 每条 evidence 与当前 `session_id + turn_index` 对齐。
- [√] 挂断后生成长期 history，但原始 messages/rag_context/runtime 完整保留。
- [√] 下一次同知识库会话能读取 history。
- [√] 不同知识库的 history 和 session prompt 互不污染。
- [√] RAG Core、LiveKit Server、Agent 分别启动；Agent 只等待健康状态。

---

# M3：产品管理层

## 阶段目标

在 M1/M2 稳定后补齐产品管理能力。CRUD、动态配置、Knowledge Overview、job 展示和多格式解析不能反向改变 M1/M2 的核心接口。

## M3 文件范围

```text
liverag/api/rag_gateway.py
liverag/api/server.py
liverag/api/main.py
liverag/config/settings.py
liverag/context/overview.py
liverag/rag/doc_parser.py
liverag/rag/server.py
docs/API.md
tests/api/
tests/context/test_overview.py
tests/rag/test_doc_parser.py
```

## M3 工作包 A：统一管理 API（9821）

- [√] 实现 health/runtime、knowledge base、documents、jobs、query 代理接口。
- [√] 前端只访问 `9821`，内部 RAG Core `/v1/*` 保持内部依赖。
- [√] `RagGateway` 调用 `wait_for_rag_ready()` 只等待健康状态，不自动启动 RAG 服务。
- [√] 实现 SOUL、session 列表/详情/turn 聚合、当前 KB 选择和显式 session 导出。
- [√] session 删除只接受一个明确 `session_id`，逐个删除已知文件后移除空目录；不提供批量清空接口。
- [√] 提交：`feat: expose unified management api`。

## M3 工作包 B：动态模型与 Prompt 配置

- [√] 在现有 `.env` 默认值之上增加 runtime model/context config JSON 覆盖层。
- [√] 实现模型、STT、TTS、Context Model、SOUL 和 RAG `auto/never` 配置接口。
- [√] 实现 API Key 掩码输出与掩码值回填保护。
- [√] 明确语音 provider 变更“下一次通话生效”，不得在进行中的 session 热切换。
- [√] 配置校验失败返回稳定字段级错误，不写入部分配置。
- [ ] 提交：`feat: add runtime model and prompt management`。

## M3 工作包 C：Knowledge Overview 与 Job 管理

- [√] 索引 job 完成且有新 processed 文档时，后台调用独立 Context Model 生成 overview。
- [√] 上传或删除文档后把 overview 标记 stale；生成失败写降级 overview，不影响文档和通话。
- [√] 管理 API 提供 job 查询、文档状态、错误原因和 overview 状态。
- [√] job 管理只观察/同步任务，不承担 RAG Core 进程生命周期。
- [ ] 提交：`feat: add knowledge overview and job management`。

## M3 工作包 D：多格式文档解析

- [ ] 在 M1 `parse_file_content` 接口上依次加入 PDF、DOCX、PPTX、XLSX。
- [ ] 每种格式使用最小 fixture 验证关键文字、空文件、损坏文件和加密文件错误。
- [ ] PDF 优先使用稳定解析路径；可选 Docling 使用延迟导入，缺失时回退到 pypdf。
- [ ] 解析失败保留原文件与错误元数据，不提交 LightRAG。
- [ ] 提交：`feat: add office and pdf document parsers`。

## M3 验收标准

- [ ] 所有产品管理操作都能经 `9821` 完成。
- [ ] M1 文本 RAG 与 M2 Agent 在管理 API 未启动时仍可独立运行。
- [ ] 动态配置只影响后续 session，密钥不会在 API 响应中泄露。
- [ ] 多格式文档可上传、解析、索引、查看状态和查询。
- [ ] 原始 session 可审计、导出，并按明确 session ID 管理。

---

# M4：已有前端适配、Docker 和生产化

## 阶段目标

复用 `E:/CS/project/LiveRAG-Fronted/agent-starter-react`，只做 API 契约适配、缺陷修复和必要交互补齐；随后完成五服务部署、健康检查、指标与端到端验收。

## M4 文件范围

```text
E:/CS/project/LiveRAG-Fronted/agent-starter-react/types/liverag.ts
E:/CS/project/LiveRAG-Fronted/agent-starter-react/lib/api/
E:/CS/project/LiveRAG-Fronted/agent-starter-react/app/api/
E:/CS/project/LiveRAG-Fronted/agent-starter-react/components/knowledge/
E:/CS/project/LiveRAG-Fronted/agent-starter-react/components/voice/
Dockerfile
docker-compose.yml
liverag/agent/metrics_hooks.py
README.md
tests/e2e/
```

## M4 工作包 A：适配已有 Next.js 前端

- [ ] 先对照 `docs/API.md` 修正现有 TypeScript 类型和 API client，不重写页面架构。
- [ ] 复用已有 `/` 语音页、`/knowledge` 管理页、知识库组件和 voice components。
- [ ] 保留同源 `/api/liverag/*` 代理，浏览器不得直接访问 RAG Core。
- [ ] 保留服务端 LiveKit token 签发，API secret 不进入浏览器。
- [ ] 修复知识库选择锁定、job 轮询、上传失败、RAG evidence 展示等现有交互问题。
- [ ] 展示 `not_queried/hit/miss/failed`、命中文档、片段和错误；cache hit 只有 M4 真正引入缓存后才展示。
- [ ] 运行 `corepack pnpm lint`、`corepack pnpm typecheck`、`corepack pnpm build`。
- [ ] 提交：`fix: adapt existing frontend to staged backend`。

## M4 工作包 B：Docker Compose 五服务部署

- [ ] 复用共享后端镜像，部署 livekit、liverag-rag、liverag-api、liverag-agent、liverag-frontend。
- [ ] RAG Core、API 和 Agent 共享 `/data`，但各自是独立进程。
- [ ] Compose 用 `depends_on` 与 healthcheck 管理启动顺序；应用代码不启动子进程。
- [ ] RAG Core 暴露 ready healthcheck，API/Agent 仅等待 ready。
- [ ] 验证 Compose 重启后 SQLite、workspaces、sessions 和 history 均保留。
- [ ] 提交：`ops: add five-service compose deployment`。

## M4 工作包 C：指标、缓存和端到端验收

- [ ] 在语音闭环稳定后增加 TTFT、首音频延迟、EOU、STT/LLM/TTS/RAG duration hooks。
- [ ] 只有在观测到重复查询收益且具备知识库版本失效键后，才实现可选 TTL cache；缓存键至少包含 `kb_id + knowledge_version + query + query_options`。
- [ ] 知识库新增、删除或重建索引时使对应版本缓存失效；默认可继续关闭缓存。
- [ ] 做两库隔离测试、跨 session history 测试、无命中防幻觉测试和原始 session 审计测试。
- [ ] 用 3 个库内问题、2 个库外问题、2 个闲聊问题对齐 messages、rag_context、history 和 UI。
- [ ] 记录至少 20 轮无 RAG/有 RAG 的 P50/P95 首音频延迟，再决定是否调整 endpointing 和查询参数。
- [ ] 执行后端 pytest/ruff/compileall、前端 lint/typecheck/build、`docker compose config` 和完整手工通话验收。
- [ ] 提交：`test: complete production readiness validation`。

## M4 验收标准

- [ ] 已有前端完成适配，没有重新开发一套重复 UI。
- [ ] 五服务能通过 Compose 启动、健康检查和重启恢复。
- [ ] 用户能完成建库、上传、等待索引、选择单 KB、语音问答和 evidence 查看。
- [ ] 原始 session、长期 history、SQLite 和 LightRAG workspace 均持久化。
- [ ] 指标能够定位 STT、LLM、RAG、TTS 的主要延迟，不为追求数字提前改变语音参数。

---

## 阶段依赖与停止条件

| 当前阶段 | 进入下一阶段前必须满足 | 不满足时不要做什么 |
| --- | --- | --- |
| M1 | 两库隔离、txt/md HTTP 闭环、无命中不作答 | 不接 LiveKit，不写管理 UI |
| M2 | evidence 对齐、原始 session 保留、history 跨会话且按 KB 隔离 | 不做动态配置和多格式解析 |
| M3 | `9821` 契约稳定、job/overview/配置可管理 | 不大改已有前端 |
| M4 | 前三阶段回归全部通过 | 不用性能优化掩盖功能错误 |

## 学习优先级

1. 先理解 `kb_id → workspace` 的物理隔离，这是整个项目最重要的数据边界。
2. 再理解 `query → evidence → answer`，无 evidence 时必须显式拒答。
3. 接着理解 `session_id + turn_index` 如何关联原始消息、工具调用和回答。
4. 最后理解 history 是可追溯的摘要层，不是删除原始会话的理由。
5. 管理 API、UI、Docker、缓存和指标都是对核心链路的包装与运维能力，不应反向主导前两阶段设计。

## 最终复现完成定义

- M1 能独立演示文本 RAG。
- M2 能独立演示实时语音 RAG 与跨会话 history。
- M3 能通过统一 API 管理模型、Prompt、session、job、overview 和多格式文档。
- M4 复用已有前端并完成五服务部署、观测与端到端验收。
- 任一阶段的失败都能通过 SQLite、原始 session、RAG evidence 和基础日志定位，不依赖猜测。
