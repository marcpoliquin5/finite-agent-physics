from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

import agent_physics.live_load as live_load
from agent_physics.live_load import (
    LiveLoadInvariantError,
    build_live_load_contract,
    run_live_load,
    verify_live_load_evidence,
    write_live_load_evidence,
)
from agent_physics.serialization import canonical_json, content_digest


@pytest.fixture(scope="module")
def valid_live_evidence(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("live-load") / "evidence"
    run_live_load(
        output,
        concurrency=32,
        rounds=1,
        request_timeout_seconds=90.0,
    )
    return output


def _read(path: Path, name: str) -> dict[str, Any]:
    value = json.loads((path / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write(path: Path, name: str, value: object) -> None:
    (path / name).write_text(canonical_json(value) + "\n", encoding="utf-8", newline="\n")


def _refresh_manifest(path: Path, changed_name: str) -> None:
    manifest = _read(path, "manifest.json")
    changed = path / changed_name
    entry = next(item for item in manifest["files"] if item["name"] == changed_name)
    entry["bytes"] = changed.stat().st_size
    entry["sha256"] = live_load._sha256_bytes(changed.read_bytes())
    manifest.pop("manifest_digest")
    manifest["manifest_digest"] = content_digest(manifest)
    _write(path, "manifest.json", manifest)


def _rewrite_object(
    path: Path,
    name: str,
    mutate: Any,
    *,
    digest_field: str | None = None,
) -> None:
    value = _read(path, name)
    mutate(value)
    if digest_field is not None:
        value.pop(digest_field, None)
        value[digest_field] = content_digest(value)
    _write(path, name, value)
    _refresh_manifest(path, name)


def _rewrite_raw(path: Path, mutate: Any) -> None:
    raw_path = path / "raw-records.jsonl"
    lines = raw_path.read_text(encoding="utf-8").splitlines()
    mutate(lines)
    raw_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    _refresh_manifest(path, "raw-records.jsonl")


def _variant(valid: Path, tmp_path: Path, name: str) -> Path:
    destination = tmp_path / name
    shutil.copytree(valid, destination)
    return destination


def test_live_load_contract_is_bounded_digest_bound_and_rejects_invalid_designs() -> None:
    contract = build_live_load_contract(concurrency=32, rounds=1)

    assert contract["total_accepted_runs"] == 32
    assert contract["max_active_runs"] == 32
    assert contract["max_control_events_per_run"] == 4
    assert len(contract["control_sequence"]) == 4
    assert len(contract["contract_digest"]) == 64

    for concurrency in (True, 31, 129):
        with pytest.raises(LiveLoadInvariantError, match="concurrency"):
            build_live_load_contract(concurrency=concurrency, rounds=1)  # type: ignore[arg-type]
    for rounds in (True, 0, 129):
        with pytest.raises(LiveLoadInvariantError, match="rounds"):
            build_live_load_contract(concurrency=32, rounds=rounds)  # type: ignore[arg-type]
    with pytest.raises(LiveLoadInvariantError, match="capped"):
        build_live_load_contract(concurrency=128, rounds=33)
    for timeout in (True, float("nan"), 0.9, 301.0):
        with pytest.raises(LiveLoadInvariantError, match="request_timeout_seconds"):
            build_live_load_contract(
                concurrency=32,
                rounds=1,
                request_timeout_seconds=timeout,  # type: ignore[arg-type]
            )


def test_real_socket_32_way_load_limits_effect_isolation_and_call_free_replay(
    valid_live_evidence: Path,
) -> None:
    report = verify_live_load_evidence(valid_live_evidence)

    assert report["passed"] is True
    assert report["record_count"] == 32
    assert report["unexpected_error_count"] == 0
    assert report["active_run_limit"]["enforced"] is True
    assert report["control_event_limit"]["enforced"] is True
    assert report["effects"] == {
        "externally_committed_count": 0,
        "intent_count": 32,
        "proposed_count": 32,
        "unique_idempotency_key_count": 32,
        "unique_intent_count": 32,
    }
    assert report["adaptive_replay"]["passed_count"] == 32
    assert report["adaptive_replay"]["zero_call_count"] == 32
    assert report["adaptive_replay"]["post_terminal_replay_call_delta"] == 0
    assert report["completed_run_throughput_per_second"] > 0
    assert report["latency"]["submission"]["p95_ns"] > 0
    assert report["latency"]["end_to_end"]["p50_ns"] > 0

    serialized = "".join(
        path.read_text(encoding="utf-8") for path in valid_live_evidence.iterdir()
    )
    assert "Bearer " not in serialized
    assert "state_directory" not in serialized
    assert "ephemeral_tcp_port" not in serialized


def test_manifest_parser_and_byte_integrity_fail_closed(
    valid_live_evidence: Path,
    tmp_path: Path,
) -> None:
    path = _variant(valid_live_evidence, tmp_path, "schema")
    manifest = _read(path, "manifest.json")
    manifest["schema_version"] = "unsupported"
    _write(path, "manifest.json", manifest)
    with pytest.raises(LiveLoadInvariantError, match="unsupported"):
        verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "manifest-digest")
    manifest = _read(path, "manifest.json")
    manifest["manifest_digest"] = "0" * 64
    _write(path, "manifest.json", manifest)
    with pytest.raises(LiveLoadInvariantError, match="manifest digest"):
        verify_live_load_evidence(path)

    for name, replacement in (
        ("file-order", lambda files: list(reversed(files))),
        ("malformed-entry", lambda files: ["bad", *files[1:]]),
    ):
        path = _variant(valid_live_evidence, tmp_path, name)
        manifest = _read(path, "manifest.json")
        manifest["files"] = replacement(manifest["files"])
        manifest.pop("manifest_digest")
        manifest["manifest_digest"] = content_digest(manifest)
        _write(path, "manifest.json", manifest)
        with pytest.raises(LiveLoadInvariantError, match="file set or order"):
            verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "missing")
    (path / "contract.json").unlink()
    with pytest.raises(LiveLoadInvariantError, match="missing or unsafe"):
        verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "byte-digest")
    (path / "report.json").write_text("{}", encoding="utf-8")
    with pytest.raises(LiveLoadInvariantError, match="byte digest"):
        verify_live_load_evidence(path)


