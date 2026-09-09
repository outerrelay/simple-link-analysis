"""Running actions, and writing what the analyst accepts.

Two responsibilities, deliberately in one place.

**Policy resolution.** Whether a result is staged for review or committed at
once resolves most-specific-first: the invocation, then the action's own
default, then the global setting. "Auto-commit" runs the identical path and
accepts immediately, so there is only ever one way into the graph.

**Accepting.** Turning accepted items into nodes and edges goes through the
ordinary repository, so every relationship still arrives with an assertion
recording the action that produced it, its confidence and its source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sla.actions.base import Action, ActionContext, ActionError, missing_requirements
from sla.app import staging
from sla.app.models import ItemKind, ProposalStatus
from sla.app.staging import Proposal, ProposedEntity, ProposedRelationship, StagedSet
from sla.config import Settings, WritePolicy
from sla.graph.identity import IdentityResolver
from sla.graph.model import EntityRecord, ExtractionMethod
from sla.graph.repository import GraphError, GraphRepository
from sla.ontology import Ontology


@dataclass(frozen=True)
class ActionRun:
    """What one run of an action produced."""

    staged: StagedSet
    accepted: AcceptResult | None = None
    """Set when the policy was auto-commit, so the result was written at once."""

    matches: list[dict] = field(default_factory=list)
    """Nodes the action wants to point at rather than add, from a duplicate
    check. Carried on the result rather than in module state, because actions
    run concurrently as background jobs."""


@dataclass(frozen=True)
class AcceptResult:
    entities_written: int
    relationships_written: int
    errors: list[str]
    duplicate_candidates: int = 0
    """Possible duplicates noticed among what was just written."""


def resolve_policy(
    action: Action, settings: Settings, override: WritePolicy | None = None
) -> WritePolicy:
    """Most specific wins: invocation, then action, then global default."""
    if override is not None:
        return override
    if action.default_policy is not None:
        return action.default_policy
    return settings.default_write_policy


async def run_action(
    action: Action,
    *,
    entity_id: str,
    repository: GraphRepository,
    ontology: Ontology,
    settings: Settings,
    options: dict[str, object] | None = None,
    chart_id: str | None = None,
    policy: WritePolicy | None = None,
) -> ActionRun:
    """Run an action and stage what it produced.

    Nothing reaches Neo4j here unless the policy is auto-commit, in which case
    the staged set is accepted immediately through the same path.
    """
    if missing := missing_requirements(action, settings):
        raise ActionError(f"{action.label} needs {', '.join(missing)} to be configured")

    entity = await repository.get_entity(entity_id)
    if entity is None:
        raise ActionError(f"no entity {entity_id!r}, or it is suppressed")

    permitted = {
        concrete
        for declared in action.input_types
        for concrete in ontology.concrete_subtypes(declared)
    }
    if entity.type not in permitted:
        raise ActionError(
            f"{action.label} does not apply to a {entity.type}; " f"it accepts {sorted(permitted)}"
        )

    proposal = await action.run(
        ActionContext(
            entity=entity,
            repository=repository,
            ontology=ontology,
            settings=settings,
            options=options or {},
        )
    )
    proposal.subject_entity_id = entity_id
    staged = staging.record(proposal, chart_id=chart_id)
    matches = list(getattr(proposal, "matches", []) or [])

    if resolve_policy(action, settings, policy) is WritePolicy.AUTO_COMMIT:
        accepted = await accept(
            staged.id,
            item_ids=[item.id for item in staged.items],
            rejected_ids=[],
            repository=repository,
            action_id=action.id,
            check_duplicates=getattr(action, "external", False),
        )
        return ActionRun(staged=staged, accepted=accepted, matches=matches)

    return ActionRun(staged=staged, matches=matches)


async def accept(
    set_id: str,
    *,
    item_ids: list[str],
    rejected_ids: list[str],
    repository: GraphRepository,
    action_id: str = "",
    check_duplicates: bool = False,
) -> AcceptResult:
    """Write the accepted items to Neo4j and tombstone the rejected ones.

    Entities are written before relationships, since an edge needs both its
    endpoints to exist. An item that cannot be written is reported rather than
    aborting the rest: eleven good officers should not be lost to one bad one.
    """
    staged = staging.get_set(set_id)
    if staged is None:
        raise ActionError(f"no proposal set {set_id!r}")

    chosen = set(item_ids)
    errors: list[str] = []
    entities_written = 0
    relationships_written = 0

    # Map provisional ids onto whatever the graph ends up holding, so a
    # relationship written afterwards points at the right node.
    resolved: dict[str, str] = {}
    written_ids: list[str] = []

    for item in staged.entities:
        payload = item.payload
        if item.id not in chosen:
            # Not accepted, but still note where an existing entity lives, so a
            # relationship to it can be written if that was accepted.
            if payload.get("existing_id"):
                resolved[payload["id"]] = payload["existing_id"]
            continue
        try:
            record = EntityRecord(
                id=payload.get("existing_id") or payload["id"],
                type=payload["type"],
                properties=payload.get("properties", {}),
            )
            written = await repository.upsert_entity(record)
            resolved[payload["id"]] = written.id
            written_ids.append(written.id)
            entities_written += 1
        except GraphError as exc:
            errors.append(f"{payload.get('type', 'entity')}: {exc}")

    for item in staged.relationships:
        if item.id not in chosen:
            continue
        payload = item.payload
        source_id = resolved.get(payload["source_id"], payload["source_id"])
        target_id = resolved.get(payload["target_id"], payload["target_id"])
        try:
            await repository.assert_relationship(
                predicate=payload["type"],
                subject_id=source_id,
                object_id=target_id,
                properties=payload.get("properties", {}),
                valid_from=_as_date(payload.get("valid_from")),
                valid_to=_as_date(payload.get("valid_to")),
                confidence=float(payload.get("confidence", 1.0)),
                method=ExtractionMethod.CONNECTOR,
                model=action_id or staged.action_id,
            )
            relationships_written += 1
        except GraphError as exc:
            errors.append(f"{payload.get('type', 'relationship')}: {exc}")

    staging.decide_items(set_id, accepted=list(chosen), rejected=rejected_ids)

    # Data from outside is where a company you already hold turns up under a
    # different name, so look for that now rather than leaving it to be found
    # by accident. Candidates only — nothing is merged.
    candidates = 0
    if check_duplicates and written_ids:
        resolver = IdentityResolver(
            repository._driver,  # noqa: SLF001 - same layer, one driver
            repository._ontology,  # noqa: SLF001
            repository._database,  # noqa: SLF001
        )
        candidates = len(await resolver.propose_candidates())

    return AcceptResult(entities_written, relationships_written, errors, candidates)


async def reject_all(set_id: str) -> StagedSet | None:
    """Turn down everything in a set, remembering each refusal."""
    staged = staging.get_set(set_id)
    if staged is None:
        return None
    return staging.decide_items(set_id, accepted=[], rejected=[item.id for item in staged.items])


def _as_date(value: object) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


__all__ = [
    "AcceptResult",
    "ActionRun",
    "ItemKind",
    "Proposal",
    "ProposalStatus",
    "ProposedEntity",
    "ProposedRelationship",
    "accept",
    "reject_all",
    "resolve_policy",
    "run_action",
]
