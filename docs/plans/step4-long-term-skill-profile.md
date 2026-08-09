# 第四步：长期能力画像 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于跨场面试的持久化 `AnswerEvaluation` 构建可解释、可追溯、可幂等重建的长期 `SkillProgress`，并让它确定性地影响下一次 `InterviewPlan`。

**Architecture:** PostgreSQL/SQLite 继续保存权威业务状态；候选人身份以固定个人资料库为稳定聚合根，题目分类通过版本化两级技能 taxonomy 映射为唯一 `skill_key`。`SkillProgressService` 只消费已持久化评价，通过证据明细表、数据库唯一约束和基于 Evidence 的全量重算保证幂等聚合，避免重复评价请求、任务重试或画像重建导致重复累计；Planner 把岗位相关题占比作为硬约束，把弱项复测、证据补充和已掌握抽查作为可与岗位相关性重叠的软训练意图。画像更新和报告后的画像对账都是 best-effort 派生操作，失败不能阻塞实时面试或报告核心结果；Redis 不保存画像结果。

**Tech Stack:** Python 3.10+、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、PostgreSQL/SQLite、pytest；Next.js 15、React 19、TypeScript 5、Playwright。

## Global Constraints

- 以 `docs/plans/interview-coach-plan.md` 当前工作区中的第四步要求为范围基线。
- 不引入用户、租户或账号系统；当前产品仍是单用户/受控演示环境。
- 不把 transcript、`InterviewReport` 或 LLM 自由总结作为长期能力画像的写入来源。
- `SkillProgress` 的唯一事实来源是已经写入 `answer_evaluations` 的 `AnswerEvaluation`。
- `SkillProgress` 是可由历史评价重建的派生状态；画像更新失败不能回滚 `AnswerEvaluation`、阻塞实时状态机或把已成功生成的 `InterviewReport` 改为失败。
- 同一 `evaluation_id` 被接口重试、任务重试或 rebuild 重复处理时不得重复累计；本计划只承诺基于 Evidence 的幂等处理，不宣称分布式 exactly-once。
- taxonomy、分数、时间衰减、置信度、薄弱点合并和 Planner 调权全部由确定性代码完成，LLM 不生成最终能力分或主观置信度。
- Redis 只承担第三步已有队列与短期锁；画像、证据和重建结果全部持久化到 SQL 数据库。
- 保留 `InterviewPlan.candidate_profile` / `job_profile` 的场次快照；第四步新增的是稳定聚合身份和训练策略审计信息，不用可变画像覆盖历史计划。
- 同一实现必须在 SQLite 和 PostgreSQL 上通过；不按数据库类型分叉业务规则。
- 保留现有同步 `/api/interviews/prepared` 与异步 `interview_preparation` 两条准备路径，两条路径都必须读取长期画像。
- 实时 LiveKit 音频、STT/TTS、状态机和逐轮事件不进入 Redis round-trip。
- 不增加对 Kafka、Kubernetes、向量数据库或新的 Agent 框架的依赖。
- 后端与 `LiveRAG-Fronted/agent-starter-react` 是两个 Git 仓库；分别提交，不能在后端仓库把整个 `LiveRAG-Fronted/` 当作新目录加入。

---

## 0. 开工基线与最近进展

### 0.1 第二步现状

第二步已经具备第四步所需的数据库基础：

- Alembic baseline：`alembic/versions/975f61b782e0_initial_interview_schema.py`
- Background Job 迁移：`alembic/versions/75e3f27927f0_add_background_jobs_table.py`
- SQLAlchemy Repository 同时支持 SQLite/PostgreSQL。
- `interviews.version` 与 `interview_sessions.version` 已使用条件更新实现乐观锁。
- Compose 已提供 PostgreSQL 健康检查和持久化卷。

### 0.2 第三步与 2026-08-06 至 2026-08-09 提交

| 提交 | 进展 | 第四步可复用能力 |
|---|---|---|
| `d7d3eb6` | Job 表、Redis Queue、Worker、异步 API、19 个初始后台任务测试 | 可复用持久化 Job 与 Worker 对账机制 |
| `9129df0` | 简历解析、五阶段 preparation、profile/planner 接线、大量后台任务测试 | 可在 `CANDIDATE_PROFILE` 阶段绑定稳定候选人聚合根 |
| `0394342` | Planner 重构、同步/异步准备修复、plan prompt | 可把长期画像作为 Planner 的显式可选输入 |
| `eae6d64` | Interview Intelligence、Nowcoder Spider、MCP Server | 与长期画像保持输入边界，不直接写 `SkillProgress` |
| `e95178b` | Intelligence 聚合/缓存/提取、settings、Planner 集成 | 已形成“可选增强失败不阻断基础计划”的模式 |
| `6cfa72c` | API/架构/数据流文档、Job 队列和 Worker 微调 | 最新 API 与任务语义已写入文档和测试 |

当前分支 `feature/interview` 与 `origin/feature/interview` 同步到 `6cfa72c`。当前工作区对第二、三步文档有未提交的“已完成”状态更新；实现第四步时不得覆盖这些修改。

### 0.3 当前代码缺口

1. `CandidateProfile` 只是 `InterviewPlan` 中的本场快照，没有可供跨场聚合的稳定数据库 ID。
2. `AnswerEvaluationModel` 已保存 `id`、`rubric_version`、`created_at`，但 Repository 只返回 `AnswerEvaluation` JSON，无法建立 `source_evaluation_ids` 和时间趋势。
3. `InterviewQuestion.category/subcategory` 已具备两级分类雏形，但没有版本化 taxonomy、稳定 `skill_key` 和别名规则。
4. `InterviewPlanner.build()` 当前只接收 Candidate/Job/Company 三类画像，尚未接收历史技能状态。
5. `/profile` 当前是个人简历知识库页面；长期画像必须使用独立 `/progress` 页面，不能替换简历管理入口。

### 0.4 第四步完成链路

```整条链：

单题回答
 ↓
AnswerEvaluation
 ↓
SkillProgressEvidence
 ↓
SkillProgress
 ↓
下一场 Planner
 ↓
更针对性的 InterviewPlan
 ↓
新的回答
 ↓
新的 AnswerEvaluation
 ↓
继续更新 SkillProgress
```

---

## 1. 锁定的数据与算法契约

### 1.1 候选人聚合身份

新增 `candidate_profiles` 表作为稳定聚合根：

```text
id                    candidate_profile_<sha256(normalized kb_id)[:32]>
kb_id                 当前个人资料库 ID；唯一
latest_profile_json   最近一次 CandidateProfile 快照，可空
created_at
updated_at
```

`candidate_profile_id` 不是一次 LLM 画像生成的随机 ID，而是由个人资料库 `kb_id` 确定。当前默认个人资料库始终映射到同一个 ID；未来即使更新简历内容，也延续同一训练历史，同时每场 `InterviewPlan.candidate_profile` 仍冻结当时快照。

确定性 ID：

```python
def candidate_profile_id_for_kb(kb_id: str) -> str:
    normalized = " ".join(kb_id.strip().casefold().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"candidate_profile_{digest}"
```

### 1.2 两级技能 taxonomy

作用：告诉系统这道题的评价应该归到哪个SkillProgress
题目的主技能只由 `category + subcategory` 决定；`topics` 继续用于检索和选题细粒度匹配，但不让一次评价重复累计到多个技能。

```text
Level 1: category
Level 2: subcategory；为空时使用固定显示名“通用”
```

稳定键不直接依赖展示文案：

```python
parent_key = "domain_" + sha256(normalized_category)[:12]
skill_key = "skill_" + sha256(normalized_category + "\0" + normalized_subcategory)[:16]
```

```链路
Question
  ↓
category + subcategory
  ↓
taxonomy
  ↓
skill_key
  ↓
SkillProgressEvidence
  ↓
SkillProgress
```

初始 `skill_taxonomy.v1.json` 由当前运行时 `question_bank.v1.json` 与已评审 `question_bank.v2.reviewed.json` 的分类并集确定性生成并提交。以后改展示名必须在 taxonomy 文件中追加 alias，不得修改既有 `skill_key`。

### 1.3 有效评价与 attempts

- `answer_evaluations.id` 是唯一评价证据 ID。
- 每个 `answer_id` 现有唯一约束保证最多一个有效评价。
- 每条有效评价映射到一个 `skill_key`，计为一次 `attempt`。
- 澄清后的新 answer 拥有新的 `answer_id`，因此作为新证据计数；API 重试、报告重试和画像重建不会重复计数。
- 评价版本通过 `rubric_version` 保留在证据中，不覆盖历史评价。

