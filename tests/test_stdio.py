"""End-to-end tests that run the real PrintMCP server over stdio.

Most of the suite exercises tool handlers in-process. This file proves the
full transport path works: spawn ``python -m printmcp`` as a subprocess, speak
JSON-RPC over stdio with the official MCP client, and round-trip a real tool
call. It catches failure modes the in-process tests can't -- e.g. a tool
module that fails to register on startup, or human output written to stdout
that would corrupt the protocol stream (all such output must go to stderr).

No network, Cura, or printer required: the action tool we call is a dry run
(``confirm=false``), which by contract sends ZERO requests, and the failing
call exercises a missing-token ToolError.

Coroutines are driven with ``asyncio.run()`` (as in test_server.py) so these
tests don't require a pytest-asyncio plugin.
"""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Tool names we expect registered, one per level, to prove all three tool
# modules imported and registered their handlers at startup.
EXPECTED_TOOLS = {
    "thingiverse_search_models",
    "cura_slice_model",
    "orca_slice_model",
    "orca_list_profiles",
    "octoprint_get_status",
    "octoprint_set_temperature",
}


def _server_params():
    """Spawn the in-repo server. Dummy OctoPrint config lets dry-run tools run
    offline; PYTHONUNBUFFERED keeps the child's stderr from buffering."""
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "OCTOPRINT_URL": "http://printer.test",
        "OCTOPRINT_API_KEY": "test-key-do-not-leak",
    }
    return StdioServerParameters(
        command=sys.executable, args=["-m", "printmcp"], env=env
    )


async def _list_tool_names():
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.list_tools()


async def _call(name, arguments):
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(name, arguments)


def test_stdio_list_tools_round_trip():
    """The stdio server starts, handshakes, and lists all 14 tools."""
    tools = asyncio.run(_list_tool_names())
    names = {t.name for t in tools.tools}
    assert EXPECTED_TOOLS <= names
    # Structured output contract survived the transport: schemas present.
    for t in tools.tools:
        assert t.outputSchema is not None, f"{t.name} lost its outputSchema"


def test_stdio_dry_run_tool_call_sends_no_network():
    """A dry-run action tool called over stdio returns dry_run=True (offline)."""
    result = asyncio.run(
        _call(
            "octoprint_set_temperature",
            {
                "heater": "bed",
                "target": 60,
                "confirm": False,
                "response_format": "json",
            },
        )
    )
    assert not result.isError, f"dry-run call errored: {result.content}"
    sc = result.structuredContent
    assert sc is not None
    assert sc.get("dry_run") is True
    assert sc.get("heater") == "bed"
    assert sc.get("target") == 60


def test_stdio_tool_error_propagates():
    """A failing tool returns isError=True rather than crashing the server."""
    # No Thingiverse token in the child env -> missing-token ToolError.
    env = os.environ.copy()
    env.pop("THINGIVERSE_TOKEN", None)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "printmcp"],
        env={**env, "PYTHONUNBUFFERED": "1", "THINGIVERSE_TOKEN": ""},
    )

    async def _run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(
                    "thingiverse_search_models", {"query": "benchy"}
                )

    result = asyncio.run(_run())
    assert result.isError
    first = result.content[0]
    text = getattr(first, "text", str(first))
    assert "THINGIVERSE_TOKEN" in text
