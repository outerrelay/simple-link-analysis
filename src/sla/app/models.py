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

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
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
