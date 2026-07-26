# M2-D 最小 LiveKit Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已完成的 Session 存档、固定 Prompt 和 RagClient 接入一个可启动、可测试、只绑定单个知识库的最小 LiveKit 语音 Agent。

**Architecture:** `main.py` 只负责编排一次 LiveKit job：解析单个 `kb_id`、等待外部 RAG Core、创建 session 存档、渲染固定 prompt、组装并启动 AgentSession、挂断时结束存档。`providers.py` 隔离 STT/LLM/TTS/VAD 实例化，`assistant.py` 隔离 LiveKit 消息事件和 RAG function tool；ContextManager 继续作为消息与检索的业务边界。

**Tech Stack:** Python 3.10–3.14、LiveKit Agents 1.4、Volcengine STT、OpenAI-compatible LLM、MiniMax/DashScope TTS、Silero VAD、pytest/pytest-asyncio。

## Global Constraints

- 一次 Agent session 只接受一个 `kb_id`，不得实现 `kb_ids` 或多库 fan-out。
- M2 只允许 `.env -> settings`；不增加运行时 JSON 配置或网页配置。
- `rag_tool_mode` 只支持 `auto`、`never`，不得恢复 `always`。
- Agent 和 API 不得启动 RAG Core；`wait_for_rag_ready()` 只轮询外部服务。
- 原始 user/assistant 消息必须按 `session_id + kb_id + turn_index` 追加保存，挂断不得删除。
- 只记录 `session_id/kb_id/turn_index/duration` 等基础字段，不实现完整 metrics hooks。
- 保持最小可用 provider、VAD、打断和 endpointing 参数，不在 M2-D 调优性能。
- 新增注释、docstring 和 `.env.example` 注释使用中文。

---

## 文件结构

### 必须创建

- `tests/agent/test_providers.py`：provider 工厂的参数映射和非法配置测试。
- `tests/agent/test_assistant.py`：消息提交、turn_index、RAG 工具和 never 模式测试。
- `tests/agent/test_main.py`：LiveKit job 生命周期、RAG 未就绪、单 KB 绑定和挂断收尾测试。

### 必须修改

- `liverag/config/settings.py`：新增 M2-D 所需的 `VoiceSettings`、`AgentSettings` 与 `load_agent_settings()`。
- `liverag/agent/providers.py`：实现 `build_agent_session(settings) -> AgentSession`。
- `liverag/agent/assistant.py`：实现最小 `VoiceAssistant(Agent)` 和 `search_knowledge_base` function tool。
- `liverag/main.py`：实现 `AgentServer` job 入口与 `python -m liverag.main dev` CLI。
- `liverag/context/manager.py`：补齐原始 user/assistant 消息落盘接口，并维护 turn 上下文。
- `liverag/rag/service.py`：让 `wait_for_rag_ready()` 只探测，不调用 `start_embedded_rag_service()`。
- `.env.example`：补齐 LiveKit、语音 provider、单 KB、RAG 客户端配置示例。

### 仅联动验证

- `liverag/context/store.py`：复用 `start_session()`、`append_message()`、`end_session()`，不改存储布局。
- `liverag/context/renderer.py`：复用 `SessionPromptRenderer.render()`，不在通话中重渲染 prompt。
- `liverag/agent/tool/rag_client.py`：复用单 KB 查询和 `rag_context.jsonl` 审计，不改变 HTTP 契约。
- `liverag/agent/dashscope_tts.py`：仅由 provider 工厂实例化，不改 WebSocket 协议。

---

### Task 1: 固化 Agent 与语音 Provider 配置

**Files:**
- Modify: `liverag/config/settings.py`
- Modify: `.env.example`
- Test: `tests/config/test_settings.py`

**Interfaces:**
- Consumes: 现有 `RagClientSettings`、`RagToolMode`。
- Produces: `VoiceSettings`、`AgentSettings`、`load_agent_settings() -> AgentSettings`。

- [ ] **Step 1: 写失败测试**

