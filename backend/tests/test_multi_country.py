"""Multi-country behaviour: Singapore (SMM2) and India (IS 1200 / CPWD DSR)."""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from app.benchmark import build_benchmark, resolve_tpi
from app.classifier import classify
from app.countries import COUNTRIES, country_codes, get_country
from app.models import BoQUpload, BoQItem

SG_SAMPLE = "sample_boq.csv"
IN_SAMPLE = "sample_boq_india.csv"


def _upload_id(session, filename: str) -> int:
    return session.scalar(select(BoQUpload.id).where(BoQUpload.filename == filename))


def _items(session, upload_id: int) -> list[BoQItem]:
    return list(
        session.scalars(select(BoQItem).where(BoQItem.upload_id == upload_id).order_by(BoQItem.id))
    )


def _run(session, upload_id: int, series: str, quarter: str = "2024Q4", country: str = "SG"):
    return build_benchmark(
        session,
        upload_id=upload_id,
        filename="",
        items=_items(session, upload_id),
        tender_quarter=quarter,
        tpi_series_name=series,
        country=country,
    )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def test_registry_exposes_both_markets_with_credible_sources() -> None:
    assert country_codes() == ["IN", "SG"]

    india = get_country("in")
    assert india.name == "India"
    assert india.currency == "INR"
    assert india.measurement_standard == "IS 1200 / CPWD DSR"
    # The default India series must be a REAL one; the CPWD/NBO city indices remain placeholders.
    assert india.default_tpi_series == "WPI-CONST"

    singapore = COUNTRIES["SG"]
    assert singapore.currency == "SGD"
    assert singapore.measurement_standard == "SMM2"

    # The India source list must name the real publications, not invented ones.
    names = " | ".join(source.name for source in india.sources)
    for expected in ("CPWD", "Delhi Schedule of Rates", "Wholesale Price Index", "NBO", "IS 1200", "Reserve Bank"):
        assert expected in names, f"{expected} missing from the India source registry"
    assert all(source.url.startswith("https://") for source in india.sources)


def test_get_country_rejects_an_unknown_code() -> None:
    with pytest.raises(ValueError) as excinfo:
        get_country("US")
    assert "US" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# India classification vocabulary
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Reinforced cement concrete M25 in pile caps and raft foundation", "Concrete"),
        ("Plain cement concrete PCC 1:4:8 below foundation", "Concrete"),
        ("TMT reinforcement bars Fe500D, cutting, bending and fixing", "Reinforcement"),
        ("Tor steel reinforcement to columns", "Reinforcement"),
        ("Shuttering and formwork to soffits of suspended slabs", "Formwork"),
        ("Earthwork in excavation in foundation trenches", "Excavation"),
        ("Brickwork in cement mortar 1:6 in superstructure", "Masonry"),
        ("AAC block work in internal partitions", "Masonry"),
        ("Cement plaster 12mm thick to internal walls", "Plaster"),
        ("External rendering 20mm thick in cement mortar 1:4", "Plaster"),
        ("APP modified bitumen membrane waterproofing to terrace", "Waterproofing"),
        ("PVC conduit and casing-capping containment to electrical services", "M&E Containment"),
        ("Preliminaries including work charged establishment and insurance", "Preliminaries"),
    ],
)
def test_india_vocabulary_classifies(description: str, expected: str) -> None:
    assert classify(description, country="IN").smm2_section == expected


def test_india_and_singapore_rule_tables_differ_but_share_sections() -> None:
    from app.classifier import rules_as_dicts

    sg = {r["smm2_section"]: r["pattern"] for r in rules_as_dicts("SG")}
    india = {r["smm2_section"]: r["pattern"] for r in rules_as_dicts("IN")}
    assert set(sg) == set(india), "both standards use the same canonical section vocabulary"
    assert india["Formwork"] != sg["Formwork"], "India adds shuttering"
    assert "shuttering" in india["Formwork"]
    assert "shuttering" not in sg["Formwork"], "Singapore rules must be unchanged"


# --------------------------------------------------------------------------- #
# India benchmark end to end
# --------------------------------------------------------------------------- #
def test_india_sample_boq_is_seeded_and_in_rupees(session) -> None:
    upload_id = _upload_id(session, IN_SAMPLE)
    assert upload_id is not None
    upload = session.get(BoQUpload, upload_id)
    assert upload.country == "IN"
    assert upload.currency == "INR"
    assert len(_items(session, upload_id)) == 20


