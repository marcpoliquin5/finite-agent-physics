from __future__ import annotations

from dataclasses import replace

import pytest

from agent_physics import framework_conformance as conformance
from agent_physics.contracts import EffectClass
from agent_physics.framework_conformance import (
    BrowserObservation,
    CachePolicy,
    FrameworkConformanceError,
    FrameworkWrapperManifest,
    GovernedPageActionContract,
    PageActionIntent,
    ProfileSelection,
    WrapperRuntimePolicy,
    build_reference_page_action_contract,
    finite_to_wrapper,
    validate_page_action_contract,
    validate_wrapper_manifest,
)
from agent_physics.serialization import content_digest
from agent_physics.workflow_ir import compile_python


def _workflow():
    return compile_python(
        {
            "schema_version": 1,
            "envelope": {
                "deadline_ms": 1_000,
                "max_tokens": 1_000,
                "max_cost_microusd": 1_000,
                "max_context_bytes": 10_000,
                "max_parallelism": 1,
                "provider_limits": {"local": 1},
            },
            "tasks": [
                {
                    "task_id": "a",
                    "profiles": [
                        {
                            "name": "qualified",
                            "provider": "local",
                            "duration_ms_p50": 1,
                            "duration_ms_p95": 2,
                            "quality": 0.9,
                        },
                        {
                            "name": "below-floor",
                            "provider": "local",
                            "duration_ms_p50": 1,
                            "duration_ms_p95": 2,
                            "quality": 0.1,
                        },
                    ],
                    "min_quality": 0.5,
                    "effect": {"kind": "pure"},
                },
                {
                    "task_id": "b",
                    "profiles": [
                        {
                            "name": "qualified",
                            "provider": "local",
                            "duration_ms_p50": 1,
                            "duration_ms_p95": 2,
                            "quality": 1.0,
                        }
                    ],
                    "dependencies": ["a"],
                    "effect": {"kind": "pure"},
                },
            ],
        }
    )


def _bindings() -> dict[str, tuple[str, ...]]:
    return {"a": ("validator:a",), "b": ("validator:b",)}


def _manifest() -> FrameworkWrapperManifest:
    return finite_to_wrapper(
        _workflow(),
        validator_bindings=_bindings(),
        cache_policy=CachePolicy("disabled"),
    )


def _reseal_manifest(manifest: FrameworkWrapperManifest) -> FrameworkWrapperManifest:
    unsigned = replace(manifest, manifest_digest="").unsigned_payload()
    return replace(manifest, manifest_digest=content_digest(unsigned))


def _reseal_page(contract: GovernedPageActionContract) -> GovernedPageActionContract:
    unsigned = replace(contract, contract_digest="").unsigned_payload()
    return replace(contract, contract_digest=content_digest(unsigned))


def test_conversion_boundary_rejects_invalid_policies_bindings_and_selections() -> None:
    workflow = _workflow()
    kwargs = {
        "validator_bindings": _bindings(),
        "cache_policy": CachePolicy("disabled"),
    }
    with pytest.raises(FrameworkConformanceError, match="CompiledWorkflow"):
        finite_to_wrapper(object(), **kwargs)  # type: ignore[arg-type]
    with pytest.raises(FrameworkConformanceError, match="digest is inconsistent"):
        finite_to_wrapper(replace(workflow, digest="0" * 64), **kwargs)
    with pytest.raises(FrameworkConformanceError, match="unsupported wrapper target"):
        finite_to_wrapper(workflow, target="unsupported", **kwargs)
    with pytest.raises(FrameworkConformanceError, match="unsupported loss policy"):
        finite_to_wrapper(workflow, loss_policy="ignore", **kwargs)  # type: ignore[arg-type]

    bad_caches = (
        (CachePolicy("unknown"), "mode is unsupported"),
        (CachePolicy("disabled", ("field",)), "cannot declare key fields"),
        (CachePolicy("content_addressed_readonly"), "requires explicit key fields"),
        (
            CachePolicy("content_addressed_readonly", ("field", "field")),
            "must be unique",
        ),
        (
            CachePolicy("content_addressed_readonly", ("",)),
            "nonempty strings",
        ),
    )
    for policy, message in bad_caches:
        with pytest.raises(FrameworkConformanceError, match=message):
            finite_to_wrapper(
                workflow,
                validator_bindings=_bindings(),
                cache_policy=policy,
            )

    bad_runtimes = (
        (WrapperRuntimePolicy(max_attempts=True), "positive integer"),
        (WrapperRuntimePolicy(max_hidden_retries=-1), "nonnegative integer"),
        (WrapperRuntimePolicy(checkpoint_mode="unknown"), "checkpoint mode"),
        (WrapperRuntimePolicy(effect_mode="unknown"), "effect mode"),
    )
    for policy, message in bad_runtimes:
        with pytest.raises(FrameworkConformanceError, match=message):
            finite_to_wrapper(workflow, runtime_policy=policy, **kwargs)

    bad_bindings = (
        ({"a": (), "b": ("validator:b",)}, "at least one"),
        ({"a": ("",), "b": ("validator:b",)}, "nonempty strings"),
        ({"a": ("duplicate", "duplicate"), "b": ("validator:b",)}, "unique"),
    )
    for bindings, message in bad_bindings:
        with pytest.raises(FrameworkConformanceError, match=message):
            finite_to_wrapper(
                workflow,
                validator_bindings=bindings,
                cache_policy=CachePolicy("disabled"),
            )

    selections = {"a": ("local", "missing"), "b": ("local", "qualified")}
    with pytest.raises(FrameworkConformanceError, match="is not unique"):
        finite_to_wrapper(workflow, selected_profiles=selections, **kwargs)
    selections["a"] = ("local", "below-floor")
    with pytest.raises(FrameworkConformanceError, match="below the quality floor"):
        finite_to_wrapper(workflow, selected_profiles=selections, **kwargs)

    node = _manifest().nodes[0]
    no_qualified = replace(
        node, profiles=(replace(node.profiles[0], quality=0.1),), min_quality=0.9
    )
    with pytest.raises(FrameworkConformanceError, match="no profile meeting"):
        conformance._default_profile(no_qualified)
    with pytest.raises(FrameworkConformanceError, match="unsupported wrapper target"):
        conformance._target_accounting(
            "unsupported",
            (node,),
            _manifest().envelope,
            CachePolicy("disabled"),
            WrapperRuntimePolicy(),
        )


