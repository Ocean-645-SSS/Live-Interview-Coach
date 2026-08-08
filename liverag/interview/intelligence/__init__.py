"""Interview Intelligence 模块。

提供公司面经情报的完整链路：
- provider.py — 领域契约（Protocol、数据模型、错误类型）
- nowcoder/spider.py — 牛客 HTTP 搜索和帖子抓取
- mcp/server.py — stdio MCP Server + 结构化 Tool
- mcp_client.py — 安全受控 MCP stdio Client
- nowcoder_provider.py — Query ↔ MCP ↔ RawExperience Adapter
- normalizer.py — 确定性标准化
- extractor.py — 不可信正文 → 结构化面经
- aggregator.py — 面经聚合 → CompanyInterviewProfile
- service.py — 完整编排 + 降级策略 + Redis 缓存
"""
