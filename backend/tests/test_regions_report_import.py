"""India regional adjusters, the full CSV report, and the real-data importer."""

from __future__ import annotations

import csv
import io

import pandas as pd
import pytest
from sqlalchemy import select

from app.benchmark import RegionLookupError, build_benchmark, compute_sensitivity, resolve_region
from app.models import BoQUpload, BoQItem, TPISeries

SG_SAMPLE = "sample_boq.csv"
IN_SAMPLE = "sample_boq_india.csv"


def _upload_id(session, filename: str) -> int:
    return session.scalar(select(BoQUpload.id).where(BoQUpload.filename == filename))


def _items(session, upload_id: int) -> list[BoQItem]:
    return list(
        session.scalars(select(BoQItem).where(BoQItem.upload_id == upload_id).order_by(BoQItem.id))
    )


def _run(session, filename=IN_SAMPLE, series="WPI-CONST", region=None, country="IN", **kw):
    upload_id = _upload_id(session, filename)
    return build_benchmark(
        session,
        upload_id=upload_id,
        filename=filename,
        items=_items(session, upload_id),
        tender_quarter="2024Q4",
        tpi_series_name=series,
        country=country,
        region_code=region,
        **kw,
    )


# --------------------------------------------------------------------------- #
# Regional cost adjustment
# --------------------------------------------------------------------------- #
def test_india_has_many_regions_with_exactly_one_default(session) -> None:
    from app.models import RegionalFactor

    rows = list(session.scalars(select(RegionalFactor).where(RegionalFactor.country == "IN")))
    assert len(rows) >= 10, "India is a multi-city market"
    assert len([r for r in rows if r.is_default]) == 1
    assert [r for r in rows if r.is_default][0].region_code == "DEL"
    codes = {r.region_code for r in rows}
    for expected in ("DEL", "MUM", "BLR", "CHN", "KOL", "HYD", "PUN", "AHM", "JAI", "KOC"):
        assert expected in codes


def test_singapore_has_a_single_reference_region(session) -> None:
    from app.models import RegionalFactor

    rows = list(session.scalars(select(RegionalFactor).where(RegionalFactor.country == "SG")))
    assert len(rows) == 1
    assert rows[0].region_code == "SGP"
    assert rows[0].factor == 1.0
    assert rows[0].is_default is True


def test_region_falls_back_to_the_country_default(session) -> None:
    default = resolve_region(session, "IN", None)
    explicit = resolve_region(session, "IN", "DEL")
    assert default.region_code == "DEL"
    assert default.factor == explicit.factor == 1.0


def test_unknown_region_lists_the_valid_ones(session) -> None:
    with pytest.raises(RegionLookupError) as excinfo:
        resolve_region(session, "IN", "NYC")
    message = str(excinfo.value)
    assert "NYC" in message and "Available regions" in message and "MUM" in message


def test_a_higher_regional_factor_raises_should_cost(session) -> None:
    delhi = _run(session, region="DEL")
    mumbai = _run(session, region="MUM")     # 1.128
    jaipur = _run(session, region="JAI")     # 0.941
    assert mumbai.totals["should_cost_total"] > delhi.totals["should_cost_total"]
    assert jaipur.totals["should_cost_total"] < delhi.totals["should_cost_total"]
    # The BoQ itself is untouched by the region - only the benchmark moves.
    assert mumbai.totals["boq_total"] == delhi.totals["boq_total"] == jaipur.totals["boq_total"]


def test_the_regional_factor_is_linear_on_every_benchmarked_line(session) -> None:
    delhi = _run(session, region="DEL")
    mumbai = _run(session, region="MUM")
    factor = resolve_region(session, "IN", "MUM").factor
    by_id = {l.item_id: l for l in delhi.lines}
    for line in mumbai.lines:
        if not line.is_benchmarked:
            continue
        assert line.regional_factor == pytest.approx(factor)
        assert line.benchmark_base_rate == pytest.approx(
            by_id[line.item_id].benchmark_base_rate * factor, abs=0.02
        )
        # A regional multiplier is a modelling input, not an observation.
        assert line.basis == "assumed"
        assert line.user_adjusted is True


