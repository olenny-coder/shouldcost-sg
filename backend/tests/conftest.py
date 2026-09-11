"""Shared pytest fixtures.

The DATABASE_URL is redirected to a throwaway SQLite file BEFORE any application
module is imported, so the test suite never touches the developer database.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

_TMP_DIR = pathlib.Path(tempfile.mkdtemp(prefix="shouldcost-tests-"))
# Default to a throwaway SQLite file. Set SHOULDCOST_TEST_DATABASE_URL to run the
# whole suite against a real PostgreSQL instance instead - the engine the app
# actually runs on in production. Point it at a DISPOSABLE database: the suite
# creates and drops tables and rewrites the seeded sample uploads.
os.environ["DATABASE_URL"] = os.environ.get(
    "SHOULDCOST_TEST_DATABASE_URL",
    f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}",
)
# The suite asserts DEVELOPMENT CORS behaviour, so pin the environment rather
# than inheriting whatever the host set. Without this, running the tests inside a
# production container (ENVIRONMENT=production, no FRONTEND_URL) yields an
# intentionally empty CORS allowlist and the preflight tests fail for the right
# reason in the wrong place. Production CORS is covered by unit tests that build
# Settings directly.
os.environ["ENVIRONMENT"] = "development"
os.environ.pop("FRONTEND_URL", None)
os.environ.pop("FRONTEND_PREVIEW_REGEX", None)

import pytest  # noqa: E402

from app.db import get_engine, get_session_factory, init_db  # noqa: E402
from app.etl import run_etl  # noqa: E402
from app.models import BoQUpload  # noqa: E402

SAMPLE_FILENAME = "sample_boq.csv"


@pytest.fixture(scope="session", autouse=True)
def _prepared_database():
    init_db()
    run_etl()
    yield
    engine = get_engine()
    engine.dispose()


@pytest.fixture()
def session():
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def sample_upload_id(session) -> int:
    upload = session.query(BoQUpload).filter_by(filename=SAMPLE_FILENAME).one()
    return upload.id


@pytest.fixture(scope="module")
def client_module():
    """Module-scoped TestClient, shared with test_multi_country.py."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def tmp_data_dir() -> pathlib.Path:
    path = _TMP_DIR / "uploads"
    path.mkdir(exist_ok=True)
    return path
