# Simple Link Analysis

A browser-based network analysis tool for investigative and due-diligence work,
in the spirit of Maltego or Analyst's Notebook.

Entities and relationships are defined by a portable ontology, stored in a Neo4j
knowledge graph, and explored on a canvas where you can lay out, expand and
annotate the network. Data arrives from registry APIs, uploaded documents and
language-model extraction — and nothing enters the database until you accept it.

> **Status: early development.** Milestones M0–M4 are complete: the ontology,
> the graph store, the canvas, and actions with review before anything is
> written.
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

Requires **Python 3.11 or later** and **Docker** (for Neo4j).

The work is on a feature branch; `main` is still empty, so the `-b` is needed.

**macOS / Linux**

```bash
git clone -b claude/browser-network-analysis-tool-mt1vxh https://github.com/outerrelay/simple-link-analysis
cd simple-link-analysis

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .

cp .env.example .env               # then set NEO4J_PASSWORD
docker compose up -d               # one way to get Neo4j — see below
python -m sla.seed --reset         # optional: a small worked example
uvicorn sla.main:app --reload
```

**Windows (Command Prompt)**

```bat
git clone -b claude/browser-network-analysis-tool-mt1vxh https://github.com/outerrelay/simple-link-analysis
cd simple-link-analysis

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .

copy .env.example .env
notepad .env                       :: set NEO4J_PASSWORD, then save and close
docker compose up -d               :: one way to get Neo4j — see below
python -m sla.seed --reset
uvicorn sla.main:app --reload
```

**Windows (PowerShell)** is the same, except activation is
`.venv\Scripts\Activate.ps1`. If that is blocked, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

Open <http://localhost:8000>.

### Getting a Neo4j

**Community Edition is enough.** The app creates only uniqueness constraints
and plain indexes; nothing needs Enterprise, and it uses the single default
database. Neo4j 5.x is required — the constraint syntax is 5-era.

Any instance works. The app takes one connection string, so pick whichever
suits you and set it in `.env`:

| | |
|---|---|
| **Docker** — `docker compose up -d` | One command, reproducible. `docker-compose.yml` reads the password from `.env`. On Windows this means installing Docker Desktop, which needs WSL2 |
| **Neo4j Desktop** | A normal installer, no Docker. Often the easiest route on Windows. Create a 5.x database, set a password, start it |
| **Neo4j Aura** | Nothing installed at all. Hosted, so **your data leaves your machine** — fine for the seed example, worth thinking about for real case material |

```ini
# Docker or Neo4j Desktop
NEO4J_URI=bolt://localhost:7687

# Aura — note the different scheme
NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
```

Everything after that step is identical whichever you chose. If you are not
using Docker, skip `docker compose up -d`; the compose file is left in the
repository because it is still the one-command route for anyone else, and for
deploying to a server later.

`docker-compose.yml` reads the password from `.env`, so it is set in one place.
Set it **before the first `docker compose up`**: Neo4j stores the password when
the volume is first created, and changing it afterwards needs
`docker compose down -v` to discard the volume.

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
the `neo4j.error` field says why. The app starts either way, so the page and
the ontology are served even when the database is down.

### If something is wrong

| Symptom | Cause |
|---|---|
| `required variable NEO4J_PASSWORD is missing` from compose | No `.env` yet — copy `.env.example` |
| `degraded`, connection refused | Neo4j not up yet; it takes a few seconds. `docker compose ps` |
| `degraded`, authentication failure | `.env` password changed after the volume was created. `docker compose down -v`, then up again |
| `constraint that cannot be created` on startup | The database predates the current ontology. Run the matching script in `migrations/` |
| Port 7687 or 8000 already in use | Something else is on it; stop it, or pass `--port` to uvicorn |
| `Failed to build PyYAML pydantic-core` during install | pip found no wheel for your Python and tried to compile from source. Upgrade pip (`pip install --upgrade pip`) and reinstall; if it persists, your Python is newer than any released wheel and you need an older interpreter |
| `cp` / `source` not recognised (Windows) | Those are Unix commands. Use the Windows block above: `copy` and `.venv\Scripts\activate` |
| `docker` not recognised | Docker Desktop is a separate install on Windows and Mac. Either install it, or use Neo4j Desktop or Aura instead and skip the compose step |
| `python3` not recognised (Windows) | Windows installs it as `python`. `py -3.12 -m venv .venv` picks a specific version if you have several |

Dependencies are held to version *ranges* rather than exact pins, so pip can
pick a release with wheels for whichever Python you have. An exact pin only
has wheels for the interpreters that existed when it was published, which is
what makes installs fail on a new Python with a compiler error.

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

## Actions

An **action** is something you invoke on a node from the right-click menu:
expand it from the database, look it up in a registry, later search the news.
Every one has the same shape — declared input and output types, runs as a
background job, and returns a *proposal* rather than writing to the graph.

Maltego calls these "transforms". An action is simply what the menu offers.

### Nothing is written until you accept it

Actions never write to Neo4j. They stage a proposal, which the canvas draws in
dashed amber with a review panel beside it. You accept item by item — accepting
eight of twelve officers is a normal outcome — and only then does anything
reach the database. Rejecting writes a tombstone, so the same suggestion is
never offered twice.

