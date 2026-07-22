from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

import pytest

from agent_physics.contracts import EffectClass
from agent_physics.workflow_ir import (
    UNSUPPORTED_SCHEMA_FEATURES,
    WORKFLOW_SCHEMA_VERSION,
    WorkflowIRValidationError,
    compile_json,
    compile_contracts,
    compile_python,
    compile_workflow,
    compile_yaml,
)


def workflow_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "envelope": {
            "deadline_ms": 2_000,
            "max_tokens": 4_000,
            "max_cost_microusd": 25_000,
            "max_context_bytes": 64_000,
            "max_parallelism": 3,
            "min_modeled_success_probability": 0.8,
            "provider_limits": {"watsonx": 2, "local": 1},
        },
        "tasks": [
            {
                "task_id": "publish",
                "profiles": [
                    {
                        "name": "quality",
                        "provider": "watsonx",
                        "duration_ms_p50": 120,
                        "duration_ms_p95": 240,
                        "input_tokens": 300,
                        "output_tokens": 200,
                        "cost_microusd": 900,
                        "context_bytes": 2_048,
                        "quality": 0.98,
                        "failure_probability": 0.02,
                    },
                    {
                        "name": "fast",
                        "provider": "local",
                        "duration_ms_p50": 30,
                        "duration_ms_p95": 60,
                        "quality": 0.85,
                    },
                ],
                "dependencies": ["review", "collect"],
                "effect": {
                    "kind": "irreversible_write",
                    "resource": "public-alerts",
                    "requires_approval": True,
                    "idempotency_key": "alert/${run_id}",
                    "compensation": None,
                },
                "optional": False,
                "value": 9,
                "min_quality": 0.8,
                "deadline_ms": 1_900,
                "description": "Publish an approved alert.",
            },
            {
                "task_id": "review",
                "profiles": [
                    {
                        "name": "review",
                        "provider": "watsonx",
                        "duration_ms_p50": 50,
                        "duration_ms_p95": 100,
                    }
                ],
                "dependencies": ["collect"],
                "effect": {"kind": "pure"},
            },
            {
                "task_id": "collect",
                "profiles": [
                    {
                        "name": "reader",
                        "provider": "local",
                        "duration_ms_p50": 10,
                        "duration_ms_p95": 20,
                    }
                ],
                "effect": {"kind": "read", "resource": "incident-feed"},
            },
        ],
    }


EQUIVALENT_YAML = """
tasks:
  - effect:
      resource: public-alerts
      kind: irreversible_write
      idempotency_key: alert/${run_id}
      requires_approval: true
      compensation: null
    deadline_ms: 1900
    min_quality: .8
    value: 9.0
    description: Publish an approved alert.
    optional: false
    dependencies: [collect, review]
    profiles:
      - quality: .85
        duration_ms_p95: 60
        duration_ms_p50: 30
        provider: local
        name: fast
      - failure_probability: .02
        context_bytes: 2048
        cost_microusd: 900
        output_tokens: 200
        input_tokens: 300
        quality: .98
        duration_ms_p95: 240
        duration_ms_p50: 120
        provider: watsonx
        name: quality
    task_id: publish
  - task_id: collect
    effect: {kind: read, resource: incident-feed}
    profiles:
      - {provider: local, name: reader, duration_ms_p95: 20, duration_ms_p50: 10}
  - dependencies: [collect]
    profiles:
      - {duration_ms_p95: 100, name: review, provider: watsonx, duration_ms_p50: 50}
    effect: {kind: pure}
    task_id: review
envelope:
  provider_limits: {local: 1, watsonx: 2}
  max_parallelism: 3
  max_context_bytes: 64000
  max_cost_microusd: 25000
  max_tokens: 4000
  min_modeled_success_probability: .8
  deadline_ms: 2000
schema_version: 1
"""


def test_json_yaml_and_python_compile_to_identical_contracts_and_digest() -> None:
    python_result = compile_python(workflow_document())
    json_result = compile_json(json.dumps(workflow_document(), indent=2))
    yaml_result = compile_yaml(EQUIVALENT_YAML)

    assert python_result.graph == json_result.graph == yaml_result.graph
    assert python_result.envelope == json_result.envelope == yaml_result.envelope
    assert python_result.canonical_json == json_result.canonical_json == yaml_result.canonical_json
    assert python_result.digest == json_result.digest == yaml_result.digest
    assert (
        python_result.digest
        == hashlib.sha256(python_result.canonical_json.encode("utf-8")).hexdigest()
    )