```python
def test_load_agent_settings_reads_single_kb_and_voice_env(monkeypatch):
    monkeypatch.setenv("LIVERAG_KB_ID", "kb-alpha")
    monkeypatch.setenv("LIVERAG_KB_NAME", "Alpha")
    monkeypatch.setenv("LIVEKIT_URL", "ws://127.0.0.1:7880")
    monkeypatch.setenv("VOICE_LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("VOICE_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("VOICE_LLM_API_KEY", "secret")
    settings = load_agent_settings()
    assert settings.kb_id == "kb-alpha"
    assert settings.voice.llm_model == "qwen-plus"
    assert settings.rag.rag_tool_mode in {"auto", "never"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/config/test_settings.py -v`

Expected: FAIL，提示 `load_agent_settings` 不存在。

- [ ] **Step 3: 实现最小配置对象**

```python
@dataclass(frozen=True)
class VoiceSettings:
    livekit_url: str = field(default_factory=lambda: _str_env("LIVEKIT_URL"))
    stt_app_id: str = field(default_factory=lambda: _str_env("VOLCENGINE_APP_ID"))
    stt_access_token: str = field(default_factory=lambda: _str_env("VOLCENGINE_ACCESS_TOKEN"))
    stt_model: str = field(default_factory=lambda: _str_env("VOLCENGINE_STT_MODEL", "bigmodel"))
    llm_model: str = field(default_factory=lambda: _str_env("VOICE_LLM_MODEL"))
    llm_base_url: str = field(default_factory=lambda: _str_env("VOICE_LLM_BASE_URL").rstrip("/"))
    llm_api_key: str = field(default_factory=lambda: _str_env("VOICE_LLM_API_KEY"))
    tts_provider: Literal["minimax", "dashscope"] = "minimax"
    tts_model: str = ""
    tts_voice: str = ""
    tts_api_key: str = ""

@dataclass(frozen=True)
class AgentSettings:
    user_data_dir: Path
    kb_id: str
    kb_name: str
    history_limit: int
    rag_ready_timeout_ms: int
    voice: VoiceSettings
    rag: RagClientSettings

def load_agent_settings() -> AgentSettings:
    return AgentSettings(
        user_data_dir=Path(_str_env("LIVERAG_USER_DATA_DIR", "~/.LiveRAG")).expanduser(),
        kb_id=_str_env("LIVERAG_KB_ID", "default"),
        kb_name=_str_env("LIVERAG_KB_NAME", "默认知识库"),
        history_limit=_int_env("LIVERAG_HISTORY_LIMIT", 8),
        rag_ready_timeout_ms=_int_env("LIVERAG_RAG_READY_TIMEOUT_MS", 15000),
        voice=VoiceSettings(),
        rag=RagClientSettings(),
    )
```

实现时对空 `kb_id`、非正 `history_limit`、非正 timeout、未知 TTS provider 和缺少所选 provider 密钥抛出 `ValueError`；不要把密钥写入日志或 runtime。

- [ ] **Step 4: 补齐 `.env.example` 并验证**

Run: `uv run pytest tests/config/test_settings.py -v`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add liverag/config/settings.py tests/config/test_settings.py .env.example
git commit -m "feat: add minimal agent settings"
```

### Task 2: 构建可替换测试的 AgentSession Provider 工厂

**Files:**
- Modify: `liverag/agent/providers.py`
- Create: `tests/agent/test_providers.py`

**Interfaces:**
- Consumes: `VoiceSettings`。
- Produces: `build_agent_session(settings: VoiceSettings) -> AgentSession`。

- [ ] **Step 1: 写失败测试**

```python
def test_build_agent_session_wires_all_providers(monkeypatch, voice_settings):
    calls = install_fake_livekit_providers(monkeypatch)
    session = build_agent_session(voice_settings)
    assert session is calls.agent_session
    assert calls.stt_kwargs["model"] == voice_settings.stt_model
    assert calls.llm_kwargs["model"] == voice_settings.llm_model
    assert calls.vad_loaded is True
```

另加两个独立测试：`tts_provider="minimax"` 选择 MiniMax；`tts_provider="dashscope"` 选择 `DashScopeRealtimeTTS`。所有 fake 必须纯内存运行，测试不得联网。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/agent/test_providers.py -v`

Expected: FAIL，提示 `build_agent_session` 不存在。

- [ ] **Step 3: 实现 provider 工厂**

