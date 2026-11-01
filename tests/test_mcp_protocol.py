import asyncio
import importlib.util

import pytest


def test_mcp_client_discovers_and_invokes_tools() -> None:
    if importlib.util.find_spec("mcp") is None:
        pytest.skip("install the 'mcp' optional dependency for protocol testing")

    from mcp import Client

    from odkg.mcp_server import mcp

    async def scenario() -> None:
        async with Client(mcp) as client:
            result = await client.list_tools()
            names = {tool.name for tool in result.tools}
            assert "ingest_and_preview" in names
            assert "run_update_cycle" in names
            version = await client.call_tool("get_ontology_version", {})
            assert version.structured_content == {"version": "v0.1.0"}

    asyncio.run(scenario())
