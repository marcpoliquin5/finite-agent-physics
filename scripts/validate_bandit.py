"""Validate a full Bandit JSON report and reject scanner errors or medium/high findings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_physics.quality_gate import QualityGateError, validate_bandit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bandit_json", type=Path)
    args = parser.parse_args()
    try:
        result = validate_bandit(args.bandit_json)
    except QualityGateError as exc:
        print(f"Bandit quality gate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
