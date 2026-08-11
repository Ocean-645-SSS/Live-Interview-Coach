"""安全受控的 stdio MCP Client。

职责：
- 启动固定 MCP subprocess（python -m liverag.interview.intelligence.mcp.server）
- 验证 Tool contract（存在性 + schema 匹配）
- 调用固定 Tool（search_nowcoder_experiences）
- 读取 structured_content → 本地 Pydantic 二次校验
- 超时 / 不可用 / 契约不匹配 → ProviderError

流程：
search()
   ↓
_search_impl()
   ↓
启动 MCP Server 子进程
   ↓
建立 ClientSession
   ↓
initialize()
   ↓
_verify_contract()
   ↓
call_tool()
   ↓
_validate_result()
   ↓
NowcoderSearchResult

约束：
- stdio transport only，不实现 Transport 抽象工厂
- 不使用 MCP session pool / process pool / connection pool
- 一次查询使用一次 Client 生命周期
- 不允许请求参数控制 executable / module path / tool name
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from liverag.interview.intelligence.mcp.server import (
    TOOL_NAME,
    NowcoderSearchResult,
)
from liverag.interview.intelligence.provider import (
    ProviderError,
    ProviderErrorCode,
)

logger = logging.getLogger(__name__)

# ====================== 常量 ======================

# MCP Server 固定模块路径 — 不允许外部控制
_SERVER_MODULE = "liverag.interview.intelligence.mcp.server"

# 默认整个 MCP stage 的超时（秒），需小于 Worker task timeout
DEFAULT_PROVIDER_TIMEOUT = 60.0


# ====================== Client ======================

class McpNowcoderClient:
    """安全受控的牛客 MCP stdio Client。

    使用方式:
        client = McpNowcoderClient(timeout=25)
        result = await client.search(["字节跳动 Agent开发 面经"], max_results=10)
    """

    def __init__(self, timeout: float = DEFAULT_PROVIDER_TIMEOUT) -> None:
        self._timeout = timeout
        #相当于python -m liverag.interview.intelligence.mcp.server
        self._server_params = StdioServerParameters(
            command=sys.executable, #用什么解释器启动子进程
            args=["-m", _SERVER_MODULE],    #命令行参数
        )

    # ---- public API ----
    async def search(
        self, queries: list[str], max_results: int = 10,
    ) -> NowcoderSearchResult:
        """搜索牛客面经。

        完整链路：启动 subprocess → 初始化 session → 校验 contract → 调用 Tool
        → 读取 structured_content → Pydantic 二次校验。

        Args:
            queries: 搜索关键词列表。
            max_results: 期望面经数量，默认 10。
        Returns:
            经 Pydantic 二次校验的 NowcoderSearchResult。
        Raises:
            ProviderError: 超时 / 不可用 / Tool 不存在 / 契约不匹配 / 返回非法。
        """

        try:
            #最多30s
            async with asyncio.timeout(self._timeout):
                return await self._search_impl(queries, max_results)
        #超时报错
        except TimeoutError:
            raise ProviderError(
                code=ProviderErrorCode.TIMEOUT,
                provider="community_nowcoder_spider",
                message=f"MCP call timed out after {self._timeout}s",
                retryable=True,
            ) from None

    # ---- 内部实现 ----
    async def _search_impl(
        self, queries: list[str], max_results: int,
    ) -> NowcoderSearchResult:
        try:
            #启动server子进程，建立read+write通信管道
            #与server里的stdio_server对应
            async with (
                stdio_client(self._server_params) as (read, write),
                ClientSession(read, write) as session,
            ):
                #完成 mcp client与server 握手
                await session.initialize()

                #校验 Tool contract：看server里有没有要用到的tool
                await self._verify_contract(session)

                #调用 Tool
                result = await session.call_tool(
                    name=TOOL_NAME,
                    arguments={"queries": queries, "max_results": max_results},
                )

        except OSError as exc:
            raise ProviderError(
                code=ProviderErrorCode.UNAVAILABLE,
                provider="community_nowcoder_spider",
                message=f"Failed to start MCP subprocess: {exc}",
                retryable=True,
            ) from exc

        #校验返回结果 structured_content
        return self._validate_result(result)


    async def _verify_contract(self, session: ClientSession) -> None:
        """验证 MCP Server 提供的 Tool contract 符合预期。

        检查项：
        1. search_nowcoder_experiences Tool 存在
        2. input schema 的关键字段匹配
        """

        tools_result = await session.list_tools()
        tools = tools_result.tools

        tool_names = {t.name for t in tools}
        if TOOL_NAME not in tool_names:
            raise ProviderError(
                code=ProviderErrorCode.TOOL_NOT_FOUND,
                provider="community_nowcoder_spider",
                message=(
                    f"Tool '{TOOL_NAME}' not found. "
                    f"Available tools: {sorted(tool_names)}"
                ),
            )

        # 找到目标 Tool，校验 schema 关键字段
        target = next(t for t in tools if t.name == TOOL_NAME)
        actual_schema = target.input_schema or {}

        # 校验 queries 字段存在且为 array of string
        props = actual_schema.get("properties", {})
        queries_schema = props.get("queries", {})
        if (
            queries_schema.get("type") != "array"
            or queries_schema.get("items", {}).get("type") != "string"
        ):
            raise ProviderError(
                code=ProviderErrorCode.CONTRACT_MISMATCH,
                provider="community_nowcoder_spider",
                message=(
                    f"Tool '{TOOL_NAME}' input schema mismatch: "
                    f"expected queries: array[string], "
                    f"got {queries_schema}"
                ),
            )

        logger.debug("MCP contract verified: tool=%s, schema OK", TOOL_NAME)

    def _validate_result(self, result: Any) -> NowcoderSearchResult:
        """对 MCP 返回的 structured_content 进行本地 Pydantic 二次校验。

        structured_content 是唯一正式数据源；text content 仅用于错误诊断。
        """

        raw = getattr(result, "structured_content", None)

        if raw is None:
            # 尝试从 text content 提取诊断信息
            text_parts = []
            for c in getattr(result, "content", []) or []:
                if hasattr(c, "text"):
                    text_parts.append(c.text)
            diag = "; ".join(text_parts) if text_parts else "(empty)"
            raise ProviderError(
                code=ProviderErrorCode.INVALID_RESPONSE,
                provider="community_nowcoder_spider",
                message=f"No structured_content in MCP response. Text content: {diag}",
            )

        try:
            return NowcoderSearchResult.model_validate(raw)
        except Exception as exc:
            raise ProviderError(
                code=ProviderErrorCode.INVALID_RESPONSE,
                provider="community_nowcoder_spider",
                message=f"Pydantic validation failed: {exc}",
            ) from exc


__all__ = [
    "DEFAULT_PROVIDER_TIMEOUT",
    "McpNowcoderClient",
]
