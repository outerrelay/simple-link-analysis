"""Proposing that two nodes are the same thing.

Nothing here ever merges. Merging destroys provenance and cannot be undone, and
in due-diligence work a false merge is a serious error — so a match becomes a
``SAME_AS`` relationship with status ``candidate``, and a human decides.

Two signals produce candidates:

* two entities pointing at the same :class:`Identifier` node — an LEI, a
  company number, a passport number;
* two entities of the same type agreeing on a property the ontology declares as
  a strong identifier, such as a document's content hash or a vessel's IMO
  number.

A decision already recorded is never overwritten: an analyst's ``rejected`` is
not undone by re-running detection.
"""

from __future__ import annotations

from dataclasses import dataclass

from neo4j import AsyncDriver

from sla.graph.model import new_id, utcnow
from sla.ontology import Ontology


@dataclass(frozen=True)
class DuplicateCandidate:
    """A proposed identity between two entities, awaiting review."""

    left_id: str
    right_id: str
    basis: str
    """Why it was proposed, e.g. ``shared identifier lei:5493001KJTIIGC8Y1R12``."""


class IdentityResolver:
    """Finds duplicate candidates and records them for review."""

    def __init__(self, driver: AsyncDriver, ontology: Ontology, database: str = "neo4j") -> None:
        self._driver = driver
        self._ontology = ontology
        self._database = database

    def _session(self):
        return self._driver.session(database=self._database)

    async def find_candidates(self) -> list[DuplicateCandidate]:
        """Look for duplicates without recording anything."""
        return [*await self._by_shared_identifier_node(), *await self._by_identifier_property()]

    async def propose_candidates(self) -> list[DuplicateCandidate]:
        """Find duplicates and record the new ones as ``SAME_AS`` candidates.

        Returns only what was newly proposed, so re-running is quiet once the
        queue has been worked through.
        """
        proposed: list[DuplicateCandidate] = []
        for candidate in await self.find_candidates():
            if await self._record(candidate):
                proposed.append(candidate)
        return proposed

    async def _by_shared_identifier_node(self) -> list[DuplicateCandidate]:
        """Two entities bearing the same issued identifier.

        ``left.id < right.id`` keeps each pair from being reported twice.
        """
        async with self._session() as session:
            result = await session.run(
                """
                MATCH (left:Thing)-[:HAS_IDENTIFIER]->(i:Identifier)
                MATCH (i)<-[:HAS_IDENTIFIER]-(right:Thing)
                WHERE left.id < right.id
                  AND coalesce(left.suppressed, false) = false
                  AND coalesce(right.suppressed, false) = false
                RETURN left.id AS left_id, right.id AS right_id,
                       i.scheme AS scheme, i.value AS value
                """  # type: ignore[arg-type]
            )
            return [
                DuplicateCandidate(
                    left_id=record["left_id"],
                    right_id=record["right_id"],
                    basis=f"shared identifier {record['scheme']}:{record['value']}",
                )
                async for record in result
            ]

    async def _by_identifier_property(self) -> list[DuplicateCandidate]:
        """Two entities of one type agreeing on a declared strong identifier."""
        candidates: list[DuplicateCandidate] = []
        for type_name, resolved in sorted(self._ontology.concrete_entity_types.items()):
            for property_name in resolved.spec.identifiers:
                async with self._session() as session:
                    result = await session.run(
                        f"""
                        MATCH (left:{type_name}), (right:{type_name})
                        WHERE left.id < right.id
                          AND left.`{property_name}` IS NOT NULL
                          AND left.`{property_name}` = right.`{property_name}`
                          AND coalesce(left.suppressed, false) = false
                          AND coalesce(right.suppressed, false) = false
                        RETURN left.id AS left_id, right.id AS right_id,
                               left.`{property_name}` AS value
                        """  # type: ignore[arg-type]
                    )
                    async for record in result:
                        candidates.append(
                            DuplicateCandidate(
                                left_id=record["left_id"],
                                right_id=record["right_id"],
                                basis=(
                                    f"matching {type_name}.{property_name} "
                                    f"= {record['value']!r}"
                                ),
                            )
                        )
        return candidates

    async def _record(self, candidate: DuplicateCandidate) -> bool:
        """Record a candidate unless the pair already has a decision.

        Returns True when something new was written.
        """
        async with self._session() as session:
            result = await session.run(
                """
                MATCH (left:Thing {id: $left_id}), (right:Thing {id: $right_id})
                OPTIONAL MATCH (left)-[existing:SAME_AS]-(right)
                WITH left, right, existing WHERE existing IS NULL
                CREATE (left)-[r:SAME_AS {
                    id: $id, status: 'candidate', basis: $basis,
                    created_at: $now, updated_at: $now, assertion_ids: []
                }]->(right)
                RETURN r.id AS id
                """,  # type: ignore[arg-type]
                left_id=candidate.left_id,
                right_id=candidate.right_id,
                basis=candidate.basis,
                id=new_id(),
                now=utcnow(),
            )
            return await result.single() is not None

    async def pending_review(self) -> list[dict[str, str]]:
        """Candidates still awaiting a decision, for the review queue."""
        async with self._session() as session:
            result = await session.run(
                """
                MATCH (left:Thing)-[r:SAME_AS {status: 'candidate'}]->(right:Thing)
                RETURN r.id AS id, r.basis AS basis,
                       left.id AS left_id, left.name AS left_name, left.type AS left_type,
                       right.id AS right_id, right.name AS right_name, right.type AS right_type
                ORDER BY r.created_at
                """  # type: ignore[arg-type]
            )
            return [dict(record) async for record in result]

    async def decide(self, same_as_id: str, *, confirmed: bool, decided_by: str) -> bool:
        """Confirm or reject a candidate.

        Confirming does **not** merge the nodes. It records that they denote the
        same thing, which queries can follow; the nodes stay distinct so the
        decision remains reversible and each keeps its own provenance.
        """
        async with self._session() as session:
            result = await session.run(
                """
                MATCH ()-[r:SAME_AS {id: $id}]-()
                SET r.status = $status, r.decided_by = $decided_by,
                    r.decided_at = $now, r.updated_at = $now
                RETURN r.id AS id
                """,  # type: ignore[arg-type]
                id=same_as_id,
                status="confirmed" if confirmed else "rejected",
                decided_by=decided_by,
                now=utcnow(),
            )
            return await result.single() is not None
