"""The section-to-published-index coverage matrix.

The dashboard states, per measurement section, whether a published series can
re-price it. These tests hold that statement honest: every canonical section must
appear, India's new producer series must actually close the sections they claim,
and no section may be reported as covered by a series that is not loaded.
"""

from __future__ import annotations

import pytest

from app import coverage
from app.classifier import SMM2_SECTIONS, UNCLASSIFIED


EXPECTED_SECTIONS = set(SMM2_SECTIONS) | {UNCLASSIFIED}


def test_coverage_names_every_canonical_section(session) -> None:
    payload = coverage.build_coverage(session, "SG")
    assert {row["section"] for row in payload["sections"]} == EXPECTED_SECTIONS


def test_india_covers_every_section_with_a_producer_index(session) -> None:
    """Excavation used to be uncovered; Petroleum Products closed it.

    A section with no covering series is held at base year by the benchmark, so an
    uncovered section is a real limitation, not a cosmetic one.
    """
    payload = coverage.build_coverage(session, "IN")
    assert payload["uncovered_sections"] in ([], [UNCLASSIFIED])
    covered = set(payload["producer_covered_sections"])
    assert {
        "Excavation",
        "Piling",
        "Concrete",
        "Reinforcement",
        "Formwork",
        "Masonry",
        "Waterproofing",
        "Plaster",
        "M&E Containment",
    } <= covered


def test_india_reports_the_producer_series_as_preferred(session) -> None:
    payload = coverage.build_coverage(session, "IN")
    assert payload["preferred_producer_series"] == "PPI-ALL"
    assert payload["preferred_consumer_series"] == "CPI-ALL"
    assert payload["producer_series_count"] >= 16
    assert payload["consumer_series_count"] == 2


def test_singapore_documents_the_missing_producer_index(session) -> None:
    """Singapore has no usable PPI. That must be stated, not glossed over."""
    payload = coverage.build_coverage(session, "SG")
    assert payload["producer_series_count"] == 0
    assert payload["preferred_producer_series"] == ""
    assert any("M213461" in note or "M213411" in note for note in payload["notes"])


def test_every_claimed_series_is_actually_loaded(session) -> None:
    """A coverage row may only name series that exist for that country."""
    from sqlalchemy import select

    from app.models import PriceSeries

    for code in ("SG", "IN"):
        loaded = {
            row.series_name
            for row in session.scalars(select(PriceSeries).where(PriceSeries.country == code))
        }
        payload = coverage.build_coverage(session, code)
        for row in payload["sections"]:
            for series in row["producer_series"] + row["consumer_series"]:
                assert series["series_name"] in loaded
                # And the series must actually claim the section it is listed under.
                scoped = {p.strip() for p in series["scope_sections"].split(";") if p.strip()}
                assert row["section"] in scoped


def test_labour_gap_is_reported_for_labour_dominated_sections(session) -> None:
    payload = coverage.build_coverage(session, "IN")
    assert "Excavation" in payload["labour_dominated_sections"]
    excavation = [r for r in payload["sections"] if r["section"] == "Excavation"][0]
    assert excavation["status"] == "producer_plus_labour_gap"
    assert any("labour" in s["name"].lower() for s in excavation["gap_sources"])


def test_every_gap_source_carries_a_real_url_and_status(session) -> None:
    for code in ("SG", "IN"):
        payload = coverage.build_coverage(session, code)
        for row in payload["sections"]:
            for gap in row["gap_sources"]:
                assert gap["url"].startswith("https://")
                assert gap["status"] in {"wired", "partial", "gap"}
                assert gap["what"].strip()


def test_the_new_producer_series_are_real_published_values(session) -> None:
    """PPI-PETRO and PPI-AGG are the two that closed previously empty sections."""
    from sqlalchemy import select

    from app.models import PriceSeries

    for name in ("PPI-PETRO", "PPI-AGG", "PPI-LIME", "PPI-ELECIND", "PPI-ELECOTH"):
        rows = list(
            session.scalars(
                select(PriceSeries).where(
                    PriceSeries.country == "IN", PriceSeries.series_name == name
                )
            )
        )
        assert rows, f"{name} is not loaded"
        assert all(row.kind == "PPI" for row in rows)
        assert all(row.is_placeholder is False for row in rows)
        assert all(row.source_url.startswith("https://eaindustry.nic.in/") for row in rows)
        assert all(row.base_year == 2022 for row in rows)


def test_price_series_endpoint_exposes_the_index_kind(client_module) -> None:
    rows = client_module.get(
        "/api/indices/price-series", params={"country": "IN", "series": "PPI-CEM"}
    ).json()
    assert rows
    assert rows[0]["kind"] == "PPI"
    assert rows[0]["scope_sections"] == "Concrete;Masonry;Plaster"
    assert "Cement" in rows[0]["title"]


def test_coverage_endpoint(client_module) -> None:
    payload = client_module.get("/api/indices/coverage", params={"country": "IN"}).json()
    assert payload["country"] == "IN"
    assert payload["classification_standard"].startswith("IS 1200")
    assert payload["sections"]
    assert payload["notes"]


def test_coverage_endpoint_is_country_scoped(client_module) -> None:
    payload = client_module.get("/api/indices/coverage", params={"country": "SG"}).json()
    assert payload["country"] == "SG"
    assert payload["producer_series_count"] == 0


def test_coverage_endpoint_rejects_an_unknown_country(client_module) -> None:
    response = client_module.get("/api/indices/coverage", params={"country": "ZZ"})
    assert response.status_code == 422


@pytest.mark.parametrize("section", sorted(SMM2_SECTIONS))
def test_every_section_has_an_explanatory_note(session, section: str) -> None:
    payload = coverage.build_coverage(session, "IN")
    row = [r for r in payload["sections"] if r["section"] == section][0]
    assert row["note"], f"{section} has no coverage note"
