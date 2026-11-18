"""Local stdio MCP interface; stdout is reserved for protocol messages."""

import json
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from sql_optimization_agent.service import ORDERS_DATE_SCENARIO, configured_service


def create_server(service=None) -> FastMCP:
    service = service if service is not None else configured_service()
    server = FastMCP("SQL Optimization Agent")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
    def optimize_orders_date(
        scenario: Annotated[str, Field(description="orders_date_filter_2025_06_15 or orders_with_items_count")] = ORDERS_DATE_SCENARIO,
        date: Annotated[str | None, Field(description="Optional YYYY-MM-DD calendar date; omission uses the public service default")] = None,
    ) -> CallToolResult:
        """Optimize either supported orders scenario for an optional date using server-side model settings."""
        report = service.optimize(scenario, date=date)
        if report["status"] == "invalid_date":
            report = {**report, "error": {"type": "invalid_tool_argument", "parameter": "date"}}
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(report, ensure_ascii=False))],
            structuredContent=report,
            isError=report["status"] not in ("completed", "no_suggestion", "no_rewrite"),
        )

    return server


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
