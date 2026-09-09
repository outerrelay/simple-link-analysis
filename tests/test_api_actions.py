"""The HTTP surface for running actions and reviewing proposals."""

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

        person = await repository.upsert_entity(
            EntityRecord(type="Person", properties={"name": "A. Director"})
        )
        company = await repository.upsert_entity(
            EntityRecord(
                type="Company",
                properties={"name": "Acme AS", "registration_number": "912345678"},
            )
        )
        await repository.assert_relationship(
            predicate="DIRECTOR_OF", subject_id=person.id, object_id=company.id
        )
        return {"person": person.id, "company": company.id}
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


def run_and_wait(client: TestClient, action_id: str, entity_id: str) -> dict:
    """Start an action and read the finished job.

    TestClient runs background tasks before returning, so the job has already
    finished by the time the response arrives.
    """
    started = client.post(f"/api/actions/{action_id}/run", json={"entity_id": entity_id})
    assert started.status_code == 202, started.text
    return client.get(f"/api/actions/jobs/{started.json()['id']}").json()


def test_actions_are_listed_for_an_entity_type(client) -> None:
    test_client, _ = client

    for_company = {a["id"]: a for a in test_client.get("/api/actions/for/Company").json()}

    assert "gleif.lookup" in for_company
    assert "expand.database" in for_company
    assert for_company["companies_house.profile"]["available"] is False


def test_an_unknown_entity_type_is_a_404(client) -> None:
    test_client, _ = client

    assert test_client.get("/api/actions/for/Nonsense").status_code == 404


def test_expanding_from_the_database_auto_commits(client) -> None:
    """Nothing to decide: the data is already stored."""
    test_client, ids = client

    job = run_and_wait(test_client, "expand.database", ids["person"])

    assert job["status"] == "succeeded"
    assert "committed" in job["message"]
    proposal = test_client.get(f"/api/actions/proposals/{job['proposal_set_id']}").json()
    assert proposal["status"] == "accepted"


def test_a_missing_credential_fails_the_job_rather_than_the_request(client) -> None:
    """The job records why, instead of the run endpoint erroring."""
    test_client, ids = client

    job = run_and_wait(test_client, "companies_house.profile", ids["company"])

    assert job["status"] == "failed"
    assert "companies_house_api_key" in job["message"]


def test_running_an_action_on_the_wrong_type_fails_the_job(client) -> None:
    test_client, ids = client

    job = run_and_wait(test_client, "gleif.lookup", ids["person"])

    assert job["status"] == "failed"
    assert "does not apply" in job["message"]


def test_unknown_action_is_a_404(client) -> None:
    test_client, ids = client

    response = test_client.post(
        "/api/actions/no.such.action/run", json={"entity_id": ids["person"]}
    )

    assert response.status_code == 404


def test_unknown_job_is_a_404(client) -> None:
    test_client, _ = client

    assert test_client.get("/api/actions/jobs/no-such-job").status_code == 404


# --- deleting from the graph ------------------------------------------------


def test_deleting_suppresses_by_default(client) -> None:
    """Reversible, and leaves the audit trail intact."""
    test_client, ids = client

    response = test_client.delete(f"/api/actions/entity/{ids['company']}")

    assert response.status_code == 204
    assert test_client.get(f"/api/graph/entity/{ids['company']}").status_code == 404


def test_suppression_survives_re_ingestion(client) -> None:
    """The tombstone is what makes a rejection stick."""
    test_client, ids = client
    test_client.delete(f"/api/actions/entity/{ids['company']}")

    # Expanding the person would otherwise pull the company back in.
    run_and_wait(test_client, "expand.database", ids["person"])

    assert test_client.get(f"/api/graph/entity/{ids['company']}").status_code == 404


def test_hard_delete_removes_the_entity(client) -> None:
    test_client, ids = client

    response = test_client.delete(
        f"/api/actions/entity/{ids['company']}", params={"suppress": False}
    )

    assert response.status_code == 204
    assert test_client.get(f"/api/graph/entity/{ids['company']}").status_code == 404


def test_deleting_something_absent_is_a_404(client) -> None:
    test_client, _ = client

    assert test_client.delete("/api/actions/entity/no-such-id").status_code == 404
