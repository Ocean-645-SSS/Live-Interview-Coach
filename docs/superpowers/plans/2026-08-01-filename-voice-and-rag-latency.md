# Original Filename, Dual TTS Voice, and RAG Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve and display users' original document filenames everywhere, offer Cherry female and Ethan male TTS voices on the main page, and reduce RAG response latency using measured stage-level evidence without weakening grounded-answer safeguards.

**Architecture:** Keep collision-safe internal document storage and make `original_filename` the authoritative public display field across RAG Core, API gateway, evidence records, and frontend. Reuse the existing runtime model-config API for a two-choice TTS selector whose value is saved before a call and applied to the next LiveKit session. Instrument the RAG pipeline before optimizing it, then remove avoidable serial work and duplicate same-turn retrieval while retaining strict no-evidence behavior.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, LightRAG, LiveKit Agents, pytest, React 19, Next.js 15, TypeScript, pnpm.

## Scope Decisions

- Implement now: original filename consistency, two TTS voices, main-page voice selection, RAG latency measurement and targeted optimization.
- Defer: indexing progress UI (ISSUE-003) and indexing cancellation (ISSUE-001).
- Preserve: current anti-hallucination behavior and improved logical voice-turn detection.
- Default voice remains `Cherry`（芊悦，女声）.
- Male voice is `Ethan`（晨煦）: standard Mandarin with a slight northern accent and a warm, energetic style. Both selected voices support the existing Qwen realtime TTS family according to the [official Alibaba Cloud voice list](https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list).
- Voice changes are allowed while idle, including after a call ends, and are locked during an active call. A saved selection applies to the next call; it never mutates the current session mid-stream.
- Existing files whose true original filename was never persisted cannot be reconstructed reliably. Do not guess; use the best stored metadata fallback and guarantee correctness for all new uploads.

## Acceptance Targets

- Uploading `Fino-Net 项目说明 v1.docx` results in that exact filename on document cards, job/detail responses, RAG evidence, and downloads; internal storage may still use UUID-based paths.
- The main page exposes exactly `芊悦 · 女声` and `晨煦 · 男声`; the control is keyboard accessible, persists via backend config, is disabled during a call, and becomes available immediately after hang-up.
- A newly started session records and uses the selected voice; switching after a call affects the next session only.
- Every RAG request reports stage timings sufficient to distinguish retrieval/model/network overhead from local code overhead.
- A completed voice turn performs at most one outbound RAG query for the same normalized query and knowledge-base/index version.
- On a fixed warm-cache benchmark set, target RAG Core p50 ≤ 2.5 s and p95 ≤ 5 s. If the upstream LightRAG/model call alone exceeds the target, retain the measurements and document that external bottleneck instead of masking it with unsafe timeouts.
- Miss/failure responses still instruct the answer model not to invent knowledge-base facts.

---

### Task 1: Lock the original-filename API contract

**Files:**
- Modify: `liverag/rag/server.py`
- Modify: `liverag/api/rag_gateway.py`
- Test: `tests/rag/test_client.py`
- Test: `tests/api/test_rag_proxy.py`

**Interfaces:**
- Consumes: multipart upload filename and stored `documents.original_filename`
- Produces: a non-empty `original_filename` in upload, list, detail, job/document-summary, evidence-document, and download contracts

- [ ] **Step 1: Add failing contract tests**

Add Unicode/space-containing filename cases and assert the exact original filename survives upload, list, detail, indexing-job lookup, evidence serialization, and `Content-Disposition`. Add a compatibility case where the gateway receives an older response without `original_filename`; it may derive only a safe basename fallback and must never expose an absolute/internal source path as the display label.

- [ ] **Step 2: Run the focused tests and confirm the uncovered paths fail**

Run: `.venv\Scripts\python.exe -m pytest tests/rag/test_client.py tests/api/test_rag_proxy.py -q --basetemp .pytest-runs\original-filename`

Expected: at least the newly added evidence/gateway compatibility assertions fail before normalization is completed.

- [ ] **Step 3: Make `original_filename` authoritative**

Keep `_write_source_file()` collision-safe. Normalize public document summaries from the metadata field first, remove internal `source_file_path` from browser-facing payloads, and use a basename-only fallback for legacy payloads. Ensure download headers use the same sanitized original name without changing the stored physical path.