"Auto-commit" is therefore a policy flag meaning *propose and immediately
accept*, not a second code path. It resolves most-specific-first:

1. **per invocation** — the menu offers "Expand" and "Expand — preview first";
2. **per action** — each declares its own default;
3. **global** — `DEFAULT_WRITE_POLICY`, which defaults to `review`.

### Built in

| Action | Needs | Default |
|---|---|---|
| Expand from database | — | auto-commit (the data is already stored) |
| Check for duplicates | — | reports only; never writes |
| Look up LEI (GLEIF) | — | review |
| Companies House: company details | `COMPANIES_HOUSE_API_KEY` | review |
| Companies House: officers | `COMPANIES_HOUSE_API_KEY` | review |

Actions whose credentials are missing still appear in the menu, marked
unavailable with the reason, rather than silently disappearing.

> **The two registry connectors have not been run against the live APIs.**
> Outbound access to both is blocked from the development environment, so they
> are written to the published response formats and tested against fixtures.
> Expect to correct details on first real use.

### Adding one

Implement the interface, register it, import it in `main.py`:

```python
class MyLookup:
    id = "my.lookup"
    label = "Look up somewhere"
    description = "What this does."
    input_types = ("Company",)        # resolved through the ontology
    output_types = ("Person",)
    default_policy = WritePolicy.REVIEW
    requires = ("my_api_key",)        # settings that must be set

    async def run(self, context: ActionContext) -> Proposal:
        ...

register(MyLookup())
```

`input_types` resolves through the ontology, so declaring `LegalEntity` offers
the action on companies and organisations alike.

## Adding things by hand

Right-click empty canvas for **Add an entity here**, or select two nodes and
right-click for **Connect these two**.

Both dialogs are built from the ontology. The type list is what the ontology
declares; the fields are that type's properties, with enums as selects, dates
as date pickers, required fields first and personal data marked; and a
connection is only offered where the ontology permits one, in that direction.
Add a property to `ontology.yaml` and it appears on the form at the next
reload — no JavaScript changes.

Manual creation writes **straight through** rather than staging a proposal:
you are asserting it, so there is nothing to review. The ontology still
applies, so an undeclared property or a forbidden connection is refused with
the reason. Typing in a phone number somebody else already has attaches to the
existing node. If the new record looks like a duplicate you are told, and
nothing is done about it.

## Duplicates and merging

Nothing is ever merged automatically. Two operations, deliberately separate:

**"Check for duplicates"** on any node asks whether the same thing is already
in the database. Matches come back strongest first, each with the reason — a
shared issued identifier, the same registration number, a similar name after
legal-form suffixes are stripped so that *Acme AS* and *ACME A/S* meet. The
reason is shown because a bare similarity score tells you nothing you can
check. Anything arriving from a registry is checked automatically once
accepted; candidates go to the review queue.

**Merge** combines two records of the same type, and only when you say so:

- You choose which record survives, and can swap the direction.
- Conflicting properties keep the survivor's values by default, or you can
  choose field by field. Whichever name loses is kept as an alias.
- Relationships move across; duplicates collapse into one edge citing both
  assertions; an edge between the two is dropped rather than becoming a loop.
- The absorbed record is **kept and hidden**, not deleted, and every merge can
  be undone from the merge history.

## Removing things, in full

| Operation | Effect |
|---|---|
| Remove from chart | Off the canvas; untouched in the database |
| Delete from database | Tombstoned: gone from every chart and query, and re-importing will not bring it back |
| Hard delete (`?suppress=false`) | Node, edges and assertions removed outright |
| Merged away | Hidden and marked `merged_into` the survivor; undoable |

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

On Windows the variable is set separately — `set SLA_ALLOW_DESTRUCTIVE_TESTS=1`
in Command Prompt, or `$env:SLA_ALLOW_DESTRUCTIVE_TESTS=1` in PowerShell — and
then `pytest` on the next line.

## Roadmap

| Milestone | Scope | Status |
|---|---|---|
| **M0** | Project skeleton, Neo4j via Docker, health check | ✅ Done |
| **M1** | Ontology file, validation, code generation | ✅ Done |
| **M2** | Graph store, assertion layer, temporal queries, `SAME_AS` detection | ✅ Done |
| **M3** | Canvas: icons, drag, multi-select, layouts over selections | ✅ Done |
| **M4** | Staging and review, expand actions, first registry connectors | ✅ Done |
| M5 | Language-model actions: online search, registry routing | Next |
| M6 | Document and spreadsheet ingestion with ontology mapping | Deferred |

M0–M4 form the first usable tool; we reassess before M5.

## Configuration

All settings are read from the environment or a local `.env` file — see
[`.env.example`](.env.example). API credentials are never sent to the browser.

| Variable | Purpose |
|---|---|
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | Knowledge graph connection |
| `APP_DATABASE_URL` | Application state; SQLite locally, PostgreSQL for multi-user |
| `DEFAULT_WRITE_POLICY` | `review` (default) stages action output; `auto_commit` accepts it immediately |
| `ANTHROPIC_API_KEY` | Language-model features (M5) |
| `COMPANIES_HOUSE_API_KEY` | UK registry connector (M4) |