def test_manifest_outer_shape_validation_is_total_and_digest_bound() -> None:
    assert validate_wrapper_manifest(object()) == ("value is not a FrameworkWrapperManifest",)
    manifest = _manifest()
    mutations = (
        (replace(manifest, schema_version="unknown"), "schema version"),
        (replace(manifest, target="unknown"), "target is unsupported"),
        (replace(manifest, source_workflow_schema_version=999), "source workflow schema"),
        (replace(manifest, nodes=[]), "nodes must be a nonempty tuple"),  # type: ignore[arg-type]
        (replace(manifest, nodes=("node",)), "malformed node"),  # type: ignore[arg-type]
        (replace(manifest, edges=[]), "edges are malformed"),  # type: ignore[arg-type]
        (replace(manifest, cache_policy="cache"), "cache policy is malformed"),  # type: ignore[arg-type]
        (replace(manifest, runtime_policy="runtime"), "runtime policy is malformed"),  # type: ignore[arg-type]
    )
    for forged, expected in mutations:
        forged = _reseal_manifest(forged)
        assert any(expected in problem for problem in validate_wrapper_manifest(forged))

    unsealed = replace(manifest, claim_boundaries=())
    assert "manifest digest is invalid" in validate_wrapper_manifest(unsealed)


def test_manifest_semantic_mutations_are_rejected_even_after_resealing() -> None:
    manifest = _manifest()
    a, b = manifest.nodes
    low = next(profile for profile in a.profiles if profile.name == "below-floor")

    cases: list[tuple[FrameworkWrapperManifest, str]] = []
    cases.append((replace(manifest, nodes=(b, a), edges=conformance._edges((b, a))), "node IDs"))
    cases.append((replace(manifest, edges=()), "edges do not exactly"))

    wrong_task = replace(
        a,
        selected_profile=replace(a.selected_profile, task_id="b"),
    )
    cases.append((replace(manifest, nodes=(wrong_task, b)), "task ID differs"))

    missing = replace(
        a,
        selected_profile=replace(a.selected_profile, profile_name="missing"),
    )
    cases.append((replace(manifest, nodes=(missing, b)), "does not exist"))

    below = replace(
        a,
        selected_profile=ProfileSelection(
            task_id="a",
            profile_name=low.name,
            provider=low.provider,
            selection_rule="caller-explicit",
            profile_digest=content_digest(low),
        ),
    )
    cases.append((replace(manifest, nodes=(below, b)), "below quality floor"))

    unknown_rule = replace(
        a,
        selected_profile=replace(a.selected_profile, selection_rule="attacker-rule"),
    )
    cases.append((replace(manifest, nodes=(unknown_rule, b)), "selection rule is unknown"))
    cases.append((replace(manifest, nodes=(replace(a, validator_ids=()), b)), "bindings are empty"))
    cases.append(
        (
            replace(manifest, nodes=(replace(a, validator_ids=("",)), b)),
            "binding is malformed",
        )
    )
    cases.append(
        (
            replace(manifest, nodes=(replace(a, validator_ids=("same", "same")), b)),
            "bindings are duplicated",
        )
    )

    invalid_dependency = replace(b, dependencies=("missing",))
    invalid_nodes = (a, invalid_dependency)
    cases.append(
        (
            replace(manifest, nodes=invalid_nodes, edges=conformance._edges(invalid_nodes)),
            "cannot reconstruct valid FINITE IR",
        )
    )
    cases.append((replace(manifest, feature_accounting=()), "feature accounting"))
    cases.append((replace(manifest, claim_boundaries=()), "claim boundaries"))

    for forged, expected in cases:
        problems = validate_wrapper_manifest(_reseal_manifest(forged))
        assert any(expected in problem for problem in problems), (expected, problems)


