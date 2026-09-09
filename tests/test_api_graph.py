"""The HTTP surface the canvas talks to, against a live Neo4j."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from neo4j import AsyncGraphDatabase

from sla.app import database
from sla.config import Settings, get_settings
from sla.graph import schema
from sla.graph.model import EntityRecord
from sla.graph.repository import GraphRepository
from sla.main import app
from sla.ontology import load


async def _seed(settings: Settings) -> dict[str, EntityRecord]:
    """Populate a small graph using a driver that lives and dies here.

    The app makes its own driver inside TestClient's event loop; sharing one
    across two loops is what the Neo4j async driver refuses to do.
    """
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        notifications_disabled_categories=["UNRECOGNIZED"],
    )
    try:
        await driver.verify_connectivity()
        await schema.apply(driver, settings.neo4j_database)
        await schema.drop_all_data(driver, settings.neo4j_database)

        repository = GraphRepository(driver, load(), settings.neo4j_database)

        async def entity(type_name, name, **properties):
            return await repository.upsert_entity(
                EntityRecord(type=type_name, properties={"name": name, **properties})
            )

        person = await entity("Person", "Markus Wiedenmann", last_name="Wiedenmann")
        company = await entity("Company", "Acme AS", jurisdiction="NO")
        report = await entity(
            "Document", "Filing.pdf", retrieved_at=datetime(2024, 1, 1, tzinfo=UTC)
        )
        await repository.assert_relationship(
            predicate="OWNS",
            subject_id=person.id,
            object_id=company.id,
            properties={"percentage": 51.0},
            valid_from=date(2019, 1, 1),
        )
        await repository.assert_relationship(
            predicate="MENTIONS", subject_id=report.id, object_id=person.id
        )
        return {"person": person, "company": company, "report": report}
    finally:
        await driver.close()


async def _teardown(settings: Settings) -> None:
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        notifications_disabled_categories=["UNRECOGNIZED"],
    )
    try:
        await schema.drop_all_data(driver, settings.neo4j_database)
    finally:
        await driver.close()


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """A seeded graph plus an HTTP client, or skip when Neo4j is not running."""
    monkeypatch.setenv("APP_DATABASE_URL", f"sqlite:///{tmp_path}/app.sqlite")
    get_settings.cache_clear()
    settings = Settings(_env_file=None, app_database_url=f"sqlite:///{tmp_path}/app.sqlite")

    from tests.conftest import scratch_neo4j_settings

    settings = scratch_neo4j_settings().model_copy(
        update={"app_database_url": f"sqlite:///{tmp_path}/app.sqlite"}
    )
    monkeypatch.setenv("NEO4J_URI", settings.neo4j_uri)
    get_settings.cache_clear()

    try:
        entities = asyncio.run(_seed(settings))
    except Exception as exc:  # noqa: BLE001 - no database here means skip
        pytest.skip(f"no usable scratch Neo4j: {exc}")

    with TestClient(app) as client:
        yield client, entities

    database.dispose()
    asyncio.run(_teardown(settings))
    get_settings.cache_clear()


def test_search_finds_entities_by_name(seeded) -> None:
    client, _ = seeded

    response = client.get("/api/graph/search", params={"q": "wiedenmann"})
    assert response.status_code == 200, response.text
    results = response.json()

    assert [r["label"] for r in results] == ["Markus Wiedenmann"]
    assert results[0]["type"] == "Person"


def test_search_can_be_narrowed_by_type(seeded) -> None:
    client, _ = seeded

    results = client.get("/api/graph/search", params={"entity_type": "Company"}).json()

    assert [r["type"] for r in results] == ["Company"]


def test_expand_returns_neighbours_without_sources(seeded) -> None:
    client, ids = seeded

    body = client.get("/api/graph/expand", params={"entity_ids": [ids["person"].id]}).json()

    types = {e["type"] for e in body["entities"]}
    assert types == {"Person", "Company"}, "the Document is a source and stays hidden"
    assert body["relationships"][0]["type"] == "OWNS"


def test_expand_can_include_sources(seeded) -> None:
    client, ids = seeded

    body = client.get(
        "/api/graph/expand",
        params={"entity_ids": [ids["person"].id], "include_sources": True},
    ).json()

    assert "Document" in {e["type"] for e in body["entities"]}


def test_expand_as_of_a_date(seeded) -> None:
    client, ids = seeded

    before = client.get(
        "/api/graph/expand",
        params={"entity_ids": [ids["person"].id], "as_of": "2018-01-01"},
    ).json()

    assert before["relationships"] == [], "the ownership had not begun in 2018"


def test_expand_carries_what_the_canvas_needs_to_draw_an_edge(seeded) -> None:
    client, ids = seeded

    body = client.get("/api/graph/expand", params={"entity_ids": [ids["person"].id]}).json()
    edge = body["relationships"][0]

    assert edge["directed"] is True
    assert edge["valid_from"] == "2019-01-01"
    assert edge["properties"]["percentage"] == 51.0
    assert edge["assertion_count"] == 1


def test_expand_requires_at_least_one_entity(seeded) -> None:
    client, _ = seeded

    assert client.get("/api/graph/expand").status_code == 400


def test_unknown_entity_is_a_404(seeded) -> None:
    client, _ = seeded

    assert client.get("/api/graph/entity/no-such-id").status_code == 404


def test_labels_come_from_the_ontology_display_template(seeded) -> None:
    """Identifier renders as scheme:value, not as a bare name."""
    client, _ = seeded

    results = client.get("/api/graph/search", params={"q": "Acme"}).json()

    assert results[0]["label"] == "Acme AS"


# --- charts ---------------------------------------------------------------


def test_chart_round_trip(seeded) -> None:
    client, ids = seeded
    chart = client.post("/api/charts", json={"name": "Case 1"}).json()

    client.post(
        f"/api/charts/{chart['id']}/nodes",
        json=[
            {"entity_id": ids["person"].id, "x": 10, "y": 20},
            {"entity_id": ids["company"].id, "x": 90, "y": 80},
        ],
    )
    loaded = client.get(f"/api/charts/{chart['id']}").json()

    assert {p["entity_id"] for p in loaded["placements"]} == {
        ids["person"].id,
        ids["company"].id,
    }
    assert len(loaded["graph"]["relationships"]) == 1, "the OWNS edge between them"


def test_removing_from_a_chart_does_not_touch_the_graph(seeded) -> None:
    """The distinction the whole two-store split exists for."""
    client, ids = seeded
    chart = client.post("/api/charts", json={"name": "Case 1"}).json()
    client.post(f"/api/charts/{chart['id']}/nodes", json=[{"entity_id": ids["person"].id}])

    client.post(
        f"/api/charts/{chart['id']}/nodes/remove",
        json={"entity_ids": [ids["person"].id]},
    )

    assert client.get(f"/api/charts/{chart['id']}").json()["placements"] == []
    assert client.get(f"/api/graph/entity/{ids['person'].id}").status_code == 200


def test_chart_skips_entities_deleted_from_the_graph(seeded) -> None:
    """There is no foreign key across the two stores, so a stale reference
    must not make a chart unopenable."""
    client, ids = seeded
    chart = client.post("/api/charts", json={"name": "Case 1"}).json()
    client.post(
        f"/api/charts/{chart['id']}/nodes",
        json=[{"entity_id": ids["person"].id}, {"entity_id": "vanished-entity"}],
    )

    loaded = client.get(f"/api/charts/{chart['id']}")

    assert loaded.status_code == 200
    assert [p["entity_id"] for p in loaded.json()["placements"]] == [ids["person"].id]


def test_saving_positions(seeded) -> None:
    client, ids = seeded
    chart = client.post("/api/charts", json={"name": "Case 1"}).json()
    client.post(f"/api/charts/{chart['id']}/nodes", json=[{"entity_id": ids["person"].id}])

    client.put(
        f"/api/charts/{chart['id']}/positions",
        json=[{"entity_id": ids["person"].id, "x": 400, "y": 300}],
    )

    placement = client.get(f"/api/charts/{chart['id']}").json()["placements"][0]
    assert (placement["x"], placement["y"]) == (400, 300)


def test_missing_chart_is_a_404(seeded) -> None:
    client, _ = seeded

    assert client.get("/api/charts/no-such-chart").status_code == 404
