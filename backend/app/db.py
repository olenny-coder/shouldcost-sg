"""Database engine, session factory and FastAPI dependency.

The same code path serves local SQLite and production PostgreSQL. Only the
SQLite branch needs `check_same_thread=False`, because FastAPI runs synchronous
endpoints in a worker threadpool.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.schema import MetaData
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
    """Create every table, then add any columns an older table is missing.

    Safe to call repeatedly. See add_missing_columns for why the second step exists.
    """
    from . import models  # noqa: F401  (import registers the mappers)

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    add_missing_columns(engine)


def add_missing_columns(engine: Engine, metadata: MetaData | None = None) -> list[str]:
    """ALTER TABLE ... ADD COLUMN for any model column an existing table lacks.

    create_all() creates missing TABLES but never alters an existing one, so before this
    a new column was invisible until the table was rebuilt - and rebuilding means --reset,
    which drops every uploaded BoQ. Adding a nullable column is the one migration that can
    be done safely and automatically, so it is done here rather than left to the operator.

    Deliberately narrow: it only ADDS columns, never drops, retypes or renames one. A
    change of that kind still needs --reset, and a real deployment wants a migration tool.

    `metadata` defaults to the application's own; tests pass an older or newer schema so the
    behaviour can be exercised against a table that genuinely predates the column.
    """
    from sqlalchemy import inspect, text

    target_metadata = metadata if metadata is not None else Base.metadata
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []

    for table in target_metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all just made it, with every column
        present = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable and column.default is None and column.server_default is None:
                # Cannot be added to a populated table without a value; refuse loudly
                # rather than silently producing NULLs in a NOT NULL column.
                raise RuntimeError(
                    f"{table.name}.{column.name} is NOT NULL with no default, so it cannot be "
                    f"added to an existing table. Rebuild with --reset."
                )
            ddl = f'ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(engine.dialect)}'
            default = column.default
            if default is not None and getattr(default, "is_scalar", False):
                value = default.arg
                if isinstance(value, str):
                    ddl += f" DEFAULT '{value}'"
                elif isinstance(value, bool):
                    ddl += f" DEFAULT {'true' if value else 'false'}"
                elif isinstance(value, (int, float)):
                    ddl += f" DEFAULT {value}"
            with engine.begin() as connection:
                connection.execute(text(ddl))
            added.append(f"{table.name}.{column.name}")
    return added
