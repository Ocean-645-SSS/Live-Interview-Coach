# Interview Turn Latency Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure and summarize the interval from a candidate stopping speech to the browser receiving the first audible interviewer audio frame.

**Architecture:** A client-only hook analyzes the existing local microphone and remote Agent tracks with Web Audio API. It records only completed turns in memory and derives percentiles locally, so both endpoint timestamps share `performance.now()` and no extra real-time network call can affect the measured path. The live-page development diagnostics presents the latest sample and P50/P90/P95.

**Tech Stack:** Next.js 15, React 19, LiveKit client 2, Web Audio API, TypeScript.

## Global Constraints

- Sample only during a persisted `LISTENING` answer window.
- Do not publish raw audio or transcripts from the browser.
- Use refs for high-frequency audio samples; React state may update only after a completed latency sample.
- Treat this as a headset benchmark: speaker feedback can make local VAD unreliable.

---

### Task 1: Add a browser-local turn-latency probe

**Files:**
- Create: `LiveRAG-Fronted/agent-starter-react/hooks/use-interview-turn-latency.ts`

**Interfaces:**
- Consumes: `Room`, a local `LocalAudioTrack`, Agent participant identity, and `enabled`.
- Produces: `useInterviewTurnLatency(...)` returning `{ lastSample, summary }`, where summary contains count, P50, P90, and P95 milliseconds.

- [ ] **Step 1: Define pure percentile behavior**

```ts
export function percentile(values: number[], percentileValue: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.ceil((percentileValue / 100) * sorted.length) - 1];
}
```

- [ ] **Step 2: Detect local speech end without React renders**

```ts
const rms = calculateRms(localAnalyser.getByteTimeDomainData(buffer));
if (rms >= SPEECH_RMS_THRESHOLD) {
  sawSpeechRef.current = true;
  silenceStartedAtRef.current = null;
} else if (sawSpeechRef.current && silenceElapsed >= SILENCE_WINDOW_MS) {
  pendingTurnStartedAtRef.current = performance.now();
}
```

- [ ] **Step 3: Detect first Agent audio and finish one pending sample**

```ts
if (pendingTurnStartedAtRef.current !== null && agentRms >= SPEECH_RMS_THRESHOLD) {
  completeSample(performance.now() - pendingTurnStartedAtRef.current);
}
```

- [ ] **Step 4: Verify TypeScript**

Run: `corepack pnpm typecheck`

Expected: PASS.

### Task 2: Surface benchmark evidence in the live interview diagnostics

**Files:**
- Modify: `LiveRAG-Fronted/agent-starter-react/components/interview/interview-live.tsx`

**Interfaces:**
- Consumes: `useInterviewTurnLatency` from Task 1.
- Produces: a development-only diagnostics block that exposes sample count, last sample, P50, P90, and P95.

- [ ] **Step 1: Connect the hook to existing track sources**

```tsx
const latency = useInterviewTurnLatency({
  room,
  microphoneTrack: microphoneTrack?.track,
  agentIdentity: assistant.agent?.identity,
  enabled: answerWindowOpen,
});
```

- [ ] **Step 2: Render results only in development diagnostics**

```tsx
<dt>Turn latency samples</dt><dd>{latency.summary.count}</dd>
<dt>Stop → first audio (last/P50/P90/P95)</dt>
<dd>{formatLatency(latency.lastSample)} / {formatLatency(latency.summary.p50)} / {formatLatency(latency.summary.p90)} / {formatLatency(latency.summary.p95)}</dd>
```

- [ ] **Step 3: Verify static checks and build**

Run: `corepack pnpm lint; corepack pnpm typecheck; corepack pnpm build`

Expected: all commands exit 0.

### Task 3: Document repeatable manual benchmark procedure

**Files:**
- Modify: `docs/系统架构与数据流.md`

**Interfaces:**
- Consumes: displayed probe summaries from Task 2.
- Produces: an explicit latency definition and a 30-turn measurement procedure for ordinary, follow-up, and RAG turns.

- [ ] **Step 1: Add the metric definition**

```text
Turn latency = browser-side timestamp of local speech ending − browser-side timestamp of the first non-silent Agent audio frame.
```

- [ ] **Step 2: Add the test matrix and reporting format**

```text
For each scenario, collect at least 30 completed samples with headphones and report P50/P90/P95 separately.
```

- [ ] **Step 3: Run final verification**

Run: `corepack pnpm lint; corepack pnpm typecheck; corepack pnpm build`

Expected: all commands exit 0.
