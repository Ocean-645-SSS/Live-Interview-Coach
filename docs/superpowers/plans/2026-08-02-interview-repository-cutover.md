# Interview Repository Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Interview 状态机和持久化测试完整切换到 SQLAlchemy Repository，并移除旧 sqlite3 Store 与自定义 migrations。

**Architecture:** `InterviewStateMachine` 只依赖 `InterviewRepository` Protocol；测试通过统一的 Repository fixture 注入 `SQLAlchemyInterviewRepository`。SQLAlchemy metadata 成为唯一 schema 来源，旧 `store.py` 和 `migrations.py` 在引用归零、回归通过后逐文件删除。

**Tech Stack:** Python 3.10、SQLAlchemy 2.x、SQLite、Pydantic、pytest、Pyright、Ruff。

## Global Constraints

- 第一步只使用 SQLAlchemy + SQLite，不引入 Alembic、PostgreSQL、Redis 或用户体系。
- 不实现 Answer + Event + Session 三记录原子事务。
- 不批量删除文件；只删除明确确认无运行时引用的 `liverag/interview/store.py` 和 `liverag/interview/migrations.py`。
- 保留 `records.py` 作为 Repository 对外返回契约。

---

### Task 1: Repository 契约测试

**Files:**
- Modify: `tests/interview/test_sqlalchemy_repository.py`

**Interfaces:**
- Consumes: `InterviewRepository` Protocol、`SQLAlchemyInterviewRepository(session_factory)`。
- Produces: 只通过 Protocol 调用的 Interview、Session、Attempt、Event、Answer、Evaluation、Report 行为测试。

- [x] **Step 1: 将 fixture 返回类型收敛到 Protocol**

```python
@pytest.fixture
def repository(tmp_path: Path) -> Iterator[InterviewRepository]:
    engine = create_sqlite_engine(tmp_path / "interview.db")
    Base.metadata.create_all(engine)
    yield SQLAlchemyInterviewRepository(create_session_factory(engine))
    engine.dispose()
```

- [x] **Step 2: 覆盖旧 Store 的公共约束**

```python
with pytest.raises(ConcurrentUpdateError, match="版本已变化"):
    repository.update_interview_state(
        interview_id=interview.id,
        state=InterviewState.FAILED,
        expected_version=1,
    )

with pytest.raises(DuplicateEventError, match="已经处理"):
    repository.record_transition(
        event_id=event.id,
        session_id=session.id,
        event_type="start",
        payload={},
        expected_version=1,
        state_before=InterviewState.READY,
        state_after=InterviewState.INTRODUCTION,
        resume_state=None,
        current_question_index=0,
        current_question_id="question-1",
        follow_up_count=0,
        started_at=None,
        ended_at=None,
    )
```

- [x] **Step 3: 运行契约测试**

Run: `python -m pytest tests/interview/test_sqlalchemy_repository.py -q`
Expected: PASS。

### Task 2: 状态机切换到 Repository Protocol

**Files:**
- Modify: `liverag/interview/state_machine.py`
- Modify: `tests/interview/test_state_machine.py`

**Interfaces:**
- Consumes: `InterviewRepository.event_exists()`、`get_session()`、`get_interview_plan()`、`record_transition()`。
- Produces: `InterviewStateMachine(repository: InterviewRepository)`。

- [x] **Step 1: 替换状态机依赖类型**

```python
from liverag.interview.repository import DuplicateEventError, InterviewRepository

class InterviewStateMachine:
    def __init__(self, repository: InterviewRepository):
        self._repository = repository
```

- [x] **Step 2: 测试运行时注入 SQLAlchemy 实现**

```python
engine = create_sqlite_engine(tmp_path / "interview.db")
Base.metadata.create_all(engine)
repository = SQLAlchemyInterviewRepository(create_session_factory(engine))
machine = InterviewStateMachine(repository)
```

- [x] **Step 3: 运行状态机测试**

Run: `python -m pytest tests/interview/test_state_machine.py -q`
Expected: PASS。

### Task 3: 移除旧 SQLite 实现

**Files:**
- Delete: `liverag/interview/store.py`
- Delete: `liverag/interview/migrations.py`
- Modify: `docs/plans/interview-coach-plan.md`

**Interfaces:**
- Consumes: Task 1 和 Task 2 的全绿结果。
- Produces: 仅由 `models.py` + `repository.py` + `sqlalchemy_repository.py` 管理的 Interview 持久化结构。

- [x] **Step 1: 确认源码和测试无旧引用**

Run: `rg -n "interview\.(store|migrations)|InterviewStore|apply_migrations" liverag tests`
Expected: 无匹配。

- [x] **Step 2: 分别删除两个明确文件**

使用单文件补丁分别删除 `liverag/interview/store.py` 和 `liverag/interview/migrations.py`，不删除目录。

- [x] **Step 3: 更新主计划**

```markdown
- [√] 将旧 `InterviewStore` 行为迁移为 Repository 契约测试。
- [√] 让状态机依赖 Repository Protocol，并注入 SQLAlchemy Repository。
- [√] 移除旧 sqlite3 Store 和自定义 migration runner。
```

- [x] **Step 4: 运行完整验证**

Run: `pyright --pythonpath .venv/Scripts/python.exe liverag/interview/repository.py liverag/interview/sqlalchemy_repository.py liverag/interview/state_machine.py`
Expected: `0 errors`。

Run: `ruff check liverag/interview tests/interview --no-cache`
Expected: PASS。

Run: `python -m pytest tests/interview -q`
Expected: PASS。

Run: `rg -n "interview\.(store|migrations)|InterviewStore|apply_migrations" liverag tests`
Expected: 无匹配。
