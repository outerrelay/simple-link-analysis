# Design decisions

The choices below were made before implementation started. They are recorded
here because most of them are expensive to reverse, and because anyone reading
the code later will otherwise have to guess at the reasoning.

## 1. The ontology is a hand-written YAML file, aligned to FollowTheMoney

`ontology/ontology.yaml` is the single source of truth for entity types,
relationship types, their properties and their display. Code generation turns
it into Pydantic models, JSON Schema and Neo4j constraints; the canvas reads
the ontology from `GET /api/ontology` at runtime rather than from a generated
JavaScript file, which is one fewer artefact that can fall behind its source.

Generated artefacts are **committed**, so a fresh clone works without a build
step, and a change to the ontology shows up as a reviewable diff in the code it
affects. `sla-ontology check` fails when they have drifted, and a test asserts
the same thing.

The structure stays compatible with [FollowTheMoney][ftm], the model behind
OpenSanctions and Aleph, and `ontology/ftm-mapping.yaml` records the
correspondence. This buys interoperability with an established ecosystem
without adopting its conventions wholesale.

*Rejected:* adopting FollowTheMoney directly (inherits conventions we may not
want), and LinkML (more machinery than this project needs).

[ftm]: https://followthemoney.tech/

## 2. Neo4j stores the knowledge graph

The tool's core operations — expand a node, find paths, traverse ownership
chains — are graph traversals, and Cypher expresses them directly. Neo4j runs
as a single container.

*Note:* Neo4j Community Edition is AGPL. That constrains distribution, not
internal use.

## 3. Application state is stored separately from the graph

Charts, node positions, jobs, staged proposals, the review queue and (later)
users live in SQLite, not Neo4j. Two reasons: the knowledge graph stays clean
and portable for other tools to consume, and moving to PostgreSQL for a
multi-user deployment becomes a connection-string change.

This separation is also what makes "remove from canvas" and "delete from the
database" genuinely different operations: the first is a write to SQLite, the
second a write to Neo4j.

## 4. Node identity is always a UUID

Every node is keyed by a generated UUID. Strong identifiers — LEI, company
registration number, passport number — are never used as primary keys.

Instead they are modelled as their own nodes (`(:Identifier {scheme, value})`
linked by `HAS_IDENTIFIER`), which makes duplicate detection a single Cypher
query over entities sharing an identifier, and gives identifiers their own
provenance.

Those nodes are **canonical**: keyed by `(scheme, value)` rather than by a fresh
UUID, enforced by a uniqueness constraint. Two companies bearing the same LEI
therefore point at the *same* node, and the candidate query is one hop. Minting
a node per mention would leave nothing shared and detection would never fire —
which is exactly what the first version did until a test caught it.

Matches are **never merged automatically.** They produce a `SAME_AS` candidate
that enters the review queue. A human confirms or rejects. Hard merges destroy
provenance and cannot be undone; in due-diligence work a false merge is a
serious error.

## 5. Provenance is a hybrid of direct edges and an assertion layer

Neo4j cannot attach a relationship to another relationship, so the question
"which document tells us that A owns B?" needs a deliberate answer.

Direct relationships carry `assertion_ids`, `confidence` and the temporal
fields, and are what the canvas queries. Underneath, `(:Assertion)` nodes link
subject, object and source, recording confidence, extraction method, model and
timestamps. The direct edge is *derived*: accepting an assertion upserts it,
retracting the last supporting assertion removes it. That synchronisation lives
in one module so the two layers cannot drift apart.

This supports two documents that disagree, and per-source confidence, without
making every canvas query walk the assertion layer.

Source nodes are excluded from expansion queries unless explicitly requested,
so documents do not clutter the canvas.

## 6. Relationships are time-aware in two dimensions

`valid_from` / `valid_to` record when something was true in the world.
`observed_at` records when we learned it. Directorships end and ownership
changes, so "who owned this in 2021" is a routine question; and separating what
we know from when we knew it preserves the audit trail. Both are cheap now and
a painful migration later.

## 7. Nothing reaches Neo4j without passing through staging

Transforms never write to the graph. They return a **proposal set** — nodes and
edges with provenance — stored in SQLite. The canvas renders proposals
alongside committed data but visually distinct. Accepting writes the set to
Neo4j in one transaction; rejecting discards it, leaving no trace in the graph.

"Auto-commit" is therefore not a second code path but a policy flag meaning
*propose and immediately accept*. The flag resolves most-specific-first:

1. per invocation (the context menu offers preview or keep),
2. per transform (declared in its manifest, overridable in config),
3. global default (`DEFAULT_WRITE_POLICY`, defaults to `review`).

Rejecting writes a tombstone so that re-running a transform does not re-propose
what was already turned down.

*Superseded:* an earlier decision let deterministic API connectors commit
directly while only LLM output was reviewed. Making staging universal is both
safer and simpler — one write path instead of two.

