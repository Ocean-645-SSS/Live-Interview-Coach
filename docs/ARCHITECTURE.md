# 当前运行架构说明

本文描述现有 LiveRAG 运行时。面向公网、多用户、支持异步任务的 Interview Coach 目标架构见 [Interview Coach 目标架构](./INTERVIEW_COACH_ARCHITECTURE.md)。

## 总体结构

```text
liverag/
  main.py      # LiveKit worker 统一入口
  agent/       # LiveKit 语音 Agent、provider、工具调用、语音链路指标
  context/     # SessionSystemPrompt、SOUL、per-KB history、知识库概览
  rag/         # LightRAG Core Service，按 knowledge_base 物理隔离
  api/         # 前端管理 API，唯一对外后端入口
  config/      # 全局配置和运行时模型配置
  logging/     # 全局事件日志
  runtime/     # ~/.LiveRAG 路径和运行状态
```

## 通话开始前数据流

```text
LiveKit job 创建
  -> 读取下次通话配置的 kb_id
  -> 预热该知识库 engine
  -> 清空上一通话 messages/rag_context/session_system_prompt
  -> 读取已有 knowledge_overview.md；缺失时使用降级说明，不在启动时生成
  -> 读取 system_prompt_template.md
  -> 读取 SOUL.md
  -> 读取 history/{kb_id}/history.jsonl 最近 N 条
  -> 读取 context/{kb_id}/knowledge_overview.md
  -> 根据 rag_tool_mode 渲染 RAG_TOOL_DESCRIPTION
  -> 写入 session/session_system_prompt.md
  -> VoiceAssistant 使用固定 instructions 启动
```

## 通话中数据流

```text
用户语音
  -> LiveKit STT
  -> messages.jsonl 追加 user
  -> LLM 基于固定 instructions + 当前通话 messages 推理
  -> auto 模式下模型可调用 search_knowledge_base
  -> RAG 工具只查询当前锁定 kb_id
  -> rag_context.jsonl 写入查询事实和证据
  -> LLM 输出回复
  -> messages.jsonl 追加 assistant
  -> TTS 播放
```

通话中不再读取或拼接：

- `history.jsonl`
- `knowledge_overview.md`
- 最近消息摘要
- 动态 system prompt
- `memory.md`

## 挂断后数据流

```text
电话结束
  -> 读取本次 messages.jsonl
  -> 读取 SOUL.md、当前 KB knowledge_overview.md、当前 KB 最近 history
  -> Context Model 压缩成本次长期 history 内容
  -> 追加到 history/{kb_id}/history.jsonl
  -> cursor 自增
  -> 清空 messages.jsonl
  -> runtime_state.json 写入 ended_at 和 history_compaction 结果
```

压缩失败不阻断下一次启动，只写日志和 runtime state。

## 模块边界

- `liverag/agent/` 只负责 LiveKit hooks、工具调用、语音模型装配和链路指标。
- `liverag/context/` 负责提示词模板、SOUL、history、知识库概览和固定 SessionSystemPrompt 渲染。
- `liverag/rag/` 负责知识库 CRUD、文档原文件、LightRAG workspace、检索和索引。
- `liverag/api/` 是前端唯一后端入口，负责模型配置、session、SOUL、knowledge_overview 和 RAG 包装接口；不暴露系统提示词模板和 history。
- `liverag/config/` 负责环境变量和运行时配置文件读取。
