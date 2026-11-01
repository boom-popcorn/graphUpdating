# Phase 4: multimodal ingestion and protocol-level integration

## What is implemented

The update pipeline now accepts `RawArtifact` inputs before the normalized
`Observation` contract. Three auditable adapter paths are included:

- text relation extraction using versioned regular-expression rules and an
  entity registry;
- structured records from ETL, APIs or business tables;
- canonical relation detections emitted by an external OCR, detector or VLM.

The image adapter consumes model output, not raw pixels. This boundary is
intentional: a future vision model can be replaced without changing evidence
fusion, ontology evolution or transaction semantics.

Candidate statements are fused only when subject, predicate, object, types and
literal status agree. Confidence is the strongest candidate plus a bounded
corroboration bonus (maximum 0.10). Every source, modality, character span,
row locator or bounding box remains attached to the fused observation.

## Run the vertical slice

```powershell
python -m pip install -e ".[dev]"
python scripts/run_multimodal_demo.py
python scripts/run_multimodal_demo.py --commit --runtime-dir runtime/phase4
pytest -q --basetemp=.pytest-tmp/phase4
```

The first command path is a dry run. It should report three candidates fused
into one observation, two proposed schema operations, a conforming validation
report and transaction status `prepared` without changing `v0.1.0`.

## MCP acceptance

Install the official MCP v2 SDK in an isolated environment and run a real
stdio client against the server process:

```powershell
python -m venv .venv-mcp
.venv-mcp\Scripts\python -m pip install -e ".[mcp]"
.venv-mcp\Scripts\python scripts/mcp_smoke.py
```

The smoke test negotiates the protocol, discovers the tool list, calls
`get_ontology_version` and `get_evolution_policy`, and writes all temporary
runtime artifacts outside the repository. The server also exposes
`ingest_and_preview` and `ingest_and_run` for Harness clients.

## Neo4j acceptance

The repository currently targets Neo4j 5 or later. Neo4j Community 5.26.28 at
`D:\Neo4j-5.26` has now passed the online commit/rollback acceptance test using
JDK 21 and a project-local mutable data directory. The older local Neo4j 3.5.28
is deliberately rejected because it does not support the modern constraint
syntax used by the versioned snapshot store. A portable 5.26 LTS Community
compose definition is also provided:

```powershell
$env:NEO4J_AUTH = "neo4j/<strong-password>"
docker compose -f integrations/neo4j/compose.yaml up -d
$env:ODKG_NEO4J_URI = "neo4j://localhost:7687"
$env:ODKG_NEO4J_USER = "neo4j"
$env:ODKG_NEO4J_PASSWORD = "<strong-password>"
$env:ODKG_NEO4J_STORE_ID = "phase4-smoke"
python -m pip install -e ".[neo4j]"
python scripts/neo4j_smoke.py
```

The Neo4j smoke test publishes the bootstrap snapshot, commits one evolved
version, switches the active pointer back and checks status after rollback.
See the [online acceptance record](phase4-neo4j-acceptance.md) for the verified
triple counts, Windows Java selector workaround and security boundary.

## Scope still open

- raw image/audio/video inference adapters and learned entity linking;
- contradiction handling for mutually exclusive candidate objects;
- protocol smoke execution after the MCP dependency environment is complete;
- Hermes/DeepSeek Harness end-to-end runs after their Python requirements are
  met.
