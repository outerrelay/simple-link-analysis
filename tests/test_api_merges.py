"""Duplicate checking and merging over HTTP."""

from __future__ import annotations

import asyncio

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
from tests.conftest import scratch_neo4j_settings


async def _seed(settings: Settings) -> dict[str, str]:
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

        async def company(name, **properties):
            return await repository.upsert_entity(
                EntityRecord(type="Company", properties={"name": name, **properties})
            )

        # Two records for one company, joined only by a shared LEI.
        first = await company("Nordic Infrastructure AS", jurisdiction="NO")
        second = await company("Nordic Infrastructure A/S")
        lei = await repository.upsert_identifier("lei", "5493001KJTIIGC8Y1R12")
        for company_id in (first.id, second.id):
            await repository.assert_relationship(
                predicate="HAS_IDENTIFIER", subject_id=company_id, object_id=lei.id
            )

        director = await repository.upsert_entity(
            EntityRecord(type="Person", properties={"name": "A. Director"})
        )
        await repository.assert_relationship(
            predicate="DIRECTOR_OF", subject_id=director.id, object_id=second.id
        )
        unrelated = await company("Totally Different Ltd")
        return {
            "first": first.id,
            "second": second.id,
            "director": director.id,
            "unrelated": unrelated.id,
        }
    finally:
        await driver.close()


async def _clear(settings: Settings) -> None:
    """Leave the database as we found it, so later tests are not skipped."""
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
def client(tmp_path, monkeypatch):
    settings = scratch_neo4j_settings()
    monkeypatch.setenv("NEO4J_URI", settings.neo4j_uri)
    monkeypatch.setenv("APP_DATABASE_URL", f"sqlite:///{tmp_path}/app.sqlite")
    get_settings.cache_clear()
    try:
        ids = asyncio.run(_seed(settings))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no usable scratch Neo4j: {exc}")

    with TestClient(app) as test_client:
        yield test_client, ids

    database.dispose()
    asyncio.run(_clear(settings))
    get_settings.cache_clear()


# --- "is this already in the database?" -------------------------------------


def test_a_shared_identifier_is_a_strong_match(client) -> None:
    test_client, ids = client

    matches = test_client.get(f"/api/merges/matches/{ids['first']}").json()

    assert [m["entity_id"] for m in matches] == [ids["second"]]
    assert matches[0]["strength"] == "strong"
    assert "5493001KJTIIGC8Y1R12" in matches[0]["reason"]


def test_every_match_says_why(client) -> None:
    """A bare similarity score tells an analyst nothing they can check."""
    test_client, ids = client

    matches = test_client.get(f"/api/merges/matches/{ids['first']}").json()

    assert all(m["reason"] for m in matches)


def test_an_unrelated_company_matches_nothing(client) -> None:
    test_client, ids = client

    assert test_client.get(f"/api/merges/matches/{ids['unrelated']}").json() == []


# --- preview ----------------------------------------------------------------


def test_preview_describes_the_merge_without_performing_it(client) -> None:
    test_client, ids = client

    preview = test_client.get(
        "/api/merges/preview",
        params={"survivor_id": ids["first"], "absorbed_id": ids["second"]},
    ).json()

    assert preview["entity_type"] == "Company"
    assert {c["name"] for c in preview["conflicts"]} == {"name"}
    assert preview["edges_to_collapse"] == 1, "both hold the same LEI"
    assert preview["edges_to_move"] == 1, "the directorship moves across"
    # Nothing changed.
    assert test_client.get(f"/api/graph/entity/{ids['second']}").status_code == 200


def test_merging_different_types_is_refused(client) -> None:
    test_client, ids = client

    response = test_client.get(
        "/api/merges/preview",
        params={"survivor_id": ids["first"], "absorbed_id": ids["director"]},
    )

    assert response.status_code == 400
    assert "same type" in response.json()["detail"]


# --- merging ----------------------------------------------------------------


def test_merging_moves_the_edges_and_hides_the_absorbed_record(client) -> None:
    test_client, ids = client

    result = test_client.post(
        "/api/merges",
        json={"survivor_id": ids["first"], "absorbed_id": ids["second"]},
    ).json()

    assert result["edges_moved"] == 1
    assert test_client.get(f"/api/graph/entity/{ids['second']}").status_code == 404
    expanded = test_client.get("/api/graph/expand", params={"entity_ids": [ids["first"]]}).json()
    assert ids["director"] in {e["id"] for e in expanded["entities"]}


def test_the_losing_name_survives_as_an_alias(client) -> None:
    test_client, ids = client

    test_client.post(
        "/api/merges", json={"survivor_id": ids["first"], "absorbed_id": ids["second"]}
    )

    survivor = test_client.get(f"/api/graph/entity/{ids['first']}").json()
    assert "Nordic Infrastructure A/S" in survivor["properties"]["aliases"]


def test_a_conflict_can_be_resolved_explicitly(client) -> None:
    test_client, ids = client

    test_client.post(
        "/api/merges",
        json={
            "survivor_id": ids["first"],
            "absorbed_id": ids["second"],
            "resolutions": {"name": "Nordic Infrastructure A/S"},
        },
    )

    survivor = test_client.get(f"/api/graph/entity/{ids['first']}").json()
    assert survivor["properties"]["name"] == "Nordic Infrastructure A/S"


def test_a_merge_is_recorded_and_can_be_undone(client) -> None:
    test_client, ids = client
    merge = test_client.post(
        "/api/merges", json={"survivor_id": ids["first"], "absorbed_id": ids["second"]}
    ).json()

    listed = test_client.get("/api/merges").json()
    assert [m["id"] for m in listed] == [merge["merge_id"]]

    undone = test_client.post(f"/api/merges/{merge['merge_id']}/undo").json()

    assert undone["undone"] is True
    assert test_client.get(f"/api/graph/entity/{ids['second']}").status_code == 200


def test_a_merge_cannot_be_undone_twice(client) -> None:
    test_client, ids = client
    merge = test_client.post(
        "/api/merges", json={"survivor_id": ids["first"], "absorbed_id": ids["second"]}
    ).json()
    test_client.post(f"/api/merges/{merge['merge_id']}/undo")

    response = test_client.post(f"/api/merges/{merge['merge_id']}/undo")

    assert response.status_code == 400


def test_undoing_an_unknown_merge_is_a_404(client) -> None:
    test_client, _ = client

    assert test_client.post("/api/merges/no-such-merge/undo").status_code == 404


def test_nothing_merges_without_being_asked(client) -> None:
    """The duplicate check proposes; it never combines."""
    test_client, ids = client

    job = test_client.post(
        "/api/actions/identity.check/run", json={"entity_id": ids["first"]}
    ).json()
    finished = test_client.get(f"/api/actions/jobs/{job['id']}").json()

    assert finished["status"] == "succeeded"
    assert finished["result"]["matches"][0]["entity_id"] == ids["second"]
    assert test_client.get(f"/api/graph/entity/{ids['second']}").status_code == 200
