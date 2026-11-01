---
name: odkg-rule-layer
description: Apply OWL, SHACL and configurable evolution-policy checks to candidate ontology and KG updates.
version: 0.1.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  odkg:
    layer: rule
---

# Rule-layer skill

1. Call `get_evolution_policy` and retain its `policy_id`.
2. Check confidence, independent-source count, modality count and blocked
   namespaces.
3. Call `preview_update_cycle` before every mutating update.
4. Inspect OWL structural findings and SHACL results in the preview.
5. Require human approval whenever the preview says
   `requires_human_review=true`.

Do not treat an LLM judgment as an OWL/SHACL consistency proof. Do not bypass a
policy rejection. Validation failure returns to planning and is never silently
committed.
