# SQLite Alembic Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Alembic solely responsible for the production Interview schema while preserving explicit test-database setup.

**Architecture:** Production API and Interview Agent startup construct only the SQLite engine and session factory. Tests that own an isolated in-memory or temporary SQLite database may create the ORM schema explicitly. Existing databases are enrolled manually with a verified, backed-up `alembic stamp head`; no startup path runs Alembic.

**Tech Stack:** Python, SQLAlchemy 2, SQLite, Alembic, pytest

## Global Constraints

- Do not modify ORM models or the baseline migration.
- Do not add PostgreSQL or change voice, frontend, or business logic.
- Do not run `alembic upgrade` from FastAPI or Agent startup.
- Do not execute `alembic stamp` automatically.

---

### Task 1: Enforce the production schema boundary

**Files:**
- Modify: `liverag/api/server.py`
- Modify: `liverag/interview_main.py`
- Test: `tests/interview/test_schema_management.py`

**Interfaces:**
- Consumes: `create_sqlite_engine(Path) -> Engine` and `create_session_factory(Engine)`
- Produces: production startup paths with no schema creation or Alembic execution

- [ ] **Step 1: Add a regression test**

```python
def test_production_startup_does_not_manage_interview_schema() -> None:
    for path in PRODUCTION_STARTUP_PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not _schema_management_calls(tree)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/interview/test_schema_management.py -q`
Expected: failure identifying `metadata.create_all` in both production startup paths.

- [ ] **Step 3: Remove only production `create_all` calls and unused model imports**

Keep engine/session construction unchanged. Do not add an Alembic command in either startup path.

- [ ] **Step 4: Run focused and full tests**

Run: `uv run pytest tests/interview/test_schema_management.py tests/interview tests/api -q`
Expected: all selected tests pass; test-owned temporary databases continue using `Base.metadata.create_all()`.

- [ ] **Step 5: Verify source scope**

Run: `git diff --check` and `rg -n "metadata\.create_all|alembic.*upgrade|alembic.*stamp" liverag tests`
Expected: `create_all` occurs only in test code, and production code contains no automatic Alembic invocation.
