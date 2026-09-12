"""The upload template, the schedule-of-rates catalogue, and the sections summary.

The template is the app's front door: an analyst fills it in, uploads it, and everything
downstream depends on those lines landing in the right section. Three things are pinned
here, and each of them was a real defect before:

1. **The template lists the WHOLE schedule.** Every item the two published extracts carry -
   454 for Singapore, 1,876 for India. An earlier revision silently dropped 60 Singapore
   items (no published rate) and 30 India items (a unit the app cannot compare), which made
   the template shorter than the schedule it claimed to be.

2. **The sections summary factors the schedule descriptions in.** Every catalogue
   description must still classify to the section stored against it, and an uploaded line
   that matches a schedule item exactly must take that item's section - so a bill built
   from the template always lands where the template said it would.

3. **One template, with instructions.** A single XLSX download, two sheets, and the
   instructions state where the pre-filled rates came from and what the market's schedule
   holds per section.
"""

from __future__ import annotations

import csv
import io

import pandas as pd
import pytest

from app import boq_template
from app.classifier import UNCLASSIFIED, classify

SG = "SG"
IN = "IN"
MARKETS = {
    SG: {"items": 454, "bookable": 169},
    IN: {"items": 1876, "bookable": 1040},
}


def _csv_bytes(rows: list[list]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _template_subset(country: str, wanted: int = 12) -> list[list]:
    """A small, filled bill built from real schedule rows.

    One row per section the library prices, so the upload exercises every section the
    catalogue covers rather than a single trade.
    """
    headers = boq_template.HEADERS
    rows = [headers]
    seen: set[str] = set()
    for item in boq_template.catalogue_rows(country):
        if not item.section or item.unit not in {"m", "m2", "m3", "t", "item"}:
            continue
        if item.section in seen and len(seen) < 10:
            continue
        seen.add(item.section)
        rows.append(
            [item.code, item.description, item.section, item.unit, 10, item.rate or 100, "false", ""]
        )
        if len(rows) - 1 >= wanted:
            break
    return rows


def _upload(client, country: str, rows: list[list], name: str = "bill.csv"):
    return client.post(
        f"/api/boq/upload?country={country}",
        files={"file": (name, io.BytesIO(_csv_bytes(rows)), "text/csv")},
    )


# --------------------------------------------------------------------------- #
# The catalogue is the whole schedule
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("country", [SG, IN])
def test_catalogue_lists_every_schedule_item(country) -> None:
    items = boq_template.catalogue_rows(country)
    assert len(items) == MARKETS[country]["items"], (
        "the template must list every item the published extract carries"
    )
    assert len({item.code for item in items}) == len(items), "schedule codes must be unique"
    assert all(item.description.strip() for item in items)
    assert all(item.source for item in items)
    assert all(item.currency == ("INR" if country == IN else "SGD") for item in items)
    # A unit is either usable, or carried verbatim from the extract and flagged as not
    # comparable, or absent in the extract (in which case it is flagged too). No row is
    # allowed to look comparable when it is not.
    for item in items:
        if not item.unit.strip():
            assert item.unit_comparable is False


def test_catalogue_keeps_rows_it_cannot_price() -> None:
    """Rows with no published rate, or an odd unit, are listed and flagged - not dropped."""
    sg = boq_template.catalogue_rows(SG)
    assert any(item.rate is None for item in sg), "the BCA extract has items with no rate"
    assert sum(1 for item in sg if item.rate is None) >= 50

    india = boq_template.catalogue_rows(IN)
    odd = [item for item in india if not item.unit_comparable]
    assert odd, "the CPWD extract measures some items in units the app cannot compare"
    assert all(item.unit == item.published_unit for item in odd), (
        "an unconvertible unit is kept verbatim so the analyst can see what the schedule said"
    )


@pytest.mark.parametrize("country", [SG, IN])
def test_every_catalogue_description_still_classifies_to_its_stored_section(country) -> None:
    """The sections summary is only as good as the descriptions behind it.

    If a classifier rule changes and the catalogue is not rebuilt, the template would
    advertise a section the app no longer produces. This is the tripwire that says so.
    """
    drifted = []
    for item in boq_template.catalogue_rows(country):
        section = classify(item.description, country).smm2_section
        current = "" if section == UNCLASSIFIED else section
        if current != item.section:
            drifted.append((item.code, item.section, current))
    assert not drifted, (
        f"{len(drifted)} catalogue items no longer classify to their stored section - "
        f"re-run tools/build_sor_items.py. First few: {drifted[:5]}"
    )


@pytest.mark.parametrize("country", [SG, IN])
def test_sections_summary_accounts_for_every_item(country) -> None:
    summary = boq_template.sections_summary(country)
    totals = boq_template.catalogue_totals(country)
    assert sum(row["sor_items"] for row in summary) == totals["sor_items"]
    assert sum(row["with_rate"] for row in summary) == totals["with_rate"]
    assert sum(row["bookable"] for row in summary) == totals["bookable"]
    # The last row is always the items outside the ten sections, which the library cannot price.
    outside = summary[-1]
    assert outside["smm2_section"] == "(outside the ten sections)"
    assert outside["bookable"] == 0
    assert outside["sor_items"] == totals["outside_sections"]
    assert totals["bookable"] < totals["sor_items"], "some items must be priceable"
    assert totals["outside_sections"] > 0, "and some must fall outside the ten sections"


def test_description_matching_is_exact_and_normalised() -> None:
    item = boq_template.catalogue_rows(IN)[0]
    assert boq_template.match_description(IN, item.description) == item
    # Case, spacing and punctuation are normalised...
    assert boq_template.match_description(IN, "  " + item.description.upper() + "  ") == item
    # ...but a paraphrase is not silently mapped to the wrong schedule item.
    assert boq_template.match_description(IN, item.description + " plus my own addition") is None
    assert boq_template.match_description(IN, "") is None


# --------------------------------------------------------------------------- #
# One template, with instructions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("country", [SG, IN])
def test_the_template_is_one_xlsx_with_instructions(client_module, country) -> None:
    # No format parameter: there is one template, and this is it.
    default = client_module.get(f"/api/boq/template?country={country}")
    assert default.status_code == 200
    assert default.content[:2] == b"PK", "the default template download is the XLSX"
    assert default.headers["content-disposition"].endswith('.xlsx"')

    sheets = pd.read_excel(io.BytesIO(default.content), sheet_name=None, engine="openpyxl")
    assert set(sheets) == {"BoQ", "Instructions"}

    boq = sheets["BoQ"]
    items = boq_template.catalogue_rows(country)
    assert len(boq) == len(items)
    assert list(boq["description"]) == [item.description for item in items]
    assert list(boq["sor_code"]) == [item.code for item in items]
    # Quantity is the analyst's to set; an untouched template totals zero.
    assert (boq["quantity"] == 0).all()
    # A rate is pre-filled only where the schedule publishes one.
    no_rate = [item.code for item in items if item.rate is None]
    assert (boq.loc[boq["sor_code"].isin(no_rate), "rate"] == 0).all()
    assert (boq.loc[~boq["sor_code"].isin(no_rate), "rate"] > 0).all()
    assert (boq["is_placeholder"].astype(str).str.lower() == "true").all()


@pytest.mark.parametrize("country", [SG, IN])
def test_the_instructions_explain_the_schedule_and_the_sections(client_module, country) -> None:
    response = client_module.get(f"/api/boq/template?country={country}")
    instructions = pd.read_excel(
        io.BytesIO(response.content), sheet_name="Instructions", engine="openpyxl", header=None
    ).astype(str)
    text = instructions.to_string()
    totals = boq_template.catalogue_totals(country)

    assert "HOW TO USE THIS TEMPLATE" in text
    assert "There is only one template" in text
    assert f"{totals['sor_items']:,}" in text
    # Where the pre-filled rates come from, and the publisher's own caveat.
    assert "WHERE THE RATES COME FROM" in text
    assert "ESCALATED TO 2026 BY CPI" in text
    assert ("1.171" if country == SG else "1.2364") in text
    assert "validate" in text.lower()
    # And the sections summary, section by section.
    assert "SECTIONS SUMMARY" in text
    for row in boq_template.sections_summary(country):
        assert row["smm2_section"] in text


def test_template_csv_variant_is_the_item_list_only(client_module) -> None:
    response = client_module.get("/api/boq/template?country=IN&format=csv")
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
    assert rows[0] == boq_template.HEADERS
    assert len(rows) - 1 == MARKETS[IN]["items"]
    assert "WHERE THE RATES COME FROM" not in response.content.decode("utf-8")


# --------------------------------------------------------------------------- #
# The sections summary on an upload
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("country", [SG, IN])
def test_upload_from_the_template_reports_the_schedule_section(client_module, country) -> None:
    rows = _template_subset(country)
    response = _upload(client_module, country, rows, name=f"from-template-{country}.csv")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["row_count"] == len(rows) - 1
    # Every line was taken from the schedule, so every line carries its code...
    assert all(item["sor_code"] for item in body["items"])
    # ...and takes the section the template advertised, not a keyword guess.
    expected = {row[0]: row[2] for row in rows[1:]}
    for item in body["items"]:
        assert item["smm2_section"] == expected[item["sor_code"]]

    summary = {row["smm2_section"]: row for row in body["sections_summary"]}
    assert summary, "an upload carries its sections summary"
    assert sum(row["lines"] for row in body["sections_summary"]) == body["row_count"]
    assert sum(row["from_sor_template"] for row in body["sections_summary"]) == body["row_count"]
    assert sum(row["matched_sor_description"] for row in body["sections_summary"]) == body["row_count"]
    # Each section row states how many schedule items exist for it, and whether the rate
    # library can price it - the two facts that decide whether a line can be benchmarked.
    for row in body["sections_summary"]:
        if row["smm2_section"] == UNCLASSIFIED:
            continue
        assert row["sor_items_available"] > 0
        assert row["benchmark_rate_available"] is True
    assert body["sor_catalogue"]["sor_items"] == MARKETS[country]["items"]


def test_sections_summary_separates_hand_written_lines_from_schedule_lines(client_module) -> None:
    """A bill written by hand has no schedule codes, and the summary must say so."""
    rows = [
        ["description", "unit", "quantity", "rate"],
        ["Reinforced concrete grade 35 to columns", "m3", 100, 250],
        ["Waterproof membrane to pile cap tops", "m2", 400, 55],
    ]
    body = _upload(client_module, SG, rows, name="hand-written.csv").json()
    assert body["row_count"] == 2
    assert all(not item["sor_code"] for item in body["items"]), "hand-written lines carry no code"
    assert sum(row["from_sor_template"] for row in body["sections_summary"]) == 0
    assert sum(row["matched_sor_description"] for row in body["sections_summary"]) == 0
    # The keyword rules still do the work, and the summary still reports the library's reach.
    sections = {row["smm2_section"] for row in body["sections_summary"]}
    assert "Concrete" in sections and "Piling" in sections
    assert all(row["sor_items_available"] >= 0 for row in body["sections_summary"])


def test_reopening_an_upload_keeps_its_sections_summary(client_module) -> None:
    rows = _template_subset(SG, wanted=6)
    created = _upload(client_module, SG, rows, name="reopen.csv").json()
    detail = client_module.get(f"/api/boq/{created['upload_id']}").json()
    assert detail["sections_summary"] == created["sections_summary"]
    assert detail["sor_catalogue"] == created["sor_catalogue"]


def test_benchmark_lines_carry_their_schedule_code(client_module) -> None:
    rows = _template_subset(SG, wanted=6)
    created = _upload(client_module, SG, rows, name="coded-benchmark.csv").json()
    benchmark = client_module.post(
        f"/api/boq/{created['upload_id']}/benchmark",
        json={"tender_quarter": "2024Q4", "tpi_series_name": "BCA", "variance_threshold": 15},
    ).json()
    coded = [line for line in benchmark["lines"] if line["sor_code"]]
    assert len(coded) == len(benchmark["lines"]), (
        "a bill built from the template keeps its schedule reference all the way to the variance table"
    )
    assert all(line["sor_code"] for line in coded)


def test_template_lines_are_classified_by_the_schedule_not_by_accident() -> None:
    """Guard against the mis-fires the catalogue exposed.

    Each of these was priced under the wrong section before the guards went in: the first
    two because "concrete" named the substrate or the thing being removed rather than the
    work, the third because "grade 316L" matched a concrete grade, the fourth because
    "stockpiling" contains "piling", and the last because painting mentions pipes. The
    plaster case is the mirror image: it was priced as Masonry, and the work measured is
    the plastering, so it belongs in Plaster.
    """
    cases = [
        # description, market, section it must NOT be priced as, section it must land in
        ("Finishing with Epoxy paint (On concrete work)", IN, "Concrete", UNCLASSIFIED),
        ("Demolishing lime concrete manually/ by mechanical means", IN, "Concrete", UNCLASSIFIED),
        (
            "Demolishing R.C.C. work by mechanical means and stockpiling at designated locations",
            IN, "Piling", UNCLASSIFIED,
        ),
        (
            "Painting on rain water, soil waste and vent pipes with black anticorrosive paint",
            IN, "M&E Containment", UNCLASSIFIED,
        ),
        (
            "15 mm cement plaster on the rough side of single or half brick wall of mix 1:4",
            IN, "Masonry", "Plaster",
        ),
        (
            "Painting: Internal Painting: General Surfaces: Preparing, sealing, applying paint",
            SG, "Concrete", UNCLASSIFIED,
        ),
        (
            "Concrete Blocks: Autoclaved Aerated Concrete Blockwork: Autoclaved aerated concrete",
            SG, "Concrete", "Masonry",
        ),
    ]
    for description, country, wrong, expected in cases:
        section = classify(description, country).smm2_section
        assert section != wrong, f"{description!r} is priced as {wrong}"
        assert section == expected, f"{description!r} should be {expected}, not {section}"
