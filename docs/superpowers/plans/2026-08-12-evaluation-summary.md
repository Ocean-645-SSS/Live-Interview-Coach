# Evaluation Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create one concise, traceable Markdown summary of the four frozen best-version evaluation reports.

**Architecture:** The summary is a documentation-only artifact in `evaluation/reports/`. It records the source report for each evaluation dimension, its benchmark context, its headline metrics, and source-specific limitations without recalculating or changing any result.

**Tech Stack:** Markdown.

## Global Constraints

- Use only the four user-designated `*-best-version.md` reports as metric sources.
- Do not modify benchmark implementations, production code, scenarios, or source reports.
- Retain direct relative links to each source report.

---

### Task 1: Create the consolidated evaluation summary

**Files:**
- Create: `evaluation/reports/evaluation-summary.md`

**Interfaces:**
- Consumes: four frozen Markdown reports in `evaluation/reports/`.
- Produces: a Markdown document with the evaluation overview and one compact section per report.

- [ ] **Step 1: Extract the source-of-truth benchmark metadata and headline metrics**

Read these files: `evaluation/reports/2026-08-12-rag-best-version.md`, `evaluation/reports/2026-08-11-answer-best-version.md`, `evaluation/reports/2026-08-12-latency-best-version.md`, and `evaluation/reports/2026-08-11-behavior-best-version.md`.

- [ ] **Step 2: Write the minimal consolidated document**

Include a source report table, a results-at-a-glance table, the relevant latency normal/RAG breakdown, and explicitly preserve caveats that RAG is retrieval-only and behavior difficulty adaptation is offline-policy only.

- [ ] **Step 3: Verify the document is traceable and faithful**

Run: `Get-Content evaluation\\reports\\evaluation-summary.md -Raw`

Expected: all four source report links resolve relatively and every displayed number matches its frozen source report.
