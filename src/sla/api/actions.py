"""Running actions and reviewing what they propose.

Actions call slow external services, so they run in the background and the
interface polls the job. Nothing they produce reaches Neo4j until it is
accepted here.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from sla.actions import registry, runner
from sla.actions.base import ActionError
from sla.api.deps import get_ontology, get_repository
from sla.app import staging
from sla.config import Settings, WritePolicy, get_settings
from sla.graph.repository import GraphRepository
from sla.ontology import Ontology

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/actions", tags=["actions"])


class ActionOut(BaseModel):
    id: str
    label: str
    description: str
    output_types: list[str]
    default_policy: str
    available: bool
    unavailable_reason: str = ""


class RunRequest(BaseModel):
    entity_id: str
    chart_id: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    policy: WritePolicy | None = Field(
        default=None,
        description="Override the action's own default for this invocation only.",
    )


class JobOut(BaseModel):
    id: str
    action_id: str
    status: str
    message: str = ""
    proposal_set_id: str | None = None


class ProposalItemOut(BaseModel):
    id: str
    kind: str
    decision: str
    label: str
    detail: str = ""
    payload: dict[str, Any]


class ProposalSetOut(BaseModel):
    id: str
    action_id: str
    summary: str
    status: str
    subject_entity_id: str | None = None
    created_at: str = ""
    items: list[ProposalItemOut] = Field(default_factory=list)


class DecisionRequest(BaseModel):
    accepted: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)


@router.get("/for/{entity_type}", response_model=list[ActionOut])
def actions_for(
    entity_type: str,
    ontology: Ontology = Depends(get_ontology),
    settings: Settings = Depends(get_settings),
) -> list[ActionOut]:
    """Which actions may be invoked on a node of this type."""
    try:
        descriptors = registry.applicable_to(entity_type, ontology, settings)
    except Exception as exc:  # noqa: BLE001 - an unknown type is a client error
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        ActionOut(
            id=d.id,
            label=d.label,
            description=d.description,
            output_types=list(d.output_types),
            default_policy=d.default_policy.value,
            available=d.available,
            unavailable_reason=d.unavailable_reason,
        )
        for d in descriptors
    ]


@router.post("/{action_id}/run", response_model=JobOut, status_code=202)
async def run(
    action_id: str,
    request: RunRequest,
    background: BackgroundTasks,
    repository: GraphRepository = Depends(get_repository),
    ontology: Ontology = Depends(get_ontology),
    settings: Settings = Depends(get_settings),
) -> JobOut:
    """Start an action. Returns immediately; poll the job for the result."""
    try:
        action = registry.get(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    job_id = staging.create_job(
        action_id, subject_entity_id=request.entity_id, chart_id=request.chart_id
    )

    async def execute() -> None:
        try:
            staged, accepted = await runner.run_action(
                action,
                entity_id=request.entity_id,
                repository=repository,
                ontology=ontology,
                settings=settings,
                options=request.options,
                chart_id=request.chart_id,
                policy=request.policy,
            )
            message = staged.summary
            if accepted is not None:
                message = (
                    f"{staged.summary} — committed "
                    f"({accepted.entities_written} entities, "
                    f"{accepted.relationships_written} relationships)"
                )
                if accepted.errors:
                    message += f"; {len(accepted.errors)} could not be written"
            staging.finish_job(
                job_id, status="succeeded", message=message, proposal_set_id=staged.id
            )
        except ActionError as exc:
            staging.finish_job(job_id, status="failed", message=str(exc))
        except Exception as exc:  # noqa: BLE001 - a job must always terminate
            logger.exception("action %s failed", action_id)
            staging.finish_job(job_id, status="failed", message=f"unexpected error: {exc}")

    background.add_task(execute)
    return JobOut(id=job_id, action_id=action_id, status="running")


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str) -> JobOut:
    job = staging.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    return JobOut(
        id=job.id,
        action_id=job.action_id,
        status=job.status,
        message=job.message,
        proposal_set_id=job.proposal_set_id,
    )


@router.get("/proposals", response_model=list[ProposalSetOut])
def list_proposals(
    ontology: Ontology = Depends(get_ontology),
) -> list[ProposalSetOut]:
    """Everything still awaiting a decision."""
    return [_to_out(s, ontology) for s in staging.pending_sets()]


@router.get("/proposals/{set_id}", response_model=ProposalSetOut)
def get_proposal(set_id: str, ontology: Ontology = Depends(get_ontology)) -> ProposalSetOut:
    staged = staging.get_set(set_id)
    if staged is None:
        raise HTTPException(status_code=404, detail=f"no proposal set {set_id}")
    return _to_out(staged, ontology)


@router.post("/proposals/{set_id}/decide", response_model=ProposalSetOut)
async def decide(
    set_id: str,
    request: DecisionRequest,
    repository: GraphRepository = Depends(get_repository),
    ontology: Ontology = Depends(get_ontology),
) -> ProposalSetOut:
    """Accept some items, reject others.

    Accepted items are written to Neo4j; rejected ones are tombstoned so the
    same suggestion is not made again.
    """
    try:
        result = await runner.accept(
            set_id,
            item_ids=request.accepted,
            rejected_ids=request.rejected,
            repository=repository,
        )
    except ActionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    staged = staging.get_set(set_id)
    assert staged is not None
    out = _to_out(staged, ontology)
    if result.errors:
        out.summary = f"{out.summary} — {len(result.errors)} could not be written"
    return out


@router.post("/proposals/{set_id}/reject", response_model=ProposalSetOut)
async def reject(set_id: str, ontology: Ontology = Depends(get_ontology)) -> ProposalSetOut:
    """Turn down everything in a set."""
    staged = await runner.reject_all(set_id)
    if staged is None:
        raise HTTPException(status_code=404, detail=f"no proposal set {set_id}")
    return _to_out(staged, ontology)


@router.get("/rejected")
def list_rejected() -> dict[str, list[str]]:
    """Fingerprints of everything previously turned down."""
    return {"fingerprints": sorted(staging.rejected_fingerprints())}


@router.delete("/rejected/{fingerprint}", status_code=204, response_model=None)
def forget_rejection(fingerprint: str) -> None:
    """Forget a refusal, so the suggestion can be made again."""
    if not staging.clear_rejection(fingerprint):
        raise HTTPException(status_code=404, detail="no such rejection")


@router.delete("/entity/{entity_id}", status_code=204, response_model=None)
async def delete_from_graph(
    entity_id: str,
    suppress: bool = Query(
        default=True,
        description="Tombstone rather than delete, so re-ingestion cannot resurrect it.",
    ),
    repository: GraphRepository = Depends(get_repository),
) -> None:
    """Remove an entity from the knowledge graph itself.

    Distinct from taking it off a chart. Suppression is the default because it
    is reversible and leaves the audit trail intact; passing ``suppress=false``
    deletes the node, its edges and the assertions about it outright.
    """
    done = (
        await repository.suppress_entity(entity_id)
        if suppress
        else await repository.delete_entity(entity_id)
    )
    if not done:
        raise HTTPException(status_code=404, detail=f"no entity {entity_id}")


def _to_out(staged, ontology: Ontology) -> ProposalSetOut:
    return ProposalSetOut(
        id=staged.id,
        action_id=staged.action_id,
        summary=staged.summary,
        status=staged.status,
        subject_entity_id=staged.subject_entity_id,
        created_at=staged.created_at,
        items=[
            ProposalItemOut(
                id=item.id,
                kind=item.kind,
                decision=item.decision,
                label=_item_label(item, ontology),
                detail=_item_detail(item),
                payload=item.payload,
            )
            for item in staged.items
        ],
    )


def _item_label(item, ontology: Ontology) -> str:
    """Name the item the way the ontology does, not the way the store does."""
    payload = item.payload or {}
    if item.kind == "entity":
        return str(payload.get("properties", {}).get("name") or payload.get("type", ""))

    relationship_type = str(payload.get("type", ""))
    spec = ontology.all_relationship_types.get(relationship_type)
    return spec.label if spec else relationship_type


def _item_detail(item) -> str:
    payload = item.payload or {}
    if item.kind == "entity":
        entity_type = str(payload.get("type", ""))
        # An action may propose something already stored — expanding a node
        # that has been expanded before, or a registry confirming what is
        # known. Saying so stops it reading as a new discovery.
        if payload.get("existing_id"):
            return f"{entity_type} · already in the database"
        return entity_type
    dates = [payload.get("valid_from"), payload.get("valid_to")]
    span = " – ".join(str(d) for d in dates if d)
    role = (payload.get("properties") or {}).get("role")
    return " · ".join(part for part in [role, span] if part)