### 1.4 分数计算

对某候选人的某技能全部证据按 `evaluated_at` 升序排列：

```python
attempts = len(evidence)
average_score = round(sum(item.score for item in evidence) / attempts, 2)
latest_score = evidence[-1].score

latest_at = evidence[-1].evaluated_at
weights = [
    0.5 ** ((latest_at - item.evaluated_at).total_seconds() / 86400 / 90)
    for item in evidence
]
current_score = round(
    sum(item.score * weight for item, weight in zip(evidence, weights, strict=True))
    / sum(weights),
    2,
)
```

90 天是 V1 固定半衰期。以最近评价时间为相对原点，使结果不会仅因读取时间变化而漂移；有新评价时才重新计算。

### 1.5 confidence 计算

```python
volume = min(1.0, attempts / 5)
session_coverage = min(1.0, distinct_session_count / 3)
temporal_spread = min(1.0, evaluation_span_days / 90) if attempts > 1 else 0.0
consistency = (
    0.5
    if attempts == 1
    else max(0.0, 1.0 - statistics.pstdev(scores) / 25)
)
confidence = round(
    0.35 * volume
    + 0.25 * session_coverage
    + 0.15 * temporal_spread
    + 0.25 * consistency,
    4,
)
```

confidence 是规则结果，不是概率或 LLM 自评。API 显示为 0–1，前端可格式化为百分比。

### 1.6 weak_points 规则

输入只取 `AnswerEvaluation.missing_points + AnswerEvaluation.errors`：

1. Unicode NFKC 规范化。
2. 去首尾空白和末尾 `，。；;,.`。
3. 连续空白折叠为一个空格。
4. `casefold()` 结果作为去重键，首次出现的规范化文本作为展示值。
5. 按出现次数降序、最后出现时间降序、规范化键升序排序。
6. 每个技能最多保留 5 个 `WeakPointAggregate`。
7. 每个弱点返回 `count`、`latest_at` 和完整 `source_evaluation_ids`，保证可追溯。

### 1.7 Planner 岗位硬约束与训练软目标

仅在存在历史画像时启用；没有画像时完全复用当前选题行为。

```text
弱项：current_score < 60 且 confidence >= 0.65
证据不足：confidence < 0.45
已掌握：current_score >= 80 且 confidence >= 0.65
```

对 `question_count = N`，岗位相关性与训练意图是两个正交维度：

```text
selection_intent = WEAK_RETEST | EVIDENCE_GAP | MASTERY_AUDIT | BASELINE
job_relevant = true | false
```

因此一道题可以同时是 `selection_intent=WEAK_RETEST` 和 `job_relevant=true`，但只能有一个主训练意图。

规则固定为：

1. `minimum_job_core = ceil(N * 0.50)` 是硬约束；候选池中岗位相关题足够时，最终选择必须至少包含该数量。
2. 弱项复测目标为 `min(可用弱项题数, max(1, floor(N * 0.30)))`；证据补充目标最多 1 题；`N >= 5` 时已掌握抽查目标最多 1 题。这三个都是软目标，不能挤占满足岗位硬约束所需的容量。
3. 每个训练意图都先从与岗位相关题的交集中选：`WEAK_RETEST ∩ JOB_CORE`、`EVIDENCE_GAP ∩ JOB_CORE`、`MASTERY_AUDIT ∩ JOB_CORE`。
4. 设 `effective_job_core_target = min(minimum_job_core, available_job_core_count)`，非岗位题预算固定为 `N - effective_job_core_target`；只有预算仍有余额时，软训练意图才能选择非岗位题。
5. 完成软目标选择后，继续从未选择的岗位相关候选题补齐到 `effective_job_core_target`，再用当前 topic weight、难度距离和 seed 稳定排序补满 N 题。
6. 如果没有非空 `job_labels`，记录 `JOB_CORE_LABELS_UNAVAILABLE`；如果岗位相关候选题少于 `minimum_job_core`，记录 `INSUFFICIENT_JOB_CORE_QUESTIONS`，并保存 requested/available/selected 数量。两种情况均在 `TrainingAdjustmentAudit` 中标记 degraded，不伪造 50% 已满足。
7. 同一道题只能出现一次。软目标没有足够题目时记录目标数和实际数，不使计划失败；岗位硬约束只有在输入信号或题库不足时才允许降级。

难度目标：弱项低于 50 分时优先当前配置难度的低一档；50–59.99 优先当前难度；证据补充和岗位核心使用当前难度；已掌握抽查优先高一档。超出 BEGINNER/EXPERT 边界时截断到边界。

---

## 2. 文件结构

### 2.1 后端新增

| 文件 | 单一职责 |
|---|---|
| `liverag/interview/skill_progress/__init__.py` | 导出长期画像公共类型 |
| `liverag/interview/skill_progress/taxonomy.py` | 加载、校验并解析版本化两级 taxonomy |
| `liverag/interview/skill_progress/taxonomy_builder.py` | 从题库分类并集确定性生成初始 taxonomy |
| `liverag/interview/skill_progress/policy.py` | 分数、衰减、confidence、weak_points 的纯函数 |
| `liverag/interview/skill_progress/service.py` | 消费持久化评价、幂等更新、查询、重建、训练建议 |
| `liverag/interview/skill_progress/curriculum.py` | 把历史画像转换为 Planner 选题意图和配比 |
| `liverag/interview/skill_progress/cli.py` | 对已有评价执行可重复的全量重建 |
| `liverag/interview/skill_progress/data/skill_taxonomy.v1.json` | 已提交的稳定 taxonomy 数据 |
| `alembic/versions/4a1d9c7e2b6f_add_long_term_skill_progress.py` | 候选人根、画像、证据表和历史 Interview 绑定迁移 |
| `tests/interview/test_skill_taxonomy.py` | taxonomy/题库全覆盖测试 |
| `tests/interview/test_skill_progress_policy.py` | 纯聚合算法测试 |
| `tests/interview/test_skill_progress_service.py` | 跨场、隔离、幂等、重建测试 |
| `tests/api/test_skill_progress_routes.py` | 查询 API 契约测试 |

### 2.2 后端修改

| 文件 | 修改责任 |
|---|---|
| `liverag/interview/schemas.py` | 新增画像、证据、趋势、训练审计 Pydantic 模型；`InterviewPlan` 增加可选审计字段 |
| `liverag/interview/records.py` | 新增 CandidateProfile/SkillProgress/Evaluation metadata 记录和确定性 ID helper |
| `liverag/interview/persistence/models.py` | 新增 3 张表，`interviews` 增加 `candidate_profile_id` 外键 |
| `liverag/interview/persistence/repository.py` | 扩展身份、评价 metadata、画像读写协议 |
| `liverag/interview/persistence/sqlalchemy_repository.py` | 实现事务、行锁、证据唯一约束和聚合读模型 |
| `liverag/interview/application/service.py` | 评价后 best-effort 更新画像；同步准备读取画像；报告完成后 best-effort 对账 |
| `liverag/interview/application/planner.py` | 接收历史画像并使用 curriculum 选题，写训练审计 |
| `liverag/interview/question_bank/catalog.py` | 新增按训练意图确定性分桶选题能力 |
| `liverag/interview/jobs/tasks.py` | 异步准备读取画像；报告完成后 best-effort 重建候选人画像 |
| `liverag/interview/jobs/worker.py` | 把 `SkillProgressService` 注入 task context |
| `liverag/interview/jobs/worker_main.py` | 创建 taxonomy/service 并注入 Worker |
| `liverag/api/interview_routes.py` | 增加画像总览、技能详情和趋势 API |
| `liverag/api/server.py` | 创建并注入 `SkillProgressService` |
| `pyproject.toml` | 增加 `liverag-rebuild-skill-progress` CLI entry point |
| `tests/interview/test_models.py` | 新表、关系、约束测试 |
| `tests/interview/test_sqlalchemy_repository.py` | metadata 查询、并发幂等、候选人隔离测试 |
| `tests/interview/test_planner.py` | 四类训练意图和无历史回归测试 |
| `tests/interview/test_background_jobs.py` | preparation 读取画像、report 对账幂等测试 |
| `tests/interview/test_schema_management.py` | SQLite/PostgreSQL migration head 与 backfill 测试 |

### 2.3 前端新增/修改