def test_normalization_is_order_independent_and_preserves_exact_semantics() -> None:
    result = compile_python(workflow_document())

    assert tuple(task.task_id for task in result.graph.tasks) == (
        "collect",
        "publish",
        "review",
    )
    publish = result.graph.by_id["publish"]
    assert publish.dependencies == ("collect", "review")
    assert tuple((profile.name, profile.provider) for profile in publish.profiles) == (
        ("fast", "local"),
        ("quality", "watsonx"),
    )
    assert publish.effect.kind is EffectClass.IRREVERSIBLE_WRITE
    assert publish.effect.requires_approval is True
    assert publish.effect.idempotency_key == "alert/${run_id}"
    assert publish.deadline_ms == 1_900
    assert publish.value == 9.0
    assert result.envelope.provider_limits == (("local", 1), ("watsonx", 2))


def test_omitted_optional_fields_have_one_explicit_canonical_form() -> None:
    document = workflow_document()
    collect = document["tasks"][2]
    result = compile_python(document)
    normalized_collect = result.to_python()["tasks"][0]

    assert "dependencies" not in collect
    assert normalized_collect["dependencies"] == []
    assert normalized_collect["optional"] is False
    assert normalized_collect["value"] == 1.0
    assert normalized_collect["deadline_ms"] is None
    assert normalized_collect["profiles"][0]["input_tokens"] == 0
    assert normalized_collect["effect"]["requires_approval"] is False


def test_canonical_json_round_trip_is_exact_and_copy_is_detached() -> None:
    first = compile_python(workflow_document())
    detached = first.to_python()
    detached["tasks"][0]["description"] = "mutated"
    second = compile_json(first.canonical_json)

    assert second.canonical_json == first.canonical_json
    assert second.digest == first.digest
    assert second.graph == first.graph
    assert second.envelope == first.envelope
    assert first.to_python()["tasks"][0]["description"] != "mutated"
    assert first.canonical_json == json.dumps(
        first.to_python(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    assert "\n" not in first.canonical_json


def test_compile_workflow_auto_detects_json_and_yaml_and_accepts_utf8_bytes() -> None:
    expected = compile_python(workflow_document())
    assert compile_workflow(json.dumps(workflow_document())).digest == expected.digest
    assert compile_workflow(EQUIVALENT_YAML.encode()).digest == expected.digest


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("root", "future"),
        ("envelope", "burst_tokens"),
        ("task", "alternatives"),
        ("profile", "model_temperature"),
        ("effect", "authority_scope"),
    ],
)
def test_unknown_fields_fail_closed_at_every_schema_level(location: str, field: str) -> None:
    document = workflow_document()
    target: dict[str, Any]
    if location == "root":
        target = document
    elif location == "envelope":
        target = document["envelope"]
    elif location == "task":
        target = document["tasks"][0]
    elif location == "profile":
        target = document["tasks"][0]["profiles"][0]
    else:
        target = document["tasks"][0]["effect"]
    target[field] = "must not be ignored"

    with pytest.raises(WorkflowIRValidationError, match=f"unknown field.*{field}"):
        compile_python(document)


def test_schema_version_is_mandatory_strict_and_version_gated() -> None:
    missing = workflow_document()
    missing.pop("schema_version")
    with pytest.raises(WorkflowIRValidationError, match="missing required field.*schema_version"):
        compile_python(missing)

    for value in (True, 1.0, "1"):
        wrong_type = workflow_document()
        wrong_type["schema_version"] = value
        with pytest.raises(WorkflowIRValidationError, match="expected an integer"):
            compile_python(wrong_type)

    future = workflow_document()
    future["schema_version"] = WORKFLOW_SCHEMA_VERSION + 1
    with pytest.raises(WorkflowIRValidationError, match="unsupported version"):
        compile_python(future)


@pytest.mark.parametrize(
    "field",
    [
        "deadline_ms",
        "max_tokens",
        "max_cost_microusd",
        "max_context_bytes",
        "max_parallelism",
    ],
)
@pytest.mark.parametrize("invalid", [1.5, True])
def test_envelope_integer_resources_reject_floats_and_booleans(field: str, invalid: Any) -> None:
    document = workflow_document()
    document["envelope"][field] = invalid
    with pytest.raises(WorkflowIRValidationError, match=f"{field}.*expected an integer"):
        compile_python(document)


