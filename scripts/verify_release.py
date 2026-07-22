"""Run FINITE's isolated release-candidate gate and emit offline judge evidence.

The gate never calls Bob, watsonx, a model provider, an emergency system, or an effect
adapter. Dependency installation and advisory audits can contact package registries and
vulnerability services; the structured result says so explicitly.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from time import monotonic


ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "apps" / "physics-console"
PYTHON_AUDIT = ROOT / "artifacts" / "release-pip-audit.json"
PYTHON_AUDIT_REQUIREMENTS = ROOT / "artifacts" / "release-resolved-requirements.txt"


class VerificationFailure(RuntimeError):
    """One named release-candidate check failed."""

    def __init__(self, label: str, detail: str) -> None:
        super().__init__(f"{label}: {detail}")
        self.label = label
        self.detail = detail


def _tool(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise VerificationFailure("tool preflight", f"required tool is unavailable: {name}")
    return resolved


def _run(label: str, command: list[str], *, cwd: Path = ROOT) -> dict[str, object]:
    print(f"\n==> {label}", flush=True)
    started = monotonic()
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except subprocess.CalledProcessError as exc:
        raise VerificationFailure(label, f"command exited with status {exc.returncode}") from exc
    elapsed = round(monotonic() - started, 3)
    print(f"<== {label}: passed in {elapsed:.3f}s", flush=True)
    return {"label": label, "status": "passed", "elapsed_seconds": elapsed}


def _capture(
    label: str,
    command: list[str],
    output: Path,
    *,
    cwd: Path = ROOT,
) -> dict[str, object]:
    print(f"\n==> {label}", flush=True)
    started = monotonic()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("wb") as stream:
            subprocess.run(command, cwd=cwd, check=True, stdout=stream)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = f"command failed: {exc}"
        raise VerificationFailure(label, detail) from exc
    elapsed = round(monotonic() - started, 3)
    print(f"<== {label}: passed in {elapsed:.3f}s", flush=True)
    return {"label": label, "status": "passed", "elapsed_seconds": elapsed}


def _clean_tree_check(
    label: str,
    git: str,
    *,
    pathspec: str | None = None,
) -> dict[str, object]:
    command = [git, "status", "--porcelain=v1", "--untracked-files=all"]
    if pathspec is not None:
        command.extend(["--", pathspec])
    started = monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise VerificationFailure(label, f"git status exited with {exc.returncode}") from exc
    if completed.stdout:
        print(completed.stdout, file=sys.stderr, end="")
        raise VerificationFailure(label, "tracked, staged, or untracked source changes detected")
    elapsed = round(monotonic() - started, 3)
    print(f"<== {label}: passed in {elapsed:.3f}s", flush=True)
    return {"label": label, "status": "passed", "elapsed_seconds": elapsed}


def _venv_python(directory: Path) -> Path:
    relative = Path("Scripts/python.exe") if sys.platform == "win32" else Path("bin/python")
    return directory / relative


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the non-publishing FINITE release-candidate verification gate."
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help=(
            "reuse the caller's Python environment and console dependencies; this explicitly "
            "disables Python install isolation"
        ),
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


def _perform_checks(
    *,
    args: argparse.Namespace,
    python: str,
    npm: str,
    git: str,
    checks: list[dict[str, object]],
) -> tuple[Path, Path]:
    checks.append(
        _run(
            "regenerate digest-bound console artifact",
            [python, "scripts/export_console_artifact.py"],
        )
    )
    checks.append(
        _clean_tree_check(
            "verify generated console artifact is committed",
            git,
            pathspec="apps/physics-console/app/demo-artifact.json",
        )
    )
    checks.append(_run("check Python dependency consistency", [python, "-m", "pip", "check"]))
    checks.append(_run("lint Python", [python, "-m", "ruff", "check", "."]))
    checks.append(
        _run(
            "test Python with V5 coverage floor",
            [
                python,
                "-m",
                "pytest",
                "--cov=agent_physics",
                "--cov-report=term-missing",
                "--cov-fail-under=90",
            ],
        )
    )
    PYTHON_AUDIT.parent.mkdir(parents=True, exist_ok=True)
    checks.append(
        _capture(
            "capture resolved all-extras dependency set",
            [python, "-m", "pip", "freeze", "--all", "--exclude", "agent-physics"],
            PYTHON_AUDIT_REQUIREMENTS,
        )
    )
    checks.append(
        _run(
            "audit resolved Python environment",
            [
                python,
                "-m",
                "pip_audit",
                "--strict",
                "--progress-spinner",
                "off",
                "--format",
                "json",
                "--output",
                str(PYTHON_AUDIT),
                "--requirement",
                str(PYTHON_AUDIT_REQUIREMENTS),
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
    checks.append(_clean_tree_check("verify source tree remains clean", git))
    return output, raw_experiments


def _summary(
    *,
    status: str,
    checks: list[dict[str, object]],
    isolated: bool,
    started: float,
    output: Path | None = None,
    raw_experiments: Path | None = None,
    failure: VerificationFailure | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": "finite-release-verification/v2",
        "classification": "release-candidate",
        "status": status,
        "decision": "candidate-checks-passed" if status == "passed" else "blocked",
        "release_ready": False,
        "python_install_isolated": isolated,
        "network_access": {
            "may_contact_external_services": True,
            "operations": [
                "pip installs from configured indexes unless --skip-install is used",
                "npm ci unless --skip-install is used",
                "pip-audit vulnerability lookup",
                "npm audit vulnerability lookup",
            ],
        },
        "model_provider_or_effect_calls_by_script": False,
        "limitations": [
            "This gate does not publish, tag, sign, or create a GitHub release.",
            "It does not validate a genuine Bob, watsonx, deployment, or eligibility attestation.",
            "The package build/reproducibility gate remains the GitHub package-candidate job.",
            "The console npm install remains workspace-local rather than container-isolated.",
            "The runtime-resolved audit input is exact-pinned but not a committed hashed lock.",
        ],
        "checks": checks,
        "total_elapsed_seconds": round(monotonic() - started, 3),
    }
    if output is not None:
        result["judge_bundle"] = str(output)
    if raw_experiments is not None:
        result["raw_experiments"] = str(raw_experiments)
    if failure is not None:
        result["failure"] = {"label": failure.label, "detail": failure.detail}
    return result


def main() -> int:
    args = parse_args()
    started = monotonic()
    checks: list[dict[str, object]] = []
    isolated = not args.skip_install
    try:
        npm = _tool("npm")
        git = _tool("git")
        checks.append(_clean_tree_check("verify fresh clean source tree", git))

        if args.skip_install:
            output, raw_experiments = _perform_checks(
                args=args,
                python=sys.executable,
                npm=npm,
                git=git,
                checks=checks,
            )
        else:
            with tempfile.TemporaryDirectory(prefix="finite-release-verifier-") as temporary:
                environment = Path(temporary) / "venv"
                checks.append(
                    _run(
                        "create ephemeral Python environment",
                        [sys.executable, "-m", "venv", str(environment)],
                    )
                )
                python = str(_venv_python(environment))
                checks.append(
                    _run(
                        "install pinned release tooling",
                        [python, "-m", "pip", "install", "-r", "requirements/release-tools.txt"],
                    )
                )
                checks.append(
                    _run(
                        "install project with every optional integration",
                        [python, "-m", "pip", "install", "-e", ".[dev,api,watsonx,langgraph]"],
                    )
                )
                checks.append(
                    _run("install locked Physics Console dependencies", [npm, "ci"], cwd=CONSOLE)
                )
                output, raw_experiments = _perform_checks(
                    args=args,
                    python=python,
                    npm=npm,
                    git=git,
                    checks=checks,
                )
    except VerificationFailure as failure:
        print(
            "\n" + json.dumps(
                _summary(
                    status="failed",
                    checks=checks,
                    isolated=isolated,
                    started=started,
                    failure=failure,
                ),
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(
        "\n"
        + json.dumps(
            _summary(
                status="passed",
                checks=checks,
                isolated=isolated,
                started=started,
                output=output,
                raw_experiments=raw_experiments,
            ),
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