| 文件 | 责任 |
|---|---|
| `LiveRAG-Fronted/agent-starter-react/app/progress/page.tsx` | 长期能力画像路由 |
| `LiveRAG-Fronted/agent-starter-react/components/interview/skill-progress-dashboard.tsx` | 总览、趋势、弱点、来源和训练建议 UI |
| `LiveRAG-Fronted/agent-starter-react/components/interview/skill-trend-chart.tsx` | 无额外图表依赖的 SVG 趋势图 |
| `LiveRAG-Fronted/agent-starter-react/tests/progress.spec.ts` | Playwright 页面流程 |
| `LiveRAG-Fronted/agent-starter-react/playwright.config.ts` | E2E 配置 |
| `LiveRAG-Fronted/agent-starter-react/types/interview.ts` | SkillProgress API 类型 |
| `LiveRAG-Fronted/agent-starter-react/components/interview/interview-report.tsx` | 增加“查看长期能力画像”入口 |
| `LiveRAG-Fronted/agent-starter-react/components/interview/interview-create.tsx` | 创建页增加历史训练状态入口 |
| `LiveRAG-Fronted/agent-starter-react/package.json` | 增加 `test:e2e` 和 `@playwright/test` |

---

### Task 1: 建立版本化技能 taxonomy

**Files:**
- Create: `liverag/interview/skill_progress/__init__.py`
- Create: `liverag/interview/skill_progress/taxonomy.py`
- Create: `liverag/interview/skill_progress/taxonomy_builder.py`
- Create: `liverag/interview/skill_progress/data/skill_taxonomy.v1.json`
- Create: `tests/interview/test_skill_taxonomy.py`

**Interfaces:**
- Consumes: `InterviewQuestion.category`, `InterviewQuestion.subcategory`，两个现有题库 JSON。
- Produces: `SkillTaxonomy.from_file(path)`, `SkillTaxonomy.resolve(category, subcategory) -> SkillDefinition`, `SkillDefinition.key/parent_key/display_name`。

- [x] **Step 1: 写 taxonomy schema 和解析失败测试**

```python
def test_taxonomy_resolves_alias_to_stable_skill_key(taxonomy_path):
    taxonomy = SkillTaxonomy.from_file(taxonomy_path)
    direct = taxonomy.resolve("RAG", "文档切块")
    alias = taxonomy.resolve("rag", " 文档切块 ")
    assert alias.key == direct.key
    assert direct.parent_key.startswith("domain_")


def test_taxonomy_rejects_duplicate_aliases_across_skills(tmp_path):
    path = write_taxonomy_with_duplicate_alias(tmp_path)
    with pytest.raises(SkillTaxonomyError, match="alias"):
        SkillTaxonomy.from_file(path)
```

- [x] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/interview/test_skill_taxonomy.py -q`

Expected: FAIL，提示 `liverag.interview.skill_progress.taxonomy` 不存在。

- [x] **Step 3: 实现模型、规范化和稳定键**

```python
class SkillDefinition(StrictModel):

class SkillTaxonomyDocument(StrictModel):

class SkillTaxonomy:
```
`resolve()` 对未知分类抛 `SkillNotMappedError`；这属于题库/计划契约错误，不静默生成新技能。

- [x] **Step 4: 实现初始 taxonomy builder 并生成数据**

Builder 读取两个 `QuestionBankDocument`，将空 subcategory 固定为“通用”，按规范化 `(category, subcategory)` 排序，使用 1.2 节哈希公式生成 key。

Run:
```powershell
uv run python -m liverag.interview.skill_progress.taxonomy_builder `
  --input liverag/interview/question_bank/data/question_bank.v1.json `
  --input liverag/interview/question_bank/data/question_bank.v2.reviewed.json `
  --output liverag/interview/skill_progress/data/skill_taxonomy.v1.json
```

Expected: 输出技能数量和 `skill_taxonomy.v1.json` SHA-256；同样输入重复执行得到相同文件哈希。

- [x] **Step 5: 增加两个真实题库的全覆盖测试**

```python
@pytest.mark.parametrize("filename", ["question_bank.v1.json", "question_bank.v2.reviewed.json"])
def test_every_packaged_question_maps_to_skill(filename):(filename):
```
为此给 `QuestionBank` 增加只读 `questions: tuple[InterviewQuestion, ...]` property；不暴露可变内部列表。

- [x] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/interview/test_skill_taxonomy.py tests/interview/question_bank/test_catalog.py -q`

Expected: PASS。

Commit: `feat(interview): add versioned skill taxonomy`

---

### Task 2: 增加候选人聚合根、画像表和评价证据表

**Files:**
- Modify: `liverag/interview/schemas.py`
- Modify: `liverag/interview/records.py`
- Modify: `liverag/interview/persistence/models.py`
- Create: `alembic/versions/4a1d9c7e2b6f_add_long_term_skill_progress.py`
- Modify: `tests/interview/test_models.py`
- Modify: `tests/interview/test_schema_management.py`

**Interfaces:**
- Consumes: `InterviewConfig.candidate_kb_id`, `AnswerEvaluationModel.id/created_at`。
- Produces: `CandidateProfileRecord`, `AnswerEvaluationRecord`, `SkillProgress`, `SkillProgressEvidence`, `TrainingAdjustmentAudit` 和 3 张新表。

- [ ] **Step 1: 写领域 schema 测试**

```python
def test_skill_progress_requires_traceable_sources():
    progress = SkillProgress(
        candidate_profile_id="candidate_profile_abc",
        skill_key="skill_python",
        taxonomy_version=1,
        attempts=2,
        average_score=70,
        current_score=75,
        latest_score=80,
        confidence=0.55,
        weak_points=[],
        source_evaluation_ids=["evaluation_1", "evaluation_2"],
        first_evaluated_at=NOW,
        last_evaluated_at=NOW,
        updated_at=NOW,
    )
    assert progress.attempts == len(progress.source_evaluation_ids)
```

在 `SkillProgress` model validator 中要求 source ID 不重复且数量等于 attempts。

- [ ] **Step 2: 新增 Pydantic/record 类型**

```python
class WeakPointAggregate(StrictModel):
    text: NonEmptyText
    count: PositiveInt
    latest_at: datetime
    source_evaluation_ids: list[NonEmptyText] = Field(min_length=1)


class SkillProgress(StrictModel):
    candidate_profile_id: NonEmptyText
    skill_key: NonEmptyText
    taxonomy_version: PositiveInt
    attempts: PositiveInt
    average_score: float = Field(ge=0, le=100)
    current_score: float = Field(ge=0, le=100)
    latest_score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    weak_points: list[WeakPointAggregate] = Field(max_length=5)
    source_evaluation_ids: list[NonEmptyText] = Field(min_length=1)
    first_evaluated_at: datetime
    last_evaluated_at: datetime
    updated_at: datetime


class TrainingAdjustmentAudit(StrictModel):
    taxonomy_version: PositiveInt
    source_progress_updated_at: datetime | None = None
    weak_retest_skills: list[str] = Field(default_factory=list)
    evidence_skills: list[str] = Field(default_factory=list)
    mastery_audit_skills: list[str] = Field(default_factory=list)
    selection_intents: dict[str, str] = Field(default_factory=dict)
    job_relevant_by_question: dict[str, bool] = Field(default_factory=dict)
    intent_targets: dict[str, int] = Field(default_factory=dict)
    intent_selected: dict[str, int] = Field(default_factory=dict)
    job_core_required: int = Field(ge=0)
    job_core_available: int = Field(ge=0)
    job_core_selected: int = Field(ge=0)
    degraded: bool = False
    degradation_reasons: list[str] = Field(default_factory=list)
