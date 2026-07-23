from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_physics.quality_gate import (
    QualityGateError,
    validate_bandit,
    validate_coverage,
    validate_junit,
    validate_licenses,
)


def _junit(path: Path, body: str, *, tests: int = 1, **counts: int) -> Path:
    attributes = {
        "tests": tests,
        "failures": counts.get("failures", 0),
        "errors": counts.get("errors", 0),
        "skipped": counts.get("skipped", 0),
        "disabled": counts.get("disabled", 0),
    }
    rendered = " ".join(f'{key}="{value}"' for key, value in attributes.items())
    path.write_text(
        f'<testsuites><testsuite name="pytest" {rendered}>{body}</testsuite></testsuites>',
        encoding="utf-8",
    )
    return path


def _coverage(path: Path, **totals: object) -> Path:
    defaults: dict[str, object] = {
        "num_statements": 100,
        "covered_lines": 91,
        "num_branches": 20,
        "covered_branches": 16,
    }
    defaults.update(totals)
    path.write_text(
        json.dumps({"meta": {"branch_coverage": True}, "totals": defaults}),
        encoding="utf-8",
    )
    return path


def test_junit_gate_accepts_only_a_nonempty_all_passed_report(tmp_path: Path) -> None:
    result = validate_junit(_junit(tmp_path / "junit.xml", '<testcase name="ok"/>'))

    assert result["status"] == "passed"
    assert result["testcases"] == 1
    assert result["skipped"] == result["xfailed"] == 0


