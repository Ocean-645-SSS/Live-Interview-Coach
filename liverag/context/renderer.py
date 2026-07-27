"""通话开始前，把系统模版、soul、history、知识库overview和rag规则组合为一份固定的渲染SessionSystemPrompt

SessionPromptRenderer 在会话开始时读取系统模板、SOUL、当前知识库最近的长期 History、Knowledge Overview 以及 RAG 工具规则，
校验知识库与 Session 绑定一致后完成占位符替换。
最终 Prompt 通过 ContextStore 首次写入并锁定，通话期间即使外部上下文改变也不会覆盖。
本场对话因此行为稳定且可审计，而新生成的 History 只会在下一场同知识库会话中生效。
"""

from dataclasses import dataclass

from liverag.config.settings import RagToolMode
from liverag.context.defaults import DEFAULT_RAG_TOOL_DESCRIPTION, RAG_DISABLED_DESCRIPTION
from liverag.context.store import ContextStore


@dataclass(slots=True) #降低内存占用
class SessionPromptRenderResult:
    """包装渲染结果"""

    prompt:str
    prompt_chars:int
    rag_tool_mode:RagToolMode
    history_count:int
    kb_id:str
    kb_name:str


class SessionPromptRenderer:
    """通过ContextStore读取数据，生成当前kb对应的SessionSystemPrompt"""

    def __init__(self,*,store:ContextStore,history_limit:int)->None:

        if (
        isinstance(history_limit, bool)
        or not isinstance(history_limit, int)
        or history_limit <= 0
        ):
            raise ValueError("history_limit必须是正整数")
        
        self.store=store
        self.history_limit=history_limit

    def render(
        self,
        *,
        session_id:str,
        kb_id:str,
        kb_name:str,
        rag_tool_mode:RagToolMode
    )->SessionPromptRenderResult:
        """session system prompt渲染入口"""

        #先检查kb_id是否是当前session的知识库
        runtime = self.store.read_runtime_state(session_id)
        if runtime.get("kb_id") != kb_id:
            raise ValueError("kb_id与当前session绑定的知识库不一致")

        #读取最近limit条history
        history=self.store.read_recent_history(kb_id,self.history_limit)
        #整理history文本
        rendered_history=self._format_history(history)

        #读取system prompt模版
        prompt=self.store.read_system_prompt_template()
        #校验模版占位符
        required_placeholders = (
            "{{SOUL_MD}}",
            "{{HISTORY_JSONL}}",
            "{{KNOWLEDGE_OVERVIEW_MD}}",
            "{{RAG_TOOL_DESCRIPTION}}",
        )
        missing = [
            placeholder
            for placeholder in required_placeholders
            if placeholder not in prompt
        ]
        if missing:
            raise ValueError(f"系统提示词模板缺少必要占位符: {missing}")
        
        #替换soul
        prompt=prompt.replace("{{SOUL_MD}}",self.store.read_soul().strip() or "无")
        #替换history
        prompt=prompt.replace("{{HISTORY_JSONL}}",rendered_history)
        #替换知识库overview
        prompt=prompt.replace("{{KNOWLEDGE_OVERVIEW_MD}}",self.store.read_knowledge_overview(kb_id).strip())
        #替换rag工具规则
        prompt = prompt.replace("{{RAG_TOOL_DESCRIPTION}}", self._rag_tool_description(rag_tool_mode))
        #替换kb_id/name
        prompt = prompt.replace("{{KB_ID}}", kb_id)
        prompt = prompt.replace("{{KB_NAME}}", kb_name)
        #保存最终prompt
        locked_prompt=self.store.lock_session_system_prompt(session_id=session_id,content=prompt)

        #返回渲染结果
        return SessionPromptRenderResult(
            prompt=locked_prompt,
            prompt_chars=len(locked_prompt),
            rag_tool_mode=rag_tool_mode,
            history_count=len(history),
            kb_id=kb_id,
            kb_name=kb_name,
        )

    @staticmethod
    def _format_history(history:list[dict]) -> str:
        """把JSONL格式的history转换成能放入prompt的普通文本"""

        if not history:
            return "没有历史对话记录"

        lines:list[str]=[]
        for item in history:
            cursor=item.get("cursor")
            timestamp=item.get("timestamp") or ""
            content=str(item.get("content") or "").strip()
            if not content:
                continue
            prefix=f"[{cursor}] {timestamp}".strip()
            lines.append(f"{prefix}\n{content}")
        return "\n\n".join(lines) if lines else "没有历史对话记录"

    @staticmethod
    def _rag_tool_description(rag_tool_mode:RagToolMode)->str:
        """根据RAG模式选择对应的说明
        auto：调用知识库检索工具
        never：禁用知识库检索"""

        if rag_tool_mode=="auto":
            return DEFAULT_RAG_TOOL_DESCRIPTION.strip()
        if rag_tool_mode=="never":
            return RAG_DISABLED_DESCRIPTION.strip()
        raise ValueError("rag_tool_mode must be 'auto' or 'never'")
