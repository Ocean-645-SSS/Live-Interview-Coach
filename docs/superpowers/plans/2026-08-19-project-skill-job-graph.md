# Project–Skill–Job Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有异步面试准备链路中，用候选人知识库的 LightRAG `mix` 检索生成可审计的“JD 技能 → 简历项目/职责”关系，并用这些关系选择和个性化改写面试题。

**Architecture:** 保持候选人 KB 与 JD KB 的物理隔离，先沿用现有 `JobProfile` 得到 JD 技能，再对候选人 KB 发起一次合并后的 `mix` 查询。新增 `ProjectSkillMapper` 将图谱检索上下文、`CandidateFacts.projects` 和 `JobProfile` 转成严格结构化关系；`InterviewPlanner` 把关系冻结到对应题目并交给现有 LLM 个性化改写。只接入前端当前使用的异步 `interview_preparation` 工作流，不新增数据库表、不修改实时 Agent 和报告生成。

**Tech Stack:** Python 3.10+、Pydantic、LightRAG 1.4.16、OpenAI-compatible AsyncOpenAI、pytest/pytest-asyncio。

## 实施收敛（2026-08-19）

以下约束取代本文件后续 Task 1–4 中任何冲突的接口或流程描述：

- 关系模型命名为 `ProjectSkillRelation`，只保留 `skill_id`、`project_name`、`responsibility`、`jd_evidence`、`candidate_evidence`、`source_refs` 和可选 `relation_type`。
- `ProjectSkillMapper` 不拥有检索能力。`interview_preparation` 在 `PLAN_GENERATION` 阶段先用所有 `JobProfile` skills 组成一次 consolidated query，对候选人 KB 调用一次 `retrieve_raw_context(..., mode="mix")`；Mapper 仅消费该原始上下文并进行一次结构化 LLM 映射。
- 原始检索走 `/query/data`，RAG Core 以 `aquery_data(... only_need_context=True)` 返回 chunks 与来源，禁止把 LightRAG 的答案生成结果再交给 Mapper。
- `CandidateFacts`、`CandidateProfile`、`JobFacts`、`JobProfile` 的职责与原工作流不变；普通资料检索默认 `naive`。候选人与 JD KB 保持物理隔离。
- 关系仅冻结到命中同一 JD skill 的 `PROJECT_DEEP_DIVE`、`SYSTEM_DESIGN`、`SCENARIO` 题目；纯知识题保持原题。关系由既有 `InterviewPlan` 的 `plan_json` 持久化，不新增表、迁移、Repository 或 API。

## Global Constraints

- 只修改异步准备链路；`create_prepared_interview()` 同步兼容入口保持原行为。
- 每次准备最多新增一次候选人 KB `mix` 查询和一次映射 LLM 调用，禁止按技能 N+1 查询。
- 不合并候选人 KB 与 JD KB；跨库关系在应用层构建并冻结到 `InterviewPlan`。
- 关系必须同时保存 JD 原文依据、候选人项目、职责和候选人来源引用；没有证据的技能允许无映射。
- 外部 LLM 返回未知 `skill_id` 或未知项目名时快速失败，不静默修复或回退到猜测结果。
- 不新增数据库迁移；现有 `plan_json` 持久化完整关系。

---

## File Structure

- Modify `liverag/interview/schemas.py`: 定义项目—技能关系并把它冻结到题目。
- Modify `liverag/interview/application/profile_service.py`: 让知识上下文接口显式接受 `naive`/`mix`。
- Modify `liverag/api/interview_profile_source.py`: 把选择的查询模式传给 RAG Core。
- Create `liverag/interview/application/project_skill_mapper.py`: 一次 `mix` 检索 + 一次 LLM 结构化映射。
- Modify `liverag/interview/application/planner.py`: 按题目命中的 JD 技能挂载项目关系并提供给个性化改写。
- Modify `liverag/interview/prompts/plan_prompts.py`: 要求题目和追问使用已验证关系。
- Modify `liverag/interview/jobs/tasks.py`: 在异步 `PLAN_GENERATION` 前构建关系。
- Create `tests/interview/test_project_skill_mapper.py`: 覆盖检索模式、结构化输出和边界校验。
- Modify `tests/interview/test_job_profile_extractor.py`: 覆盖 Planner 挂载关系。
- Modify `tests/interview/test_background_jobs.py`: 覆盖异步准备接线和计划持久化。