def test_json_parser_rejects_duplicates_nonfinite_nonobjects_and_missing_files(
    valid_live_evidence: Path,
    tmp_path: Path,
) -> None:
    path = _variant(valid_live_evidence, tmp_path, "duplicate")
    (path / "manifest.json").write_text(
        '{"schema_version":"a","schema_version":"b"}', encoding="utf-8"
    )
    with pytest.raises(LiveLoadInvariantError, match="duplicate field"):
        verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "nonfinite")
    (path / "manifest.json").write_text('{"unsafe":NaN}', encoding="utf-8")
    with pytest.raises(LiveLoadInvariantError, match="non-finite"):
        verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "nonobject")
    (path / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(LiveLoadInvariantError, match="one JSON object"):
        verify_live_load_evidence(path)

    with pytest.raises(LiveLoadInvariantError, match="could not parse"):
        verify_live_load_evidence(tmp_path / "absent")


def test_canonical_objects_and_frozen_cross_digests_fail_closed(
    valid_live_evidence: Path,
    tmp_path: Path,
) -> None:
    path = _variant(valid_live_evidence, tmp_path, "noncanonical")
    report = _read(path, "report.json")
    (path / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _refresh_manifest(path, "report.json")
    with pytest.raises(LiveLoadInvariantError, match="not canonical"):
        verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "contract-digest")
    _rewrite_object(path, "contract.json", lambda value: value.update(contract_digest="bad"))
    with pytest.raises(LiveLoadInvariantError, match="contract is unsupported or tampered"):
        verify_live_load_evidence(path)

    for name, field, value, expected in (
        ("contract-total", "total_accepted_runs", 31, "total is inconsistent"),
        ("contract-active", "max_active_runs", 31, "exact active-run cap"),
        ("contract-controls", "max_control_events_per_run", 3, "control-event cap"),
        ("contract-frozen", "benchmark_id", "changed", "current frozen design"),
    ):
        path = _variant(valid_live_evidence, tmp_path, name)
        _rewrite_object(
            path,
            "contract.json",
            lambda payload, field=field, value=value: payload.update({field: value}),
            digest_field="contract_digest",
        )
        with pytest.raises(LiveLoadInvariantError, match=expected):
            verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "environment-digest")
    _rewrite_object(
        path, "environment.json", lambda value: value.update(environment_digest="bad")
    )
    with pytest.raises(LiveLoadInvariantError, match="environment is unsupported"):
        verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "environment-contract")
    _rewrite_object(
        path,
        "environment.json",
        lambda value: value.update(contract_digest="other"),
        digest_field="environment_digest",
    )
    with pytest.raises(LiveLoadInvariantError, match="another contract"):
        verify_live_load_evidence(path)


