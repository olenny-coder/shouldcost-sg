"""API tests - the M3 evidence gate.

Required assertions:
  * totals reconcile within 0.01 SGD
  * at least one line breaches 15%
  * piling + RLB triggers a scope warning
  * export returns non-empty bytes
  * /api/healthz returns 200
"""

from __future__ import annotations

import io
import pathlib

import pytest

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
SAMPLE_BOQ = DATA_DIR / "sample_boq.csv"


def _upload_sample(client, filename: str = "api-test-boq.csv") -> dict:
    payload = SAMPLE_BOQ.read_bytes()
    response = client.post(
        "/api/boq/upload",
        files={"file": (filename, io.BytesIO(payload), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def uploaded(client_module):
    return _upload_sample(client_module)


# --------------------------------------------------------------------------- #
# ETL idempotency
# --------------------------------------------------------------------------- #
def test_etl_is_idempotent_even_with_duplicate_sample_uploads(client_module) -> None:
    """Re-running the ETL must not accumulate rows, even if the API created an
    upload carrying the same filename as the seeded sample."""
    from sqlalchemy import func, select

    from app.db import get_session_factory
    from app.etl import run_etl
    from app.models import BoQUpload, BoQItem

    sample_name = "sample_boq.csv"

    def sample_counts(session):
        uploads = list(session.scalars(select(BoQUpload).where(BoQUpload.filename == sample_name)))
        ids = [u.id for u in uploads]
        items = 0
        if ids:
            items = session.scalar(
                select(func.count()).select_from(BoQItem).where(BoQItem.upload_id.in_(ids))
            )
        return len(uploads), items or 0

    # Create a second upload carrying the same filename as the seeded sample.
    _upload_sample(client_module, filename=sample_name)

    session = get_session_factory()()
    try:
        assert sample_counts(session)[0] == 2, "two uploads now share the sample filename"

        first = run_etl(session=session)
        second = run_etl(session=session)
        assert first == second, "the ETL must be idempotent"
        assert sample_counts(session) == (1, 20), "every duplicate sample upload must be replaced"
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_healthz_returns_200_and_reports_db_connected(client_module) -> None:
    response = client_module.get("/api/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "connected"


def test_root_endpoint_discloses_placeholder_data(client_module) -> None:
    body = client_module.get("/").json()
    assert "PLACEHOLDER" in body["data_notice"]


# --------------------------------------------------------------------------- #
# Upload / classify
# --------------------------------------------------------------------------- #
def test_upload_classifies_every_row(uploaded) -> None:
    assert uploaded["row_count"] == 20
    assert uploaded["unclassified_count"] == 2
    assert uploaded["classified_count"] == 18
    assert uploaded["currency"] == "SGD"
    assert len(uploaded["items"]) == 20
    assert sum(uploaded["counts_by_section"].values()) == 20
    assert uploaded["warnings"], "unclassified lines must be surfaced to the user"


def test_upload_rejects_a_file_without_required_columns(client_module) -> None:
    response = client_module.post(
        "/api/boq/upload",
        files={"file": ("bad.csv", io.BytesIO(b"foo,bar\n1,2\n"), "text/csv")},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Missing required column" in detail
    assert "description" in detail


def test_upload_rejects_a_legacy_xls_file_with_guidance(client_module) -> None:
    response = client_module.post(
        "/api/boq/upload",
        files={"file": ("legacy.xls", io.BytesIO(b"\xd0\xcf\x11\xe0 legacy biff"), "application/vnd.ms-excel")},
    )
    assert response.status_code == 422
    assert "Re-save it as .xlsx or .csv" in response.json()["detail"]


def test_upload_rejects_an_empty_file(client_module) -> None:
    response = client_module.post(
        "/api/boq/upload", files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")}
    )
    assert response.status_code == 400


def test_get_upload_returns_items(client_module, uploaded) -> None:
    response = client_module.get(f"/api/boq/{uploaded['upload_id']}")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 20
    assert body["filename"] == "api-test-boq.csv"


def test_get_missing_upload_returns_404(client_module) -> None:
    response = client_module.get("/api/boq/999999")
    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]


def test_patch_reclassifies_an_item_manually(client_module, uploaded) -> None:
    unclassified = [i for i in uploaded["items"] if i["smm2_section"] == "Unclassified"]
    assert unclassified
    target = unclassified[0]

    response = client_module.patch(
        f"/api/boq/item/{target['id']}", json={"smm2_section": "Waterproofing"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["smm2_section"] == "Waterproofing"
    assert body["classified_by"] == "manual"

    # Put it back so later assertions about the unclassified count stay stable.
    revert = client_module.patch(
        f"/api/boq/item/{target['id']}", json={"smm2_section": "Unclassified"}
    )
    assert revert.status_code == 200


def test_patch_rejects_an_unknown_section(client_module, uploaded) -> None:
    item_id = uploaded["items"][0]["id"]
    response = client_module.patch(f"/api/boq/item/{item_id}", json={"smm2_section": "Nonsense"})
    assert response.status_code == 422
    assert "Unknown section" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Benchmark - the core gate
# --------------------------------------------------------------------------- #
def test_benchmark_totals_reconcile_within_one_cent(client_module, uploaded) -> None:
    response = client_module.post(
        f"/api/boq/{uploaded['upload_id']}/benchmark",
        json={"tender_quarter": "2024Q4", "tpi_series_name": "BCA", "variance_threshold": 15.0},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    totals = body["totals"]
    waterfall_sum = sum(component["amount"] for component in body["waterfall"])
    assert abs(totals["boq_total"] + waterfall_sum - totals["should_cost_total"]) <= 0.01

    # Lines and sections must also reconcile to the totals.
    assert abs(sum(l["boq_amount"] for l in body["lines"]) - totals["boq_total"]) <= 0.01
    assert abs(sum(s["boq_amount"] for s in body["sections"]) - totals["boq_total"]) <= 0.01
    assert abs(
        sum(s["should_cost_amount"] for s in body["sections"]) - totals["should_cost_total"]
    ) <= 0.01


def test_benchmark_flags_lines_breaching_the_threshold(client_module, uploaded) -> None:
    body = client_module.post(
        f"/api/boq/{uploaded['upload_id']}/benchmark",
        json={"tender_quarter": "2024Q4", "tpi_series_name": "BCA", "variance_threshold": 15.0},
    ).json()
    breaches = [
        l for l in body["lines"]
        if "over_threshold" in l["flags"] or "under_threshold" in l["flags"]
    ]
    assert len(breaches) >= 1, "at least one line must breach the 15% threshold"
    assert body["totals"]["breached_line_count"] == len(breaches)
    for line in breaches:
        assert abs(line["variance_pct"]) >= 15.0


def test_raising_the_threshold_reduces_the_breach_count(client_module, uploaded) -> None:
    base = {"tender_quarter": "2024Q4", "tpi_series_name": "BCA"}
    strict = client_module.post(
        f"/api/boq/{uploaded['upload_id']}/benchmark", json={**base, "variance_threshold": 15.0}
    ).json()
    loose = client_module.post(
        f"/api/boq/{uploaded['upload_id']}/benchmark", json={**base, "variance_threshold": 50.0}
    ).json()
    assert loose["totals"]["breached_line_count"] < strict["totals"]["breached_line_count"]


def test_piling_with_rlb_triggers_a_named_scope_warning(client_module, uploaded) -> None:
    body = client_module.post(
        f"/api/boq/{uploaded['upload_id']}/benchmark",
        json={"tender_quarter": "2024Q4", "tpi_series_name": "RLB", "variance_threshold": 15.0},
    ).json()
    joined = " ".join(body["warnings"])
    assert "Scope exclusion" in joined
    assert "Piling" in joined
    assert "RLB" in joined

    piling = [l for l in body["lines"] if l["smm2_section"] == "Piling" and l["is_benchmarked"]]
    assert piling
    assert any(l["scope_excluded"] and l["scope_factor"] != 1.0 for l in piling)
    assert any("scope_factor" in a for a in body["assumptions"])


def test_benchmark_response_distinguishes_measured_derived_assumed(client_module, uploaded) -> None:
    body = client_module.post(
        f"/api/boq/{uploaded['upload_id']}/benchmark",
        json={"tender_quarter": "2024Q4", "tpi_series_name": "BCA", "variance_threshold": 15.0},
    ).json()
    bases = {l["basis"] for l in body["lines"]}
    assert "derived" in bases
    assert "assumed" in bases
    assert all(l["basis"] in {"measured", "derived", "assumed"} for l in body["lines"])
    assert body["assumptions"], "apportioned figures must be disclosed in assumptions[]"
    assert any(w["basis"] == "assumed" for w in body["waterfall"])
    for line in body["lines"]:
        if line["is_benchmarked"]:
            prov = line["provenance"]
            assert prov["is_placeholder"] is True
            assert prov["source"] and prov["source_date"] and prov["base_year"]
            assert prov["scope_inclusions"] and prov["scope_exclusions"]
            assert prov["confidence"]


def test_benchmark_rejects_an_unknown_series(client_module, uploaded) -> None:
    response = client_module.post(
        f"/api/boq/{uploaded['upload_id']}/benchmark",
        json={"tender_quarter": "2024Q4", "tpi_series_name": "NOPE", "variance_threshold": 15.0},
    )
    assert response.status_code == 400
    assert "NOPE" in response.json()["detail"]


def test_benchmark_rejects_a_malformed_quarter(client_module, uploaded) -> None:
    response = client_module.post(
        f"/api/boq/{uploaded['upload_id']}/benchmark",
        json={"tender_quarter": "2024-Q4", "tpi_series_name": "BCA", "variance_threshold": 15.0},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def test_csv_export_returns_non_empty_bytes(client_module, uploaded) -> None:
    response = client_module.get(f"/api/boq/{uploaded['upload_id']}/export?format=csv")
    assert response.status_code == 200
    assert len(response.content) > 0
    assert "text/csv" in response.headers["content-type"]
    assert b"description" in response.content
    assert "attachment; filename=" in response.headers["content-disposition"]


def test_xlsx_export_returns_non_empty_bytes(client_module, uploaded) -> None:
    response = client_module.get(f"/api/boq/{uploaded['upload_id']}/export?format=xlsx")
    assert response.status_code == 200
    assert len(response.content) > 0
    assert response.content[:2] == b"PK", "xlsx is a zip container"
    assert "spreadsheetml" in response.headers["content-type"]


def test_export_can_embed_benchmark_columns(client_module, uploaded) -> None:
    response = client_module.get(
        f"/api/boq/{uploaded['upload_id']}/export?format=csv"
        "&tender_quarter=2024Q4&tpi_series_name=BCA&variance_threshold=15"
    )
    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert "adjusted_benchmark_rate" in text
    assert "variance_pct" in text
    assert "basis" in text


def test_export_rejects_an_unknown_format(client_module, uploaded) -> None:
    response = client_module.get(f"/api/boq/{uploaded['upload_id']}/export?format=pdf")
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Indices
# --------------------------------------------------------------------------- #
def test_tpi_index_endpoint_filters_by_series(client_module) -> None:
    response = client_module.get("/api/indices/tpi?series=BCA")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 8
    assert all(r["series_name"] == "BCA" for r in rows)
    assert all(r["base_year"] == 2010 for r in rows)
    assert all(r["is_placeholder"] is True for r in rows)
    assert all(r["source_url"] for r in rows)
    assert all(r["replace_with"].startswith("# TODO:") for r in rows)


def test_tpi_index_endpoint_filters_by_quarter_range(client_module) -> None:
    rows = client_module.get(
        "/api/indices/tpi?series=BCA&from_quarter=2024Q1&to_quarter=2024Q2"
    ).json()
    assert [r["quarter"] for r in rows] == ["2024Q1", "2024Q2"]


def test_material_index_endpoint_filters(client_module) -> None:
    rows = client_module.get("/api/indices/materials?country=SG&material=steel_rebar").json()
    assert len(rows) == 12
    assert all(r["material"] == "steel_rebar" for r in rows)
    # The Singapore series is REAL BCA data published annually, so it is not a placeholder.
    assert all(r["is_placeholder"] is False for r in rows)
    assert all(r["frequency"] == "annual" for r in rows)
    assert all(r["currency"] == "SGD" for r in rows)
    windowed = client_module.get(
        "/api/indices/materials?country=SG&material=cement&from_month=2022&to_month=2024"
    ).json()
    assert [r["month"] for r in windowed] == ["2022", "2023", "2024"]


def test_benchmark_rate_library_exposes_full_provenance(client_module) -> None:
    rows = client_module.get("/api/indices/benchmark-rates").json()
    assert len(rows) == 10
    for row in rows:
        assert row["is_placeholder"] is True
        assert row["source"] and row["source_date"]
        assert row["scope_inclusions"] and row["scope_exclusions"]
        assert row["confidence"] in {"high", "medium", "low"}
        assert row["base_year"] == 2010
        assert row["country"] == "SG"
        assert row["currency"] == "SGD"
        assert row["replace_with"].startswith("# TODO:")


def test_classifier_rules_are_auditable(client_module) -> None:
    body = client_module.get("/api/indices/classifier-rules").json()
    assert len(body["rules"]) == 10
    assert body["unclassified_label"] == "Unclassified"


# --------------------------------------------------------------------------- #
# CORS / security posture
# --------------------------------------------------------------------------- #
def test_cors_never_uses_a_wildcard(client_module) -> None:
    config = client_module.get("/api/config").json()
    assert "*" not in config["cors_allow_origins"]


def test_cors_preflight_allows_a_configured_origin(client_module) -> None:
    """Preflight must succeed for an origin the app actually allows.

    The allowed list is environment-dependent: in development it is the localhost
    origins, in production it is FRONTEND_URL alone. Reading it from /api/config
    keeps this test honest in both, instead of asserting a hardcoded dev origin
    that production is right to reject.
    """
    allowed = client_module.get("/api/config").json()["cors_allow_origins"]
    assert allowed, "some origin must be allowed"
    origin = allowed[0]
    response = client_module.options(
        "/api/boq/upload",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_cors_preflight_rejects_an_unlisted_origin(client_module) -> None:
    response = client_module.options(
        "/api/boq/upload",
        headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers

