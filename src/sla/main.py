"""Application entry point.

Run locally with::

    uvicorn sla.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from sla import __version__
from sla.actions import (  # noqa: F401  (importing registers them)
    companies_house,
    duplicates,
    expand,
    gleif,
)
from sla.api import actions, charts, graph, health, merges, ontology
from sla.app import database as app_database
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
    app_database.init(settings)
    driver = await graph_driver.connect(settings)
    try:
        await schema.apply(driver, settings.neo4j_database)
    except Exception as exc:  # noqa: BLE001 - /health reports the detail
        logger.warning("could not apply graph schema: %s", exc)
    try:
        yield
    finally:
        await graph_driver.close()
        app_database.dispose()


app = FastAPI(
    title="Simple Link Analysis",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(health.router)
app.include_router(ontology.router)
app.include_router(graph.router)
app.include_router(charts.router)
app.include_router(actions.router)
app.include_router(merges.router)

# The canvas is plain ES modules with no build step, so the files are served
# as they are. Icons come straight from the ontology directory, which keeps
# them beside the declarations that reference them.
WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
ICON_ROOT = Path(__file__).resolve().parents[2] / "ontology" / "icons"
app.mount("/icons", StaticFiles(directory=ICON_ROOT), name="icons")
app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
