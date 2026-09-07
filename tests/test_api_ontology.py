"""The ontology endpoint the canvas reads its styling and menu from."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sla.ontology import load


def test_endpoint_returns_only_concrete_types(client: TestClient) -> None:
    body = client.get("/api/ontology").json()

    assert "Company" in body["entity_types"]
    assert "LegalEntity" not in body["entity_types"], "abstract types are not instantiable"
    assert body["version"] == load().version


def test_entity_types_carry_what_the_canvas_needs_to_draw_them(client: TestClient) -> None:
    company = client.get("/api/ontology").json()["entity_types"]["Company"]

    assert company["icon"] and company["color"]
    assert company["display_name"]
    assert company["labels"][0] == "Company"


def test_outgoing_relationships_drive_the_context_menu(client: TestClient) -> None:
    """A Person is never offered 'issued tender'."""
    types = client.get("/api/ontology").json()["entity_types"]

    assert "DIRECTOR_OF" in types["Person"]["outgoing_relationships"]
    assert "ISSUED_TENDER" not in types["Person"]["outgoing_relationships"]
    assert "ISSUED_TENDER" in types["Company"]["outgoing_relationships"]


def test_source_types_are_listed_so_the_canvas_can_hide_them(client: TestClient) -> None:
    body = client.get("/api/ontology").json()

    assert set(body["source_types"]) == {"ApiRecord", "Document", "NewsArticle", "WebPage"}


def test_relationship_endpoints_are_concrete(client: TestClient) -> None:
    owns = client.get("/api/ontology").json()["relationship_types"]["OWNS"]

    assert "Company" in owns["target_types"]
    assert "LegalEntity" not in owns["target_types"]
    assert owns["directed"] is True


def test_system_relationships_are_kept_separate(client: TestClient) -> None:
    body = client.get("/api/ontology").json()

    assert set(body["system_relationship_types"]) == {"MENTIONS", "SAME_AS"}
    assert "SAME_AS" not in body["relationship_types"]
