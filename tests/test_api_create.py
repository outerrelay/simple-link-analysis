"""Adding entities and relationships by hand.

Manual creation writes straight through rather than staging a proposal: the
analyst typing a company in is asserting it, and asking them to approve what
they just typed would be pointless. What is tested is that it still goes
through the ontology and still leaves a trail.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from neo4j import AsyncGraphDatabase

from sla.app import database
from sla.config import Settings, get_settings
from sla.graph import schema
from sla.main import app
from tests.conftest import scratch_neo4j_settings


async def _prepare(settings: Settings) -> None:
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        notifications_disabled_categories=["UNRECOGNIZED"],
    )
    try:
        await driver.verify_connectivity()
        await schema.apply(driver, settings.neo4j_database)
        await schema.drop_all_data(driver, settings.neo4j_database)
    finally:
        await driver.close()


async def _clear(settings: Settings) -> None:
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
        asyncio.run(_prepare(settings))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no usable scratch Neo4j: {exc}")

    with TestClient(app) as test_client:
        yield test_client

    database.dispose()
    asyncio.run(_clear(settings))
    get_settings.cache_clear()


def create(client: TestClient, entity_type: str, **properties) -> dict:
    response = client.post(
        "/api/graph/entity", json={"type": entity_type, "properties": properties}
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- entities ---------------------------------------------------------------


def test_an_entity_can_be_created_by_hand(client) -> None:
    result = create(client, "Company", name="Hand Typed AS", jurisdiction="NO")

    entity = result["entity"]
    assert entity["type"] == "Company"
    assert entity["label"] == "Hand Typed AS"
    assert client.get(f"/api/graph/entity/{entity['id']}").status_code == 200


def test_creation_is_immediate_with_nothing_to_review(client) -> None:
    """No proposal is staged: the analyst already decided by typing it."""
    create(client, "Company", name="Hand Typed AS")

    assert client.get("/api/actions/proposals").json() == []


def test_the_ontology_still_applies(client) -> None:
    response = client.post(
        "/api/graph/entity",
        json={"type": "Company", "properties": {"name": "X", "invented": "y"}},
    )

    assert response.status_code == 400
    assert "invented" in response.json()["detail"]


def test_a_missing_required_property_is_refused(client) -> None:
    response = client.post("/api/graph/entity", json={"type": "Company", "properties": {}})

    assert response.status_code == 400
    assert "requires" in response.json()["detail"]


def test_an_abstract_type_cannot_be_created(client) -> None:
    response = client.post(
        "/api/graph/entity", json={"type": "LegalEntity", "properties": {"name": "X"}}
    )

    assert response.status_code == 400


def test_an_unknown_type_is_refused(client) -> None:
    response = client.post(
        "/api/graph/entity", json={"type": "Wombat", "properties": {"name": "X"}}
    )

    assert response.status_code == 400


def test_blank_values_are_dropped_rather_than_stored(client) -> None:
    """An untouched form field should not become an empty string on the node."""
    result = create(client, "Company", name="Hand Typed AS", legal_form="", sector=[])

    assert "legal_form" not in result["entity"]["properties"]


def test_typing_in_a_canonical_type_attaches_to_the_existing_node(client) -> None:
    """Two people entering the same phone number must share one node."""
    first = create(client, "PhoneNumber", name="+47 123 45 678", e164="+4712345678")
    second = create(client, "PhoneNumber", name="+4712345678", e164="+4712345678")

    assert first["entity"]["id"] == second["entity"]["id"]


def test_a_possible_duplicate_is_reported_but_not_acted_on(client) -> None:
    create(client, "Company", name="Acme AS", registration_number="912345678")
    result = create(client, "Company", name="Acme AS", registration_number="912345678")

    assert result["matches"], "the analyst is told"
    assert result["matches"][0]["reason"]
    assert (
        len(client.get("/api/graph/search", params={"q": "Acme"}).json()) == 2
    ), "and nothing was merged"


# --- relationships ----------------------------------------------------------


def test_two_entities_can_be_connected_by_hand(client) -> None:
    person = create(client, "Person", name="A. Director")["entity"]
    company = create(client, "Company", name="Acme AS")["entity"]

    response = client.post(
        "/api/graph/relationship",
        json={
            "type": "DIRECTOR_OF",
            "source_id": person["id"],
            "target_id": company["id"],
            "properties": {"role": "chair"},
            "valid_from": "2020-01-01",
        },
    )

    assert response.status_code == 201
    edge = response.json()
    assert edge["type"] == "DIRECTOR_OF"
    assert edge["properties"]["role"] == "chair"
    assert edge["valid_from"] == "2020-01-01"


def test_a_hand_drawn_edge_records_that_a_person_drew_it(client) -> None:
    """It still arrives with an assertion behind it, like any other edge."""
    person = create(client, "Person", name="A. Director")["entity"]
    company = create(client, "Company", name="Acme AS")["entity"]
    client.post(
        "/api/graph/relationship",
        json={
            "type": "DIRECTOR_OF",
            "source_id": person["id"],
            "target_id": company["id"],
        },
    )

    edge = client.get("/api/graph/expand", params={"entity_ids": [person["id"]]}).json()[
        "relationships"
    ][0]
    assert edge["assertion_count"] == 1


def test_a_connection_the_ontology_forbids_is_refused(client) -> None:
    """A tender cannot issue a company."""
    company = create(client, "Company", name="Acme AS")["entity"]
    tender = create(client, "PublicTender", name="Bridge works")["entity"]

    response = client.post(
        "/api/graph/relationship",
        json={
            "type": "ISSUED_TENDER",
            "source_id": tender["id"],
            "target_id": company["id"],
        },
    )

    assert response.status_code == 400
    assert "does not accept" in response.json()["detail"]


def test_validity_dates_on_a_non_temporal_relationship_are_refused(client) -> None:
    company = create(client, "Company", name="Acme AS")["entity"]
    tender = create(client, "PublicTender", name="Bridge works")["entity"]

    response = client.post(
        "/api/graph/relationship",
        json={
            "type": "ISSUED_TENDER",
            "source_id": company["id"],
            "target_id": tender["id"],
            "valid_from": "2020-01-01",
        },
    )

    assert response.status_code == 400
    assert "not temporal" in response.json()["detail"]


def test_connecting_to_something_that_does_not_exist_is_refused(client) -> None:
    company = create(client, "Company", name="Acme AS")["entity"]

    response = client.post(
        "/api/graph/relationship",
        json={"type": "OWNS", "source_id": company["id"], "target_id": "no-such-id"},
    )

    assert response.status_code == 400
