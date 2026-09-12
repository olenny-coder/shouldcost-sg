"""Manual index adjusters, sensitivity analysis, BoQ template and export levels."""

from __future__ import annotations

import io

import pandas as pd
import pytest
from sqlalchemy import select

from app.benchmark import build_benchmark, compute_sensitivity, resolve_tpi
from app.models import BoQUpload, BoQItem

SAMPLE = "sample_boq.csv"


def _upload_id(session) -> int:
    return session.scalar(select(BoQUpload.id).where(BoQUpload.filename == SAMPLE))


def _items(session, upload_id: int) -> list[BoQItem]:
    return list(
        session.scalars(select(BoQItem).where(BoQItem.upload_id == upload_id).order_by(BoQItem.id))
    )


def _run(session, adjustments=None, **overrides):
    upload_id = _upload_id(session)
    params = {
        "upload_id": upload_id,
        "filename": SAMPLE,
        "items": _items(session, upload_id),
        "tender_quarter": "2024Q4",
        "tpi_series_name": "BCA",
        "country": "SG",
    }
    params.update(overrides)
    return build_benchmark(session, adjustments=adjustments, **params)


# --------------------------------------------------------------------------- #
# Manual index adjusters
# --------------------------------------------------------------------------- #
def test_no_adjustments_leaves_every_line_derived(session) -> None:
    result = _run(session)
    assert all(not l.user_adjusted for l in result.lines)
    assert any(l.basis == "derived" for l in result.lines if l.is_benchmarked)
    assert result.adjustments_applied["is_noop"] is True
    assert not any("ASSUMED: the analyst" in a for a in result.assumptions)


def test_tpi_scale_moves_should_cost_upward(session) -> None:
    base = _run(session)
    hotter = _run(session, {"tpi_scale_pct": 10.0})
    assert hotter.totals["should_cost_total"] > base.totals["should_cost_total"]
    assert hotter.totals["total_variance_abs"] < base.totals["total_variance_abs"]


def test_tpi_scale_marks_lines_assumed_and_discloses_the_assumption(session) -> None:
    result = _run(session, {"tpi_scale_pct": 10.0})
    adjusted = [l for l in result.lines if l.is_benchmarked]
    assert adjusted
    for line in adjusted:
        assert line.user_adjusted is True
        assert line.basis == "assumed", "a user-adjusted rate is no longer purely derived"
        assert "user_adjusted" in line.flags
    assert any("applied a +10.00% shift" in a for a in result.assumptions)
    assert result.adjustments_applied["tpi_scale_pct"] == 10.0


def test_tpi_value_override_replaces_the_published_value(session) -> None:
    result = _run(session, {"tpi_value_override": 160.0})
    benchmarked = [l for l in result.lines if l.is_benchmarked]
    assert all(l.tpi_value == pytest.approx(160.0) for l in benchmarked)
    assert all(l.tpi_value_published == pytest.approx(139.2) for l in benchmarked)
    # The override states the index value at the tender quarter, so the ratio it produces
    # depends on what each rate's denominator is: 100 for a section still at the series
    # base year, and the index at 2026Q2 for a section the library expresses there.
    at_series_base = [l for l in benchmarked if l.rate_base_quarter == ""]
    at_library_quarter = [l for l in benchmarked if l.rate_base_quarter]
    assert at_series_base and at_library_quarter
    for line in at_series_base:
        assert line.tpi_ratio == pytest.approx(1.6)
    # The denominator for those rows is the index at the library's own quarter, which the
    # engine derives by carrying the last published observation forward.
    at_library = resolve_tpi(session, "BCA", "2026Q2")
    for line in at_library_quarter:
        assert line.tpi_ratio < 1.6
        assert line.tpi_ratio == pytest.approx(160.0 / at_library.value, abs=1e-6)
    assert any("overrode the BCA index value" in a for a in result.assumptions)


def test_override_and_scale_compose_rather_than_override_each_other(session) -> None:
    result = _run(session, {"tpi_value_override": 100.0, "tpi_scale_pct": 50.0})
    assert all(l.tpi_value == pytest.approx(150.0) for l in result.lines if l.is_benchmarked)


