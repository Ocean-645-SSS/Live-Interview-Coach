# SQLAlchemy Interview Repository Implementation Plan

> **Status:** 本计划记录 Repository 建立阶段；旧 Store 并存约束已由 [Interview Repository Cutover Implementation Plan](2026-08-02-interview-repository-cutover.md) 完成并取代。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立与现有 Interview 持久化契约等价的 Repository Protocol 和 SQLAlchemy 实现，同时保持状态机继续使用旧 `InterviewStore`。

**Architecture:** `repository.py` 保存稳定接口和共享异常，`sqlalchemy_repository.py` 只依赖 SQLAlchemy Session 工厂并负责 ORM/Record/Pydantic 转换。当前步骤不修改状态机接线，也不把 Answer、Event、Session 合并成新的三记录事务。

**Tech Stack:** Python 3.10、SQLAlchemy 2.x、SQLite、Pydantic、pytest、Ruff。

## Global Constraints

- 第一步只使用 SQLAlchemy + SQLite，不引入 Alembic、PostgreSQL、Redis 或用户体系。
- `models.py` 是新数据库结构的唯一来源；不得扩展自定义 `MIGRATIONS`。
- 保留 `records.py` 作为当前业务层返回类型。
- 不修改 `state_machine.py` 对旧 `InterviewStore` 的依赖。
- 不实现 Answer + Event + Session 三记录原子事务。

---

### Task 1: Repository Protocol 和共享异常

**Files:**
- Create: `liverag/interview/repository.py`
- Modify: `liverag/interview/store.py`
- Test: `tests/interview/test_sqlalchemy_repository.py`

**Interfaces:**
- Consumes: `InterviewConfig`、`InterviewPlan`、`AnswerEvaluation`、Record dataclass 和领域枚举。
- Produces: `InterviewRepository`、`RecordNotFoundError`、`ConcurrentUpdateError`、`DuplicateEventError`。

- [ ] **Step 1: 写出 Protocol 结构测试**

```python
assert isinstance(repository, InterviewRepository)
```

- [ ] **Step 2: 验证测试因模块不存在而失败**

Run: `python -m pytest tests/interview/test_sqlalchemy_repository.py -q`
Expected: FAIL，提示 `liverag.interview.repository` 不存在。

- [ ] **Step 3: 定义完整持久化接口**

```python
@runtime_checkable
class InterviewRepository(Protocol):
    def create_interview(...) -> InterviewRecord: ...
    def get_interview(...) -> InterviewRecord: ...
    def list_interviews(...) -> list[InterviewRecord]: ...
    def get_interview_config(...) -> InterviewConfig: ...
    def get_interview_plan(...) -> InterviewPlan | None: ...
    def save_interview_plan(...) -> InterviewRecord: ...
    def update_interview_state(...) -> InterviewRecord: ...
    def create_session(...) -> InterviewSessionRecord: ...
    def get_session(...) -> InterviewSessionRecord: ...
    def list_sessions(...) -> list[InterviewSessionRecord]: ...
    def update_session_snapshot(...) -> InterviewSessionRecord: ...
    def create_attempt(...) -> InterviewAttemptRecord: ...
    def get_attempt(...) -> InterviewAttemptRecord: ...
    def list_attempts(...) -> list[InterviewAttemptRecord]: ...
    def update_attempt_state(...) -> InterviewAttemptRecord: ...
    def event_exists(...) -> bool: ...
    def record_transition(...) -> InterviewEventRecord: ...
    def list_events(...) -> list[InterviewEventRecord]: ...
    def create_answer(...) -> InterviewAnswerRecord: ...
    def get_answer(...) -> InterviewAnswerRecord: ...
    def list_answers(...) -> list[InterviewAnswerRecord]: ...
    def update_answer_state(...) -> InterviewAnswerRecord: ...
    def save_evaluation(...) -> AnswerEvaluation: ...
    def get_evaluation(...) -> AnswerEvaluation: ...
    def list_evaluations(...) -> list[AnswerEvaluation]: ...
    def create_report(...) -> InterviewReportRecord: ...
    def get_report(...) -> InterviewReportRecord: ...
    def get_report_by_session(...) -> InterviewReportRecord | None: ...
    def start_report_generation(...) -> InterviewReportRecord: ...
    def fail_report(...) -> InterviewReportRecord: ...
    def complete_report(...) -> InterviewReportRecord: ...
```

- [ ] **Step 4: 让旧 Store 导入并重新导出共享异常**

```python
from liverag.interview.repository import (
    ConcurrentUpdateError,
    DuplicateEventError,
    RecordNotFoundError,
)
```

- [ ] **Step 5: 运行旧状态机回归**

Run: `python -m pytest tests/interview/test_state_machine.py -q`
Expected: PASS。

### Task 2: SQLAlchemy Repository 实现

**Files:**
- Create: `liverag/interview/sqlalchemy_repository.py`
- Test: `tests/interview/test_sqlalchemy_repository.py`

**Interfaces:**
- Consumes: `sessionmaker[Session]`、七个 ORM Models 和 Task 1 的 Protocol/异常。
- Produces: `SQLAlchemyInterviewRepository(session_factory)`，覆盖 Protocol 的所有方法。

- [ ] **Step 1: 写生命周期测试**

```python
interview = repository.create_interview(title="测试", config=config)
ready = repository.save_interview_plan(
    interview_id=interview.id,
    plan=plan,
    expected_version=interview.version,
)
session = repository.create_session(interview_id=ready.id)
assert session.state is InterviewState.READY
```

- [ ] **Step 2: 写约束测试**

```python
with pytest.raises(ConcurrentUpdateError):
    repository.update_interview_state(
        interview_id=interview.id,
        state=InterviewState.FAILED,
        expected_version=1,
    )
```

- [ ] **Step 3: 实现 ORM/Record 转换和全部 CRUD**

实现必须使用 SQLAlchemy `select()`、`update()`、`Session.flush()` 和 `session_scope()`；Pydantic 内容继续保存稳定 JSON，UTC `datetime` 在 Record 边界转为 ISO 8601 字符串。分页、空字符串、非负计数、乐观锁、事件幂等和报告状态规则必须保持旧 Store 语义。

- [ ] **Step 4: 验证 SQLAlchemy Repository**

Run: `python -m pytest tests/interview/test_sqlalchemy_repository.py -q`
Expected: PASS。

### Task 3: 范围与质量门槛

**Files:**
- Modify: `docs/plans/interview-coach-plan.md`

- [ ] **Step 1: 更新 Repository 进度但不勾选测试迁移或状态机接线**

```markdown
- [√] 建立 Interview Repository Protocol 和 SQLAlchemy 实现。
- [ ] 将旧 InterviewStore 测试迁移为 Repository 契约测试。
- [ ] 让状态机依赖 Repository Protocol。
```

- [ ] **Step 2: 运行质量检查**

Run: `ruff check liverag/interview/repository.py liverag/interview/sqlalchemy_repository.py tests/interview/test_sqlalchemy_repository.py --no-cache`
Expected: PASS。

Run: `python -m pytest tests/interview -q`
Expected: PASS。

- [ ] **Step 3: 核对范围**

确认 `state_machine.py`、Alembic、PostgreSQL、Redis 和 Background Worker 均未修改，然后停止。
