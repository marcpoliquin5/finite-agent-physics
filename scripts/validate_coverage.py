"""Validate branch-enabled coverage evidence against statement and branch floors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_physics.quality_gate import QualityGateError, validate_coverage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--statement-floor", type=float, default=90.0)
    parser.add_argument("--branch-floor", type=float, required=True)
    args = parser.parse_args()
    try:
        result = validate_coverage(
            args.coverage_json,
            statement_floor=args.statement_floor,
            branch_floor=args.branch_floor,
        )
    except QualityGateError as exc:
        print(f"Coverage quality gate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
