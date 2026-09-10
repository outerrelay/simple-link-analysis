"""Reading the knowledge graph over HTTP."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from sla.api.deps import get_ontology, get_repository, get_resolver
from sla.api.schemas import EntityOut, GraphOut, RelationshipOut
from sla.graph.identity import IdentityResolver
from sla.graph.model import EntityRecord, ExtractionMethod
from sla.graph.repository import GraphError, GraphRepository
from sla.ontology import Ontology

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/search", response_model=list[EntityOut])
async def search(
    q: str = Query(default="", description="Match against the entity name."),
    entity_type: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
    repository: GraphRepository = Depends(get_repository),
    ontology: Ontology = Depends(get_ontology),
) -> list[EntityOut]:
    """Find entities to put on the canvas."""
    try:
        records = await repository.find_entities(
            entity_type=entity_type, name_contains=q or None, limit=limit
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [EntityOut.of(record, ontology) for record in records]


@router.get("/entity/{entity_id}", response_model=EntityOut)
async def get_entity(
    entity_id: str,
    repository: GraphRepository = Depends(get_repository),
    ontology: Ontology = Depends(get_ontology),
) -> EntityOut:
    record = await repository.get_entity(entity_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no entity {entity_id}")
    return EntityOut.of(record, ontology)


@router.get("/expand", response_model=GraphOut)
async def expand(
    entity_ids: list[str] = Query(default_factory=list),
    depth: int = Query(default=1, ge=1, le=4),
    relationship_types: list[str] | None = Query(default=None),
    include_sources: bool = Query(
        default=False, description="Sources are hidden unless explicitly requested."
    ),
    as_of: date | None = Query(default=None, description="Show the graph as it stood."),
    limit: int = Query(default=500, ge=1, le=5000),
    repository: GraphRepository = Depends(get_repository),
    ontology: Ontology = Depends(get_ontology),
) -> GraphOut:
    """Return the neighbourhood around one or more entities."""
    if not entity_ids:
        raise HTTPException(status_code=400, detail="at least one entity_id is required")
    try:
        neighbourhood = await repository.expand(
            entity_ids,
            depth=depth,
            relationship_types=relationship_types,
            include_sources=include_sources,
            as_of=as_of,
            limit=limit,
        )
    except GraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return GraphOut.of(neighbourhood, ontology)


@router.get("/degree/{entity_id}")
async def degree(
    entity_id: str, repository: GraphRepository = Depends(get_repository)
) -> dict[str, int]:
    """How many edges a node has, so the interface can warn before expanding."""
    return {"degree": await repository.degree(entity_id)}


# --- creating things by hand ------------------------------------------------
#
# An analyst typing a company in is asserting it directly, so this writes
# through rather than staging a proposal for them to approve: nobody needs to
# be asked whether they meant what they just typed. The assertion still records
# that a person entered it by hand, which is what "how do you know that?" needs.


class NewEntity(BaseModel):
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class NewEntityOut(BaseModel):
    entity: EntityOut
    matches: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Existing records this may duplicate. Reported, never merged.",
    )


class NewRelationship(BaseModel):
    type: str
    source_id: str
    target_id: str
    properties: dict[str, Any] = Field(default_factory=dict)
    valid_from: date | None = None
    valid_to: date | None = None


@router.post("/entity", response_model=NewEntityOut, status_code=201)
async def create_entity(
    request: NewEntity,
    repository: GraphRepository = Depends(get_repository),
    ontology: Ontology = Depends(get_ontology),
    resolver: IdentityResolver = Depends(get_resolver),
) -> NewEntityOut:
    """Create an entity by hand.

    A canonical type — a phone number, an identifier — is upserted on its key
    rather than created outright, so typing in a number somebody else already
    has attaches to the same node instead of making a second one.
    """
    try:
        resolved = ontology.entity_type(request.type)
    except Exception as exc:  # noqa: BLE001 - an unknown type is a client error
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    properties = {
        key: value for key, value in request.properties.items() if value not in (None, "", [])
    }

    try:
        if resolved.spec.canonical_key:
            record = await repository.upsert_canonical(request.type, properties)
        else:
            record = await repository.upsert_entity(
                EntityRecord(type=request.type, properties=properties)
            )
    except GraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Typing in a company is exactly where a duplicate arrives, so say so —
    # without doing anything about it, which is the analyst's call.
    matches = await resolver.matches_for(record.id, limit=5)
    return NewEntityOut(
        entity=EntityOut.of(record, ontology),
        matches=[
            {
                "entity_id": m.entity_id,
                "name": m.name,
                "type": m.type,
                "reason": m.reason,
                "strength": m.strength,
            }
            for m in matches
        ],
    )


@router.post("/relationship", response_model=RelationshipOut, status_code=201)
async def create_relationship(
    request: NewRelationship,
    repository: GraphRepository = Depends(get_repository),
    ontology: Ontology = Depends(get_ontology),
) -> RelationshipOut:
    """Connect two entities by hand.

    Goes through the ordinary assertion path, so the edge arrives with a claim
    behind it recording that a person drew it.
    """
    try:
        _, edge = await repository.assert_relationship(
            predicate=request.type,
            subject_id=request.source_id,
            object_id=request.target_id,
            properties={
                key: value
                for key, value in request.properties.items()
                if value not in (None, "", [])
            },
            valid_from=request.valid_from,
            valid_to=request.valid_to,
            method=ExtractionMethod.MANUAL,
        )
    except GraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RelationshipOut.of(edge, ontology)
