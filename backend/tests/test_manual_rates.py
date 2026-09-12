"""Analyst-supplied manual rates: completing the benchmark on every line."""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from app.benchmark import build_benchmark, excluded_sections, compute_sensitivity
from app.models import BoQUpload, BoQItem

SAMPLE = "sample_boq.csv"
IN_SAMPLE = "sample_boq_india.csv"


def _upload_id(session, filename=SAMPLE) -> int:
    return session.scalar(select(BoQUpload.id).where(BoQUpload.filename == filename))


def _items(session, upload_id: int) -> list[BoQItem]:
    return list(
        session.scalars(select(BoQItem).where(BoQItem.upload_id == upload_id).order_by(BoQItem.id))
    )


def _run(session, manual=None, filename=SAMPLE, series="BCA", country="SG", region=None, **kw):
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
        manual_rates=manual,
        **kw,
    )


def _unbenchmarked(result):
    return [l for l in result.lines if not l.is_benchmarked]


def _rates_for(lines, value=50.0):
    return {str(l.item_id): {"base_rate": value, "indexed": True} for l in lines}


# --------------------------------------------------------------------------- #
# Closing the coverage gap
# --------------------------------------------------------------------------- #
def test_the_sample_starts_with_untested_lines(session) -> None:
    result = _run(session)
    missing = _unbenchmarked(result)
    assert len(missing) == 3
    assert result.totals["unbenchmarked_line_count"] == 3
    assert {l.exclusion_reason for l in missing} == {"unit_mismatch", "unclassified_section"}
    # Untested lines carry zero tested variance - that is the gap being closed.
    for line in missing:
        assert line.variance_amount == 0.0
        assert line.basis == "assumed"


def test_supplying_rates_benchmarks_every_line(session) -> None:
    base = _run(session)
    supplied = _rates_for(_unbenchmarked(base))
    after = _run(session, manual=supplied)

    assert not _unbenchmarked(after)
    assert after.totals["unbenchmarked_line_count"] == 0
    assert sum(1 for l in after.lines if l.is_benchmarked) == len(after.lines)


def test_a_manual_rate_is_an_assumption_not_a_measurement(session) -> None:
    base = _run(session)
    supplied = _rates_for(_unbenchmarked(base))
    after = _run(session, manual=supplied)

    for line in after.lines:
        if "manual_rate" in line.flags:
            assert line.basis == "assumed"
            assert line.user_adjusted is True
            assert line.from_library is False
            assert line.exclusion_reason is None
            assert line.provenance["source"] == "Analyst-supplied manual rate"
            assert line.provenance["confidence"] == "analyst"
            assert line.provenance["is_placeholder"] is False
        elif line.is_benchmarked:
            assert line.from_library is True


def test_manual_rates_are_disclosed_in_warnings_and_assumptions(session) -> None:
    base = _run(session)
    after = _run(session, manual=_rates_for(_unbenchmarked(base)))
    joined_warnings = " ".join(after.warnings)
    assert "ANALYST-SUPPLIED" in joined_warnings
    assert "manual_rate" in joined_warnings
    assert after.adjustments_applied["manual_rate_count"] == 3


def test_reconciliation_survives_manual_rates(session) -> None:
    base = _run(session)
    after = _run(session, manual=_rates_for(_unbenchmarked(base), value=1234.5))
    delta = after.totals["boq_total"] + sum(w["amount"] for w in after.waterfall)
    assert abs(delta - after.totals["should_cost_total"]) <= 0.01


def test_a_manual_rate_changes_the_variance(session) -> None:
    base = _run(session)
    missing = _unbenchmarked(base)
    cheap = _run(session, manual=_rates_for(missing, value=1.0))
    dear = _run(session, manual=_rates_for(missing, value=100000.0))
    assert cheap.totals["should_cost_total"] < base.totals["should_cost_total"]
    assert dear.totals["should_cost_total"] > base.totals["should_cost_total"]
    assert dear.totals["should_cost_total"] > cheap.totals["should_cost_total"]


