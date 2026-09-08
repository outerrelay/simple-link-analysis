"""Request and response shapes for the HTTP API.

Entities and relationships cross the wire in the form the canvas wants: a flat
node with its type, display label and properties, and an edge with its
endpoints and validity dates.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from sla.graph.model import EntityRecord, Neighbourhood, RelationshipRecord
from sla.ontology import Ontology


class EntityOut(BaseModel):
    id: str
    type: str
    label: str = Field(description="Rendered from the type's display template.")
    properties: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime | None = None

    @classmethod
    def of(cls, record: EntityRecord, ontology: Ontology) -> EntityOut:
        return cls(
            id=record.id,
            type=record.type,
            label=display_label(record, ontology),
            properties=record.properties,
            observed_at=record.observed_at,
        )


class RelationshipOut(BaseModel):
    id: str
    type: str
    source_id: str
    target_id: str
    directed: bool = True
    confidence: float = 1.0
    valid_from: date | None = None
    valid_to: date | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    assertion_count: int = 0

    @classmethod
    def of(cls, record: RelationshipRecord, ontology: Ontology) -> RelationshipOut:
        spec = ontology.all_relationship_types.get(record.type)
        return cls(
            id=record.id,
            type=record.type,
            source_id=record.source_id,
            target_id=record.target_id,
            directed=spec.directed if spec else True,
            confidence=record.confidence,
            valid_from=record.valid_from,
            valid_to=record.valid_to,
            properties=record.properties,
            assertion_count=len(record.assertion_ids),
        )


class GraphOut(BaseModel):
    """A set of entities and the relationships among them."""

    entities: list[EntityOut] = Field(default_factory=list)
    relationships: list[RelationshipOut] = Field(default_factory=list)
    truncated: bool = False
    """True when a degree cap stopped the expansion short."""

    @classmethod
    def of(cls, neighbourhood: Neighbourhood, ontology: Ontology) -> GraphOut:
        return cls(
            entities=[EntityOut.of(e, ontology) for e in neighbourhood.entities],
            relationships=[RelationshipOut.of(r, ontology) for r in neighbourhood.relationships],
            truncated=neighbourhood.truncated,
        )


class PlacementIn(BaseModel):
    entity_id: str
    x: float = 0.0
    y: float = 0.0
    pinned: bool = False


class ChartOut(BaseModel):
    id: str
    name: str
    description: str = ""
    node_count: int = 0
    updated_at: str = ""


class ChartContentOut(BaseModel):
    """A chart plus the graph it currently shows."""

    id: str
    name: str
    description: str = ""
    placements: list[PlacementIn] = Field(default_factory=list)
    graph: GraphOut = Field(default_factory=GraphOut)


def display_label(record: EntityRecord, ontology: Ontology) -> str:
    """Render an entity's label from its type's display template.

    Falls back to the name, then the id, so a node is never unlabelled — an
    entity missing an optional property should still be legible on the canvas.
    """
    try:
        template = ontology.entity_type(record.type).spec.display_name
    except Exception:  # noqa: BLE001 - an unknown type still deserves a label
        return record.name

    values = {key: value for key, value in record.properties.items() if value not in (None, [])}
    try:
        rendered = template.format(**values).strip()
    except (KeyError, IndexError):
        rendered = ""
    return rendered or record.name or record.id