```

给 `InterviewPlan` 增加：

```python
candidate_profile_id: str | None = None
training_adjustment: TrainingAdjustmentAudit | None = None
```

两个字段保持可选，确保历史 `plan_json` 可以继续反序列化。

- [ ] **Step 3: 写 ORM 约束测试**

覆盖：

```text
candidate_profiles.kb_id 唯一
skill_progress(candidate_profile_id, skill_key) 唯一
skill_progress_evidence(candidate_profile_id, skill_key, evaluation_id) 唯一
attempts >= 1
0 <= average/current/latest_score <= 100
0 <= confidence <= 1
interviews.candidate_profile_id 外键存在
```

- [ ] **Step 4: 实现 ORM 模型**

新增 `CandidateProfileModel`、`SkillProgressModel` 和 `SkillProgressEvidenceModel`；三者都使用现有 `Base`、UTC aware timestamp helper 和显式关系名。

`SkillProgressEvidenceModel` 至少保存：

```text
id, candidate_profile_id, skill_key, evaluation_id,
session_id, question_id, taxonomy_version, rubric_version,
score, weak_points_json, evaluated_at, created_at
```

`SkillProgressModel` 至少保存：

```text
candidate_profile_id, skill_key, taxonomy_version, attempts,
average_score, current_score, latest_score, confidence,
weak_points_json, source_evaluation_ids_json,
first_evaluated_at, last_evaluated_at, updated_at
```

- [ ] **Step 5: 编写 Alembic 迁移与历史 Interview backfill**

迁移顺序固定为：

1. 建 `candidate_profiles`。
2. 给 `interviews` 增加可空 `candidate_profile_id`。
3. Python 读取每条 `config_json`，以 `candidate_kb_id`（缺失时使用历史默认值 `default`）计算确定性 ID并插入候选人根。
4. 回填每条 Interview。
5. 将列改为 non-null，加外键和索引。
6. 建 `skill_progress` 与 `skill_progress_evidence`。

Downgrade 逐个撤销上述对象，不删除任何用户文件或目录。

- [ ] **Step 6: 验证空库升级与历史 backfill**

Run:

```powershell
uv run alembic upgrade head
uv run pytest tests/interview/test_models.py tests/interview/test_schema_management.py -q
```

Expected: SQLite 空库和包含旧 Interview fixture 的数据库均升级成功；旧 Interview 的 `candidate_profile_id` 与其 config 一致。

- [ ] **Step 7: 提交**

Commit: `feat(interview): add persistent skill progress schema`

---

### Task 3: 扩展 Repository 的评价元数据与基于 Evidence 的幂等画像写入

**Files:**
- Modify: `liverag/interview/persistence/repository.py`
- Modify: `liverag/interview/persistence/sqlalchemy_repository.py`
- Modify: `liverag/interview/records.py`
- Modify: `tests/interview/test_sqlalchemy_repository.py`

**Interfaces:**
- Consumes: Task 2 ORM/records。
- Produces: 候选人身份、评价 metadata、画像查询和基于 Evidence 唯一约束的幂等写入方法。

- [ ] **Step 1: 写 Repository 合同测试**

```python
def test_evaluation_record_exposes_id_version_and_time(repository, evaluated_answer):
    record = repository.get_evaluation_record(evaluated_answer.id)
    assert record.id.startswith("evaluation_")
    assert record.rubric_version >= 1
    assert record.created_at
    assert record.evaluation.answer_id == evaluated_answer.id


def test_duplicate_evaluation_does_not_duplicate_skill_progress(repository, evidence):
    first = repository.apply_skill_evidence(evidence)
    second = repository.apply_skill_evidence(evidence)
    assert second == first
    assert second.attempts == 1
    assert second.source_evaluation_ids == [evidence.evaluation_id]
```

- [ ] **Step 2: 扩展协议**

协议增加以下精确签名：

- `ensure_candidate_profile(*, kb_id: str) -> CandidateProfileRecord`
- `update_candidate_profile_snapshot(*, candidate_profile_id: str, profile: CandidateProfile) -> CandidateProfileRecord`
- `get_candidate_profile(candidate_profile_id: str) -> CandidateProfileRecord`
- `get_candidate_profile_by_kb(kb_id: str) -> CandidateProfileRecord`
- `get_evaluation_record(answer_id: str) -> AnswerEvaluationRecord`
- `list_evaluation_records_for_candidate(candidate_profile_id: str) -> list[AnswerEvaluationRecord]`
- `apply_skill_evidence(evidence: SkillProgressEvidence) -> SkillProgress`
- `replace_skill_progress(*, candidate_profile_id: str, progress: list[SkillProgress], evidence: list[SkillProgressEvidence]) -> list[SkillProgress]`
- `list_skill_progress(candidate_profile_id: str) -> list[SkillProgress]`
- `list_skill_evidence(*, candidate_profile_id: str, skill_key: str) -> list[SkillProgressEvidence]`

- [ ] **Step 3: 在 `create_interview()` 事务内绑定候选人根**

`create_interview(title, config)` 先按 `config.candidate_kb_id` ensure 候选人根，再写 `InterviewModel.candidate_profile_id`。返回的 `InterviewRecord` 增加该字段。

- [ ] **Step 4: 实现评价 metadata join**

`list_evaluation_records_for_candidate()` 连接：

```text
candidate_profiles
  -> interviews
  -> interview_sessions
  -> interview_answers
  -> answer_evaluations
```

按 `AnswerEvaluationModel.created_at, AnswerEvaluationModel.id` 稳定排序，并返回 session/interview/question/evaluation ID、rubric version 和时间。

- [ ] **Step 5: 实现并发安全的 `apply_skill_evidence()`**

事务顺序：

1. ensure `skill_progress` 聚合行；并发首次创建由唯一约束裁决。
2. 使用 SQLAlchemy `select(SkillProgressModel).with_for_update()` 锁定聚合行；SQLite 写事务自然串行。
3. 插入 evidence；唯一键已存在时直接返回当前聚合。
4. 查询该候选人/技能的全部 evidence。
5. 调 Task 4 的纯 `calculate_skill_progress()` 计算完整快照。
6. 更新聚合行并提交。

不得通过 `attempts = attempts + 1` 猜测状态；每次都从权威 evidence 明细重算，使重试和并发结果一致。

- [ ] **Step 6: 实现全量替换事务**

`replace_skill_progress()` 只删除指定 `candidate_profile_id` 的画像/证据后写回重建结果，并在一个事务内完成。禁止无 candidate 条件的全表删除。

- [ ] **Step 7: 运行测试并提交**

Run: `uv run pytest tests/interview/test_sqlalchemy_repository.py -q`

Expected: 跨场查询、候选人隔离、重复 evidence、两个并发 evaluation fixture 全部 PASS。

Commit: `feat(interview): persist traceable skill evidence`

---

### Task 4: 实现确定性 SkillProgressPolicy 与 SkillProgressService

**目标：** 使用 Task 3 暴露的持久化评价元数据构造 `SkillProgressEvidence`，并通过纯函数聚合、幂等增量应用和全量重建提供统一的长期画像业务入口。

**核心要求：** 同一 `evaluation_id` 对同一候选人和技能只能形成一条 Evidence；重复评价请求、任务重试或 rebuild 不增加 attempts；所有分数、confidence 和 weak points 都从数据库中的全部有效 Evidence 重新计算。

**Files:**
- Create: `liverag/interview/skill_progress/policy.py`
- Create: `liverag/interview/skill_progress/service.py`
- Create: `liverag/interview/skill_progress/cli.py`
- Modify: `pyproject.toml`
- Create: `tests/interview/test_skill_progress_policy.py`
- Create: `tests/interview/test_skill_progress_service.py`

**Interfaces:**
- Consumes: Task 1 taxonomy、Task 3 Repository、持久化评价与冻结计划题目。
- Produces: `calculate_skill_progress()`, `SkillProgressService.apply_evaluation()`, `rebuild_candidate()`, `get_dashboard_for_kb()`, `recommend_questions()`。

- [ ] **Step 1: 用固定样本写算法测试**

```python
def test_policy_calculates_average_decay_latest_and_confidence():
    evidence = [
        item("evaluation_old", score=40, day=0, session="s1"),
        item("evaluation_new", score=80, day=90, session="s2"),
    ]
    progress = calculate_skill_progress(evidence, taxonomy_version=1)
    assert progress.average_score == 60.0
    assert progress.current_score == 66.67
    assert progress.latest_score == 80.0
    assert progress.attempts == 2
    assert progress.source_evaluation_ids == ["evaluation_old", "evaluation_new"]
```

另测：单样本 confidence、多 session、同日评价、分数方差、5 条 top weak points、NFKC/大小写去重和稳定排序。

- [ ] **Step 2: 实现纯策略函数**

实现精确接口 `calculate_skill_progress(evidence: Sequence[SkillProgressEvidence], *, taxonomy_version: int) -> SkillProgress` 与 `normalize_weak_point(value: str) -> tuple[str, str]`。

函数严格使用第 1.4–1.6 节公式，不读数据库、不读当前时间、不调用 LLM。

- [ ] **Step 3: 写 Service 跨场与隔离测试**

```python
def test_two_interviews_update_one_candidate_skill(service, candidate_fixture):
    service.apply_evaluation("answer_1")
    progress = service.apply_evaluation("answer_2")
    assert progress.attempts == 2
    assert progress.source_evaluation_ids == ["evaluation_1", "evaluation_2"]


def test_candidates_never_share_progress(service, two_candidates_fixture):
    service.apply_evaluation("answer_candidate_a")
    assert service.list_progress("candidate_b") == []
```

- [ ] **Step 4: 实现 `apply_evaluation()`**

流程固定为：

```text
answer_id
 -> get_evaluation_record(answer_id)
 -> get session/interview/frozen InterviewPlan
 -> locate question by question_id
 -> taxonomy.resolve(category, subcategory)
 -> build SkillProgressEvidence from evaluation metadata
 -> repository.apply_skill_evidence(evidence)
