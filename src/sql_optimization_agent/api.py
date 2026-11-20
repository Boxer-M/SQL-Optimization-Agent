"""Local HTTP interface for the two supported orders scenarios."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

from sql_optimization_agent.service import configured_service
from sql_optimization_agent.shared_review import router as review_router


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scenario: str = Field(description="orders_date_filter_2025_06_15 or orders_with_items_count")
    date: str | None = Field(default=None, description="Optional YYYY-MM-DD; omission uses the public service default")


def create_app(service=None) -> FastAPI:
    application = FastAPI(title="SQL Optimization Agent")
    application.include_router(review_router)
    application.state.optimization_service = service if service is not None else configured_service()

    @application.get("/workbench", include_in_schema=False)
    def workbench():
        return FileResponse(Path(__file__).parent/"web/workbench.html",headers={
            "Cache-Control":"no-store", "Referrer-Policy":"no-referrer",
            "Content-Security-Policy":"default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})

    @application.get("/workbench/app.js", include_in_schema=False)
    def workbench_script():
        return FileResponse(Path(__file__).parent/"web/app.js",media_type="text/javascript",headers={"Cache-Control":"no-store"})

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
