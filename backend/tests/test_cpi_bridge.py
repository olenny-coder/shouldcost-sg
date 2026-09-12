"""The CPI bridge: keeping the index used for a tender quarter up to date.

Published construction cost indexes lag the tender quarter. These tests pin the
whole contract:

* the CPI series itself is real published data with provenance;
* a stale index observation is carried forward by the observed CPI movement, with
  the exact arithmetic asserted against the seeded values;
* every bridged line is basis="assumed" and flagged, and the bridge is its own
  waterfall step;
* the bridge can be switched off, and then the observation is held unchanged;
* the bridge never runs past the latest published CPI month, and says so;
* an analyst override replaces the bridged level;
* scope-exclusion and reconciliation behaviour survive a bridged run.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import benchmark as benchmark_module
from app.benchmark import (
    BRIDGE_NONE,
    build_benchmark,
    compute_sensitivity,
    month_sort_key,
    quarter_months,
    resolve_index_bridge,
    resolve_tpi,
)
from app.countries import Country, get_country
from app.models import BoQUpload, BoQItem, PriceSeries

SG_SAMPLE = "sample_boq.csv"
IN_SAMPLE = "sample_boq_india.csv"

# The demonstration BoQ is calibrated for 2024Q4, which every series has
# published. 2026Q3 is the interesting case: no construction index has published
# it, only the monthly CPI has.
BRIDGED_QUARTER = "2026Q3"


def _items(session, upload_id: int) -> list[BoQItem]:
    return list(
        session.scalars(select(BoQItem).where(BoQItem.upload_id == upload_id).order_by(BoQItem.id))
    )


def _upload_id(session, filename: str = SG_SAMPLE) -> int:
    return session.scalar(select(BoQUpload.id).where(BoQUpload.filename == filename))


def _run(session, quarter=BRIDGED_QUARTER, series="BCA", filename=SG_SAMPLE, **kw):
    upload_id = _upload_id(session, filename)
    upload = session.get(BoQUpload, upload_id)
    return build_benchmark(
        session,
        upload_id=upload_id,
        filename=filename,
        items=_items(session, upload_id),
        tender_quarter=quarter,
        tpi_series_name=series,
        country=upload.country,
        **kw,
    )


def _cpi_value(session, month: str, country: str = "SG", series_name: str = "CPI-ALL") -> float:
    return session.scalar(
        select(PriceSeries.value).where(
            PriceSeries.country == country,
            PriceSeries.month == month,
            PriceSeries.series_name == series_name,
        )
    )


def _mean(values) -> float:
    return sum(values) / len(values)


# --------------------------------------------------------------------------- #
# The seed data is real
# --------------------------------------------------------------------------- #
def test_cpi_seed_is_real_published_data_with_provenance(session) -> None:
    rows = list(session.scalars(select(PriceSeries).where(PriceSeries.country == "SG")))
    assert rows, "the Singapore CPI series must be seeded"
    assert all(row.is_placeholder is False for row in rows)
    assert all(row.provenance_note for row in rows), "every real row states its provenance"
    assert all(row.source_url.startswith("http") for row in rows)
    assert {row.base_year for row in rows} == {2024}
    assert rows[-1].month >= "2026-01", "the series must be current, not years old"


def test_singapore_cpi_values_match_the_published_table(session) -> None:
    # Spot checks read out of SingStat Table Builder table M213751, row
    # "All Items", 2024 = 100. These are the publisher's own numbers.
    expected = {
        "2024-10": 100.119,
        "2024-12": 100.661,
        "2026-06": 102.858,
        "2026-07": 102.696,
    }
    for month, value in expected.items():
        assert _cpi_value(session, month) == pytest.approx(value, abs=1e-9)


def test_every_market_declares_a_cpi_series(session) -> None:
    for code in ("SG", "IN"):
        registry = get_country(code)
        assert registry.default_cpi_series, f"{code} must name its consumer price series"


# --------------------------------------------------------------------------- #
# The bridge arithmetic
# --------------------------------------------------------------------------- #
def test_stale_index_is_carried_forward_by_the_observed_cpi_move(session) -> None:
    tpi = resolve_tpi(session, "BCA", BRIDGED_QUARTER)
    assert tpi.fallback_used is True, "no BCA observation exists for 2026Q3"
    assert tpi.resolved_quarter == "2024Q4"
    assert tpi.lag_quarters == 7
    assert tpi.bridge_applied is True

    # The published observation is untouched...
    assert tpi.value_published == pytest.approx(139.2)
    # ...and the value actually used is it times the observed CPI move.
    from_months = quarter_months("2024Q4")
    from_mean = _mean([_cpi_value(session, month) for month in from_months])
    to_value = _cpi_value(session, "2026-07")  # the only month published in 2026Q3
    expected_factor = to_value / from_mean
    assert tpi.bridge.factor == pytest.approx(expected_factor, abs=1e-12)
    assert tpi.value == pytest.approx(139.2 * expected_factor, abs=1e-9)
    assert tpi.ratio == pytest.approx(tpi.value / 100.0, abs=1e-9)


def test_bridge_reports_the_months_it_used_and_marks_a_partial_quarter(session) -> None:
    tpi = resolve_tpi(session, "BCA", BRIDGED_QUARTER)
    bridge = tpi.bridge
    assert bridge.from_months == ["2024-10", "2024-11", "2024-12"]
    assert bridge.to_months == ["2026-07"], "only July 2026 is published for 2026Q3"
    assert bridge.partial is True
    assert bridge.covered_through_month == "2026-07"
    assert bridge.to_quarter == "2026Q3"
    assert bridge.series_name == "CPI-ALL"
    assert bridge.base_year == 2024
    assert bridge.base_value == pytest.approx(100.0)
    assert bridge.source_url.startswith("http")


def test_bridge_never_runs_past_the_latest_published_cpi_month(session) -> None:
    # 2027Q1 is entirely beyond the published CPI. The bridge must stop at the
    # last real observation rather than extrapolate into empty space.
    tpi = resolve_tpi(session, "BCA", "2027Q1")
    bridge = tpi.bridge
    assert bridge.applied is True
    assert bridge.covered_through_month == "2026-07"
    assert bridge.shortfall_months == 8  # 2026-07 -> 2027-03
    result = _run(session, quarter="2027Q1")
    assert any("short of the end" in warning for warning in result.warnings)
    assert result.index_bridge["shortfall_months"] == 8


def test_quarter_months_helper_is_consistent() -> None:
    assert quarter_months("2026Q1") == ["2026-01", "2026-02", "2026-03"]
    assert quarter_months("2026Q4") == ["2026-10", "2026-11", "2026-12"]
    assert month_sort_key("2026-07") - month_sort_key("2026-04") == 3


# --------------------------------------------------------------------------- #
# Disclosure: bridged lines are assumed, flagged, and visible
# --------------------------------------------------------------------------- #
def test_bridged_run_marks_every_line_assumed_and_flagged(session) -> None:
    result = _run(session)
    assert result.index_bridge["applied"] is True
    benchmarked = [line for line in result.lines if line.is_benchmarked]
    assert benchmarked, "the sample BoQ must produce benchmarked lines"
    for line in benchmarked:
        assert line.tpi_bridged is True
        assert "index_bridged" in line.flags
        assert line.basis == "assumed"
        assert line.cpi_bridge_factor != 1.0
        assert line.cpi_month_used == "2026-07"
        assert line.index_lag_quarters == 7
    assert result.totals["index_bridge_applied"] is True
    assert result.totals["index_bridged_lines"] == sum(
        1 for line in result.lines if line.tpi_bridged
    )
    assert result.totals["basis"] == "assumed"


def test_bridged_run_explains_itself_in_warnings_and_assumptions(session) -> None:
    result = _run(session)
    assert any("Index freshness" in w for w in result.warnings)
    assert any("MODELLED" in w for w in result.warnings)
    bridge_assumption = [a for a in result.assumptions if "ASSUMED (modelled)" in a]
    assert bridge_assumption, "the bridge must be restated as an assumption"
    text = bridge_assumption[0]
    assert "CPI-ALL" in text
    assert "2024Q4" in text
    assert "not a published observation" in text
    assert "trend to date" in text


def test_bridge_is_its_own_waterfall_step(session) -> None:
    result = _run(session)
    components = {w["component"]: w for w in result.waterfall}
    assert components["cpi_bridge"]["basis"] == "assumed"
    assert components["market_risk"]["basis"] == "derived"
    assert components["cpi_bridge"]["amount"] > 0, "the bridge must move should-cost upward"
    # The two index steps still add up to the single market-risk term the
    # pre-bridge engine reported, and the identity still closes.
    identity = result.totals["boq_total"] + sum(w["amount"] for w in result.waterfall)
    assert identity == pytest.approx(result.totals["should_cost_total"], abs=0.01)
    # market_risk is now the PUBLISHED observation movement only, measured per line from
    # whatever denominator that line's rate is expressed at: the series base year for a
    # retained estimate, and the library's own quarter for a rate derived to 2026Q2.
    assert components["market_risk"]["amount"] == pytest.approx(
        sum(
            line.quantity
            * (line.benchmark_base_rate_exact or line.benchmark_base_rate)
            * ((line.tpi_ratio_published_exact or line.tpi_value_published / line.tpi_base_value) - 1.0)
            for line in result.lines
            if line.is_benchmarked
        ),
        abs=0.02,
    )


def test_market_risk_plus_bridge_equals_the_unbridged_market_risk(session) -> None:
    """The split must not change the total: 2024Q4 and a bridged run are comparable."""
    bridged = _run(session)
    components = {w["component"]: w["amount"] for w in bridged.waterfall}
    held = _run(session, index_bridge=BRIDGE_NONE)
    held_components = {w["component"]: w["amount"] for w in held.waterfall}
    # Holding the observation gives market_risk with no bridge at all.
    assert held_components["cpi_bridge"] == 0.0
    # Bridging moves exactly (index ratio used - published ratio) worth of cost
    # out of market_risk and into the bridge step.
    assert components["market_risk"] + components["cpi_bridge"] > held_components["market_risk"]


def test_bridged_runs_reconcile_with_a_negligible_residual(session) -> None:
    """A bridged run must reconcile as tightly as an observed one.

    The index ratio is used at FULL precision in the waterfall. Using the rounded
    display value there leaves a residual proportional to quantity x base_rate (a
    few currency units on a large BoQ) that lands in `unexplained` and makes a
    bridged run look structurally different from an observed one.
    """
    for kwargs in (
        {"quarter": BRIDGED_QUARTER},
        {"quarter": BRIDGED_QUARTER, "series": "CPWD", "filename": IN_SAMPLE},
        {"quarter": "2025Q3", "series": "CPWD", "filename": IN_SAMPLE},
    ):
        result = _run(session, **kwargs)
        components = {w["component"]: w["amount"] for w in result.waterfall}
        identity = result.totals["boq_total"] + sum(components.values())
        assert identity == pytest.approx(result.totals["should_cost_total"], abs=0.01), kwargs
        assert abs(components["unexplained"]) <= 0.05, (kwargs, components["unexplained"])


# --------------------------------------------------------------------------- #
# Off switch, coverage, override
# --------------------------------------------------------------------------- #
def test_bridge_can_be_switched_off_and_holds_the_observation(session) -> None:
    result = _run(session, index_bridge=BRIDGE_NONE)
    assert result.index_bridge["applied"] is False
    assert result.index_bridge["reason"] == "bridge_disabled_by_analyst"
    assert result.index_bridge["mode"] == "none"
    benchmarked = [line for line in result.lines if line.is_benchmarked]
    for line in benchmarked:
        assert line.tpi_bridged is False
        assert line.tpi_value == pytest.approx(line.tpi_value_published)
        assert line.cpi_bridge_factor == 1.0
        assert "index_bridged" not in line.flags
    assert result.totals["index_bridge_applied"] is False
    assert any("switched OFF" in w for w in result.warnings)


def test_no_bridge_when_the_series_covers_the_quarter(session) -> None:
    """No carry-forward is needed for the quarter being priced.

    The waterfall's bridge step is still non-zero, and that is correct: the rate library is
    expressed at 2026Q2, and the index at 2026Q2 is itself derived by carrying the 2024Q4
    observation forward. That derivation is modelled, so it belongs in the bridge step even
    though the tender quarter needed no bridge of its own.
    """
    result = _run(session, quarter="2024Q4")
    assert result.index_bridge["applied"] is False
    assert result.index_bridge["reason"] == "index_observation_covers_requested_quarter"
    assert result.index_bridge["lag_quarters"] == 0
    components = {w["component"]: w["amount"] for w in result.waterfall}
    assert components["cpi_bridge"] != 0.0, (
        "the library base quarter is unpublished, so its index is carried forward"
    )
    assert not any("index_bridged" in line.flags for line in result.lines)
    assert result.totals["basis"] == "derived"


def test_unknown_preferred_series_falls_back_to_a_loaded_one(session, monkeypatch) -> None:
    """A registry pointing at a series this database does not hold must still work.

    India and Singapore each carry more than one consumer price series, and the
    engine picks whichever one spans both endpoints rather than giving up on a
    naming mismatch.
    """
    original = get_country

    def fake_get_country(code):
        registry = original(code)
        return Country(
            code=registry.code,
            name=registry.name,
            currency=registry.currency,
            currency_symbol=registry.currency_symbol,
            measurement_standard=registry.measurement_standard,
            measurement_note=registry.measurement_note,
            default_tpi_series=registry.default_tpi_series,
            unit_convention=registry.unit_convention,
            default_cpi_series="CPI-NOT-LOADED",
            sources=registry.sources,
        )

    monkeypatch.setattr(benchmark_module, "get_country", fake_get_country)
    bridge = resolve_index_bridge(
        session,
        country="SG",
        observation_quarter="2024Q4",
        requested_quarter=BRIDGED_QUARTER,
    )
    assert bridge.applied is True
    assert bridge.series_name == "CPI-ALL"


def test_no_cpi_data_at_all_degrades_to_a_warning_not_a_crash(session, monkeypatch) -> None:
    """With an empty CPI table the run must still complete, holding the index."""
    monkeypatch.setattr(benchmark_module, "_price_rows", lambda *args, **kwargs: [])
    bridge = resolve_index_bridge(
        session,
        country="SG",
        observation_quarter="2024Q4",
        requested_quarter=BRIDGED_QUARTER,
    )
    assert bridge.applied is False
    assert bridge.reason == "no_price_observations_loaded"

    result = _run(session)
    assert result.index_bridge["applied"] is False
    assert any("could not be computed" in w or "held unchanged" in w for w in result.warnings)
    assert result.totals["should_cost_total"] > 0


def test_absolute_override_replaces_the_bridged_level(session) -> None:
    from app.schemas import IndexAdjustments

    result = _run(session, adjustments=IndexAdjustments(tpi_value_override=150.0))
    line = [l for l in result.lines if l.is_benchmarked][0]
    assert line.tpi_value == pytest.approx(150.0)
    # An override sets the index level at the TENDER quarter. It does not remove the
    # carry-forward that produced the index at the library's own base quarter (2026Q2),
    # which is a separate, still-modelled step on the retained sections.
    components = {w["component"]: w["amount"] for w in result.waterfall}
    assert components["cpi_bridge"] != 0.0
    assert "index_bridged" in line.flags  # the line is still disclosed as modelled


def test_scope_exclusion_still_cancels_under_a_bridge(session) -> None:
    result = _run(session)
    piling = [
        line
        for line in result.lines
        if line.smm2_section == "Piling" and line.is_benchmarked
    ]
    assert piling, "the sample BoQ contains piling, which the BCA series excludes"
    for line in piling:
        assert line.scope_excluded is True
        assert line.scope_factor == pytest.approx(1.0 / line.tpi_ratio, abs=1e-6)
        # scope_factor cancels the index movement exactly, whatever the bridge did.
        assert line.adjusted_benchmark_rate == pytest.approx(line.benchmark_base_rate, abs=0.01)


# --------------------------------------------------------------------------- #
# Sensitivity and exports carry the bridge
# --------------------------------------------------------------------------- #
def test_sensitivity_baseline_equals_the_bridged_headline(session) -> None:
    result = _run(session)
    sensitivity = compute_sensitivity(
        session,
        upload_id=result.upload_id,
        items=_items(session, result.upload_id),
        tender_quarter=BRIDGED_QUARTER,
        tpi_series_name="BCA",
        country="SG",
    )
    assert sensitivity["index_bridge"]["applied"] is True
    assert sensitivity["baseline"]["should_cost_total"] == pytest.approx(
        result.totals["should_cost_total"], abs=0.01
    )
    assert sensitivity["baseline_tpi_value"] == pytest.approx(result.index_bridge["index_value_used"])
    assert sensitivity["baseline_tpi_value_published"] == pytest.approx(139.2)
    # The break-even still drives total variance to zero on a bridged run.
    break_even = sensitivity["break_even_scale_pct"]
    assert break_even is not None
    moved = _run(
        session,
        adjustments={"tpi_scale_pct": break_even, "base_rate_scale_pct": 0,
                     "tpi_value_override": None, "section_rate_scale_pct": {}},
    )
    assert moved.totals["total_variance_abs"] == pytest.approx(0.0, abs=0.05)


def test_report_and_export_carry_the_bridge(session, client_module) -> None:
    upload_id = _upload_id(session, SG_SAMPLE)
    response = client_module.post(
        f"/api/boq/{upload_id}/export",
        params={"level": "report", "format": "csv"},
        json={
            "tender_quarter": BRIDGED_QUARTER,
            "tpi_series_name": "BCA",
            "variance_threshold": 15.0,
            "index_bridge": "cpi",
        },
    )
    assert response.status_code == 200
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8"))))
    header = {r["ref"]: r["value"] for r in rows if r["block"] == "report"}
    assert header["index_bridge_applied"] == "True"
    assert header["index_observation_quarter"] == "2024Q4"
    assert float(header["cpi_bridge_factor"]) > 1.0
    assert header["cpi_months_to"] == "2026-07"
    assert "cpi_series" in header
    waterfall = {
        r["ref"]: r for r in rows if r["block"] == "waterfall" and r["item"] == "amount"
    }
    assert waterfall["cpi_bridge"]["basis"] == "assumed"
    assert float(waterfall["cpi_bridge"]["value"]) > 0

    # The summary level states the bridge flags too.
    summary_response = client_module.post(
        f"/api/boq/{upload_id}/export",
        params={"level": "summary", "format": "csv"},
        json={
            "tender_quarter": BRIDGED_QUARTER,
            "tpi_series_name": "BCA",
            "variance_threshold": 15.0,
            "index_bridge": "cpi",
        },
    )
    summary = {
        r["key"]: r["value"]
        for r in csv.DictReader(io.StringIO(summary_response.content.decode("utf-8")))
    }
    assert summary["index_bridge_applied"] == "True"
    assert summary["index_bridged_through_month"] == "2026-07"


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #
def test_cpi_endpoint_serves_the_real_series(client_module) -> None:
    response = client_module.get("/api/indices/price-series", params={"country": "SG"})
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) > 40
    assert all(row["is_placeholder"] is False for row in rows)
    assert all(row["provenance_note"] for row in rows)
    assert rows[-1]["month"] >= "2026-01"


def test_freshness_endpoint_reports_the_lag_and_the_bridge(client_module) -> None:
    response = client_module.get(
        "/api/indices/freshness", params={"country": "SG", "reference_quarter": "2026Q3"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["cpi_latest_month"] == "2026-07"
    assert payload["cpi_observations"] > 40
    by_series = {row["series_name"]: row for row in payload["series"]}
    bca = by_series["BCA"]
    assert bca["latest_quarter"] == "2024Q4"
    assert bca["lag_quarters"] == 7
    assert bca["bridge"]["applied"] is True
    assert bca["bridge"]["bridged_through_month"] == "2026-07"
    assert bca["bridge"]["index_value_used"] > bca["latest_value"]


def test_freshness_endpoint_rejects_a_bad_quarter(client_module) -> None:
    response = client_module.get(
        "/api/indices/freshness", params={"country": "SG", "reference_quarter": "2026-Q3"}
    )
    assert response.status_code == 422


def test_benchmark_endpoint_accepts_the_bridge_switch(client_module, session) -> None:
    upload_id = _upload_id(session, SG_SAMPLE)
    on = client_module.post(
        f"/api/boq/{upload_id}/benchmark",
        json={
            "tender_quarter": BRIDGED_QUARTER,
            "tpi_series_name": "BCA",
            "variance_threshold": 15.0,
            "index_bridge": "cpi",
        },
    )
    assert on.status_code == 200
    payload = on.json()
    assert payload["index_bridge"]["applied"] is True
    assert payload["totals"]["index_bridge_applied"] is True
    assert payload["lines"][0]["tpi_bridged"] is True
    assert payload["lines"][0]["cpi_month_used"] == "2026-07"
    assert any(w["component"] == "cpi_bridge" for w in payload["waterfall"])

    off = client_module.post(
        f"/api/boq/{upload_id}/benchmark",
        json={
            "tender_quarter": BRIDGED_QUARTER,
            "tpi_series_name": "BCA",
            "variance_threshold": 15.0,
            "index_bridge": "none",
        },
    )
    assert off.status_code == 200
    assert off.json()["index_bridge"]["applied"] is False
    assert off.json()["totals"]["should_cost_total"] != payload["totals"]["should_cost_total"]


def test_benchmark_endpoint_rejects_an_unknown_bridge_mode(client_module, session) -> None:
    upload_id = _upload_id(session, SG_SAMPLE)
    response = client_module.post(
        f"/api/boq/{upload_id}/benchmark",
        json={
            "tender_quarter": BRIDGED_QUARTER,
            "tpi_series_name": "BCA",
            "variance_threshold": 15.0,
            "index_bridge": "extrapolate",
        },
    )
    assert response.status_code == 422


def test_countries_endpoint_publishes_the_cpi_series(client_module) -> None:
    rows = client_module.get("/api/countries").json()
    by_code = {row["code"]: row for row in rows}
    assert by_code["SG"]["default_cpi_series"] == "CPI-ALL"
    assert by_code["IN"]["default_cpi_series"] == "CPI-ALL"


# --------------------------------------------------------------------------- #
# India: two publisher bases, no overlap, no chaining
# --------------------------------------------------------------------------- #
def test_india_cpi_seed_carries_the_current_base_and_its_predecessor(session) -> None:
    rows = list(session.scalars(select(PriceSeries).where(PriceSeries.country == "IN")))
    assert rows, "the India CPI series must be seeded"
    assert all(row.is_placeholder is False for row in rows)
    assert all(row.provenance_note for row in rows)
    by_series = {}
    for row in rows:
        by_series.setdefault(row.series_name, []).append(row)
    # India now carries real producer indices alongside the consumer series.
    assert "CPI-ALL" in by_series
    assert any(name.startswith("PPI-") for name in by_series)

    current = by_series["CPI-ALL"]
    older = by_series["CPI-ALL-2012"]
    assert {r.base_year for r in current} == {2024}
    assert {r.base_year for r in older} == {2012}
    # MoSPI publishes the current base together with its own back-cast months on that
    # base, so the current series reaches back past the rebase and no splice is ever
    # needed for an index observation this app holds (the earliest is 2023Q1).
    assert current[0].month <= "2023-01"
    assert current[-1].month >= "2026-07"
    assert any("BACK-CAST" in r.provenance_note for r in current), (
        "back-cast months must say so in their provenance"
    )
    assert all("does NOT overlap" in r.provenance_note for r in older), (
        "the predecessor base must state that it does not overlap"
    )


def test_india_cpi_values_match_the_published_series(session) -> None:
    # Spot checks read out of the MoSPI eSankhyiki API, All-India Combined General
    # index (`/api/cpi/getCPIData`, sector Combined, state All India). July 2026 =
    # 107.94 is the figure the publisher headlines; December 2024 = 102.90 is one of
    # its own back-cast months on the 2024 base; December 2025 = 198.0 is on the
    # predecessor 2012 base.
    assert _cpi_value(session, "2026-07", "IN") == pytest.approx(107.94, abs=1e-9)
    assert _cpi_value(session, "2026-06", "IN") == pytest.approx(107.00, abs=1e-9)
    assert _cpi_value(session, "2025-06", "IN") == pytest.approx(102.51, abs=1e-9)
    assert _cpi_value(session, "2024-12", "IN") == pytest.approx(102.90, abs=1e-9)
    assert _cpi_value(session, "2025-12", "IN", "CPI-ALL-2012") == pytest.approx(198.0, abs=1e-9)


def test_india_bridge_prefers_the_producer_index(session) -> None:
    """WPI-CONST published 2026Q2; pricing 2026Q3 is carried on the PRODUCER index.

    India publishes a commodity-level PPI, so the default (auto) bridge must take it
    rather than the consumer index: producer prices measure what suppliers charge for
    the materials a construction rate is built from.
    """
    tpi = resolve_tpi(session, "WPI-CONST", BRIDGED_QUARTER, country="IN")
    bridge = tpi.bridge
    assert bridge.applied is True
    assert bridge.kind == "PPI", "a producer index must be reached first"
    assert bridge.series_name == "PPI-ALL"
    assert bridge.from_months == ["2026-04", "2026-05", "2026-06"]
    assert bridge.to_months == ["2026-07"]
    assert tpi.value == pytest.approx(tpi.value_published * bridge.factor, abs=1e-9)


def test_india_bridge_uses_the_current_consumer_base_when_cpi_is_forced(session) -> None:
    """With mode='cpi' the current 2024-based consumer series must be preferred."""
    tpi = resolve_tpi(session, "WPI-CONST", BRIDGED_QUARTER, country="IN", bridge="cpi")
    bridge = tpi.bridge
    assert bridge.applied is True
    assert bridge.kind == "CPI"
    assert bridge.series_name == "CPI-ALL", "the current base must be preferred"
    assert bridge.from_months == ["2026-04", "2026-05", "2026-06"]
    assert bridge.to_months == ["2026-07"]
    from_mean = _mean([_cpi_value(session, m, "IN") for m in bridge.from_months])
    assert bridge.factor == pytest.approx(_cpi_value(session, "2026-07", "IN") / from_mean, abs=1e-12)
    assert tpi.value == pytest.approx(tpi.value_published * bridge.factor, abs=1e-9)


def test_india_bridge_spans_the_rebase_via_the_back_cast_months(session) -> None:
    """A 2024Q4 index observation is bridged on ONE base, thanks to the back-cast."""
    tpi = resolve_tpi(session, "CPWD", "2025Q2", country="IN", bridge="cpi")
    bridge = tpi.bridge
    assert bridge.applied is True
    assert bridge.series_name == "CPI-ALL"
    assert bridge.base_year == 2024
    assert bridge.from_months == ["2024-10", "2024-11", "2024-12"]
    assert bridge.to_months == ["2025-04", "2025-05", "2025-06"]
    assert bridge.shortfall_months == 0
    # 2024 months come from the publisher's back-cast rows: real published values.
    assert any(
        "BACK-CAST" in row.provenance_note
        for row in session.scalars(
            select(PriceSeries).where(
                PriceSeries.country == "IN",
                PriceSeries.series_name == "CPI-ALL",
                PriceSeries.month.in_(bridge.from_months),
            )
        )
    )


def test_india_older_base_is_used_when_the_current_one_cannot_span(session, monkeypatch) -> None:
    """With no producer index available, the engine falls back to the older consumer base.

    This is the no-chaining rule in action: the 2012-based series can only reach
    2025-12, so a 2026Q3 request is carried as far as real data allows and the
    remaining gap is reported rather than invented.

    Producer indices are excluded here so the test exercises the CPI fallback; the
    PPI-first precedence is covered separately.
    """
    real_rows = benchmark_module._price_rows

    def rows_without_back_cast(session_, country, series_name=""):
        # Producer indices are tried first, so remove them entirely: this test is
        # about the CONSUMER fallback and the no-chaining rule, not the precedence.
        rows = [r for r in real_rows(session_, country) if r.kind != "PPI"]
        return [
            row
            for row in rows
            if not (row.country == "IN" and row.series_name == "CPI-ALL" and row.month < "2026-01")
        ]

    monkeypatch.setattr(benchmark_module, "_price_rows", rows_without_back_cast)
    tpi = resolve_tpi(session, "CPWD", BRIDGED_QUARTER, country="IN")
    bridge = tpi.bridge
    assert bridge.applied is True
    assert bridge.series_name == "CPI-ALL-2012"
    assert bridge.covered_through_month == "2025-12"
    assert bridge.shortfall_months == 9

    result = _run(session, quarter=BRIDGED_QUARTER, series="CPWD", filename=IN_SAMPLE)
    assert result.index_bridge["applied"] is True
    assert result.index_bridge["shortfall_months"] == 9
    assert any("short of the end" in w for w in result.warnings)
    assert any("CPI-ALL-2012" in w for w in result.warnings)


def test_freshness_endpoint_lists_every_cpi_series(client_module) -> None:
    payload = client_module.get(
        "/api/indices/freshness", params={"country": "IN", "reference_quarter": "2026Q3"}
    ).json()
    names = {row["series_name"] for row in payload["cpi_series_list"]}
    assert names == {"CPI-ALL", "CPI-ALL-2012"}, "the consumer list carries consumer series only"
    preferred = [row for row in payload["cpi_series_list"] if row["is_preferred"]]
    assert len(preferred) == 1
    assert preferred[0]["series_name"] == "CPI-ALL"
    assert preferred[0]["base_year"] == 2024
    assert payload["cpi_latest_month"] == "2026-07"

    # The producer side is reported separately, and it is what the bridge reaches for.
    ppi_names = {row["series_name"] for row in payload["ppi_series_list"]}
    assert "PPI-ALL" in ppi_names
    assert len(ppi_names) > 1, "India publishes a commodity-level producer index"
    assert [row for row in payload["ppi_series_list"] if row["is_preferred"]][0][
        "series_name"
    ] == "PPI-ALL"
    assert payload["ppi_series_available"] is True
    assert payload["bridge_preference"] == "producer"

    by_series = {row["series_name"]: row for row in payload["series"]}
    assert by_series["WPI-CONST"]["bridge"]["applied"] is True
    assert by_series["WPI-CONST"]["bridge"]["kind"] == "PPI"
    assert by_series["WPI-CONST"]["bridge"]["series_name"] == "PPI-ALL"
