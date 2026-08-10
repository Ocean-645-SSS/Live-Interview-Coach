"""通话挂断后，使用独立的Context Model压缩对话消息为长期历史

HistoryCompactor 在通话结束后读取原始消息，并结合 SOUL、当前知识库 Overview 和该 KB 已有的最近 History，调用独立 Context Model 提取长期价值信息。
模型返回 NO_HISTORY 时不写入；
有价值时以追加式 JSONL 保存，并记录 source_session_id 以追溯原始通话。
模型失败只返回结构化失败结果并写入 Runtime，不删除原始 Session，也不阻断下一场会话。
同 KB 的下一场 Session 会通过 Renderer 将这条 History 注入固定 Prompt;
上一场会话结束后新增的 History，只会影响后续新 Session 的 Prompt，不会修改已经开始或结束的旧 Session Prompt
"""

import asyncio
import json
import logging
from typing import Any

from openai import AsyncOpenAI

from liverag.config.settings import ContextModelSettings
from liverag.context.defaults import HISTORY_FACT_GROUNDING_RULES
from liverag.context.store import ContextStore

logger = logging.getLogger("agent.context.history")


class HistoryCompactor:
    """使用当前独立的Context Model整理当前对话"""

    def __init__(self,*,store:ContextStore,settings:ContextModelSettings):
        """绑定存储和配置"""

        self.store = store
        self.settings = settings
        self._compact_lock = asyncio.Lock()

    async def compact_after_call(self,*,session_id:str,kb_id:str,kb_name:str)->dict[str,Any]:
        """读取当前通话消息，压缩为一条历史消息"""

        # 同一个 Compactor 内串行执行，避免重复关闭回调并发追加同一 Session 的摘要。
        async with self._compact_lock:
            return await self._compact_after_call_locked(
                session_id=session_id,
                kb_id=kb_id,
                kb_name=kb_name,
            )

    async def _compact_after_call_locked(
        self,
        *,
        session_id: str,
        kb_id: str,
        kb_name: str,
    ) -> dict[str, Any]:
        """在幂等锁内校验并执行一次 History 压缩。"""

        # History 必须写入 Session 启动时锁定的知识库，拒绝调用方传错 kb_id。
        runtime = self.store.read_runtime_state(session_id)
        session_kb_id = runtime.get("kb_id") if runtime else None
        if session_kb_id != kb_id:
            raise ValueError(
                "HistoryCompactor 的 kb_id 与 session 锁定的知识库不一致："
                f"期望 {session_kb_id!r}，实际 {kb_id!r}"
            )

        # 同一原始 Session 只允许生成一条长期 History。
        existing = self.store.find_history_by_source_session(kb_id, session_id)
        if existing is not None:
            return {
                "updated": False,
                "reason": "already_compacted",
                "record": existing,
            }

        #读取本次通话消息
        messages=self.store.read_message(session_id=session_id)
        rag_records = self.store.read_rag_context(session_id=session_id)

        #检查是否需要压缩
        #1.如果没有消息
        if not messages:
            return {"updated":False,"reason":"empty_session"}
        #2.如果没有context model api key
        if not self.settings.api_key:
            return {"updated":False,"reason":"missing_context_model_api_key"}

        #整理user/assistant messages消息文本
        session_text,truncated=self._format_messages(messages,max_chars=self.settings.max_session_chars)
        rag_text, _ = self._format_rag_context(
            rag_records,
            max_chars=self.settings.max_session_chars,
        )
        prompt = "\n\n".join(
            [
                f"# 当前知识库\nkb_id: {kb_id}\nkb_name: {kb_name}",
                f"# SOUL.md\n{self.store.read_soul().strip()}", # SOUL.md：让压缩结果与 Agent 的长期角色和关注重点保持一致
                f"# 当前知识库概览\n{self.store.read_knowledge_overview(kb_id).strip()}",   # overview：给模型当前知识库的语境，帮助判断哪些信息值得在这个 KB 下长期保留
                f"# 已有 history 最近记录\n{self._format_existing_history(kb_id)}",   # all_history：让模型知道哪些信息已经记录过，尽量避免每场通话重复写入同一条长期记忆
                f"# 本次通话消息\n{session_text}",
                f"# 本次通话 RAG Evidence\n{rag_text}",
            ]
        )

        try:
            #生成模型
            client=AsyncOpenAI(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url,
                timeout=max(self.settings.timeout_ms, 1000) / 1000.0,
            )
            #调用LLM生成回答
            response=await client.chat.completions.create(
                model=self.settings.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"{self.store.read_history_compress_prompt().rstrip()}\n\n"
                            f"{HISTORY_FACT_GROUNDING_RULES.strip()}"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=self.settings.temperature,
                max_tokens=self.settings.max_tokens
            )
            #清理代码块包裹
            content=self._clean_model_text(response.choices[0].message.content or "")
            #如果是NO_HISTORY，不追加记录
            if not content or content.strip().upper()=="NO_HISTORY":
                return {
                    "updated": False,
                    "reason": "no_history_value",
                    "message_count": len(messages),
                    "session_truncated": truncated,
                }
            #把记录添加到history.jsonl
            record=self.store.append_history(kb_id=kb_id, content=content,source_session_id=session_id)
            return {
                "updated": True,
                "reason": "appended",
                "record": record,
                "message_count": len(messages),
                "session_truncated": truncated,
            }
        #抛出异常，记录日志
        except Exception as exc:
            logger.warning("history.compact_failed", extra={"kb_id": kb_id, "error": str(exc)})
            return {
                "updated": False,
                "reason": "context_model_failed",
                "error": f"{type(exc).__name__}: {exc}",
                "message_count": len(messages),
                "session_truncated": truncated,
            }

    @staticmethod
    def _format_messages(messages:list[dict[str,Any]],*,max_chars:int) -> tuple[str,bool]:
        """把当前通话消息格式化给压缩模型，控制消息长度"""

        lines:list[str]=[]

        for msg in messages:
            role=str(msg.get("role") or "?")
            content=str(msg.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content}")

        texts="\n".join(lines)
        #文本长度在范围内，直接返回
        if max_chars<=0 or len(texts)<=max_chars:
            return texts,False

        #超过长度范围截取返回
        marker = "【前文过长，已保留最近通话消息用于 history 压缩】\n"
        budget = max(max_chars - len(marker), 1)
        return marker + texts[-budget:].lstrip(), True

    @staticmethod
    def _format_rag_context(
        records: list[dict[str, Any]],
        *,
        max_chars: int,
    ) -> tuple[str, bool]:
        """将 RAG 审计记录压缩为模型需要的查询、结果与 Evidence 摘要。"""

        summaries: list[dict[str, Any]] = []
        for record in records:
            error = record.get("error")
            status = (
                "failed"
                if error is not None
                else "hit"
                if record.get("hit") is True and record.get("has_context") is True
                else "miss"
            )
            summaries.append(
                {
                    "turn_index": record.get("turn_index"),
                    "query": record.get("query"),
                    "effective_query": record.get("effective_query"),
                    "status": status,
                    "context_preview": record.get("context_preview"),
                    "evidence_documents": record.get("evidence_documents") or [],
                    "evidence_chunks": record.get("evidence_chunks") or [],
                    "error": error,
                }
            )

        if not summaries:
            return "无", False

        text = json.dumps(summaries, ensure_ascii=False, indent=2)
        if max_chars <= 0 or len(text) <= max_chars:
            return text, False

        marker = "【RAG Evidence 过长，已保留最近记录】\n"
        budget = max(max_chars - len(marker), 1)
        return marker + text[-budget:].lstrip(), True

    def _format_existing_history(self,kb_id:str)->str:
        """格式化最新消息，防止通话内容重复
        格式：[1] 2026-07-26 10:30
                用户偏好简短回答。"""

        #读取历史
        history=self.store.read_recent_history(kb_id,limit=self.settings.history_reference_limit)
        if not history:
            return "无"

        #修改格式
        lines:list[str]=[]
        for msg in history:
            content=str(msg.get("content") or "").strip()
            if content:
                lines.append(f"[{msg.get('cursor')}] {msg.get('timestamp')}\n{content}")
        #返回格式化结果
        return "\n\n".join(lines) if lines else "无。"

    @staticmethod
    def _clean_model_text(text: str) -> str:
        """清理模型输出中的代码块包裹。"""

        content = text.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            #删除第一行代码块标记
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            #删除最后一行代码块标记
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            #重新组合
            content = "\n".join(lines).strip()
        return content
