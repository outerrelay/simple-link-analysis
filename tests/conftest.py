"""Shared fixtures.

The unit suite must run without Neo4j; tests that genuinely need a live
database are marked ``neo4j`` and skipped unless one is reachable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sla.config import Settings, get_settings
from sla.main import app


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A test client whose lifespan does not require a running Neo4j."""
    monkeypatch.setattr("sla.graph.driver.connect", _noop)
    monkeypatch.setattr("sla.graph.driver.close", _noop)
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client


async def _noop(*args: object, **kwargs: object) -> None:
    return None