```python
def build_agent_session(settings: VoiceSettings) -> AgentSession:
    stt = volcengine.STT(
        app_id=settings.stt_app_id,
        access_token=settings.stt_access_token,
        model=settings.stt_model,
    )
    llm_provider = openai.LLM(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
    )
    tts_provider = _build_tts(settings)
    return AgentSession(
        stt=stt,
        llm=llm_provider,
        tts=tts_provider,
        vad=silero.VAD.load(),
    )
```

`_build_tts()` 只允许两个显式分支；未知 provider 必须抛错。构造参数以当前锁文件中的 LiveKit 1.4 API 为准，不加入 M4 才需要的 metrics、prewarm 或动态切换。

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/agent/test_providers.py -v`

Expected: PASS，且没有网络请求。

- [ ] **Step 5: 提交**

```bash
git add liverag/agent/providers.py tests/agent/test_providers.py
git commit -m "feat: build minimal livekit providers"
```

### Task 3: 实现消息落盘和最小 VoiceAssistant

**Files:**
- Modify: `liverag/context/manager.py`
- Modify: `liverag/agent/assistant.py`
- Modify: `tests/context/test_manager.py`
- Create: `tests/agent/test_assistant.py`

**Interfaces:**
- Consumes: `ContextStore.append_message()`、`ContextManager.search_knowledge_base()`。
- Produces: `ContextManager.record_user_message()`、`record_assistant_message()`、`VoiceAssistant`。

- [ ] **Step 1: 写 ContextManager 失败测试**

```python
def test_record_messages_preserves_turn_alignment(manager, store):
    manager.record_user_message(content="M2-D 是什么？", turn_index=1)
    manager.record_assistant_message(content="最小 LiveKit Agent。", turn_index=1)
    messages = store.read_message(session_id="session-1")
    assert [(item["role"], item["turn_index"]) for item in messages] == [
        ("user", 1),
        ("assistant", 1),
    ]
```

- [ ] **Step 2: 实现消息接口并通过现有上下文测试**

```python
def record_user_message(self, *, content: str, turn_index: int) -> None:
    text = content.strip()
    if not text:
        return
    self._previous_user_text = self._last_user_text
    self._last_user_text = text
    self.rag_client.store.append_message(
        session_id=self.session_id, role="user", content=text, turn_index=turn_index
    )

def record_assistant_message(self, *, content: str, turn_index: int) -> None:
    self.rag_client.store.append_message(
        session_id=self.session_id,
        role="assistant",
        content=content,
        turn_index=turn_index,
    )
```

Run: `uv run pytest tests/context/test_manager.py -v`

Expected: PASS。

- [ ] **Step 3: 写 VoiceAssistant 失败测试**

测试以下行为：

```python
async def test_search_tool_delegates_current_turn(manager):
    assistant = VoiceAssistant(
        context_manager=manager,
        instructions="固定提示词",
        rag_tool_mode="auto",
    )
    assistant._turn_index = 3
    result = await assistant.search_knowledge_base("M2-D 是什么？")
    manager.search_knowledge_base.assert_awaited_once_with(
        query="M2-D 是什么？",
        turn_index=3,
    )
    assert '"status": "hit"' in result

def test_never_mode_removes_knowledge_tool(manager):
    assistant = VoiceAssistant(
        context_manager=manager,
        instructions="固定提示词",
        rag_tool_mode="never",
    )
    tools = assistant.select_tools([FakeTool("search_knowledge_base"), FakeTool("other")])
    assert [tool.name for tool in tools] == ["other"]
```

消息提交测试用两个相同的 fake committed event 调用公开适配方法，断言 store 仅新增一条记录；工具异常测试令 manager 抛出 `RuntimeError("timeout")`，断言返回文本明确包含“查询失败”和“不要编造”。

- [ ] **Step 4: 实现最小 Agent**

```python
class VoiceAssistant(Agent):
    def __init__(self, *, context_manager: ContextManager, instructions: str, rag_tool_mode: RagToolMode):
        self.context_manager = context_manager
        self.rag_tool_mode = rag_tool_mode
        self._turn_index = 0
        super().__init__(instructions=instructions)

    @llm.function_tool(name="search_knowledge_base")
    async def search_knowledge_base(self, query: str) -> str:
        payload = await self.context_manager.search_knowledge_base(
            query=query, turn_index=self._turn_index
        )
        return json.dumps(payload, ensure_ascii=False)