```

`weak_points` 只取 `missing_points + errors`；不读取 transcript/report。

- [ ] **Step 5: 实现重建**

```python
def rebuild_candidate(self, candidate_profile_id: str) -> list[SkillProgress]:
    records = self._repository.list_evaluation_records_for_candidate(candidate_profile_id)
    evidence = [self._to_evidence(record) for record in records]
    grouped = group_by_skill(evidence)
    progress = [
        calculate_skill_progress(items, taxonomy_version=self.taxonomy.version)
        for _, items in sorted(grouped.items())
    ]
    return self._repository.replace_skill_progress(
        candidate_profile_id=candidate_profile_id,
        progress=progress,
        evidence=evidence,
    )
```

同一批评价重复重建必须得到相同 JSON 结果。

- [ ] **Step 6: 实现训练建议**

`recommend_questions(candidate_profile_id, question_bank, limit=5)` 按以下键排序技能：

```text
弱项优先：current_score 升序
证据不足其次：confidence 升序
同分：skill_key 升序
```

每个技能取一道未重复题目，返回题目 ID、题干、难度、技能显示名和推荐理由枚举 `WEAK_RETEST | EVIDENCE_GAP`。

- [ ] **Step 7: 增加 CLI**

`pyproject.toml`：

```toml
liverag-rebuild-skill-progress = "liverag.interview.skill_progress.cli:main"
```

Run: `uv run liverag-rebuild-skill-progress --candidate-kb-id default --dry-run`

Expected: 输出评价数、技能数和将写入的 source evaluation 数；`--dry-run` 不写数据库。正式命令去掉 `--dry-run`。

- [ ] **Step 8: 运行测试并提交**

Run: `uv run pytest tests/interview/test_skill_progress_policy.py tests/interview/test_skill_progress_service.py -q`

Expected: PASS。

Commit: `feat(interview): aggregate deterministic long term skill progress`

---

### Task 5: 把画像更新接入评价和报告对账

**核心要求：**

- `AnswerEvaluation` 和 `InterviewReport` 是核心业务事实，`SkillProgress` 是可重建派生状态。
- 画像应用失败不得回滚已提交评价，不得阻止 follow-up、next-question 或实时状态迁移。
- 报告生成与持久化成功后即保持 `COMPLETED`；后续画像对账失败只能记录错误。
- 评价应用、报告后对账和 CLI rebuild 都只读取 `AnswerEvaluation`，报告内容不是画像输入。

**Files:**
- Modify: `liverag/interview/application/service.py`
- Modify: `liverag/interview/application/evaluator.py`
- Modify: `liverag/interview/jobs/tasks.py`
- Modify: `liverag/interview/jobs/worker.py`
- Modify: `liverag/interview/jobs/worker_main.py`
- Modify: `liverag/api/server.py`
- Modify: `tests/interview/test_services.py`
- Modify: `tests/interview/test_background_jobs.py`

**Interfaces:**
- Consumes: `SkillProgressService.apply_evaluation/rebuild_candidate`。
- Produces: 评价后的 best-effort 派生更新 + 报告完成后的 best-effort 对账；两者失败都不改变核心业务结果。

- [ ] **Step 1: 写评价重试幂等测试**

扩展现有 `test_evaluation_retry_reuses_saved_result_and_transition`：

```python
first = await interview_service.evaluate_answer(answer.id)
second = await interview_service.evaluate_answer(answer.id)
progress = skill_progress_service.list_progress(candidate_profile_id)
assert second.evaluation == first.evaluation
assert progress[0].attempts == 1
```

- [ ] **Step 2: 写画像失败不阻塞实时评价的测试**

```python
@pytest.mark.asyncio
async def test_skill_progress_failure_does_not_block_evaluation_or_transition(
    interview_service,
    answer,
    repository,
    skill_progress_service,
    real_skill_progress_service,
    candidate_profile_id,
):
    skill_progress_service.apply_evaluation.side_effect = RuntimeError("progress unavailable")

    result = await interview_service.evaluate_answer(answer.id)

    assert result.evaluation.answer_id == answer.id
    assert repository.get_evaluation(answer.id) == result.evaluation
    assert result.decision.event_type is InterviewEventType.NEXT_QUESTION
    assert result.transitions

    recovered = real_skill_progress_service.rebuild_candidate(candidate_profile_id)
    assert recovered[0].source_evaluation_ids == [repository.get_evaluation_record(answer.id).id]
```

该 fixture 固定 evaluator 返回 `NEXT_QUESTION`；另用现有 follow-up fixture 断言 `FOLLOW_UP_REQUIRED` 同样不受画像失败影响。

- [ ] **Step 3: 注入 `SkillProgressService`**

`InterviewService.__init__()` 增加：

```python
skill_progress_service: SkillProgressService | None = None
```

生产 `server.py` 必须传入；测试可显式传 fake 或 `None`。如果不传，保持第一至三步的旧用例可独立运行。

- [ ] **Step 4: 在评价提交后 best-effort 应用画像**

在 `evaluate_answer()` 取得新评价或复用旧评价后、计算 follow-up decision 前执行，但不得和 `save_evaluation()` 共用一个会因画像错误而回滚的事务：

```python
if self.skill_progress_service is not None:
    try:
        self.skill_progress_service.apply_evaluation(answer_id)
    except Exception:
        logger.exception(
            "interview.skill_progress.apply_failed",
            extra={"answer_id": answer_id},
        )

decision = self.decide_after_evaluation(evaluation)
```

重复调用由 Evidence 唯一约束处理。这里只隔离可重建派生状态的失败；`decide_after_evaluation()` 和状态机自身的错误继续按现有语义抛出。

- [ ] **Step 5: 写报告对账成功与失败测试**

正常对账验证：

```text
已有 3 条 evaluation、画像只含 2 条
 -> report_generation
 -> complete_report
 -> rebuild_candidate
 -> 画像包含 3 条
```

失败隔离测试：

```python
def test_report_remains_completed_when_skill_progress_rebuild_fails(
    interview_service,
    completed_session,
    repository,
    skill_progress_service,
    real_skill_progress_service,
    candidate_profile_id,
):
    skill_progress_service.rebuild_candidate.side_effect = RuntimeError("rebuild unavailable")

    report = interview_service.generate_report(completed_session.id)

    assert report.state is ReportState.COMPLETED
    assert repository.get_report_by_session(completed_session.id).state is ReportState.COMPLETED

    recovered = real_skill_progress_service.rebuild_candidate(candidate_profile_id)
    assert recovered
```

`tests/interview/test_background_jobs.py` 使用同一失败 fake 调 `report_generation_task()`，断言 Job 返回成功报告 ID、数据库 Report 仍为 `COMPLETED`。再次对账或 CLI rebuild 后 attempts 与 Evidence 数一致。

- [ ] **Step 6: 在同步与异步报告完成后 best-effort 对账**

`InterviewService.generate_report()` 和 `jobs/tasks.py::report_generation_task()` 的顺序固定为：

1. 只用持久化 `AnswerEvaluation` 构建报告内容。
2. 调 `complete_report()` 提交报告核心业务状态。
3. 在报告核心 `try/except` 之外或独立嵌套 `try/except` 中读取 Session/Interview、解析 `candidate_profile_id` 并调用 `rebuild_candidate()`；身份查询也属于可失败的派生对账步骤。
4. 对账失败记录 `interview.skill_progress.reconcile_failed` 和 `session_id/candidate_profile_id`，返回已完成报告，不调用 `fail_report()`。

参考结构：

```python
content = builder.build(session_id)
completed = interview_repo.complete_report(report_id=report.id, content=content)

if skill_progress_service is not None:
    candidate_profile_id: str | None = None
    try:
        session = interview_repo.get_session(session_id)
        interview = interview_repo.get_interview(session.interview_id)
        candidate_profile_id = interview.candidate_profile_id
        skill_progress_service.rebuild_candidate(candidate_profile_id)
    except Exception:
        logger.exception(
            "interview.skill_progress.reconcile_failed",
            extra={
                "session_id": session_id,
                "candidate_profile_id": candidate_profile_id,
            },
        )

