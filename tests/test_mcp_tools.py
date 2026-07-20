from agent_physics.mcp_server import (
    finite_capabilities,
    finite_context_drill,
    finite_effect_drill,
    finite_preflight,
    finite_registered_faults,
    finite_simulate,
    finite_verify,
)


def test_mcp_capability_statement_is_explicitly_simulated() -> None:
    payload = finite_capabilities()
    assert payload["stage"] == "deterministic-simulation"
    assert "live IBM Granite or watsonx execution" in payload["not_implemented"]


def test_mcp_preflight_can_refuse_without_calling_external_systems() -> None:
    payload = finite_preflight(max_tokens=1)
    assert payload["status"] == "refused"
    assert payload["measurement_kind"] == "deterministic-simulation"


def test_mcp_simulation_and_verification_are_machine_readable() -> None:
    simulation = finite_simulate(include_events=False)
    verification = finite_verify()
    assert simulation["success"] is True
    assert "events" not in simulation
    assert verification["passed"] is True
    assert simulation["measurement_kind"] == "deterministic-simulation"


def test_fault_registry_does_not_claim_execution() -> None:
    faults = finite_registered_faults()["faults"]
    assert faults
    assert {fault["execution_status"] for fault in faults} == {"registered-not-executed"}


def test_context_drill_packs_or_refuses_without_exposing_raw_hostile_text() -> None:
    packed = finite_context_drill()
    refused = finite_context_drill(max_bytes=1, max_tokens=1)
    assert packed["status"] == "packed"
    assert packed["verified"] is True
    assert packed["raw_attack_visible_in_wire"] is False
    assert refused["status"] == "refused"
    assert refused["verified"] is True


def test_effect_drill_is_single_apply_after_hard_and_soft_faults() -> None:
    for crash_mode in ("none", "soft", "hard"):
        result = finite_effect_drill(crash_mode)
        assert result["external_effects_possible"] is False
        assert result["final_state"] == "committed"
        assert result["physical_apply_count"] == 1
