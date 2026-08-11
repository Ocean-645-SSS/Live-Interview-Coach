# Session Hot Words Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select and inject a plan-specific ASR hot-word list before an interview AgentSession is created.

**Architecture:** Keep `docs/HOT_WORDS.md` as the compatible global source of truth. `liverag.agent.hot_words` parses both existing two-column entries and the new metadata columns, ranks entries against the frozen `InterviewPlan`, then serializes only the selected canonical words to Volcengine's `corpus.context` format. The interview worker loads the plan before calling `build_agent_session` and passes the generated JSON explicitly.

**Tech Stack:** Python 3, Pydantic models already used by the interview domain, pytest, LiveKit Agents, Volcengine BigModel STT.

## Global Constraints

- Do not alter Volcengine binary packet construction, STT connection ordering, keepalive, or reconnect behavior.
- `HOT_WORDS.md` entries in the existing `word|level` form must remain valid.
- Only canonical `word` and `level` values are injected into the ASR request; aliases and known misrecognitions are metadata for future text normalization.
- Select 30–80 relevant entries, then append the configured fixed core entries without duplicate canonical words.
- When no plan-specific JSON is supplied, `build_agent_session` must retain its current default-file behavior.

---

### Task 1: Parse and select structured hot words

**Files:**
- Modify: `liverag/agent/hot_words.py`
- Modify: `docs/HOT_WORDS.md`
- Create: `tests/agent/test_hot_words.py`

**Interfaces:**
- Produces `HotWordEntry(word: str, level: int, domains: tuple[str, ...], aliases: tuple[str, ...], misrecognitions: tuple[str, ...])`.
- Produces `load_hot_word_entries(path: Path | None = None) -> list[HotWordEntry]`.
- Produces `select_session_hot_words(plan: InterviewPlan, entries: Sequence[HotWordEntry], *, min_words: int = 30, max_words: int = 80) -> list[HotWordEntry]`.
- Produces `serialize_hot_words(entries: Sequence[HotWordEntry]) -> str`.

- [ ] **Step 1: Write parsing and selection tests**

```python
def test_parser_accepts_legacy_and_metadata_entries() -> None:
    entries = load_hot_word_entries_from_text(
        "Agent|10\\nKafka|9|backend,middleware|卡夫卡|卡夫卡\\n"
    )
    assert entries[0] == HotWordEntry("Agent", 10, (), (), ())
    assert entries[1].domains == ("backend", "middleware")


def test_selector_prioritizes_plan_topics_and_fixed_core_words(plan: InterviewPlan) -> None:
    selected = select_session_hot_words(plan, entries, min_words=2, max_words=3)
    assert [entry.word for entry in selected] == ["Kafka", "Agent", "LLM"]
```

- [ ] **Step 2: Run the focused test file and verify it fails**

Run: `pytest tests/agent/test_hot_words.py -q`

Expected: FAIL because the parser and selector do not exist.

- [ ] **Step 3: Implement compatible parsing and deterministic ranking**

```python
@dataclass(frozen=True, slots=True)
class HotWordEntry:
    word: str
    level: int
    domains: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    misrecognitions: tuple[str, ...] = ()


def select_session_hot_words(
    plan: InterviewPlan,
    entries: Sequence[HotWordEntry],
    *,
    min_words: int = 30,
    max_words: int = 80,
) -> list[HotWordEntry]:
    unique_entries = _unique_entries(entries)
    plan_text = plan.model_dump_json().casefold()
    fixed_core_keys = {_word_key(word) for word in _FIXED_SESSION_CORE_WORDS}
    core_entries = [
        entry for entry in unique_entries if _word_key(entry.word) in fixed_core_keys
    ][:max_words]
    core_keys = {_word_key(entry.word) for entry in core_entries}
    dynamic_capacity = max_words - len(core_entries)
    ranked = sorted(
        (
            (_entry_relevance_score(plan_text, entry), index, entry)
            for index, entry in enumerate(unique_entries)
            if _word_key(entry.word) not in core_keys
        ),
        key=lambda item: (-item[0], -item[2].level, item[1]),
    )
    selected = [entry for score, _, entry in ranked if score > 0][:dynamic_capacity]
    return selected + core_entries
```

Keep `load_hot_words()` capped at its existing 100 entries for callers that do not opt into session selection. `build_session_hot_words()` must read the full source before selecting its 30–80 canonical entries. Keep `build_corpus_context()` and binary injection unchanged.

- [ ] **Step 4: Add metadata to representative `HOT_WORDS.md` entries**

```text
Agent|10|agent、llm|AI Agent、智能体|ancient、a jason
Kafka|10|backend、middleware、messaging|卡夫卡|卡夫卡
```