---

### Task 1: Add the auditable relation model and retrieval-mode boundary

**Files:**
- Modify: `liverag/interview/schemas.py`
- Modify: `liverag/interview/application/profile_service.py`
- Modify: `liverag/api/interview_profile_source.py`
- Test: `tests/interview/test_project_skill_mapper.py`

**Interfaces:**
- Produces: `ProjectSkillMatch`
- Produces: `KnowledgeContextSource.retrieve(*, kb_id: str, query: str, mode: Literal["naive", "mix"] = "naive") -> KnowledgeContext`
- Produces: `InterviewQuestion.project_skill_matches: list[ProjectSkillMatch]`

- [ ] **Step 1: Write the failing retrieval-mode test**

```python
@pytest.mark.asyncio
async def test_profile_source_forwards_mix_mode() -> None:
    gateway = MagicMock()
    gateway.post_json = AsyncMock(
        return_value=GatewayResponse(
            status_code=200,
            body={
                "status": "ok",
                "data": {"context": "Redis 项目证据", "references": []},
            },
        )
    )
    source = RagGatewayProfileSource(gateway)

    result = await source.retrieve(kb_id="candidate-kb", query="Redis", mode="mix")

    assert result.context == "Redis 项目证据"
    assert gateway.post_json.await_args.kwargs["payload"]["mode"] == "mix"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest tests/interview/test_project_skill_mapper.py::test_profile_source_forwards_mix_mode -q`

Expected: FAIL because `retrieve()` does not accept `mode`.

- [ ] **Step 3: Add the relation model to `schemas.py`**

Add immediately after `JobProfile`:

```python
class ProjectSkillMatch(StrictModel):
    """一条有双向原文依据的“岗位技能由候选人项目证明”关系。"""

    skill_id: NonEmptyText
    requirement: Literal["required", "preferred"]
    job_evidence: NonEmptyText
    project_name: NonEmptyText
    project_role: str = ""
    responsibility: NonEmptyText
    project_technologies: list[NonEmptyText] = Field(default_factory=list)
    candidate_evidence_refs: list[NonEmptyText] = Field(default_factory=list)
```

Add to `InterviewQuestion` beside `job_skill_matches`:

```python
project_skill_matches: list[ProjectSkillMatch] = Field(default_factory=list)
```

- [ ] **Step 4: Extend the retrieval protocol and adapter**

In `profile_service.py`, import `Literal` and change the protocol to:

```python
async def retrieve(
    self,
    *,
    kb_id: str,
    query: str,
    mode: Literal["naive", "mix"] = "naive",
) -> KnowledgeContext: ...
```

Make the same signature change in `RagGatewayProfileSource.retrieve()` and replace its hard-coded payload entry with:

```python
"mode": mode,
```

Existing callers omit `mode`, so resume/JD profile extraction remains `naive`.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/interview/test_project_skill_mapper.py::test_profile_source_forwards_mix_mode tests/interview/test_job_profile_extractor.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add liverag/interview/schemas.py liverag/interview/application/profile_service.py liverag/api/interview_profile_source.py tests/interview/test_project_skill_mapper.py
git commit -m "feat: model auditable project skill relations"
```

---

### Task 2: Build one-shot graph-backed project–skill mapping

**Files:**
- Create: `liverag/interview/application/project_skill_mapper.py`
- Test: `tests/interview/test_project_skill_mapper.py`

**Interfaces:**
- Consumes: `CandidateFacts`, `JobProfile`, `KnowledgeContextSource`, `AsyncOpenAI`
- Produces: `ProjectSkillMapper.build(candidate_facts: CandidateFacts, job_profile: JobProfile) -> list[ProjectSkillMatch]`

- [ ] **Step 1: Write the failing happy-path test**

```python
@pytest.mark.asyncio
async def test_mapper_uses_one_mix_query_and_returns_evidenced_relation() -> None:
    source = MagicMock()
    source.retrieve = AsyncMock(
        return_value=KnowledgeContext(
            context="推荐系统中负责 Redis 缓存和一致性处理",
            evidence_refs=("resume.pdf",),
        )
    )
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_completion(
            {
                "matches": [
                    {
                        "skill_id": "skill_redis",
                        "requirement": "required",
                        "job_evidence": "熟悉 Redis 缓存设计",
                        "project_name": "推荐系统",
                        "project_role": "后端开发",
                        "responsibility": "负责 Redis 缓存和一致性处理",
                        "project_technologies": ["Redis"],
                        "candidate_evidence_refs": ["resume.pdf"],
                    }
                ]
            }
        )
    )
    mapper = ProjectSkillMapper(
        profile_source=source,
        llm_client=client,
        llm_model="test-model",
    )

    matches = await mapper.build(
        candidate_facts=CandidateFacts(
            kb_id="candidate-kb",
            projects=[
                ProjectFact(
                    name="推荐系统",
                    role="后端开发",
                    description="负责 Redis 缓存和一致性处理",
                    technologies=["Redis"],
                )
            ],
            raw_evidence_refs=["resume.pdf"],
        ),
        job_profile=JobProfile(
            kb_id="jd-kb",
            role="后端工程师",
            required_skills=[
                JobSkill(
                    skill_id="skill_redis",
                    weight=1.0,
                    evidence="熟悉 Redis 缓存设计",
                )
            ],
        ),
    )

    assert len(matches) == 1
    assert matches[0].project_name == "推荐系统"
    source.retrieve.assert_awaited_once()
    assert source.retrieve.await_args.kwargs["mode"] == "mix"
