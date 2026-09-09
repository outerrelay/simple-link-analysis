"""Lifecycle and health of the Neo4j connection.

A single async driver is shared by the whole process; Neo4j's driver is
thread-safe and pools connections internally, so creating more than one wastes
sockets for no benefit.
"""

from __future__ import annotations

from dataclasses import dataclass

from neo4j import AsyncDriver, AsyncGraphDatabase

from sla.config import Settings

_driver: AsyncDriver | None = None


@dataclass(frozen=True)
class ServerInfo:
    """Identity of the Neo4j server currently connected to."""

    name: str
    version: str
    edition: str


def get_driver() -> AsyncDriver:
    """Return the shared driver, which must have been opened by ``connect``."""
    if _driver is None:
        raise RuntimeError("Neo4j driver not initialised; call connect() first")
    return _driver


async def connect(settings: Settings) -> AsyncDriver:
    """Open the shared driver. Safe to call repeatedly; only the first opens one."""
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            # Expansion names every relationship type the ontology declares,
            # so a database that does not yet hold one of them warns on every
            # query. That is expected, not a problem worth logging.
            notifications_disabled_categories=["UNRECOGNIZED"],
        )
    return _driver


async def close() -> None:
    """Close the shared driver and forget it."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def server_info(settings: Settings) -> ServerInfo:
    """Ask the server to identify itself.

    Doubles as a connectivity check: it fails loudly if the database is
    unreachable or the credentials are wrong.
    """
    driver = get_driver()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            "CALL dbms.components() YIELD name, versions, edition "
            "RETURN name, versions[0] AS version, edition"
        )
        record = await result.single()

    if record is None:
        raise RuntimeError("Neo4j returned no component information")
    return ServerInfo(name=record["name"], version=record["version"], edition=record["edition"])
