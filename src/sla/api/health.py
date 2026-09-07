"""Health endpoint.

Reports whether the process can actually reach Neo4j, rather than merely that
the web server is up — a distinction worth having the moment anything depends
on the database.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from sla import __version__
from sla.config import Settings, get_settings
from sla.graph import driver as graph_driver

router = APIRouter(tags=["health"])


class Neo4jHealth(BaseModel):
    connected: bool
    name: str | None = None
    version: str | None = None
    edition: str | None = None
    error: str | None = None


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    app_version: str
    neo4j: Neo4jHealth


@router.get("/health", response_model=Health)
async def health(settings: Settings = Depends(get_settings)) -> Health:
    """Return app version and Neo4j connectivity.

    Always answers 200 so that a monitoring probe can distinguish "the app is
    down" from "the app is up but its database is not"; the ``status`` field
    carries that verdict.
    """
    try:
        info = await graph_driver.server_info(settings)
    except Exception as exc:  # noqa: BLE001 - any failure means "not connected"
        return Health(
            status="degraded",
            app_version=__version__,
            neo4j=Neo4jHealth(connected=False, error=str(exc)),
        )

    return Health(
        status="ok",
        app_version=__version__,
        neo4j=Neo4jHealth(
            connected=True,
            name=info.name,
            version=info.version,
            edition=info.edition,
        ),
    )
