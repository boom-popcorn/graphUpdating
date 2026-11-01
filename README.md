# Ontology-driven multi-agent knowledge graph update system

This repository is the framework-neutral core of an ontology-evolution-driven
multi-agent knowledge graph update method. It deliberately keeps third-party
Harnesses and research baselines behind adapters.

## Implemented vertical slice

1. A pluggable ingestion layer extracts and conservatively fuses text,
   structured and external vision-model evidence into normalized observations.
2. Semantic perception emits evidence-bearing candidate facts.
3. Ontology evolution detects schema gaps and emits a KGCL-like change set.
4. Decision planning performs consistency checks, SHACL validation and a dry run.
5. Action execution atomically commits ontology and instance changes, records
   provenance and supports rollback.
6. The orchestrator returns runtime feedback that can trigger another evolution
   cycle or human review.
7. Committed versions persist ontology, facts, shapes, KGCL, OCH RDF and a
   SHA-256 manifest; rejected transactions remain auditable.
8. A configurable evolution policy routes proposals to accept, review or reject.
9. Optional Neo4j snapshots and four Harness-facing Skills sit behind stable
   interfaces rather than changing the transaction core.
10. Rule, OpenAI-compatible LLM/VLM, AdaKGC-result and ContinueMKGC-result
    adapters normalize into the same evidence-bearing observation contract.
11. A process-scoped DeepSeek configuration, end-to-end model preview and
    unified adapter smoke benchmark exercise the complete semantic-to-update
    path without storing API credentials.

## Run

```powershell
python -m pip install -e ".[dev]"
python -m odkg.demo
python scripts/run_multimodal_demo.py
pytest
```

`pyshacl` is used when installed. A deterministic structural validator remains
available so that the core and tests can run before optional dependencies are
installed.

## Maritime domain package

The generic transaction core can run with the merged maritime ontology and
SHACL package. Set `ODKG_DOMAIN=maritime` before starting `odkg-mcp` to make
the maritime ontology active. The MCP server exposes `preview_maritime_events`
and `run_maritime_events` for normalized AIS/radar events. The first tool is a
non-mutating dry run; the second uses the same policy, validation, versioning,
audit, Neo4j publication and rollback path as generic updates.

The first maritime rule detects hovering only when low-speed evidence spans at
least 600 seconds and comes from at least two modalities. Raw events require
timezone-aware timestamps, bounded WGS84 coordinates and explicit modality.

The complete maritime application is installed in `odkg.maritime`. It adds
versioned graph operations for object creation, property updates, relation and
event creation, fact invalidation and rollback; incremental batch deduplication;
schema-drift proposals; human-approved ontology evolution; persistent Turtle
snapshots; and the interactive review workbench. Start it with:

```powershell
odkg-workbench
```

Then open `http://127.0.0.1:8876` to inspect targets and tracks, receive data,
review candidate facts, approve ontology evolution, inspect versions and roll
back a graph snapshot.

## Model-driven multi-agent Harness

`odkg.llm_harness.LLMMultiAgentHarness` runs three independent model roles with
different prompts: semantic perception, ontology evolution and decision
planning. Each call records the model, prompt version, input hash, timestamps,
decision and rationale. OWL/SHACL validation and action execution remain
deterministic; model output can never directly write the graph. A non-preview
run still requires `human_approved=true`.

Hermes can orchestrate the role-level MCP tools `llm_semantic_perception`,
`llm_ontology_evolution`, `llm_decision_planning`, followed by the guarded
execution surface. `run_llm_multi_agent_maritime` provides the same complete
chain for repeatable demonstrations. The server fails explicitly when no model
endpoint is configured instead of silently substituting deterministic logic.

## Upstream integration policy

- KGCL: change-language adapter.
- OntoRipple: ontology-change impact propagation to RML/SHACL.
- ContinueMKGC and AdaKGC: semantic-perception adapters.
- LKGE, IncDE and FastKGE: post-commit representation-refresh baselines.
- Hermes and DeepSeek Harness: runtime adapters over the same agent contracts.
- Claude Code: development aid only, not a runtime dependency.

See [standards integration](docs/standards-integration.md) for the KGCL/OCH
boundary, isolated OntoRipple execution and persistent runtime layout.
The [phase 2 acceptance record](docs/phase2-acceptance.md) records the verified
upstream-parser and mapping/shape-propagation results, as well as the remaining
scope limits.
See [phase 3 implementation](docs/phase3-implementation.md) for policy, Neo4j,
benchmark and safety-preview details, and [Harness integration](docs/harness-integration.md)
for the Hermes MCP boundary.
See [phase 4](docs/phase4-multimodal-mcp.md) for the raw-artifact ingestion
contract, stdio MCP acceptance script and compatible Neo4j deployment path.
The [Neo4j 5.26 acceptance record](docs/phase4-neo4j-acceptance.md) contains the
verified online commit and rollback results for the local Windows installation.
See [phase 5](docs/phase5-harness-semantic-adapters.md) for the real Hermes MCP
configuration, model adapter contract, research-baseline boundaries and fair
comparison protocol.
See [phase 6](docs/phase6-deepseek-benchmark.md) for secure DeepSeek session
configuration, the first model-backed end-to-end run and the unified semantic
benchmark workflow.