def test_global_base_rate_scale_shifts_every_benchmarked_line(session) -> None:
    base = _run(session)
    shifted = _run(session, {"base_rate_scale_pct": 5.0})
    base_by_id = {l.item_id: l for l in base.lines}
    for line in shifted.lines:
        if not line.is_benchmarked:
            continue
        original = base_by_id[line.item_id]
        assert line.benchmark_base_rate == pytest.approx(original.benchmark_base_rate * 1.05, abs=0.02)


def test_section_scale_only_touches_its_own_section(session) -> None:
    base = _run(session)
    shifted = _run(session, {"section_rate_scale_pct": {"Concrete": 20.0}})
    base_by_id = {l.item_id: l for l in base.lines}
    touched = 0
    for line in shifted.lines:
        if not line.is_benchmarked:
            continue
        original = base_by_id[line.item_id]
        if line.smm2_section == "Concrete":
            touched += 1
            assert line.rate_scale_pct == 20.0
            assert line.benchmark_base_rate == pytest.approx(original.benchmark_base_rate * 1.2, abs=0.02)
        else:
            assert line.rate_scale_pct == 0.0
            assert line.benchmark_base_rate == pytest.approx(original.benchmark_base_rate, abs=0.02)
    assert touched >= 1
    assert any("Concrete benchmark base rate" in a for a in shifted.assumptions)


def test_adjustments_do_not_break_reconciliation(session) -> None:
    result = _run(session, {"tpi_scale_pct": -12.5, "section_rate_scale_pct": {"Piling": 8.0}})
    delta = result.totals["boq_total"] + sum(w["amount"] for w in result.waterfall)
    assert abs(delta - result.totals["should_cost_total"]) <= 0.01


