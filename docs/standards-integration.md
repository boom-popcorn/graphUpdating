# Standards integration

The core deliberately separates two change representations:

- KGCL controlled language is the reviewable change plan and audit artifact.
- OCH RDF is the machine input used by OntoRipple to propagate ontology changes
  into RML mappings and SHACL shapes.

Every committed version package contains both `changes.kgcl` and
`changes.och.ttl`. Treating them as interchangeable would lose semantics that
OntoRipple requires, such as explicit domain/range change nodes.

## Official KGCL parser

`odkg.adapters.kgcl.validate_with_official_parser` imports the upstream
`kgcl_schema.grammar.parser.parse_statement` function when `kgcl_schema` is
installed. If it is missing, the report says `available=false`; the system does
not claim that official validation succeeded.

Keep the downloaded KGCL repository isolated and install it into a standards
environment rather than copying its source into `odkg`:

```powershell
python -m venv .venv-standards
.\.venv-standards\Scripts\python.exe -m pip install -e upstream\kgcl
```

## OntoRipple

`to_och_graph` generates OntoRipple-compatible change RDF for the supported
operations. `propagate` launches OntoRipple without a shell, requires every
declared input to exist, and parses both output files before returning an impact
report.

Install the downloaded OntoRipple source into its own environment:

```powershell
.\.venv-standards\Scripts\python.exe -m pip install -e upstream\ontoripple
```

The adapter accepts that environment's Python executable, so OntoRipple's
dependencies remain outside the core system environment.

An executable generic example is included:

```powershell
$env:PYTHONPATH="src"
.\.venv-standards\Scripts\python.exe scripts\run_ontoripple_demo.py `
  --ontoripple-python .\.venv-standards\Scripts\python.exe
```

It creates the OCH change graph and evolved ontology, invokes OntoRipple, then
parses the generated mapping and shape files before reporting their triple
counts.

The KGCL path has a separate executable acceptance check:

```powershell
$env:PYTHONPATH="src"
.\.venv-standards\Scripts\python.exe scripts\verify_kgcl.py
```

For the bundled generic `ResearchProject fundedBy Organization` case, the
acceptance criteria are:

- all five generated KGCL statements are accepted by the official parser;
- OntoRipple adds `fundedBy` to the RML mapping;
- OntoRipple adds a SHACL property shape whose path is `fundedBy` and whose
  class is `Organization`.

## Persistent runtime

Set `ODKG_RUNTIME_DIR` before starting the MCP server. Each committed version is
stored under `versions/<version>/` with ontology, facts, shapes, KGCL, OCH and a
hash manifest. All accepted and rejected transactions are stored separately,
and every agent event is appended to `audit/events.jsonl`.

