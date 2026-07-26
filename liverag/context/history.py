import logging
from typing import Any

from openai import AsyncOpenAI

from liverag.config.settings import ContextModelSettings
from liverag.context.store import ContextStore

logger = logging.getLogger("agent.context.history")


class HistoryCompactor:
    """使用当前独立的Context Model整理当前对话"""

    def __init__(self,*,store:ContextStore,settings:ContextModelSettings):
        """绑定存储和配置"""

        self.store = store
        self.settings = settings

    async def compact_after_call(self,*,session_id:str,kb_id:str,kb_name:str)->dict[str,Any]:
        """读取当前通话消息，压缩为一条历史消息"""

        #读取本次通话消息
        messages=self.store.read_message(session_id=session_id)

        #检查是否需要压缩
        #1.如果没有消息
        if not messages:
            return {"updated":False,"reason":"empty_session"}
        #2.如果没有context model api key
        if not self.settings.api_key:
            return {"updated":False,"reason":"missing_context_model_api_key"}

        #整理输入内容:知识库id+name/SOUL.md/overview/all_history
        session_text,truncated=self._format_messages(messages,max_chars=self.settings.max_session_chars)
        prompt = "\n\n".join(
            [
                f"# 当前知识库\nkb_id: {kb_id}\nkb_name: {kb_name}",
                f"# SOUL.md\n{self.store.read_soul().strip()}",
                f"# 当前知识库概览\n{self.store.read_knowledge_overview(kb_id).strip()}",
                f"# 已有 history 最近记录\n{self._format_existing_history(kb_id)}",
                f"# 本次通话消息\n{session_text}",
            ]
        )

        try:
            #生成模型
            client=AsyncOpenAI(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url,
                timeout=max(self.settings.timeout_ms, 1000) / 1000.0,                
            )
            #调用模型生成回答
            response=await client.chat.completions.create(
                model=self.settings.model,
                messages=[
                    {"role": "system", "content": self.store.read_history_compress_prompt()},
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
        """把当前通话消息格式化给压缩模型"""

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

    def _format_existing_history(self,kb_id:str)->str:
        """格式化最新消息，防止通话内容重复"""

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