def test_the_region_is_disclosed_in_the_assumptions(session) -> None:
    result = _run(session, region="MUM")
    joined = " ".join(result.assumptions)
    assert "1.128" in joined
    assert "Mumbai" in joined
    assert "single blended factor, not a material/labour split" in joined
    assert result.region_code == "MUM"
    assert result.region_name == "Mumbai"


def test_a_modelled_region_factor_raises_a_warning(session) -> None:
    result = _run(session, region="MUM")
    assert result.regional_factor_is_placeholder is True
    warning = [w for w in result.warnings if "Regional adjustment" in w][0]
    assert "modelled" in warning
    assert "Source:" in warning
    # It states what the multiplier is and where it came from, not that it is a placeholder.
    assert "INDICATIVE" not in warning
    assert "TODO" not in warning


def test_the_delhi_region_does_not_mark_lines_assumed(session) -> None:
    """A factor of exactly 1.0 changes nothing, so it must not taint the basis."""
    result = _run(session, region="DEL")
    assert result.regional_factor == 1.0
    assert all(not l.user_adjusted for l in result.lines)


def test_region_flows_through_sensitivity(session) -> None:
    upload_id = _upload_id(session, IN_SAMPLE)
    args = {
        "upload_id": upload_id,
        "items": _items(session, upload_id),
        "tender_quarter": "2024Q4",
        "tpi_series_name": "WPI-CONST",
        "country": "IN",
    }
    delhi = compute_sensitivity(session, region_code="DEL", **args)
    mumbai = compute_sensitivity(session, region_code="MUM", **args)
    assert delhi["region_name"] == "Delhi (NCR)"
    assert mumbai["region_name"] == "Mumbai"
    assert mumbai["baseline"]["should_cost_total"] > delhi["baseline"]["should_cost_total"]
    assert mumbai["regional_factor"] == pytest.approx(1.128)


def test_regions_endpoint(client_module) -> None:
    india = client_module.get("/api/indices/regions?country=IN").json()
    singapore = client_module.get("/api/indices/regions?country=SG").json()
    assert len(india) >= 10 and len(singapore) == 1
    assert india[0]["is_default"] is True, "the default region sorts first"
    for row in india:
        assert row["currency"] == "INR"
        assert row["source_url"].startswith("https://")
        assert row["notes"]
    assert client_module.get("/api/indices/regions?country=US").status_code == 422


def test_benchmark_endpoint_accepts_a_region(client_module, session) -> None:
    upload_id = _upload_id(session, IN_SAMPLE)
    body = {"tender_quarter": "2024Q4", "tpi_series_name": "WPI-CONST", "variance_threshold": 15}
    delhi = client_module.post(f"/api/boq/{upload_id}/benchmark", json={**body, "region_code": "DEL"}).json()
    mumbai = client_module.post(f"/api/boq/{upload_id}/benchmark", json={**body, "region_code": "MUM"}).json()
    assert mumbai["totals"]["should_cost_total"] > delhi["totals"]["should_cost_total"]
    assert mumbai["region_code"] == "MUM"
    assert mumbai["region_name"] == "Mumbai"
    assert mumbai["regional_factor"] == pytest.approx(1.128)
    assert all(l["regional_factor"] == pytest.approx(1.128) for l in mumbai["lines"] if l["is_benchmarked"])
    bad = client_module.post(f"/api/boq/{upload_id}/benchmark", json={**body, "region_code": "NYC"})
    assert bad.status_code == 400


