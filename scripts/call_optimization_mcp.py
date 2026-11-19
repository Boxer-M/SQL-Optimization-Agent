"""Discover and call the local stdio MCP tool using the official client."""

import asyncio
import argparse
from datetime import timedelta
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from sql_optimization_agent.service import ORDERS_DATE_SCENARIO


async def call(date: str | None = None, scenario: str = ORDERS_DATE_SCENARIO):
    parameters = StdioServerParameters(command=sys.executable,
                                      args=["-m", "sql_optimization_agent.mcp_server"],
                                      env={key: value for key, value in os.environ.items()
                                           if key in {"SQL_OPTIMIZATION_DATE_REPAIR", "SQL_OPTIMIZATION_MODEL_FIXTURE", "SQL_OPTIMIZATION_ITEMS_MODE",
                                                      "SQL_OPTIMIZATION_ITEMS_FIXTURE", "QWEN_API_KEY", "QWEN_BASE_URL",
                                                      "QWEN_MODEL", "POSTGRES_PORT", "POSTGRES_READER_USER",
                                                      "POSTGRES_READER_PASSWORD"}})
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=180)) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Discovered tools: " + ", ".join(tool.name for tool in tools.tools), file=sys.stderr)
            arguments = {"scenario": scenario}
            if date is not None:
                arguments["date"] = date
            result = await session.call_tool("optimize_orders_date", arguments)
            print(json.dumps(result.structuredContent, ensure_ascii=False, indent=2))
            return 1 if result.isError else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Optional date forwarded to the MCP tool; service defines the default")
    parser.add_argument("--scenario", default=ORDERS_DATE_SCENARIO, help="Supported scenario identifier")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(call(args.date,args.scenario)))
