"""Application entry point.

Run locally with::

    uvicorn sla.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sla import __version__
from sla.api import health, ontology
from sla.config import get_settings
from sla.graph import driver as graph_driver
from sla.graph import schema

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the Neo4j driver for the life of the process.

    Connecting lazily here rather than verifying the connection means the app
    still starts when Neo4j is down; ``/health`` then reports it as degraded,
    which is friendlier than a crash loop during development.

    When the database *is* reachable, the ontology's constraints and indexes
    are applied. They are idempotent, so this keeps a development database in
    step with the ontology on the current branch — for additive changes.
    A breaking change still needs the migration ``sla-ontology diff`` describes.
    """
    settings = get_settings()
    driver = await graph_driver.connect(settings)
    try:
        await schema.apply(driver, settings.neo4j_database)
    except Exception as exc:  # noqa: BLE001 - /health reports the detail
        logger.warning("could not apply graph schema: %s", exc)
    try:
        yield
    finally:
        await graph_driver.close()


app = FastAPI(
    title="Simple Link Analysis",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(health.router)
app.include_router(ontology.router)