# --------------------------------------------------------------------------- #
# Full report export
# --------------------------------------------------------------------------- #
def _report(client_module, upload_id: int, **overrides) -> list[dict]:
    body = {
        "tender_quarter": "2024Q4",
        "tpi_series_name": "WPI-CONST",
        "variance_threshold": 15,
        "region_code": "MUM",
    }
    body.update(overrides)
    response = client_module.post(f"/api/boq/{upload_id}/export?format=csv&level=report", json=body)
    assert response.status_code == 200, response.text
    assert len(response.content) > 0
    text = response.content.decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def test_report_contains_every_block(client_module, session) -> None:
    rows = _report(client_module, _upload_id(session, IN_SAMPLE))
    blocks = {r["block"] for r in rows}
    assert blocks == {
        "report", "totals", "section", "waterfall", "line", "adjustment", "warning",
        "assumption", "source",
    }
    assert list(rows[0].keys()) == ["block", "ref", "item", "value", "basis"]


def test_report_header_names_the_market_region_and_index(client_module, session) -> None:
    rows = _report(client_module, _upload_id(session, IN_SAMPLE))
    # The report uses ref = machine-readable key, item = human-readable label.
    header = {r["ref"]: r["value"] for r in rows if r["block"] == "report"}
    assert "India" in header["country"]
    assert header["currency"] == "INR"
    assert "Mumbai" in header["region"]
    assert header["regional_factor"] == "1.128"
    assert header["index_series"] == "WPI-CONST"
    assert header["tender_quarter"] == "2024Q4"
    assert "IS 1200" in header["measurement_standard"]
    assert "adjusted_benchmark_rate" in header["formula"]


def test_report_reconciles_and_carries_provenance(client_module, session) -> None:
    rows = _report(client_module, _upload_id(session, IN_SAMPLE))
    # The totals block also carries non-numeric metadata rows (the overall basis, the
    # variance basis label, and the index-freshness booleans).
    def _as_number(value: str):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    totals = {
        r["ref"]: _as_number(r["value"])
        for r in rows
        if r["block"] == "totals" and _as_number(r["value"]) is not None
    }
    waterfall = [r for r in rows if r["block"] == "waterfall" and r["item"] == "amount"]
    component_sum = sum(
        float(r["value"]) for r in waterfall if r["ref"] not in ("boq_total", "should_cost_total")
    )
    assert abs(totals["boq_total"] + component_sum - totals["should_cost_total"]) <= 0.01

    # Every waterfall component states its basis.
    bases = {r["ref"]: r["basis"] for r in rows if r["block"] == "waterfall" and r["item"] == "amount"}
    assert bases["material"] == "assumed"
    assert bases["market_risk"] == "derived"

    # Every benchmarked line carries the benchmark provenance.
    line_rows = [r for r in rows if r["block"] == "line"]
    assert any(r["item"] == "benchmark_source" for r in line_rows)
    assert any(r["item"] == "benchmark_base_year" for r in line_rows)
    assert any(r["item"] == "regional_factor" for r in line_rows)
    # The report states provenance, not a placeholder flag or a "replace this" TODO.
    assert not any("is_placeholder" in r["item"] for r in line_rows)
    assert not any("replace_with" in r["item"] for r in line_rows)


def test_report_contains_warnings_assumptions_and_sources(client_module, session) -> None:
    rows = _report(client_module, _upload_id(session, IN_SAMPLE))
    assumptions = [r["value"] for r in rows if r["block"] == "assumption"]
    assert any("Regional" in a or "1.128" in a for a in assumptions)
    assert any("derived" in a and "source it came from" in a for a in assumptions)
    assert [r for r in rows if r["block"] == "warning"]
    sources = {r["ref"] for r in rows if r["block"] == "source"}
    assert any("Wholesale Price Index" in s for s in sources)
    assert any("CPWD" in s for s in sources)


def test_report_records_every_adjustment(client_module, session) -> None:
    rows = _report(
        client_module,
        _upload_id(session, IN_SAMPLE),
        adjustments={"tpi_scale_pct": 7.5, "section_rate_scale_pct": {"Concrete": -4}},
    )
    adjustments = {r["ref"]: r["value"] for r in rows if r["block"] == "adjustment"}
    assert adjustments["tpi_scale_pct"] == "7.5"
    assert adjustments["regional_factor"] == "1.128"
    assert adjustments["section:Concrete"] == "-4.0" or adjustments["section:Concrete"] == "-4"


