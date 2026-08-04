# Split Interview Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate the LiveKit interview Agent behavior from the interview Worker composition root without changing runtime behavior.

**Architecture:** Move `LiveKitInterviewAgent` into `liverag/agent/interview_assistant.py`, beside the generic `VoiceAssistant`. Keep `liverag/interview_main.py` responsible for metadata parsing, dependency construction, LiveKit job lifecycle, and process startup. Import the extracted Agent into the Worker entrypoint.

**Tech Stack:** Python 3.10, LiveKit Agents, pytest, Ruff, Pyright

## Global Constraints

- Do not delete files or directories in bulk.
- Preserve the public behavior and method signatures of `LiveKitInterviewAgent`.
- Keep `interview_main.py` as the `liverag-interview-agent` script entrypoint.

---

### Task 1: Extract the LiveKit interview adapter

**Files:**
- Create: `liverag/agent/interview_assistant.py`
- Modify: `liverag/interview_main.py`
- Modify: `tests/interview/test_interview_worker.py`

**Interfaces:**
- Consumes: `InterviewAgentController`, `InterviewSpeech`, `InterviewSpeechKind`, and LiveKit `Agent` callbacks.
- Produces: `LiveKitInterviewAgent(controller: InterviewAgentController)` with `on_enter`, `on_user_turn_completed`, `llm_node`, and `_play` unchanged.

- [x] **Step 1: Change the behavioral test to import the Agent from its new module**

```python
from liverag.agent.interview_assistant import LiveKitInterviewAgent
from liverag.interview_main import InterviewJobMetadata
```

- [x] **Step 2: Run the focused test and verify the new module is initially missing**

Run: `.venv/Scripts/python.exe -m pytest tests/interview/test_interview_worker.py -q`

Expected: collection fails with `ModuleNotFoundError: liverag.agent.interview_assistant`.

- [x] **Step 3: Create the extracted adapter and update the composition root**

Create `liverag/agent/interview_assistant.py` with the LiveKit callback adapter:

```python
"""LiveKit 实时语音事件与面试业务控制器之间的适配层。"""

import asyncio

from livekit.agents import Agent, ModelSettings, llm

from liverag.interview.controller import (
    InterviewAgentController,
    InterviewSpeech,
    InterviewSpeechKind,
)
from liverag.interview.schemas import InterviewState


class LiveKitInterviewAgent(Agent):
    def __init__(self, controller: InterviewAgentController) -> None:
        super().__init__(instructions="按照面试计划逐题进行模拟面试。")
        self._controller = controller
        self._turn_lock = asyncio.Lock()

    async def on_enter(self) -> None:
        async with self._turn_lock:
            state_before = self._controller.get_session().state
            first_speech = self._controller.start()
            await self._play(first_speech)
            if first_speech.kind is InterviewSpeechKind.INTRODUCTION:
                question = self._controller.introduction_spoken()
                await self._play(question)
                self._controller.prompt_spoken(question.kind)
            elif first_speech.kind is InterviewSpeechKind.CLOSING:
                self._controller.complete()
            elif state_before is not InterviewState.LISTENING:
                self._controller.prompt_spoken(first_speech.kind)

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        del turn_ctx
        transcript = (new_message.text_content or "").strip()
        if not transcript:
            return
        async with self._turn_lock:
            result = await self._controller.receive_final_answer(transcript)
            speech = result.next_speech
            await self._play(speech)
            if speech.kind is InterviewSpeechKind.CLOSING:
                self._controller.complete()
            else:
                self._controller.prompt_spoken(speech.kind)

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ) -> None:
        del chat_ctx, tools, model_settings
        return None

    async def _play(self, speech: InterviewSpeech) -> None:
        handle = self.session.say(
            speech.text, allow_interruptions=False, add_to_chat_ctx=True
        )
        await handle.wait_for_playout()


__all__ = ["LiveKitInterviewAgent"]
```

Delete the old class definition from `liverag/interview_main.py`, reduce its LiveKit imports to the Worker dependencies, and import the adapter:

```python
from livekit.agents import AgentServer, JobContext, cli, room_io

from liverag.interview.controller import InterviewAgentController
from liverag.agent.interview_assistant import LiveKitInterviewAgent
```

Keep the composition root exports limited to:

```python
__all__ = [
    "InterviewJobMetadata",
    "build_interview_service",
    "interview_agent_entrypoint",
    "main",
    "server",
]
```

- [x] **Step 4: Run focused and regression verification**

Run:

```powershell
.venv/Scripts/ruff.exe check liverag/agent/interview_assistant.py liverag/interview_main.py tests/interview/test_interview_worker.py --no-cache
.venv/Scripts/pyright.exe liverag/agent/interview_assistant.py liverag/interview_main.py --pythonpath .venv/Scripts/python.exe --pythonversion 3.10
.venv/Scripts/python.exe -m pytest tests/interview/test_interview_worker.py tests/interview/test_services.py -q
```

Expected: Ruff and Pyright report no errors; all selected tests pass.

- [ ] **Step 5: Commit the extraction**

```bash
git add liverag/agent/interview_assistant.py liverag/interview_main.py tests/interview/test_interview_worker.py docs/superpowers/plans/2026-08-04-split-interview-worker.md
git commit -m "refactor: split interview agent from worker entrypoint"
```
