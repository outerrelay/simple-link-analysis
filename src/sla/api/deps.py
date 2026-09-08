"""Shared dependencies for the API routers."""

from __future__ import annotations

from functools import lru_cache

from sla.config import get_settings
from sla.graph import driver as graph_driver
from sla.graph.identity import IdentityResolver
from sla.graph.repository import GraphRepository
from sla.ontology import Ontology, load


@lru_cache
def get_ontology() -> Ontology:
    """Load once per process; restart to pick up an edited ontology file."""
    return load()


def get_repository() -> GraphRepository:
    settings = get_settings()
    return GraphRepository(graph_driver.get_driver(), get_ontology(), settings.neo4j_database)


def get_resolver() -> IdentityResolver:
    settings = get_settings()
    return IdentityResolver(graph_driver.get_driver(), get_ontology(), settings.neo4j_database)
