"""Normalize one Python sdist's tar and gzip metadata for repeatable hashing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_physics.release_candidate import normalize_sdist


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source-date-epoch", required=True, type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = normalize_sdist(
        args.source,
        args.destination,
        source_date_epoch=args.source_date_epoch,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
