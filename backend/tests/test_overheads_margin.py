"""Overheads and margin: building a FULL should-cost from the benchmark cost.

A rate library prices work. It does not price the contractor's site and head-office
overheads, or their profit. These inputs let the analyst add both, and the contract
pinned here is:

    full_rate = adjusted_benchmark_rate x (1 + overhead_pct/100) x (1 + margin_pct/100)

* margin compounds on overheads (the usual commercial convention), and that is
  asserted numerically rather than described;
* the full cost is additive: benchmark cost + overheads + margin, with the waterfall
  closing exactly on the full total;
* overheads and margin are ASSUMED, never derived: every benchmarked line is
  basis="assumed" and flagged, and the percentages are restated in assumptions[];
* they are NOT applied to lines held at the tendered rate, because that rate already
  carries the contractor's own OH&P;
* the variance comparison basis is explicit: full-to-full by default, or against the
  benchmark rate before overheads when the analyst says the tender excludes them;
* with no percentages supplied, nothing changes at all - the full total equals the
  benchmark total and both waterfall steps are exactly zero.
"""

from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import select

from app.benchmark import build_benchmark, compute_sensitivity
from app.models import BoQUpload, BoQItem
from app.schemas import IndexAdjustments

SG_SAMPLE = "sample_boq.csv"
IN_SAMPLE = "sample_boq_india.csv"


def _items(session, upload_id: int) -> list[BoQItem]:
    return list(
        session.scalars(select(BoQItem).where(BoQItem.upload_id == upload_id).order_by(BoQItem.id))
    )


def _upload_id(session, filename: str = SG_SAMPLE) -> int:
    return session.scalar(select(BoQUpload.id).where(BoQUpload.filename == filename))


