# Interview Database Runtime Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow the Interview API and Worker to use the configured SQLite or PostgreSQL database URL at runtime.

**Architecture:** Add one nested immutable dataclass to the existing `AppSettings` composition. Replace the two SQLite-specific engine construction calls with the existing generic SQLAlchemy engine factory; repository and service construction remain unchanged.

**Tech Stack:** Python, dataclasses, SQLAlchemy, PostgreSQL, SQLite

## Global Constraints

- Use `settings.interview_database.url`; do not introduce `settings.interview_database_url`.
- Do not modify Interview Service, Repository, Agent, STT, or TTS logic.
- Do not add another database abstraction layer.

---

### Task 1: Add the nested Interview database configuration

**Files:**
- Modify: `liverag/config/settings.py`

**Interfaces:**
- Produces: `InterviewDatabaseSettings.url: str`
- Produces: `AppSettings.interview_database: InterviewDatabaseSettings`

- [ ] **Step 1: Add the immutable configuration dataclass**

```python
@dataclass(frozen=True)
class InterviewDatabaseSettings:
    """Interview 模块数据库配置。"""

    url: str = field(
        default_factory=lambda: _str_env(
            "INTERVIEW_DATABASE_URL",
            "sqlite:///~/.LiveRAG/liverag.db",
        )
    )
```

- [ ] **Step 2: Add it to `AppSettings`**

```python
interview_database: InterviewDatabaseSettings = field(
    default_factory=InterviewDatabaseSettings
)
```

- [ ] **Step 3: Verify environment loading**

Run: `uv run python -c "from liverag.config.settings import load_app_settings; print(load_app_settings().interview_database.url)"`

Expected: the configured URL, or `sqlite:///~/.LiveRAG/liverag.db` when unset.

### Task 2: Switch API and Worker engine construction

**Files:**
- Modify: `liverag/api/server.py`
- Modify: `liverag/interview_main.py`

**Interfaces:**
- Consumes: `settings.interview_database.url: str`
- Consumes: `create_database_engine(database_url: str, *, echo=False)`
- Produces: existing `SQLAlchemyInterviewRepository` construction with a URL-configured engine

- [ ] **Step 1: Replace API engine construction**

```python
interview_engine = create_database_engine(
    settings.interview_database.url
)
```

- [ ] **Step 2: Replace Worker engine construction**

```python
engine = create_database_engine(
    settings.interview_database.url
)
```

- [ ] **Step 3: Verify imports and syntax**

Run: `python -c "import ast, pathlib; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8')) for p in ['liverag/api/server.py', 'liverag/interview_main.py']]; print('syntax: ok')"`

Expected: `syntax: ok`.

- [ ] **Step 4: Verify source scope**

Run: `rg -n "create_sqlite_engine|create_database_engine|interview_database" liverag/api/server.py liverag/interview_main.py liverag/config/settings.py`

Expected: both entry points use `create_database_engine(settings.interview_database.url)` and neither imports `create_sqlite_engine`.