# --------------------------------------------------------------------------- #
# indexed vs not indexed
# --------------------------------------------------------------------------- #
def test_an_indexed_manual_rate_is_indexed_like_a_library_rate(session) -> None:
    base = _run(session)
    # An Unclassified line belongs to no excluded section, so the index applies in full.
    line = [l for l in _unbenchmarked(base) if l.exclusion_reason == "unclassified_section"][0]
    after = _run(session, manual={str(line.item_id): {"base_rate": 100.0, "indexed": True}})
    row = [l for l in after.lines if l.item_id == line.item_id][0]
    assert row.from_library is False
    # 100 base x BCA 2024Q4 ratio 1.392, no scope exclusion
    assert row.adjusted_benchmark_rate == pytest.approx(139.20, abs=0.05)
    assert "not_indexed" not in row.flags


def test_a_scope_excluded_section_cancels_the_index_for_a_manual_rate_too(session) -> None:
    """A supplied rate for an excluded section is still held at base year.

    The section is taken from the series' OWN declared exclusions rather than hardcoded as
    Piling: the demonstration bills are generated from the schedules of rates, so which
    sections they contain - and which of those are excluded - moves with the seed.
    """
    base = _run(session)
    present = {l.smm2_section for l in base.lines}
    excluded = set(
        excluded_sections(base.tpi_series_scope_exclusions, present)
    )
    assert excluded, "the selected series must exclude something this BoQ contains"
    line = [l for l in _unbenchmarked(base) if l.smm2_section in excluded][0]
    after = _run(session, manual={str(line.item_id): {"base_rate": 500.0, "indexed": True}})
    row = [l for l in after.lines if l.item_id == line.item_id][0]
    assert row.scope_excluded is True
    assert row.adjusted_benchmark_rate == pytest.approx(500.0, abs=0.05)
    assert "scope_excluded" in row.flags


def test_an_unindexed_manual_rate_is_taken_as_stated(session) -> None:
    base = _run(session)
    line = _unbenchmarked(base)[0]
    after = _run(session, manual={str(item_id := line.item_id): {"base_rate": 100.0, "indexed": False}})
    row = [l for l in after.lines if l.item_id == item_id][0]
    assert row.adjusted_benchmark_rate == pytest.approx(100.0, abs=0.01)
    assert "not_indexed" in row.flags
    assert row.regional_factor == 1.0


def test_region_applies_to_an_indexed_manual_rate_but_not_an_unindexed_one(session) -> None:
    base = _run(session, filename=IN_SAMPLE, series="WPI-CONST", country="IN", region="DEL")
    line = _unbenchmarked(base)[0]
    delhi_indexed = _run(session, filename=IN_SAMPLE, series="WPI-CONST", country="IN", region="DEL",
                         manual={str(line.item_id): {"base_rate": 1000.0, "indexed": True}})
    mumbai_indexed = _run(session, filename=IN_SAMPLE, series="WPI-CONST", country="IN", region="MUM",
                          manual={str(line.item_id): {"base_rate": 1000.0, "indexed": True}})
    mumbai_stated = _run(session, filename=IN_SAMPLE, series="WPI-CONST", country="IN", region="MUM",
                         manual={str(line.item_id): {"base_rate": 1000.0, "indexed": False}})

    def adj(result):
        return [l for l in result.lines if l.item_id == line.item_id][0].adjusted_benchmark_rate

    assert adj(mumbai_indexed) > adj(delhi_indexed), "Mumbai costs more"
    assert adj(mumbai_stated) == pytest.approx(1000.0), "a stated rate is not regionally re-scaled"


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [0, -5, "", None, "abc"])
def test_unusable_manual_rates_are_ignored(session, bad) -> None:
    base = _run(session)
    line = _unbenchmarked(base)[0]
    after = _run(session, manual={str(line.item_id): {"base_rate": bad, "indexed": True}})
    # The line stays unbenchmarked rather than being benchmarked on nonsense.
    assert after.totals["unbenchmarked_line_count"] == 3
    assert after.adjustments_applied["manual_rate_count"] == 0


def test_a_rate_for_an_already_benchmarked_line_is_accepted(session) -> None:
    base = _run(session)
    library_line = [l for l in base.lines if l.is_benchmarked][0]
    after = _run(session, manual={str(library_line.item_id): {"base_rate": 1234.0, "indexed": False}})
    row = [l for l in after.lines if l.item_id == library_line.item_id][0]
    assert row.adjusted_benchmark_rate == pytest.approx(1234.0, abs=0.01)
    assert "manual_rate" in row.flags
    assert row.from_library is False
    # Exactly one line was overridden; the rest of the library is untouched.
    assert sum(1 for l in after.lines if "manual_rate" in l.flags) == 1
    untouched = [l for l in after.lines if l.is_benchmarked and l.item_id != library_line.item_id]
    assert all(l.from_library for l in untouched)


