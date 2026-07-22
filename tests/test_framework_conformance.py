from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from agent_physics.contracts import EffectClass
from agent_physics.framework_conformance import (
    LANGGRAPH_TARGET,
    NEUTRAL_TARGET,
    PINNED_LANGGRAPH_CHECKPOINT_VERSION,
    PINNED_LANGGRAPH_VERSION,
    TAINTED_BROWSER_OBSERVATION,
    CachePolicy,
    FrameworkConformanceError,
    WrapperRuntimePolicy,
    build_reference_page_action_contract,
    finite_to_wrapper,
    langgraph_conformance_available,
    run_pinned_langgraph_conformance_witness,
    validate_page_action_contract,
    validate_wrapper_manifest,
    wrapper_to_finite,
)
from agent_physics.serialization import content_digest
from agent_physics.workflow_ir import compile_python


def _workflow_document() -> dict[str, object]:
    return {
        "schema_version": 2,
        "envelope": {
            "deadline_ms": 5_000,
            "max_tokens": 3_000,
            "max_cost_microusd": 9_000,
            "max_context_bytes": 32_000,
            "max_parallelism": 2,
            "min_modeled_success_probability": 0.9,
            "provider_limits": {"watsonx": 1, "local": 2},
        },
        "tasks": [
            {
                "task_id": "collect",
                "profiles": [
                    {
                        "name": "local-reader",
                        "provider": "local",
                        "duration_ms_p50": 10,
                        "duration_ms_p95": 20,
                        "quality": 0.9,
                    }
                ],
                "effect": {"kind": "read", "resource": "fixture"},
                "output_ports": [
                    {
                        "name": "facts",
                        "schema": "stormshift-facts",
                        "schema_version": "1",
                        "media_type": "application/json",
                    }
                ],
                "adapter_requirements": {
                    "cancellation": "cooperative",
                    "checkpoint": "receipt",
                    "streaming": False,
                    "usage": "provider_reported",
                    "effect_fencing": False,
                    "max_hidden_retries": 0,
                },
            },
            {
                "task_id": "publish",
                "profiles": [
                    {
                        "name": "granite",
                        "provider": "watsonx",
                        "duration_ms_p50": 50,
                        "duration_ms_p95": 100,
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cost_microusd": 500,
                        "context_bytes": 2_000,
                        "quality": 0.98,
                    },
                    {
                        "name": "fallback",
                        "provider": "local",
                        "duration_ms_p50": 25,
                        "duration_ms_p95": 50,
                        "quality": 0.85,
                    },
                ],
                "dependencies": ["collect"],
                "input_ports": [
                    {
                        "name": "facts",
                        "source_task_id": "collect",
                        "source_port": "facts",
                        "schema": "stormshift-facts",
                        "schema_version": "1",
                        "media_type": "application/json",
                    }
                ],
                "effect": {
                    "kind": "irreversible_write",
                    "resource": "simulation-preview",
                    "requires_approval": True,
                    "idempotency_key": "preview/${run_id}",
                },
                "min_quality": 0.8,
                "adapter_requirements": {
                    "cancellation": "cooperative",
                    "checkpoint": "receipt",
                    "streaming": False,
                    "usage": "provider_reported",
                    "effect_fencing": True,
                    "max_hidden_retries": 0,
                },
            },
        ],
    }


def _bindings() -> dict[str, tuple[str, ...]]:
    return {
        "collect": ("validate-fixture-read/v1",),
        "publish": ("validate-preview-intent/v1", "validate-no-external-send/v1"),
    }


def _reseal_manifest(manifest):  # type: ignore[no-untyped-def]
    unsigned = replace(manifest, manifest_digest="").unsigned_payload()
    return replace(manifest, manifest_digest=content_digest(unsigned))


def _reseal_page_contract(contract):  # type: ignore[no-untyped-def]
    unsigned = replace(contract, contract_digest="").unsigned_payload()
    return replace(contract, contract_digest=content_digest(unsigned))