```

- [ ] **Step 2: Write boundary tests**

Add one test where the LLM returns an unknown `skill_id`, and one where it returns an unknown `project_name`. Both calls must raise `ValueError`; no fallback result is accepted.

```python
with pytest.raises(ValueError, match="未知岗位技能"):
    await mapper.build(candidate_facts=candidate_facts, job_profile=job_profile)
```

```python
with pytest.raises(ValueError, match="未知候选人项目"):
    await mapper.build(candidate_facts=candidate_facts, job_profile=job_profile)
```

- [ ] **Step 3: Run the tests and verify they fail**

Run: `python -m pytest tests/interview/test_project_skill_mapper.py -q`

Expected: FAIL because `ProjectSkillMapper` does not exist.

- [ ] **Step 4: Implement the strict output envelope and mapper**

Create `project_skill_mapper.py` with these public types and method:

```python
from __future__ import annotations

import json

from openai import AsyncOpenAI
from pydantic import Field

from liverag.interview.application.profile_service import KnowledgeContextSource
from liverag.interview.schemas import (
    CandidateFacts,
    JobProfile,
    ProjectSkillMatch,
    StrictModel,
)


class ProjectSkillMappingOutput(StrictModel):
    matches: list[ProjectSkillMatch] = Field(default_factory=list)


