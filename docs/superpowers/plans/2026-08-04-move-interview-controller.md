# Move Interview Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `InterviewAgentController` and its speech result types out of the LiveKit agent package into the interview business package.

**Architecture:** `liverag/agent/interview_assistant.py` remains the LiveKit adapter containing `LiveKitInterviewAgent`. The former `liverag/agent/interview_agent.py` becomes `liverag/interview/controller.py`, making the dependency direction explicit: LiveKit adapter → interview controller → interview service.

**Tech Stack:** Python 3.10, LiveKit Agents, pytest, Ruff, Pyright

## Global Constraints

- Do not delete files or directories in bulk.
- Preserve all controller classes, method signatures, and runtime behavior.
- Update every Python import and active architecture-plan path to the new module.

---

### Task 1: Move the controller module and update consumers

**Files:**
- Move: `liverag/agent/interview_agent.py` → `liverag/interview/controller.py`
- Modify: `liverag/agent/interview_assistant.py`
- Modify: `liverag/interview_main.py`
- Modify: `tests/interview/test_services.py`
- Modify: `docs/plans/interview-coach-plan.md`
- Modify: `docs/superpowers/plans/2026-08-04-interview-v1-completion.md`
- Modify: `docs/superpowers/plans/2026-08-04-split-interview-worker.md`

**Interfaces:**
- Consumes: existing `InterviewService`, `InterviewStateMachine`, repository records, and LiveKit callbacks.
- Produces: `liverag.interview.controller.InterviewAgentController`, `InterviewSpeech`, `InterviewSpeechKind`, and `AnswerTurnResult` with unchanged signatures.

- [x] **Step 1: Point tests at the intended module**

```python
from liverag.interview.controller import (
    InterviewAgentController,
    InterviewSpeechKind,
)
```

- [x] **Step 2: Run the focused test and verify the module is initially missing**

Run: `.venv/Scripts/python.exe -m pytest tests/interview/test_services.py -q`

Expected: collection fails with `ModuleNotFoundError: liverag.interview.controller`.

- [x] **Step 3: Move the module and update runtime imports**

Move the single explicit file:

```powershell
Move-Item -LiteralPath 'liverag/agent/interview_agent.py' -Destination 'liverag/interview/controller.py'
```

Use these imports in the LiveKit adapter and Worker composition root:

```python
from liverag.interview.controller import (
    InterviewAgentController,
    InterviewSpeech,
    InterviewSpeechKind,
)
```

```python
from liverag.interview.controller import InterviewAgentController
```

- [x] **Step 4: Replace active documentation paths**

Replace every active reference to:

```text
liverag/agent/interview_agent.py
```

with:

```text
liverag/interview/controller.py
```

and replace Python import examples with `from liverag.interview.controller import ...`.

- [x] **Step 5: Run static and regression verification**

```powershell
.venv/Scripts/ruff.exe check liverag/interview/controller.py liverag/agent/interview_assistant.py liverag/interview_main.py tests/interview --no-cache
.venv/Scripts/pyright.exe liverag/interview/controller.py liverag/agent/interview_assistant.py liverag/interview_main.py --pythonpath .venv/Scripts/python.exe --pythonversion 3.10
.venv/Scripts/python.exe -m pytest tests/interview tests/agent/test_assistant.py tests/agent/test_main.py -q
```

Expected: Ruff and Pyright report no errors; all selected tests pass.

- [ ] **Step 6: Commit the rename**

```bash
git add liverag/agent/interview_agent.py liverag/interview/controller.py liverag/agent/interview_assistant.py liverag/interview_main.py tests/interview docs/plans/interview-coach-plan.md docs/superpowers/plans/2026-08-04-*.md
git commit -m "refactor: move interview controller into domain package"
```