def test_neutral_manifest_round_trip_preserves_every_finite_contract_and_wrapper_policy() -> None:
    workflow = compile_python(_workflow_document())
    cache = CachePolicy(
        "content_addressed_readonly",
        ("workflow_digest", "task_id", "dependency_output_digests"),
    )
    runtime = WrapperRuntimePolicy(2, 0, "resumable", "fenced_commit")
    selections = {
        "collect": ("local", "local-reader"),
        "publish": ("watsonx", "granite"),
    }

    manifest = finite_to_wrapper(
        workflow,
        target=NEUTRAL_TARGET,
        selected_profiles=selections,
        validator_bindings=_bindings(),
        cache_policy=cache,
        runtime_policy=runtime,
    )
    projection = wrapper_to_finite(manifest)

    assert manifest.verify_digest()
    assert validate_wrapper_manifest(manifest) == ()
    assert manifest.semantic_losses == ()
    assert projection.workflow.digest == workflow.digest
    assert projection.workflow.graph == workflow.graph
    assert projection.workflow.envelope == workflow.envelope
    assert dict(projection.validator_bindings) == _bindings()
    assert projection.cache_policy == cache
    assert projection.runtime_policy == runtime
    assert {
        item.task_id: (item.provider, item.profile_name) for item in projection.selected_profiles
    } == selections
    assert {item.status for item in manifest.feature_accounting} == {"represented"}


def test_default_profile_selection_is_deterministic_and_quality_qualified() -> None:
    workflow = compile_python(_workflow_document())

    first = finite_to_wrapper(
        workflow,
        validator_bindings=_bindings(),
        cache_policy=CachePolicy("disabled"),
    )
    replay = finite_to_wrapper(
        workflow,
        validator_bindings=_bindings(),
        cache_policy=CachePolicy("disabled"),
    )

    assert first == replay
    selections = {node.task_id: node.selected_profile for node in first.nodes}
    assert selections["publish"].profile_name == "granite"
    assert all(
        item.selection_rule == "highest_quality_qualified_stable_tiebreak"
        for item in selections.values()
    )
    for node in first.nodes:
        profile = next(
            item
            for item in node.profiles
            if (item.provider, item.name)
            == (node.selected_profile.provider, node.selected_profile.profile_name)
        )
        assert node.selected_profile.profile_digest == content_digest(profile)


def test_langgraph_manifest_enumerates_every_non_native_semantic_without_overclaim() -> None:
    workflow = compile_python(_workflow_document())
    runtime = WrapperRuntimePolicy(3, 1, "resumable", "fenced_commit")
    cache = CachePolicy("content_addressed_readwrite", ("workflow_digest", "task_id"))

    manifest = finite_to_wrapper(
        workflow,
        target=LANGGRAPH_TARGET,
        validator_bindings=_bindings(),
        cache_policy=cache,
        runtime_policy=runtime,
        loss_policy="record",
    )

    loss_ids = {item.loss_id for item in manifest.semantic_losses}
    assert {
        "loss:langgraph:effect-declarations",
        "loss:langgraph:validators",
        "loss:langgraph:run-budgets",
        "loss:langgraph:typed-ports",
        "loss:langgraph:approvals",
        "loss:langgraph:adapter-requirements",
        "loss:langgraph:cache-policy",
        "loss:langgraph:retries",
        "loss:langgraph:checkpoint-resume",
        "loss:langgraph:effect-commit",
    } <= loss_ids
    accounting = {item.feature: item.status for item in manifest.feature_accounting}
    assert accounting["dag"] == "native"
    assert accounting["dependencies"] == "native"
    assert accounting["typed-ports"] == "metadata-only"
    assert accounting["approvals"] == "metadata-only"
    assert accounting["run-budgets"] == "partially-native"
    assert accounting["effect-execution-semantics"] == "narrowed-proposal-only"
    assert any("not a LangGraph execution witness" in item for item in manifest.claim_boundaries)
    assert any("no Alibaba PageAgent or BeeAI" in item for item in manifest.claim_boundaries)
    assert validate_wrapper_manifest(manifest) == ()
    assert wrapper_to_finite(manifest).workflow.digest == workflow.digest


