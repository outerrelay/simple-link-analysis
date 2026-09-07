"""The health endpoint reports app version and Neo4j reachability."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sla import __version__
from sla.graph.driver import ServerInfo


def test_health_reports_connected_server(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_server_info(_settings: object) -> ServerInfo:
        return ServerInfo(name="Neo4j Kernel", version="5.26.0", edition="community")

    monkeypatch.setattr("sla.api.health.graph_driver.server_info", fake_server_info)

    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["app_version"] == __version__
    assert body["neo4j"] == {
        "connected": True,
        "name": "Neo4j Kernel",
        "version": "5.26.0",
        "edition": "community",
        "error": None,
    }


def test_health_is_degraded_when_database_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable database is reported, not raised.

    The endpoint still answers 200 so a probe can tell "app down" apart from
    "app up, database down".
    """

    async def failing_server_info(_settings: object) -> ServerInfo:
        raise ConnectionError("cannot reach bolt://localhost:7687")

    monkeypatch.setattr("sla.api.health.graph_driver.server_info", failing_server_info)

    response = client.get("/health")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert body["neo4j"]["connected"] is False
    assert "cannot reach" in body["neo4j"]["error"]
