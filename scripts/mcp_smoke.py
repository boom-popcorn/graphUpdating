from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import Client, StdioServerParameters


async def smoke() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="odkg-mcp-") as runtime:
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "odkg.mcp_server"],
            cwd=root,
            env={"ODKG_RUNTIME_DIR": runtime},
        )
        async with Client(server) as client:
            listed = await client.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            required = {
                "get_ontology_version",
                "get_evolution_policy",
                "ingest_and_preview",
                "run_update_cycle",
            }
            missing = required.difference(names)
            if missing:
                raise RuntimeError(f"MCP tool discovery missing: {sorted(missing)}")
            version = await client.call_tool("get_ontology_version", {})
            policy = await client.call_tool("get_evolution_policy", {})
            print(
                json.dumps(
                    {
                        "server": getattr(client.server_info, "name", None),
                        "protocol_version": str(client.protocol_version),
                        "tools": names,
                        "version": version.structured_content,
                        "policy": policy.structured_content,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )


if __name__ == "__main__":
    if os.environ.get("ODKG_NEO4J_URI"):
        raise RuntimeError("unset ODKG_NEO4J_URI for the isolated MCP smoke test")
    asyncio.run(smoke())
