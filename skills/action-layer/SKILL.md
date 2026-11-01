---
name: odkg-action-layer
description: Execute, verify and roll back audited ontology-driven KG update transactions through MCP.
version: 0.1.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  odkg:
    layer: action
---

# Action-layer skill

1. Compare the active ontology version with the preview's base version.
2. Call `run_update_cycle`; set `human_approved=true` only after real human
   approval.
3. Verify transaction status, target version and manifest hashes.
4. Check that `get_graph_status` matches the committed ontology version.
5. If verification fails, stop writes and use `rollback_latest` only for the
   latest transaction.

Never reinterpret `prepared` as `committed`, or retry an uncertain mutating
call before checking status. Preserve KGCL, OCH, ontology, facts, shapes and
manifest as one version unit.
