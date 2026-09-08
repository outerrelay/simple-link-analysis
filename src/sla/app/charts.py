"""Chart operations: the canvas layer over the knowledge graph.

Everything here writes only to the application store. Adding an entity to a
chart does not create it; removing one does not delete it. That is the whole
point of keeping the two apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from sla.app.database import session_scope
from sla.app.models import LOCAL_USER, Chart, ChartNode


@dataclass(frozen=True)
class Placement:
    """Where one entity sits on a chart."""

    entity_id: str
    x: float
    y: float
    pinned: bool = False


@dataclass(frozen=True)
class ChartSummary:
    id: str
    name: str
    description: str
    node_count: int
    updated_at: str


@dataclass(frozen=True)
class ChartDetail:
    id: str
    name: str
    description: str
    placements: list[Placement]


def create_chart(name: str, description: str = "", user_id: str = LOCAL_USER) -> ChartSummary:
    with session_scope() as session:
        chart = Chart(name=name, description=description, user_id=user_id)
        session.add(chart)
        session.flush()
        return _summarise(chart, 0)


def list_charts(user_id: str = LOCAL_USER) -> list[ChartSummary]:
    with session_scope() as session:
        charts = session.scalars(
            select(Chart).where(Chart.user_id == user_id).order_by(Chart.updated_at.desc())
        ).all()
        return [_summarise(chart, len(chart.nodes)) for chart in charts]


def get_chart(chart_id: str) -> ChartDetail | None:
    with session_scope() as session:
        chart = session.get(Chart, chart_id)
        if chart is None:
            return None
        return ChartDetail(
            id=chart.id,
            name=chart.name,
            description=chart.description,
            placements=[
                Placement(entity_id=n.entity_id, x=n.x, y=n.y, pinned=n.pinned) for n in chart.nodes
            ],
        )


def rename_chart(chart_id: str, name: str, description: str | None = None) -> bool:
    with session_scope() as session:
        chart = session.get(Chart, chart_id)
        if chart is None:
            return False
        chart.name = name
        if description is not None:
            chart.description = description
        return True


def delete_chart(chart_id: str) -> bool:
    """Delete the chart. The entities it referenced stay in the graph."""
    with session_scope() as session:
        chart = session.get(Chart, chart_id)
        if chart is None:
            return False
        session.delete(chart)
        return True


def add_to_chart(chart_id: str, placements: list[Placement]) -> int:
    """Place entities on a chart, updating any already there.

    Returns how many were newly added.
    """
    added = 0
    with session_scope() as session:
        chart = session.get(Chart, chart_id)
        if chart is None:
            raise KeyError(chart_id)
        existing = {node.entity_id: node for node in chart.nodes}
        for placement in placements:
            node = existing.get(placement.entity_id)
            if node is None:
                session.add(
                    ChartNode(
                        chart_id=chart_id,
                        entity_id=placement.entity_id,
                        x=placement.x,
                        y=placement.y,
                        pinned=placement.pinned,
                    )
                )
                added += 1
            else:
                node.x, node.y, node.pinned = placement.x, placement.y, placement.pinned
        chart.updated_at = chart.updated_at  # touch, so onupdate fires
    return added


def save_positions(chart_id: str, placements: list[Placement]) -> int:
    """Persist positions after a drag or a layout run."""
    updated = 0
    with session_scope() as session:
        chart = session.get(Chart, chart_id)
        if chart is None:
            raise KeyError(chart_id)
        by_entity = {node.entity_id: node for node in chart.nodes}
        for placement in placements:
            node = by_entity.get(placement.entity_id)
            if node is not None:
                node.x, node.y, node.pinned = placement.x, placement.y, placement.pinned
                updated += 1
    return updated


def remove_from_chart(chart_id: str, entity_ids: list[str]) -> int:
    """Take entities off the canvas. The graph is not touched."""
    with session_scope() as session:
        chart = session.get(Chart, chart_id)
        if chart is None:
            raise KeyError(chart_id)
        removed = 0
        for node in list(chart.nodes):
            if node.entity_id in entity_ids:
                session.delete(node)
                removed += 1
        return removed


def _summarise(chart: Chart, node_count: int) -> ChartSummary:
    return ChartSummary(
        id=chart.id,
        name=chart.name,
        description=chart.description,
        node_count=node_count,
        updated_at=chart.updated_at.isoformat() if chart.updated_at else "",
    )