def test_raw_receipts_fail_closed_on_syntax_canonical_digest_and_completeness(
    valid_live_evidence: Path,
    tmp_path: Path,
) -> None:
    path = _variant(valid_live_evidence, tmp_path, "raw-json")
    _rewrite_raw(path, lambda lines: lines.__setitem__(0, "{"))
    with pytest.raises(LiveLoadInvariantError, match="invalid JSON"):
        verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "raw-canonical")
    _rewrite_raw(path, lambda lines: lines.__setitem__(0, lines[0] + " "))
    with pytest.raises(LiveLoadInvariantError, match="non-canonical"):
        verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "raw-digest")

    def corrupt_record(lines: list[str]) -> None:
        record = json.loads(lines[0])
        record["terminal_state"] = "failed"
        lines[0] = canonical_json(record)

    _rewrite_raw(path, corrupt_record)
    with pytest.raises(LiveLoadInvariantError, match="tampered record"):
        verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "raw-incomplete")
    _rewrite_raw(path, lambda lines: lines.pop())
    with pytest.raises(LiveLoadInvariantError, match="incomplete or duplicated"):
        verify_live_load_evidence(path)


def test_report_and_evidence_semantic_gates_fail_closed(
    valid_live_evidence: Path,
    tmp_path: Path,
) -> None:
    cases = (
        ("report-digest", lambda value: value.update(report_digest="bad"), None, "report is"),
        (
            "record-set",
            lambda value: value.update(record_set_digest="bad"),
            "report_digest",
            "record-set digest",
        ),
        (
            "report-failed",
            lambda value: value.update(passed=False),
            "report_digest",
            "did not pass",
        ),
        (
            "accepted-count",
            lambda value: value.update(accepted_submission_count=31),
            "report_digest",
            "not every registered",
        ),
        (
            "active-cap",
            lambda value: value["active_run_limit"].update(enforced=False),
            "report_digest",
            "active-run admission",
        ),
        (
            "control-cap",
            lambda value: value["control_event_limit"].update(enforced=False),
            "report_digest",
            "control-event cap",
        ),
        (
            "effects",
            lambda value: value["effects"].update(externally_committed_count=1),
            "report_digest",
            "effect isolation",
        ),
        (
            "replay",
            lambda value: value["adaptive_replay"].update(zero_call_count=31),
            "report_digest",
            "call-free adaptive replay",
        ),
        (
            "report-binding",
            lambda value: value.update(contract_digest="other"),
            "report_digest",
            "cross-bound",
        ),
    )
    for name, mutate, digest_field, expected in cases:
        path = _variant(valid_live_evidence, tmp_path, name)
        _rewrite_object(path, "report.json", mutate, digest_field=digest_field)
        with pytest.raises(LiveLoadInvariantError, match=expected):
            verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "evidence-digest")
    _rewrite_object(
        path, "evidence.json", lambda value: value.update(evidence_digest="bad")
    )
    with pytest.raises(LiveLoadInvariantError, match="evidence object"):
        verify_live_load_evidence(path)

    path = _variant(valid_live_evidence, tmp_path, "evidence-disagrees")
    _rewrite_object(
        path,
        "evidence.json",
        lambda value: value.update(contract={}),
        digest_field="evidence_digest",
    )
    with pytest.raises(LiveLoadInvariantError, match="files disagree"):
        verify_live_load_evidence(path)


