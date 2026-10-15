# Human Annotation Template Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare new, non-executable human-annotation templates for answer scoring, behavior ablation, and ProjectSkillRelation mapping without changing production code, evaluator behavior, planner behavior, or prompts.

**Architecture:** Store drafts separately from runnable benchmark datasets. Each draft records source provenance and an `annotation_status` of `PENDING_HUMAN_REVIEW`; every gold field is `null`, never generated. The guide defines conversion into runnable calibration or frozen-test datasets only after human annotation and review.

**Tech Stack:** JSON, JSONL, Markdown.

## Global Constraints

- Do not modify production feature code, `Evaluator`, `Planner`, or any prompt.
- Do not reuse a row from the observed historical 50-sample answer or behavior benchmark.
- Do not write human score, four-dimension score, weighted score, failure class, expected behavior, or expected mapping gold.
- Do not seal a frozen dataset and do not create a hash lock.
- Frozen draft means “reserved for later human review”; it is not a runnable frozen-test dataset.

---

### Task 1: Create answer annotation drafts

**Files:**
- Create: `evaluation/annotation_templates/answer-calibration-v2.draft.json`
- Create: `evaluation/annotation_templates/answer-frozen-test-v1.draft.json`

**Interfaces:**
- Produces one pending row per question/answer pair with `question_type`, source provenance, candidate answer, and all scoring fields set to `null`.

- [ ] **Step 1: Select only unused question-bank IDs or newly authored scenario inputs**

Use two rows per question type in each split. Preserve the question-bank ID under `source_question_id`; generated project/scenario rows use `source_kind: "generated_for_annotation"` and a stable draft ID.

- [ ] **Step 2: Leave every gold field blank**

Each row must contain exactly these human-only fields with `null`: `human_dimension_scores`, `human_weighted_score`, `human_overall_score`, `expected_issues`, `unresolved_failure_labels`, and `annotation_notes`.

### Task 2: Create behavior ablation drafts

**Files:**
- Create: `evaluation/annotation_templates/behavior-calibration-v3.draft.json`
- Create: `evaluation/annotation_templates/behavior-frozen-test-v1.draft.json`

**Interfaces:**
- Produces shared raw candidate/job/conversation input and `selection_seed == case_id` for every ablation case.
- Produces null human labels for relevance, expected skill/topic, follow-up decision, category, and difficulty action.

- [ ] **Step 1: Write four calibration and four frozen archetypes**

Cover taxonomy-matched, taxonomy-unmatched, direct-project-evidence, and related-project-evidence cases. Store one immutable shared input per case; variants reference that exact input and never receive a per-variant seed.

- [ ] **Step 2: Leave structured facts/relations and all behavior gold pending**

The human reviewer fills JobFacts-derived skills, relation inputs, expected topic, relevance, follow-up and difficulty labels after reviewing the common source evidence.

### Task 3: Create 30 mapping candidate cases

**Files:**
- Create: `evaluation/annotation_templates/project-skill-mapping-cases.draft.jsonl`

**Interfaces:**
- Produces 20 positive-candidate and 10 no-relation-candidate inputs across calibration and reserved-frozen splits.
- Produces `expected_relations: null` for every case.

- [ ] **Step 1: Draft inputs only**

Every JSONL row supplies candidate project evidence, JD requirement evidence, retrieval context, source references, and `case_kind`. No expected mapping, relation type, precision label, or no-relation gold may be generated.

### Task 4: Write the annotation guide

**Files:**
- Create: `evaluation/annotation_templates/ANNOTATION_GUIDE.md`

**Interfaces:**
- Defines all human-entered fields, scoring rules, split discipline, review workflow, recommended counts, and conversion preconditions.

- [ ] **Step 1: Document scoring and frozen discipline**

Require independent first-pass annotation, second-review reconciliation, and only then explicit conversion of the reserved draft into a runnable frozen dataset. The guide must say that no lock is created automatically.