def test_reject_policy_refuses_any_lossy_target_conversion() -> None:
    workflow = compile_python(_workflow_document())

    with pytest.raises(FrameworkConformanceError, match="semantic loss"):
        finite_to_wrapper(
            workflow,
            target=LANGGRAPH_TARGET,
            validator_bindings=_bindings(),
            cache_policy=CachePolicy("disabled"),
            loss_policy="reject",
        )


def test_forged_loss_ledger_is_rejected_even_after_attacker_reseals_digest() -> None:
    manifest = finite_to_wrapper(
        compile_python(_workflow_document()),
        target=LANGGRAPH_TARGET,
        validator_bindings=_bindings(),
        cache_policy=CachePolicy("disabled"),
    )
    forged = _reseal_manifest(replace(manifest, semantic_losses=manifest.semantic_losses[:-1]))

    problems = validate_wrapper_manifest(forged)
    assert "semantic-loss ledger is incomplete or inconsistent" in problems
    with pytest.raises(FrameworkConformanceError, match="semantic-loss ledger"):
        wrapper_to_finite(forged)


def test_graph_profile_and_source_mutations_cannot_survive_round_trip_validation() -> None:
    manifest = finite_to_wrapper(
        compile_python(_workflow_document()),
        validator_bindings=_bindings(),
        cache_policy=CachePolicy("disabled"),
    )
    publish = manifest.nodes[1]
    selection = replace(publish.selected_profile, profile_digest="0" * 64)
    forged_profile = _reseal_manifest(
        replace(
            manifest,
            nodes=(manifest.nodes[0], replace(publish, selected_profile=selection)),
        )
    )
    forged_edges = _reseal_manifest(replace(manifest, edges=()))
    forged_effect = _reseal_manifest(
        replace(
            manifest,
            nodes=(
                manifest.nodes[0],
                replace(publish, effect=replace(publish.effect, resource="other-target")),
            ),
        )
    )

    assert any("profile digest" in item for item in validate_wrapper_manifest(forged_profile))
    assert any("edges" in item for item in validate_wrapper_manifest(forged_edges))
    assert any(
        "workflow digest differs" in item for item in validate_wrapper_manifest(forged_effect)
    )

    fallback = next(item for item in publish.profiles if item.name == "fallback")
    forged_rule = _reseal_manifest(
        replace(
            manifest,
            nodes=(
                manifest.nodes[0],
                replace(
                    publish,
                    selected_profile=replace(
                        publish.selected_profile,
                        provider=fallback.provider,
                        profile_name=fallback.name,
                        profile_digest=content_digest(fallback),
                    ),
                ),
            ),
        )
    )
    assert any(
        "violates its deterministic rule" in item for item in validate_wrapper_manifest(forged_rule)
    )


def test_strict_conversion_rejects_missing_validators_bad_cache_and_partial_selections() -> None:
    workflow = compile_python(_workflow_document())
    with pytest.raises(FrameworkConformanceError, match="validator bindings"):
        finite_to_wrapper(
            workflow,
            validator_bindings={"collect": ("validator",)},
            cache_policy=CachePolicy("disabled"),
        )
    with pytest.raises(FrameworkConformanceError, match="enabled cache"):
        finite_to_wrapper(
            workflow,
            validator_bindings=_bindings(),
            cache_policy=CachePolicy("content_addressed_readonly"),
        )
    with pytest.raises(FrameworkConformanceError, match="profile selections"):
        finite_to_wrapper(
            workflow,
            selected_profiles={"collect": ("local", "local-reader")},
            validator_bindings=_bindings(),
            cache_policy=CachePolicy("disabled"),
        )