- [ ] **Step 4: Run filename regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/rag/test_client.py tests/api/test_rag_proxy.py -q --basetemp .pytest-runs\original-filename-final`

Expected: all tests pass and duplicate uploads with the same original name remain physically distinct.

### Task 2: Display the original filename in every frontend surface

**Files:**
- Modify: `E:/CS/project/LiveRAG-Fronted/agent-starter-react/types/liverag.ts`
- Modify: `E:/CS/project/LiveRAG-Fronted/agent-starter-react/components/knowledge/document-card.tsx`
- Modify: `E:/CS/project/LiveRAG-Fronted/agent-starter-react/components/knowledge/knowledge-workspace.tsx`
- Modify: `E:/CS/project/LiveRAG-Fronted/agent-starter-react/components/voice/rag-turn-panel.tsx`

**Interfaces:**
- Consumes: `DocumentSummary.original_filename` and evidence document display names
- Produces: original filenames in cards, search, accessible labels, and hit-only RAG evidence

- [ ] **Step 1: Tighten frontend types and display helpers**

Add `original_filename` to the evidence-document type, and centralize a small display-name helper that prefers `original_filename`, then a safe basename fallback. Do not render UUID storage paths or percent/hex-encoded internal filenames.

- [ ] **Step 2: Apply the helper consistently**

Use it for document titles, delete labels, knowledge search, and source labels inside hit cards. Preserve the existing rule that miss/failed/not-queried RAG records are hidden.

- [ ] **Step 3: Run frontend quality gates**

Run in `E:/CS/project/LiveRAG-Fronted/agent-starter-react`: `corepack pnpm lint; corepack pnpm typecheck; corepack pnpm build`

Expected: all commands exit `0`; a manual Unicode filename upload displays identically in knowledge management and the conversation evidence card.

### Task 3: Restrict the supported TTS voice contract to Cherry and Ethan

**Files:**
- Modify: `liverag/config/settings.py`
- Modify: `liverag/api/server.py`
- Test: `tests/config/test_settings.py`
- Test: `tests/api/test_model_config.py`
- Test: `tests/agent/test_providers.py`

**Interfaces:**
- Consumes: `PUT /model/config` with `{ "voice": { "tts": { "voice": "Cherry|Ethan" } } }`
- Produces: two public TTS options and a next-session runtime configuration

- [ ] **Step 1: Write failing voice-option tests**

Assert that `/model/options` exposes exactly Cherry and Ethan with stable Chinese display metadata; both values are accepted, unsupported voices return `422`, the default remains Cherry, and saving a different voice does not rewrite an already active session's recorded configuration.

- [ ] **Step 2: Run the tests and confirm the current broader option list fails**

Run: `.venv\Scripts\python.exe -m pytest tests/config/test_settings.py tests/api/test_model_config.py tests/agent/test_providers.py -q --basetemp .pytest-runs\dual-voice`

Expected: the exact-two-options assertion fails before the public contract is restricted.

- [ ] **Step 3: Implement the two-voice allowlist**

Keep provider/model settings unchanged. Return only Cherry and Ethan in public options, validate persisted updates against this allowlist, preserve masked-secret behavior, and retain `effective="next_session"`. Ensure `build_tts()` receives the saved voice when constructing a new Agent session.

- [ ] **Step 4: Run voice regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/config/test_settings.py tests/api/test_model_config.py tests/agent/test_providers.py -q --basetemp .pytest-runs\dual-voice-final`

Expected: all tests pass for both voices and active-session immutability.

### Task 4: Add the main-page voice selector

**Files:**
- Create: `E:/CS/project/LiveRAG-Fronted/agent-starter-react/components/voice/voice-picker.tsx`
- Modify: `E:/CS/project/LiveRAG-Fronted/agent-starter-react/components/voice/voice-experience.tsx`
- Modify: `E:/CS/project/LiveRAG-Fronted/agent-starter-react/types/liverag.ts`
- Modify: `E:/CS/project/LiveRAG-Fronted/agent-starter-react/app/globals.css`

**Interfaces:**
- Consumes: `GET /model/config`, `GET /model/options`, and `PUT /model/config`
- Produces: an idle-state two-choice selector that persists the next session's TTS voice

- [ ] **Step 1: Add typed config state and API loading**

Define the minimal model-config/voice-option types. Load the configured voice with the idle-page data, display a non-blocking error if it fails, and send only the nested TTS voice patch when a choice changes.

- [ ] **Step 2: Build the compact selector**

Place a compact segmented/popup control beside the current knowledge-base context on the main voice page, matching the existing monochrome rounded visual language. Show `芊悦 · 女声` and `晨煦 · 男声`, a clear selected state, a small “下次通话使用” hint, visible focus styles, and an `aria-label`. Do not add preview playback in this scope.

- [ ] **Step 3: Enforce call-state behavior**

Render the selected voice as locked/read-only during `LiveKitRoom` connection states. Re-enable editing after `onDisconnected`/hang-up completes. Disable the start-call action while a voice update is in flight so session construction cannot race the config write.

- [ ] **Step 4: Validate the frontend**

Run in `E:/CS/project/LiveRAG-Fronted/agent-starter-react`: `corepack pnpm lint; corepack pnpm typecheck; corepack pnpm build`

Expected: all commands exit `0`; keyboard and pointer selection work before/after calls, no switch is possible during a call, and a newly connected call uses the chosen backend configuration.

### Task 5: Instrument RAG latency by stage

**Files:**
- Modify: `liverag/rag/engine.py`
- Modify: `liverag/rag/server.py`
- Modify: `liverag/agent/tool/rag_client.py`
- Modify: `liverag/context/store.py`
- Test: `tests/rag/test_engine.py`
- Test: `tests/agent/tool/test_rag_client.py`
- Test: `tests/context/test_store.py`