return completed
```

已有 `COMPLETED` 报告的短路路径也执行同一个 best-effort reconciliation helper 后再返回，使后续显式重试或同一候选人的下一次报告任务可以修复画像。同步 Service 返回 `completed` record；异步 task 在对账后继续返回当前 `{report_id, session_id, state: "COMPLETED"}` 结构。报告只作为对账触发器，不作为画像数据源。

- [ ] **Step 7: Worker 注入 service**

`BackgroundWorker` 构造参数增加 `skill_progress_service`，`_task_context()` 使用固定 key `skill_progress_service`。`worker_main.py` 从同一 taxonomy 文件创建实例。

- [ ] **Step 8: 运行测试并提交**

Run: `uv run pytest tests/interview/test_services.py tests/interview/test_background_jobs.py -q`

Expected: 评价/报告正常路径通过；画像应用失败不阻塞实时状态迁移；画像对账失败不改变 Report `COMPLETED`；评价重试、报告重试、Worker 重启和后续 rebuild 均不会重复累计。

Commit: `feat(interview): update skill progress from persisted evaluations`

---

### Task 6: 让长期画像确定性影响下一次 InterviewPlan

**Files:**
- Create: `liverag/interview/skill_progress/curriculum.py`
- Modify: `liverag/interview/question_bank/catalog.py`
- Modify: `liverag/interview/application/planner.py`
- Modify: `liverag/interview/application/service.py`
- Modify: `liverag/interview/jobs/tasks.py`
- Modify: `tests/interview/test_planner.py`
- Modify: `tests/interview/test_background_jobs.py`

**Interfaces:**
- Consumes: `Sequence[SkillProgress]`, taxonomy 和现有 Candidate/Job/Company 画像。
- Produces: `TrainingSelectionRequest`, `QuestionBank.select_training_questions()`, `InterviewPlan.training_adjustment`。

- [ ] **Step 1: 写 curriculum 分类和配比测试**

```python
def test_curriculum_builds_soft_targets_and_hard_job_minimum():
    request = TrainingCurriculum(taxonomy).build(
        question_count=5,
        progress=[
            progress("skill_weak", score=45, confidence=0.8),
            progress("skill_unknown", score=70, confidence=0.2),
            progress("skill_mastered", score=90, confidence=0.8),
        ],
        job_labels=["Python"],
        job_constraint_enabled=True,
    )
    assert request.weak_target == 1
    assert request.evidence_target == 1
    assert request.mastery_target == 1
    assert request.minimum_job_core == 3
```

另测 `question_count=1..30` 的 `minimum_job_core == ceil(N * 0.50)`；没有 `JobProfile` 时 `job_constraint_enabled=False`、`minimum_job_core=0`，不将通用训练错误标记为岗位约束降级。

- [ ] **Step 2: 实现 curriculum 纯策略**

```python
@dataclass(frozen=True, slots=True)
class TrainingSelectionRequest:
    weak_skill_keys: tuple[str, ...]
    evidence_skill_keys: tuple[str, ...]
    mastery_skill_keys: tuple[str, ...]
    job_labels: tuple[str, ...]
    weak_target: int
    evidence_target: int
    mastery_target: int
    minimum_job_core: int
    job_constraint_enabled: bool


@dataclass(frozen=True, slots=True)
class TrainingSelectionResult:
    questions: tuple[InterviewQuestion, ...]
    selection_intents: dict[str, str]
    job_relevant_by_question: dict[str, bool]
    intent_targets: dict[str, int]
    intent_selected: dict[str, int]
    job_core_required: int
    job_core_available: int
    job_core_selected: int
    degradation_reasons: tuple[str, ...]
```

`TrainingCurriculum` 从 `JobProfile.role + JobProfile.required_skills` 构造去空、去重后的 `job_labels`。存在 `JobProfile` 时启用岗位硬约束；不存在 `JobProfile` 时该约束不适用。严格实现 1.7 节阈值、软目标和难度规则。

`TrainingSelectionResult.job_core_required` 始终保存原始硬目标 `minimum_job_core`，`job_core_available` 保存过滤后候选池中的岗位题数量，`job_core_selected` 保存最终入选数量；`TrainingAdjustmentAudit.degraded` 固定由 `bool(degradation_reasons)` 计算。

- [ ] **Step 3: 为岗位硬约束和训练软目标写选题测试**

至少覆盖以下测试：

`question_bank` fixture 固定包含 3 道 Python 岗位题（其中 `q-weak-python` 同时属于弱项技能）、2 道 `RareJobSkill` 岗位题，以及 3 道分别对应 weak/evidence/mastery 的非岗位题；`sufficiently_large_question_bank` 为每个支持题量提供至少 30 道岗位题和 30 道非岗位题。

```python
def test_job_core_hard_minimum_limits_non_job_soft_targets(question_bank, taxonomy):
    result = question_bank.select_training_questions(
        config=InterviewConfig(question_count=5),
        training=TrainingSelectionRequest(
            weak_skill_keys=("skill_weak_non_job",),
            evidence_skill_keys=("skill_evidence_non_job",),
            mastery_skill_keys=("skill_mastery_non_job",),
            job_labels=("Python",),
            weak_target=1,
            evidence_target=1,
            mastery_target=1,
            minimum_job_core=3,
            job_constraint_enabled=True,
        ),
        taxonomy=taxonomy,
        relevance_text=None,
        explicitly_requested_topics=(),
        selection_seed="plan-1",
    )
    assert result.job_core_selected >= 3
    assert sum(not value for value in result.job_relevant_by_question.values()) <= 2
    assert sum(result.intent_selected.values()) <= 5


def test_training_intent_can_also_be_job_relevant(question_bank, taxonomy):
    result = question_bank.select_training_questions(
        config=InterviewConfig(question_count=5),
        training=TrainingSelectionRequest(
            weak_skill_keys=("skill_python_weak",),
            evidence_skill_keys=(),
            mastery_skill_keys=(),
            job_labels=("Python",),
            weak_target=1,
            evidence_target=0,
            mastery_target=0,
            minimum_job_core=3,
            job_constraint_enabled=True,
        ),
        taxonomy=taxonomy,
        relevance_text=None,
        explicitly_requested_topics=(),
        selection_seed="plan-2",
    )
    assert result.selection_intents["q-weak-python"] == "WEAK_RETEST"
    assert result.job_relevant_by_question["q-weak-python"] is True


@pytest.mark.parametrize("question_count", range(1, 31))
def test_job_core_minimum_holds_for_every_supported_question_count(
    question_count,
    sufficiently_large_question_bank,
    taxonomy,
):
    result = sufficiently_large_question_bank.select_training_questions(
        config=InterviewConfig(question_count=question_count),
        training=TrainingSelectionRequest(
            weak_skill_keys=(),
            evidence_skill_keys=(),
            mastery_skill_keys=(),
            job_labels=("Python",),
            weak_target=0,
            evidence_target=0,
            mastery_target=0,
            minimum_job_core=math.ceil(question_count * 0.50),
            job_constraint_enabled=True,
        ),
        taxonomy=taxonomy,
        relevance_text=None,
        explicitly_requested_topics=(),
        selection_seed=f"plan-{question_count}",
    )
    assert result.job_core_selected >= math.ceil(question_count * 0.50)
    assert result.degradation_reasons == ()


def test_insufficient_job_questions_is_explicitly_degraded(question_bank, taxonomy):
    result = question_bank.select_training_questions(
        config=InterviewConfig(question_count=5),
        training=TrainingSelectionRequest(
            weak_skill_keys=(),
            evidence_skill_keys=(),
            mastery_skill_keys=(),
            job_labels=("RareJobSkill",),
            weak_target=0,
            evidence_target=0,
            mastery_target=0,
            minimum_job_core=3,
            job_constraint_enabled=True,
        ),
        taxonomy=taxonomy,
        relevance_text=None,
        explicitly_requested_topics=(),
        selection_seed="plan-degraded",
    )
    assert result.job_core_required == 3
    assert result.job_core_available == 2
    assert result.job_core_selected == 2
    assert "INSUFFICIENT_JOB_CORE_QUESTIONS" in result.degradation_reasons


def test_missing_job_labels_is_explicitly_degraded(question_bank, taxonomy):
    result = question_bank.select_training_questions(
        config=InterviewConfig(question_count=5),
        training=TrainingSelectionRequest(
            weak_skill_keys=(),
            evidence_skill_keys=(),
            mastery_skill_keys=(),
            job_labels=(),
            weak_target=0,
            evidence_target=0,
            mastery_target=0,
            minimum_job_core=3,
            job_constraint_enabled=True,
        ),
        taxonomy=taxonomy,
        relevance_text=None,
        explicitly_requested_topics=(),
        selection_seed="plan-no-labels",
    )
    assert result.job_core_required == 3
    assert result.job_core_available == 0
    assert "JOB_CORE_LABELS_UNAVAILABLE" in result.degradation_reasons