class ProjectSkillMapper:
    def __init__(
        self,
        *,
        profile_source: KnowledgeContextSource,
        llm_client: AsyncOpenAI,
        llm_model: str,
    ) -> None:
        self._profile_source = profile_source
        self._llm_client = llm_client
        self._llm_model = llm_model

    async def build(
        self,
        *,
        candidate_facts: CandidateFacts,
        job_profile: JobProfile,
    ) -> list[ProjectSkillMatch]:
        job_skills = [
            {"requirement": requirement, **skill.model_dump(mode="json")}
            for requirement, skills in (
                ("required", job_profile.required_skills),
                ("preferred", job_profile.preferred_skills),
            )
            for skill in skills
        ]
        if not job_skills or not candidate_facts.projects:
            return []

        query = (
            "根据候选人简历知识图谱，找出下列岗位技能分别在哪些具体项目和职责中得到明确证明。"
            "只使用简历原文有依据的关系：\n"
            + json.dumps(job_skills, ensure_ascii=False)
        )
        knowledge = await self._profile_source.retrieve(
            kb_id=candidate_facts.kb_id,
            query=query,
            mode="mix",
        )
        response = await self._llm_client.chat.completions.create(
            model=self._llm_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "把岗位技能与候选人项目建立有证据的关系。"
                        "只可使用给定 skill_id 和项目名；无明确证据的技能不要输出。"
                        "输出 JSON：{\"matches\": [...]}。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "job_skills": job_skills,
                            "candidate_projects": [
                                item.model_dump(mode="json")
                                for item in candidate_facts.projects
                            ],
                            "candidate_evidence_refs": candidate_facts.raw_evidence_refs,
                            "graph_context": knowledge.context,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        output = ProjectSkillMappingOutput.model_validate_json(
            response.choices[0].message.content or "{}"
        )
        allowed_skill_ids = {item["skill_id"] for item in job_skills}
        allowed_project_names = {item.name for item in candidate_facts.projects}
        for match in output.matches:
            if match.skill_id not in allowed_skill_ids:
                raise ValueError(f"项目技能映射包含未知岗位技能：{match.skill_id}")
            if match.project_name not in allowed_project_names:
                raise ValueError(f"项目技能映射包含未知候选人项目：{match.project_name}")
        return output.matches
```

The concrete test helper `_completion(payload)` must return a mock whose `choices[0].message.content` is `json.dumps(payload, ensure_ascii=False)`.

- [ ] **Step 5: Run mapper tests**

Run: `python -m pytest tests/interview/test_project_skill_mapper.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add liverag/interview/application/project_skill_mapper.py tests/interview/test_project_skill_mapper.py
git commit -m "feat: map job skills to resume projects with graph retrieval"
```

---

### Task 3: Freeze relations into questions and use them for personalization

**Files:**
- Modify: `liverag/interview/application/planner.py`
- Modify: `liverag/interview/prompts/plan_prompts.py`
- Test: `tests/interview/test_job_profile_extractor.py`

**Interfaces:**
- Consumes: `InterviewPlanner.build(..., project_skill_matches: Sequence[ProjectSkillMatch] = ())`
- Produces: selected questions whose `project_skill_matches` only contain relations for that question's `job_skill_matches`

- [ ] **Step 1: Write the failing Planner test**

Extend `test_planner_records_job_skill_evidence_on_selected_question` with a matching `ProjectSkillMatch`, pass it to `planner.build()`, and assert:

```python
assert plan.questions[0].project_skill_matches == [project_match]
assert plan.questions[0].project_skill_matches[0].responsibility == "负责缓存一致性"
```

Add a second non-Redis relation and assert it is not attached to the Redis question.

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest tests/interview/test_job_profile_extractor.py::test_planner_records_job_skill_evidence_on_selected_question -q`

Expected: FAIL because `build()` does not accept `project_skill_matches`.

- [ ] **Step 3: Extend Planner input and question annotation**

Add this keyword argument to `InterviewPlanner.build()`:

```python
project_skill_matches: Sequence[ProjectSkillMatch] = (),
```

After `_annotate_job_skill_matches()`, call a new method:

```python
questions = self._annotate_project_skill_matches(questions, project_skill_matches)
```

Implement it as a direct skill-ID join:

```python
@staticmethod
def _annotate_project_skill_matches(
    questions: list[InterviewQuestion],
    matches: Sequence[ProjectSkillMatch],
) -> list[InterviewQuestion]:
    matches_by_skill: dict[str, list[ProjectSkillMatch]] = {}
    for match in matches:
        matches_by_skill.setdefault(match.skill_id, []).append(match)
    return [
        question.model_copy(
            update={
                "project_skill_matches": [
                    match
                    for skill in question.job_skill_matches
                    for match in matches_by_skill.get(skill.skill_id, [])
                ]
            }
        )
        for question in questions
    ]
```

- [ ] **Step 4: Include only per-question relations in LLM personalization**

Add to each `questions_input` item:

```python
"project_skill_matches": [
    item.model_dump(mode="json") for item in q.project_skill_matches
],
```

Update `PLAN_PERSONALIZATION_SYSTEM_PROMPT` with this rule:

```text
- 如果题目携带 project_skill_matches，question_text 必须点名其中一个项目或职责，follow_up_hints 必须至少包含一个围绕该职责真实性、技术决策或结果的追问。
- 不得把其他题目的 project_skill_matches 用到当前题目。
```

Keep `project_skill_matches` in the prompt's immutable-field list so the LLM cannot alter audited evidence.

- [ ] **Step 5: Run Planner and prompt tests**

Run: `python -m pytest tests/interview/test_job_profile_extractor.py tests/interview/test_planner.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add liverag/interview/application/planner.py liverag/interview/prompts/plan_prompts.py tests/interview/test_job_profile_extractor.py
git commit -m "feat: drive interview questions from project skill relations"
```

---

### Task 4: Wire mapping into the production async preparation workflow

**Files:**
- Modify: `liverag/interview/jobs/tasks.py`
- Modify: `tests/interview/test_background_jobs.py`

**Interfaces:**
- Consumes: `ProjectSkillMapper.build()` from Task 2
- Produces: persisted `InterviewPlan.questions[*].project_skill_matches`

- [ ] **Step 1: Write the failing workflow test**

In the existing successful `interview_preparation_task` test, make the fake RAG source distinguish calls by `mode`; return project evidence for `mode="mix"`. Make the fake LLM return a mapping envelope when its system message starts with `把岗位技能与候选人项目建立有证据的关系`.

After the job completes, load the persisted plan and assert:

```python
assert any(question.project_skill_matches for question in saved_plan.questions)
assert profile_source.retrieve.await_args_list[-1].kwargs["mode"] == "mix"
assert result["stage_results"]["plan_generation"]["project_skill_match_count"] == 1
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest tests/interview/test_background_jobs.py -k "interview_preparation and project_skill" -q`

Expected: FAIL because the workflow does not construct a mapper or pass relations to Planner.

- [ ] **Step 3: Construct the mapper once per preparation job**

Beside the existing `planner = InterviewPlanner(...)` construction, add:

```python
project_skill_mapper = ProjectSkillMapper(
    profile_source=profile_source,
    llm_client=llm_client,
    llm_model=llm_model,
)
```

- [ ] **Step 4: Build and pass relations inside `PLAN_GENERATION`**

Immediately before `planner.build()`:

```python
if candidate_facts is None:
    raise RuntimeError("PLAN_GENERATION 需要 CandidateFacts 构建项目技能关系")
project_skill_matches = await project_skill_mapper.build(
    candidate_facts=candidate_facts,
    job_profile=job_profile,
)
```

Pass `project_skill_matches=project_skill_matches` to `planner.build()`. When writing the existing `plan_generation` stage result, add:

```python
"project_skill_match_count": len(project_skill_matches),
```

- [ ] **Step 5: Run focused workflow tests**

Run: `python -m pytest tests/interview/test_background_jobs.py -k "interview_preparation" -q`

Expected: PASS.

- [ ] **Step 6: Run the complete relevant regression suite**

Run: `python -m pytest tests/interview/test_project_skill_mapper.py tests/interview/test_job_profile_extractor.py tests/interview/test_planner.py tests/interview/test_background_jobs.py -q`

Expected: PASS.

Run: `python -m ruff check liverag/interview/schemas.py liverag/interview/application/profile_service.py liverag/api/interview_profile_source.py liverag/interview/application/project_skill_mapper.py liverag/interview/application/planner.py liverag/interview/prompts/plan_prompts.py liverag/interview/jobs/tasks.py tests/interview/test_project_skill_mapper.py tests/interview/test_job_profile_extractor.py tests/interview/test_background_jobs.py`

Expected: PASS with no diagnostics.

- [ ] **Step 7: Commit**

```bash
git add liverag/interview/jobs/tasks.py tests/interview/test_background_jobs.py
git commit -m "feat: persist graph-backed project skill interview plans"
```

---

## Acceptance Criteria

- A preparation run makes exactly one candidate-KB query with `mode="mix"` for project–skill mapping.
- A returned relation can only reference a `JobProfile` skill and a `CandidateFacts` project.
- Every persisted relation contains JD evidence, project responsibility and candidate source references.
- Questions only receive relations whose `skill_id` matches their existing `job_skill_matches`.
- Personalized questions name the evidenced project/responsibility when a relation exists.
- No relation is fabricated for an unsupported JD skill; an empty mapping is valid.
- Real-time interview and report generation contain no new LightRAG calls.
- Existing naive resume/JD extraction remains unchanged.

## Self-Review

- Spec coverage: the plan covers graph retrieval, cross-domain relation construction, auditable persistence, question selection linkage, personalized wording, and async production wiring.
- Scope control: no database table, migration, cross-KB merge, frontend change, real-time RAG, or report change is included.
- Type consistency: `ProjectSkillMatch` is produced by `ProjectSkillMapper`, consumed by `InterviewPlanner.build`, and persisted on `InterviewQuestion.project_skill_matches`.
- Placeholder scan: no placeholder or unspecified implementation step remains.