def test_india_benchmark_uses_cpwd_index_and_reports_inr(session) -> None:
    upload_id = _upload_id(session, IN_SAMPLE)
    result = _run(session, upload_id, "CPWD", country="IN")
    assert result.country == "IN"
    assert result.country_name == "India"
    assert result.currency == "INR"
    assert result.classification_standard == "IS 1200 / CPWD DSR"
    assert result.totals["boq_total"] > 1_000_000, "an Indian BoQ in rupees is a large number"
    assert abs(
        result.totals["boq_total"] + sum(w["amount"] for w in result.waterfall)
        - result.totals["should_cost_total"]
    ) <= 0.01


def test_india_cpwd_piling_scope_warning_fires(session) -> None:
    upload_id = _upload_id(session, IN_SAMPLE)
    result = _run(session, upload_id, "CPWD", country="IN")
    joined = " ".join(result.warnings)
    assert "Scope exclusion" in joined
    assert "Piling" in joined and "CPWD" in joined

    piling = [l for l in result.lines if l.smm2_section == "Piling" and l.is_benchmarked]
    assert piling
    assert all(l.scope_excluded and l.scope_factor != 1.0 for l in piling)


def test_india_control_series_fires_no_scope_warning(session) -> None:
    """The WPI series are MATERIALS indices: they declare no section exclusions, so no scope
    factor moves and no scope warning fires."""
    upload_id = _upload_id(session, IN_SAMPLE)
    result = _run(session, upload_id, "WPI-CONST", country="IN")
    assert all(l.scope_factor == 1.0 for l in result.lines)
    assert not any("Scope exclusion" in w for w in result.warnings)


def test_countries_do_not_share_index_series(session) -> None:
    with pytest.raises(ValueError) as excinfo:
        resolve_tpi(session, "CPWD", "2024Q4", country="SG")
    assert "CPWD" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo2:
        resolve_tpi(session, "BCA", "2024Q4", country="IN")
    assert "BCA" in str(excinfo2.value)


def test_india_sample_breaches_the_threshold_in_both_directions(session) -> None:
    upload_id = _upload_id(session, IN_SAMPLE)
    result = _run(session, upload_id, "WPI-CONST", country="IN")
    over = [l for l in result.lines if "over_threshold" in l.flags]
    under = [l for l in result.lines if "under_threshold" in l.flags]
    assert len(over) >= 2
    assert len(under) >= 2


def test_india_benchmark_base_rate_currency_is_inr(session) -> None:
    upload_id = _upload_id(session, IN_SAMPLE)
    result = _run(session, upload_id, "CPWD", country="IN")
    benchmarked = [l for l in result.lines if l.is_benchmarked]
    assert benchmarked
    for line in benchmarked:
        # Indian benchmark rates are three to five figures in rupees, never SGD-scale.
        assert line.benchmark_base_rate > 100
        assert line.provenance["base_year"] == 2023
        assert "CPWD" in line.provenance["source"] or "NBO" in line.provenance["source"]
        assert line.provenance["is_placeholder"] is True


def test_both_markets_are_independent_in_one_database(session) -> None:
    sg = _run(session, _upload_id(session, SG_SAMPLE), "BCA", country="SG")
    india = _run(session, _upload_id(session, IN_SAMPLE), "CPWD", country="IN")
    assert sg.currency == "SGD"
    assert india.currency == "INR"
    assert sg.totals["boq_total"] != india.totals["boq_total"]


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #
def test_countries_endpoint_lists_both_markets(client_module) -> None:
    rows = client_module.get("/api/countries").json()
    assert {row["code"] for row in rows} == {"SG", "IN"}
    india = [row for row in rows if row["code"] == "IN"][0]
    assert india["currency"] == "INR"
    assert india["measurement_standard"] == "IS 1200 / CPWD DSR"
    assert len(india["sources"]) >= 5


def test_tpi_endpoint_is_country_scoped(client_module) -> None:
    sg = client_module.get("/api/indices/tpi?country=SG").json()
    india = client_module.get("/api/indices/tpi?country=IN").json()
    assert {r["series_name"] for r in sg} == {"BCA", "HDB", "RLB", "AECOM"}
    assert {r["series_name"] for r in india} == {
        "WPI-CONST", "WPI-CEM", "WPI-STL", "WPI-CEM-OPC", "WPI-STL-BARS", "WPI-RMC", "CPWD", "NBO",
    }
    assert all(r["currency"] == "SGD" for r in sg)
    assert all(r["currency"] == "INR" for r in india)
    assert all(r["base_year"] == 2010 for r in sg)
    assert all(r["base_year"] == 2022 for r in india if r["series_name"].startswith("WPI-"))
    # Singapore's TPI is still a placeholder; the India WPI series are real.
    assert all(r["is_placeholder"] is True for r in sg)
    real = [r for r in india if not r["is_placeholder"]]
    assert real and all(r["series_name"].startswith("WPI-") for r in real)


