---
name: odkg-object-layer
description: Identify evidence-grounded entities and candidate ontology classes for ontology-driven knowledge graph updates.
version: 0.1.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  odkg:
    layer: object
---

# Object-layer skill

Use this skill when observations must be normalized into typed entities before
an ontology-driven KG update.

## Input contract

Each observation must contain `subject`, `subject_type`, `predicate`, `object`,
`confidence`, and at least one evidence item. State explicitly whether the
object is a resource or literal. Keep source identifiers and media locators.

## Procedure

1. Resolve repeated mentions to stable IRIs without merging ambiguous entities.
2. Select the narrowest evidence-supported type; do not create a new class only
   because a surface phrase is novel.
3. Preserve confidence and every evidence link.
4. Pass the normalized observation to the relation-layer skill.

## Invariants

- Never discard provenance.
- Never use a literal as an entity IRI.
- A new class is a candidate, not an accepted ontology change.
- If identity or type is ambiguous, mark it for review instead of guessing.