## 8. Transforms are a plugin registry

Every right-click action — expand from the database, look up a company
registry, later the LLM-driven searches — implements one interface: declared
input entity type, declared output types, runs as an async job, returns
ontology-conformant nodes and edges plus provenance. New capabilities are new
files, not UI changes.

## 9. The canvas is Cytoscape.js and plain ES modules

The canvas has to be JavaScript. Cytoscape.js provides the layouts, multi-select
and — importantly — running a layout over a *subset* of elements, which is a
core requirement. No build step, no framework, no npm: the browser loads ES
modules directly, and the styling comes from the generated ontology module.

Everything else — ontology, graph access, transforms, ingestion, LLM calls — is
Python.

## 10. Single-user locally, designed for multi-user

The application runs locally with no authentication for now, but `user_id`,
`case_id` and an audit trail are present in the schema and API from the start,
so the multi-user deployment is a change of deployment rather than a rewrite.

## 11. Facts come from APIs; language models route and read

Language models are not asked to recall facts about companies or people. A
model may decide *which* registry to query, and may read and summarise
documents or retrieved search results, but the facts themselves come from an
API response or a document that is stored as a source and can be re-read.

Anthropic's API is the first provider, behind a narrow internal interface so
others can be added without touching call sites.


## 12. Ontology changes are classified, because data does not regenerate

Editing the ontology and running `sla-ontology generate` updates every derived
artefact. It does **not** update data already in Neo4j — renaming a property
leaves every stored node using the old key.

So `sla-ontology diff` sorts changes into two kinds:

- **Additive** — new types, new optional properties, added enum values, wider
  relationship endpoints. Safe to apply to a populated database.
- **Breaking** — removals, renames, type or cardinality changes, newly required
  properties, narrowed endpoints. Each carries a note describing the migration
  it implies, and the command exits non-zero.

A rename necessarily reads as a removal plus an addition, since the file gives
no way to express intent; the migration note says so explicitly.

Nodes record the ontology version they were written under, so a migration can
find the ones it still has to convert.


## 13. The assertion layer is invisible to the canvas

Assertions are wired to entities by `SUBJECT`, `OBJECT` and `DERIVED_FROM`
relationships. Expansion must never traverse them: an `:Assertion` is not an
entity, has no ontology type, and would appear on the canvas as a meaningless
node.

So expansion names the relationship types it will follow — every type the
ontology declares, and nothing else — rather than following whatever edges
exist. Combined with requiring every node on the path to carry `:Thing`, the
provenance layer stays queryable but out of sight.

## 14. Expansion is capped, and says when it capped

A registered-agent address can have thousands of companies attached. Returning
them all would stall the browser and tell the analyst nothing, so `expand()`
takes a limit, defaulting to 500 edges, and sets `truncated` when it stops
short. `degree()` lets the interface warn before running the expansion at all.


## 15. Canvas libraries are vendored, not loaded from a CDN

Cytoscape.js, dagre and cytoscape-dagre (all MIT) live in `web/vendor/` with
their licences. Two reasons: the tool should work with no internet access, and
a CDN request tells a third party which investigation tool is open and when.
For software that handles due-diligence material that is a needless disclosure.

The cost is about 660 KB in the repository and manual updates. Both are
acceptable for a dependency set this small and this stable.

## 16. The canvas reads the ontology, and hard-codes no entity type

`web/js/style.js` builds the Cytoscape stylesheet from `GET /api/ontology`.
Adding an entity type to the YAML gives it a styled node, a legend entry and
context-menu actions on the next reload, with no change to any JavaScript.

The right-click menu offers a node only the relationships its own type can be
the source of, so a Person is never asked to "expand by issued tender". That
list comes from the ontology's inheritance resolution, not from a hand-written
mapping.

Sources are drawn as squares rather than circles, and system relationships
(`MENTIONS`, `SAME_AS`) as dotted or dashed lines, because neither is an
ordinary claim about the network: one is evidence, the other a proposal
awaiting review.

## 17. Layouts run over a selection within its own bounding box

Running a layout over selected nodes only is a stated requirement, and the
naive implementation gets it wrong: Cytoscape lays a collection out in the
viewport, which flings the selection across the canvas and abandons everything
else. So a selection layout computes the box the selected nodes already
occupy and confines the result to it. Tidying five nodes leaves them where
they were, tidier.

## 18. The test suite refuses to wipe a populated database

The graph tests clear the database between cases. Pointed at a working graph
that would destroy an investigation, so the fixture counts the nodes first and
skips with an explanation rather than deleting anything. `SLA_TEST_NEO4J_URI`
points the tests at a scratch instance; `SLA_ALLOW_DESTRUCTIVE_TESTS=1` says
the configured database is disposable.

This was found the hard way: a test run silently deleted the development seed
data during M3.
