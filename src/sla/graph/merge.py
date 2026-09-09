"""Merging two nodes that turn out to be the same thing.

Never automatic. An analyst decides that two records denote one company, and
this carries that decision out.

The absorbed node is **kept**, marked ``merged_into`` the survivor and hidden
from queries, and a record of everything moved is written so the merge can be
undone. Deleting it would be simpler and would make a mistaken merge
unrecoverable, which in due-diligence work is the wrong trade.

Note the distinction from ``SAME_AS`` confirmed. That is a claim about the
world — these two records denote one thing. This is an operation on the store —
combine them into one node. Confirming the first does not perform the second,
because keeping two source records distinct for audit is a legitimate choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from neo4j import AsyncDriver

from sla.graph.model import EntityRecord, entity_from_node, to_python, utcnow
from sla.ontology import Ontology


class MergeError(Exception):
    """The merge was refused, and the message says why."""


@dataclass
class PropertyConflict:
    """One property the two nodes disagree about."""

    name: str
    survivor_value: Any
    absorbed_value: Any


@dataclass
class MergePreview:
    """What a merge would do, shown before it is carried out."""

    survivor: EntityRecord
    absorbed: EntityRecord
    conflicts: list[PropertyConflict] = field(default_factory=list)
    gained_properties: dict[str, Any] = field(default_factory=dict)
    """Properties the survivor lacks and would gain outright."""

    edges_to_move: int = 0
    edges_to_collapse: int = 0
    """Edges of the same type to the same neighbour, which become one."""

    self_edges_to_drop: int = 0
    """Edges between the two nodes, which would become loops."""


@dataclass
class MergeResult:
    survivor_id: str
    absorbed_id: str
    merge_id: str
    edges_moved: int
    edges_collapsed: int
    self_edges_dropped: int
    properties_taken: dict[str, Any]
    conflicts_resolved: dict[str, Any]


# Relationships belonging to the provenance layer rather than the network.
ASSERTION_EDGES = ("SUBJECT", "OBJECT", "DERIVED_FROM")


class MergeService:
    """Previews and performs merges, and undoes them."""

    def __init__(self, driver: AsyncDriver, ontology: Ontology, database: str = "neo4j") -> None:
        self._driver = driver
        self._ontology = ontology
        self._database = database

    def _session(self):
        return self._driver.session(database=self._database)

    async def preview(self, survivor_id: str, absorbed_id: str) -> MergePreview:
        """Describe the merge without performing it."""
        survivor, absorbed = await self._load_pair(survivor_id, absorbed_id)

        conflicts: list[PropertyConflict] = []
        gained: dict[str, Any] = {}
        declared = self._ontology.entity_type(survivor.type).properties

        for name, value in absorbed.properties.items():
            if value in (None, "", []):
                continue
            current = survivor.properties.get(name)
            if current in (None, "", []):
                gained[name] = value
            elif declared.get(name) and declared[name].multi:
                continue  # multi-valued properties are unioned, never in conflict
            elif current != value:
                conflicts.append(PropertyConflict(name, current, value))

        async with self._session() as session:
            result = await session.run(
                """
                MATCH (a:Thing {id: $absorbed_id})-[r]-(other:Thing)
                WHERE NOT type(r) IN $assertion_edges
                RETURN type(r) AS type, other.id AS other_id,
                       startNode(r).id = $absorbed_id AS outgoing
                """,  # type: ignore[arg-type]
                absorbed_id=absorbed_id,
                assertion_edges=list(ASSERTION_EDGES),
            )
            edges = [dict(record) async for record in result]

            existing = await session.run(
                """
                MATCH (s:Thing {id: $survivor_id})-[r]-(other:Thing)
                WHERE NOT type(r) IN $assertion_edges
                RETURN type(r) AS type, other.id AS other_id,
                       startNode(r).id = $survivor_id AS outgoing
                """,  # type: ignore[arg-type]
                survivor_id=survivor_id,
                assertion_edges=list(ASSERTION_EDGES),
            )
            survivor_edges = {
                (record["type"], record["other_id"], record["outgoing"])
                async for record in existing
            }

        self_edges = sum(1 for e in edges if e["other_id"] == survivor_id)
        collapse = sum(
            1
            for e in edges
            if e["other_id"] != survivor_id
            and (e["type"], e["other_id"], e["outgoing"]) in survivor_edges
        )
        move = len(edges) - self_edges - collapse

        return MergePreview(
            survivor=survivor,
            absorbed=absorbed,
            conflicts=conflicts,
            gained_properties=gained,
            edges_to_move=move,
            edges_to_collapse=collapse,
            self_edges_to_drop=self_edges,
        )

    async def merge(
        self,
        survivor_id: str,
        absorbed_id: str,
        *,
        resolutions: dict[str, Any] | None = None,
        merge_id: str,
    ) -> tuple[MergeResult, dict[str, Any]]:
        """Carry out the merge.

        ``resolutions`` names the value to keep for each conflicting property.
        Anything not named keeps the survivor's value, which is the default
        behaviour when the analyst does not want to decide field by field.

        Returns the result and a snapshot sufficient to undo it.
        """
        preview = await self.preview(survivor_id, absorbed_id)
        resolutions = resolutions or {}
        unknown = set(resolutions) - {c.name for c in preview.conflicts}
        if unknown:
            raise MergeError(f"no conflict on {sorted(unknown)}; nothing to resolve")

        declared = self._ontology.entity_type(preview.survivor.type).properties

        # Properties: take what the survivor lacks, union the multi-valued
        # ones, and apply any explicit resolutions.
        updates: dict[str, Any] = dict(preview.gained_properties)
        for name, spec in declared.items():
            if not spec.multi:
                continue
            combined = list(
                dict.fromkeys(
                    [
                        *(preview.survivor.properties.get(name) or []),
                        *(preview.absorbed.properties.get(name) or []),
                    ]
                )
            )
            if combined and combined != (preview.survivor.properties.get(name) or []):
                updates[name] = combined
        updates.update(resolutions)

        # Whichever name loses is kept as an alias: it is how the entity appears
        # in some source, and searching for it should still find this record.
        # Which one loses depends on the resolution, so it is worked out rather
        # than assumed to be the absorbed record's.
        aliases = list(updates.get("aliases", preview.survivor.properties.get("aliases") or []))
        for conflict in preview.conflicts:
            if conflict.name != "name":
                continue
            kept = resolutions.get("name", conflict.survivor_value)
            losing = (
                conflict.absorbed_value
                if kept == conflict.survivor_value
                else conflict.survivor_value
            )
            if losing and losing not in aliases:
                aliases.append(losing)
        if aliases and "aliases" in declared:
            updates["aliases"] = aliases

        snapshot = await self.snapshot(survivor_id, absorbed_id)

        async with self._session() as session:
            moved = await session.run(
                """
                MATCH (a:Thing {id: $absorbed_id})-[r]-(other:Thing)
                WHERE NOT type(r) IN $assertion_edges
                RETURN elementId(r) AS element_id, type(r) AS type,
                       properties(r) AS properties, other.id AS other_id,
                       startNode(r).id = $absorbed_id AS outgoing
                """,  # type: ignore[arg-type]
                absorbed_id=absorbed_id,
                assertion_edges=list(ASSERTION_EDGES),
            )
            edges = [dict(record) async for record in moved]

            moved_count = collapsed = dropped = 0
            for edge in edges:
                if edge["other_id"] == survivor_id:
                    # Would become a self-loop; the relationship was between the
                    # two records, which turn out to be one thing.
                    await session.run(
                        "MATCH ()-[r]-() WHERE elementId(r) = $eid DELETE r",
                        eid=edge["element_id"],
                    )
                    dropped += 1
                    continue

                direction = ("(s)-[r2:%s]->(o)" if edge["outgoing"] else "(o)-[r2:%s]->(s)") % edge[
                    "type"
                ]
                result = await session.run(
                    f"""
                    MATCH (s:Thing {{id: $survivor_id}}), (o:Thing {{id: $other_id}})
                    OPTIONAL MATCH {direction}
                    RETURN elementId(r2) AS existing, r2.assertion_ids AS assertion_ids
                    """,  # type: ignore[arg-type]
                    survivor_id=survivor_id,
                    other_id=edge["other_id"],
                )
                record = await result.single()

                if record and record["existing"]:
                    # The survivor already has this edge: fold the assertions in
                    # rather than leaving two edges saying the same thing.
                    combined_assertions = list(
                        dict.fromkeys(
                            [
                                *(record["assertion_ids"] or []),
                                *(edge["properties"].get("assertion_ids") or []),
                            ]
                        )
                    )
                    await session.run(
                        """
                        MATCH ()-[r2]-() WHERE elementId(r2) = $eid
                        SET r2.assertion_ids = $assertion_ids, r2.updated_at = $now
                        """,  # type: ignore[arg-type]
                        eid=record["existing"],
                        assertion_ids=combined_assertions,
                        now=utcnow(),
                    )
                    await session.run(
                        "MATCH ()-[r]-() WHERE elementId(r) = $eid DELETE r",
                        eid=edge["element_id"],
                    )
                    collapsed += 1
                else:
                    create = (
                        f"CREATE (s)-[r2:{edge['type']}]->(o)"
                        if edge["outgoing"]
                        else f"CREATE (o)-[r2:{edge['type']}]->(s)"
                    )
                    await session.run(
                        f"""
                        MATCH (s:Thing {{id: $survivor_id}}), (o:Thing {{id: $other_id}})
                        {create}
                        SET r2 = $properties, r2.updated_at = $now
                        """,  # type: ignore[arg-type]
                        survivor_id=survivor_id,
                        other_id=edge["other_id"],
                        properties=edge["properties"],
                        now=utcnow(),
                    )
                    await session.run(
                        "MATCH ()-[r]-() WHERE elementId(r) = $eid DELETE r",
                        eid=edge["element_id"],
                    )
                    moved_count += 1

            # Assertions about the absorbed node now concern the survivor;
            # leaving them pointed at a hidden node would strand the provenance.
            for role in ("SUBJECT", "OBJECT"):
                await session.run(
                    f"""
                    MATCH (a:Assertion)-[r:{role}]->(absorbed:Thing {{id: $absorbed_id}})
                    MATCH (s:Thing {{id: $survivor_id}})
                    CREATE (a)-[:{role}]->(s)
                    DELETE r
                    """,  # type: ignore[arg-type]
                    absorbed_id=absorbed_id,
                    survivor_id=survivor_id,
                )

            if updates:
                await session.run(
                    """
                    MATCH (s:Thing {id: $survivor_id})
                    SET s += $updates, s.updated_at = $now
                    """,  # type: ignore[arg-type]
                    survivor_id=survivor_id,
                    updates=updates,
                    now=utcnow(),
                )

            await session.run(
                """
                MATCH (a:Thing {id: $absorbed_id})
                SET a.merged_into = $survivor_id,
                    a.merge_id = $merge_id,
                    a.suppressed = true,
                    a.updated_at = $now
                """,  # type: ignore[arg-type]
                absorbed_id=absorbed_id,
                survivor_id=survivor_id,
                merge_id=merge_id,
                now=utcnow(),
            )

            # A candidate between them has been answered by the merge itself.
            await session.run(
                """
                MATCH (x:Thing {id: $survivor_id})-[r:SAME_AS]-(y:Thing {id: $absorbed_id})
                SET r.status = 'confirmed', r.decided_at = $now,
                    r.basis = coalesce(r.basis, '') + ' (merged)'
                """,  # type: ignore[arg-type]
                survivor_id=survivor_id,
                absorbed_id=absorbed_id,
                now=utcnow(),
            )

        return (
            MergeResult(
                survivor_id=survivor_id,
                absorbed_id=absorbed_id,
                merge_id=merge_id,
                edges_moved=moved_count,
                edges_collapsed=collapsed,
                self_edges_dropped=dropped,
                properties_taken=updates,
                conflicts_resolved=resolutions,
            ),
            snapshot,
        )

    async def unmerge(self, snapshot: dict[str, Any]) -> None:
        """Undo a merge from its snapshot.

        Restores both nodes' properties and the absorbed node's edges. Anything
        added to the survivor since the merge stays with the survivor: the
        snapshot describes the merge, not everything that happened after it.
        """
        survivor_id = snapshot["survivor"]["id"]
        absorbed_id = snapshot["absorbed"]["id"]

        async with self._session() as session:
            await session.run(
                """
                MATCH (s:Thing {id: $id})
                SET s = $properties, s.updated_at = $now
                """,  # type: ignore[arg-type]
                id=survivor_id,
                properties={**snapshot["survivor"]["properties"], "id": survivor_id},
                now=utcnow(),
            )
            await session.run(
                """
                MATCH (a:Thing {id: $id})
                SET a = $properties, a.updated_at = $now
                REMOVE a.merged_into, a.merge_id
                SET a.suppressed = false
                """,  # type: ignore[arg-type]
                id=absorbed_id,
                properties={**snapshot["absorbed"]["properties"], "id": absorbed_id},
                now=utcnow(),
            )

            for edge in snapshot["absorbed"]["edges"]:
                create = (
                    f"CREATE (a)-[r:{edge['type']}]->(o)"
                    if edge["outgoing"]
                    else f"CREATE (o)-[r:{edge['type']}]->(a)"
                )
                await session.run(
                    f"""
                    MATCH (a:Thing {{id: $absorbed_id}}), (o:Thing {{id: $other_id}})
                    {create}
                    SET r = $properties
                    """,  # type: ignore[arg-type]
                    absorbed_id=absorbed_id,
                    other_id=edge["other_id"],
                    properties=edge["properties"],
                )

            # Edges that moved to the survivor go back; ones the survivor
            # already had stay, minus the assertions that came from the merge.
            for edge in snapshot["absorbed"]["edges"]:
                if edge["other_id"] == survivor_id:
                    continue
                direction = ("(s)-[r:%s]->(o)" if edge["outgoing"] else "(o)-[r:%s]->(s)") % edge[
                    "type"
                ]
                await session.run(
                    f"""
                    MATCH (s:Thing {{id: $survivor_id}}), (o:Thing {{id: $other_id}})
                    MATCH {direction}
                    WHERE r.id = $edge_id
                    DELETE r
                    """,  # type: ignore[arg-type]
                    survivor_id=survivor_id,
                    other_id=edge["other_id"],
                    edge_id=edge["properties"].get("id"),
                )

    # --- helpers ---------------------------------------------------------

    async def _load_pair(
        self, survivor_id: str, absorbed_id: str
    ) -> tuple[EntityRecord, EntityRecord]:
        if survivor_id == absorbed_id:
            raise MergeError("a node cannot be merged into itself")

        async with self._session() as session:
            result = await session.run(
                "MATCH (n:Thing) WHERE n.id IN $ids RETURN n",  # type: ignore[arg-type]
                ids=[survivor_id, absorbed_id],
            )
            found = {record["n"]["id"]: entity_from_node(record["n"]) async for record in result}

        for entity_id in (survivor_id, absorbed_id):
            if entity_id not in found:
                raise MergeError(f"no entity {entity_id!r}")

        survivor, absorbed = found[survivor_id], found[absorbed_id]
        if survivor.type != absorbed.type:
            raise MergeError(
                f"cannot merge a {absorbed.type} into a {survivor.type}; "
                f"merge only joins records of the same type"
            )
        for entity in (survivor, absorbed):
            if entity.properties.get("merged_into"):
                raise MergeError(f"{entity.id} has already been merged away")
        return survivor, absorbed

    async def snapshot(self, survivor_id: str, absorbed_id: str) -> dict[str, Any]:
        """Everything needed to undo the merge."""
        async with self._session() as session:
            nodes: dict[str, Any] = {}
            result = await session.run(
                "MATCH (n:Thing) WHERE n.id IN $ids RETURN n.id AS id, properties(n) AS p",
                ids=[survivor_id, absorbed_id],
            )
            async for record in result:
                nodes[record["id"]] = {key: to_python(value) for key, value in record["p"].items()}

            result = await session.run(
                """
                MATCH (a:Thing {id: $absorbed_id})-[r]-(other:Thing)
                WHERE NOT type(r) IN $assertion_edges
                RETURN type(r) AS type, properties(r) AS properties,
                       other.id AS other_id, startNode(r).id = $absorbed_id AS outgoing
                """,  # type: ignore[arg-type]
                absorbed_id=absorbed_id,
                assertion_edges=list(ASSERTION_EDGES),
            )
            edges = [
                {
                    "type": record["type"],
                    "properties": {
                        key: to_python(value) for key, value in record["properties"].items()
                    },
                    "other_id": record["other_id"],
                    "outgoing": record["outgoing"],
                }
                async for record in result
            ]

        return {
            "survivor": {"id": survivor_id, "properties": nodes.get(survivor_id, {})},
            "absorbed": {
                "id": absorbed_id,
                "properties": nodes.get(absorbed_id, {}),
                "edges": edges,
            },
        }
