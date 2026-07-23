"""Fail-closed validation for machine-readable test and coverage evidence."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

MAX_EVIDENCE_BYTES = 100 * 1024 * 1024
_BANDIT_SEVERITIES = frozenset({"UNDEFINED", "LOW", "MEDIUM", "HIGH"})
_UNKNOWN_LICENSES = frozenset({"", "N/A", "NOASSERTION", "NONE", "UNKNOWN"})
_ALWAYS_PROHIBITED_LICENSE_MARKERS = (
    "AFFERO GENERAL PUBLIC LICENSE",
    "AGPL",
    "BUSINESS SOURCE LICENSE",
    "COMMONS CLAUSE",
    "ELASTIC LICENSE",
    "SERVER SIDE PUBLIC LICENSE",
    "SSPL",
)
_GPL_RE = re.compile(r"(?:\bGNU GENERAL PUBLIC LICENSE\b|\bGPL(?:[- V]?[0-9])?\b)")
_PERMISSIVE_ALTERNATIVE_MARKERS = (
    "APACHE",
    "BSD",
    "MIT",
    "MOZILLA",
    "MPL",
    "PSF",
    "PUBLIC DOMAIN",
    "PYTHON SOFTWARE FOUNDATION",
)


class QualityGateError(ValueError):
    """Raised when quality evidence is missing, ambiguous, or fails policy."""


def _read_bounded(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise QualityGateError(f"evidence must be one regular non-link file: {path}")
    size = path.stat().st_size
    if size <= 0 or size > MAX_EVIDENCE_BYTES:
        raise QualityGateError(
            f"evidence size must be from 1 through {MAX_EVIDENCE_BYTES} bytes"
        )
    return path.read_bytes()


def _nonnegative_integer(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise QualityGateError(f"{field} must be a non-negative integer")
    return value


def validate_junit(path: Path) -> dict[str, Any]:
    """Require at least one test and zero failures, errors, skips, or xfails."""

    payload = _read_bounded(path)
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise QualityGateError("JUnit evidence must not contain a DTD or entity declaration")
    try:
        root = DefusedET.fromstring(payload)
    except (DefusedET.ParseError, DefusedXmlException) as exc:
        raise QualityGateError(f"JUnit XML is malformed: {exc}") from exc
    if root.tag not in {"testsuite", "testsuites"}:
        raise QualityGateError("JUnit root must be testsuite or testsuites")

    cases = list(root.iter("testcase"))
    if not cases:
        raise QualityGateError("JUnit evidence contains no test cases")
    failures = sum(len(case.findall("failure")) for case in cases)
    errors = sum(len(case.findall("error")) for case in cases)
    skipped_nodes = [node for case in cases for node in case.findall("skipped")]
    skipped = len(skipped_nodes)
    xfailed = sum(node.attrib.get("type") == "pytest.xfail" for node in skipped_nodes)

    disabled = 0
    declared_tests = 0
    declared_failures = 0
    declared_errors = 0
    declared_skipped = 0
    leaf_suites = 0
    for suite in root.iter("testsuite"):
        for field in ("errors", "failures", "skipped", "tests"):
            raw = suite.attrib.get(field, "0")
            try:
                parsed = int(raw)
            except ValueError as exc:
                raise QualityGateError(f"JUnit testsuite {field} must be an integer") from exc
            if parsed < 0:
                raise QualityGateError(f"JUnit testsuite {field} must be non-negative")
        raw_disabled = suite.attrib.get("disabled", "0")
        try:
            suite_disabled = int(raw_disabled)
        except ValueError as exc:
            raise QualityGateError("JUnit testsuite disabled must be an integer") from exc
        if suite_disabled < 0:
            raise QualityGateError("JUnit testsuite disabled must be non-negative")
        disabled += suite_disabled
        if not list(suite.findall("testsuite")):
            declared_tests += int(suite.attrib.get("tests", "0"))
            declared_failures += int(suite.attrib.get("failures", "0"))
            declared_errors += int(suite.attrib.get("errors", "0"))
            declared_skipped += int(suite.attrib.get("skipped", "0"))
            leaf_suites += 1

    if leaf_suites and declared_tests != len(cases):
        raise QualityGateError(
            f"JUnit declared {declared_tests} tests but contains {len(cases)} cases"
        )
    if leaf_suites and (declared_failures, declared_errors, declared_skipped) != (
        failures,
        errors,
        skipped,
    ):
        raise QualityGateError(
            "JUnit declared outcome counts do not match testcase elements; "
            f"declared={(declared_failures, declared_errors, declared_skipped)!r}, "
            f"observed={(failures, errors, skipped)!r}"
        )
    if failures or errors or skipped or disabled:
        raise QualityGateError(
            "JUnit policy requires zero failures, errors, skips/xfails, and disabled tests; "
            f"observed failures={failures}, errors={errors}, skipped={skipped}, "
            f"xfailed={xfailed}, disabled={disabled}"
        )
    return {
        "schema_version": "finite-junit-gate/v1",
        "status": "passed",
        "testcases": len(cases),
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "xfailed": xfailed,
        "disabled": disabled,
        "runner_policy": (
            "pytest must enable xfail_strict; non-pytest runners require a companion "
            "runner-specific nonpass-state gate"
        ),
    }


def _percentage_floor(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualityGateError(f"{field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 <= parsed <= 100:
        raise QualityGateError(f"{field} must be finite and from 0 through 100")
    return parsed


def validate_coverage(
    path: Path,
    *,
    statement_floor: float,
    branch_floor: float,
) -> dict[str, Any]:
    """Require branch measurement and enforce explicit statement and branch floors."""

    statement_minimum = _percentage_floor(statement_floor, field="statement_floor")
    branch_minimum = _percentage_floor(branch_floor, field="branch_floor")
    try:
        document = json.loads(_read_bounded(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualityGateError(f"coverage JSON is malformed: {exc}") from exc
    if type(document) is not dict:
        raise QualityGateError("coverage evidence must be a JSON object")
    meta = document.get("meta")
    totals = document.get("totals")
    if type(meta) is not dict or type(totals) is not dict:
        raise QualityGateError("coverage evidence must contain meta and totals objects")
    if meta.get("branch_coverage") is not True:
        raise QualityGateError("coverage run did not enable branch measurement")

    statements = _nonnegative_integer(totals.get("num_statements"), field="num_statements")
    covered_lines = _nonnegative_integer(totals.get("covered_lines"), field="covered_lines")
    branches = _nonnegative_integer(totals.get("num_branches"), field="num_branches")
    covered_branches = _nonnegative_integer(
        totals.get("covered_branches"), field="covered_branches"
    )
    if statements == 0 or branches == 0:
        raise QualityGateError("coverage evidence must contain measured statements and branches")
    if covered_lines > statements or covered_branches > branches:
        raise QualityGateError("covered counts cannot exceed measured counts")

    statement_percent = 100.0 * covered_lines / statements
    branch_percent = 100.0 * covered_branches / branches
    if statement_percent + 1e-12 < statement_minimum:
        raise QualityGateError(
            f"statement coverage {statement_percent:.6f}% is below {statement_minimum:.6f}%"
        )
    if branch_percent + 1e-12 < branch_minimum:
        raise QualityGateError(
            f"branch coverage {branch_percent:.6f}% is below {branch_minimum:.6f}%"
        )
    return {
        "schema_version": "finite-coverage-gate/v1",
        "status": "passed",
        "statements": statements,
        "covered_statements": covered_lines,
        "statement_percent": round(statement_percent, 6),
        "statement_floor_percent": statement_minimum,
        "branches": branches,
        "covered_branches": covered_branches,
        "branch_percent": round(branch_percent, 6),
        "branch_floor_percent": branch_minimum,
    }


def validate_bandit(path: Path) -> dict[str, Any]:
    """Require a complete Bandit report with no scanner errors or medium/high findings."""

    try:
        document = json.loads(_read_bounded(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualityGateError(f"Bandit JSON is malformed: {exc}") from exc
    if type(document) is not dict:
        raise QualityGateError("Bandit evidence must be a JSON object")
    errors = document.get("errors")
    results = document.get("results")
    metrics = document.get("metrics")
    if type(errors) is not list or type(results) is not list or type(metrics) is not dict:
        raise QualityGateError("Bandit evidence must contain errors, results, and metrics")
    if errors:
        raise QualityGateError(f"Bandit reported {len(errors)} scanner errors")
    totals = metrics.get("_totals")
    if type(totals) is not dict:
        raise QualityGateError("Bandit evidence has no aggregate metrics")
    source_lines = _nonnegative_integer(totals.get("loc"), field="Bandit loc")
    if source_lines == 0:
        raise QualityGateError("Bandit evidence contains no scanned source lines")
    skipped_tests = _nonnegative_integer(
        totals.get("skipped_tests"), field="Bandit skipped_tests"
    )
    if skipped_tests:
        raise QualityGateError(f"Bandit skipped {skipped_tests} tests")

    observed = {severity: 0 for severity in _BANDIT_SEVERITIES}
    for index, issue in enumerate(results):
        if type(issue) is not dict:
            raise QualityGateError(f"Bandit result {index} must be an object")
        severity = issue.get("issue_severity")
        if severity not in _BANDIT_SEVERITIES:
            raise QualityGateError(f"Bandit result {index} has an invalid severity")
        observed[severity] += 1
        for field in ("filename", "issue_text", "test_id"):
            if not isinstance(issue.get(field), str) or not issue[field]:
                raise QualityGateError(f"Bandit result {index} has no {field}")

    for severity, count in observed.items():
        declared = _nonnegative_integer(
            totals.get(f"SEVERITY.{severity}"),
            field=f"Bandit SEVERITY.{severity}",
        )
        if declared != count:
            raise QualityGateError(
                f"Bandit declared {declared} {severity} findings but contains {count}"
            )
    blocked = observed["MEDIUM"] + observed["HIGH"]
    if blocked:
        raise QualityGateError(
            "Bandit policy requires zero medium/high findings; "
            f"observed medium={observed['MEDIUM']}, high={observed['HIGH']}"
        )
    return {
        "schema_version": "finite-bandit-gate/v1",
        "status": "passed",
        "source_lines": source_lines,
        "findings": len(results),
        "low": observed["LOW"],
        "medium": observed["MEDIUM"],
        "high": observed["HIGH"],
        "scanner_errors": 0,
        "skipped_tests": 0,
    }


def _prohibited_license(license_name: str) -> bool:
    normalized = " ".join(license_name.upper().split())
    if any(marker in normalized for marker in _ALWAYS_PROHIBITED_LICENSE_MARKERS):
        return True
    if not _GPL_RE.search(normalized):
        return False
    return not any(marker in normalized for marker in _PERMISSIVE_ALTERNATIVE_MARKERS)


def validate_licenses(path: Path) -> dict[str, Any]:
    """Apply FINITE's metadata-level dependency license deny policy."""

    try:
        document = json.loads(_read_bounded(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualityGateError(f"license JSON is malformed: {exc}") from exc
    if type(document) is not list or not document:
        raise QualityGateError("license evidence must be a non-empty JSON array")

    seen: set[tuple[str, str]] = set()
    prohibited: list[str] = []
    mixed_metadata = 0
    for index, package in enumerate(document):
        if type(package) is not dict:
            raise QualityGateError(f"license record {index} must be an object")
        fields: dict[str, str] = {}
        for field in ("License", "Name", "Version"):
            value = package.get(field)
            if not isinstance(value, str) or not value.strip():
                raise QualityGateError(f"license record {index} has no {field}")
            fields[field] = value.strip()
        identity = (fields["Name"].casefold(), fields["Version"])
        if identity in seen:
            raise QualityGateError(
                f"license evidence contains duplicate package {fields['Name']} {fields['Version']}"
            )
        seen.add(identity)
        normalized_license = " ".join(fields["License"].upper().split())
        if normalized_license in _UNKNOWN_LICENSES:
            raise QualityGateError(
                f"package {fields['Name']} {fields['Version']} has an unknown license"
            )
        if _prohibited_license(fields["License"]):
            prohibited.append(
                f"{fields['Name']} {fields['Version']} ({fields['License']})"
            )
        if _GPL_RE.search(normalized_license) and not _prohibited_license(fields["License"]):
            mixed_metadata += 1
    if prohibited:
        raise QualityGateError(
            "dependency license deny policy rejected: " + "; ".join(sorted(prohibited))
        )
    return {
        "schema_version": "finite-license-gate/v1",
        "status": "passed",
        "packages": len(document),
        "prohibited": 0,
        "unknown": 0,
        "mixed_metadata_records": mixed_metadata,
        "scope": "installed-distribution metadata; not legal advice",
    }


__all__ = [
    "QualityGateError",
    "validate_bandit",
    "validate_coverage",
    "validate_junit",
    "validate_licenses",
]
