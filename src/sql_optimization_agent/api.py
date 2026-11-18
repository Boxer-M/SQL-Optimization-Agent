"""Local HTTP interface for the supported orders-date scenario only."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from sql_optimization_agent.service import configured_service


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scenario: str


def create_app(service=None) -> FastAPI:
    application = FastAPI(title="SQL Optimization Agent")
    application.state.optimization_service = service if service is not None else configured_service()

    @application.post("/optimize")
    def optimize(request: OptimizeRequest):
        report = application.state.optimization_service.optimize(request.scenario)
        status = report["status"]
        code = 400 if status == "unsupported_scenario" else (
            200 if status in ("completed", "no_suggestion", "result_mismatch", "invalid_suggestion") else 503
        )
        return JSONResponse(content=report, status_code=code)

    return application


app = create_app()
