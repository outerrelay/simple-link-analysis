"""Shared fixtures.

The unit suite runs without a database. Tests that genuinely need Neo4j use the
``repository`` or ``neo4j_driver`` fixtures, which skip the test when no server
is reachable, so ``pytest`` is always runnable on a fresh clone.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from neo4j import AsyncGraphDatabase

from sla.config import Settings, get_settings
from sla.graph import schema
from sla.graph.identity import IdentityResolver
from sla.graph.repository import GraphRepository
from sla.main import app
from sla.ontology import load

# --- guarding a real database from the test suite -------------------------

TEST_URI_VAR = "SLA_TEST_NEO4J_URI"
ALLOW_VAR = "SLA_ALLOW_DESTRUCTIVE_TESTS"


def test_neo4j_settings() -> Settings:
    """Where the graph tests should point.

    Defaults to the configured database, but ``SLA_TEST_NEO4J_URI`` overrides
    it so a developer can keep their working graph on a different instance.
    """
    settings = Settings(_env_file=None)
    override = os.environ.get(TEST_URI_VAR)
    return settings.model_copy(update={"neo4j_uri": override}) if override else settings


async def _refuse_if_populated(driver, database: str) -> None:
    """Skip rather than wipe a database that already holds data.

    These tests clear the database between cases. Running them against a
    working graph would destroy an investigation, so a non-empty database is
    treated as somebody's, not ours, unless explicitly allowed.
    """
    if os.environ.get(ALLOW_VAR) == "1":
        return
    async with driver.session(database=database) as session:
        result = await session.run("MATCH (n) RETURN count(n) AS n LIMIT 1")
        record = await result.single()
    if record and record["n"]:
        pytest.skip(
            f"{record['n']} nodes already in this database; refusing to wipe it. "
            f"Point {TEST_URI_VAR} at a scratch instance, or set {ALLOW_VAR}=1 "
            f"if this database is disposable."
        )


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def ontology():
    return load()


async def _noop(*args: object, **kwargs: object) -> None:
    return None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A test client whose lifespan does not require a running Neo4j."""
    monkeypatch.setattr("sla.graph.driver.connect", _noop)
    monkeypatch.setattr("sla.graph.driver.close", _noop)
    monkeypatch.setattr("sla.main.schema.apply", _noop)
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def neo4j_driver() -> AsyncIterator:
    """A driver connected to a scratch Neo4j, or skip.

    Each test starts from an empty database with the schema applied, so tests
    cannot leak state into one another — which is exactly why a database that
    already holds data is refused rather than cleared.
    """
    settings = test_neo4j_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        await driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001 - any failure means "no database here"
        await driver.close()
        pytest.skip(f"no Neo4j at {settings.neo4j_uri}: {exc}")

    try:
        await _refuse_if_populated(driver, settings.neo4j_database)
    except BaseException:
        await driver.close()
        raise

    await schema.apply(driver, settings.neo4j_database)
    await schema.drop_all_data(driver, settings.neo4j_database)
    try:
        yield driver
    finally:
        await schema.drop_all_data(driver, settings.neo4j_database)
        await driver.close()


@pytest_asyncio.fixture
async def repository(neo4j_driver, ontology) -> GraphRepository:
    return GraphRepository(neo4j_driver, ontology, test_neo4j_settings().neo4j_database)


@pytest_asyncio.fixture
async def resolver(neo4j_driver, ontology) -> IdentityResolver:
    return IdentityResolver(neo4j_driver, ontology, test_neo4j_settings().neo4j_database)