@pytest.mark.parametrize(
    "field",
    [
        "duration_ms_p50",
        "duration_ms_p95",
        "input_tokens",
        "output_tokens",
        "cost_microusd",
        "context_bytes",
    ],
)
def test_profile_integer_resources_reject_floats(field: str) -> None:
    document = workflow_document()
    document["tasks"][0]["profiles"][0][field] = 1.25
    with pytest.raises(WorkflowIRValidationError, match=f"{field}.*expected an integer"):
        compile_python(document)


def test_task_deadline_and_provider_capacity_are_strict_integers() -> None:
    task_float = workflow_document()
    task_float["tasks"][0]["deadline_ms"] = 1_000.0
    with pytest.raises(WorkflowIRValidationError, match="deadline_ms.*expected an integer"):
        compile_python(task_float)

    provider_float = workflow_document()
    provider_float["envelope"]["provider_limits"]["local"] = 1.0
    with pytest.raises(WorkflowIRValidationError, match="local.*expected an integer"):
        compile_python(provider_float)


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("envelope", "min_modeled_success_probability"),
        ("task", "value"),
        ("task", "min_quality"),
        ("profile", "quality"),
        ("profile", "failure_probability"),
    ],
)
def test_nonfinite_numbers_are_rejected(location: str, field: str, invalid: float) -> None:
    document = workflow_document()
    if location == "envelope":
        document["envelope"][field] = invalid
    elif location == "task":
        document["tasks"][0][field] = invalid
    else:
        document["tasks"][0]["profiles"][0][field] = invalid
    with pytest.raises(WorkflowIRValidationError, match="expected a finite number"):
        compile_python(document)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_json_nonfinite_extensions_are_rejected(constant: str) -> None:
    text = json.dumps(workflow_document()).replace("0.8", constant, 1)
    with pytest.raises(WorkflowIRValidationError, match="not finite"):
        compile_json(text)


def test_yaml_nonfinite_number_is_rejected_after_safe_loading() -> None:
    text = EQUIVALENT_YAML.replace(
        "min_modeled_success_probability: .8", "min_modeled_success_probability: .nan"
    )
    with pytest.raises(WorkflowIRValidationError, match="expected a finite number"):
        compile_yaml(text)


@pytest.mark.parametrize(
    "text",
    [
        '{"schema_version":1,"schema_version":1,"envelope":{},"tasks":[]}',
        '{"schema_version":1,"envelope":{"deadline_ms":1,"deadline_ms":2},"tasks":[]}',
    ],
)
def test_json_duplicate_keys_are_rejected_at_any_depth(text: str) -> None:
    with pytest.raises(WorkflowIRValidationError, match="duplicate key"):
        compile_json(text)


@pytest.mark.parametrize(
    "text",
    [
        "schema_version: 1\nschema_version: 1\nenvelope: {}\ntasks: []\n",
        "schema_version: 1\nenvelope:\n  deadline_ms: 1\n  deadline_ms: 2\ntasks: []\n",
    ],
)
def test_yaml_duplicate_keys_are_rejected_at_any_depth(text: str) -> None:
    with pytest.raises(WorkflowIRValidationError, match="duplicate key"):
        compile_yaml(text)


def test_yaml_uses_a_safe_loader() -> None:
    malicious = "!!python/object/apply:os.system ['echo unsafe']"
    with pytest.raises(WorkflowIRValidationError, match="invalid YAML"):
        compile_yaml(malicious)


def test_effect_contract_is_not_weakened_during_compilation() -> None:
    invalid = workflow_document()
    effect = invalid["tasks"][0]["effect"]
    effect["requires_approval"] = False
    effect["idempotency_key"] = None
    with pytest.raises(
        WorkflowIRValidationError,
        match="idempotency key.*irreversible writes must declare an approval gate",
    ):
        compile_python(invalid)

    invalid_kind = workflow_document()
    invalid_kind["tasks"][0]["effect"]["kind"] = "best_effort_write"
    with pytest.raises(WorkflowIRValidationError, match="expected one of"):
        compile_python(invalid_kind)


def test_graph_profile_and_envelope_invariants_are_not_weakened() -> None:
    cycle = workflow_document()
    cycle["tasks"][2]["dependencies"] = ["publish"]
    with pytest.raises(WorkflowIRValidationError, match="cycle"):
        compile_python(cycle)

    duplicate_profile = workflow_document()
    duplicate_profile["tasks"][0]["profiles"].append(
        copy.deepcopy(duplicate_profile["tasks"][0]["profiles"][0])
    )
    with pytest.raises(WorkflowIRValidationError, match="backend identities must be unique"):
        compile_python(duplicate_profile)

    invalid_envelope = workflow_document()
    invalid_envelope["envelope"]["max_parallelism"] = 0
    with pytest.raises(WorkflowIRValidationError, match="max_parallelism must be positive"):
        compile_python(invalid_envelope)


