import json

from agent_physics.cli import main
from agent_physics.examples import miami_eoc_envelope, miami_eoc_graph
from agent_physics.scheduler import SchedulePolicy, Scheduler


def test_miami_demo_adapts_to_the_envelope() -> None:
    result = Scheduler().schedule(
        miami_eoc_graph(),
        miami_eoc_envelope(),
        SchedulePolicy.ADAPTIVE,
    )
    assert result.success
    assert result.makespan_ms <= miami_eoc_envelope().deadline_ms
    assert result.total_tokens <= miami_eoc_envelope().max_tokens
    assert any(entry.backend == "simulated-granite-accurate" for entry in result.entries)
    assert any(entry.backend == "fixture-rule-engine" for entry in result.entries)


def test_cli_emits_json(capsys: object) -> None:
    assert main(["demo", "--policy", "adaptive", "--json"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["success"] is True
    assert payload["policy"] == "adaptive"


def test_cli_emits_preflight_evidence(capsys: object) -> None:
    assert main(["preflight"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["status"] == "feasible"
    assert payload["conservation"]["passed"] is True
    assert len(payload["certificate_digest"]) == 64
