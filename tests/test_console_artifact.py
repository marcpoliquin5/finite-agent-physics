from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.export_console_artifact import build_payload


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "apps" / "physics-console" / "app" / "demo-artifact.json"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def test_console_artifact_is_current_digest_bound_kernel_output() -> None:
    envelope = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    canonical_payload = envelope["canonical_payload"]
    payload = json.loads(canonical_payload)

    assert envelope["schema_version"] == "finite-console-artifact/v1"
    assert envelope["digest_algorithm"] == "sha256"
    assert envelope["sha256"] == hashlib.sha256(
        canonical_payload.encode("utf-8")
    ).hexdigest()
    assert canonical_payload == _canonical_json(build_payload())
    assert sum(len(states) for states in payload["decisions"].values()) == 1_080
    experiment = payload["registered_fault_experiment"]
    assert experiment["raw_record_count"] == 450
    assert experiment["paired_seed_count"] == 30
    assert experiment["condition_count"] == 5
    assert experiment["policy_count"] == 3
    assert experiment["revision_provenance"] == "caller-supplied-unverified"


def test_console_artifact_keeps_simulation_and_refusal_boundaries_explicit() -> None:
    payload = json.loads(
        json.loads(ARTIFACT.read_text(encoding="utf-8"))["canonical_payload"]
    )

    assert payload["measurement_kind"] == "deterministic-simulation"
    assert payload["claim_status"] == "descriptive-only"
    assert payload["fictional_fixture"] is True
    assert payload["external_systems_called"] is False
    for decisions in payload["decisions"].values():
        refused = decisions["5000:5000"]
        assert refused["status"] == "refused"
        assert refused["failure_reason"]

    effect = payload["independent_effect_drill"]
    assert effect["measurement_kind"] == "simulated-effect-target"
    assert effect["physical_apply_count"] == 1
    assert effect["external_effects_possible"] is False
