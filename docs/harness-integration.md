# Harness integration

The core is an independent MCP server; a Harness is a replaceable orchestration
client rather than an imported runtime dependency.

## Hermes first

The downloaded Hermes checkout supports stdio MCP servers, allowlists,
untrusted-server approval and Skills. That checkout requires Python 3.14 while
the core currently runs on Python 3.13, so process isolation is required.

Merge `integrations/hermes/config.example.yaml` into a Hermes profile, set
`ODKG_PYTHON` to the Python executable containing this project, and install the
four folders under `skills/` into the profile Skills directory. Keep
`supports_parallel_tool_calls: false`, because update and rollback modify
ordered version state.

The example marks the server `untrusted`, keeping mutating tools behind Hermes'
approval surface. The rule-layer skill independently requires a dry run before
execution.

## DeepSeek Harness later

DeepSeek Harness can consume the same MCP surface, but its downloaded source is
still a developer-preview runtime. A native adapter should wait until those APIs
stabilize; ontology, transaction and experiment code must remain in this core.
