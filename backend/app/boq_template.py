"""Downloadable BoQ templates, built from the schedule of rates.

A template is the fastest way to remove upload friction: the analyst fills in the sheet the
parser already understands, in the vocabulary of their own market, rather than guessing
column names.

The template is not three example rows any more. It is the **whole schedule of rates for
the market** - every BCA line for Singapore, every CPWD DSR line for India - pre-filled so
the file uploads and analyses immediately:

  sor_code      the schedule item code. Blank means the analyst added the line, and the
                loaded schedule therefore carries no rate for it, so it needs a manual rate
  description   verbatim from the schedule
  unit          the schedule unit, converted to the app's vocabulary (sqm -> m2, kg -> t)
  quantity      ZERO. A schedule of rates has no quantities; only a bill does. An untouched
                template therefore totals zero, which is deliberate: it cannot be mistaken
                for a priced bill
  rate          the schedule's own 2026 rate, so a filled quantity analyses straight away.
                It is the SCHEDULE rate, not a tendered rate, and every row says so

Two sheets are produced for XLSX: the BoQ itself, and the instructions.
"""

from __future__ import annotations

import csv
import functools
import io
from pathlib import Path

import pandas as pd

from .countries import Country, get_country

HEADERS = ["sor_code", "description", "section", "unit", "quantity", "rate",
           "is_placeholder", "replace_with"]

CATALOGUE = Path(__file__).resolve().parent.parent / "data" / "sor_items.csv"

RATE_TODO = (
    "# TODO: the rate is the SCHEDULE rate for this item, not a tendered rate. Replace it "
    "with your own, and set quantity, before reading the variance."
)
ADDED_TODO = (
    "# TODO: this line is NOT in the loaded schedule of rates (sor_code is blank), so the app "
    "has no benchmark for it. Supply a rate in the app's Coverage panel to complete the "
    "should-cost, or reclassify it from the Variance table."
)


@functools.lru_cache(maxsize=4)
def catalogue_rows(country_code: str) -> tuple[tuple[str, str, str, str, float], ...]:
    """(sor_code, description, section, unit, rate) for every schedule item in a market.

    Cached: the file is ~400 KB and the template endpoint is hit repeatedly.

    `section` is the app's own classification of the description, blank when the schedule
    item falls outside the ten canonical sections (glazing, painting, metalwork, roofing,
    floor and ceiling finishes). It is in the template so the analyst can FILTER: the rows
    with a section are the ones the library can price, and the blank ones are the lines that
    will need a manual rate. Without it, a 1,846-row sheet is unnavigable.
    """
    if not CATALOGUE.exists():
        raise FileNotFoundError(
            f"Schedule catalogue {CATALOGUE} not found. Build it with "
            f"python tools/build_sor_items.py."
        )
    out = []
    with CATALOGUE.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["country"] != country_code:
                continue
            try:
                rate = float(row["rate_2026"])
            except (TypeError, ValueError):
                continue
            out.append((row["sor_code"], row["description"], row["section"], row["unit"], rate))
    return tuple(out)


def _item_rows(country: Country) -> list[list]:
    return [
        [code, description, section, unit, 0, rate, "true", RATE_TODO]
        for code, description, section, unit, rate in catalogue_rows(country.code)
    ]


def coverage_counts(country_code: str) -> tuple[int, int]:
    """(items the library can price, items it cannot) for one market's catalogue."""
    rows = catalogue_rows(country_code)
    priceable = sum(1 for row in rows if row[2])
    return priceable, len(rows) - priceable


