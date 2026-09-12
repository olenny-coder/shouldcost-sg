"""Benchmark engine tests.

Covers the required cases: TPI up, TPI down, scope_factor != 1.0 with piling
present, missing-quarter fallback, and unclassified item handling.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.benchmark import (
    DEFAULT_BASE_YEAR_INDEX_VALUE,
    TPILookupError,
    build_benchmark,
    excluded_sections,
    normalise_unit,
    parse_quarter,
    quarter_sort_key,
    resolve_tpi,
)
from app.models import BoQUpload, BoQItem, TPISeries

TENDER_QUARTER = "2024Q4"


def _items(session, upload_id: int) -> list[BoQItem]:
    return list(session.scalars(select(BoQItem).where(BoQItem.upload_id == upload_id).order_by(BoQItem.id)))


def _run(session, upload_id: int, series: str, quarter: str = TENDER_QUARTER, threshold: float = 15.0):
    upload = session.get(BoQUpload, upload_id)
    return build_benchmark(
        session,
        upload_id=upload.id,
        filename=upload.filename,
        currency=upload.currency,
        items=_items(session, upload.id),
        tender_quarter=quarter,
        tpi_series_name=series,
        variance_threshold=threshold,
    )


# --------------------------------------------------------------------------- #
# Required case 1: TPI up
# --------------------------------------------------------------------------- #
def test_tpi_up_raises_the_adjusted_benchmark_rate(session, sample_upload_id) -> None:
    tpi = resolve_tpi(session, "BCA", TENDER_QUARTER)
    assert tpi.value == pytest.approx(139.2)
    assert tpi.base_value == DEFAULT_BASE_YEAR_INDEX_VALUE
    assert tpi.ratio == pytest.approx(1.392)
    assert tpi.ratio > 1.0

    result = _run(session, sample_upload_id, "BCA")
    # A section whose rate is still at the index series' own base year is escalated by the
    # full ratio. The SOR-derived sections are expressed at 2026Q2 instead, so they are
    # escalated only from there - asserted in its own test below.
    # scope_factor = 1/ratio holds an excluded section at the library level, so the
    # escalation is only visible on a line the series does not exclude.
    at_series_base = [
        l for l in result.lines
        if l.is_benchmarked and l.rate_base_quarter == "" and not l.scope_excluded
    ]
    assert at_series_base, "the sample must contain a section still at the series base"
    for line in at_series_base:
        assert line.tpi_ratio == pytest.approx(1.392, abs=1e-6)
        assert line.adjusted_benchmark_rate == pytest.approx(
            line.benchmark_base_rate * 1.392, abs=0.01
        )
        assert line.adjusted_benchmark_rate > line.benchmark_base_rate
        assert line.basis == "derived"


def test_sor_derived_rates_are_already_at_their_base_quarter(session, sample_upload_id) -> None:
    """A rate stated at 2026Q2 is escalated from 2026Q2, not from the index base year.

    This is the point of the base_quarter column. The BCA rates were cumulative-adjusted
    2022 -> 2026 by the schedule of rates itself, so applying the series' 2010 -> 2024
    movement on top would apply the same inflation twice. At the library's own quarter
    the ratio is therefore exactly 1.
    """
    result = _run(session, sample_upload_id, "BCA", quarter="2026Q2")
    sor = [l for l in result.lines if l.is_benchmarked and l.rate_base_quarter == "2026Q2"]
    assert sor, "the sample must contain SOR-derived sections"
    for line in sor:
        assert line.tpi_ratio == pytest.approx(1.0, abs=1e-9)
        assert line.adjusted_benchmark_rate == pytest.approx(line.benchmark_base_rate, abs=0.01)


# --------------------------------------------------------------------------- #
# Required case 2: TPI down
# --------------------------------------------------------------------------- #
@pytest.fixture()
def deflationary_series(session):
    """A synthetic series whose current value is BELOW the 2010 base of 100."""
    series_name = "TESTDN"
    existing = list(session.scalars(select(TPISeries).where(TPISeries.series_name == series_name)))
    for row in existing:
        session.delete(row)
    session.add(
        TPISeries(
            series_name=series_name,
            quarter=TENDER_QUARTER,
            base_year=2010,
            value=95.0,
            scope_inclusions="Synthetic deflationary test series",
            scope_exclusions="External Works",
            source_url="https://example.invalid/test-fixture",
            is_placeholder=True,
            replace_with="# TODO: test fixture only - not real index data",
        )
    )
    session.commit()
    yield series_name
    for row in session.scalars(select(TPISeries).where(TPISeries.series_name == series_name)):
        session.delete(row)
    session.commit()


def test_tpi_down_lowers_the_adjusted_benchmark_rate(session, sample_upload_id, deflationary_series) -> None:
    tpi = resolve_tpi(session, deflationary_series, TENDER_QUARTER)
    assert tpi.value == pytest.approx(95.0)
    assert tpi.ratio == pytest.approx(0.95)
    assert tpi.ratio < 1.0

    result = _run(session, sample_upload_id, deflationary_series)
    matched = [l for l in result.lines if l.is_benchmarked]
    assert matched
    for line in matched:
        assert line.adjusted_benchmark_rate < line.benchmark_base_rate

    # Measured on a section still at the series' own base year: a rate expressed at
    # 2026Q2 cannot be moved by a series whose last observation is 2024Q4.
    at_series_base = [
        l for l in matched if l.rate_base_quarter == "" and not l.scope_excluded
    ]
    assert at_series_base
    for line in at_series_base:
        assert line.adjusted_benchmark_rate == pytest.approx(
            line.benchmark_base_rate * 0.95, abs=0.01
        )


# --------------------------------------------------------------------------- #
# Required case 3: scope_factor != 1.0 when Piling is present and the series excludes it
# --------------------------------------------------------------------------- #
def test_rlb_scope_exclusion_fires_with_piling_present(session, sample_upload_id) -> None:
    tpi = resolve_tpi(session, "RLB", TENDER_QUARTER)
    assert "Piling" in tpi.scope_exclusions

    result = _run(session, sample_upload_id, "RLB")

    piling = [l for l in result.lines if l.smm2_section == "Piling" and l.is_benchmarked]
    assert piling, "the sample BoQ must contain a benchmarkable Piling line"
    for line in piling:
        assert line.scope_excluded is True
        assert line.scope_factor == pytest.approx(1.0 / tpi.ratio, abs=1e-6)
        assert line.scope_factor != 1.0
        assert "scope_excluded" in line.flags
        # scope_factor = 1/ratio holds the rate at base year.
        assert line.adjusted_benchmark_rate == pytest.approx(line.benchmark_base_rate, abs=0.01)

    warnings = " ".join(result.warnings)
    assert "Scope exclusion" in warnings
    assert "Piling" in warnings
    assert "RLB" in warnings

    # A non-excluded section keeps scope_factor == 1.0.
    concrete = [l for l in result.lines if l.smm2_section == "Concrete" and l.is_benchmarked][0]
    assert concrete.scope_excluded is False
    assert concrete.scope_factor == 1.0

    assert any("scope_factor" in a for a in result.assumptions)


def test_control_series_without_piling_exclusion_keeps_scope_factor_one(session, sample_upload_id) -> None:
    result = _run(session, sample_upload_id, "AECOM")
    assert all(l.scope_factor == 1.0 for l in result.lines)
    assert not any("Scope exclusion" in w for w in result.warnings)


def test_excluded_sections_alias_mapping() -> None:
    assert excluded_sections("Piling;Substructure", ["Piling", "Excavation", "Concrete"]) == {
        "Piling": "Piling",
        "Excavation": "Substructure",
    }
    assert excluded_sections("External Works", ["Piling", "Concrete"]) == {}
    assert excluded_sections("", ["Piling"]) == {}


# --------------------------------------------------------------------------- #
# Required case 4: missing-quarter fallback
# --------------------------------------------------------------------------- #
def test_missing_quarter_falls_back_to_nearest_prior_quarter(session) -> None:
    tpi = resolve_tpi(session, "BCA", "2025Q1")
    assert tpi.fallback_used is True
    assert tpi.requested_quarter == "2025Q1"
    assert tpi.resolved_quarter == "2024Q4"


def test_fallback_is_disclosed_in_warnings(session, sample_upload_id) -> None:
    result = _run(session, sample_upload_id, "BCA", quarter="2025Q1")
    assert all(l.tpi_fallback_used for l in result.lines)
    assert all(l.tpi_quarter_used == "2024Q4" for l in result.lines)
    assert any("no BCA TPI observation is published for 2025Q1" in w.lower() or
               "No BCA TPI observation is published for 2025Q1" in w for w in result.warnings)


def test_quarter_before_earliest_observation_raises_clearly(session) -> None:
    with pytest.raises(TPILookupError) as excinfo:
        resolve_tpi(session, "BCA", "2020Q1")
    message = str(excinfo.value)
    assert "2020Q1" in message
    assert "2023Q1" in message


def test_unknown_series_raises_clearly(session) -> None:
    with pytest.raises(TPILookupError) as excinfo:
        resolve_tpi(session, "NOPE", TENDER_QUARTER)
    assert "NOPE" in str(excinfo.value)
    assert "Available series" in str(excinfo.value)


def test_malformed_quarter_raises_value_error(session) -> None:
    with pytest.raises(ValueError):
        resolve_tpi(session, "BCA", "2024-Q4")


# --------------------------------------------------------------------------- #
# Required case 5: unclassified item handling
# --------------------------------------------------------------------------- #
def test_unclassified_lines_are_not_benchmarked_and_are_disclosed(session, sample_upload_id) -> None:
    result = _run(session, sample_upload_id, "BCA")
    unclassified = [l for l in result.lines if l.smm2_section == "Unclassified"]
    assert len(unclassified) == 2
    for line in unclassified:
        assert line.is_benchmarked is False
        assert line.exclusion_reason == "unclassified_section"
        assert line.benchmark_base_rate is None
        assert line.adjusted_benchmark_rate is None
        assert line.variance_abs is None
        assert line.variance_pct is None
        assert line.basis == "assumed"
        assert "unclassified" in line.flags
        # Held at the tendered rate: zero tested variance, never a fake 0% pass.
        assert line.should_cost_amount == pytest.approx(line.boq_amount)
        assert line.variance_amount == 0.0
    assert any("could not be classified" in w for w in result.warnings)
    assert any("could not be benchmarked" in a for a in result.assumptions)


def test_unit_mismatch_lines_are_excluded_from_variance(session, sample_upload_id) -> None:
    """An m2 waterproofing line classified as Piling (unit m) must not be compared."""
    result = _run(session, sample_upload_id, "BCA")
    mismatched = [l for l in result.lines if l.exclusion_reason == "unit_mismatch"]
    assert len(mismatched) == 1
    line = mismatched[0]
    assert line.unit == "m2"
    assert line.is_benchmarked is False
    assert line.variance_amount == 0.0
    assert any("unit does not match" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# Reconciliation and aggregation
# --------------------------------------------------------------------------- #
def test_waterfall_reconciles_boq_total_to_should_cost_total(session, sample_upload_id) -> None:
    result = _run(session, sample_upload_id, "BCA")
    total = result.totals["boq_total"] + sum(w["amount"] for w in result.waterfall)
    assert abs(total - result.totals["should_cost_total"]) <= 0.01

    components = {w["component"] for w in result.waterfall}
    assert components == {
        "material", "labour", "market_risk", "cpi_bridge", "scope", "overhead", "margin",
        "unexplained",
    }
    bases = {w["component"]: w["basis"] for w in result.waterfall}
    assert bases["material"] == "assumed"
    assert bases["labour"] == "assumed"
    assert bases["market_risk"] == "derived"
    # The CPI bridge is a modelled step, never a derived observation.
    assert bases["cpi_bridge"] == "assumed"
    assert bases["scope"] == "derived"
    # Overheads and margin are analyst inputs, so they are assumed - and exactly zero
    # when the analyst supplied none.
    assert bases["overhead"] == "assumed"
    assert bases["margin"] == "assumed"
    assert bases["unexplained"] == "derived"
    amounts = {w["component"]: w["amount"] for w in result.waterfall}
    assert amounts["overhead"] == 0.0
    assert amounts["margin"] == 0.0

    # The tender quarter itself needs no bridge - the index has published 2024Q4 - so
    # index_bridge reports no carry-forward for the quarter being priced.
    assert result.totals["index_bridge_applied"] is False
    assert result.index_bridge["applied"] is False
    # The bridge step is still non-zero, because the rate library is expressed at 2026Q2
    # and the index at 2026Q2 is itself derived by carrying the 2024Q4 observation
    # forward. That carry-forward is modelled, so it belongs in the bridge step even
    # though the tender quarter needed no bridge of its own.
    assert amounts["cpi_bridge"] != 0.0
    assert any("expressed at" in a for a in result.assumptions)
    # With no overheads or margin supplied, the full total is the benchmark total.
    assert result.totals["full_should_cost_total"] == result.totals["should_cost_total"]


def test_per_line_amounts_sum_to_the_totals(session, sample_upload_id) -> None:
    result = _run(session, sample_upload_id, "BCA")
    assert abs(sum(l.boq_amount for l in result.lines) - result.totals["boq_total"]) <= 0.01
    assert abs(sum(l.should_cost_amount for l in result.lines) - result.totals["should_cost_total"]) <= 0.01


def test_section_aggregates_sum_to_totals(session, sample_upload_id) -> None:
    result = _run(session, sample_upload_id, "BCA")
    assert abs(sum(s["boq_amount"] for s in result.sections) - result.totals["boq_total"]) <= 0.01
    assert abs(sum(s["should_cost_amount"] for s in result.sections) - result.totals["should_cost_total"]) <= 0.01


def test_the_sample_boq_visibly_breaches_the_threshold(session, sample_upload_id) -> None:
    result = _run(session, sample_upload_id, "BCA", threshold=15.0)
    over = [l for l in result.lines if "over_threshold" in l.flags]
    under = [l for l in result.lines if "under_threshold" in l.flags]
    assert len(over) >= 2, "the sample BoQ must contain clearly overpriced lines"
    assert len(under) >= 2, "the sample BoQ must contain clearly underpriced lines"
    assert result.totals["breached_line_count"] == len(over) + len(under)


def test_scope_and_market_risk_cancel_for_excluded_sections(session, sample_upload_id) -> None:
    """scope_factor = 1/ratio means the scope bar exactly offsets market risk."""
    result = _run(session, sample_upload_id, "RLB")
    piling_ids = {
        l.item_id for l in result.lines if l.is_benchmarked and l.scope_excluded
    }
    assert piling_ids
    for line in result.lines:
        if line.item_id in piling_ids:
            # The EXACT ratio, not the 6dp display value: scope_factor is 1/exact_ratio,
            # so pairing it with a rounded ratio leaves a residual proportional to
            # quantity x base_rate x 5e-7.
            exact = line.tpi_ratio_exact or line.tpi_ratio
            market = line.quantity * line.benchmark_base_rate * (exact - 1.0)
            scope = line.quantity * line.benchmark_base_rate * exact * (line.scope_factor - 1.0)
            assert market + scope == pytest.approx(0.0, abs=1e-6)


def test_every_benchmarked_line_carries_full_provenance(session, sample_upload_id) -> None:
    result = _run(session, sample_upload_id, "BCA")
    for line in result.lines:
        if not line.is_benchmarked:
            assert line.provenance is None
            continue
        p = line.provenance
        assert p is not None
        for field in (
            "source", "source_date", "base_year", "scope_inclusions",
            "scope_exclusions", "confidence",
        ):
            assert p[field] not in (None, ""), f"{field} missing on line {line.item_id}"
        # Provenance names where the rate came from. It no longer ships a placeholder flag or a
        # "replace this with real data" TODO: the library rate IS the rate for the section.
        assert "replace_with" not in p
        assert "is_placeholder" not in p


def test_tpi_up_and_down_move_variance_in_opposite_directions(session, sample_upload_id, deflationary_series) -> None:
    up = _run(session, sample_upload_id, "BCA")
    down = _run(session, sample_upload_id, deflationary_series)
    assert down.totals["should_cost_total"] < up.totals["should_cost_total"]
    assert down.totals["total_variance_abs"] > up.totals["total_variance_abs"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def test_quarter_helpers() -> None:
    assert parse_quarter("2024Q4") == (2024, 4)
    assert quarter_sort_key("2024Q4") == 2024 * 4 + 4
    assert quarter_sort_key("2024Q1") < quarter_sort_key("2024Q2")
    assert quarter_sort_key("2023Q4") < quarter_sort_key("2024Q1")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("m2", "m2"), ("SQM", "m2"), ("m3", "m3"), ("CUM", "m3"), ("nr", "item"),
     ("Sum", "item"), ("tonne", "t"), ("LM", "m")],
)
def test_unit_normalisation(raw: str, expected: str) -> None:
    assert normalise_unit(raw) == expected
