"""M2-D VoiceAssistant 最小行为测试。"""

from types import SimpleNamespace
from typing import Any

import pytest
from livekit.agents import Agent

from liverag.agent.assistant import VoiceAssistant


class FakeContextManager:
    """记录 VoiceAssistant 传入的消息和 RAG 查询。"""

    def __init__(self) -> None:
        self.user_messages: list[dict[str, Any]] = []
        self.assistant_messages: list[dict[str, Any]] = []
        self.queries: list[dict[str, Any]] = []
        self.query_result = SimpleNamespace(
            error=None,
            has_context=True,
            context="知识库上下文",
        )

    def record_user_message(self, **kwargs: Any) -> None:
        self.user_messages.append(kwargs)

    def record_assistant_message(self, **kwargs: Any) -> None:
        self.assistant_messages.append(kwargs)

    async def query_knowledge_base(self, **kwargs: Any) -> Any:
        self.queries.append(kwargs)
        return self.query_result


class FakeMessage:
    """提供 ChatContext 消息所需的最小字段。"""

    def __init__(self, role: str, text: str) -> None:
        self.role = role
        self.text_content = text


class FakeTool:
    """提供工具过滤所需的最小字段。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.id = ""
        self.function_info = None


def make_assistant(
    manager: FakeContextManager,
    *,
    rag_tool_mode: str = "auto",
) -> VoiceAssistant:
    """创建不依赖在线服务的 VoiceAssistant。"""

    return VoiceAssistant(
        context_manager=manager,
        session_system_prompt="固定系统提示词",
        rag_tool_mode=rag_tool_mode,
    )


async def test_query_tool_uses_clean_query_and_current_turn() -> None:
    """RAG 工具清理输入，并携带当前 turn_index。"""

    manager = FakeContextManager()
    assistant = make_assistant(manager)
    assistant.turn_index = 2

    result = await assistant._query_knowledge_base_tool_text(
        query="  M2-D 是什么？  ",
        source="tool",
    )

    assert result == "知识库上下文"
    assert manager.queries == [
        {
            "query": "M2-D 是什么？",
            "source": "tool",
            "turn_index": 2,
            "tool_name": "search_knowledge_base",
        }
    ]


async def test_query_tool_returns_stable_miss_text() -> None:
    """RAG 未命中时返回明确的依据不足提示。"""

    manager = FakeContextManager()
    manager.query_result = SimpleNamespace(
        error=None,
        has_context=False,
        context="",
    )
    assistant = make_assistant(manager)

    result = await assistant._query_knowledge_base_tool_text(
        query="未知问题",
        source="tool",
    )

    assert "未找到足够依据" in result
    assert "不要编造" in result


def test_user_messages_are_recorded_only_once() -> None:
    """同一个 ChatContext 再次进入 LLM 流程时不重复保存用户消息。"""

    manager = FakeContextManager()
    assistant = make_assistant(manager)
    user_texts = ["第一问", "第二问"]

    assert assistant._ensure_user_turns_recorded(user_texts) == 2
    assert assistant._ensure_user_turns_recorded(user_texts) == 2
    assert manager.user_messages == [
        {"content": "第一问", "turn_index": 1},
        {"content": "第二问", "turn_index": 2},
    ]


def test_never_mode_removes_only_knowledge_tool() -> None:
    """never 模式删除 RAG 工具，但保留其他工具。"""

    tools = [
        FakeTool("search_knowledge_base"),
        FakeTool("other_tool"),
    ]

    remaining = VoiceAssistant._without_knowledge_tool(tools)

    assert [tool.name for tool in remaining] == ["other_tool"]


async def test_llm_node_streams_and_records_assistant_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认 LLM 输出被原样转发，并在结束后保存一条完整回答。"""

    manager = FakeContextManager()
    assistant = make_assistant(manager, rag_tool_mode="never")
    received_tools: list[Any] = []

    async def fake_llm_node(
        agent: Agent,
        *,
        chat_ctx: Any,
        tools: list[Any],
        model_settings: Any,
    ):
        del agent, chat_ctx, model_settings
        received_tools.extend(tools)
        yield "你好"
        yield "，世界"

    monkeypatch.setattr(Agent.default, "llm_node", fake_llm_node)
    chat_context = SimpleNamespace(messages=[FakeMessage("user", "请打个招呼")])
    tools = [
        FakeTool("search_knowledge_base"),
        FakeTool("other_tool"),
    ]

    chunks = [
        chunk
        async for chunk in assistant.llm_node(
            chat_context,
            tools,
            object(),
        )
    ]

    assert chunks == ["你好", "，世界"]
    assert [tool.name for tool in received_tools] == ["other_tool"]
    assert manager.user_messages == [{"content": "请打个招呼", "turn_index": 1}]
    assert manager.assistant_messages == [{"content": "你好，世界", "turn_index": 1}]
