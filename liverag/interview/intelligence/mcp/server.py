"""Interview Coach MCP Server。

暴露 Nowcoder Spider 为一个结构化 MCP Tool，通过 stdio transport与 MCP Client 通信。

职责边界：
- ✅ 暴露 search_nowcoder_experiences Tool
- ✅ 将 Spider 返回的 RawNowcoderPost[] 包装为结构化 NowcoderSearchResult
- ❌ 不包含 Planner / LLM / Cache / Normalizer / Aggregator / Interview 状态

输出约束：
- 日志统一写 stderr，stdout 属于 MCP 协议通道，禁止 print()
- Tool 返回结构化数据，禁止返回 JSON 文件路径
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)
from pydantic import BaseModel, Field

from liverag.interview.intelligence.nowcoder.spider import (
    NowcoderSpider,
    RawNowcoderPost,
    SpiderResult,
)

logger = logging.getLogger(__name__)

# ====================== Tool 元数据 ======================

TOOL_NAME = "search_nowcoder_experiences"
SERVER_NAME = "interview-coach-nowcoder"
SERVER_VERSION = "1.0.0"

TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "description": "搜索关键词列表，例如 ['字节跳动 Agent开发 面经']",
            "minItems": 1,
        },
        "max_results": {
            "type": "integer",
            "description": "期望获取的面经数量，默认 10，最多 20",
            "default": 10,
            "minimum": 1,
            "maximum": 20,
        },
    },
    "required": ["queries"],
}

# ====================== 结构化输出模型 ======================


class NowcoderPostItem(BaseModel):
    """MCP Tool 返回的单条帖子。"""

    source_id: str
    source_type: str
    title: str = ""
    content: str = ""
    url: str = ""
    matched_query: str = ""


class NowcoderSearchResult(BaseModel):
    """MCP Tool search_nowcoder_experiences 的结构化输出。
    用于client 与 server 之间"""

    items: list[NowcoderPostItem] = Field(default_factory=list)
    discovered_count: int = 0
    collected_count: int = 0
    failed_count: int = 0
    partial: bool = False


def _post_to_item(post: RawNowcoderPost) -> NowcoderPostItem:
    """将 Spider 内部模型转换为 MCP 输出模型。"""
    return NowcoderPostItem(
        source_id=post.source_id,
        source_type=post.source_type,
        title=post.title,
        content=post.content,
        url=post.url,
        matched_query=post.matched_query,
    )


def _result_to_search_result(result: SpiderResult) -> NowcoderSearchResult:
    """将 SpiderResult 转换为结构化 MCP 输出。"""
    return NowcoderSearchResult(
        items=[_post_to_item(p) for p in result.posts],
        discovered_count=result.discovered_count,
        collected_count=result.collected_count,
        failed_count=result.failed_count,
        partial=result.partial,
    )


# ====================== MCP Handler ======================

async def _list_tools(_ctx: Any, _params: Any) -> ListToolsResult:
    """列出可用 Tool。

    Tool discovery 仅用于契约验证，不用于让 LLM 动态选择工具。
    目前只有一个固定 Tool。
    """

    return ListToolsResult(
        tools=[
            Tool(
                name=TOOL_NAME,
                title="搜索牛客面经",
                description=(
                    "搜索牛客网面经帖子，返回结构化面经数据。"
                    "支持按公司、岗位等多关键词组合搜索，自动去重并抓取帖子正文。"
                ),
                input_schema=TOOL_INPUT_SCHEMA,
            )
        ]
    )


async def _call_tool(
    _ctx: Any,
    params: CallToolRequestParams,
) -> CallToolResult:
    """处理 Tool 调用。"""

    #只处理 search_nowcoder_experiences，其他 Tool 名称返回错误
    if params.name != TOOL_NAME:
        logger.warning("Unknown tool requested: %s", params.name)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Unknown tool: {params.name}. "
                    f"Only '{TOOL_NAME}' is supported.",
                )
            ],
            is_error=True,
        )

    #参数
    arguments: dict[str, Any] = params.arguments or {}
    queries: list[str] = arguments.get("queries", [])
    max_results: int = arguments.get("max_results", 10)

    logger.info(
        "search_nowcoder_experiences: queries=%s, max_results=%d",
        queries, max_results,
    )

    #获取牛客爬虫
    spider = NowcoderSpider(max_results=max_results)
    #爬取结果
    result = spider.search_and_collect(queries)
    #将爬取结果转换为结构化MCP输出
    structured = _result_to_search_result(result)

    # 构造人类可读摘要
    summary = (
        f"搜索完成: 发现 {result.discovered_count} 篇，"
        f"成功抓取 {result.collected_count} 篇，"
        f"失败 {result.failed_count} 篇"
    )
    if result.partial:
        summary += "（部分成功）"

    logger.info("search_nowcoder_experiences: %s", summary)

    return CallToolResult(
        content=[
            TextContent(type="text", text=summary),
        ],
        structured_content=structured.model_dump(),
        is_error=False,
    )


# ====================== Server 工厂 ======================

def create_server() -> Server:
    """创建并配置 MCP Server 实例。

    Returns:已配置 on_list_tools / on_call_tool handler 的 Server 实例。
    """

    return Server(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        description="Interview Coach — Nowcoder Interview Experience MCP Server",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )


# ====================== 入口 ======================

def main() -> None:
    """MCP Server stdio 主入口。

    配置日志输出到 stderr，然后启动 stdio MCP Server loop。
    该函数由 ``python -m liverag.interview.intelligence.nowcoder_mcp_server`` 调用。
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    #创建MCP server
    server = create_server()

    async def _run() -> None:
        #打开mcp server通信通道：
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,    #server读client的消息
                write_stream,   #server给client回消息
                server.create_initialization_options(), #初始化MCP
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