@pytest.mark.parametrize(
    ("body", "counts", "message"),
    [
        ('<testcase name="bad"><failure/></testcase>', {"failures": 1}, "failures=1"),
        ('<testcase name="bad"><error/></testcase>', {"errors": 1}, "errors=1"),
        ('<testcase name="skip"><skipped/></testcase>', {"skipped": 1}, "skipped=1"),
        (
            '<testcase name="xfail"><skipped type="pytest.xfail"/></testcase>',
            {"skipped": 1},
            "xfailed=1",
        ),
    ],
)
def test_junit_gate_rejects_every_nonpass_outcome(
    tmp_path: Path,
    body: str,
    counts: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(QualityGateError, match=message):
        validate_junit(_junit(tmp_path / "junit.xml", body, **counts))


def test_junit_gate_rejects_empty_malformed_mismatched_and_doctype(tmp_path: Path) -> None:
    with pytest.raises(QualityGateError, match="no test cases"):
        validate_junit(_junit(tmp_path / "empty.xml", "", tests=0))
    mismatched = _junit(tmp_path / "mismatch.xml", '<testcase name="ok"/>', tests=2)
    with pytest.raises(QualityGateError, match="declared 2 tests"):
        validate_junit(mismatched)
    malformed = tmp_path / "malformed.xml"
    malformed.write_text("<testsuite>", encoding="utf-8")
    with pytest.raises(QualityGateError, match="malformed"):
        validate_junit(malformed)
    doctype = tmp_path / "doctype.xml"
    doctype.write_text(
        '<!DOCTYPE foo><testsuite tests="1"><testcase name="ok"/></testsuite>',
        encoding="utf-8",
    )
    with pytest.raises(QualityGateError, match="DTD"):
        validate_junit(doctype)


def test_coverage_gate_reports_statement_and_branch_percentages_separately(
    tmp_path: Path,
) -> None:
    result = validate_coverage(
        _coverage(tmp_path / "coverage.json"),
        statement_floor=90,
        branch_floor=80,
    )

    assert result["statement_percent"] == 91.0
    assert result["branch_percent"] == 80.0
    assert result["branch_floor_percent"] == 80.0


def test_coverage_gate_rejects_no_branch_measurement_and_low_statements(tmp_path: Path) -> None:
    no_branches = tmp_path / "no-branches.json"
    no_branches.write_text(
        json.dumps(
            {
                "meta": {"branch_coverage": False},
                "totals": {
                    "num_statements": 1,
                    "covered_lines": 1,
                    "num_branches": 0,
                    "covered_branches": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(QualityGateError, match="did not enable branch"):
        validate_coverage(no_branches, statement_floor=90, branch_floor=80)
    with pytest.raises(QualityGateError, match="below"):
        validate_coverage(
            _coverage(tmp_path / "low.json", covered_lines=89),
            statement_floor=90,
            branch_floor=80,
        )

    with pytest.raises(QualityGateError, match="branch coverage"):
        validate_coverage(
            _coverage(tmp_path / "low-branch.json", covered_branches=15),
            statement_floor=90,
            branch_floor=80,
        )


def test_coverage_gate_rejects_impossible_counts_and_bad_floor(tmp_path: Path) -> None:
    with pytest.raises(QualityGateError, match="cannot exceed"):
        validate_coverage(
            _coverage(tmp_path / "impossible.json", covered_branches=21),
            statement_floor=90,
            branch_floor=80,
        )
    with pytest.raises(QualityGateError, match="from 0 through 100"):
        validate_coverage(
            _coverage(tmp_path / "floor.json"),
            statement_floor=float("nan"),
            branch_floor=80,
        )
    with pytest.raises(QualityGateError, match="branch_floor"):
        validate_coverage(
            _coverage(tmp_path / "branch-floor.json"),
            statement_floor=90,
            branch_floor=float("inf"),
        )


def test_coverage_gate_rejects_malformed_documents_and_scalar_count_tricks(
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(QualityGateError, match="malformed"):
        validate_coverage(malformed, statement_floor=90, branch_floor=80)

    scalar = tmp_path / "scalar.json"
    scalar.write_text("[]", encoding="utf-8")
    with pytest.raises(QualityGateError, match="JSON object"):
        validate_coverage(scalar, statement_floor=90, branch_floor=80)

    boolean_count = _coverage(tmp_path / "boolean.json", covered_lines=True)
    with pytest.raises(QualityGateError, match="covered_lines"):
        validate_coverage(boolean_count, statement_floor=90, branch_floor=80)


def test_junit_gate_rejects_inconsistent_declared_outcome_counts(tmp_path: Path) -> None:
    inconsistent = _junit(tmp_path / "inconsistent.xml", '<testcase name="ok"/>', failures=1)
    with pytest.raises(QualityGateError, match="declared outcome counts"):
        validate_junit(inconsistent)


def _bandit(path: Path, *results: dict[str, object], errors: list[object] | None = None) -> Path:
    severities = {"UNDEFINED": 0, "LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for result in results:
        severities[str(result["issue_severity"])] += 1
    totals = {f"SEVERITY.{key}": value for key, value in severities.items()}
    totals.update({"loc": 100, "skipped_tests": 0})
    path.write_text(
        json.dumps(
            {
                "errors": errors or [],
                "metrics": {"_totals": totals},
                "results": list(results),
            }
        ),
        encoding="utf-8",
    )
    return path


def _bandit_issue(severity: str) -> dict[str, object]:
    return {
        "filename": "src/example.py",
        "issue_severity": severity,
        "issue_text": "fixture finding",
        "test_id": "B999",
    }


def test_bandit_gate_preserves_low_findings_but_blocks_medium_and_high(tmp_path: Path) -> None:
    passed = validate_bandit(_bandit(tmp_path / "low.json", _bandit_issue("LOW")))
    assert passed["low"] == 1
    assert passed["medium"] == passed["high"] == 0

    for severity in ("MEDIUM", "HIGH"):
        with pytest.raises(QualityGateError, match="zero medium/high"):
            validate_bandit(_bandit(tmp_path / f"{severity}.json", _bandit_issue(severity)))


def test_bandit_gate_rejects_errors_skips_and_count_tampering(tmp_path: Path) -> None:
    with pytest.raises(QualityGateError, match="scanner errors"):
        validate_bandit(_bandit(tmp_path / "error.json", errors=[{"message": "bad"}]))

    skipped = _bandit(tmp_path / "skipped.json")
    document = json.loads(skipped.read_text(encoding="utf-8"))
    document["metrics"]["_totals"]["skipped_tests"] = 1
    skipped.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(QualityGateError, match="skipped 1"):
        validate_bandit(skipped)

    tampered = _bandit(tmp_path / "tampered.json", _bandit_issue("LOW"))
    document = json.loads(tampered.read_text(encoding="utf-8"))
    document["metrics"]["_totals"]["SEVERITY.LOW"] = 0
    tampered.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(QualityGateError, match="declared 0 LOW"):
        validate_bandit(tampered)


def _licenses(path: Path, records: list[dict[str, str]]) -> Path:
    path.write_text(json.dumps(records), encoding="utf-8")
    return path


def test_license_gate_accepts_permissive_and_mixed_classifier_metadata(tmp_path: Path) -> None:
    report = _licenses(
        tmp_path / "licenses.json",
        [
            {"Name": "alpha", "Version": "1.0", "License": "Apache-2.0"},
            {
                "Name": "doc-tool",
                "Version": "2.0",
                "License": "BSD License; GNU General Public License (GPL); Public Domain",
            },
        ],
    )
    result = validate_licenses(report)
    assert result["packages"] == 2
    assert result["mixed_metadata_records"] == 1


@pytest.mark.parametrize(
    "license_name",
    ["AGPL-3.0-only", "SSPL-1.0", "GNU General Public License (GPL)"],
)
def test_license_gate_blocks_prohibited_metadata(tmp_path: Path, license_name: str) -> None:
    report = _licenses(
        tmp_path / "blocked.json",
        [{"Name": "blocked", "Version": "1.0", "License": license_name}],
    )
    with pytest.raises(QualityGateError, match="deny policy rejected"):
        validate_licenses(report)


def test_license_gate_rejects_unknown_and_duplicate_records(tmp_path: Path) -> None:
    with pytest.raises(QualityGateError, match="unknown license"):
        validate_licenses(
            _licenses(
                tmp_path / "unknown.json",
                [{"Name": "unknown", "Version": "1", "License": "UNKNOWN"}],
            )
        )
    duplicate = {"Name": "same", "Version": "1", "License": "MIT"}
    with pytest.raises(QualityGateError, match="duplicate package"):
        validate_licenses(_licenses(tmp_path / "duplicate.json", [duplicate, duplicate]))
