"""Verify a generated FINITE release-candidate evidence directory offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_physics.release_candidate import verify_release_candidate


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "candidate_dir",
        nargs="?",
        type=Path,
        default=ROOT / "artifacts" / "release-candidate",
    )
    return parser.parse_args()


def main() -> int:
    result = verify_release_candidate(parse_args().candidate_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
