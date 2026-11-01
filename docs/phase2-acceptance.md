# Phase 2 acceptance record

This phase turns the framework-neutral prototype into an auditable vertical
slice with persistent artifacts and real upstream-tool integration.

## Verified on 2026-10-22

### Core regression suite

The core suite covers update commit/rollback, datatype properties, KGCL
rendering and parser handling, OCH generation, persistent version/audit storage,
and the framework-neutral MCP facade.

### Official KGCL parser

The generic evolution case generated five KGCL statements: one class creation,
one property creation, its object-property declaration, domain declaration and
range declaration. The upstream `kgcl_schema` parser accepted all five.

### OntoRipple propagation

The bundled old RML mapping (14 triples) and SHACL graph (4 triples) were
propagated using the upstream OntoRipple implementation. The outputs contain 21
mapping triples and 10 shape triples. Semantic inspection confirmed:

- `ex:fundedBy` is present as an RML predicate;
- the mapping links the research-project map to the organization map;
- a `fundedBy` SHACL property shape was generated;
- its `sh:path` is `ex:fundedBy` and `sh:class` is `ex:Organization`.

Generated runtime files live under `runtime/ontoripple-demo/` and are ignored by
version control. They can be regenerated with the commands in
`standards-integration.md`.

## Scope boundary

This proves the change-representation and asset-propagation path. It does not
yet prove multimodal extraction quality, concurrent-agent scheduling, or
large-scale graph performance. Those belong to the next experimental phases and
must be evaluated separately.
