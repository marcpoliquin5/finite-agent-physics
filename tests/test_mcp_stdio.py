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
                } == names
                assert len(tools.tools) == 13
                result = await session.call_tool("finite_capabilities", {})
                assert not result.isError
                quota = await session.call_tool(
                    "finite_quota_corpus", {"seed": 13, "cycles": 2}
                )
                assert not quota.isError

    asyncio.run(exercise())
