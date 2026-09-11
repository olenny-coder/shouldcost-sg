"""Database engine, session factory and FastAPI dependency.

The same code path serves local SQLite and production PostgreSQL. Only the
SQLite branch needs `check_same_thread=False`, because FastAPI runs synchronous
endpoints in a worker threadpool.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def _engine_options(url: str) -> dict:
    options: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    return options


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        _engine = create_engine(url, **_engine_options(url))
    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _session_factory


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_engine() -> None:
    """Dispose and forget the cached engine (used by tests and `make reset-db`)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def init_db() -> None:
    """Create every table. Safe to call repeatedly."""
    from . import models  # noqa: F401  (import registers the mappers)

    Base.metadata.create_all(bind=get_engine())
