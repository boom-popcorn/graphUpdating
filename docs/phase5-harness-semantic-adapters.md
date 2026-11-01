# Phase 5: Hermes runtime and comparable semantic adapters

## Scope completed

The semantic-perception boundary now supports four comparable paths without
changing evidence fusion, ontology-gap detection, evolution policy, validation,
atomic commit, version persistence or rollback:

1. `rule-text-v1`: the deterministic regular-expression baseline.
2. `openai-compatible-llm-v1` and `openai-compatible-vlm-v1`: structured JSON
   extraction against any explicitly configured OpenAI-compatible endpoint.
3. `adakgc-record-v1`: import of AdaKGC `*_preds_record.txt` JSONL records.
4. `continuemkgc-mre-v1`: import of exported ContinueMKGC MRE predictions paired
   with their native `h`, `t`, `token` records.

Every path emits the same `Observation` contract. Evidence records now carry
`extractor_id`, `model_id` and adapter metadata, so fused facts remain traceable
to the exact front end used in an experiment.

## Important upstream boundary

AdaKGC and ContinueMKGC are research baselines, not drop-in online services.

- AdaKGC documents Python 3.8, Torch 1.8/CUDA, an external mixed T5/UIE model and
  external datasets. Its inference script writes `*_preds_record.txt`; the
  adapter reads that native result directly.
- ContinueMKGC (MSPT) is a continual MNER/MRE training and evaluation codebase.
  Its public MRE loop computes predicted label IDs and metrics but does not
  expose an online inference endpoint or persist per-example predictions. A
  thin exporter must pair the predicted relation label/confidence with the
  original native record before `continuemkgc-mre-v1` can consume it.

The supplied `scripts/export_continuemkgc_predictions.py` performs that merge
from a native record file, one prediction label/ID per line, `rel2id` JSON and
an optional confidence file. Only the small upstream evaluation loop needs to
write its `pred_labels` (and, when available, softmax maxima) to those files.

Keeping these legacy GPU environments out of the transaction service is
intentional: baseline failure cannot corrupt ontology/KG state, and their
outputs can be replayed exactly during an experiment.

## LLM/VLM configuration

Remote or local model calls are disabled unless both endpoint and model are
set. The two model paths use separate variables so they can be compared:

```powershell
$env:ODKG_LLM_BASE_URL = "http://127.0.0.1:8000/v1"
$env:ODKG_LLM_MODEL = "text-model"
$env:ODKG_LLM_API_KEY = "optional"

$env:ODKG_VLM_BASE_URL = "http://127.0.0.1:8001/v1"
$env:ODKG_VLM_MODEL = "vision-model"
$env:ODKG_VLM_API_KEY = "optional"
```

Artifacts use modality `llm_text` or `vlm_image`. A VLM artifact supplies an
`image_url` in metadata; this may be an endpoint-supported HTTP URL or data URL.
The model is asked for full IRIs and evidence locations. Unknown schema IRIs are
allowed only as proposals: the existing evolution policy and validation path
still decides whether they are accepted, reviewed or rejected.

The prompt context is resolved from the active ontology on every model call,
not cached at service startup. After a successful ontology commit, the next
LLM/VLM extraction therefore receives the new ontology version, classes and
properties.

## Fair comparison

`compare_semantic_adapters` accepts adapter-specific materializations of the
same benchmark case, computes extraction precision/recall/F1 and latency, then
runs every extracted observation through the same downstream dry-run cycle.
This separates semantic-extraction quality from ontology-evolution and
transaction quality.

## Hermes MCP integration

The checked Hermes source accepts stdio servers under `mcp_servers` with
`command`, `args`, `env` and optional tool filters. The previous editor-style
placeholders and non-Hermes fields were removed. Generate an executable config:

```powershell
.\.venv-hermes\Scripts\python.exe scripts\render_hermes_config.py
```

This writes `runtime/hermes-home/config.yaml` with absolute paths. After Hermes
is installed into the same isolated environment, verify discovery using:

```powershell
integrations\hermes\test-mcp.ps1
```

Hermes can orchestrate the eleven MCP tools, while the domain workflow remains
inside the deterministic service. An LLM API credential is needed only for an
actual conversational Hermes session, not for `hermes mcp test` or the MCP
protocol smoke test.

## Acceptance criteria

- Python 3.14 full test suite passes, including the MCP client test.
- Independent stdio client discovers all eleven tools and invokes version and
  policy calls.
- Hermes `mcp test ontology_driven_kg` discovers the configured tools.
- A model/baseline extraction failure becomes an `IngestionIssue`; it never
  silently falls back to another method or enters the commit path.

## Verified local acceptance (2026-10-23)

- Runtime: Python 3.14.8 in `.venv-hermes`.
- Hermes: local `hermes-agent-main.zip`, installed editable as required by its
  own build guard; ZIP SHA-256
  `B12AFE5E78F2A659A493A78AE7571A068C0A1C7CD76EB210EAFB7371BE42DDAA`.
- Dependency integrity: `pip check` reports `No broken requirements found`.
- Test suite: `33 passed` on Python 3.14.8, including dynamic ontology prompt
  refresh and the in-process MCP client.
- Compilation: `python -m compileall -q src scripts` exits successfully.
- Independent stdio smoke test: protocol `2026-07-28`, server
  `ontology-evolution-kg`, eleven tools discovered, version and policy calls
  returned structured results.
- Hermes-native test: `hermes mcp test ontology_driven_kg` connected in
  16,933 ms and discovered all eleven tools.

This proves the Harness-to-MCP control path. It does not claim model-quality
results: a real LLM/VLM endpoint, AdaKGC checkpoint/dataset or ContinueMKGC
checkpoint/dataset must still be supplied for comparative accuracy experiments.

