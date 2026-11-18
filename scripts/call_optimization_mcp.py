"""Discover and call the local stdio MCP tool using the official client."""

import asyncio
from datetime import timedelta
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from sql_optimization_agent.service import ORDERS_DATE_SCENARIO


async def call():
    parameters = StdioServerParameters(command=sys.executable,
                                      args=["-m", "sql_optimization_agent.mcp_server"],
                                      env={key: value for key, value in os.environ.items()
                                           if key in {"SQL_OPTIMIZATION_MODEL_FIXTURE", "QWEN_API_KEY", "QWEN_BASE_URL",
                                                      "QWEN_MODEL", "POSTGRES_PORT", "POSTGRES_READER_USER",
                                                      "POSTGRES_READER_PASSWORD"}})
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=180)) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Discovered tools: " + ", ".join(tool.name for tool in tools.tools), file=sys.stderr)
            result = await session.call_tool("optimize_orders_date", {"scenario": ORDERS_DATE_SCENARIO})
            print(json.dumps(result.structuredContent, ensure_ascii=False, indent=2))
            return 1 if result.isError else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(call()))
