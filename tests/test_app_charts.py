"""The chart store: canvas state, kept out of the knowledge graph."""

from __future__ import annotations

import pytest

from sla.app import charts as store
from sla.app import database
from sla.config import Settings


@pytest.fixture(autouse=True)
def app_db(tmp_path):
    """A throwaway SQLite database per test."""
    database.init(Settings(_env_file=None, app_database_url=f"sqlite:///{tmp_path}/app.sqlite"))
    yield
    database.dispose()


def test_create_and_list() -> None:
    created = store.create_chart("Müller matter", "Ownership tracing")

    charts = store.list_charts()

    assert [c.id for c in charts] == [created.id]
    assert charts[0].name == "Müller matter"
    assert charts[0].node_count == 0


def test_adding_entities_records_their_positions() -> None:
    chart = store.create_chart("Chart")

    added = store.add_to_chart(
        chart.id,
        [store.Placement("entity-a", 10, 20), store.Placement("entity-b", 30, 40)],
    )

    detail = store.get_chart(chart.id)
    assert added == 2
    assert {p.entity_id: (p.x, p.y) for p in detail.placements} == {
        "entity-a": (10, 20),
        "entity-b": (30, 40),
    }


def test_adding_the_same_entity_twice_moves_it_rather_than_duplicating() -> None:
    chart = store.create_chart("Chart")
    store.add_to_chart(chart.id, [store.Placement("entity-a", 10, 20)])

    added = store.add_to_chart(chart.id, [store.Placement("entity-a", 99, 99)])

    detail = store.get_chart(chart.id)
    assert added == 0
    assert len(detail.placements) == 1
    assert (detail.placements[0].x, detail.placements[0].y) == (99, 99)


def test_saving_positions_after_a_layout() -> None:
    chart = store.create_chart("Chart")
    store.add_to_chart(chart.id, [store.Placement("entity-a", 0, 0)])

    updated = store.save_positions(chart.id, [store.Placement("entity-a", 150, 250, pinned=True)])

    placement = store.get_chart(chart.id).placements[0]
    assert updated == 1
    assert (placement.x, placement.y, placement.pinned) == (150, 250, True)


def test_saving_a_position_for_something_not_on_the_chart_is_ignored() -> None:
    chart = store.create_chart("Chart")

    assert store.save_positions(chart.id, [store.Placement("stranger", 1, 1)]) == 0


def test_removing_from_a_chart_leaves_other_charts_alone() -> None:
    """The same entity can sit on several charts; removal is per chart."""
    first = store.create_chart("First")
    second = store.create_chart("Second")
    store.add_to_chart(first.id, [store.Placement("shared", 0, 0)])
    store.add_to_chart(second.id, [store.Placement("shared", 0, 0)])

    removed = store.remove_from_chart(first.id, ["shared"])

    assert removed == 1
    assert store.get_chart(first.id).placements == []
    assert len(store.get_chart(second.id).placements) == 1


def test_deleting_a_chart_removes_its_placements() -> None:
    chart = store.create_chart("Chart")
    store.add_to_chart(chart.id, [store.Placement("entity-a", 0, 0)])

    assert store.delete_chart(chart.id) is True
    assert store.get_chart(chart.id) is None


def test_operations_on_a_missing_chart_report_it() -> None:
    assert store.get_chart("no-such-chart") is None
    assert store.delete_chart("no-such-chart") is False
    assert store.rename_chart("no-such-chart", "x") is False
    with pytest.raises(KeyError):
        store.add_to_chart("no-such-chart", [])
