"""Runtime configuration, read from the environment or a local ``.env``."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class WritePolicy(str, Enum):
    """What an action does with the nodes and edges it produces.

    ``REVIEW`` stages the result as a proposal set; nothing reaches Neo4j until
    the user accepts it. ``AUTO_COMMIT`` stages and immediately accepts, so the
    two policies share a single write path.
    """

    REVIEW = "review"
    AUTO_COMMIT = "auto_commit"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Neo4j: the knowledge graph.
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "please-change-me"
    neo4j_database: str = "neo4j"

    # Application state: charts, jobs, proposals, review queue.
    app_database_url: str = "sqlite:///./data/app.sqlite"

    # Default for actions that do not declare their own policy.
    default_write_policy: WritePolicy = WritePolicy.REVIEW

    # Credentials for later milestones; absent is fine until then.
    anthropic_api_key: str = ""
    companies_house_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once."""
    return Settings()
