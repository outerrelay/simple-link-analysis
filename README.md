# Simple Link Analysis

A browser-based network analysis tool for investigative and due-diligence work,
in the spirit of Maltego or Analyst's Notebook.

Entities and relationships are defined by a portable ontology, stored in a Neo4j
knowledge graph, and explored on a canvas where you can lay out, expand and
annotate the network. Data arrives from registry APIs, uploaded documents and
language-model extraction — and nothing enters the database until you accept it.

> **Status: early development.** Milestones M0–M3 are complete: the ontology,
> the graph store and a working canvas.
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

## Working with the ontology

`ontology/ontology.yaml` is the data model. Edit it, then regenerate:

```bash
sla-ontology validate       # is it coherent?
sla-ontology generate       # rewrite the derived artefacts
sla-ontology check          # fail if anything is out of date (for CI)
sla-ontology show Company   # describe one type
```

Generated artefacts are committed, so a fresh clone works without a build step:

| Artefact | Consumer |
|---|---|
| `src/sla/ontology/generated/models.py` | Pydantic models for type checking and validation |
| `ontology/build/ontology.schema.json` | Other tools, and structured output from language models |
| `ontology/build/constraints.cypher` | Neo4j constraints and indexes |

The canvas reads the ontology from `GET /api/ontology` at runtime rather than
from a generated file.

### Before you change the ontology

Regenerating updates the code. It does **not** update data already in Neo4j.
`sla-ontology diff` says which of your changes are safe:

```bash
sla-ontology diff git:HEAD          # compare against the committed version
```

```
Additive (2) — safe to apply to existing data:
  + Company.employee_count: new optional property
  + LegalCase.status: enum values added: ['stayed']

Breaking (1) — existing data needs migrating:
  ! Person.birth_date: property removed
      migration: Drop 'birth_date' from existing Person nodes. If this is a
                 rename, copy the value to the new property first.
```

It exits non-zero when a change is breaking.

## The graph store

Every relationship is derived from an **assertion** recording where the claim
came from, with what confidence, by what method. Creating one is the only way
an edge enters the graph:

```python
assertion, edge = await repository.assert_relationship(
    predicate="OWNS",
    subject_id=person.id,
    object_id=company.id,
    source_id=document.id,          # the evidence
    method=ExtractionMethod.LANGUAGE_MODEL,
    confidence=0.85,
    valid_from=date(2019, 3, 1),    # when it became true
)
```

An edge exists exactly while at least one assertion supports it. Two documents
saying the same thing produce one edge with two supporting claims; retracting
either leaves the edge standing, and retracting the last removes it.

Expansion hides sources unless asked, filters by relationship type, and answers
as of a date:

```python
await repository.expand([person.id], depth=2, as_of=date(2020, 1, 1))
```

### Removing things

Three distinct operations, only two of which live here:

| Operation | Effect |
|---|---|
| Remove from chart | Application store only; the graph is untouched |
| `suppress_entity` | Tombstone: hidden from every query, and re-ingestion will not resurrect it |
| `delete_entity` | Gone, with its edges and the assertions about it. Sources survive |

### Duplicates

`IdentityResolver` proposes, and never merges. Two entities sharing an
identifier — or agreeing on a property the ontology declares as a strong
identifier — get a `SAME_AS` relationship with status `candidate`. A human
confirms or rejects; a decision already recorded is never overwritten by
re-running detection, and confirming still does not merge the nodes.

## The canvas

```bash
python -m sla.seed --reset   # a small worked example to look at
uvicorn sla.main:app --reload
```

Then open <http://localhost:8000>. Search for an entity to put it on the
canvas, and right-click it to expand.

- **Layouts** — organic, hierarchy, tree, circle, concentric and grid, each
  runnable over the whole chart or **only the current selection**. A selection
  layout tidies the selected nodes within the space they already occupy rather
  than rearranging the chart around them.
- **Selection** — click, shift-click, or drag a box.
- **Right-click** — expand one or two hops, expand by a specific relationship
  type, reveal sources, or remove from the chart. The relationship types
  offered come from the ontology, so a Person is never offered "issued tender".
- **Remove from chart** takes a node off the canvas and leaves it in the
  database. Deleting from the database is a separate, deliberate action.

Nothing in the canvas hard-codes an entity type. Add one to `ontology.yaml`,
drop an SVG in `ontology/icons/`, and it appears with its icon, colour, legend
entry and menu actions on the next reload.

## Development

```bash
pytest              # graph tests skip automatically when Neo4j is not running
ruff check .        # lint
ruff format .       # format
```

Tests that need a database use the `repository` fixture, which skips when none
is reachable, so a fresh clone runs green without Docker. Start Neo4j with
`docker compose up -d` to exercise them.

**The graph tests clear the database between cases.** If the configured
database already holds data they skip rather than wipe it. To run them:

```bash
SLA_TEST_NEO4J_URI=bolt://localhost:7688 pytest   # a scratch instance
SLA_ALLOW_DESTRUCTIVE_TESTS=1 pytest              # this database is disposable
```

## Roadmap

| Milestone | Scope | Status |
|---|---|---|
| **M0** | Project skeleton, Neo4j via Docker, health check | ✅ Done |
| **M1** | Ontology file, validation, code generation | ✅ Done |
| **M2** | Graph store, assertion layer, temporal queries, `SAME_AS` detection | ✅ Done |
| **M3** | Canvas: icons, drag, multi-select, layouts over selections | ✅ Done |
| **M4** | Staging and review, expand transforms, first registry connectors | Next |
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
