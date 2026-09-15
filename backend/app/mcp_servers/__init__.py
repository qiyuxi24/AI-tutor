"""MCP server 集合（标准 Model Context Protocol，2026-09-12）。

每个模块 = 一个可独立运行的 MCP server（stdio / Streamable HTTP），
同时可被 app/core/agent_tools/mcp_host.py 以 in-memory 方式挂进本项目 Agent。
"""
