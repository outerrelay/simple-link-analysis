"""Shared fixtures.

The unit suite runs without a database. Tests that genuinely need Neo4j use the
``repository`` or ``neo4j_driver`` fixtures, which skip the test when no server
is reachable, so ``pytest`` is always runnable on a fresh clone.
"""

from __future__ import annotations

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
async def neo4j_driver(settings: Settings) -> AsyncIterator:
    """A driver connected to a live Neo4j, or skip.

    Each test starts from an empty database with the schema applied, so tests
    cannot leak state into one another.
    """
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        await driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001 - any failure means "no database here"
        await driver.close()
        pytest.skip(f"no Neo4j at {settings.neo4j_uri}: {exc}")

    await schema.apply(driver, settings.neo4j_database)
    await schema.drop_all_data(driver, settings.neo4j_database)
    try:
        yield driver
    finally:
        await schema.drop_all_data(driver, settings.neo4j_database)
        await driver.close()


@pytest_asyncio.fixture
async def repository(neo4j_driver, ontology, settings: Settings) -> GraphRepository:
    return GraphRepository(neo4j_driver, ontology, settings.neo4j_database)


@pytest_asyncio.fixture
async def resolver(neo4j_driver, ontology, settings: Settings) -> IdentityResolver:
    return IdentityResolver(neo4j_driver, ontology, settings.neo4j_database)