```

消息提交适配放在一个可单测的方法中，并确保同一 committed 事件不会重复写入。`never` 模式必须在交给模型前过滤 `search_knowledge_base`，不能生成伪查询记录；miss/failed 工具结果必须保留 `instruction`，要求模型如实说明依据不足或查询失败。

- [ ] **Step 5: 运行测试并提交**

Run: `uv run pytest tests/context/test_manager.py tests/agent/test_assistant.py -v`

Expected: PASS。

```bash
git add liverag/context/manager.py liverag/agent/assistant.py tests/context/test_manager.py tests/agent/test_assistant.py
git commit -m "feat: connect agent turns to rag context"
```

### Task 4: 禁止 Agent 隐式启动 RAG Core

**Files:**
- Modify: `liverag/rag/service.py`
- Modify: `tests/rag/test_service.py`

**Interfaces:**
- Consumes: RAG Core `/v1/readyz`。
- Produces: `wait_for_rag_ready(*, base_url: str, api_key: str, timeout_ms: int, interval_ms: int = 250) -> RagReadyState`，只等待不启动。

- [ ] **Step 1: 写失败测试**

```python
def test_wait_for_ready_never_starts_embedded_service(monkeypatch):
    monkeypatch.setattr(service, "start_embedded_rag_service", lambda: pytest.fail("不得启动"))
    install_ready_response(monkeypatch, ready=True)
    state = service.wait_for_rag_ready(
        base_url="http://rag-core:9819", api_key="secret", timeout_ms=100
    )
    assert state.ready is True
```

另测超时返回 `ready=False`，并保留最后一次连接错误。

- [ ] **Step 2: 运行测试确认当前实现失败**

Run: `uv run pytest tests/rag/test_service.py -v`

Expected: FAIL，因为当前函数会调用 `start_embedded_rag_service()`。

- [ ] **Step 3: 移除隐式启动**

```python
def wait_for_rag_ready(*, base_url: str, api_key: str = "", timeout_ms: int = 15000,
                       interval_ms: int = 250) -> RagReadyState:
    url = f"{base_url.rstrip('/')}/v1/readyz"
    # 只轮询 url；不调用 start_embedded_rag_service()
```

成功状态使用 `status="ready"`，超时使用 `status="timeout"`；保留旧的显式启动函数供 RAG 自己的 CLI 使用，但 Agent 路径不得调用它。

- [ ] **Step 4: 运行测试并提交**

Run: `uv run pytest tests/rag/test_service.py -v`

Expected: PASS。

```bash
git add liverag/rag/service.py tests/rag/test_service.py
git commit -m "fix: make rag readiness probe non-starting"
```

### Task 5: 编排 LiveKit Job 生命周期

**Files:**
- Modify: `liverag/main.py`
- Create: `tests/agent/test_main.py`

**Interfaces:**
- Consumes: `load_agent_settings()`、`build_agent_session()`、`ContextStore`、`SessionPromptRenderer`、`RagClient`、`ContextManager`、`VoiceAssistant`。
- Produces: LiveKit worker entry `uv run python -m liverag.main dev`。

- [ ] **Step 1: 写失败的 job 生命周期测试**

```python
async def test_agent_job_starts_and_ends_one_archived_session(fake_ctx, dependencies):
    await run_agent_job(fake_ctx, settings=dependencies.settings)
    assert dependencies.store.read_runtime_state("job-1")["state"] == "ended"
    assert dependencies.session.started_with.room is fake_ctx.room
```

覆盖：

- RAG 未 ready 时抛出 `RuntimeError`，且不创建 session 目录、不调用 `session.start()`。
- `kb_id` 只来自本次 settings，分别传给 `start_session`、`RagClient`、renderer。
- 顺序为 ready → start_session → render → connect → session.start。
- shutdown callback 只调用 `end_session`，不删除 `messages.jsonl`/`rag_context.jsonl`。

- [ ] **Step 2: 实现可注入依赖的 job 编排函数**

```python
async def run_agent_job(ctx: JobContext, *, settings: AgentSettings | None = None) -> None:
    config = settings or load_agent_settings()
    ready = await asyncio.to_thread(
        wait_for_rag_ready,
        base_url=config.rag.base_url,
        api_key=config.rag.api_key,
        timeout_ms=config.rag_ready_timeout_ms,
    )
    if not ready.ready:
        raise RuntimeError(f"RAG Core 未就绪: {ready.error}")
    session_id = ctx.job.id
    store = ContextStore(build_runtime_paths(config.user_data_dir))
    store.initialize()
    store.start_session(session_id, config.kb_id)
    prompt = SessionPromptRenderer(store=store, history_limit=config.history_limit).render(
        session_id=session_id,
        kb_id=config.kb_id,
        kb_name=config.kb_name,
        rag_tool_mode=config.rag.rag_tool_mode,
    )
