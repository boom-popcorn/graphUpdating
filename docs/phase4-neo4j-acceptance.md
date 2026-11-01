# Neo4j 5.26 online acceptance record

Date: 2026-10-23

## Environment

- distribution: Neo4j Community 5.26.28;
- distribution path:
  `D:\Neo4j-5.26\neo4j-community-5.26.28-windows\neo4j-community-5.26.28`;
- Java: Oracle JDK 21.0.8;
- system environment variables: unchanged;
- mutable acceptance data:
  `runtime/neo4j-526` inside this project.

The Codex filesystem boundary permitted the distribution to be read but did
not permit Neo4j to write its own `logs`, `run` and `data` directories. The
acceptance instance therefore uses a project-local `NEO4J_CONF`. Libraries,
plugins, licenses and import files remain read-only under `D:`.

The configuration disables authentication only for this isolated acceptance
instance and binds HTTP/Bolt explicitly to `127.0.0.1`. It is not a deployment
configuration.

## Windows Java selector fix

The first server launch failed in `WEPollSelectorImpl` with:

```text
java.io.IOException: Unable to establish loopback connection
java.net.SocketException: Invalid argument: connect
```

On Windows, Java selects the automatically bound Unix-domain socket directory
from `jdk.net.unixdomain.tmpdir`, then `TEMP`, before `java.io.tmpdir`. The
project start script sets all of them to the short, writable path
`Documents\Codex\.neo4j-tmp-526`. After that change Neo4j opened Bolt on 7687
and HTTP on 7474 successfully.

## Online transaction result

The `phase4-live-526-e2e` isolated store produced the following result:

```text
bootstrap: active=v0.1.0, ontology_triples=6, facts=0
commit:    active=v0.2.0, ontology_triples=10, facts=3, status=committed
rollback:  active=v0.1.0, ontology_triples=6, facts=0
```

This verifies the real Python driver connection, Neo4j 5 composite constraints,
bootstrap publication, versioned RDF projection, active-version switch and
coordinated in-memory rollback. It does not yet measure concurrent writers,
large-graph throughput or crash recovery.

## Re-run

Start the isolated instance in one terminal:

```powershell
.\integrations\neo4j\start-local-526.ps1 console
```

In another terminal:

```powershell
$env:PYTHONPATH = "src"
$env:ODKG_NEO4J_URI = "bolt://127.0.0.1:7687"
$env:ODKG_NEO4J_USER = "neo4j"
$env:ODKG_NEO4J_PASSWORD = "local-auth-disabled"
$env:ODKG_NEO4J_DATABASE = "neo4j"
$env:ODKG_NEO4J_STORE_ID = "phase4-live-526-e2e"
python scripts/neo4j_smoke.py
```

Stop the console with `Ctrl+C`. Do not expose ports 7474 or 7687 outside the
local machine while this acceptance configuration has authentication disabled.