def test_duplicate_dependencies_are_rejected_instead_of_silently_deduplicated() -> None:
    document = workflow_document()
    document["tasks"][0]["dependencies"] = ["collect", "collect"]
    with pytest.raises(WorkflowIRValidationError, match="dependencies.*unique"):
        compile_python(document)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document["tasks"][0].__setitem__("optional", 1),
        lambda document: document["tasks"][0].__setitem__("description", None),
        lambda document: document["tasks"][0]["effect"].__setitem__("requires_approval", "yes"),
        lambda document: document["envelope"].__setitem__("provider_limits", []),
        lambda document: document.__setitem__(1, "non-string key"),
    ],
)
def test_schema_rejects_type_coercion_and_non_string_object_keys(mutation: Any) -> None:
    document = workflow_document()
    mutation(document)
    with pytest.raises(WorkflowIRValidationError):
        compile_python(document)


def test_invalid_source_media_and_encoding_fail_explicitly() -> None:
    with pytest.raises(WorkflowIRValidationError, match="mapping, text, or UTF-8 bytes"):
        compile_workflow(42)  # type: ignore[arg-type]
    with pytest.raises(WorkflowIRValidationError, match="valid UTF-8"):
        compile_json(b"\xff")
    with pytest.raises(WorkflowIRValidationError, match="python source must be a mapping"):
        compile_workflow("{}", source_format="python")
    with pytest.raises(WorkflowIRValidationError, match="unsupported source format"):
        compile_workflow("{}", source_format="toml")  # type: ignore[arg-type]
    with pytest.raises(WorkflowIRValidationError, match="json source must be text"):
        compile_workflow(workflow_document(), source_format="json")


def test_malformed_documents_and_arrays_fail_with_schema_errors() -> None:
    with pytest.raises(WorkflowIRValidationError, match="invalid JSON"):
        compile_json("{")
    with pytest.raises(WorkflowIRValidationError, match="expected an object"):
        compile_json("[]")

    tasks_object = workflow_document()
    tasks_object["tasks"] = {}
    with pytest.raises(WorkflowIRValidationError, match="tasks.*expected an array"):
        compile_python(tasks_object)

    profiles_object = workflow_document()
    profiles_object["tasks"][0]["profiles"] = {}
    with pytest.raises(WorkflowIRValidationError, match="profiles.*expected an array"):
        compile_python(profiles_object)


def test_extreme_integer_in_float_semantic_field_fails_as_nonfinite() -> None:
    document = workflow_document()
    document["tasks"][0]["value"] = 10**10_000
    with pytest.raises(WorkflowIRValidationError, match="expected a finite number"):
        compile_python(document)


def test_schema_limitations_are_explicit_and_fail_closed() -> None:
    assert UNSUPPORTED_SCHEMA_FEATURES == (
        "alternative branches",
        "speculative execution",
        "typed artifact ports",
    )
    for field in ("alternatives", "speculation", "artifact_ports"):
        document = workflow_document()
        document["tasks"][0][field] = []
        with pytest.raises(WorkflowIRValidationError, match="unknown field"):
            compile_python(document)


def _typed_workflow_document() -> dict[str, Any]:
    document = workflow_document()
    document["schema_version"] = 2
    collect = next(task for task in document["tasks"] if task["task_id"] == "collect")
    review = next(task for task in document["tasks"] if task["task_id"] == "review")
    collect["output_ports"] = [
        {
            "name": "incident",
            "schema": "stormshift.incident",
            "schema_version": "1.0.0",
            "media_type": "application/json",
        }
    ]
    review["input_ports"] = [
        {
            "name": "incident",
            "source_task_id": "collect",
            "source_port": "incident",
            "schema": "stormshift.incident",
            "schema_version": "1.0.0",
            "media_type": "application/json",
        }
    ]
    return document


def test_schema_v2_binds_exact_versioned_artifact_ports() -> None:
    compiled = compile_python(_typed_workflow_document())
    review = compiled.graph.by_id["review"]
    collect = compiled.graph.by_id["collect"]

    assert compiled.schema_version == 2
    assert review.input_ports[0].source_task_id == "collect"
    assert review.input_ports[0].schema_version == "1.0.0"
    assert collect.output_ports[0].media_type == "application/json"
    normalized = compiled.to_python()
    normalized_review = next(task for task in normalized["tasks"] if task["task_id"] == "review")
    assert normalized_review["input_ports"][0]["source_port"] == "incident"


