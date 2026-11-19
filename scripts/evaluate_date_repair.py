"""Evaluate three fixed-wrong-upper candidates; only --live enables real Qwen repair."""

import argparse
import json
import os
from pathlib import Path
import sys

from dotenv import dotenv_values
from sql_optimization_agent.repair_evaluation import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Allow one real repair request per case, at most three total')
    parser.add_argument('--output', type=Path, help='New report path; existing files are never overwritten')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output or root/'reports'/('date_repair_live.json' if args.live else 'date_repair_simulated.json')
    config = {**dotenv_values(root/'.env', interpolate=False), **os.environ}
    try:
        report = evaluate(config, output, live=args.live, progress=lambda row:
                          print(f"Case {row['case']}: {row['status']}", file=sys.stderr, flush=True))
        print(json.dumps({'report': str(output), 'status': report['status'], 'summary': report['summary']}, indent=2))
        return 0 if report['status'] == 'completed' and report['summary']['passed_cases'] == 3 else 1
    except FileExistsError:
        print('Output already exists; choose a new --output path. No cases executed.', file=sys.stderr)
        return 2
    except OSError:
        print('Unable to access local configuration, services or report output.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
