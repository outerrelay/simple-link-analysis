"""Records exchanged with the graph store, and conversion to and from Neo4j.

These are storage-shaped: an entity is a type name plus a bag of ontology
properties plus the system fields. The generated Pydantic models in
:mod:`sla.ontology.generated.models` remain the typed view for API boundaries;
these records are what the repository reads and writes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

import neo4j.time

# Label carried by every entity, which is why one uniqueness constraint suffices.
ROOT_LABEL = "Thing"


def new_id() -> str:
    """Node and relationship identity is always a generated UUID."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class AssertionStatus(str, Enum):
    """Where a claim stands.

    Only ``ACCEPTED`` assertions support a direct edge. Proposals live in the
    application store until accepted, so they never reach Neo4j at all; this
    status covers what happens to a claim afterwards.
    """

    ACCEPTED = "accepted"
    RETRACTED = "retracted"


class ExtractionMethod(str, Enum):
    """How a claim came to be, recorded so it can be weighed later."""

    MANUAL = "manual"
    CONNECTOR = "connector"
    LANGUAGE_MODEL = "language_model"
    INFERENCE = "inference"


@dataclass
class EntityRecord:
    """An entity as stored."""

    type: str
    properties: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_id)
    observed_at: datetime | None = None
    suppressed: bool = False
    """Tombstoned: hidden from queries, and not recreated by re-ingestion."""

    created_at: datetime | None = None
    updated_at: datetime | None = None
    ontology_version: str | None = None

    @property
    def name(self) -> str:
        value = self.properties.get("name")
        return value if isinstance(value, str) else self.id


@dataclass
class RelationshipRecord:
    """A direct edge as stored, derived from one or more assertions."""

    type: str
    source_id: str
    target_id: str
    properties: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_id)
    confidence: float = 1.0
    valid_from: date | None = None
    valid_to: date | None = None
    observed_at: datetime | None = None
    assertion_ids: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class AssertionRecord:
    """A claim that a relationship holds, and where that claim came from.

    Direct edges are derived from these: an edge exists exactly while at least
    one accepted assertion supports it.
    """

    predicate: str
    subject_id: str
    object_id: str
    id: str = field(default_factory=new_id)
    confidence: float = 1.0
    method: ExtractionMethod = ExtractionMethod.MANUAL
    model: str | None = None
    """Model identifier, when the claim came from a language model."""

    source_id: str | None = None
    """The Source entity supporting the claim, if there is one."""

    status: AssertionStatus = AssertionStatus.ACCEPTED
    valid_from: date | None = None
    valid_to: date | None = None
    observed_at: datetime | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass
class Neighbourhood:
    """The result of expanding one or more nodes."""

    entities: list[EntityRecord] = field(default_factory=list)
    relationships: list[RelationshipRecord] = field(default_factory=list)
    truncated: bool = False
    """True when a degree cap stopped the expansion short."""


# --- conversion ---------------------------------------------------------

# Fields the system owns; everything else in a node belongs to the ontology.
SYSTEM_ENTITY_KEYS = frozenset(
    {"id", "type", "created_at", "updated_at", "observed_at", "suppressed", "ontology_version"}
)
SYSTEM_RELATIONSHIP_KEYS = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "observed_at",
        "confidence",
        "assertion_ids",
        "valid_from",
        "valid_to",
    }
)


def to_python(value: Any) -> Any:
    """Convert Neo4j temporal types back to their Python equivalents."""
    if isinstance(value, neo4j.time.DateTime | neo4j.time.Date):
        return value.to_native()
    if isinstance(value, list):
        return [to_python(item) for item in value]
    return value


def entity_from_node(node: Any) -> EntityRecord:
    """Build a record from a Neo4j node."""
    data = {key: to_python(value) for key, value in dict(node).items()}
    return EntityRecord(
        id=data["id"],
        type=data["type"],
        observed_at=data.get("observed_at"),
        suppressed=bool(data.get("suppressed", False)),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        ontology_version=data.get("ontology_version"),
        properties={k: v for k, v in data.items() if k not in SYSTEM_ENTITY_KEYS},
    )


def relationship_from_edge(edge: Any, source_id: str, target_id: str) -> RelationshipRecord:
    """Build a record from a Neo4j relationship.

    Endpoint ids are passed in rather than read from the edge: the driver
    exposes internal node references, and this project keys everything by UUID.
    """
    data = {key: to_python(value) for key, value in dict(edge).items()}
    return RelationshipRecord(
        id=data["id"],
        type=edge.type,
        source_id=source_id,
        target_id=target_id,
        confidence=float(data.get("confidence", 1.0)),
        valid_from=data.get("valid_from"),
        valid_to=data.get("valid_to"),
        observed_at=data.get("observed_at"),
        assertion_ids=list(data.get("assertion_ids") or []),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        properties={k: v for k, v in data.items() if k not in SYSTEM_RELATIONSHIP_KEYS},
    )


def assertion_from_node(node: Any, subject_id: str, object_id: str) -> AssertionRecord:
    data = {key: to_python(value) for key, value in dict(node).items()}
    reserved = {
        "id",
        "predicate",
        "confidence",
        "method",
        "model",
        "status",
        "valid_from",
        "valid_to",
        "observed_at",
        "created_at",
    }
    return AssertionRecord(
        id=data["id"],
        predicate=data["predicate"],
        subject_id=subject_id,
        object_id=object_id,
        confidence=float(data.get("confidence", 1.0)),
        method=ExtractionMethod(data.get("method", "manual")),
        model=data.get("model"),
        status=AssertionStatus(data.get("status", "accepted")),
        valid_from=data.get("valid_from"),
        valid_to=data.get("valid_to"),
        observed_at=data.get("observed_at"),
        created_at=data.get("created_at"),
        properties={k: v for k, v in data.items() if k not in reserved},
    )
