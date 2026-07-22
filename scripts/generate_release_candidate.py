"""Generate deterministic FINITE release-candidate evidence without publishing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_physics.release_candidate import generate_release_candidate


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "artifacts" / "release-candidate"
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--source-date-epoch", required=True, type=int)
    parser.add_argument("--source-state", required=True, choices=("clean", "dirty"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = generate_release_candidate(
        dist_dir=args.dist_dir,
        output_dir=args.output_dir,
        project_root=args.project_root,
        source_revision=args.source_revision,
        source_date_epoch=args.source_date_epoch,
        source_state=args.source_state,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