```

同一测试模块继续覆盖无可用弱项题、题目去重、seed 可复现和软目标不足时的 `intent_targets/intent_selected` 审计值。

- [ ] **Step 4: 实现保留岗位容量的 `select_training_questions()`**

接口固定为 `select_training_questions(config: InterviewConfig, *, training: TrainingSelectionRequest, taxonomy: SkillTaxonomy, relevance_text: str | None, explicitly_requested_topics: Iterable[str], selection_seed: str) -> TrainingSelectionResult`。岗位相关性的唯一输入是 `training.job_labels`；不再同时传入第二份可能漂移的 required relevance labels。

算法顺序固定为：

```text
1. 应用现有可选题类型和 `_specific_terms_are_supported()` 基础过滤，得到不重复候选池；候选数少于 N 时继续抛现有 `QuestionBankError`。
2. 不把现有 `_matches_required_relevance()` 用作“所有候选题必须岗位相关”的过滤器；改用 `training.job_labels` 计算每题 `job_relevant`，训练意图与该布尔值分开保存。
3. job_constraint_enabled 且 job_labels 为空：记录 JOB_CORE_LABELS_UNAVAILABLE。
4. effective_job_core_target = min(minimum_job_core, available_job_core_count)。
5. non_job_budget = N - effective_job_core_target。
6. 依次处理 WEAK_RETEST、EVIDENCE_GAP、MASTERY_AUDIT：
   a. 先选该意图与 job_relevant 的交集；
   b. 仍未达到软目标时，只在 selected_non_job < non_job_budget 时选非岗位题；
   c. 达不到软目标时只记录实际数，不挤占岗位保留容量。
7. 从剩余 job_relevant 候选补齐 effective_job_core_target。
8. 按现有 topic weight、难度距离和 seed 稳定排序补齐 N。
9. 如果 available_job_core_count < minimum_job_core，记录 INSUFFICIENT_JOB_CORE_QUESTIONS。
10. 返回 TrainingSelectionResult；selection_intent 只允许 WEAK_RETEST、EVIDENCE_GAP、MASTERY_AUDIT、BASELINE。
```

`JOB_CORE` 不再是 `selection_intent`，而是 `job_relevant_by_question[question_id]`。当岗位候选池足够时，步骤 5 的非岗位预算保证任何 N 都不可能因软目标先占位而破坏 50% 硬约束。

- [ ] **Step 5: 扩展 Planner 接口**

`InterviewPlanner.build()` 固定增加 `candidate_profile_id: str | None = None` 与 `skill_progress: Sequence[SkillProgress] = ()` 两个 keyword-only 参数，其余现有参数和返回类型 `InterviewPlan` 不变。

有画像时调用训练选题，并把 `TrainingSelectionResult` 完整映射到 `TrainingAdjustmentAudit`：训练意图、岗位相关布尔值、软目标/实际数、岗位 required/available/selected 数和 degradation reasons 都进入冻结计划。无画像时继续调用现有 `select_questions()`，保证旧测试选题不变。

- [ ] **Step 6: 同步和异步准备读取画像**

- `InterviewService.create_prepared_interview()`：ensure candidate profile、更新最新 CandidateProfile snapshot、读取 progress 后传 Planner。
- `interview_preparation_task()`：在 `CANDIDATE_PROFILE` stage 更新 snapshot；`PLAN_GENERATION` 前读取 progress 后传 Planner。
- 异步路径如果 `TrainingAdjustmentAudit.degradation_reasons` 非空，把原因追加到 preparation 的 `degradation_reasons` 并设置 `degraded=true`；同步路径通过冻结 Plan audit 暴露同样原因。
- `stage_results.plan_generation` 只记录技能数量、selection intent 数量、`job_core_required/available/selected` 和 degradation reasons，不复制完整历史画像进 Job payload。

- [ ] **Step 7: 写闭环测试**

```text
第一次计划无历史画像 -> 旧选题路径
完成面试并保存低分 Python evaluation
第二次同 candidate_profile + 同岗位准备
 -> Plan 包含 Python 弱项复测题
 -> training_adjustment.source_progress_updated_at 非空
 -> selection_intents 记录该题为 WEAK_RETEST
 -> job_relevant_by_question 独立记录该题是否岗位相关
```

另测不同 candidate profile 不影响选题；N=5 且三个软训练目标都是非岗位题时，最终仍至少选择 3 道岗位题，未满足的软目标通过 `intent_targets/intent_selected` 如实审计。

- [ ] **Step 8: 运行测试并提交**

Run: `uv run pytest tests/interview/test_planner.py tests/interview/test_background_jobs.py -q`

Expected: 旧 Planner 测试和新增闭环测试全部 PASS；岗位候选池充足时所有支持题量都满足 `job_core_selected >= ceil(N * 0.50)`，不足时计划明确 degraded 且不伪造满足。

Commit: `feat(interview): adapt interview plans from skill history`

---

### Task 7: 增加长期画像查询 API

**Files:**
- Modify: `liverag/api/interview_routes.py`
- Modify: `liverag/interview/application/service.py`
- Create: `tests/api/test_skill_progress_routes.py`
- Modify: `docs/API.md`

**Interfaces:**
- Consumes: `SkillProgressService.get_dashboard_for_kb()`。
- Produces: 总览和技能详情两个只读 API。

- [ ] **Step 1: 写 API 合同测试**

```python
def test_get_skill_progress_dashboard(client, candidate_profile_id):
    response = client.get("/api/interviews/skill-progress?candidate_kb_id=default")
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_profile_id"] == candidate_profile_id
    assert body["taxonomy_version"] == 1
    assert body["skills"][0]["source_evaluation_ids"] == ["evaluation_1"]


def test_get_skill_detail_contains_trend_and_sources(client):
    response = client.get(
        "/api/interviews/skill-progress/skill_python?candidate_kb_id=default"
    )
    assert response.status_code == 200
    assert response.json()["trend"][0]["evaluation_id"] == "evaluation_1"
```

- [ ] **Step 2: 定义响应模型**

```python
class SkillTrendPoint(StrictModel):
    evaluation_id: str
    session_id: str
    interview_id: str
    question_id: str
    score: float
    rubric_version: int
    evaluated_at: datetime


class SkillProgressDashboard(StrictModel):
    candidate_profile_id: str
    taxonomy_version: int
    skills: list[SkillProgress]
    recommendations: list[TrainingQuestionRecommendation]