def test_page_action_reference_is_tainted_proposal_only_and_not_an_integration_claim() -> None:
    contract = build_reference_page_action_contract()

    assert contract.verify_digest()
    assert validate_page_action_contract(contract) == ()
    assert contract.alibaba_pageagent_exercised is False
    assert contract.beeai_exercised is False
    assert contract.integration_evidence_digests == ()
    assert all(item.taint_label == TAINTED_BROWSER_OBSERVATION for item in contract.observations)
    assert all(item.state == "proposed" for item in contract.action_intents)
    assert all(item.executed_externally is False for item in contract.action_intents)
    assert all(item.effect_class.writes for item in contract.action_intents)
    assert any("Alibaba PageAgent is not" in item for item in contract.claim_boundaries)


def test_page_observation_cannot_be_promoted_to_authority() -> None:
    contract = build_reference_page_action_contract()
    observation = contract.observations[0]
    intent = replace(
        contract.action_intents[0],
        authority_grant_id=observation.observation_id,
    )
    forged = _reseal_page_contract(replace(contract, action_intents=(intent,)))

    problems = validate_page_action_contract(forged)
    assert any("browser observation as authority" in item for item in problems)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("untaint", "not explicitly tainted"),
        ("write-as-read", "not classified as a write"),
        ("executed", "unexecuted proposal"),
        ("host-escape", "not exactly allowlisted"),
        ("pageagent-claim", "PageAgent execution cannot be claimed"),
        ("beeai-claim", "BeeAI execution cannot be claimed"),
    ],
)
def test_page_action_adversarial_mutations_fail_after_resealing(
    mutation: str,
    expected: str,
) -> None:
    contract = build_reference_page_action_contract()
    observation = contract.observations[0]
    intent = contract.action_intents[0]
    if mutation == "untaint":
        forged = replace(
            contract,
            observations=(replace(observation, taint_label="trusted"),),
        )
    elif mutation == "write-as-read":
        forged = replace(
            contract,
            action_intents=(replace(intent, effect_class=EffectClass.READ),),
        )
    elif mutation == "executed":
        forged = replace(
            contract,
            action_intents=(replace(intent, state="committed", executed_externally=True),),
        )
    elif mutation == "host-escape":
        forged = replace(
            contract,
            observations=(
                replace(
                    observation,
                    page_url="https://stormshift.invalid.evil.example/console",
                ),
            ),
        )
    elif mutation == "pageagent-claim":
        forged = replace(contract, alibaba_pageagent_exercised=True)
    else:
        forged = replace(contract, beeai_exercised=True)
    forged = _reseal_page_contract(forged)

    assert any(expected in item for item in validate_page_action_contract(forged))


def test_langgraph_availability_probe_is_boolean() -> None:
    assert type(langgraph_conformance_available()) is bool


@pytest.mark.skipif(
    not langgraph_conformance_available(),
    reason="install pinned optional witness with `pip install -e .[langgraph]`",
)
def test_real_pinned_langgraph_conformance_witness_executes_and_stays_bounded(
    tmp_path,
) -> None:
    witness = asyncio.run(
        run_pinned_langgraph_conformance_witness(
            run_id="framework-conformance-real-langgraph",
            checkpoint_path=tmp_path / "langgraph-conformance.sqlite",
        )
    )

    assert witness.actual_framework_execution is True
    assert witness.framework == "langgraph"
    assert witness.framework_version == PINNED_LANGGRAPH_VERSION
    assert witness.checkpoint_package_version == PINNED_LANGGRAPH_CHECKPOINT_VERSION
    assert witness.pinned_versions_match is True
    assert witness.all_tasks_executed_once is True
    assert witness.dependencies_preserved is True
    assert witness.static_profile_selection_preserved is True
    assert witness.validator_executed is True
    assert witness.cache_disabled is True
    assert witness.checkpoint_receipt_verified is True
    assert witness.effects_proposal_only is True
    assert witness.model_calls_made is False
    assert witness.external_calls_made is False
    assert witness.external_effects_executed == 0
    assert "loss:langgraph:run-budgets" in witness.semantic_loss_ids
    assert "loss:langgraph:approvals" in witness.semantic_loss_ids
    assert "loss:langgraph:effect-commit" in witness.semantic_loss_ids
    assert witness.verify_digest()
