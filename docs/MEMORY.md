# History 机制

## 设计原则

LiveRAG 不再使用 `memory.md`。长期上下文改为按知识库隔离的 `history.jsonl`：

```text
~/.LiveRAG/history/{kb_id}/history.jsonl
~/.LiveRAG/history/{kb_id}/.cursor
```

每条 history 是一次通话结束后的压缩摘要：

```json
{"cursor":1,"timestamp":"2026-05-17 17:30","content":"- 用户事实：...\n- 决策：...\n- 方案：...\n- 事件：...\n- 偏好：..."}
```

## SOUL.md

`~/.LiveRAG/prompts/SOUL.md` 是用户定义的 Agent 人设、语气、表达风格和行为习惯。

规则：

- 后端不会自动修改 `SOUL.md`。
- 通话开始前会把 `SOUL.md` 渲染进固定 SessionSystemPrompt。
- 通话中不会重新读取 `SOUL.md`。

## 通话开始

通话开始前读取：

- `system_prompt_template.md`
- `SOUL.md`
- 当前 `kb_id` 的最近 N 条 `history.jsonl`
- 当前 `kb_id` 的 `knowledge_overview.md`

渲染占位符：

- `{{SOUL_MD}}`
- `{{HISTORY_JSONL}}`
- `{{KNOWLEDGE_OVERVIEW_MD}}`
- `{{RAG_TOOL_DESCRIPTION}}`

渲染后的固定提示词写入：

```text
~/.LiveRAG/session/session_system_prompt.md
```

## 通话中

通话中只保留当前通话 messages 和当前用户输入。不会动态拼接 history、overview 或额外 memory。

当前通话消息位置：

```text
~/.LiveRAG/session/messages.jsonl
```

## 挂断后

挂断后使用独立 Context Model 压缩本次通话：

- 输入：本次 messages、SOUL、当前 KB overview、当前 KB 最近 history。
- 输出：一条 history content 文本。
- 写入：`history/{kb_id}/history.jsonl`。
- 完成后：清空 `session/messages.jsonl`。

如果本次通话没有长期价值，模型输出 `NO_HISTORY`，后端不追加 history。

## Context Model

Context Model 独立于语音 LLM，用于：

- 知识库概览生成。
- 挂断后 history 压缩。

配置文件：

```text
~/.LiveRAG/model/context_config.json
```

管理接口：

- `GET /model/context-config`
- `PUT /model/context-config`