def test_scope_exclusion_still_cancels_market_risk_under_adjustment(session) -> None:
    result = _run(session, {"tpi_scale_pct": 15.0})
    excluded = [l for l in result.lines if l.is_benchmarked and l.scope_excluded]
    assert excluded
    for line in excluded:
        exact = line.tpi_ratio_exact or line.tpi_ratio
        market = line.quantity * line.benchmark_base_rate * (exact - 1.0)
        scope = (
            line.quantity * line.benchmark_base_rate * exact * (line.scope_factor - 1.0)
        )
        assert market + scope == pytest.approx(0.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# Sensitivity
# --------------------------------------------------------------------------- #
def _sensitivity(session, **overrides):
    upload_id = _upload_id(session)
    params = {
        "upload_id": upload_id,
        "items": _items(session, upload_id),
        "tender_quarter": "2024Q4",
        "tpi_series_name": "BCA",
        "country": "SG",
    }
    params.update(overrides)
    return compute_sensitivity(session, **params)


def test_sensitivity_baseline_matches_the_headline_benchmark(session) -> None:
    base = _run(session)
    sens = _sensitivity(session)
    assert sens["baseline"]["boq_total"] == base.totals["boq_total"]
    assert sens["baseline"]["should_cost_total"] == base.totals["should_cost_total"]
    baseline_point = [p for p in sens["tpi_sweep"] if p["is_baseline"]]
    assert len(baseline_point) == 1
    assert baseline_point[0]["should_cost_total"] == base.totals["should_cost_total"]


def test_sensitivity_sweep_is_monotonic_and_covers_the_range(session) -> None:
    sens = _sensitivity(session, tpi_scale_min_pct=-20, tpi_scale_max_pct=20, tpi_scale_step_pct=5)
    points = sens["tpi_sweep"]
    assert len(points) == 9, "(-20..20 step 5) is 9 points"
    assert points[0]["tpi_scale_pct"] == pytest.approx(-20)
    assert points[-1]["tpi_scale_pct"] == pytest.approx(20)
    totals = [p["should_cost_total"] for p in points]
    assert totals == sorted(totals), "should-cost must rise as the index rises"
    for point in points:
        assert point["lines_over"] + point["lines_under"] == point["breached_line_count"]


def test_break_even_drives_total_variance_to_zero(session) -> None:
    sens = _sensitivity(session)
    assert sens["break_even_scale_pct"] is not None
    assert sens["break_even_tpi_value"] is not None
    at_break_even = _run(session, {"tpi_scale_pct": sens["break_even_scale_pct"]})
    # Break-even is a linear solve rounded for display, and each line is rounded to cents,
    # so the residual is a rounding artefact rather than a modelling error. Bound it per
    # benchmarked line instead of in absolute currency, which would just track the size of
    # the demonstration bill.
    benchmarked = sum(1 for l in at_break_even.lines if l.is_benchmarked)
    assert abs(at_break_even.totals["total_variance_abs"]) <= 0.10 * benchmarked


def test_section_tornado_is_ranked_by_swing(session) -> None:
    sens = _sensitivity(session, section_scale_pct=10.0)
    tornado = sens["section_tornado"]
    assert tornado, "the sample BoQ has benchmarked sections"
    swings = [row["swing"] for row in tornado]
    assert swings == sorted(swings, reverse=True)
    assert sens["most_sensitive_section"] == tornado[0]["smm2_section"]
    for row in tornado:
        assert row["delta_high"] > 0, "raising a section's rate must raise should-cost"
        assert row["delta_low"] < 0
        assert row["swing"] == pytest.approx(row["delta_high"] - row["delta_low"], abs=0.02)


def test_unbenchmarked_sections_are_excluded_from_the_tornado(session) -> None:
    sens = _sensitivity(session)
    names = {row["smm2_section"] for row in sens["section_tornado"]}
    assert "Unclassified" not in names
    share = sum(row["share_of_should_cost_pct"] for row in sens["section_tornado"])
    assert 0 < share <= 100.5


def test_sensitivity_discloses_that_scenarios_are_not_forecasts(session) -> None:
    sens = _sensitivity(session)
    joined = " ".join(sens["assumptions"])
    assert "scenario inputs chosen to bracket plausible outcomes, not forecasts" in joined
    assert "break-even" in joined


def test_sensitivity_rejects_an_inverted_range(session) -> None:
    with pytest.raises(ValueError) as excinfo:
        _sensitivity(session, tpi_scale_min_pct=20, tpi_scale_max_pct=-20)
    assert "must be >=" in str(excinfo.value)


def test_sensitivity_rejects_an_absurd_number_of_points(session) -> None:
    with pytest.raises(ValueError) as excinfo:
        _sensitivity(session, tpi_scale_min_pct=-90, tpi_scale_max_pct=90, tpi_scale_step_pct=0.5)
    assert "limit is 81" in str(excinfo.value)


def test_sensitivity_honours_existing_adjustments(session) -> None:
    hotter = _sensitivity(session, adjustments={"tpi_scale_pct": 10.0})
    plain = _sensitivity(session)
    assert hotter["baseline"]["should_cost_total"] > plain["baseline"]["should_cost_total"]


# --------------------------------------------------------------------------- #
# API: adjusters, sensitivity, template, export levels
# --------------------------------------------------------------------------- #
def _sg_upload(client) -> int:
    import pathlib

    sample = pathlib.Path(__file__).resolve().parent.parent / "data" / SAMPLE
    response = client.post(
        "/api/boq/upload?country=SG",
        files={"file": ("adj-test.csv", io.BytesIO(sample.read_bytes()), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["upload_id"]


def test_benchmark_endpoint_accepts_adjusters(client_module) -> None:
    upload_id = _sg_upload(client_module)
    plain = client_module.post(
        f"/api/boq/{upload_id}/benchmark",
        json={"tender_quarter": "2024Q4", "tpi_series_name": "BCA", "variance_threshold": 15},
    ).json()
    adjusted = client_module.post(
        f"/api/boq/{upload_id}/benchmark",
        json={
            "tender_quarter": "2024Q4",
            "tpi_series_name": "BCA",
            "variance_threshold": 15,
            "adjustments": {"tpi_scale_pct": 12.5, "section_rate_scale_pct": {"Concrete": -5}},
        },
    ).json()
    assert adjusted["totals"]["should_cost_total"] > plain["totals"]["should_cost_total"]
    assert adjusted["adjustments_applied"]["tpi_scale_pct"] == 12.5
    assert adjusted["adjustments_applied"]["is_noop"] is False
    assert any("ASSUMED: the analyst" in a for a in adjusted["assumptions"])
    assert any(l["user_adjusted"] for l in adjusted["lines"])
    assert all(l["basis"] in {"measured", "derived", "assumed"} for l in adjusted["lines"])


def test_sensitivity_endpoint_returns_sweep_and_tornado(client_module) -> None:
    upload_id = _sg_upload(client_module)
    response = client_module.post(
        f"/api/boq/{upload_id}/sensitivity",
        json={
            "tender_quarter": "2024Q4",
            "tpi_series_name": "BCA",
            "variance_threshold": 15,
            "tpi_scale_min_pct": -10,
            "tpi_scale_max_pct": 10,
            "tpi_scale_step_pct": 5,
            "section_scale_pct": 10,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["tpi_sweep"]) == 5
    assert body["section_tornado"]
    assert body["most_sensitive_section"]
    assert body["break_even_scale_pct"] is not None
    assert body["currency"] == "SGD"


def test_sensitivity_endpoint_rejects_a_bad_range(client_module) -> None:
    upload_id = _sg_upload(client_module)
    response = client_module.post(
        f"/api/boq/{upload_id}/sensitivity",
        json={
            "tender_quarter": "2024Q4",
            "tpi_series_name": "BCA",
            "tpi_scale_min_pct": 10,
            "tpi_scale_max_pct": -10,
        },
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# BoQ template
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("country", ["SG", "IN"])
def test_template_csv_downloads_and_round_trips(client_module, country) -> None:
    response = client_module.get(f"/api/boq/template?format=csv&country={country}")
    assert response.status_code == 200
    assert len(response.content) > 0
    assert "text/csv" in response.headers["content-type"]
    assert "attachment; filename=" in response.headers["content-disposition"]
    assert f"template-{country.lower()}" in response.headers["content-disposition"]

    # The template is the whole schedule of rates for the market, so its length is the
    # catalogue's length - read from the file rather than hardcoded.
    from app.boq_template import catalogue_rows

    items = catalogue_rows(country)
    assert len(items) > 300, "the template must be the schedule, not a handful of examples"

    # The template must be directly uploadable - that is the whole point of it. Every
    # quantity is zero, so it uploads and totals zero rather than failing validation.
    upload = client_module.post(
        f"/api/boq/upload?country={country}",
        files={"file": (f"template-{country}.csv", io.BytesIO(response.content), "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert body["row_count"] == len(items)
    assert body["currency"] == ("INR" if country == "IN" else "SGD")
    assert all(item["sor_code"] for item in body["items"]), (
        "every catalogue line must carry the schedule code it came from"
    )

    # The unclassified share is real, not a defect: the schedules span trades outside the
    # library's ten sections, and those lines are exactly the ones that need a manual rate.
    priceable = sum(1 for row in items if row[2])
    assert body["unclassified_count"] == len(items) - priceable
    assert body["unclassified_count"] < len(items), "some schedule items must be priceable"


@pytest.mark.parametrize("country", ["SG", "IN"])
def test_template_xlsx_has_boq_and_instructions_sheets(client_module, country) -> None:
    response = client_module.get(f"/api/boq/template?format=xlsx&country={country}")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    sheets = pd.read_excel(io.BytesIO(response.content), sheet_name=None, engine="openpyxl")
    assert set(sheets) == {"BoQ", "Instructions"}
    # sor_code first so a line can be traced to the schedule, and section next to the
    # description so the analyst can filter to the rows the library can actually price.
    assert list(sheets["BoQ"].columns)[:3] == ["sor_code", "description", "section"]
    assert set(sheets["BoQ"].columns) >= {"unit", "quantity", "rate", "is_placeholder"}
    from app.boq_template import catalogue_rows

    assert len(sheets["BoQ"]) == len(catalogue_rows(country))
    assert (sheets["BoQ"]["quantity"] == 0).all(), "an untouched template must total zero"
    instructions = sheets["Instructions"].astype(str).to_string()
    assert "HOW TO USE THIS TEMPLATE" in instructions
    assert "Measurement standard" in instructions
    assert "FILTER ON THE section COLUMN" in instructions


def _csv_bytes(rows: list[list]) -> bytes:
    """Quote properly: descriptions contain commas, and an unquoted one truncates the row."""
    import csv as _csv

    buffer = io.StringIO()
    _csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue().encode("utf-8")


def test_lines_added_outside_the_schedule_are_reported(client_module) -> None:
    """A line with no schedule code came from the analyst, so the schedule cannot price it.

    The template lists every schedule item, so a blank sor_code is exactly what "this line is
    not in the list" looks like - and those are the lines that need a manual rate before the
    should-cost is complete.
    """
    rows = [
        ["sor_code", "description", "unit", "quantity", "rate"],
        # From the template: carries the code it was quoted from.
        ["III.1.2.A",
         "Reinforced Concrete: Reinforced concrete to any location - grade 25",
         "m3", 100, 158.09],
        # Added by the analyst: no code, so nothing in the schedule covers it.
        ["", "Curtain walling to atrium, aluminium and glass, including fixings",
         "m2", 240, 980.00],
    ]
    upload = client_module.post(
        "/api/boq/upload?country=SG",
        files={"file": ("mixed.csv", io.BytesIO(_csv_bytes(rows)), "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert [i["sor_code"] for i in body["items"]] == ["III.1.2.A", ""]
    assert any("NOT in the loaded schedule of rates" in w for w in body["warnings"])
    assert any("1 of 2" in w for w in body["warnings"])


def test_a_plain_upload_without_codes_is_not_nagged(client_module) -> None:
    """With no sor_code column at all, every line is codeless - saying so would be noise."""
    rows = [
        ["description", "unit", "quantity", "rate"],
        ["Sawn timber formwork to soffits of suspended slabs", "m2", 500, 45.00],
        ["Cement and sand plaster to internal walls, 20mm thick", "m2", 300, 25.00],
    ]
    upload = client_module.post(
        "/api/boq/upload?country=SG",
        files={"file": ("plain.csv", io.BytesIO(_csv_bytes(rows)), "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    assert not any("NOT in the loaded schedule" in w for w in upload.json()["warnings"])


def test_template_rejects_an_unknown_country(client_module) -> None:
    assert client_module.get("/api/boq/template?country=US").status_code == 422


# --------------------------------------------------------------------------- #
# Export levels
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("level", ["items", "sections", "waterfall", "summary"])
def test_export_levels_return_non_empty_csv(client_module, level) -> None:
    upload_id = _sg_upload(client_module)
    response = client_module.get(
        f"/api/boq/{upload_id}/export?format=csv&level={level}"
        "&tender_quarter=2024Q4&tpi_series_name=BCA&variance_threshold=15"
    )
    assert response.status_code == 200, response.text
    assert len(response.content) > 0
    assert f"-{level}.csv" in response.headers["content-disposition"]


def test_sections_export_totals_match_the_benchmark(client_module) -> None:
    upload_id = _sg_upload(client_module)
    benchmark = client_module.post(
        f"/api/boq/{upload_id}/benchmark",
        json={"tender_quarter": "2024Q4", "tpi_series_name": "BCA", "variance_threshold": 15},
    ).json()
    export = client_module.get(
        f"/api/boq/{upload_id}/export?format=csv&level=sections"
        "&tender_quarter=2024Q4&tpi_series_name=BCA&variance_threshold=15"
    )
    frame = pd.read_csv(io.BytesIO(export.content))
    assert abs(frame["boq_amount"].sum() - benchmark["totals"]["boq_total"]) <= 0.01
    assert abs(frame["should_cost_total" if "should_cost_total" in frame else "should_cost_amount"].sum()
               - benchmark["totals"]["should_cost_total"]) <= 0.01


def test_post_export_reflects_index_adjustments(client_module) -> None:
    upload_id = _sg_upload(client_module)
    body = {
        "tender_quarter": "2024Q4",
        "tpi_series_name": "BCA",
        "variance_threshold": 15,
        "adjustments": {"tpi_scale_pct": 15.0},
    }
    plain = client_module.post(
        f"/api/boq/{upload_id}/export?format=csv&level=summary", json=body | {"adjustments": None}
    )
    adjusted = client_module.post(
        f"/api/boq/{upload_id}/export?format=csv&level=summary", json=body
    )
    assert plain.status_code == 200 and adjusted.status_code == 200
    plain_frame = pd.read_csv(io.BytesIO(plain.content))
    adjusted_frame = pd.read_csv(io.BytesIO(adjusted.content))

    def total(frame):
        row = frame[frame["key"] == "should_cost_total"]
        return float(row["value"].iloc[0])

    assert total(adjusted_frame) > total(plain_frame)
    scales = adjusted_frame[adjusted_frame["key"] == "tpi_scale_pct"]["value"]
    assert float(scales.iloc[0]) == pytest.approx(15.0)


def test_export_rejects_an_unknown_level(client_module) -> None:
    upload_id = _sg_upload(client_module)
    assert client_module.get(f"/api/boq/{upload_id}/export?level=banana").status_code == 422
