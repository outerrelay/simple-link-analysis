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

import re
from dataclasses import dataclass

from neo4j import AsyncDriver

from sla.graph.model import new_id, utcnow
from sla.ontology import Ontology


@dataclass(frozen=True)
class Match:
    """A node that may be the same thing as the one asked about."""

    entity_id: str
    name: str
    type: str
    reason: str
    """Why it matched, so the analyst can judge it rather than trust a score."""

    strength: str
    """``strong`` for a shared issued identifier, ``weak`` for a name match."""


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

    async def matches_for(self, entity_id: str, *, limit: int = 20) -> list[Match]:
        """Answer "is this already in the database?" for one node.

        Ordered strongest first. A shared issued identifier is near-conclusive;
        an exact registration number within one jurisdiction nearly so; a name
        match is a prompt to look, nothing more. The reason is returned with
        every match because a bare similarity score tells an analyst nothing
        they can check.
        """
        matches: dict[str, Match] = {}

        for finder in (
            self._matches_by_identifier,
            self._matches_by_strong_property,
            self._matches_by_name,
        ):
            for match in await finder(entity_id):
                matches.setdefault(match.entity_id, match)
            if len(matches) >= limit:
                break

        order = {"strong": 0, "weak": 1}
        return sorted(matches.values(), key=lambda m: (order[m.strength], m.name))[:limit]

    async def _matches_by_identifier(self, entity_id: str) -> list[Match]:
        async with self._session() as session:
            result = await session.run(
                """
                MATCH (n:Thing {id: $id})-[:HAS_IDENTIFIER]->(i:Identifier)
                MATCH (i)<-[:HAS_IDENTIFIER]-(other:Thing)
                WHERE other.id <> $id AND coalesce(other.suppressed, false) = false
                RETURN other.id AS id, other.name AS name, other.type AS type,
                       i.scheme AS scheme, i.value AS value
                """,  # type: ignore[arg-type]
                id=entity_id,
            )
            return [
                Match(
                    entity_id=record["id"],
                    name=record["name"] or record["id"],
                    type=record["type"],
                    reason=f"shares identifier {record['scheme']}:{record['value']}",
                    strength="strong",
                )
                async for record in result
            ]

    async def _matches_by_strong_property(self, entity_id: str) -> list[Match]:
        """Same declared identifier property, and same type."""
        entity = await self._entity(entity_id)
        if entity is None:
            return []
        resolved = self._ontology.entity_type(entity["type"])
        matches: list[Match] = []

        for property_name in resolved.spec.identifiers:
            value = entity.get(property_name)
            if value in (None, "", []):
                continue
            async with self._session() as session:
                result = await session.run(
                    f"""
                    MATCH (other:{entity["type"]})
                    WHERE other.id <> $id
                      AND other.`{property_name}` = $value
                      AND coalesce(other.suppressed, false) = false
                    RETURN other.id AS id, other.name AS name, other.type AS type
                    """,  # type: ignore[arg-type]
                    id=entity_id,
                    value=value,
                )
                async for record in result:
                    matches.append(
                        Match(
                            entity_id=record["id"],
                            name=record["name"] or record["id"],
                            type=record["type"],
                            reason=f"same {property_name}: {value}",
                            strength="strong",
                        )
                    )
        return matches

    async def _matches_by_name(self, entity_id: str) -> list[Match]:
        """Same type and a similar name — a prompt to look, not a conclusion.

        Comparison is on a loosely normalised name: case folded, punctuation
        dropped, and common legal-form suffixes removed, so that "Acme AS" and
        "ACME A/S" meet. Deliberately crude; it proposes, a human disposes.
        """
        entity = await self._entity(entity_id)
        if entity is None or not entity.get("name"):
            return []

        normalised = _normalise_name(entity["name"])
        if not normalised:
            return []

        async with self._session() as session:
            result = await session.run(
                f"""
                MATCH (other:{entity["type"]})
                WHERE other.id <> $id
                  AND other.name IS NOT NULL
                  AND coalesce(other.suppressed, false) = false
                RETURN other.id AS id, other.name AS name, other.type AS type
                LIMIT 500
                """,  # type: ignore[arg-type]
                id=entity_id,
            )
            rows = [dict(record) async for record in result]

        return [
            Match(
                entity_id=row["id"],
                name=row["name"],
                type=row["type"],
                reason=f"similar name to {entity['name']!r}",
                strength="weak",
            )
            for row in rows
            if _normalise_name(row["name"]) == normalised
        ]

    async def _entity(self, entity_id: str) -> dict | None:
        async with self._session() as session:
            result = await session.run(
                "MATCH (n:Thing {id: $id}) RETURN properties(n) AS p",  # type: ignore[arg-type]
                id=entity_id,
            )
            record = await result.single()
        return dict(record["p"]) if record else None

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
                MATCH ()-[r:SAME_AS {id: $id}]->()
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


# Legal-form suffixes stripped before comparing names. Not exhaustive, and not
# meant to be: this decides what to *show* an analyst, never what to merge.
_LEGAL_FORMS = (
    "as",
    "asa",
    "ab",
    "a/s",
    "aps",
    "gmbh",
    "mbh",
    "ag",
    "kg",
    "ohg",
    "ug",
    "ltd",
    "limited",
    "plc",
    "llp",
    "lp",
    "llc",
    "inc",
    "corp",
    "co",
    "bv",
    "nv",
    "sa",
    "sarl",
    "sas",
    "srl",
    "spa",
    "oy",
    "ab publ",
)


def _normalise_name(name: str) -> str:
    """Fold a name to something two spellings of it can agree on.

    Punctuation becomes whitespace, so "A/S" arrives as two tokens; the suffix
    check therefore tries the last token and the last two joined, which is how
    "Acme A/S" and "Acme AS" come to agree.
    """
    text = re.sub(r"[^\w\s]", " ", (name or "").lower())
    words = [word for word in text.split() if word]

    changed = True
    while changed and words:
        changed = False
        if len(words) >= 2 and "".join(words[-2:]) in _LEGAL_FORMS:
            del words[-2:]
            changed = True
        elif words[-1] in _LEGAL_FORMS:
            words.pop()
            changed = True
    return " ".join(words)
