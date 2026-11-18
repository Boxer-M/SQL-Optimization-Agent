"""Local HTTP interface for the two supported orders scenarios."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from sql_optimization_agent.service import configured_service


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scenario: str = Field(description="orders_date_filter_2025_06_15 or orders_with_items_count")
    date: str | None = Field(default=None, description="Optional YYYY-MM-DD; omission uses the public service default")


def create_app(service=None) -> FastAPI:
    application = FastAPI(title="SQL Optimization Agent")
    application.state.optimization_service = service if service is not None else configured_service()

    @application.post("/optimize")
    def optimize(request: OptimizeRequest):
        report = application.state.optimization_service.optimize(request.scenario, date=request.date)
        status = report["status"]
        code = 400 if status in ("unsupported_scenario", "invalid_date") else (
            200 if status in ("completed", "no_suggestion", "no_rewrite", "result_mismatch", "invalid_suggestion",
                              "membership_mismatch", "count_mismatch") else 503
        )
        return JSONResponse(content=report, status_code=code)

    return application


app = create_app()