Do not overwrite or discard the user's existing uncommitted `HOT_WORDS.md` edits.

- [ ] **Step 5: Run the focused tests**

Run: `pytest tests/agent/test_hot_words.py -q`

Expected: PASS.

### Task 2: Accept an explicit session hot-word payload

**Files:**
- Modify: `liverag/agent/providers.py`
- Modify: `tests/agent/test_providers.py`

**Interfaces:**
- Changes `build_agent_session(settings: AppSettings, *, hot_words_json: str | None = None) -> AgentSession`.
- An explicit non-`None` payload takes precedence; `None` loads the configured/default hot-word file.

- [ ] **Step 1: Add a failing override test**

```python
def test_build_agent_session_uses_explicit_session_hot_words(monkeypatch) -> None:
    result = providers.build_agent_session(settings, hot_words_json='{"hotwords": []}')
    assert result.stt.options["hot_words_json"] == '{"hotwords": []}'
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest tests/agent/test_providers.py::test_build_agent_session_uses_explicit_session_hot_words -q`

Expected: FAIL because `build_agent_session` does not accept the keyword argument.

- [ ] **Step 3: Implement the optional override**

```python
def build_agent_session(settings: AppSettings, *, hot_words_json: str | None = None) -> AgentSession:
    if hot_words_json is None:
        hot_words_path = Path(voice.stt_hot_words_path) if voice.stt_hot_words_path else None
        hot_words_json = load_hot_words(hot_words_path)
```

- [ ] **Step 4: Run the provider tests**

Run: `pytest tests/agent/test_providers.py -q`

Expected: PASS.

### Task 3: Generate and inject hot words before ASR construction

**Files:**
- Modify: `liverag/interview_main.py`
- Modify: `tests/interview/test_interview_worker.py`

**Interfaces:**
- The worker calls `repository.get_session()` and `repository.get_interview_plan()` before `build_agent_session()`.
- `build_session_hot_words(plan: InterviewPlan) -> str` produces the selected Volcengine JSON payload.

- [ ] **Step 1: Add a failing worker test**

```python
def test_session_hot_words_are_selected_from_the_frozen_plan(monkeypatch) -> None:
    plan = object()
    monkeypatch.setattr(
        interview_main,
        "build_session_hot_words",
        lambda received_plan, path=None: '{"hotwords": [{"word": "Kafka", "level": 10}]}',
    )
    result = _build_session_hot_words_json(repository, "session-1")
    assert result == '{"hotwords": [{"word": "Kafka", "level": 10}]}'
```

- [ ] **Step 2: Run the targeted worker test and verify it fails**

Run: `pytest tests/interview/test_interview_worker.py -k hot_words -q`

Expected: FAIL because the session creation call has no selected hot-word argument.

- [ ] **Step 3: Load the frozen plan, generate session JSON, and construct STT**

```python
session_record = repository.get_session(metadata.session_id)
plan = repository.get_interview_plan(session_record.interview_id)
if plan is None:
    raise ValueError("面试 Session 缺少冻结计划")
hot_words_json = build_session_hot_words(plan)
session = build_agent_session(settings, hot_words_json=hot_words_json)
```

Log only count and selected canonical words; never log credentials or raw candidate profile text.

- [ ] **Step 4: Run focused worker, agent, and hot-word tests**

Run: `pytest tests/agent/test_hot_words.py tests/agent/test_providers.py tests/agent/test_volcengine_stt.py tests/interview/test_interview_worker.py -q`

Expected: PASS.

### Task 4: Verify static quality and preserve compatibility

**Files:**
- Modify only files from Tasks 1–3 as required by failures.

- [ ] **Step 1: Run the full project test suite**

Run: `pytest -q`

Expected: PASS.

- [ ] **Step 2: Run configured static checks**

Run: `corepack pnpm lint; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; corepack pnpm typecheck`

Expected: PASS.

- [ ] **Step 3: Review the diff**

Run: `git diff --check; git diff -- liverag/agent/hot_words.py liverag/agent/providers.py liverag/interview_main.py docs/HOT_WORDS.md tests/agent/test_hot_words.py tests/agent/test_providers.py tests/interview/test_interview_worker.py`

Expected: No whitespace errors; the only behavioral change is session-specific hot-word selection.

## Self-Review

- Plan parsing, ranking, override injection, worker timing, and regression testing each have a dedicated task.
- The plan preserves backward compatibility for existing two-column lines and for callers that omit `hot_words_json`.
- The plan deliberately excludes schema, evaluator, and database changes, which belong to the later B+C phase.
