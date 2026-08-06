## 已知问题：ASR 专业术语识别错误与评分鲁棒性

现象：
- ASR 对 AI/LLM/Agent 领域专业术语存在误识别。
- 例如：
  - "Agent" 被识别为 "ancient"
  - "CoT" 被拆分为 "c,o,t"
  - 部分大小写敏感术语可能出现格式丢失

当前影响：
- 影响语音助手评分，把用户说的正确的单词评价为错误的而扣分，影响面试评分准确性。

可用的解决方案：
- STT热词：
火山引擎提供热词能力，可以根据用户上传的简历、目标JD以及面试题库动态生成 session 级热词表。流程是：
用户上传简历/JD
        |
        v
LLM提取技术关键词
        |
        v
生成本次Interview Session热词表
        |
        v
创建STT boosting table
        |
        v
Live Interview

官方文档说明热词支持中英文，先根据频率分级，再给词设置 1–10 的权重。例如：
核心术语
10：
Agent|10
RAG|10
LangGraph|10
MCP|10

常见技术词
8：
FastAPI|8
Redis|8
PostgreSQL|8
Embedding|8

容易误识别但不是核心
6：
CoT|6
ToT|6
Qdrant|6
因为 CoT 本身太短：
C O T这种短词很容易引起误纠


- 增加评分器对 STT 错误的容错能力
即使纠错模块没有把 ancient 修成 Agent，Evaluator 也不应该仅凭单个英文词就判定整段回答错误。可在评价 Prompt 中加入：
"输入回答来自自动语音识别，可能包含英文技术术语的同音词、大小写、空格和音译错误。
评价时应结合：
1. 当前问题；
2. 上下文语义；
3. 技术术语表；
4. 回答其余部分；
判断该词是否可能属于转写错误。
若回答的技术逻辑正确，仅存在疑似 STT 术语错误：
- 不得直接记为技术错误；
- 将其放入 asr_uncertainties；
- 必要时选择 CLARIFY；
- 不得仅因疑似转写错误扣除 technical_accuracy。"

评分结果可以增加：
asr_uncertainties: list[AnswerUncertainty]
例如：
{
  "asr_uncertainties": [
    {
    "text":"ancient",
    "possible_term":"Agent",
    "confidence":0.92,
    "reason":"phonetic_similarity",
    "impact":"HIGH"
    }
  ],
  "next_action": "CLARIFY",
  "follow_up_question": "你刚才提到的是 AI Agent 吗？"
}
这样系统不会因为 STT 犯错而直接惩罚用户，在评价中扣分。

但必须注意：
热词增强只影响ASR候选概率，不应作为强制替换规则。最终纠错仍需要结合上下文语义判断。


必须遵守的要求：
1. 调研当前STT实现中火山引擎ASR请求参数结构。
2. 增加热词表支持：
   - 支持固定Agent技术词表，我已经写了一份热词表： @HOT_WORDS.md
   - 保留未来session动态生成入口
3. 不修改现有LiveKit语音状态机。
4. 不影响已有STT流式识别流程。
5. 增加配置化管理，例如：
   stt.hot_words
   stt.boosting_table_id
6. 同时优化Evaluator Prompt：
   - ASR结果可能存在技术术语误识别
   - 不应因为单个疑似错误词降低technical_accuracy
   - 增加 transcript_uncertainties 字段
