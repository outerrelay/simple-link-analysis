"""Proposals: everything an action produces, before it reaches the graph.

Actions never write to Neo4j. They record a proposal set here; the canvas draws
it as provisional; accepting it writes to the graph through the ordinary
repository, so the assertion layer and its provenance still apply.

"Auto-commit" is therefore not a second write path but a policy that accepts a
set the moment it is proposed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select

from sla.app.database import session_scope
from sla.app.models import (
    LOCAL_USER,
    ItemKind,
    Job,
    ProposalItem,
    ProposalSet,
    ProposalStatus,
    RejectedProposal,
)


@dataclass
class ProposedEntity:
    """An entity an action would like to add.

    ``existing_id`` is set when the action matched something already in the
    graph — the same company by registration number, say. Accepting then
    updates that entity rather than creating a duplicate beside it.
    """

    type: str
    properties: dict[str, Any]
    id: str
    """Provisional UUID, minted when proposed so accepting is idempotent."""

    existing_id: str | None = None

    @property
    def target_id(self) -> str:
        return self.existing_id or self.id

    @property
    def label(self) -> str:
        name = self.properties.get("name")
        return name if isinstance(name, str) else self.type


@dataclass
class ProposedRelationship:
    """A relationship an action would like to assert."""

    type: str
    source_id: str
    target_id: str
    properties: dict[str, Any] = field(default_factory=dict)
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: float = 1.0


@dataclass
class Proposal:
    """What one run of one action produced."""

    action_id: str
    entities: list[ProposedEntity] = field(default_factory=list)
    relationships: list[ProposedRelationship] = field(default_factory=list)
    summary: str = ""
    subject_entity_id: str | None = None
    source: ProposedEntity | None = None
    """The record of the call itself, kept so the claims can be re-checked."""

    matches: list[dict[str, Any]] = field(default_factory=list)
    """Nodes the action wants to point at rather than add.

    A duplicate check answers "look at these", not "add these", so its result
    reaches the interface through the job rather than as staged writes.
    """

    def __bool__(self) -> bool:
        return bool(self.entities or self.relationships)


# --- fingerprints ---------------------------------------------------------


def entity_fingerprint(entity: ProposedEntity) -> str:
    """Identify a proposed entity by what it *claims to be*, not by its UUID.

    A provisional UUID differs on every run, so a rejection recorded against it
    would never match again. Hashing the type and the identifying properties
    means the same claim from the same source produces the same fingerprint.
    """
    strong = {
        key: value
        for key, value in sorted(entity.properties.items())
        if key in _STRONG_KEYS and value not in (None, "", [])
    }
    # A strong identifier settles identity on its own. Mixing the name in
    # would mean a company renamed between runs came back as a fresh
    # suggestion after it had already been turned down.
    identifying = strong or {"name": entity.properties.get("name")}
    return _hash(["entity", entity.type, identifying])


def relationship_fingerprint(
    relationship: ProposedRelationship,
    entities_by_id: dict[str, ProposedEntity] | None = None,
) -> str:
    """Identify a proposed relationship by its endpoints and type.

    Endpoints are resolved to entity fingerprints where they refer to something
    also being proposed, so the identity survives new provisional UUIDs.
    """
    entities_by_id = entities_by_id or {}

    def endpoint(entity_id: str) -> str:
        proposed = entities_by_id.get(entity_id)
        return entity_fingerprint(proposed) if proposed else entity_id

    return _hash(
        [
            "relationship",
            relationship.type,
            endpoint(relationship.source_id),
            endpoint(relationship.target_id),
            relationship.valid_from,
        ]
    )


# Properties strong enough to settle identity on their own. The name is
# deliberately absent: it is the fallback used only when none of these is
# present, never a component alongside them.
_STRONG_KEYS = frozenset(
    {
        "registration_number",
        "content_hash",
        "iban",
        "imo_number",
        "cadastral_reference",
        "reference",
        "case_number",
        "url",
        "value",
    }
)


def json_safe(value: Any) -> Any:
    """Make a value storable in a JSON column.

    Entities read from Neo4j carry real ``date`` and ``datetime`` objects,
    which JSON cannot hold. They are written as ISO strings and coerced back
    to typed values by the repository when the proposal is accepted.
    """
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    return value


def _hash(parts: list[Any]) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# --- recording proposals ---------------------------------------------------


@dataclass(frozen=True)
class StagedItem:
    id: str
    kind: str
    payload: dict[str, Any]
    fingerprint: str
    decision: str


@dataclass(frozen=True)
class StagedSet:
    id: str
    action_id: str
    subject_entity_id: str | None
    summary: str
    status: str
    created_at: str
    items: list[StagedItem]

    @property
    def entities(self) -> list[StagedItem]:
        return [i for i in self.items if i.kind == ItemKind.ENTITY.value]

    @property
    def relationships(self) -> list[StagedItem]:
        return [i for i in self.items if i.kind == ItemKind.RELATIONSHIP.value]


def record(
    proposal: Proposal, *, chart_id: str | None = None, user_id: str = LOCAL_USER
) -> StagedSet:
    """Store a proposal, dropping anything previously rejected.

    Filtering here rather than at accept time means the analyst is never shown
    a suggestion they have already refused.
    """
    entities_by_id = {e.id: e for e in proposal.entities}
    tombstoned = rejected_fingerprints(user_id)

    rows: list[tuple[str, dict[str, Any], str]] = []
    dropped_entity_ids: set[str] = set()

    for entity in proposal.entities:
        fingerprint = entity_fingerprint(entity)
        if fingerprint in tombstoned:
            dropped_entity_ids.add(entity.id)
            continue
        rows.append((ItemKind.ENTITY.value, json_safe(asdict(entity)), fingerprint))

    for relationship in proposal.relationships:
        # An edge to something that was refused has nothing to attach to.
        if (
            relationship.source_id in dropped_entity_ids
            or relationship.target_id in dropped_entity_ids
        ):
            continue
        fingerprint = relationship_fingerprint(relationship, entities_by_id)
        if fingerprint in tombstoned:
            continue
        rows.append((ItemKind.RELATIONSHIP.value, json_safe(asdict(relationship)), fingerprint))

    with session_scope() as session:
        proposal_set = ProposalSet(
            action_id=proposal.action_id,
            subject_entity_id=proposal.subject_entity_id,
            chart_id=chart_id,
            summary=proposal.summary,
            user_id=user_id,
        )
        session.add(proposal_set)
        session.flush()
        for position, (kind, payload, fingerprint) in enumerate(rows):
            session.add(
                ProposalItem(
                    proposal_set_id=proposal_set.id,
                    kind=kind,
                    payload=payload,
                    fingerprint=fingerprint,
                    position=position,
                )
            )
        session.flush()
        session.refresh(proposal_set)
        return _to_staged(proposal_set)


def get_set(set_id: str) -> StagedSet | None:
    with session_scope() as session:
        proposal_set = session.get(ProposalSet, set_id)
        return _to_staged(proposal_set) if proposal_set else None


def pending_sets(user_id: str = LOCAL_USER) -> list[StagedSet]:
    with session_scope() as session:
        sets = session.scalars(
            select(ProposalSet)
            .where(
                ProposalSet.user_id == user_id,
                ProposalSet.status == ProposalStatus.PENDING.value,
            )
            .order_by(ProposalSet.created_at.desc())
        ).all()
        return [_to_staged(s) for s in sets]


def decide_items(
    set_id: str, *, accepted: list[str], rejected: list[str], user_id: str = LOCAL_USER
) -> StagedSet | None:
    """Mark items accepted or rejected and tombstone the refusals.

    Does not touch Neo4j: writing accepted items is the caller's job, so that
    the graph write and its provenance stay in the repository.
    """
    accepted_set, rejected_set = set(accepted), set(rejected)
    with session_scope() as session:
        proposal_set = session.get(ProposalSet, set_id)
        if proposal_set is None:
            return None

        for item in proposal_set.items:
            if item.id in accepted_set:
                item.decision = ProposalStatus.ACCEPTED.value
            elif item.id in rejected_set:
                item.decision = ProposalStatus.REJECTED.value
                _tombstone(session, item, proposal_set.action_id, user_id)

        decisions = {item.decision for item in proposal_set.items}
        if ProposalStatus.PENDING.value in decisions:
            proposal_set.status = ProposalStatus.PENDING.value
        elif decisions == {ProposalStatus.ACCEPTED.value}:
            proposal_set.status = ProposalStatus.ACCEPTED.value
        elif decisions == {ProposalStatus.REJECTED.value}:
            proposal_set.status = ProposalStatus.REJECTED.value
        else:
            proposal_set.status = ProposalStatus.PARTIAL.value

        if proposal_set.status != ProposalStatus.PENDING.value:
            proposal_set.decided_at = datetime.now(tz=UTC)

        session.flush()
        session.refresh(proposal_set)
        return _to_staged(proposal_set)


def _tombstone(session, item: ProposalItem, action_id: str, user_id: str) -> None:
    """Remember a refusal, so re-running the action does not repeat it."""
    existing = session.scalars(
        select(RejectedProposal).where(
            RejectedProposal.fingerprint == item.fingerprint,
            RejectedProposal.user_id == user_id,
        )
    ).first()
    if existing is not None:
        return
    payload = item.payload or {}
    summary = payload.get("properties", {}).get("name") or payload.get("type", "")
    session.add(
        RejectedProposal(
            fingerprint=item.fingerprint,
            kind=item.kind,
            summary=str(summary),
            action_id=action_id,
            user_id=user_id,
        )
    )


def rejected_fingerprints(user_id: str = LOCAL_USER) -> set[str]:
    with session_scope() as session:
        return set(
            session.scalars(
                select(RejectedProposal.fingerprint).where(RejectedProposal.user_id == user_id)
            ).all()
        )


def clear_rejection(fingerprint: str, user_id: str = LOCAL_USER) -> bool:
    """Forget a refusal, so the suggestion can be made again."""
    with session_scope() as session:
        row = session.scalars(
            select(RejectedProposal).where(
                RejectedProposal.fingerprint == fingerprint,
                RejectedProposal.user_id == user_id,
            )
        ).first()
        if row is None:
            return False
        session.delete(row)
        return True


def _to_staged(proposal_set: ProposalSet) -> StagedSet:
    return StagedSet(
        id=proposal_set.id,
        action_id=proposal_set.action_id,
        subject_entity_id=proposal_set.subject_entity_id,
        summary=proposal_set.summary,
        status=proposal_set.status,
        created_at=proposal_set.created_at.isoformat() if proposal_set.created_at else "",
        items=[
            StagedItem(
                id=item.id,
                kind=item.kind,
                payload=item.payload,
                fingerprint=item.fingerprint,
                decision=item.decision,
            )
            for item in sorted(proposal_set.items, key=lambda i: i.position)
        ],
    )


# --- jobs ------------------------------------------------------------------


@dataclass(frozen=True)
class JobRecord:
    id: str
    action_id: str
    status: str
    message: str
    proposal_set_id: str | None
    subject_entity_id: str | None
    result: dict[str, Any] = field(default_factory=dict)


def create_job(
    action_id: str,
    *,
    subject_entity_id: str | None,
    chart_id: str | None,
    user_id: str = LOCAL_USER,
) -> str:
    with session_scope() as session:
        job = Job(
            action_id=action_id,
            subject_entity_id=subject_entity_id,
            chart_id=chart_id,
            user_id=user_id,
        )
        session.add(job)
        session.flush()
        return job.id


def finish_job(
    job_id: str,
    *,
    status: str,
    message: str = "",
    proposal_set_id: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = status
        job.message = message
        job.proposal_set_id = proposal_set_id
        job.result = json_safe(result or {})
        job.finished_at = datetime.now(tz=UTC)


def get_job(job_id: str) -> JobRecord | None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return None
        return JobRecord(
            id=job.id,
            action_id=job.action_id,
            status=job.status,
            message=job.message,
            proposal_set_id=job.proposal_set_id,
            subject_entity_id=job.subject_entity_id,
            result=job.result or {},
        )
