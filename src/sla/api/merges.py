"""Merging records that turn out to be the same thing.

Always at the analyst's instruction. The endpoints here preview a merge, carry
it out, and undo it; nothing merges on its own.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sla.api.deps import get_merge_service, get_ontology, get_resolver
from sla.app import merges as store
from sla.graph.identity import IdentityResolver
from sla.graph.merge import MergeError, MergeService
from sla.graph.model import new_id
from sla.ontology import Ontology

router = APIRouter(prefix="/api/merges", tags=["merges"])


class MatchOut(BaseModel):
    entity_id: str
    name: str
    type: str
    reason: str
    strength: str


class ConflictOut(BaseModel):
    name: str
    label: str
    survivor_value: Any = None
    absorbed_value: Any = None


class PreviewOut(BaseModel):
    survivor_id: str
    survivor_name: str
    absorbed_id: str
    absorbed_name: str
    entity_type: str
    conflicts: list[ConflictOut] = Field(default_factory=list)
    gained_properties: dict[str, Any] = Field(default_factory=dict)
    edges_to_move: int = 0
    edges_to_collapse: int = 0
    self_edges_to_drop: int = 0


class MergeRequest(BaseModel):
    survivor_id: str
    absorbed_id: str
    resolutions: dict[str, Any] = Field(
        default_factory=dict,
        description="Value to keep for each conflicting property. Anything not "
        "named keeps the survivor's value.",
    )


class MergeOut(BaseModel):
    merge_id: str
    survivor_id: str
    absorbed_id: str
    edges_moved: int
    edges_collapsed: int
    self_edges_dropped: int
    summary: str


class RecordedMergeOut(BaseModel):
    id: str
    survivor_id: str
    absorbed_id: str
    entity_type: str
    summary: str
    undone: bool
    merged_at: str


@router.get("/matches/{entity_id}", response_model=list[MatchOut])
async def matches(
    entity_id: str, resolver: IdentityResolver = Depends(get_resolver)
) -> list[MatchOut]:
    """Answer "is this already in the database?" for one node."""
    found = await resolver.matches_for(entity_id)
    return [
        MatchOut(
            entity_id=m.entity_id,
            name=m.name,
            type=m.type,
            reason=m.reason,
            strength=m.strength,
        )
        for m in found
    ]


@router.get("/preview", response_model=PreviewOut)
async def preview(
    survivor_id: str,
    absorbed_id: str,
    service: MergeService = Depends(get_merge_service),
    ontology: Ontology = Depends(get_ontology),
) -> PreviewOut:
    """Describe what a merge would do, without doing it."""
    try:
        result = await service.preview(survivor_id, absorbed_id)
    except MergeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    declared = ontology.entity_type(result.survivor.type).properties
    return PreviewOut(
        survivor_id=result.survivor.id,
        survivor_name=result.survivor.name,
        absorbed_id=result.absorbed.id,
        absorbed_name=result.absorbed.name,
        entity_type=result.survivor.type,
        conflicts=[
            ConflictOut(
                name=c.name,
                label=c.name.replace("_", " ").capitalize()
                if c.name not in declared
                else (declared[c.name].description or c.name).split(".")[0],
                survivor_value=c.survivor_value,
                absorbed_value=c.absorbed_value,
            )
            for c in result.conflicts
        ],
        gained_properties=result.gained_properties,
        edges_to_move=result.edges_to_move,
        edges_to_collapse=result.edges_to_collapse,
        self_edges_to_drop=result.self_edges_to_drop,
    )


@router.post("", response_model=MergeOut)
async def merge(
    request: MergeRequest, service: MergeService = Depends(get_merge_service)
) -> MergeOut:
    """Merge two records. Never called by anything but a person."""
    merge_id = new_id()
    try:
        preview_result = await service.preview(request.survivor_id, request.absorbed_id)
    except MergeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # The undo record is written *before* the graph is touched. The two stores
    # cannot share a transaction, so the ordering decides which way a failure
    # hurts: a record with no merge behind it is harmless and detectable, while
    # a merge with no record is one that cannot be undone.
    snapshot = await service.snapshot(request.survivor_id, request.absorbed_id)
    store.record(
        merge_id=merge_id,
        survivor_id=request.survivor_id,
        absorbed_id=request.absorbed_id,
        entity_type=preview_result.survivor.type,
        summary=f"{preview_result.absorbed.name} merged into {preview_result.survivor.name}",
        snapshot=snapshot,
        resolutions=request.resolutions,
    )

    try:
        result, _ = await service.merge(
            request.survivor_id,
            request.absorbed_id,
            resolutions=request.resolutions,
            merge_id=merge_id,
        )
    except MergeError as exc:
        store.discard(merge_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        store.discard(merge_id)
        raise

    summary = (
        f"{preview_result.absorbed.name} merged into {preview_result.survivor.name}: "
        f"{result.edges_moved} edges moved, {result.edges_collapsed} collapsed"
    )
    store.set_summary(merge_id, summary)
    return MergeOut(
        merge_id=merge_id,
        survivor_id=result.survivor_id,
        absorbed_id=result.absorbed_id,
        edges_moved=result.edges_moved,
        edges_collapsed=result.edges_collapsed,
        self_edges_dropped=result.self_edges_dropped,
        summary=summary,
    )


@router.get("", response_model=list[RecordedMergeOut])
def list_merges() -> list[RecordedMergeOut]:
    return [RecordedMergeOut(**vars(m)) for m in store.recent()]


@router.post("/{merge_id}/undo", response_model=RecordedMergeOut)
async def undo(
    merge_id: str, service: MergeService = Depends(get_merge_service)
) -> RecordedMergeOut:
    """Undo a merge, restoring both nodes and the absorbed node's edges.

    Anything added to the survivor since the merge stays with the survivor: the
    snapshot describes the merge, not everything that happened afterwards.
    """
    found = store.get(merge_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no merge {merge_id}")
    record, snapshot = found
    if record.undone:
        raise HTTPException(status_code=400, detail="that merge was already undone")

    await service.unmerge(snapshot)
    store.mark_undone(merge_id)
    return RecordedMergeOut(**{**vars(record), "undone": True})
