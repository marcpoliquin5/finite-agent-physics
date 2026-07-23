"""Require a JUnit report with tests and no failures, errors, skips, or xfails."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_physics.quality_gate import QualityGateError, validate_junit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path)
    args = parser.parse_args()
    try:
        result = validate_junit(args.junit)
    except QualityGateError as exc:
        print(f"JUnit quality gate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
