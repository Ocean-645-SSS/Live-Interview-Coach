# Interview Coach 架构与运行说明

## 产品边界

Interview Coach 用于完成可追溯的中文技术模拟面试。它不把实时语音识别结果直接当作最终评价事实：回答、题目、状态事件、评价、报告和能力画像均有明确的持久化模型与版本边界。

## 主要组件

| 组件 | 责任 |
| --- | --- |
| FastAPI + `InterviewService` | 提供创建、计划、Session、回答、评价、报告和准备任务 API |
| PostgreSQL + SQLAlchemy + Alembic | 面试领域数据和异步任务的持久化事实来源 |
| Redis + Worker | 准备类后台任务、队列协调与情报缓存 |
| Question Bank | 版本化题库、题目筛选和计划生成 |
| LiveKit `interview-agent` | 实时音频、逐题流程控制、热词注入和回答提交 |
| Evaluator | 基于参考答案与 rubric 的结构化评价、转写规范化和证据保存 |
| Skill Progress | 根据可追溯评价聚合长期能力画像与训练建议 |

## 准备与实时面试

创建 `/api/interviews/prepared` 时，系统会从候选人知识库、岗位资料和题库生成可开始的计划。对于耗时准备，可调用 `POST /api/interviews/{interview_id}/prepare`，由 Worker 按“简历解析 → 候选人画像 → 岗位画像 → 可选公司情报 → 计划生成”的阶段执行，并通过 preparation API 查询进度。

面试开始前创建 Session 和 Attempt。Attempt 绑定一个专属 LiveKit 房间；断线重连创建新的 Attempt，不覆盖原记录。面试 Agent 以冻结的计划构建会话热词，开始后根据状态机逐题推进。

## 状态与幂等性

状态由 `InterviewEventType` 驱动，核心路径是：

```text
CREATED → PREPARING → READY → INTRODUCTION → ASKING → LISTENING
       → EVALUATING → FOLLOW_UP / NEXT_QUESTION → COMPLETING → COMPLETED
```

每次状态改变带有事件 ID 和乐观版本号。重复事件、状态不匹配或过期版本被拒绝而不是静默覆盖。`PAUSED`、`ABORTED`、`FAILED` 是受控的非正常出口。

## 评价与转写规范化

每题回答保存原始 transcript。评价器可以输出 `normalized_transcript` 和逐条 `transcript_corrections`，以缓解专业术语的 ASR 误识别；服务端验证纠正记录能从原始 transcript 按顺序重放出规范化文本，禁止无依据的改写。评分仍由题目 rubric 的技术准确性、完整性、表达结构和岗位相关性权重计算。

评价完成后生成可追溯的能力证据；能力画像按技能聚合历史得分、薄弱点与来源评价 ID。报告与画像是派生结果，原始回答和评价记录始终保留为审计来源。

## 配置与部署基线

- `INTERVIEW_DATABASE_URL`：PostgreSQL 连接串；生产环境运行 `alembic upgrade head`。
- `INTERVIEW_REDIS_URL`：Redis 连接串；用于队列、锁和情报缓存。
- `VOICE_LLM_*`：回答评价模型配置；缺少 API Key 时不能执行模型评价。
- `INTERVIEW_INTELLIGENCE_ENABLED=False`：默认关闭外部公司面经情报，启用后应遵守数据来源的使用规则。

Compose 会启动 PostgreSQL、Redis、面试 Agent 和 Worker；生产环境应使用独立托管数据库、持久卷、受控凭据与监控。
