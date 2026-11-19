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
from sql_optimization_agent.order_items_qwen import MODEL_FIXTURES, run as run_items_qwen
from sql_optimization_agent.performance import attach_assessment
from sql_optimization_agent.recommendation import attach_recommendation


ORDERS_DATE_SCENARIO = "orders_date_filter_2025_06_15"
DEFAULT_ORDER_DATE = "2025-06-15"


class OptimizationService:
    def __init__(self, config=None, *, fixture=None, enabled_scenarios=None,
                 items_mode='controlled', items_fixture=None, date_repair_enabled=False):
        if config is None:
            root = Path(__file__).resolve().parents[2]
            config = {**dotenv_values(root / ".env", interpolate=False), **os.environ}
        self.config = dict(config)
        self.fixture = fixture
        self.items_mode = items_mode
        self.items_fixture = items_fixture
        self.date_repair_enabled = date_repair_enabled
        self.enabled_scenarios = (ORDERS_DATE_SCENARIO,ORDER_ITEMS_SCENARIO) if enabled_scenarios is None else enabled_scenarios

    def optimize(self, scenario: str, *, date: str | None = None, use_qwen: bool | None = None, repair_on_mismatch: bool = False) -> dict:
        if scenario == ORDERS_DATE_SCENARIO:
            if type(self.date_repair_enabled) is not bool:
                return attach_recommendation(attach_assessment({
                    "status":"invalid_config","stage":"configuration",
                    "reason":"SQL_OPTIMIZATION_DATE_REPAIR must be true or false",
                    "model_requests":0,"benchmark":None}))
            repair_on_mismatch = repair_on_mismatch or self.date_repair_enabled
        return attach_recommendation(attach_assessment(self._optimize(scenario,date=date,use_qwen=use_qwen,repair_on_mismatch=repair_on_mismatch)))

    def _optimize(self, scenario: str, *, date: str | None = None, use_qwen: bool | None = None, repair_on_mismatch: bool = False) -> dict:
        if scenario not in self.enabled_scenarios:
            return {"status": "unsupported_scenario", "reason": "Unknown scenario; use " + ", ".join(self.enabled_scenarios),
                    "benchmark": None}
        if repair_on_mismatch and scenario != ORDERS_DATE_SCENARIO:
            return {"status":"unsupported_mode","stage":"configuration",
                    "reason":"Mismatch repair is only supported for the orders date-filter scenario",
                    "model_requests":0,"benchmark":None}
        try:
            date_context = OrderDateContext.parse(DEFAULT_ORDER_DATE if date is None else date)
        except ValueError as error:
            return {"status": "invalid_date", "stage": "configuration", "reason": str(error),
                    "model_requests": 0, "benchmark": None}
        if use_qwen is None:
            if scenario == ORDER_ITEMS_SCENARIO and self.items_mode not in ('controlled','qwen'):
                return {'status':'invalid_config','stage':'configuration','reason':'Invalid server-side items mode',
                        'model_requests':0,'benchmark':None}
            use_qwen = scenario == ORDER_ITEMS_SCENARIO and self.items_mode == 'qwen'
        if use_qwen and scenario != ORDER_ITEMS_SCENARIO:
            return {'status':'unsupported_mode','stage':'configuration','reason':'Explicit --qwen mode is for orders_with_items_count',
                    'model_requests':0,'benchmark':None}
        supported_fixtures = (MODEL_FIXTURES if use_qwen else ITEM_FIXTURES) if scenario == ORDER_ITEMS_SCENARIO else FIXTURES
        fixture = self.items_fixture if scenario == ORDER_ITEMS_SCENARIO and self.items_fixture is not None else self.fixture
        if fixture is not None and fixture not in supported_fixtures:
            return {"status": "invalid_fixture", "reason": "Unknown server-side model fixture", "benchmark": None}
        try:
            if scenario == ORDER_ITEMS_SCENARIO:
                report = (run_items_qwen(self.config,date_context,fixture=fixture) if use_qwen
                          else run_items(self.config,date_context,fixture=fixture or 'normal'))
            else:
                report = run_workflow(self.config, date_context=date_context, fixture=fixture,
                                      **({"repair_on_mismatch": True} if repair_on_mismatch else {}))
        except (DatabaseConfigurationError,DatabaseConnectionError,BenchmarkValidationError) as error:
            report = {"status":"validation_error","stage":"result_validation","reason":str(error),
                      "scenario":scenario,"target_date":date_context.target.isoformat(),"model_requests":0,"benchmark":None}
        except OSError:
            report = {"status": "service_error", "reason": "Unable to access local configuration or services", "benchmark": None}
        # Preserve typed mismatch values consistently instead of HTTP's Decimal→float conversion.
        return json.loads(json.dumps(report, ensure_ascii=False, default=json_value))


def configured_service() -> OptimizationService:
    """Fixtures are selected by the local server operator, never by request bodies."""
    # Mode and per-scenario fixtures are local operator settings, not request fields.
    setting = os.environ.get('SQL_OPTIMIZATION_DATE_REPAIR', 'false').strip().lower()
    date_repair = {'true': True, 'false': False}.get(setting, setting)
    return OptimizationService(date_repair_enabled=date_repair, fixture=os.environ.get("SQL_OPTIMIZATION_MODEL_FIXTURE") or None,
                               items_mode=os.environ.get('SQL_OPTIMIZATION_ITEMS_MODE','qwen'),
                               items_fixture=os.environ.get('SQL_OPTIMIZATION_ITEMS_FIXTURE') or None)