```

随后按以下显式依赖链构造对象：

```python
rag_client = RagClient(
    config.rag,
    store,
    user_data_dir=config.user_data_dir,
    kb_id=config.kb_id,
    kb_name=config.kb_name,
)
manager = ContextManager(
    rag_client=rag_client,
    session_id=session_id,
    rag_tool_mode=config.rag.rag_tool_mode,
)
assistant = VoiceAssistant(
    context_manager=manager,
    instructions=prompt.prompt,
    rag_tool_mode=config.rag.rag_tool_mode,
)
session = build_agent_session(config.voice)
```

注册幂等 shutdown callback，调用 `await ctx.connect()` 与 `await session.start(agent=assistant, room=ctx.room)`。失败路径执行 `store.end_session(session_id, state="failed")`，再把精简错误类型写入 runtime，保留全部原始文件后重新抛出。

- [ ] **Step 3: 注册 LiveKit 入口和 CLI**

```python
server = AgentServer()

@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext) -> None:
    await run_agent_job(ctx)

def main() -> None:
    cli.run_app(server)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行 M2-D 测试**

Run: `uv run pytest tests/agent/test_main.py tests/agent/test_assistant.py tests/agent/test_providers.py -v`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add liverag/main.py tests/agent/test_main.py
git commit -m "feat: add minimal livekit rag agent"
```

### Task 6: M2-D 回归与手工验收

**Files:**
- Verify: `liverag/`
- Verify: `tests/`
- Verify: `.env.example`

**Interfaces:**
- Produces: 可独立演示的最小语音 RAG Agent，不包含 M2-E history 压缩。

- [ ] **Step 1: 运行自动化回归**

Run: `uv run pytest tests/config tests/context tests/agent tests/rag/test_service.py -v`

Expected: PASS。

- [ ] **Step 2: 运行静态检查**

Run: `uv run ruff check liverag tests`

Expected: PASS。

Run: `uv run python -m compileall liverag`

Expected: PASS。

- [ ] **Step 3: 分进程手工验收**

分别启动：

```bash
uv run liverag-rag-service
livekit-server --dev
uv run python -m liverag.main dev
```

完成一轮命中知识库问题和一轮普通闲聊。预期：

- 同一通话只出现一个 session 目录和一个 `kb_id`。
- `messages.jsonl` 的 user/assistant 使用相同 `turn_index`。
- 命中问题在 `rag_context.jsonl` 中有 evidence；闲聊在 `auto` 模式下可无 RAG 记录。
- 挂断后 `runtime.json.state == "ended"`，原始文件仍存在。
- 停止 RAG Core 后 Agent 启动失败，且不会自行拉起 RAG 子进程。

- [ ] **Step 4: 最终提交**

```bash
git add .env.example liverag tests
git commit -m "test: verify m2-d livekit agent lifecycle"
```

## 不属于 M2-D

- `HistoryCompactor` 和跨会话长期摘要：M2-E。
- 管理 API、动态模型/密钥配置、session CRUD：M3。
- 前端通话页、Docker Compose、完整 metrics/TTFT/首音频延迟：M4。
- RAG `always` 模式、TTL cache、多知识库查询：本阶段明确禁止。

## 自检结论

- M2-D 原始条目的 7 个实现要求已分别落到 Task 2–6。
- 单 KB、原始 session 保留、RAG 不自启动、M2 不做完整 metrics 等全局约束均有对应测试。
- `build_agent_session(settings) -> AgentSession` 与 worker CLI 两个显式产物均已覆盖。
- 计划未修改用户已有改动的总规划文件。