def test_real_data_is_labeled_as_real_and_carries_provenance(client_module) -> None:
    # Every real row must explain where it came from; every placeholder must carry a TODO.
    real_rows = client_module.get("/api/indices/tpi?country=IN").json()
    real_rows = [r for r in real_rows if not r["is_placeholder"]]
    assert real_rows
    for row in real_rows:
        assert row["provenance_note"].startswith("REAL DATA."), row["provenance_note"][:60]
        assert "Office of the Economic Adviser" in row["provenance_note"]
        assert row["source_url"].startswith("https://eaindustry.nic.in/")
        assert row["replace_with"] == ""

    placeholders = [r for r in client_module.get("/api/indices/tpi?country=SG").json()]
    for row in placeholders:
        assert row["replace_with"].startswith("# TODO:")
        assert "PLACEHOLDER" in row["provenance_note"]


def test_material_series_declares_index_or_price(client_module) -> None:
    """India publishes WPI cost INDICES; Singapore publishes actual PRICES. The unit says which."""
    india = client_module.get("/api/indices/materials?country=IN&material=cement").json()
    singapore = client_module.get("/api/indices/materials?country=SG&material=cement").json()
    assert india and singapore
    assert all(r["unit"].startswith("index") for r in india), "an index must not masquerade as a price"
    assert all(r["unit"] == "tonne" for r in singapore)
    assert all(r["frequency"] == "monthly" for r in india)
    assert all(r["frequency"] == "annual" for r in singapore)
    assert all(r["is_placeholder"] is False for r in india + singapore)


def test_materials_endpoint_is_country_scoped(client_module) -> None:
    sg = client_module.get("/api/indices/materials?country=SG&material=cement").json()
    india = client_module.get("/api/indices/materials?country=IN&material=cement").json()
    assert len(sg) == 12 and len(india) == 40
    assert all(r["currency"] == "SGD" for r in sg)
    assert all(r["currency"] == "INR" for r in india)


def test_benchmark_rates_endpoint_is_country_scoped(client_module) -> None:
    sg = client_module.get("/api/indices/benchmark-rates?country=SG").json()
    india = client_module.get("/api/indices/benchmark-rates?country=IN").json()
    assert len(sg) == 10 and len(india) == 10
    assert {r["classification_standard"] for r in sg} == {"SMM2"}
    assert {r["classification_standard"] for r in india} == {"IS 1200 / CPWD DSR"}
    for row in india:
        assert row["currency"] == "INR"
        assert row["base_year"] == 2023
        assert row["is_placeholder"] is True
        assert row["replace_with"].startswith("# TODO:")


def test_unknown_country_is_rejected(client_module) -> None:
    assert client_module.get("/api/indices/tpi?country=US").status_code == 422
    assert client_module.get("/api/indices/benchmark-rates?country=ZZ").status_code == 422


def test_classifier_rules_endpoint_reports_the_standard(client_module) -> None:
    india = client_module.get("/api/indices/classifier-rules?country=IN").json()
    assert india["classification_standard"] == "IS 1200 / CPWD DSR"
    formwork = [r for r in india["rules"] if r["smm2_section"] == "Formwork"][0]
    assert "shuttering" in formwork["pattern"]


def test_india_upload_and_benchmark_via_the_api(client_module) -> None:
    import pathlib

    sample = pathlib.Path(__file__).resolve().parent.parent / "data" / IN_SAMPLE
    response = client_module.post(
        "/api/boq/upload?country=IN",
        files={"file": (IN_SAMPLE, io.BytesIO(sample.read_bytes()), "text/csv")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["country"] == "IN"
    assert body["currency"] == "INR"
    assert body["unclassified_count"] == 2

    result = client_module.post(
        f"/api/boq/{body['upload_id']}/benchmark",
        json={"tender_quarter": "2024Q4", "tpi_series_name": "CPWD", "variance_threshold": 15},
    )
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["currency"] == "INR"
    assert payload["classification_standard"] == "IS 1200 / CPWD DSR"
    assert any("Piling" in w and "CPWD" in w for w in payload["warnings"])
    assert abs(
        payload["totals"]["boq_total"]
        + sum(w["amount"] for w in payload["waterfall"])
        - payload["totals"]["should_cost_total"]
    ) <= 0.01


def test_uploading_an_indian_series_against_a_singapore_boq_fails_clearly(
    client_module, session
) -> None:
    upload_id = _upload_id(session, SG_SAMPLE)
    assert upload_id is not None
    response = client_module.post(
        f"/api/boq/{upload_id}/benchmark",
        json={"tender_quarter": "2024Q4", "tpi_series_name": "CPWD", "variance_threshold": 15},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "CPWD" in detail
    assert "SG" in detail, "the error must say which market the series belongs to"
