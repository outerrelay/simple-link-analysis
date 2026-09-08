"""Session handling for the application store."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sla.app.models import Base
from sla.config import Settings

_engine = None
_session_factory: sessionmaker[Session] | None = None


def init(settings: Settings) -> None:
    """Create the engine and the tables, making the SQLite directory if needed."""
    global _engine, _session_factory

    url = settings.app_database_url
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(url, future=True)
    Base.metadata.create_all(_engine)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transactional session: commits on success, rolls back on error."""
    if _session_factory is None:
        raise RuntimeError("application database not initialised; call init() first")
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
