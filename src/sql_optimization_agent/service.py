"""One scenario-based application entry point shared by CLI, HTTP and MCP."""

import json
import os
from pathlib import Path

from dotenv import dotenv_values

from sql_optimization_agent.qwen_workflow import FIXTURES, run_workflow
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.tools.sql_benchmark import json_value
from sql_optimization_agent.tools.order_items_benchmark import SCENARIO as ORDER_ITEMS_SCENARIO, FIXTURES as ITEM_FIXTURES, run as run_items
from sql_optimization_agent.tools.sql_benchmark import BenchmarkValidationError
from sql_optimization_agent.db import DatabaseConfigurationError, DatabaseConnectionError


ORDERS_DATE_SCENARIO = "orders_date_filter_2025_06_15"
DEFAULT_ORDER_DATE = "2025-06-15"


class OptimizationService:
    def __init__(self, config=None, *, fixture=None, enabled_scenarios=None):
        if config is None:
            root = Path(__file__).resolve().parents[2]
            config = {**dotenv_values(root / ".env", interpolate=False), **os.environ}
        self.config = dict(config)
        self.fixture = fixture
        self.enabled_scenarios = (ORDERS_DATE_SCENARIO,ORDER_ITEMS_SCENARIO) if enabled_scenarios is None else enabled_scenarios

    def optimize(self, scenario: str, *, date: str | None = None) -> dict:
        if scenario not in self.enabled_scenarios:
            return {"status": "unsupported_scenario", "reason": "Unknown scenario; use " + ", ".join(self.enabled_scenarios),
                    "benchmark": None}
        try:
            date_context = OrderDateContext.parse(DEFAULT_ORDER_DATE if date is None else date)
        except ValueError as error:
            return {"status": "invalid_date", "stage": "configuration", "reason": str(error),
                    "model_requests": 0, "benchmark": None}
        supported_fixtures = ITEM_FIXTURES if scenario == ORDER_ITEMS_SCENARIO else FIXTURES
        if self.fixture is not None and self.fixture not in supported_fixtures:
            return {"status": "invalid_fixture", "reason": "Unknown server-side model fixture", "benchmark": None}
        try:
            if scenario == ORDER_ITEMS_SCENARIO:
                report = run_items(self.config,date_context,fixture=self.fixture or 'normal')
            else:
                report = run_workflow(self.config, date_context=date_context, fixture=self.fixture)
        except (DatabaseConfigurationError,DatabaseConnectionError,BenchmarkValidationError) as error:
            report = {"status":"validation_error","stage":"result_validation","reason":str(error),
                      "scenario":scenario,"target_date":date_context.target.isoformat(),"model_requests":0,"benchmark":None}
        except OSError:
            report = {"status": "service_error", "reason": "Unable to access local configuration or services", "benchmark": None}
        # Preserve typed mismatch values consistently instead of HTTP's Decimal→float conversion.
        return json.loads(json.dumps(report, ensure_ascii=False, default=json_value))


def configured_service() -> OptimizationService:
    """Fixtures are selected by the local server operator, never by request bodies."""
    # New items scenario is enabled only for the public service/CLI in this iteration.
    return OptimizationService(fixture=os.environ.get("SQL_OPTIMIZATION_MODEL_FIXTURE") or None,
                               enabled_scenarios=(ORDERS_DATE_SCENARIO,))
