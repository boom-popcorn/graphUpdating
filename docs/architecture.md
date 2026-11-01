# Architecture and integration boundaries

## Core update loop

```text
normalized multimodal observations
              |
              v
semantic perception agent ----> CandidateFact + Evidence
              |
              v
ontology evolution agent -----> KGCL-like OntologyChangeSet
              |                         ^
              v                         |
decision planning agent -------- validation feedback
              |
              v
action execution agent --------> atomic ontology + KG transaction
              |
              v
runtime feedback / human review / next evolution trigger
```

Consistency validation belongs to decision planning. The ontology evolution
agent proposes changes; it does not commit them. The action execution agent only
executes a prepared transaction.

## Adapter rule

Third-party repositories remain isolated. Every integration converts upstream
outputs to or from a core contract:

| Upstream project | Adapter input | Adapter output |
| --- | --- | --- |
| ContinueMKGC | text/image batch | `CandidateFact[]` |
| AdaKGC | schema + text | `CandidateFact[]` |
| KGCL | `OntologyChangeSet` | KGCL CNL/RDF |
| OntoRipple | ontology change + RML/SHACL | propagated artifacts |
| LKGE/IncDE/FastKGE | committed graph delta | refreshed representation report |
| Hermes/DeepSeek Harness | agent task/event | core orchestrator call |

## Safety invariants

- No LLM or extraction model writes the graph directly.
- Every change is version-bound and rejects stale base versions.
- Every write is dry-run validated before commit.
- Ontology and instance updates commit atomically.
- Every committed transaction is reversible.
- Evidence and agent provenance are retained independently of the model runtime.