```

- [ ] **Step 3: 添加静态前缀路由**

路由：

```text
GET /api/interviews/skill-progress?candidate_kb_id=default
GET /api/interviews/skill-progress/{skill_key}?candidate_kb_id=default
```

必须放在 `GET /api/interviews/{interview_id}` 之前注册，维持 FastAPI 静态/动态路径可读性。`candidate_kb_id` 为空时由 Pydantic/FastAPI 请求边界拒绝；尚无历史 Interview 的合法资料库返回空 dashboard，技能详情不存在时返回 404。

- [ ] **Step 4: 文档化响应和来源语义**

`docs/API.md` 写明：

- `current_score` 是 90 天半衰期相对加权分。
- `confidence` 是规则指标，不是概率。
- trend/source 只来自 `AnswerEvaluation`。
- 返回 404 的 candidate/skill 语义。

- [ ] **Step 5: 运行测试并提交**

Run: `uv run pytest tests/api/test_skill_progress_routes.py tests/api/test_interview_routes.py -q`

Expected: PASS，且现有 `/reports`、`/{interview_id}` 路由无冲突。

Commit: `feat(api): expose traceable skill progress`

---

### Task 8: 实现长期能力画像前端

**Files:**
- Create: `LiveRAG-Fronted/agent-starter-react/app/progress/page.tsx`
- Create: `LiveRAG-Fronted/agent-starter-react/components/interview/skill-progress-dashboard.tsx`
- Create: `LiveRAG-Fronted/agent-starter-react/components/interview/skill-trend-chart.tsx`
- Modify: `LiveRAG-Fronted/agent-starter-react/types/interview.ts`
- Modify: `LiveRAG-Fronted/agent-starter-react/components/interview/interview-report.tsx`
- Modify: `LiveRAG-Fronted/agent-starter-react/components/interview/interview-create.tsx`

**Interfaces:**
- Consumes: Task 7 API，经现有 `apiRequest()` 和 `/api/liverag/*` BFF。
- Produces: `/progress` 页面、技能展开详情和报告/创建页入口。

- [ ] **Step 1: 增加 TypeScript 类型**

```typescript
export interface InterviewRecord {
  id: string;
  candidate_profile_id: string;
  title: string;
  state: InterviewState;
  plan_json: string | null;
  version: number;
}

export interface SkillProgress {
  candidate_profile_id: string;
  skill_key: string;
  taxonomy_version: number;
  attempts: number;
  average_score: number;
  current_score: number;
  latest_score: number;
  confidence: number;
  weak_points: WeakPointAggregate[];
  source_evaluation_ids: string[];
  first_evaluated_at: string;
  last_evaluated_at: string;
  updated_at: string;
}

export interface SkillProgressDashboardResponse {
  candidate_profile_id: string;
  taxonomy_version: number;
  skills: SkillProgress[];
  recommendations: TrainingQuestionRecommendation[];
}
```

- [ ] **Step 2: 实现 `/progress` 数据加载和状态**

当前单用户页面请求 `/api/interviews/skill-progress?candidate_kb_id=default`，由后端返回 `candidate_profile_id`；前端不复制哈希算法。页面需要四种明确状态：加载中、无历史评价、加载失败、正常数据。

- [ ] **Step 3: 实现画像卡片**

每个技能展示：技能名、attempts、average/current/latest、confidence、最近更新时间、最多 5 个 weak points。点击后加载 detail API，显示趋势和每个点的 evaluation/question/session 来源。

- [ ] **Step 4: 实现无额外依赖的 SVG 趋势图**

`skill-trend-chart.tsx` 使用 `<svg viewBox="0 0 600 220">`；Y 轴固定 0–100，X 轴按评价顺序，点的 accessible label 包含日期、分数和评价 ID。只有一个点时居中显示，不绘制虚假趋势线。

- [ ] **Step 5: 实现训练建议区和导航入口**

训练建议显示问题、难度、技能与 `WEAK_RETEST/EVIDENCE_GAP` 中文理由。报告页和创建面试页增加指向 `/progress` 的链接；`/profile` 继续保留个人简历管理。

- [ ] **Step 6: 运行前端静态验证并提交前端仓库**

Run（工作目录 `LiveRAG-Fronted/agent-starter-react`）：

```powershell
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm build
```

Expected: 三个命令退出码均为 0。

Commit（在前端仓库）：`feat(interview): add long term skill progress dashboard`

---

### Task 9: 增加进度页 E2E 与全链路验收

**Files:**
- Create: `LiveRAG-Fronted/agent-starter-react/playwright.config.ts`
- Create: `LiveRAG-Fronted/agent-starter-react/tests/progress.spec.ts`
- Modify: `LiveRAG-Fronted/agent-starter-react/package.json`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/INTERVIEW_COACH_ARCHITECTURE.md`
- Modify: `docs/系统架构与数据流.md`
- Modify: `docs/plans/interview-coach-plan.md`

**Interfaces:**
- Consumes: Tasks 1–8 完整链路。
- Produces: 可重复 E2E、架构文档和第四步完成证据。

- [ ] **Step 1: 配置 Playwright**

按 Playwright 官方当前 pnpm 安装方式添加测试包和 Chromium：

```powershell
corepack pnpm add -D @playwright/test@latest
corepack pnpm exec playwright install chromium
```

`package.json` 增加脚本 `"test:e2e": "playwright test --project=chromium"`；`playwright.config.ts` 只配置 Chromium project、`testDir: "tests"` 和本地 Next.js webServer。测试使用 mock route 返回固定画像，不依赖真实 LLM、LiveKit、Redis 或牛客网络。

- [ ] **Step 2: 写进度页 E2E**

覆盖：

```text
打开 /progress
 -> 看见 current/average/latest/confidence
 -> 展开技能
 -> 看见趋势点和 evaluation source
 -> 看见弱项训练建议
 -> 从建议返回创建面试页
```

另写无历史数据用例，断言页面显示“完成至少一场带有效评价的面试后生成能力画像”。

- [ ] **Step 3: 运行后端完整验证**

Run:

```powershell
uv run ruff check liverag tests
uv run python -m compileall liverag
uv run pytest tests/interview tests/api/test_interview_routes.py tests/api/test_skill_progress_routes.py -q
uv run alembic upgrade head
docker compose config
```

Expected: 全部退出码为 0，Alembic head 唯一。

- [ ] **Step 4: 运行前端完整验证**

Run（工作目录 `LiveRAG-Fronted/agent-starter-react`）：

```powershell
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm build
corepack pnpm exec playwright install chromium
corepack pnpm test:e2e
```

Expected: 全部退出码为 0。

- [ ] **Step 5: 执行闭环验收 fixture**

固定 fixture 验证：

```text
Candidate A 第一次面试 Python = 45
Candidate A 第二次面试 Python = 75
Candidate B 一次面试 Python = 95

结果：
A attempts=2, average=60, latest=75, current=65（按实际时间 fixture 精确断言）
B attempts=1 且 source 中没有 A 的 evaluation
A 下一次 plan 至少一题 intent=WEAK_RETEST
B 下一次 plan 不受 A 的弱项影响
任一 source_evaluation_id 可回查 question/answer/rubric/evaluation
重复报告和重复 rebuild 后结果完全不变
画像即时更新失败时评价和状态迁移仍成功，随后 rebuild 恢复画像
画像对账失败时报告仍为 COMPLETED，随后 rebuild 恢复画像
岗位候选池充足时 job_core_selected >= ceil(question_count * 0.50)
岗位候选池不足时 Plan audit 包含 INSUFFICIENT_JOB_CORE_QUESTIONS 和实际数量
```

- [ ] **Step 6: 更新架构和总体计划状态**

文档必须加入：候选人聚合根、taxonomy、证据表、确定性公式、Planner 配比、API/UI 链路。仅当 Step 3–5 全部通过后，把总体计划第四步状态改为 `✅ 已完成`；第三步文档顶部遗留的 `3.2/3.3 待开始` 同时校正为已完成，保持文档内部一致。

- [ ] **Step 7: 分仓库提交**

后端提交：`docs(interview): document long term training feedback loop`

前端提交：`test(interview): cover skill progress dashboard`

---

## 3. 最终验收标准

- 同一 `candidate_profile_id` 的多场 Interview 形成一个稳定 `SkillProgress` 集合。
- 不同 `candidate_profile_id` 的评价、画像、趋势和 Planner 输入严格隔离。
- 每个 attempts、分数、confidence 和 weak point 都能追溯到具体 `answer_evaluations.id`，并继续回查 Answer、Question、Session、Interview 和 rubric version。
- 同一 `evaluation_id` 因接口重试、任务重试、报告对账或全量 rebuild 被重复处理时不增加 attempts；该保证来自 Evidence 明细、数据库唯一约束和基于全部 Evidence 的重算，仅为幂等语义。
- `AnswerEvaluation` 持久化成功后，即使即时画像应用失败，实时 follow-up/next-question 和状态机仍继续；后续 rebuild 能恢复画像。
- `InterviewReport` 完成后，即使画像 reconciliation 失败，报告仍保持 `COMPLETED` 且可读取；后续显式对账或 CLI rebuild 能恢复画像。
- `average_score`、90 天半衰期 `current_score`、`latest_score` 和 confidence 对固定输入产生固定结果。
- 题库 v1 和 v2 reviewed 的每一道题恰好映射到一个两级 taxonomy 技能。
- 没有历史画像时 Planner 保持第三步行为；有画像时计划审计字段证明历史弱项、证据不足或已掌握技能实际影响选题。
- 岗位相关候选池充足时，任何支持的题量都满足 `job_core_selected >= ceil(question_count * 0.50)`；Weak/Evidence/Mastery 仅是可与岗位相关性重叠的软目标，不能破坏该硬约束。
- 岗位标签缺失或岗位相关候选题不足时，Plan/Preparation audit 明确记录 `JOB_CORE_LABELS_UNAVAILABLE` 或 `INSUFFICIENT_JOB_CORE_QUESTIONS` 及 required/available/selected 数量，不伪造 50% 已满足。
- `/progress` 展示趋势、三类分数、confidence、weak points、评价来源和推荐训练题；`/profile` 继续承担个人简历管理。
- Redis/MCP/Nowcoder 不成为 SkillProgress 的权威数据源，也不进入实时语音主链路。
- SQLite/PostgreSQL migration、后端 tests/ruff/compileall、前端 lint/typecheck/build/E2E 全部通过。

## 4. 非本阶段范围

- 多用户登录、租户隔离和 RBAC。
- GitHub 仓库/PI Agent 代码分析。
- LLM 自动改写 taxonomy 或直接修改长期分数。
- embedding 技能聚类、语义去重或复杂 Bayesian/IRT 能力模型。
- 跨候选人排名、排行榜和社交分享。
- Kafka、流式计算平台或独立画像微服务。
