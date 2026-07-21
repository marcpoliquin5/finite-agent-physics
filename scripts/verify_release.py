"""Run FINITE's clean-room release gate and emit the offline judge evidence.

This script invokes only local build/test tooling. It does not call Bob, watsonx,
GitHub, a model provider, an emergency system, or an external effect adapter.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from time import monotonic


ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "apps" / "physics-console"


def _tool(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise RuntimeError(f"required release tool is unavailable: {name}")
    return resolved


def _run(label: str, command: list[str], *, cwd: Path = ROOT) -> dict[str, object]:
    print(f"\n==> {label}", flush=True)
    started = monotonic()
    subprocess.run(command, cwd=cwd, check=True)
    elapsed = round(monotonic() - started, 3)
    print(f"<== {label}: passed in {elapsed:.3f}s", flush=True)
    return {"label": label, "status": "passed", "elapsed_seconds": elapsed}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install, verify, build, and seal the deterministic FINITE submission."
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="reuse installed Python and locked console dependencies",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/judge-evidence.json"),
    )
    parser.add_argument(
        "--raw-experiments",
        type=Path,
        default=Path("artifacts/judge-experiments.jsonl"),
    )
    parser.add_argument(
        "--revision",
        help="optional caller label; it remains marked caller-supplied-unverified",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    python = sys.executable
    npm = _tool("npm")
    git = _tool("git")
    checks: list[dict[str, object]] = []

    if not args.skip_install:
        checks.append(
            _run(
                "install Python project and verification dependencies",
                [python, "-m", "pip", "install", "-e", ".[dev]"],
            )
        )
        checks.append(
            _run("install locked Physics Console dependencies", [npm, "ci"], cwd=CONSOLE)
        )

    checks.append(
        _run(
            "regenerate digest-bound console artifact",
            [python, "scripts/export_console_artifact.py"],
        )
    )
    checks.append(
        _run(
            "verify generated console artifact is committed",
            [
                git,
                "diff",
                "--exit-code",
                "--",
                "apps/physics-console/app/demo-artifact.json",
            ],
        )
    )
    checks.append(_run("check Python dependency consistency", [python, "-m", "pip", "check"]))
    checks.append(_run("lint Python", [python, "-m", "ruff", "check", "."]))
    checks.append(
        _run(
            "test Python with coverage floor",
            [
                python,
                "-m",
                "pytest",
                "--cov=agent_physics",
                "--cov-report=term-missing",
                "--cov-fail-under=85",
            ],
        )
    )
    checks.append(_run("render and test Physics Console", [npm, "test"], cwd=CONSOLE))
    checks.append(_run("lint Physics Console", [npm, "run", "lint"], cwd=CONSOLE))
    checks.append(
        _run(
            "audit Physics Console dependencies",
            [npm, "audit", "--audit-level=low"],
            cwd=CONSOLE,
        )
    )

    output = args.output.resolve()
    raw_experiments = args.raw_experiments.resolve()
    bundle_command = [
        python,
        "-m",
        "agent_physics.cli",
        "judge-bundle",
        "--output",
        str(output),
        "--raw-experiments",
        str(raw_experiments),
    ]
    if args.revision:
        bundle_command.extend(["--revision", args.revision])
    checks.append(_run("seal judge evidence", bundle_command))

    summary = {
        "schema_version": "finite-release-verification/v1",
        "status": "passed",
        "network_or_external_effect_calls_by_script": False,
        "checks": checks,
        "judge_bundle": str(output),
        "raw_experiments": str(raw_experiments),
        "total_elapsed_seconds": round(
            sum(float(check["elapsed_seconds"]) for check in checks),
            3,
        ),
    }
    print("\n" + json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
