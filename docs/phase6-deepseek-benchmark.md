# Phase 6: DeepSeek API and unified semantic benchmark

## DeepSeek configuration

The project automatically reads an untracked `key.env` at the repository root.
The following aliases are accepted:

```dotenv
OPENAI_API_KEY=your-key
OPENAI_API_BASE=https://api.deepseek.com/v1
```

`key.env` and `.env` are explicitly ignored. The parser performs no shell
evaluation or variable expansion. For a DeepSeek address, it maps these aliases
to both ODKG text and vision adapters and defaults the model to
`deepseek-flash`.

The project uses DeepSeek through its OpenAI-compatible Chat Completions API.
As a safer alternative to a plaintext env file, the helper below asks for the
key securely and sets process-scoped environment variables only:

```powershell
cd "C:\Users\run'wu'xi'wu's\Documents\Codex\2026-10-15\ni\ontology-driven-kg-system"
.\integrations\deepseek\configure-session.ps1
python scripts\check_deepseek_api.py
```

The default is `deepseek-flash`, `https://api.deepseek.com`, JSON output,
non-thinking mode and a 2048-token output limit. Both the text and vision
adapters are configured because the current Flash endpoint supports both input
modalities. Override the model only when `/models` confirms that the account
can use it:

```powershell
.\integrations\deepseek\configure-session.ps1 -Model deepseek-v4-pro -TextOnly
```

## End-to-end preview

The first run is deliberately non-mutating:

```powershell
python scripts\run_deepseek_e2e.py `
  --output runtime\deepseek-e2e-preview.json
```

It executes model extraction, canonical normalization, evidence fusion,
ontology-gap detection, policy checks and validation. It does not commit.
After inspecting the preview, the explicit commit path is:

```powershell
python scripts\run_deepseek_e2e.py --commit --human-approved `
  --output runtime\deepseek-e2e-commit.json
```

Human approval is explicit because the single-document smoke example does not
meet the default two-source evidence rule.

## Unified adapter smoke benchmark

Run the deterministic rule and replay adapters without network or API cost:

```powershell
python scripts\run_semantic_benchmark.py `
  --output runtime\semantic-smoke-offline.json
```

Include DeepSeek text and vision calls after configuring the session:

```powershell
python scripts\run_semantic_benchmark.py --require-online `
  --output runtime\semantic-smoke-all.json
```

`experiments/semantic_smoke_cases.json` contains English positive, Chinese
positive and negative cases. All adapters are scored against the same triples
and all accepted triples enter the same downstream dry-run cycle.

This is an adapter-contract smoke set, not a publishable model benchmark. Its
AdaKGC and ContinueMKGC entries are replay fixtures. For a real comparison,
replace those entries with native checkpoint predictions over a fixed,
human-annotated test split; preserve the test split, ontology version, prompt,
model identifier and raw prediction artifacts.

The gold schema must distinguish three states rather than forcing every
sentence into binary relation/no-relation labels:

1. a relation already present in the active ontology;
2. a supported relation that is absent from the ontology and is therefore a
   valid schema-gap/evolution proposal;
3. a genuine no-relation example containing no supported relation statement.

## Verified acceptance (2026-10-24)

- `key.env` aliases loaded without printing or persisting the credential in a
  generated report.
- `GET /v1/models` authenticated successfully and returned
  `deepseek-flash` and `deepseek-v4-pro`.
- The non-mutating end-to-end preview extracted `fundedBy` with confidence
  `0.95`, proposed `ResearchProject` and `fundedBy`, and passed structural plus
  SHACL validation. Policy correctly required human review because only one
  distinct evidence source was present.
- The corrected three-case smoke set passed through rule, DeepSeek LLM,
  DeepSeek VLM, AdaKGC replay and ContinueMKGC replay with no adapter issues.
  All five paths scored 1.0 on this deliberately small contract test.
- The initial negative sentence expressed a real `discussed` relation and was
  therefore not a valid open-world negative. It was replaced by a pure entity
  co-mention. This correction must be preserved when constructing the real
  benchmark.
- Full local test suite: 38 passed.

## Downloads

Nothing else must be downloaded for the DeepSeek API stage. The user needs only
a DeepSeek API key and an account with available quota.

Later baseline reproduction requires separate assets:

- AdaKGC: the authors' mixed UIE/T5 initialization, `t5-v1_1-base`, and either
  the authors' benchmark data or a converted domain dataset.
- ContinueMKGC: Twitter2017 and MEGA source data, BERT/CLIP base models, and a
  checkpoint trained by us (the public repository is primarily training and
  evaluation code, not a universal maritime checkpoint).
- Maritime evaluation: a versioned ontology and a human-labelled split with
  text/AIS/image/track evidence. This should be built before claiming domain
  accuracy.

