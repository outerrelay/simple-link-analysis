"""Charts: saved views over the knowledge graph.

Note what these endpoints do *not* do. Removing an entity from a chart writes
only to the application store; the entity stays in Neo4j and can be put back.
Deleting from the graph is a separate operation on a different router.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from sla.api.deps import get_ontology, get_repository
from sla.api.schemas import ChartContentOut, ChartOut, GraphOut, PlacementIn
from sla.app import charts as store
from sla.app.charts import Placement
from sla.graph.repository import GraphRepository
from sla.ontology import Ontology

router = APIRouter(prefix="/api/charts", tags=["charts"])


@router.get("", response_model=list[ChartOut])
def list_charts() -> list[ChartOut]:
    return [ChartOut(**vars(chart)) for chart in store.list_charts()]


@router.post("", response_model=ChartOut, status_code=201)
def create_chart(
    name: str = Body(embed=True), description: str = Body(default="", embed=True)
) -> ChartOut:
    return ChartOut(**vars(store.create_chart(name, description)))


@router.get("/{chart_id}", response_model=ChartContentOut)
async def get_chart(
    chart_id: str,
    repository: GraphRepository = Depends(get_repository),
    ontology: Ontology = Depends(get_ontology),
) -> ChartContentOut:
    """Load a chart and the current state of the entities on it.

    Entities deleted from the graph since the chart was saved are skipped
    rather than failing the load: the two stores have no foreign key between
    them, and a stale reference should not make a chart unopenable.
    """
    chart = store.get_chart(chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail=f"no chart {chart_id}")

    graph = GraphOut()
    if chart.placements:
        neighbourhood = await repository.expand(
            [p.entity_id for p in chart.placements], depth=1, include_sources=True
        )
        on_chart = {p.entity_id for p in chart.placements}
        neighbourhood.entities = [e for e in neighbourhood.entities if e.id in on_chart]
        neighbourhood.relationships = [
            r
            for r in neighbourhood.relationships
            if r.source_id in on_chart and r.target_id in on_chart
        ]
        graph = GraphOut.of(neighbourhood, ontology)

    live = {entity.id for entity in graph.entities}
    return ChartContentOut(
        id=chart.id,
        name=chart.name,
        description=chart.description,
        placements=[
            PlacementIn(entity_id=p.entity_id, x=p.x, y=p.y, pinned=p.pinned)
            for p in chart.placements
            if p.entity_id in live
        ],
        graph=graph,
    )


@router.patch("/{chart_id}", response_model=ChartOut)
def rename_chart(
    chart_id: str,
    name: str = Body(embed=True),
    description: str | None = Body(default=None, embed=True),
) -> ChartOut:
    if not store.rename_chart(chart_id, name, description):
        raise HTTPException(status_code=404, detail=f"no chart {chart_id}")
    chart = store.get_chart(chart_id)
    assert chart is not None
    return ChartOut(
        id=chart.id,
        name=chart.name,
        description=chart.description,
        node_count=len(chart.placements),
    )


@router.delete("/{chart_id}", status_code=204, response_model=None)
def delete_chart(chart_id: str) -> None:
    """Delete the chart. Everything it referenced stays in the graph."""
    if not store.delete_chart(chart_id):
        raise HTTPException(status_code=404, detail=f"no chart {chart_id}")


@router.post("/{chart_id}/nodes")
def add_nodes(chart_id: str, placements: list[PlacementIn]) -> dict[str, int]:
    try:
        added = store.add_to_chart(chart_id, [_placement(p) for p in placements])
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no chart {chart_id}") from None
    return {"added": added}


@router.put("/{chart_id}/positions")
def save_positions(chart_id: str, placements: list[PlacementIn]) -> dict[str, int]:
    """Persist positions after a drag or a layout run."""
    try:
        updated = store.save_positions(chart_id, [_placement(p) for p in placements])
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no chart {chart_id}") from None
    return {"updated": updated}


@router.post("/{chart_id}/nodes/remove")
def remove_nodes(chart_id: str, entity_ids: list[str] = Body(embed=True)) -> dict[str, int]:
    """Take entities off the canvas without touching the knowledge graph."""
    try:
        removed = store.remove_from_chart(chart_id, entity_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no chart {chart_id}") from None
    return {"removed": removed}


def _placement(placement: PlacementIn) -> Placement:
    return Placement(
        entity_id=placement.entity_id, x=placement.x, y=placement.y, pinned=placement.pinned
    )