def _instructions(country: Country) -> list[list[str]]:
    items = len(catalogue_rows(country.code))
    priceable, unpriceable = coverage_counts(country.code)
    return [
        ["shouldcost BoQ template"],
        [""],
        ["Country", country.name],
        ["Currency", f"{country.currency} ({country.currency_symbol})"],
        ["Measurement standard", country.measurement_standard],
        ["Measurement note", country.measurement_note],
        ["Unit convention", country.unit_convention],
        [""],
        ["HOW TO USE THIS TEMPLATE"],
        ["1", f"The BoQ sheet lists ALL {items} items in the schedule of rates for this market, so you can fill in quantities against the real vocabulary instead of retyping descriptions."],
        ["2", "DELETE THE ROWS YOU DO NOT NEED. That is the fastest route to a short bill - an untouched template is a catalogue, not a tender."],
        ["2a", f"FILTER ON THE section COLUMN FIRST. {priceable} of the {items} items fall in a section the rate library covers, so the app can benchmark those straight away. The other {unpriceable} are trades outside the library's ten sections (glazing, painting, metalwork, roofing, finishes) and will be reported as needing a manual rate."],
        ["3", "Set quantity on the rows you keep. Every row starts at 0, so an untouched file totals zero on purpose and cannot be mistaken for a priced bill."],
        ["4", "Replace rate with YOUR tendered rate. It is pre-filled with the schedule rate so the file analyses straight away, and every row is marked is_placeholder = true until you replace it."],
        ["5", "To add a line that is NOT in the schedule, leave sor_code blank. The app reports those lines separately: the schedule has no rate for them, so they need a manual rate before the should-cost is complete."],
        ["6", "description and unit are what the classifier reads. Use the wording of your own bill if it differs - the schedule wording classifies accurately because the rules were tuned on it, and anything the classifier cannot place is flagged Unclassified for you to reclassify."],
        [""],
        ["COLUMN REFERENCE"],
        ["sor_code", "The schedule item code this line was quoted from. Leave blank for a line you added; the app treats a blank code as 'not in the schedule' and flags it as needing input."],
        ["description", "Free text. Classified into a section by keyword. Unmatched lines are flagged Unclassified and can be reclassified in the app."],
        ["section", "The app's classification of this description, supplied so you can filter. BLANK means the schedule item is outside the library's ten sections and cannot be benchmarked without a manual rate. Ignored on upload - the app reclassifies from the description."],
        ["unit", "One of m, m2, m3, t, item. Units must match the benchmark rate unit for the section or the line is excluded from variance testing."],
        ["quantity", "Measured quantity. Zero in the template - you must set it."],
        ["rate", f"Tendered rate per unit, in {country.currency}. Pre-filled with the schedule rate; replace it with your own."],
        ["is_placeholder", "true when the row is not a real tendered line. Leave it true until you have replaced the rate and quantity."],
        ["replace_with", "What must replace this row before it is used for a real decision."],
        [""],
        ["SHOULD-COST COMPLETENESS"],
        ["", "A complete should-cost needs a rate for every line. Lines the library cannot price are reported in the app rather than silently dropped:"],
        ["Unclassified", "the classifier placed no section, so there is no benchmark rate. Reclassify it, or supply a manual rate."],
        ["No library rate", "the section has no rate in the library for this market."],
        ["Unit mismatch", "the line's unit differs from the library rate's unit, so the comparison would be meaningless. Re-measure it in the library unit or supply a manual rate."],
        ["Not in the schedule", "sor_code is blank, so the line came from your own bill and the loaded schedule cannot price it. Supply a manual rate."],
        [""],
        ["CREDIBLE SOURCES FOR THIS MARKET"],
        *[[s.name, s.url, s.what] for s in country.sources],
    ]


def template_csv_bytes(country_code: str) -> bytes:
    country = get_country(country_code)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADERS)
    writer.writerows(_item_rows(country))
    return buffer.getvalue().encode("utf-8")


def template_xlsx_bytes(country_code: str) -> bytes:
    country = get_country(country_code)
    buffer = io.BytesIO()
    boq = pd.DataFrame(_item_rows(country), columns=HEADERS)
    instructions = pd.DataFrame(_instructions(country))
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        boq.to_excel(writer, index=False, sheet_name="BoQ")
        instructions.to_excel(writer, index=False, header=False, sheet_name="Instructions")
    buffer.seek(0)
    return buffer.getvalue()


def template_filename(country_code: str, fmt: str) -> str:
    country = get_country(country_code)
    return f"shouldcost-boq-template-{country.code.lower()}.{fmt}"