def test_helpers_environment_http_writer_and_cli_fail_closed(
    valid_live_evidence: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = live_load._HTTPResult(
        operation="test", status=400, duration_ns=1, body={"error": {"code": "bad"}}
    )
    assert result.receipt()["error_code"] == "bad"
    with pytest.raises(LiveLoadInvariantError, match="expected"):
        live_load._expect(result, status=200)
    with pytest.raises(LiveLoadInvariantError, match="omitted"):
        live_load._state({}, operation="test")
    with pytest.raises(LiveLoadInvariantError, match="observations"):
        live_load._percentile([], 0.5)
    with pytest.raises(LiveLoadInvariantError, match="observations"):
        live_load._latency_summary([])

    class Runtime:
        _workers: dict[str, object] = {}

    class App:
        runtime = Runtime()

    with pytest.raises(LiveLoadInvariantError, match="unavailable"):
        live_load._fixture_call_counts(App())  # type: ignore[arg-type]
    App.runtime._workers = {"task": object()}
    with pytest.raises(LiveLoadInvariantError, match="malformed"):
        live_load._fixture_call_counts(App())  # type: ignore[arg-type]

    monkeypatch.setattr(live_load.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    assert live_load._git_metadata() == (None, None)

    def missing_version(_: str) -> str:
        raise live_load.metadata.PackageNotFoundError

    monkeypatch.setattr(live_load.metadata, "version", missing_version)
    environment = live_load.capture_live_load_environment(
        build_live_load_contract(concurrency=32, rounds=1)
    )
    assert environment["uvicorn_version"] is None

    output_file = tmp_path / "not-a-directory"
    output_file.write_text("x", encoding="utf-8")
    evidence = _read(valid_live_evidence, "evidence.json")
    with pytest.raises(LiveLoadInvariantError, match="must be a directory"):
        write_live_load_evidence(evidence, output_file)

    report = _read(valid_live_evidence, "report.json")
    monkeypatch.setattr(live_load, "verify_live_load_evidence", lambda _: report)
    assert live_load.main(["--verify-only", str(valid_live_evidence)]) == 0
    assert '"passed":true' in capsys.readouterr().out
    monkeypatch.setattr(live_load, "run_live_load", lambda *args, **kwargs: report)
    assert live_load.main(["--output", str(tmp_path / "unused"), "--rounds", "1"]) == 0
    capsys.readouterr()

    def fail(_: object) -> dict[str, Any]:
        raise LiveLoadInvariantError("expected failure")

    monkeypatch.setattr(live_load, "verify_live_load_evidence", fail)
    assert live_load.main(["--verify-only", str(valid_live_evidence)]) == 1
    assert "expected failure" in capsys.readouterr().err


def test_http_response_parser_rejects_invalid_json_and_nonobjects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status = 200

        def __init__(self, raw: bytes) -> None:
            self.raw = raw

        def read(self) -> bytes:
            return self.raw

    class Connection:
        def __init__(self, raw: bytes) -> None:
            self.raw = raw

        def request(self, *args: object, **kwargs: object) -> None:
            return None

        def getresponse(self) -> Response:
            return Response(self.raw)

        def close(self) -> None:
            return None

    for raw, expected in ((b"not-json", "invalid JSON"), (b"[]", "non-object")):
        monkeypatch.setattr(
            live_load.http.client,
            "HTTPConnection",
            lambda *args, raw=raw, **kwargs: Connection(raw),
        )
        with pytest.raises(LiveLoadInvariantError, match=expected):
            live_load._request(
                1,
                "token",
                "parser-test",
                "GET",
                "/",
                timeout_seconds=1,
            )
