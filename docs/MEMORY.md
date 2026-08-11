# History 机制

LiveRAG 将“当前会话记录”和“跨会话历史”分开保存，避免把短期上下文误当作永久记忆。

## 当前会话

每个通用语音会话保存以下审计数据：

- 用户与助手的原始消息；
- 每次 RAG 查询的原始问题、有效查询、命中/失败状态、证据文档和片段摘要；
- 会话运行态，包括绑定知识库、轮次与实际生效的语音配置。

这些数据由 `ContextStore` 写入本地用户数据目录，并可通过 `/sessions/{session_id}/messages`、`/turns`、`/rag-context` 和 `/export` 读取。

## 跨会话历史

通话结束后，`HistoryCompactor` 可调用独立的 Context Model 提炼长期有价值的信息。输入包括：SOUL、知识库概览、最近历史、当前会话消息及本次 RAG 证据。输出以追加式记录保存，并带有 `source_session_id`，因此可追溯到原始通话。

只有满足以下条件时才会写入历史：

- 会话有可处理的消息；
- 已配置 Context Model API Key；
- 模型输出不为 `NO_HISTORY`；
- 同一 `source_session_id` 尚未压缩过。

模型调用失败、没有长期价值或缺少配置时，只记录结果原因，不删除原始会话，也不阻断下一场会话。

## 隔离与生效时机

- 历史按 `kb_id` 分区，只能写入创建该会话时冻结的知识库。
- 新历史仅会注入后续新建会话，不会修改进行中或已结束会话的提示词。
- 历史记录的数量受 `CONTEXT_MODEL_HISTORY_REFERENCE_LIMIT` 控制；单次压缩输入受 `CONTEXT_MODEL_MAX_SESSION_CHARS` 控制。

## Context Model 配置

默认从环境变量读取，也可由 `GET/PUT /model/context-config` 管理：

```dotenv
CONTEXT_MODEL_MODEL=qwen-max
CONTEXT_MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
CONTEXT_MODEL_API_KEY=
CONTEXT_MODEL_MAX_TOKENS=2000
CONTEXT_MODEL_MAX_SESSION_CHARS=16000
CONTEXT_MODEL_HISTORY_REFERENCE_LIMIT=8
CONTEXT_MODEL_TIMEOUT_MS=15000
CONTEXT_MODEL_TEMPERATURE=0.0
```