def test_manual_rates_flow_through_sensitivity(session) -> None:
    base = _run(session)
    supplied = _rates_for(_unbenchmarked(base))
    upload_id = _upload_id(session)
    args = {
        "upload_id": upload_id,
        "items": _items(session, upload_id),
        "tender_quarter": "2024Q4",
        "tpi_series_name": "BCA",
        "country": "SG",
    }
    without = compute_sensitivity(session, **args)
    with_rates = compute_sensitivity(session, manual_rates=supplied, **args)
    assert without["baseline"]["unbenchmarked_line_count"] == 3
    assert with_rates["baseline"]["unbenchmarked_line_count"] == 0
    assert with_rates["baseline"]["should_cost_total"] != without["baseline"]["should_cost_total"]
    # The whole sweep still reconciles through the manual rates.
    assert with_rates["tpi_sweep"]
    assert with_rates["section_tornado"]


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def _upload(client, filename=SAMPLE):
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "data" / filename
    response = client.post(
        "/api/boq/upload?country=SG",
        files={"file": ("manual-rate-test.csv", io.BytesIO(path.read_bytes()), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["upload_id"]


def test_api_accepts_manual_rates_and_closes_the_gap(client_module) -> None:
    upload_id = _upload(client_module)
    body = {"tender_quarter": "2024Q4", "tpi_series_name": "BCA", "variance_threshold": 15}

    plain = client_module.post(f"/api/boq/{upload_id}/benchmark", json=body).json()
    assert plain["totals"]["unbenchmarked_line_count"] == 3

    missing = [l for l in plain["lines"] if not l["is_benchmarked"]]
    supplied = {
        str(l["item_id"]): {"base_rate": 75.0, "indexed": True, "note": "From a comparable project"}
        for l in missing
    }
    filled = client_module.post(
        f"/api/boq/{upload_id}/benchmark", json={**body, "manual_rates": supplied}
    ).json()

    assert filled["totals"]["unbenchmarked_line_count"] == 0
    assert all(l["is_benchmarked"] for l in filled["lines"])
    manual_lines = [l for l in filled["lines"] if "manual_rate" in l["flags"]]
    assert len(manual_lines) == 3
    for line in manual_lines:
        assert line["basis"] == "assumed"
        assert line["from_library"] is False
        assert line["provenance"]["source"] == "Analyst-supplied manual rate"
    assert abs(
        filled["totals"]["boq_total"]
        + sum(w["amount"] for w in filled["waterfall"])
        - filled["totals"]["should_cost_total"]
    ) <= 0.01


def test_api_rejects_a_negative_manual_rate(client_module) -> None:
    upload_id = _upload(client_module)
    response = client_module.post(
        f"/api/boq/{upload_id}/benchmark",
        json={
            "tender_quarter": "2024Q4",
            "tpi_series_name": "BCA",
            "manual_rates": {"123": {"base_rate": -5}},
        },
    )
    assert response.status_code == 422


def test_manual_rates_appear_in_the_exported_report(client_module) -> None:
    import csv

    upload_id = _upload(client_module)
    body = {"tender_quarter": "2024Q4", "tpi_series_name": "BCA", "variance_threshold": 15}
    plain = client_module.post(f"/api/boq/{upload_id}/benchmark", json=body).json()
    supplied = {
        str(l["item_id"]): {"base_rate": 42.0, "indexed": False, "note": "Analyst estimate"}
        for l in plain["lines"]
        if not l["is_benchmarked"]
    }
    response = client_module.post(
        f"/api/boq/{upload_id}/export?format=csv&level=report",
        json={**body, "manual_rates": supplied},
    )
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8"))))
    adjustments = {r["ref"]: r["value"] for r in rows if r["block"] == "adjustment"}
    assert adjustments["manual_rate_count"] == "3"
    sources = {r["value"] for r in rows if r["block"] == "line" and r["item"] == "benchmark_source"}
    assert "Analyst-supplied manual rate" in sources
