"""Application entry point.

Run locally with::

    uvicorn sla.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sla import __version__
from sla.api import health, ontology
from sla.config import get_settings
from sla.graph import driver as graph_driver


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the Neo4j driver for the life of the process.

    Connecting lazily here rather than verifying the connection means the app
    still starts when Neo4j is down; ``/health`` then reports it as degraded,
    which is friendlier than a crash loop during development.
    """
    await graph_driver.connect(get_settings())
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
