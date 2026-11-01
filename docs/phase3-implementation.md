# Phase 3 implementation

Phase 3 adds the first experiment-ready orchestration layer while keeping the
domain model generic.

## Implemented

- A JSON evolution policy controls confidence, independent evidence,
  modalities, blocked namespaces, impact limits and auto-commit thresholds.
- Every change set records the policy identifier, review flag and rule findings.
- Every committed version package stores the complete `policy.json` snapshot.
- MCP exposes a non-mutating `preview_update_cycle` and policy/graph status
  tools in addition to commit and rollback.
- Four agentskills-compatible Skills encode the object, relation, rule and
  action layers.
- Hermes receives the system as an allowlisted, untrusted stdio MCP server.
- A versioned Neo4j projection retains immutable RDF snapshots and moves an
  active-version pointer on commit/rollback.
- Graph publication failure compensates by rolling back the in-memory ontology
  and fact transaction.
- The deterministic benchmark evaluates operation precision/recall/F1, status
  accuracy, human-review routing and latency.

## Neo4j configuration

Install the optional dependency and set credentials outside source control:

```powershell
python -m pip install -e ".[neo4j]"
$env:ODKG_NEO4J_URI="neo4j://localhost:7687"
$env:ODKG_NEO4J_USER="neo4j"
$env:ODKG_NEO4J_PASSWORD="..."
$env:ODKG_NEO4J_DATABASE="neo4j"  # optional
```

Without `ODKG_NEO4J_URI`, the MCP server runs with the file artifact store only.
The snapshot contract and commit/rollback integration have deterministic tests.
The concrete Neo4j connection and Cypher execution still require a live server
and credentials for acceptance testing.

## Reproduce the benchmark

```powershell
$env:PYTHONPATH="src"
python scripts\run_benchmark.py --output runtime\phase3-benchmark.json
```

The current five-case smoke benchmark covers a multimodal object-property gap,
a datatype-property gap, a no-evolution update, a human-review route and a
low-confidence rejection. It is a functional acceptance set, not evidence of
research effectiveness; larger public datasets and comparative baselines are
still required.
