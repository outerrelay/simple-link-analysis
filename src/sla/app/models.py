"""Tables for application state.

A **chart** is a saved view of the graph: which entities are on it, where they
sit, and what the analyst has pinned or annotated. Removing a node from a chart
touches only these tables; the knowledge graph is untouched.

``user_id`` is carried from the start even though the tool runs single-user, so
that going multi-user is a deployment change rather than a migration.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

LOCAL_USER = "local"
"""Stand-in owner while the tool runs without authentication."""


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(tz=UTC)


class Base(DeclarativeBase):
    pass


class Chart(Base):
    """A saved canvas over the knowledge graph."""

    __tablename__ = "charts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[str] = mapped_column(String(64), default=LOCAL_USER, index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    nodes: Mapped[list[ChartNode]] = relationship(
        back_populates="chart", cascade="all, delete-orphan", lazy="selectin"
    )


class ChartNode(Base):
    """One entity's placement on a chart.

    ``entity_id`` refers to a node in Neo4j. There is no foreign key across the
    two stores, so a chart may reference an entity that has since been deleted;
    loading a chart skips those rather than failing.
    """

    __tablename__ = "chart_nodes"
    __table_args__ = (
        UniqueConstraint("chart_id", "entity_id", name="uq_chart_entity"),
        Index("ix_chart_nodes_chart", "chart_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    chart_id: Mapped[str] = mapped_column(String(36), ForeignKey("charts.id", ondelete="CASCADE"))
    entity_id: Mapped[str] = mapped_column(String(36))
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    """Pinned nodes keep their position when a layout runs."""

    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    chart: Mapped[Chart] = relationship(back_populates="nodes")


# ---------------------------------------------------------------------
# Staging
#
# Actions never write to Neo4j. They record a proposal set here, which the
# canvas draws as provisional; only accepting it writes to the graph. That is
# what makes "auto-commit" a policy flag — propose and immediately accept —
# rather than a second code path.
# ---------------------------------------------------------------------


class ProposalStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIAL = "partial"
    """Some items accepted, some rejected."""


class ItemKind(str, Enum):
    ENTITY = "entity"
    RELATIONSHIP = "relationship"


class ProposalSet(Base):
    """What one run of one action produced, awaiting a decision."""

    __tablename__ = "proposal_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    action_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The entity the action was invoked on, where there was one."""

    chart_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default=ProposalStatus.PENDING.value)
    user_id: Mapped[str] = mapped_column(String(64), default=LOCAL_USER, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list[ProposalItem]] = relationship(
        back_populates="proposal_set", cascade="all, delete-orphan", lazy="selectin"
    )


class ProposalItem(Base):
    """One proposed entity or relationship.

    Items are decided individually: an action returning twelve officers of whom
    you want eight should not be all-or-nothing.
    """

    __tablename__ = "proposal_items"
    __table_args__ = (Index("ix_proposal_items_set", "proposal_set_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    proposal_set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("proposal_sets.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
    """The proposed entity or relationship, as it would be written."""

    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    """Stable identity of the *claim*, so a rejection can be remembered."""

    decision: Mapped[str] = mapped_column(String(16), default=ProposalStatus.PENDING.value)
    position: Mapped[int] = mapped_column(Integer, default=0)
    """Ordering, so the review list matches what the action returned."""

    proposal_set: Mapped[ProposalSet] = relationship(back_populates="items")


class RejectedProposal(Base):
    """A tombstone: something the analyst turned down.

    Re-running an action must not keep re-proposing what was already refused,
    so every rejection is remembered by fingerprint and filtered out of later
    proposals.
    """

    __tablename__ = "rejected_proposals"
    __table_args__ = (UniqueConstraint("fingerprint", "user_id", name="uq_rejected_fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text, default="")
    """Human-readable note of what was refused, for the audit trail."""

    action_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), default=LOCAL_USER, index=True)
    rejected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    """A running or finished action.

    Actions call slow external services, so they run in the background and the
    interface polls. The record outlives the request that started it.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    action_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chart_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    proposal_set_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """Set once the action has produced something to review."""

    result: Mapped[dict] = mapped_column(JSON, default=dict)
    """Anything the action reports that is not a proposed graph write, such as
    the matches from a duplicate check."""

    user_id: Mapped[str] = mapped_column(String(64), default=LOCAL_USER, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MergeRecord(Base):
    """A merge that was carried out, and everything needed to undo it.

    Kept in the application store with the rest of the audit trail. The
    snapshot holds both nodes' properties and the absorbed node's edges as they
    stood before the merge.
    """

    __tablename__ = "merge_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    survivor_id: Mapped[str] = mapped_column(String(36), index=True)
    absorbed_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[dict] = mapped_column(JSON)
    resolutions: Mapped[dict] = mapped_column(JSON, default=dict)
    """Which value was kept for each conflicting property."""

    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    user_id: Mapped[str] = mapped_column(String(64), default=LOCAL_USER, index=True)
    merged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
