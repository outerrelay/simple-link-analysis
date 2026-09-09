"""Expand a node from what the database already holds.

The simplest action, and the one that proves the interface: it reads Neo4j
rather than an outside service, so what it returns is already in the graph.
Nothing needs writing, and the result auto-commits — accepting a node you
already have is a decision without content.
"""

from __future__ import annotations

from sla.actions.base import ActionContext
from sla.actions.registry import register
from sla.app.staging import Proposal, ProposedEntity, ProposedRelationship
from sla.config import WritePolicy


class ExpandFromDatabase:
    """Pull in the neighbours already recorded for this entity."""

    id = "expand.database"
    label = "Expand from database"
    description = "Show entities already linked to this one in the graph."
    input_types = ("Thing",)
    output_types = ("Thing",)
    default_policy = WritePolicy.AUTO_COMMIT
    requires: tuple[str, ...] = ()

    async def run(self, context: ActionContext) -> Proposal:
        depth = int(context.options.get("depth", 1) or 1)
        include_sources = bool(context.options.get("include_sources", False))
        relationship_types = context.options.get("relationship_types") or None

        neighbourhood = await context.repository.expand(
            [context.entity.id],
            depth=depth,
            relationship_types=relationship_types,
            include_sources=include_sources,
        )

        # Everything here is already stored, so each entity carries the id it
        # already has and accepting is a no-op against the graph.
        entities = [
            ProposedEntity(
                type=record.type,
                properties=record.properties,
                id=record.id,
                existing_id=record.id,
            )
            for record in neighbourhood.entities
            if record.id != context.entity.id
        ]
        relationships = [
            ProposedRelationship(
                type=record.type,
                source_id=record.source_id,
                target_id=record.target_id,
                properties=record.properties,
                valid_from=record.valid_from.isoformat() if record.valid_from else None,
                valid_to=record.valid_to.isoformat() if record.valid_to else None,
                confidence=record.confidence,
            )
            for record in neighbourhood.relationships
        ]

        summary = f"{len(entities)} entities, {len(relationships)} relationships"
        if neighbourhood.truncated:
            summary += " (truncated)"

        return Proposal(
            action_id=self.id,
            entities=entities,
            relationships=relationships,
            summary=summary,
        )


register(ExpandFromDatabase())
