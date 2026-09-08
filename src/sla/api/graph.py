"""Reading the knowledge graph over HTTP."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from sla.api.deps import get_ontology, get_repository
from sla.api.schemas import EntityOut, GraphOut
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