**Interfaces:**
- Consumes: one `/query/context` request
- Produces: `rewrite_ms`, `retrieval_ms`, `evidence_gate_ms`, `serialization_ms`, `request_total_ms`, and client-observed `network_total_ms`

- [ ] **Step 1: Add failing stage-metric tests**

Use deterministic fake retrieval and relevance functions to assert each stage key exists, values are non-negative, server total covers internal stages, the client preserves server metrics, and the audit record retains them without exposing raw exceptions to the user interface.

- [ ] **Step 2: Run focused tests and confirm metrics are missing**

Run: `.venv\Scripts\python.exe -m pytest tests/rag/test_engine.py tests/agent/tool/test_rag_client.py tests/context/test_store.py -q --basetemp .pytest-runs\rag-latency-metrics`

Expected: new stage-metric assertions fail because only aggregate latency is currently reported.

- [ ] **Step 3: Add monotonic stage timing**

Measure local rewrite, LightRAG context query, evidence relevance gate, response extraction/serialization, and end-to-end server time separately. Preserve upstream metrics through `RagClient`; record client wall time as `network_total_ms` rather than overwriting server timing.

- [ ] **Step 4: Establish a baseline**

Run at least 20 warm queries across exact fact, paraphrase, follow-up, and miss cases against one fixed indexed knowledge base. Record p50/p95 per stage and overall in the implementation notes. Compare cold-start separately. Do not optimize or lower timeouts until the dominant stage is identified.

### Task 6: Remove avoidable duplicate and serial RAG work

**Files:**
- Modify: `liverag/agent/assistant.py`
- Modify: `liverag/context/manager.py`
- Modify: `liverag/rag/engine.py`
- Test: `tests/agent/test_assistant.py`
- Test: `tests/context/test_manager.py`
- Test: `tests/rag/test_engine.py`

**Interfaces:**
- Consumes: deterministic pre-answer retrieval plus an optional same-turn tool call
- Produces: one outbound query per normalized `(session_id, turn_index, kb_id, query)` and grounded evidence without an unconditional second LLM round trip

- [ ] **Step 1: Add failing deduplication and grounding tests**

Assert that successful prefetch followed by `search_knowledge_base` reuses the same turn result, changed queries still retrieve, miss/failure instructions remain strict, and evidence judged unusable cannot reach the answer as grounded context.

- [ ] **Step 2: Run focused tests and capture the duplicate-call failure**

Run: `.venv\Scripts\python.exe -m pytest tests/agent/test_assistant.py tests/context/test_manager.py tests/rag/test_engine.py -q --basetemp .pytest-runs\rag-latency-opt`

Expected: the same-turn prefetch/tool scenario performs more than one outbound query before memoization.

- [ ] **Step 3: Add same-turn retrieval reuse**

Store only the finalized current turn's structured RAG result in session context, keyed by knowledge base, normalized query, and turn index. Reuse it for a matching tool invocation, clear it at the next finalized user turn, and never cache failures across turns or index changes.

- [ ] **Step 4: Replace the unconditional serial relevance LLM call based on Task 5 evidence**

If `evidence_gate_ms` is material, replace the always-on `rag.llm_model_func()` boolean check with deterministic structured-evidence requirements (non-empty referenced chunks, document identity, minimum normalized query-term/identifier overlap). For ambiguous cases only, retain a bounded fast-model fallback behind configuration. Keep the existing strict miss response when the deterministic gate rejects evidence. If measurements instead show the LightRAG query dominates, leave the gate unchanged and optimize the measured upstream query profile rather than making an unsupported code change.

- [ ] **Step 5: Re-run the benchmark and regression tests**

Run: `.venv\Scripts\python.exe -m pytest tests/agent tests/context tests/rag tests/api -q --basetemp .pytest-runs\filename-voice-rag-final`

Expected: all tests pass, there is at most one outbound same-turn retrieval, no-evidence hallucination safeguards remain intact, and benchmark results either meet the stated targets or identify the remaining external model/network stage explicitly.

### Task 7: Integrated manual acceptance and documentation

**Files:**
- Modify after verification: `docs/FIRST_VERSION_TEST_FINDINGS.md`

- [ ] **Step 1: Build the complete stack**

Run in backend: `docker compose up -d --build`

Run in frontend: `corepack pnpm build`

Expected: API, Agent, RAG, LiveKit, and frontend start successfully; RAG service becomes healthy.

- [ ] **Step 2: Verify the four user flows**

1. Upload a Chinese/English mixed filename and confirm exact display in management, evidence, and download.
2. Select Cherry, start/end a call, select Ethan, then start a new call and confirm the two sessions use their respective voices.
3. Ask one exact document fact and one paraphrase; confirm grounded answers, hit-only evidence cards, and a single RAG request per turn.
4. Ask an absent fact; confirm no hit card and no invented answer.

- [ ] **Step 3: Update the findings document**

Mark original filename, TTS selection, and RAG latency with measured results and verification dates. Record indexing progress and cancellation as explicitly deferred, not resolved. Preserve the existing notes that hallucination and premature turn-ending behavior have improved.

