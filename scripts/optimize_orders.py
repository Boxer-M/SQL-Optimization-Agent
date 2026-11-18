"""Run the fixed orders optimization workflow and emit a JSON report."""

import argparse
import json
import os
from pathlib import Path
import sys

from dotenv import dotenv_values

from sql_optimization_agent.qwen_workflow import FIXTURES, run_workflow
from sql_optimization_agent.tools.sql_benchmark import json_value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=tuple(FIXTURES), help="Controlled model response; still uses real PostgreSQL")
    args = parser.parse_args()
    try:
        config = {**dotenv_values(Path(__file__).resolve().parents[1] / ".env", interpolate=False), **os.environ}
        report = run_workflow(config, fixture=args.fixture)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=json_value))
        return 0 if report["status"] in ("completed", "no_suggestion") else 1
    except OSError:
        print("Unable to read local configuration or access services.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
