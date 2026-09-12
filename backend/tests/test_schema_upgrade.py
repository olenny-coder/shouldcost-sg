"""Adding a column to a table that already exists in a deployed database.

create_all() creates missing TABLES but never ALTERs an existing one, so before
add_missing_columns() a new column was invisible until the table was rebuilt - and
rebuilding means --reset, which drops every uploaded BoQ. These tests pin the narrow
behaviour that replaces that: it ADDS columns, and refuses anything it cannot do safely.

They exercise the SHIPPED helper by passing it an old and a new schema side by side, so a
regression in the real code fails the test rather than a copy of it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect, text

from app.db import Base, add_missing_columns


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'upgrade.db').as_posix()}")
    yield engine
    engine.dispose()


def _widgets(metadata: MetaData) -> Table:
    return Table(
        "widgets",
        metadata,
        Column("id", String, primary_key=True),
        Column("kept", String, nullable=False, default="x"),
    )


def test_a_missing_column_is_added_and_defaulted(engine) -> None:
    """The deployed table predates the column; it must appear, defaulted, without data loss."""
    old = MetaData()
    _widgets(old)
    old.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO widgets (id, kept) VALUES ('a', 'v')"))

    new = MetaData()
    _widgets(new).append_column(Column("added", String, nullable=False, default=""))

    assert add_missing_columns(engine, new) == ["widgets.added"]
    assert "added" in {c["name"] for c in inspect(engine).get_columns("widgets")}
    with engine.begin() as conn:
        rows = list(conn.execute(text("SELECT id, kept, added FROM widgets")))
    # The existing row survives, with the default applied to the new column.
    assert rows == [("a", "v", "")]


def test_a_table_that_does_not_exist_yet_is_left_to_create_all(engine) -> None:
    new = MetaData()
    Table(
        "fresh",
        new,
        Column("id", String, primary_key=True),
        Column("added", String, nullable=False, default=""),
    )
    assert add_missing_columns(engine, new) == []


def test_a_required_column_with_no_default_is_refused(engine) -> None:
    """Silently inserting NULLs into a NOT NULL column would be worse than failing loudly."""
    old = MetaData()
    _widgets(old)
    old.create_all(engine)

    new = MetaData()
    _widgets(new).append_column(Column("required", String, nullable=False))
    with pytest.raises(RuntimeError, match="cannot be added to an existing table"):
        add_missing_columns(engine, new)


def test_nothing_to_do_when_the_schema_is_current(engine) -> None:
    new = MetaData()
    _widgets(new)
    new.create_all(engine)
    assert add_missing_columns(engine, new) == []


def test_the_shipped_helper_walks_the_application_metadata(engine) -> None:
    """With no metadata supplied it uses the app's own, and is a no-op when already current."""
    Base.metadata.create_all(engine)
    assert add_missing_columns(engine) == []
