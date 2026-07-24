import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_server_invokes_all_tools_and_preserves_one_durable_run(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        repository = Path(__file__).resolve().parents[1]
        state_directory = tmp_path / "finite-state"
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("WATSONX_", "WX_", "IBM_"))
        }
        environment.update(
            {
                "FINITE_STATE_DIR": str(state_directory),
                # Syntactically valid fake values let the call-free Granite preflight run while
                # the child-process network guard proves it cannot become live inference.
                "WATSONX_URL": "https://watsonx.invalid.example",
                "WATSONX_API_KEY": "stdio-e2e-fake-key",
                "WATSONX_PROJECT_ID": "stdio-e2e-fake-project",
                "WATSONX_MODEL_ID": "ibm/granite-stdio-e2e-fixture",
            }
        )
        guarded_server = """
from mcp.server.fastmcp import FastMCP
from agent_physics import mcp_server
import os
import socket
import subprocess
from pathlib import Path

_original_connect = socket.socket.connect
_original_create_connection = socket.create_connection

def _loopback(address):
    return isinstance(address, tuple) and bool(address) and address[0] in {"127.0.0.1", "::1"}

def _guarded_connect(sock, address):
    if _loopback(address):
        return _original_connect(sock, address)
    raise RuntimeError(f"FINITE stdio E2E blocked external network call: {address!r}")

def _guarded_create_connection(address, *args, **kwargs):
    if _loopback(address):
        return _original_create_connection(address, *args, **kwargs)
    raise RuntimeError(f"FINITE stdio E2E blocked external network call: {address!r}")

def _blocked_process(*args, **kwargs):
    raise RuntimeError("FINITE stdio E2E blocked child process creation")

socket.socket.connect = _guarded_connect
socket.create_connection = _guarded_create_connection
subprocess.Popen = _blocked_process
state = Path(os.environ["FINITE_STATE_DIR"])
state.mkdir(parents=True, exist_ok=True)
(state / "external-io-guard.active").write_text("network-and-process-guarded", encoding="utf-8")
mcp_server.main()
"""
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-c", guarded_server],
            cwd=str(repository),
            env=environment,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {
                    "finite_capabilities",
                    "finite_preflight",
                    "finite_granite_preflight",
                    "finite_run",
                    "finite_status",
                    "finite_explain_run",
                    "finite_verify_run",
                    "finite_simulate",
                    "finite_verify",
                    "finite_registered_faults",
                    "finite_context_drill",
                    "finite_effect_drill",
                    "finite_stormshift_validate",
                    "finite_fault_experiment",
                    "finite_executor_drill",
                    "finite_quota_corpus",
                    "finite_replanning_drill",
                    "finite_decision_explanation_drill",
                    "finite_physical_admission_drill",
                    "finite_adaptive_recovery_drill",
                    "finite_production_survival_drill",
                    "finite_framework_conformance_drill",
                    "finite_artifact_integrity_drill",
                } == names
                assert len(tools.tools) == 23

                run_id = "finite-stdio-all-tools-v5"
                calls: tuple[tuple[str, dict[str, object]], ...] = (
                    ("finite_capabilities", {}),
                    (
                        "finite_preflight",
                        {
                            "deadline_ms": 12_000,
                            "max_tokens": 16_000,
                            "max_cost_microusd": 16_000,
                            "max_context_bytes": 70_000,
                            "max_parallelism": 4,
                            "min_modeled_success_probability": 0.9,
                        },
                    ),
                    ("finite_granite_preflight", {"max_new_tokens": 8}),
                    (
                        "finite_run",
                        {
                            "run_id": run_id,
                            "mode": "fixture",
                            "instruction": "",
                            "max_new_tokens": 8,
                            "bob_session_ref": "stdio-e2e-caller-assertion",
                        },
                    ),
                    ("finite_status", {"run_id": run_id}),
                    ("finite_explain_run", {"run_id": run_id, "include_payloads": False}),
                    ("finite_verify_run", {"run_id": run_id}),
                    ("finite_simulate", {"policy": "adaptive", "include_events": False}),
                    ("finite_verify", {"policy": "adaptive"}),
                    ("finite_registered_faults", {}),
                    ("finite_context_drill", {"max_bytes": 1_000, "max_tokens": 1_000}),
                    ("finite_effect_drill", {"crash_mode": "hard"}),
                    ("finite_stormshift_validate", {"fault": "none"}),
                    ("finite_fault_experiment", {"revision": "stdio-e2e-v5"}),
                    ("finite_executor_drill", {}),
                    ("finite_quota_corpus", {"seed": 13, "cycles": 2}),
                    ("finite_replanning_drill", {}),
                    (
                        "finite_decision_explanation_drill",
                        {"mode": "nominal", "include_records": False},
                    ),
                    ("finite_physical_admission_drill", {}),
                    ("finite_adaptive_recovery_drill", {}),
                    (
                        "finite_production_survival_drill",
                        {"trials_per_scenario": 3, "seed_base": 5_000},
                    ),
                    ("finite_framework_conformance_drill", {}),
                    ("finite_artifact_integrity_drill", {}),
                )
                assert {name for name, _ in calls} == names

                payloads: dict[str, dict[str, object]] = {}
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments)
                    assert not result.isError, f"{name}: {result.content!r}"
                    assert isinstance(result.structuredContent, dict), name
                    payloads[name] = result.structuredContent

                lifecycle = tuple(
                    payloads[name]
                    for name in (
                        "finite_run",
                        "finite_status",
                        "finite_explain_run",
                        "finite_verify_run",
                    )
                )
                assert {payload["run_id"] for payload in lifecycle} == {run_id}
                assert (
                    payloads["finite_run"]["event_digest"]
                    == payloads["finite_status"]["event_digest"]
                )
                assert (
                    payloads["finite_run"]["event_count"]
                    == payloads["finite_status"]["event_count"]
                )
                assert payloads["finite_run"]["live_provider_calls"] is False
                assert payloads["finite_run"]["external_effects_possible"] is False
                assert payloads["finite_verify_run"]["passed"] is True
                assert payloads["finite_granite_preflight"]["live_provider_calls"] is False
                assert "stdio-e2e-fake-key" not in repr(payloads["finite_granite_preflight"])
                assert payloads["finite_executor_drill"]["external_calls_made"] is False
                assert payloads["finite_executor_drill"]["model_calls_made"] is False
                assert payloads["finite_fault_experiment"]["external_systems_called"] is False
                assert payloads["finite_adaptive_recovery_drill"]["external_provider_calls"] == 0
                assert payloads["finite_production_survival_drill"]["verified"] is True
                assert (
                    payloads["finite_production_survival_drill"][
                        "duplicate_effect_applications"
                    ]
                    == 0
                )
                assert payloads["finite_artifact_integrity_drill"]["proof_passed"] is True

        assert (state_directory / "external-io-guard.active").read_text(encoding="utf-8") == (
            "network-and-process-guarded"
        )

    asyncio.run(exercise())
