"""Local stdio MCP interface; stdout is reserved for protocol messages."""

import json

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from sql_optimization_agent.service import ORDERS_DATE_SCENARIO, configured_service


def create_server(service=None) -> FastMCP:
    service = service if service is not None else configured_service()
    server = FastMCP("SQL Optimization Agent")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
    def optimize_orders_date(scenario: str = ORDERS_DATE_SCENARIO) -> CallToolResult:
        """Optimize only orders_date_filter_2025_06_15; arbitrary SQL is not accepted."""
        report = service.optimize(scenario)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(report, ensure_ascii=False))],
            structuredContent=report,
            isError=report["status"] not in ("completed", "no_suggestion"),
        )

    return server


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
