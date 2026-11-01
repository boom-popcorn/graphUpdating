---
name: odkg-relation-layer
description: Normalize predicates, direction, domain, range and literal/resource semantics for ontology-driven KG updates.
version: 0.1.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  odkg:
    layer: relation
---

# Relation-layer skill

Use after object normalization and before ontology-evolution planning.

## Procedure

1. Resolve the predicate to an existing ontology property when its meaning and
   direction match.
2. For a schema gap, propose one stable predicate IRI and identify its domain
   and range from the typed subject and object.
3. Use an object property for resource objects and a datatype property for
   literals. Include the literal datatype explicitly.
4. Keep inverse, symmetric or transitive semantics out of the proposal unless
   evidence or a reviewed domain rule supports them.

## Invariants

- Predicate direction must be unambiguous.
- Domain/range declarations must agree with the candidate fact.
- Near-synonyms are not merged automatically.
- Relation creation remains subject to rule-layer validation.