def test_report_applies_the_region_to_the_numbers(client_module, session) -> None:
    upload_id = _upload_id(session, IN_SAMPLE)
    delhi = _report(client_module, upload_id, region_code="DEL")
    mumbai = _report(client_module, upload_id, region_code="MUM")

    def should_cost(rows):
        found = [r for r in rows if r["block"] == "totals" and r["ref"] == "should_cost_total"]
        assert found, "the report must carry a should_cost_total row"
        return float(found[0]["value"])

    assert should_cost(mumbai) > should_cost(delhi)


def test_report_xlsx_export(client_module, session) -> None:
    upload_id = _upload_id(session, IN_SAMPLE)
    response = client_module.post(
        f"/api/boq/{upload_id}/export?format=xlsx&level=report",
        json={"tender_quarter": "2024Q4", "tpi_series_name": "WPI-CONST", "variance_threshold": 15},
    )
    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    frame = pd.read_excel(io.BytesIO(response.content), engine="openpyxl")
    assert set(["block", "ref", "item", "value", "basis"]).issubset(frame.columns)
    assert len(frame) > 100


# --------------------------------------------------------------------------- #
# Real-data importer
# --------------------------------------------------------------------------- #
@pytest.fixture()
def import_csv(tmp_path):
    path = tmp_path / "real_tpi.csv"
    rows = [
        ["country", "series_name", "quarter", "base_year", "base_value", "currency", "value",
         "scope_inclusions", "scope_exclusions", "source_url", "is_placeholder",
         "provenance_note", "replace_with"],
        ["IN", "TESTIMPORT", "2024Q4", "2022", "100.0", "INR", "93.4", "Imported test series",
         "External Works", "https://example.gov.in/", "true", "", "# TODO: n/a"],
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return path


def _drop_test_series(session, name="TESTIMPORT"):
    for row in session.scalars(select(TPISeries).where(TPISeries.series_name == name)):
        session.delete(row)
    session.commit()


def test_importer_marks_rows_real_and_stamps_provenance(import_csv, session) -> None:
    from app.importer import import_file

    _drop_test_series(session)
    try:
        result = import_file(
            import_csv,
            "tpi",
            provenance="TEST PROVENANCE: official publisher, series X, quarterly mean.",
            source_url="https://official.example.gov/",
        )
        assert result["inserted"] == 1

        rows = list(session.scalars(select(TPISeries).where(TPISeries.series_name == "TESTIMPORT")))
        assert len(rows) == 1
        row = rows[0]
        assert row.is_placeholder is False, "imported data must not be flagged as a placeholder"
        assert row.provenance_note.startswith("TEST PROVENANCE")
        assert row.replace_with == "", "a real row has nothing to replace"
        assert row.source_url == "https://official.example.gov/"
    finally:
        _drop_test_series(session)


def test_importer_is_idempotent(import_csv, session) -> None:
    from app.importer import import_file

    _drop_test_series(session)
    try:
        first = import_file(import_csv, "tpi", provenance="TEST")
        second = import_file(import_csv, "tpi", provenance="TEST")
        assert first["inserted"] == 1
        assert second["inserted"] == 0, "re-importing the same file must not duplicate rows"
        assert second["tpi_series"] == first["tpi_series"]
    finally:
        _drop_test_series(session)


def test_importer_requires_provenance(import_csv) -> None:
    from app.importer import import_file

    with pytest.raises(ValueError) as excinfo:
        import_file(import_csv, "tpi", provenance="   ")
    assert "provenance is required" in str(excinfo.value)


def test_importer_rejects_an_unknown_kind(import_csv) -> None:
    from app.importer import import_file

    with pytest.raises(ValueError) as excinfo:
        import_file(import_csv, "banana", provenance="x")
    assert "Unknown kind" in str(excinfo.value)


def test_importer_reports_a_missing_file(tmp_path) -> None:
    from app.importer import import_file

    with pytest.raises(FileNotFoundError):
        import_file(tmp_path / "nope.csv", "tpi", provenance="x")


# --------------------------------------------------------------------------- #
# CORS posture, tested without the app so it is environment-independent
# --------------------------------------------------------------------------- #
def test_production_cors_is_fail_closed_without_a_frontend_url(monkeypatch) -> None:
    """In production with no FRONTEND_URL the allowlist is empty - not a wildcard."""
    from app.config import Settings, reset_settings_cache

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.delenv("FRONTEND_PREVIEW_REGEX", raising=False)
    monkeypatch.setenv("DEV_CORS_ORIGINS", "http://localhost:5173")
    reset_settings_cache()
    try:
        settings = Settings()
        assert settings.is_production is True
        assert settings.cors_allow_origins() == []
        assert settings.cors_allow_origin_regex() is None
    finally:
        reset_settings_cache()


def test_production_cors_allows_only_the_configured_frontend(monkeypatch) -> None:
    from app.config import Settings, reset_settings_cache

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "https://shouldcost-sg.vercel.app/")
    monkeypatch.setenv("FRONTEND_PREVIEW_REGEX", r"^https://shouldcost-.*\.vercel\.app$")
    monkeypatch.setenv("DEV_CORS_ORIGINS", "http://localhost:5173")
    reset_settings_cache()
    try:
        settings = Settings()
        origins = settings.cors_allow_origins()
        # The dev origins must not leak into production, and the trailing slash is stripped.
        assert origins == ["https://shouldcost-sg.vercel.app"]
        assert "http://localhost:5173" not in origins
        assert "*" not in origins
        assert settings.cors_allow_origin_regex() == r"^https://shouldcost-.*\.vercel\.app$"
    finally:
        reset_settings_cache()


