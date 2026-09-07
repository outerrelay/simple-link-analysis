# Simple Link Analysis

A browser-based network analysis tool for investigative and due-diligence work,
in the spirit of Maltego or Analyst's Notebook.

Entities and relationships are defined by a portable ontology, stored in a Neo4j
knowledge graph, and explored on a canvas where you can lay out, expand and
annotate the network. Data arrives from registry APIs, uploaded documents and
language-model extraction — and nothing enters the database until you accept it.

> **Status: early development.** Milestone M0 (project skeleton) is complete.
> See [docs/decisions.md](docs/decisions.md) for the design rationale.

## Design in one minute

- **The ontology is a single YAML file.** `ontology/ontology.yaml` defines every
  entity and relationship type. Code generation turns it into Pydantic models,
  JSON Schema, Neo4j constraints and the canvas's styling — so other tools can
  adopt the same data model by reading one file.
- **Two stores, deliberately.** Neo4j holds the knowledge graph and nothing
  else. Charts, node positions, jobs and staged proposals live in SQLite. This
  is why removing a node from the canvas and deleting it from the database are
  different operations.
- **Nothing is written unreviewed.** Transforms return proposals, rendered on
  the canvas as provisional. You accept or reject; only accepted data reaches
  Neo4j. Auto-commit is a policy flag, not a separate path.
- **Every fact traces to a source.** Relationships are backed by assertions that
  record where a claim came from, how confident it is and when it was observed —
  and relationships carry validity dates, because directorships end.

## Requirements

- Python 3.11+
- Docker (for Neo4j)

## Getting started

```bash
git clone https://github.com/outerrelay/simple-link-analysis
cd simple-link-analysis

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .

cp .env.example .env          # then edit NEO4J_PASSWORD
docker compose up -d          # starts Neo4j on 7687 (Bolt) and 7474 (browser)

uvicorn sla.main:app --reload
```

Check that the app can reach the database:

```bash
curl -s localhost:8000/health
```

```json
{
  "status": "ok",
  "app_version": "0.1.0",
  "neo4j": {"connected": true, "name": "Neo4j Kernel", "version": "5.26.0", "edition": "community"}
}
```

A `status` of `degraded` means the app is running but Neo4j is not reachable;
the `neo4j.error` field says why.

## Development

```bash
pytest              # unit tests, no database required
ruff check .        # lint
ruff format .       # format
```

## Roadmap

| Milestone | Scope | Status |
|---|---|---|
| **M0** | Project skeleton, Neo4j via Docker, health check | ✅ Done |
| **M1** | Ontology file, validation, code generation | Next |
| **M2** | Graph store, assertion layer, temporal queries, `SAME_AS` detection | |
| **M3** | Canvas: icons, drag, multi-select, layouts over selections | |
| **M4** | Context menu, expand from database, staging and review, first registry connectors | |
| M5 | Language-model transforms: online search, registry routing | Deferred |
| M6 | Document and spreadsheet ingestion with ontology mapping | Deferred |

M0–M4 form the first usable tool; we reassess before M5.

## Configuration

All settings are read from the environment or a local `.env` file — see
[`.env.example`](.env.example). API credentials are never sent to the browser.

| Variable | Purpose |
|---|---|
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | Knowledge graph connection |
| `APP_DATABASE_URL` | Application state; SQLite locally, PostgreSQL for multi-user |
| `DEFAULT_WRITE_POLICY` | `review` (default) stages transform output; `auto_commit` accepts it immediately |
| `ANTHROPIC_API_KEY` | Language-model features (M5) |
| `COMPANIES_HOUSE_API_KEY` | UK registry connector (M4) |