def test_in_memory_contracts_round_trip_through_latest_strict_interchange() -> None:
    original = compile_python(_typed_workflow_document())

    regenerated = compile_contracts(original.graph, original.envelope)

    assert regenerated.schema_version == WORKFLOW_SCHEMA_VERSION
    assert regenerated.graph == original.graph
    assert regenerated.envelope == original.envelope
    assert regenerated.digest == original.digest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "stormshift.other"),
        ("schema_version", "2.0.0"),
        ("media_type", "text/plain"),
    ],
)
def test_schema_v2_rejects_incompatible_producer_contract(field: str, value: str) -> None:
    document = _typed_workflow_document()
    review = next(task for task in document["tasks"] if task["task_id"] == "review")
    review["input_ports"][0][field] = value

    with pytest.raises(WorkflowIRValidationError, match="incompatible"):
        compile_python(document)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_task_id", "missing", "missing producer"),
        ("source_port", "missing", "missing producer port"),
        ("source_task_id", "publish", "must be a direct dependency"),
    ],
)
def test_schema_v2_rejects_missing_or_unreachable_port_producers(
    field: str, value: str, message: str
) -> None:
    document = _typed_workflow_document()
    review = next(task for task in document["tasks"] if task["task_id"] == "review")
    review["input_ports"][0][field] = value

    with pytest.raises(WorkflowIRValidationError, match=message):
        compile_python(document)


def test_schema_v2_rejects_duplicate_port_names_and_v1_rejects_port_fields() -> None:
    duplicate = _typed_workflow_document()
    collect = next(task for task in duplicate["tasks"] if task["task_id"] == "collect")
    collect["output_ports"].append(copy.deepcopy(collect["output_ports"][0]))
    with pytest.raises(WorkflowIRValidationError, match="output port names must be unique"):
        compile_python(duplicate)

    legacy = _typed_workflow_document()
    legacy["schema_version"] = 1
    with pytest.raises(WorkflowIRValidationError, match="unknown field"):
        compile_python(legacy)


def test_schema_v2_preserves_typed_physical_resource_units() -> None:
    document = _typed_workflow_document()
    document["envelope"].update(
        {
            "max_cpu_time_ms": 10_000,
            "max_peak_memory_bytes": 1_000_000,
            "max_peak_vram_bytes": 2_000_000,
            "max_storage_read_bytes": 3_000_000,
            "max_storage_write_bytes": 4_000_000,
            "max_network_ingress_bytes": 5_000_000,
            "max_network_egress_bytes": 6_000_000,
            "available_bandwidth_bps": 10_000_000,
            "max_network_rtt_ms": 100,
            "max_egress_cost_microusd": 7_000,
        }
    )
    profile = document["tasks"][0]["profiles"][0]
    profile_identity = (profile["provider"], profile["name"])
    profile.update(
        {
            "cpu_time_ms": 50,
            "peak_memory_bytes": 100_000,
            "peak_vram_bytes": 200_000,
            "storage_read_bytes": 300_000,
            "storage_write_bytes": 400_000,
            "network_ingress_bytes": 500_000,
            "network_egress_bytes": 600_000,
            "min_bandwidth_bps": 1_000_000,
            "network_rtt_ms": 20,
            "egress_cost_microusd": 700,
        }
    )

    compiled = compile_python(document)
    selected = next(
        candidate
        for candidate in compiled.graph.by_id[str(document["tasks"][0]["task_id"])].profiles
        if (candidate.provider, candidate.name) == profile_identity
    )
    assert selected.cpu_time_ms == 50
    assert selected.peak_vram_bytes == 200_000
    assert selected.network_egress_bytes == 600_000
    assert compiled.envelope.available_bandwidth_bps == 10_000_000
    assert compiled.to_python()["envelope"]["max_network_rtt_ms"] == 100


def test_physical_fields_are_v2_only_and_reject_boolean_integer_smuggling() -> None:
    legacy = workflow_document()
    legacy["envelope"]["max_cpu_time_ms"] = 10
    with pytest.raises(WorkflowIRValidationError, match="unknown field"):
        compile_python(legacy)

    versioned = _typed_workflow_document()
    versioned["tasks"][0]["profiles"][0]["network_rtt_ms"] = True
    with pytest.raises(WorkflowIRValidationError, match="expected an integer"):
        compile_python(versioned)
