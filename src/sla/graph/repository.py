"""Reading and writing the knowledge graph.

Two rules run through everything here.

**Direct edges are derived.** A relationship exists in the graph exactly while
at least one accepted assertion supports it. :meth:`GraphRepository.assert_relationship`
creates the claim and upserts the edge; :meth:`retract_assertion` removes the
claim and drops the edge when it was the last one. Nothing else writes edges,
so the two layers cannot drift apart.

**Deleting has three meanings.** Removing a node from a chart is a matter for
the application store, not this module. Here, :meth:`suppress_entity` tombstones
a node so it disappears from queries and re-ingestion will not resurrect it,
while :meth:`delete_entity` removes it and its assertions outright.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

from neo4j import AsyncDriver

from sla.graph.model import (
    AssertionRecord,
    AssertionStatus,
    EntityRecord,
    ExtractionMethod,
    Neighbourhood,
    RelationshipRecord,
    assertion_from_node,
    entity_from_node,
    new_id,
    relationship_from_edge,
    utcnow,
)
from sla.ontology import Ontology

DEFAULT_EXPAND_LIMIT = 500
"""Edges returned per expansion before it reports itself truncated.

Expanding a node with thousands of edges would flood the canvas and stall the
browser, so the repository caps it and says that it did.
"""


class GraphError(Exception):
    """An operation was rejected because it does not fit the ontology."""


class GraphRepository:
    """Ontology-aware access to the Neo4j knowledge graph."""

    def __init__(self, driver: AsyncDriver, ontology: Ontology, database: str = "neo4j") -> None:
        self._driver = driver
        self._ontology = ontology
        self._database = database

    def _session(self):
        return self._driver.session(database=self._database)

    # --- entities -------------------------------------------------------

    async def upsert_entity(self, entity: EntityRecord) -> EntityRecord:
        """Create or update an entity, validating it against the ontology.

        A suppressed node stays suppressed: re-ingesting a document must not
        undo an analyst's decision to reject what it produced.
        """
        resolved = self._require_concrete(entity.type)
        self._validate_properties(entity.type, resolved.properties, entity.properties)

        labels = ":".join(resolved.labels)
        query = f"""
        MERGE (n:{labels} {{id: $id}})
        ON CREATE SET n.created_at = $now, n.suppressed = $suppressed
        SET n.type = $type,
            n.updated_at = $now,
            n.ontology_version = $ontology_version,
            n.observed_at = $observed_at,
            n += $properties
        RETURN n
        """
        async with self._session() as session:
            result = await session.run(
                query,  # type: ignore[arg-type]
                id=entity.id,
                type=entity.type,
                now=utcnow(),
                observed_at=entity.observed_at or utcnow(),
                ontology_version=self._ontology.version,
                suppressed=entity.suppressed,
                properties=entity.properties,
            )
            record = await result.single()
        assert record is not None
        return entity_from_node(record["n"])

    async def get_entity(
        self, entity_id: str, *, include_suppressed: bool = False
    ) -> EntityRecord | None:
        clause = "" if include_suppressed else "AND coalesce(n.suppressed, false) = false"
        async with self._session() as session:
            result = await session.run(
                f"MATCH (n:Thing) WHERE n.id = $id {clause} RETURN n",  # type: ignore[arg-type]
                id=entity_id,
            )
            record = await result.single()
        return entity_from_node(record["n"]) if record else None

    async def find_entities(
        self,
        *,
        entity_type: str | None = None,
        name_contains: str | None = None,
        limit: int = 50,
    ) -> list[EntityRecord]:
        """Search by type and/or name. Suppressed nodes are never returned."""
        label = entity_type or "Thing"
        if entity_type:
            self._ontology.entity_type(entity_type)

        clauses = ["coalesce(n.suppressed, false) = false"]
        if name_contains:
            clauses.append("toLower(n.name) CONTAINS toLower($name)")

        query = f"""
        MATCH (n:{label})
        WHERE {' AND '.join(clauses)}
        RETURN n ORDER BY n.name LIMIT $limit
        """
        async with self._session() as session:
            result = await session.run(query, name=name_contains, limit=limit)  # type: ignore[arg-type]
            return [entity_from_node(record["n"]) async for record in result]

    async def suppress_entity(self, entity_id: str, *, suppressed: bool = True) -> bool:
        """Tombstone a node without deleting it.

        The node and its history stay, but it is hidden from queries and
        ``upsert_entity`` will not un-hide it. This is what makes rejecting a
        proposal durable.
        """
        async with self._session() as session:
            result = await session.run(
                """
                MATCH (n:Thing {id: $id})
                SET n.suppressed = $suppressed, n.updated_at = $now
                RETURN n.id AS id
                """,  # type: ignore[arg-type]
                id=entity_id,
                suppressed=suppressed,
                now=utcnow(),
            )
            return await result.single() is not None

    async def delete_entity(self, entity_id: str) -> bool:
        """Delete a node outright, with its edges and the assertions about it.

        Assertions are deleted because a claim about a node that no longer
        exists cannot be evaluated. Sources are left alone: a document keeps
        its own existence.
        """
        async with self._session() as session:
            result = await session.run(
                """
                MATCH (n:Thing {id: $id})
                OPTIONAL MATCH (a:Assertion)-[:SUBJECT|OBJECT]->(n)
                DETACH DELETE a, n
                RETURN count(n) AS deleted
                """,  # type: ignore[arg-type]
                id=entity_id,
            )
            record = await result.single()
        return bool(record and record["deleted"])

    async def upsert_identifier(
        self, scheme: str, value: str, *, authority: str | None = None
    ) -> EntityRecord:
        """Get or create the canonical node for one issued identifier.

        Identifier nodes are keyed by ``(scheme, value)`` rather than by a fresh
        UUID, so two entities bearing the same LEI point at the *same* node.
        That is what makes duplicate detection a single hop; minting a new node
        per mention would make it invisible.
        """
        query = """
        MERGE (n:Identifier:Thing {scheme: $scheme, value: $value})
        ON CREATE SET n.id = $id, n.created_at = $now, n.suppressed = false,
                      n.type = 'Identifier', n.name = $name
        SET n.updated_at = $now,
            n.ontology_version = $ontology_version,
            n.observed_at = $now,
            n.authority = coalesce($authority, n.authority)
        RETURN n
        """
        async with self._session() as session:
            result = await session.run(
                query,  # type: ignore[arg-type]
                scheme=scheme,
                value=value,
                id=new_id(),
                name=f"{scheme}:{value}",
                authority=authority,
                ontology_version=self._ontology.version,
                now=utcnow(),
            )
            record = await result.single()
        assert record is not None
        return entity_from_node(record["n"])

    # --- assertions and the edges derived from them ----------------------

    async def assert_relationship(
        self,
        *,
        predicate: str,
        subject_id: str,
        object_id: str,
        source_id: str | None = None,
        confidence: float = 1.0,
        method: ExtractionMethod = ExtractionMethod.MANUAL,
        model: str | None = None,
        valid_from: date | None = None,
        valid_to: date | None = None,
        properties: dict[str, Any] | None = None,
    ) -> tuple[AssertionRecord, RelationshipRecord]:
        """Record a claim, and upsert the edge it supports.

        This is the only way an edge enters the graph, which is what keeps
        every relationship answerable for where it came from.
        """
        relationship = self._ontology.relationship_type(predicate)
        properties = properties or {}
        self._validate_properties(predicate, relationship.properties, properties)

        subject = await self.get_entity(subject_id)
        target = await self.get_entity(object_id)
        if subject is None:
            raise GraphError(f"subject {subject_id!r} does not exist or is suppressed")
        if target is None:
            raise GraphError(f"object {object_id!r} does not exist or is suppressed")

        self._check_endpoint(predicate, "source", relationship.source, subject.type)
        self._check_endpoint(predicate, "target", relationship.target, target.type)

        if not relationship.temporal and (valid_from or valid_to):
            raise GraphError(f"{predicate} is not temporal, so it takes no validity dates")

        assertion = AssertionRecord(
            id=new_id(),
            predicate=predicate,
            subject_id=subject_id,
            object_id=object_id,
            confidence=confidence,
            method=method,
            model=model,
            source_id=source_id,
            valid_from=valid_from,
            valid_to=valid_to,
            observed_at=utcnow(),
            properties=properties,
        )

        # One statement so the claim and the edge it supports cannot diverge.
        query = f"""
        MATCH (subject:Thing {{id: $subject_id}}), (object:Thing {{id: $object_id}})
        OPTIONAL MATCH (src:Source {{id: $source_id}})
        CREATE (a:Assertion {{
            id: $assertion_id, predicate: $predicate, confidence: $confidence,
            method: $method, model: $model, status: $status,
            valid_from: $valid_from, valid_to: $valid_to,
            observed_at: $now, created_at: $now
        }})
        SET a += $properties
        CREATE (a)-[:SUBJECT]->(subject)
        CREATE (a)-[:OBJECT]->(object)
        FOREACH (_ IN CASE WHEN src IS NULL THEN [] ELSE [1] END |
            CREATE (a)-[:DERIVED_FROM]->(src))
        MERGE (subject)-[r:{predicate}]->(object)
        ON CREATE SET r.id = $relationship_id, r.created_at = $now, r.assertion_ids = []
        SET r.updated_at = $now,
            r.observed_at = $now,
            r.confidence = $confidence,
            r.valid_from = $valid_from,
            r.valid_to = $valid_to,
            r.assertion_ids = r.assertion_ids + $assertion_id,
            r += $properties
        RETURN r, subject.id AS source_id, object.id AS target_id
        """
        async with self._session() as session:
            result = await session.run(
                query,  # type: ignore[arg-type]
                subject_id=subject_id,
                object_id=object_id,
                assertion_id=assertion.id,
                relationship_id=new_id(),
                predicate=predicate,
                confidence=confidence,
                method=method.value,
                model=model,
                status=AssertionStatus.ACCEPTED.value,
                valid_from=valid_from,
                valid_to=valid_to,
                source_id=source_id,
                now=utcnow(),
                properties=properties,
            )
            record = await result.single()
        assert record is not None
        edge = relationship_from_edge(record["r"], record["source_id"], record["target_id"])
        return assertion, edge

    async def retract_assertion(self, assertion_id: str) -> bool:
        """Withdraw a claim, removing its edge if nothing else supported it.

        The edge survives while another assertion still vouches for it, which
        is the point of keeping the two layers apart.
        """
        async with self._session() as session:
            result = await session.run(
                """
                MATCH (a:Assertion {id: $assertion_id})-[:SUBJECT]->(subject)
                MATCH (a)-[:OBJECT]->(object)
                MATCH (subject)-[r]->(object)
                WHERE $assertion_id IN r.assertion_ids AND type(r) = a.predicate
                SET r.assertion_ids = [x IN r.assertion_ids WHERE x <> $assertion_id]
                DETACH DELETE a
                WITH r
                FOREACH (_ IN CASE WHEN size(r.assertion_ids) = 0 THEN [1] ELSE [] END |
                    DELETE r)
                RETURN count(*) AS affected
                """,  # type: ignore[arg-type]
                assertion_id=assertion_id,
            )
            record = await result.single()
        return bool(record and record["affected"])

    async def assertions_for(
        self, source_id: str, predicate: str, target_id: str
    ) -> list[AssertionRecord]:
        """Every claim supporting one relationship — the "how do you know?" query."""
        async with self._session() as session:
            result = await session.run(
                """
                MATCH (a:Assertion {predicate: $predicate})-[:SUBJECT]->(s:Thing {id: $source_id})
                MATCH (a)-[:OBJECT]->(t:Thing {id: $target_id})
                RETURN a, s.id AS subject_id, t.id AS object_id
                ORDER BY a.created_at
                """,  # type: ignore[arg-type]
                predicate=predicate,
                source_id=source_id,
                target_id=target_id,
            )
            return [
                assertion_from_node(record["a"], record["subject_id"], record["object_id"])
                async for record in result
            ]

    async def sources_for_assertion(self, assertion_id: str) -> list[EntityRecord]:
        async with self._session() as session:
            result = await session.run(
                """
                MATCH (:Assertion {id: $assertion_id})-[:DERIVED_FROM]->(s:Source)
                RETURN s
                """,  # type: ignore[arg-type]
                assertion_id=assertion_id,
            )
            return [entity_from_node(record["s"]) async for record in result]

    # --- traversal -------------------------------------------------------

    async def expand(
        self,
        entity_ids: Sequence[str],
        *,
        depth: int = 1,
        relationship_types: Iterable[str] | None = None,
        include_sources: bool = False,
        as_of: date | None = None,
        limit: int = DEFAULT_EXPAND_LIMIT,
    ) -> Neighbourhood:
        """Return the neighbourhood around one or more nodes.

        Sources are excluded unless asked for, so documents do not crowd the
        canvas; ``as_of`` restricts the result to relationships that held on a
        given date.
        """
        if depth < 1:
            raise GraphError("depth must be at least 1")

        types = list(relationship_types or [])
        for name in types:
            self._ontology.relationship_type(name)
        if not types:
            # Without an explicit filter, traverse every ontology relationship
            # and nothing else. Naming them keeps expansion out of the
            # assertion layer, which the canvas must never see.
            types = sorted(self._ontology.all_relationship_types)
        type_filter = ":" + "|".join(types)

        conditions = [
            # Belt and braces: assertion nodes are not entities and have no type.
            "all(n IN nodes(path) WHERE n:Thing)",
            "all(n IN nodes(path) WHERE coalesce(n.suppressed, false) = false)",
        ]
        if not include_sources:
            # The seed nodes are exempt: expanding a document you selected
            # should still work.
            conditions.append(
                "all(n IN nodes(path)[1..] WHERE NOT n:Source OR n.id IN $entity_ids)"
            )
        if as_of is not None:
            conditions.append(
                "all(r IN relationships(path) WHERE "
                "(r.valid_from IS NULL OR r.valid_from <= $as_of) AND "
                "(r.valid_to IS NULL OR r.valid_to >= $as_of))"
            )

        query = f"""
        MATCH (seed:Thing) WHERE seed.id IN $entity_ids
        MATCH path = (seed)-[{type_filter}*1..{int(depth)}]-(other:Thing)
        WHERE {' AND '.join(conditions)}
        UNWIND relationships(path) AS r
        WITH DISTINCT r, startNode(r) AS s, endNode(r) AS t
        RETURN r, s, t LIMIT $limit
        """
        async with self._session() as session:
            result = await session.run(
                query,  # type: ignore[arg-type]
                entity_ids=list(entity_ids),
                as_of=as_of,
                limit=limit + 1,  # one extra, to detect truncation
            )
            rows = [record async for record in result]

        truncated = len(rows) > limit
        entities: dict[str, EntityRecord] = {}
        relationships: list[RelationshipRecord] = []
        for record in rows[:limit]:
            source = entity_from_node(record["s"])
            target = entity_from_node(record["t"])
            entities.setdefault(source.id, source)
            entities.setdefault(target.id, target)
            relationships.append(relationship_from_edge(record["r"], source.id, target.id))

        # Seeds are always returned, so expanding an isolated node still
        # produces it rather than nothing.
        for entity_id in entity_ids:
            if entity_id not in entities and (seed := await self.get_entity(entity_id)):
                entities[seed.id] = seed

        return Neighbourhood(
            entities=list(entities.values()),
            relationships=relationships,
            truncated=truncated,
        )

    async def degree(self, entity_id: str) -> int:
        """How many edges a node has — used to warn before a huge expansion."""
        async with self._session() as session:
            result = await session.run(
                """
                MATCH (n:Thing {id: $id})-[r]-(other:Thing)
                WHERE type(r) IN $types
                RETURN count(r) AS degree
                """,  # type: ignore[arg-type]
                id=entity_id,
                types=sorted(self._ontology.all_relationship_types),
            )
            record = await result.single()
        return int(record["degree"]) if record else 0

    # --- validation ------------------------------------------------------

    def _require_concrete(self, type_name: str):
        resolved = self._ontology.entity_type(type_name)
        if resolved.abstract:
            raise GraphError(f"{type_name} is abstract and cannot be instantiated")
        return resolved

    def _check_endpoint(
        self, predicate: str, role: str, declared: list[str], actual_type: str
    ) -> None:
        permitted = {
            concrete for name in declared for concrete in self._ontology.concrete_subtypes(name)
        }
        if actual_type not in permitted:
            raise GraphError(
                f"{predicate} does not accept {actual_type} as its {role}; "
                f"permitted: {sorted(permitted)}"
            )

    def _validate_properties(
        self, owner: str, declared: dict[str, Any], supplied: dict[str, Any]
    ) -> None:
        """Reject properties the ontology does not declare, and missing required ones.

        Neo4j is schemaless, so without this a typo silently becomes a new
        property on the node and the mistake is only found much later.
        """
        if unknown := sorted(set(supplied) - set(declared)):
            noun = "property" if len(unknown) == 1 else "properties"
            raise GraphError(f"{owner} has no {noun} {unknown}")

        missing = sorted(
            name
            for name, spec in declared.items()
            if spec.required and supplied.get(name) in (None, [], "")
        )
        if missing:
            raise GraphError(f"{owner} requires {missing}")
