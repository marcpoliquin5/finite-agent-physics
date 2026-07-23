from __future__ import annotations

import argparse
import runpy
from pathlib import Path


def test_release_verifier_prepares_all_fresh_clone_output_parents(
    tmp_path: Path,
) -> None:
    script = Path(__file__).parents[1] / "scripts" / "verify_release.py"
    namespace = runpy.run_path(str(script))
    output_names = (
        "CONSOLE_JUNIT",
        "LIVE_LOAD",
        "PYTHON_AUDIT",
        "PYTHON_AUDIT_REQUIREMENTS",
        "PYTHON_BANDIT",
        "PYTHON_JUNIT",
        "PYTHON_COVERAGE",
        "PYTHON_LICENSES",
    )
    for name in output_names:
        namespace[name] = tmp_path / name.lower() / "evidence.json"
    args = argparse.Namespace(
        output=tmp_path / "judge" / "bundle",
        raw_experiments=tmp_path / "experiments" / "raw.jsonl",
    )

    prepare = namespace["_prepare_output_directories"]
    prepare.__globals__.update({name: namespace[name] for name in output_names})
    prepare(args)

    for name in output_names:
        assert namespace[name].parent.is_dir()
    assert args.output.parent.is_dir()
    assert args.raw_experiments.parent.is_dir()
