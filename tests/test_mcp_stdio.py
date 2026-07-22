import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_server_handshake_tool_discovery_and_call() -> None:
    async def exercise() -> None:
        repository = Path(__file__).resolve().parents[1]
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_physics.mcp_server"],
            cwd=str(repository),
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
                    "finite_framework_conformance_drill",
                    "finite_artifact_integrity_drill",
                } == names
                assert len(tools.tools) == 22
                result = await session.call_tool("finite_capabilities", {})
                assert not result.isError
                quota = await session.call_tool("finite_quota_corpus", {"seed": 13, "cycles": 2})
                assert not quota.isError
                physical = await session.call_tool("finite_physical_admission_drill", {})
                assert not physical.isError

    asyncio.run(exercise())
