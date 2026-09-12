"""AUTO_SEED: loading the reference data on first boot.

The free Render plan has no Shell, so the documented "run python -m app.etl once"
step has nowhere to run. AUTO_SEED closes that gap, and the risk it carries is
re-seeding on every cold start - so the guard that it only fires on an EMPTY
database is the thing most worth testing.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, func, select

import app.config as config_module
import app.db as db_module


@pytest.fixture()
def isolated_database(tmp_path):
    """Point the application at a throwaway database for one test, then restore.

    The environment is restored BEFORE the engine cache is cleared, so no stale
    engine for the temporary file can leak into later tests.
    """
    keys = ("DATABASE_URL", "AUTO_SEED", "ENVIRONMENT")
    saved = {key: os.environ.get(key) for key in keys}
    db_file = tmp_path / "autoseed.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"
    os.environ["ENVIRONMENT"] = "development"
    config_module.reset_settings_cache()
    db_module.reset_engine()
    try:
        yield db_file
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config_module.reset_settings_cache()
        db_module.reset_engine()


def _count(model) -> int:
    session = db_module.get_session_factory()()
    try:
        return session.scalar(select(func.count()).select_from(model)) or 0
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# The flag
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", "On"])
def test_auto_seed_flag_accepts_the_usual_truthy_spellings(monkeypatch, raw) -> None:
    monkeypatch.setenv("AUTO_SEED", raw)
    config_module.reset_settings_cache()
    try:
        assert config_module.Settings().auto_seed is True
    finally:
        config_module.reset_settings_cache()


@pytest.mark.parametrize("raw", ["", "0", "false", "no", "off", "banana"])
def test_auto_seed_is_off_by_default(monkeypatch, raw) -> None:
    monkeypatch.setenv("AUTO_SEED", raw)
    config_module.reset_settings_cache()
    try:
        assert config_module.Settings().auto_seed is False
    finally:
        config_module.reset_settings_cache()


# --------------------------------------------------------------------------- #
# Refreshing the rate library
# --------------------------------------------------------------------------- #
def test_the_rate_library_is_replaced_not_accumulated(isolated_database) -> None:
    """A changed description or source must REPLACE the old row, not sit beside it.

    The natural key of benchmark_rates includes the description, the source and the base
    year, because two rates for one section are legitimate. The cost of that is that
    rewording a row - which is exactly what rebuilding the library from the BCA and CPWD
    schedules did - leaves the old row orphaned rather than updated. The engine then picks
    between them by confidence, and the deployed database ended up preferring an OLD
    invented rate over the new published one because the old row was 'medium' and the new
    one 'low' for Singapore Concrete.
    """
    from app.etl import load_benchmark_rates, run_etl
    from app.models import BenchmarkRate

    db_module.init_db()
    run_etl()

    session = db_module.get_session_factory()()
    try:
        # Simulate the pre-refresh library: an extra row per section, worded differently,
        # carrying a HIGHER confidence than the real one.
        for section, rate in (("Concrete", 145.0), ("Reinforcement", 1150.0)):
            session.add(
                BenchmarkRate(
                    country="SG", smm2_section=section, classification_standard="SMM2",
                    description=f"Legacy invented rate for {section}", unit="m3" if section == "Concrete" else "tonne",
                    base_rate=rate, currency="SGD", base_year=2010, base_quarter="",
                    source="Legacy source string", source_url="", source_date="",
                    scope_inclusions="", scope_exclusions="", confidence="medium",
                    is_placeholder=True, provenance_note="legacy", replace_with="# TODO: legacy",
                )
            )
        session.commit()
        assert session.scalar(
            select(func.count()).select_from(BenchmarkRate)
        ) == 22, "the legacy rows must be present before the refresh"
        session.close()

        # Re-running the ETL is what a reseed does.
        run_etl()
    finally:
        session.close()

    session = db_module.get_session_factory()()
    try:
        assert session.scalar(select(func.count()).select_from(BenchmarkRate)) == 20
        concrete = list(
            session.scalars(
                select(BenchmarkRate).where(
                    BenchmarkRate.country == "SG", BenchmarkRate.smm2_section == "Concrete"
                )
            )
        )
        assert len(concrete) == 1, "the stale row must be gone, not merely outranked"
        assert concrete[0].base_rate == pytest.approx(140.53)
        assert concrete[0].base_quarter == "2026Q2"
    finally:
        session.close()


def test_a_tie_in_confidence_is_broken_deterministically(session) -> None:
    """Two rows, same confidence: the one stating its own base quarter must win."""
    from app.benchmark import load_benchmark_rates
    from app.models import BenchmarkRate

    for rate, base_quarter, base_year in ((999.0, "", 2010), (111.0, "2026Q2", 2026)):
        session.add(
            BenchmarkRate(
                country="ZZ", smm2_section="Concrete", classification_standard="SMM2",
                description=f"tie breaker candidate {base_quarter}", unit="m3",
                base_rate=rate, currency="SGD", base_year=base_year, base_quarter=base_quarter,
                source="test fixture", source_url="", source_date="",
                scope_inclusions="", scope_exclusions="", confidence="medium",
                is_placeholder=True, provenance_note="", replace_with="",
            )
        )
    session.commit()
    try:
        chosen = load_benchmark_rates(session, country="ZZ")
        assert chosen["Concrete"].base_rate == pytest.approx(111.0)
        assert chosen["Concrete"].base_quarter == "2026Q2"
    finally:
        session.execute(delete(BenchmarkRate).where(BenchmarkRate.country == "ZZ"))
        session.commit()


# --------------------------------------------------------------------------- #
# The behaviour
# --------------------------------------------------------------------------- #
def test_an_empty_database_gets_seeded(isolated_database) -> None:
    from app.main import seed_if_empty
    from app.models import BoQUpload, RegionalFactor, TPISeries

    db_module.init_db()
    assert _count(TPISeries) == 0

    outcome = seed_if_empty()

    assert outcome["seeded"] is True
    assert outcome["reason"] == "empty database"
    assert _count(TPISeries) > 100
    assert _count(RegionalFactor) >= 10
    assert _count(BoQUpload) == 2
    # The real published data must arrive with it, not just the placeholders.
    session = db_module.get_session_factory()()
    try:
        real = session.scalar(
            select(func.count()).select_from(TPISeries).where(TPISeries.is_placeholder.is_(False))
        )
    finally:
        session.close()
    assert real and real > 0


def test_a_populated_database_is_left_alone(isolated_database) -> None:
    """A cold start must not re-run the ETL over data that is already there."""
    from app.main import seed_if_empty
    from app.models import TPISeries

    db_module.init_db()
    seed_if_empty()  # first boot
    before = _count(TPISeries)

    second = seed_if_empty()  # a later cold start

    assert second["seeded"] is False
    assert second["reason"] == "already populated"
    assert second["existing_rows"] == before
    assert _count(TPISeries) == before


def test_seeding_is_repeatable_and_does_not_duplicate(isolated_database) -> None:
    from app.etl import run_etl
    from app.models import BoQItem, MaterialPrice, TPISeries

    db_module.init_db()
    first = run_etl()
    second = run_etl()

    # Row counts must be identical; the insert counts must not be, because the
    # second run finds everything already present.
    assert first["rows"] == second["rows"]
    assert first["inserted"]["tpi_series"] == 126
    assert second["inserted"]["tpi_series"] == 0
    assert second["inserted"]["material_prices"] == 0
    assert _count(TPISeries) == 126
    assert _count(MaterialPrice) == 180
    assert _count(BoQItem) == 40


def test_an_unreadable_table_does_not_crash_the_boot(isolated_database) -> None:
    """If the schema is missing entirely, report it rather than raise."""
    from app.main import seed_if_empty

    # Deliberately no init_db(): the table does not exist yet.
    outcome = seed_if_empty()
    assert outcome["seeded"] is False
    assert outcome["reason"] == "unreadable"


def test_the_lifespan_seeds_when_the_flag_is_on(isolated_database) -> None:
    """End to end: a TestClient boot should leave a seeded database."""
    from fastapi.testclient import TestClient

    from app.models import TPISeries

    os.environ["AUTO_SEED"] = "true"
    config_module.reset_settings_cache()
    db_module.reset_engine()

    import app.main as main_module

    # app.main captures its Settings at import time, so patching the environment
    # alone would not reach the lifespan handler.
    original = main_module.settings
    main_module.settings = config_module.Settings()
    try:
        with TestClient(main_module.app) as client:
            assert client.get("/api/healthz").json()["status"] == "ok"
            assert _count(TPISeries) > 100
            assert client.get("/api/indices/regions?country=IN").json()
    finally:
        main_module.settings = original


def test_the_lifespan_does_not_seed_without_the_flag(isolated_database) -> None:
    from fastapi.testclient import TestClient

    from app.models import TPISeries

    os.environ["AUTO_SEED"] = "false"
    config_module.reset_settings_cache()
    db_module.reset_engine()

    import app.main as main_module

    original = main_module.settings
    main_module.settings = config_module.Settings()
    try:
        with TestClient(main_module.app) as client:
            assert client.get("/api/healthz").json()["status"] == "ok"
            assert _count(TPISeries) == 0
    finally:
        main_module.settings = original


def test_config_endpoint_reports_the_flag(client_module) -> None:
    assert "auto_seed" in client_module.get("/api/config").json()