def test_development_cors_includes_the_local_origins(monkeypatch) -> None:
    from app.config import Settings, reset_settings_cache

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.setenv("DEV_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    reset_settings_cache()
    try:
        settings = Settings()
        assert settings.cors_allow_origins() == ["http://localhost:5173", "http://127.0.0.1:5173"]
        assert "*" not in settings.cors_allow_origins()
    finally:
        reset_settings_cache()


# --------------------------------------------------------------------------- #
# Real-data integrity
# --------------------------------------------------------------------------- #
def test_seeded_real_data_is_not_flagged_as_placeholder(client_module) -> None:
    """The Singapore material prices are real BCA data and must say so."""
    rows = client_module.get("/api/indices/materials?country=SG").json()
    assert rows
    for row in rows:
        assert row["is_placeholder"] is False
        assert row["provenance_note"].startswith("REAL DATA.")
        assert "BUILDING AND CONSTRUCTION AUTHORITY" in row["provenance_note"]
        assert row["frequency"] == "annual"
        assert row["source_url"] == "https://tablebuilder.singstat.gov.sg/api/table/tabledata/M211671"


def test_wpi_values_match_the_published_series(client_module) -> None:
    """Spot-check real WPI values against the published 2022-23 series."""
    rows = client_module.get("/api/indices/tpi?country=IN").json()
    by_key = {(r["series_name"], r["quarter"]): float(r["value"]) for r in rows}
    # WPI cement/lime/plaster sub-group, calendar-quarter means of Apr-23..Mar-24 etc.
    assert by_key[("WPI-CEM", "2023Q2")] == pytest.approx(97.8, abs=0.05)
    assert by_key[("WPI-CEM", "2023Q4")] == pytest.approx(100.7, abs=0.05)
    assert by_key[("WPI-STL", "2023Q2")] == pytest.approx(95.6, abs=0.05)
    assert by_key[("WPI-CONST", "2024Q4")] == pytest.approx(87.6, abs=0.05)
    # And the item-level series behind them.
    assert by_key[("WPI-CEM-OPC", "2024Q4")] != by_key[("WPI-CEM", "2024Q4")]
