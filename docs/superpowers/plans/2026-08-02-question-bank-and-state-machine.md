# Interview Question Bank and State Machine Implementation Plan

> **Status:** 本计划中的原生 `InterviewStore` 持久化描述已由 [Interview Repository Cutover Implementation Plan](2026-08-02-interview-repository-cutover.md) 取代；题库和状态机业务规则仍然有效。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 固化结构化题库的转换、补全和构建行为，提供可运行的生成入口，并实现可持久化、可恢复、可幂等的 V1 面试状态机。

**Architecture:** 题库继续使用离线流水线 `converter → enricher → builder → catalog`，真实 LLM 只参与离线补全。状态机负责校验事件和计算新快照，并通过现有 `InterviewStore.record_transition()` 原子保存事件与 Session，不进入题库生成流程。

**Tech Stack:** Python 3.10、Pydantic v2、SQLite、pytest、pytest-asyncio、OpenAI-compatible SDK、Ruff。

## Global Constraints

- Python 标识符使用英文，注释、docstring 和错误信息使用中文。
- 不新增 Redis、PostgreSQL、Kafka、Celery 或多 Agent 框架。
- 不把题库、rubric 或 expected points 写入 RAG。
- 真实模型凭据缺失时禁止把 Fake Provider 结果冒充正式题库。
- 复用现有 `InterviewStore`、Session 版本号、事件幂等键和 SQLite 事务。

---

### Task 1: Markdown 转换测试

**Files:**
- Create: `tests/interview/question_bank/test_converter.py`
- Test: `liverag/interview/question_bank/converter.py`

**Interfaces:**
- Consumes: `QuestionBankMarkdownConverter.convert_file(path: Path) -> QuestionBankConversionResult`
- Produces: 对图片、外链、指导章节、去重、追问关系和真实文档统计的回归保护。

- [x] 构造最小 Markdown，断言图片和 URL 被清除但链接文字保留。
- [x] 构造重复题、指导章节和追问题，断言去重、排除和父题引用。
- [x] 转换 `study_source.md`，断言提取 419 题及已知统计。
- [x] 运行 `pytest tests/interview/question_bank/test_converter.py -q`，预期全部通过。

### Task 2: Catalog 查询测试

**Files:**
- Create: `tests/interview/question_bank/test_catalog.py`
- Test: `liverag/interview/question_bank/catalog.py`

**Interfaces:**
- Consumes: `QuestionBankDocument`、`QuestionBank.from_file()`、`filter_questions()`、`select_for_config()`。
- Produces: JSON 校验、分类过滤、父题查询和确定性选题的回归保护。

- [x] 使用合法主问题和追问题 fixture 构建题库。
- [x] 断言缺失父题被拒绝。
- [x] 断言分类、子分类、主题、难度和题型过滤正确。
- [x] 断言相同配置总是返回相同顺序。
- [x] 运行 `pytest tests/interview/question_bank/test_catalog.py -q`，预期全部通过。

### Task 3: Enricher 测试

**Files:**
- Create: `tests/interview/question_bank/test_enricher.py`
- Test: `liverag/interview/question_bank/enricher.py`

**Interfaces:**
- Consumes: `QuestionEnrichmentProvider`、`QuestionBankEnricher.enrich_draft()`。
- Produces: 不访问网络的 Fake Provider 测试，覆盖字段合并、追问题型和错误 topics。

- [x] Fake Provider 返回固定结构化结果。
- [x] 断言 Markdown 原始字段不被 LLM 覆盖。
- [x] 断言有父题的草稿强制成为 `FOLLOW_UP`。
- [x] 断言 topic 与分类重复时抛出 `QuestionEnrichmentError`。
- [x] 运行 `pytest tests/interview/question_bank/test_enricher.py -q`，预期全部通过。

### Task 4: Builder 与断点测试

**Files:**
- Create: `tests/interview/question_bank/test_builder.py`
- Test: `liverag/interview/question_bank/builder.py`

**Interfaces:**
- Consumes: `QuestionBankBuilder.build()`、`write_document()`、检查点模型。
- Produces: 中断恢复、源文件指纹和原子 JSON 输出的回归保护。

- [x] 使用可计数 Fake Provider 模拟第二题失败并检查第一题已持久化。
- [x] 第二次运行恢复检查点，断言不重复处理第一题。
- [x] 修改 Markdown 后断言旧检查点被拒绝。
- [x] 写出题库再通过 `QuestionBank.from_file()` 读取。
- [x] 运行 `pytest tests/interview/question_bank/test_builder.py -q`，预期全部通过。

### Task 5: 离线题库生成入口

**Files:**
- Create: `liverag/interview/question_bank/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/interview/question_bank/test_cli.py`

**Interfaces:**
- Consumes: `load_voice_settings()`、`OpenAIQuestionEnrichmentProvider`、`QuestionBankBuilder`。
- Produces: `liverag-build-question-bank` 命令，支持 `--limit` 小样、`--checkpoint` 和 `--output`。

- [x] 先写参数解析和缺失 Key 测试，预期失败。
- [x] 实现只显示配置状态、不打印密钥的命令入口。
- [x] 实现小样转换限制，确保正式构建和试跑走同一校验流程。
- [x] 运行 CLI 测试，预期全部通过。
- [x] 有 Key 时运行 RAG 小样并人工检查结构化字段；无 Key 时明确报告阻塞。
- [x] 小样通过后生成 `liverag/interview/question_bank/data/question_bank.v1.json`。

### Task 6: 持久化面试状态机

**Files:**
- Create: `liverag/interview/state_machine.py`
- Create: `tests/interview/test_state_machine.py`

**Interfaces:**
- Consumes: `InterviewState`、`InterviewSessionRecord`、`InterviewStore.record_transition()`。
- Produces: `InterviewEventType`、`InterviewStateMachine.transition()` 和合法迁移表。

- [x] 写合法主流程、非法迁移、暂停恢复和重复事件测试。
- [x] 定义事件枚举与合法来源状态，不允许调用者直接指定目标状态。
- [x] 根据事件计算问题索引、当前题目、追问次数、开始和结束时间。
- [x] 使用 `record_transition()` 原子持久化，复用 version 和 event id。
- [x] 运行状态机测试，预期全部通过。

### Task 7: 全量验证

**Files:**
- Modify: `docs/plans/interview-coach-plan.md`

**Interfaces:**
- Consumes: 前六项全部产物。
- Produces: 与真实目录和 V1 进度一致的规划文档。

- [x] 运行新增 Interview 测试和完整项目测试。
- [x] 运行本轮新增/修改模块 Ruff 检查。
- [x] 运行 `git diff --check`。
- [x] 搜索旧题库导入路径，预期无结果。
- [x] 更新规划文档 checkbox 和实际文件树。
