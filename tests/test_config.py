"""Settings parse from the environment, with review as the safe default."""

from __future__ import annotations

import pytest

from sla.config import Settings, WritePolicy


def test_write_policy_defaults_to_review() -> None:
    """Nothing reaches Neo4j unreviewed unless explicitly configured otherwise."""
    assert Settings(_env_file=None).default_write_policy is WritePolicy.REVIEW


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://graph.internal:7687")
    monkeypatch.setenv("DEFAULT_WRITE_POLICY", "auto_commit")

    settings = Settings(_env_file=None)

    assert settings.neo4j_uri == "bolt://graph.internal:7687"
    assert settings.default_write_policy is WritePolicy.AUTO_COMMIT