def test_page_url_and_contract_outer_shapes_fail_closed_after_resealing() -> None:
    assert validate_page_action_contract(object()) == ("value is not a GovernedPageActionContract",)
    contract = build_reference_page_action_contract()
    observation = contract.observations[0]
    intent = contract.action_intents[0]

    cases = (
        (replace(contract, schema_version="unknown"), "schema version"),
        (replace(contract, wrapper_kind="unknown"), "wrapper kind"),
        (replace(contract, contract_id=""), "contract ID"),
        (replace(contract, allowed_hosts=[]), "allowed hosts"),  # type: ignore[arg-type]
        (replace(contract, allowed_hosts=("HTTPS://EXAMPLE.COM/path",)), "allowed hosts"),
        (
            replace(contract, allowed_hosts=("stormshift.invalid", "stormshift.invalid")),
            "allowed hosts",
        ),
        (replace(contract, observations=[]), "observations are malformed"),  # type: ignore[arg-type]
        (replace(contract, observations=("observation",)), "observations are malformed"),  # type: ignore[arg-type]
        (replace(contract, action_intents=[]), "intents are malformed"),  # type: ignore[arg-type]
        (replace(contract, action_intents=("intent",)), "intents are malformed"),  # type: ignore[arg-type]
        (replace(contract, integration_evidence_digests=("a" * 64,)), "integration evidence"),
        (replace(contract, claim_boundaries=()), "claim boundaries"),
    )
    for forged, expected in cases:
        problems = validate_page_action_contract(_reseal_page(forged))
        assert any(expected in problem for problem in problems), (expected, problems)

    assert "page-action contract digest is invalid" in validate_page_action_contract(
        replace(contract, contract_id="tampered")
    )

    observation_cases: tuple[tuple[BrowserObservation, str], ...] = (
        (replace(observation, observation_id=""), "IDs must be nonempty"),
        (replace(observation, captured_at_ms=True), "timestamp is malformed"),
        (replace(observation, payload_digest="A" * 64), "payload digest is malformed"),
        (replace(observation, page_url=" https://stormshift.invalid"), "URL is malformed"),
        (replace(observation, page_url="https://stormshift.invalid:bad/x"), "cannot be parsed"),
        (replace(observation, page_url="http://stormshift.invalid/x"), "must use HTTPS"),
        (
            replace(observation, page_url="https://user:secret@stormshift.invalid/x"),
            "cannot contain credentials",
        ),
        (replace(observation, page_url="https://stormshift.invalid:444/x"), "port is not allowed"),
    )
    for changed, expected in observation_cases:
        forged = _reseal_page(replace(contract, observations=(changed,)))
        assert any(expected in problem for problem in validate_page_action_contract(forged))

    duplicate = _reseal_page(replace(contract, observations=(observation, observation)))
    assert any(
        "IDs must be nonempty and unique" in item
        for item in validate_page_action_contract(duplicate)
    )

    empty_intent = replace(intent, intent_id="")
    duplicate_intents = _reseal_page(replace(contract, action_intents=(empty_intent, empty_intent)))
    assert any(
        "intent IDs must be nonempty and unique" in item
        for item in validate_page_action_contract(duplicate_intents)
    )


def test_page_intent_semantics_reject_authority_and_effect_confusion() -> None:
    contract = build_reference_page_action_contract()
    intent = contract.action_intents[0]
    observation = contract.observations[0]
    mutations: tuple[tuple[PageActionIntent, str], ...] = (
        (replace(intent, action="shell"), "outside the governed vocabulary"),
        (replace(intent, target=""), "target is required"),
        (replace(intent, mutates_page_or_external_state=1), "mutation flag must be boolean"),  # type: ignore[arg-type]
        (replace(intent, requires_approval=1), "approval flag must be boolean"),  # type: ignore[arg-type]
        (replace(intent, authority_grant_id=""), "independent authority grant"),
        (replace(intent, observation_ids=[]), "unknown observation reference"),  # type: ignore[arg-type]
        (replace(intent, observation_ids=("missing",)), "unknown observation reference"),
        (replace(intent, effect_class="write"), "effect class is malformed"),  # type: ignore[arg-type]
        (replace(intent, action="inspect"), "mutation flag conflicts"),
        (replace(intent, idempotency_key=""), "lacks an idempotency key"),
        (replace(intent, compensation_action=None), "lacks compensation"),
        (
            replace(
                intent,
                effect_class=EffectClass.IRREVERSIBLE_WRITE,
                requires_approval=False,
            ),
            "lacks approval",
        ),
        (
            replace(
                intent,
                action="click",
                mutates_page_or_external_state=False,
                effect_class=EffectClass.READ,
            ),
            "read declaration has inconsistent",
        ),
        (
            replace(intent, authority_grant_id=observation.observation_id),
            "browser observation as authority",
        ),
    )
    for changed, expected in mutations:
        forged = _reseal_page(replace(contract, action_intents=(changed,)))
        problems = validate_page_action_contract(forged)
        assert any(expected in problem for problem in problems), (expected, problems)