def _run(session, *, filename=SG_SAMPLE, quarter="2024Q4", series="BCA", country="SG", **kwargs):
    upload_id = _upload_id(session, filename)
    return build_benchmark(
        session,
        upload_id=upload_id,
        filename=filename,
        items=_items(session, upload_id),
        tender_quarter=quarter,
        tpi_series_name=series,
        country=country,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Off by default: nothing changes
# --------------------------------------------------------------------------- #
def test_no_percentages_leaves_the_run_untouched(session) -> None:
    baseline = _run(session)
    explicit_zero = _run(session, adjustments=IndexAdjustments(overhead_pct=0, margin_pct=0))
    assert baseline.totals["full_should_cost_total"] == baseline.totals["should_cost_total"]
    assert baseline.totals["overhead_amount_total"] == 0.0
    assert baseline.totals["margin_amount_total"] == 0.0
    assert baseline.totals["full_variance_abs"] == baseline.totals["total_variance_abs"]

    components = {c["component"]: c["amount"] for c in baseline.waterfall}
    assert components["overhead"] == 0.0
    assert components["margin"] == 0.0
    # Identical to a run that explicitly passes zeroes.
    assert explicit_zero.totals == baseline.totals
    assert not any("overhead_applied" in l.flags for l in baseline.lines)


# --------------------------------------------------------------------------- #
# The arithmetic
# --------------------------------------------------------------------------- #
def test_full_rate_compounds_margin_on_overheads(session) -> None:
    adjustments = IndexAdjustments(overhead_pct=12.0, margin_pct=6.0)
    result = _run(session, adjustments=adjustments)
    line = [l for l in result.lines if l.is_benchmarked][0]

    expected_full = line.adjusted_benchmark_rate * 1.12 * 1.06
    assert line.full_adjusted_benchmark_rate == pytest.approx(expected_full, abs=0.01)
    # Compounded, not added: 1.12 x 1.06 = 1.1872, whereas 12 + 6 would give 1.18.
    additive = line.adjusted_benchmark_rate * 1.18
    assert line.full_adjusted_benchmark_rate > additive

    # Amounts: overhead on the benchmark cost, margin on (benchmark + overhead).
    benchmark_amount = line.quantity * line.adjusted_benchmark_rate
    assert line.overhead_amount == pytest.approx(benchmark_amount * 0.12, abs=0.02)
    assert line.margin_amount == pytest.approx((benchmark_amount + line.overhead_amount) * 0.06, abs=0.02)
    assert line.full_should_cost_amount == pytest.approx(
        benchmark_amount + line.overhead_amount + line.margin_amount, abs=0.02
    )


def test_totals_add_benchmark_cost_overheads_and_margin(session) -> None:
    result = _run(session, adjustments=IndexAdjustments(overhead_pct=12.0, margin_pct=6.0))
    totals = result.totals
    # Per-line amounts are rounded, so the totals agree to a cent plus that rounding.
    assert totals["full_should_cost_total"] == pytest.approx(
        totals["should_cost_total"]
        + totals["overhead_amount_total"]
        + totals["margin_amount_total"],
        abs=0.05,
    )
    assert totals["overhead_amount_total"] > 0
    assert totals["margin_amount_total"] > 0
    assert totals["full_should_cost_total"] > totals["should_cost_total"]


def test_waterfall_closes_on_the_full_total(session) -> None:
    result = _run(session, adjustments=IndexAdjustments(overhead_pct=12.0, margin_pct=6.0))
    components = {c["component"]: c for c in result.waterfall}
    assert components["overhead"]["basis"] == "assumed"
    assert components["margin"]["basis"] == "assumed"
    assert components["overhead"]["amount"] > 0
    assert components["margin"]["amount"] > 0
    identity = result.totals["boq_total"] + sum(c["amount"] for c in result.waterfall)
    assert identity == pytest.approx(result.totals["full_should_cost_total"], abs=0.01)
    assert abs(components["unexplained"]["amount"]) <= 0.05


def test_section_aggregates_carry_the_full_cost(session) -> None:
    result = _run(session, adjustments=IndexAdjustments(overhead_pct=12.0, margin_pct=6.0))
    for section in result.sections:
        assert section["full_should_cost_amount"] >= section["should_cost_amount"]
        assert section["full_should_cost_amount"] == pytest.approx(
            section["should_cost_amount"]
            + section["overhead_amount"]
            + section["margin_amount"],
            abs=0.05,
        )
    total_full = sum(s["full_should_cost_amount"] for s in result.sections)
    assert total_full == pytest.approx(result.totals["full_should_cost_total"], abs=0.05)


# --------------------------------------------------------------------------- #
# Disclosure
# --------------------------------------------------------------------------- #
def test_overheads_and_margin_are_assumed_and_flagged(session) -> None:
    result = _run(session, adjustments=IndexAdjustments(overhead_pct=12.0, margin_pct=6.0))
    for line in [l for l in result.lines if l.is_benchmarked]:
        assert line.basis == "assumed"
        assert "overhead_applied" in line.flags
        assert "margin_applied" in line.flags
        assert line.overhead_pct == 12.0
        assert line.margin_pct == 6.0
    assert result.totals["basis"] == "assumed"
    assert result.adjustments_applied["is_noop"] is False


def test_assumptions_state_the_percentages_and_the_compounding(session) -> None:
    result = _run(session, adjustments=IndexAdjustments(overhead_pct=12.0, margin_pct=6.0))
    text = " ".join(result.assumptions)
    assert "12.00% overheads" in text
    assert "6.00% margin" in text
    assert "AFTER overheads" in text  # compounding is stated, not implied
    assert "BENCHMARKED lines only" in text
    assert "ALREADY INCLUDE overheads and profit" in text
    # And the full figure itself is in the text.
    assert f"{result.totals['full_should_cost_total']:,.2f}" in text


def test_only_one_percentage_still_works(session) -> None:
    overhead_only = _run(session, adjustments=IndexAdjustments(overhead_pct=10.0))
    line = [l for l in overhead_only.lines if l.is_benchmarked][0]
    assert "overhead_applied" in line.flags
    assert "margin_applied" not in line.flags
    assert line.margin_amount == 0.0
    assert line.full_adjusted_benchmark_rate == pytest.approx(
        line.adjusted_benchmark_rate * 1.10, abs=0.01
    )

    margin_only = _run(session, adjustments=IndexAdjustments(margin_pct=10.0))
    line = [l for l in margin_only.lines if l.is_benchmarked][0]
    assert "margin_applied" in line.flags
    assert "overhead_applied" not in line.flags
    assert line.overhead_amount == 0.0
    assert line.full_adjusted_benchmark_rate == pytest.approx(
        line.adjusted_benchmark_rate * 1.10, abs=0.01
    )


# --------------------------------------------------------------------------- #
# Variance comparison basis
# --------------------------------------------------------------------------- #
def test_default_comparison_is_full_to_full(session) -> None:
    result = _run(session, adjustments=IndexAdjustments(overhead_pct=12.0, margin_pct=6.0))
    line = [l for l in result.lines if l.is_benchmarked][0]
    assert line.compared_against_full is True
    expected = (line.boq_rate - line.full_adjusted_benchmark_rate) / line.full_adjusted_benchmark_rate * 100
    assert line.variance_pct == pytest.approx(expected, abs=0.01)
    # The headline variance follows the same basis as the lines.
    assert result.totals["variance_basis"] == "full_including_overheads"
    assert result.totals["total_variance_abs"] == pytest.approx(
        result.totals["full_variance_abs"], abs=0.01
    )


def test_tender_excluding_ohp_keeps_the_variance_on_the_benchmark_rate(session) -> None:
    includes = _run(session, adjustments=IndexAdjustments(overhead_pct=12.0, margin_pct=6.0))
    excludes = _run(
        session,
        adjustments=IndexAdjustments(
            overhead_pct=12.0, margin_pct=6.0, overheads_in_tender=False
        ),
    )
    line_includes = [l for l in includes.lines if l.is_benchmarked][0]
    line_excludes = [l for l in excludes.lines if l.is_benchmarked][0]
    assert line_excludes.compared_against_full is False
    assert line_excludes.variance_pct == pytest.approx(
        (line_excludes.boq_rate - line_excludes.adjusted_benchmark_rate)
        / line_excludes.adjusted_benchmark_rate * 100,
        abs=0.01,
    )
    # Same full cost either way; only the comparison basis moves.
    assert excludes.totals["full_should_cost_total"] == includes.totals["full_should_cost_total"]
    assert excludes.totals["total_variance_pct"] != includes.totals["total_variance_pct"]
    assert excludes.totals["variance_basis"] == "benchmark_before_overheads"
    assert any("EXCLUDED from the variance test" in w for w in excludes.warnings)

    # Without overheads, the two bases are identical.
    plain = _run(session)
    assert plain.totals["variance_basis"] == "benchmark_before_overheads"
    assert plain.totals["full_variance_pct"] == plain.totals["total_variance_pct"]


# --------------------------------------------------------------------------- #
# Unbenchmarked lines are not grossed up
# --------------------------------------------------------------------------- #
def test_unbenchmarked_lines_are_not_grossed_up(session) -> None:
    result = _run(session, adjustments=IndexAdjustments(overhead_pct=12.0, margin_pct=6.0))
    unbenchmarked = [l for l in result.lines if not l.is_benchmarked]
    assert unbenchmarked, "the sample BoQ has lines the library cannot price"
    for line in unbenchmarked:
        assert line.overhead_amount == 0.0
        assert line.margin_amount == 0.0
        # Held at the tendered rate, so the full amount equals the tendered amount.
        assert line.full_should_cost_amount == line.boq_amount
        assert line.should_cost_amount == line.boq_amount
    benchmarked = [l for l in result.lines if l.is_benchmarked]
    assert sum(l.overhead_amount for l in benchmarked) == pytest.approx(
        result.totals["overhead_amount_total"], abs=0.02
    )


# --------------------------------------------------------------------------- #
# Sensitivity, exports, validation
# --------------------------------------------------------------------------- #
def test_sensitivity_carries_the_full_cost(session) -> None:
    upload_id = _upload_id(session)
    adjustments = IndexAdjustments(overhead_pct=12.0, margin_pct=6.0)
    sensitivity = compute_sensitivity(
        session,
        upload_id=upload_id,
        items=_items(session, upload_id),
        tender_quarter="2024Q4",
        tpi_series_name="BCA",
        country="SG",
        adjustments=adjustments,
    )
    headline = _run(session, adjustments=adjustments)
    # The baseline still equals the headline benchmark to the cent...
    assert sensitivity["baseline"]["should_cost_total"] == pytest.approx(
        headline.totals["should_cost_total"], abs=0.01
    )
    # ...and every sweep point reports the full cost on the same assumptions.
    assert sensitivity["baseline"]["full_should_cost_total"] == pytest.approx(
        headline.totals["full_should_cost_total"], abs=0.01
    )
    assert sensitivity["baseline"]["full_should_cost_total"] > sensitivity["baseline"]["should_cost_total"]


def test_report_and_summary_exports_carry_the_full_cost(session, client_module) -> None:
    upload_id = _upload_id(session)
    body = {
        "tender_quarter": "2024Q4",
        "tpi_series_name": "BCA",
        "variance_threshold": 15.0,
        "adjustments": {"overhead_pct": 12.0, "margin_pct": 6.0, "overheads_in_tender": True},
    }
    report = client_module.post(
        f"/api/boq/{upload_id}/export", params={"level": "report", "format": "csv"}, json=body
    )
    assert report.status_code == 200
    rows = list(csv.DictReader(io.StringIO(report.content.decode("utf-8"))))
    header = {r["ref"]: r["value"] for r in rows if r["block"] == "report"}
    assert header["overhead_pct"] == "12.0"
    assert header["margin_pct"] == "6.0"
    assert header["overheads_in_tender"] == "True"
    assert float(header["full_should_cost_total"]) > 0
    assert "adjusted_benchmark_rate" in header["full_should_cost_formula"]
    assert "overhead_pct" in header["full_should_cost_formula"]

    adjustments = {r["ref"]: r for r in rows if r["block"] == "adjustment"}
    assert float(adjustments["overhead_amount_total"]["value"]) > 0
    assert float(adjustments["margin_amount_total"]["value"]) > 0

    line_rows = [r for r in rows if r["block"] == "line"]
    assert line_rows, "the report carries the line block"
    assert any(r["item"] == "full_should_cost_amount" for r in line_rows)
    assert any(r["item"] == "overhead_amount" for r in line_rows)
    assert any(r["item"] == "margin_amount" for r in line_rows)

    summary = client_module.post(
        f"/api/boq/{upload_id}/export", params={"level": "summary", "format": "csv"}, json=body
    )
    summary_rows = {
        r["key"]: r["value"]
        for r in csv.DictReader(io.StringIO(summary.content.decode("utf-8")))
    }
    assert summary_rows["overhead_pct"] == "12.0"
    assert float(summary_rows["full_should_cost_total"]) > float(summary_rows["should_cost_total"])

    items = client_module.post(
        f"/api/boq/{upload_id}/export", params={"level": "items", "format": "csv"}, json=body
    )
    first = next(csv.DictReader(io.StringIO(items.content.decode("utf-8"))))
    assert "full_adjusted_benchmark_rate" in first
    assert "overhead_amount" in first
    assert "full_should_cost_amount" in first


def test_percentages_are_validated(client_module, session) -> None:
    upload_id = _upload_id(session)
    for bad in ({"overhead_pct": -1}, {"margin_pct": -5}, {"overhead_pct": 9999}):
        response = client_module.post(
            f"/api/boq/{upload_id}/benchmark",
            json={
                "tender_quarter": "2024Q4",
                "tpi_series_name": "BCA",
                "variance_threshold": 15.0,
                "adjustments": bad,
            },
        )
        assert response.status_code == 422, bad


def test_benchmark_endpoint_returns_the_full_cost(client_module, session) -> None:
    upload_id = _upload_id(session)
    response = client_module.post(
        f"/api/boq/{upload_id}/benchmark",
        json={
            "tender_quarter": "2024Q4",
            "tpi_series_name": "BCA",
            "variance_threshold": 15.0,
            "adjustments": {"overhead_pct": 12.0, "margin_pct": 6.0},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["full_should_cost_total"] > payload["totals"]["should_cost_total"]
    assert payload["totals"]["overhead_pct"] == 12.0
    assert payload["totals"]["margin_pct"] == 6.0
    components = {c["component"]: c["amount"] for c in payload["waterfall"]}
    assert components["overhead"] > 0 and components["margin"] > 0
    assert payload["lines"][0]["full_should_cost_amount"] is not None


def test_india_run_grossed_up_with_region_and_bridge(session) -> None:
    """The full cost stacks correctly with the other two modelling layers."""
    result = _run(
        session,
        filename=IN_SAMPLE,
        quarter="2026Q3",
        series="WPI-CONST",
        country="IN",
        region_code="MUM",
        adjustments=IndexAdjustments(overhead_pct=15.0, margin_pct=8.0),
    )
    line = [l for l in result.lines if l.is_benchmarked][0]
    assert line.regional_factor != 1.0
    assert line.tpi_bridged is True
    assert line.basis == "assumed"
    assert line.full_adjusted_benchmark_rate == pytest.approx(
        line.adjusted_benchmark_rate * 1.15 * 1.08, abs=0.01
    )
    identity = result.totals["boq_total"] + sum(c["amount"] for c in result.waterfall)
    assert identity == pytest.approx(result.totals["full_should_cost_total"], abs=0.01)
    # The waterfall builds every step from the base rate at FULL precision, so the residual
    # is zero rather than accumulating the 2dp rounding of the displayed rate.
    residual = [c for c in result.waterfall if c["component"] == "unexplained"][0]["amount"]
    assert abs(residual) <= 0.01, residual
