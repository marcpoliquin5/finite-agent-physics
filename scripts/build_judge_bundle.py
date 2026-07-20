"""Build FINITE's deterministic offline judge-evidence bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_physics.judge_bundle import build_judge_evidence  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a canonical, digest-bound judge bundle using only deterministic "
            "local fixtures and models."
        )
    )
    parser.add_argument("--output", type=Path, required=True, help="judge JSON output path")
    parser.add_argument(
        "--raw-experiments",
        type=Path,
        help="optional complete 450-record canonical JSONL output path",
    )
    parser.add_argument(
        "--revision",
        help=(
            "optional revision label; caller values are marked unverified. If omitted, "
            "local Git HEAD is read and dirty state is recorded."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generated_paths = [args.output]
    if args.raw_experiments is not None:
        generated_paths.append(args.raw_experiments)
    bundle = build_judge_evidence(
        revision=args.revision,
        project_root=ROOT,
        provenance_excluded_paths=generated_paths,
    )
    bundle.write(args.output, raw_experiments_path=args.raw_experiments)
    print(
        json.dumps(
            {
                "content_digest": bundle.content_digest,
                "judge_bundle": str(args.output.resolve()),
                "raw_experiments": (
                    str(args.raw_experiments.resolve()) if args.raw_experiments else None
                ),
                "verified": bundle.verify(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
