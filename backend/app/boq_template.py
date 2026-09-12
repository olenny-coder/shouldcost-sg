"""The single downloadable BoQ template, built from the schedule of rates.

There is ONE template per market, and it carries its instructions with it: the XLSX
has a `BoQ` sheet and an `Instructions` sheet. The instructions sheet is not optional
decoration - the template is a 450 to 1,900 row catalogue, and a spreadsheet that
size is unusable without an explanation of what the columns are, which rows the rate
library can price, and where the pre-filled rates came from.

WHAT THE ANALYST GETS
---------------------
  sor_code      the schedule item code, so a line can be traced back to the schedule
  description   the schedule's own wording, verbatim
  section       the app's classification of that description, supplied so the sheet
                can be FILTERED: rows with a section are priced by the rate library,
                blank rows are trades outside its ten sections (painting, glazing,
                metalwork, roofing, joinery, finishes, demolition) and need a manual
                rate. Ignored on upload - the app classifies from the description
  unit          the schedule unit converted to the app's vocabulary (sqm -> m2, kg -> t)
  quantity      ZERO. A schedule of rates has no quantities; only a bill does. An
                untouched template therefore totals zero and cannot be mistaken for a
                priced bill
  rate          the schedule's rate escalated to the quarter the library is stated at, so a
                filled quantity analyses immediately. It is the same rate the library prices
                that section with, from a stated source and at a stated quarter - so the sheet
                carries no placeholder flag and no "replace with real data" column. An analyst
                with tendered prices overwrites `rate`; one without can price with the library
                as it stands.

EVERY SCHEDULE DESCRIPTION IS LISTED
------------------------------------
The catalogue is built by tools/build_sor_items.py from the two published extracts and
contains every item they carry - 454 for Singapore, 1,876 for India (2,330 in total).
Rows whose rate is missing from the extract, or whose unit the app cannot compare, are
kept and flagged per row rather than dropped: the whole point of the template is that
the analyst finds the item they are pricing.

WHERE THE PRE-FILLED RATES COME FROM
------------------------------------
    Singapore  BCA Schedule of Rates, May 2022, escalated x1.171 (CPI 2022 -> 2026)
    India      CPWD Delhi Schedule of Rates 2021 Vol-II, escalated x1.2364 (2021 -> 2026)

Both extracts carry their own caveat - "this is an estimate; validate with current
market quotations and construction-specific indices" - and the instructions sheet
repeats it. The escalation is the same technique the app uses to carry a stale index
to the tender quarter, and it is disclosed here for the same reason.
"""

from __future__ import annotations

import csv
import functools
import io
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .classifier import SMM2_SECTIONS, UNCLASSIFIED, classify
from .countries import Country, get_country

HEADERS = [
    "sor_code", "description", "section",
    # UOM is the unit the RATE is per, and it is the column the parser reads (it accepts
    # "unit" and "uom" as the same thing). published_uom is what the schedule itself
    # measured, so a conversion is visible rather than silent; uom_note says so in words
    # and flags the rows the app cannot compare at all.
    "UOM", "published_uom", "uom_note",
    "quantity", "rate", "currency",
]

# The five UOMs the app compares, what each means, and the published spellings each one
# absorbs. Shown in the instructions sheet as a legend so "m2" is never a guess.
UOM_LEGEND: tuple[tuple[str, str, str], ...] = (
    ("m", "linear metre", "m, metre, lm, rm, running metre"),
    ("m2", "square metre", "m2, sqm, square metre, m^2"),
    ("m3", "cubic metre", "m3, cum, cubic metre, m^3"),
    ("t", "tonne", "t, tonne, ton, mt, kg (converted: 1 kg = 0.001 t)"),
    ("item", "number, or a lump sum", "item, no, nos, nr, each, sum, lsum"),
)

CATALOGUE = Path(__file__).resolve().parent.parent / "data" / "sor_items.csv"

# The rate this template pre-fills IS the rate library for the section: the published schedule
# rate escalated to the library quarter. It is a real, sourced rate, so the sheet does not
# carry a placeholder flag or a "replace this with real data" column - an analyst who has
# tendered prices overwrites `rate`, and one who does not can price with the library as it
# stands.


@dataclass(frozen=True)
class SorItem:
    """One schedule-of-rates item, as committed in data/sor_items.csv."""

    country: str
    code: str
    description: str
    section: str
    unit: str
    published_unit: str
    unit_comparable: bool
    rate: float | None
    currency: str
    source: str

    @property
    def bookable(self) -> bool:
        """True when the line can be compared: a canonical section and a comparable unit."""
        return bool(self.section) and self.unit_comparable


@functools.lru_cache(maxsize=4)
def catalogue(country_code: str) -> tuple[SorItem, ...]:
    """Every schedule item for a market, in schedule order. Cached: ~500 KB of CSV."""
    if not CATALOGUE.exists():
        raise FileNotFoundError(
            f"Schedule catalogue {CATALOGUE} not found. Build it with "
            f"python tools/build_sor_items.py."
        )
    out: list[SorItem] = []
    with CATALOGUE.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["country"] != country_code:
                continue
            try:
                rate = float(row["rate_2026"]) if row["rate_2026"] else None
            except (TypeError, ValueError):
                rate = None
            out.append(
                SorItem(
                    country=row["country"],
                    code=row["sor_code"],
                    description=row["description"],
                    section=row["section"],
                    unit=row["unit"],
                    published_unit=row["published_unit"],
                    unit_comparable=str(row.get("unit_comparable", "true")).lower() != "false",
                    rate=rate,
                    currency=row["currency"],
                    source=row["source"],
                )
            )
    return tuple(out)


def catalogue_rows(country_code: str) -> tuple[SorItem, ...]:
    """Alias kept for the callers that only need the items."""
    return catalogue(country_code)


# --------------------------------------------------------------------------- #
# Description matching: the schedule wording, factored into classification
# --------------------------------------------------------------------------- #
_NORMALISE = re.compile(r"[^a-z0-9]+")


def _normalise(description: str) -> str:
    return _NORMALISE.sub(" ", (description or "").lower()).strip()


@functools.lru_cache(maxsize=4)
def _by_description(country_code: str) -> dict[str, SorItem]:
    return {_normalise(item.description): item for item in catalogue(country_code)}


def match_description(country_code: str, description: str) -> SorItem | None:
    """The schedule item whose description this is, or None.

    Matching is exact after normalising case, punctuation and whitespace, which is what
    a line taken from the template looks like. It is deliberately not fuzzy: a partial
    match would attach the wrong schedule code - and the wrong section - to a line the
    analyst wrote themselves, and the app already has keyword rules for free text.
    """
    if not description:
        return None
    return _by_description(country_code).get(_normalise(description))


# --------------------------------------------------------------------------- #
# The sections summary: what the schedule holds, per section
# --------------------------------------------------------------------------- #
def sections_summary(country_code: str) -> list[dict]:
    """Per-section schedule facts, for the template instructions and the upload summary.

    This is the summary the analyst needs before filling anything in: which sections the
    schedule covers, how many items each holds, how many of those the rate library can
    actually price, and how many rows fall outside the ten sections entirely.
    """
    items = catalogue(country_code)
    # The rule table's own section order, so the summary reads like the classifier.
    names = list(SMM2_SECTIONS)
    out: list[dict] = []
    for name in names:
        subset = [i for i in items if i.section == name]
        if not subset:
            continue
        out.append(
            {
                "smm2_section": name,
                "sor_items": len(subset),
                "with_rate": sum(1 for i in subset if i.rate),
                "bookable": sum(1 for i in subset if i.bookable),
            }
        )
    outside = [i for i in items if not i.section]
    out.append(
        {
            "smm2_section": "(outside the ten sections)",
            "sor_items": len(outside),
            "with_rate": sum(1 for i in outside if i.rate),
            "bookable": 0,
        }
    )
    return out


def catalogue_totals(country_code: str) -> dict:
    items = catalogue(country_code)
    return {
        "sor_items": len(items),
        "bookable": sum(1 for i in items if i.bookable),
        "outside_sections": sum(1 for i in items if not i.section),
        "with_rate": sum(1 for i in items if i.rate),
        "unit_not_comparable": sum(1 for i in items if not i.unit_comparable),
        "sources": sorted({i.source for i in items}),
    }


# --------------------------------------------------------------------------- #
# The template itself
# --------------------------------------------------------------------------- #
def _uom_note(item: SorItem) -> str:
    """Say, per row, what the UOM is and where it came from."""
    if not item.published_unit.strip() and not item.unit.strip():
        return (
            "NO UOM: the schedule states no unit for this item, so there is nothing to convert. "
            "Set one of m, m2, m3, t, item before uploading, or the row is rejected."
        )
    if not item.unit_comparable:
        return (
            f"NOT COMPARABLE: the schedule measures this item in '{item.published_unit}'. The app "
            f"compares m, m2, m3, t and item only - re-measure it, or supply a manual rate."
        )
    if not item.unit.strip():
        return "NO UOM: the schedule states no unit for this item. Set one of m, m2, m3, t, item."
    if _normalise(item.unit) == _normalise(item.published_unit or ""):
        return ""
    return (
        f"converted from the schedule's '{item.published_unit}' to the app's '{item.unit}', and the "
        f"rate was converted with it"
    )


def _item_rows(country: Country) -> list[list]:
    rows = []
    for item in catalogue(country.code):
        rows.append(
            [
                item.code,
                item.description,
                item.section,
                item.unit,                            # UOM the rate is per
                item.published_unit,                  # what the schedule measured
                _uom_note(item),
                0,                                    # quantity: the analyst's to set
                item.rate if item.rate is not None else 0,
                item.currency or country.currency,    # stated per row, not implied
            ]
        )
    return rows


def _instructions(country: Country, library_quarter: str) -> list[list[str]]:
    items = catalogue(country.code)
    totals = catalogue_totals(country.code)
    summary = sections_summary(country.code)
    sources = totals["sources"]
    quarter = library_quarter or "2026Q2"
    return [
        ["shouldcost BoQ template", ""],
        ["", ""],
        ["Market", country.name],
        ["Currency", f"{country.currency} ({country.currency_symbol})"],
        ["Measurement standard", country.measurement_standard],
        ["Measurement note", country.measurement_note],
        ["Unit convention", country.unit_convention],
        ["Schedule items in this template", f"{totals['sor_items']:,}"],
        ["Schedule source", "; ".join(sources)],
        ["", ""],
        ["HOW TO USE THIS TEMPLATE", ""],
        ["1", f"The BoQ sheet lists all {totals['sor_items']:,} items in the schedule of rates for "
              f"this market, so you fill in quantities against the real vocabulary instead of "
              f"retyping descriptions. There is only one template, and this sheet is part of it."],
        ["2", "DELETE THE ROWS YOU DO NOT NEED. That is the fastest route to a short bill - an "
              "untouched template is a catalogue, not a tender."],
        ["2a", f"FILTER ON THE section COLUMN FIRST. {totals['sor_items'] - totals['outside_sections']:,} "
               f"of the {totals['sor_items']:,} items fall in a section the rate library covers, so the "
               f"app can benchmark those straight away. The other {totals['outside_sections']:,} are "
               f"trades outside the library's ten sections (painting, glazing, metalwork, roofing, "
               f"joinery, finishes, demolition, repairs) and are reported as needing a manual rate."],
        ["3", "Set quantity on the rows you keep. Every row starts at 0, so an untouched file totals "
              "zero on purpose and cannot be mistaken for a priced bill."],
        ["4", "Replace rate with YOUR tendered rate if you have one. It is pre-filled with the "
              "library rate for the section - the published schedule rate escalated to the quarter "
              "this library is stated at, which is the same figure the app benchmarks against (see "
              "WHERE THE RATES COME FROM below and CURRENCY AND THE QUARTER). Rows whose item has no "
              "published rate start at 0."],
        ["5", "To add a line that is NOT in the schedule, leave sor_code blank. The app reports those "
              "lines separately: the schedule has no rate for them, so they need a manual rate before "
              "the should-cost is complete."],
        ["6", "description and unit are what the classifier reads. Use the wording of your own bill "
              "if it differs - the schedule wording classifies accurately because the rules were "
              "tuned on it, and anything the classifier cannot place is flagged Unclassified for you "
              "to reclassify."],
        ["7", "Check the UOM column. The schedule measures a few items in units the app does not "
              "compare (litre, hour, per metre span); those rows keep the published unit and are "
              "reported as a unit mismatch, not silently dropped. Re-measure them or supply a manual "
              "rate."],
        ["", ""],
        ["UOM - THE UNIT YOUR RATE AND QUANTITY MUST BE IN", ""],
        ["", "Every row states the UOM its rate is per. Fill quantity in that same UOM, and give a "
             "rate per one of that UOM - a rate of 150 against 'm2' is 150 per square metre."],
        ["UOM", "means / what to fill"],
        *[[code, f"{meaning}. Accepted spellings on upload: {spellings}"]
          for code, meaning, spellings in UOM_LEGEND],
        ["published_uom", "What the schedule of rates itself measured for this item (sqm, cum, kg, "
                          "metre, each, litre, hour ...). Kept so a conversion is visible."],
        ["uom_note", "Explains the conversion, or says NOT COMPARABLE for the "
                     f"{totals['unit_not_comparable']} item(s) the app cannot compare. Those rows "
                     "keep the schedule's own unit and are reported as a unit mismatch rather than "
                     "dropped: re-measure them in m/m2/m3/t/item or supply a manual rate."],
        ["unit_convention", country.unit_convention],
        ["", f"Rows whose UOM is not comparable, by market: Singapore 1 of 454, India 30 of 1,876. "
             f"Filter on uom_note to find them."],
        ["", ""],
        ["CURRENCY AND THE QUARTER THE RATES ARE STATED AT", ""],
        ["Currency", f"{country.currency} ({country.currency_symbol}) - every rate in this file, and "
                     f"every figure the app reports for this market, is in {country.currency}."],
        ["currency column", "States it on every row, so a sheet lifted out of context still says "
                            "which currency its rates are in. Ignored on upload: the market you "
                            "upload to sets the currency."],
        ["Rate basis", f"The pre-filled rates are the published schedule rate escalated to "
                       f"{quarter} by CPI. Benchmark at {quarter} and each schedule-derived "
                       f"section carries an index ratio of exactly 1.000 - the library as "
                       f"published, with no escalation on top."],
        ["Later quarters", "Still selectable. The app then carries the rates forward from "
                           f"{quarter} with the published index and discloses the movement; "
                           f"nothing is escalated silently."],
        ["", ""],
        ["WHERE THE RATES COME FROM", ""],
        ["", "The pre-filled rate is the published schedule rate ESCALATED TO 2026 BY CPI:"],
        *[["", source] for source in sources],
        ["", "Singapore: BCA Schedule of Rates May 2022 x 1.171 (cumulative CPI 2022 to 2026)."],
        ["", "India: CPWD Delhi Schedule of Rates 2021 Vol-II x 1.2364 (cumulative CPI 2021 to 2026)."],
        ["", "That escalation is the rate basis for this market: the same technique the app uses to "
             "carry a stale index forward to the tender quarter. It is a derived rate from a stated "
             "source rather than a tendered one, which is what the app labels basis = derived when it "
             "prices a line with it - and why every line's detail names the source it came from."],
        ["", ""],
        ["SECTIONS SUMMARY - WHAT THIS MARKET'S SCHEDULE HOLDS", ""],
        ["section", "schedule items / with a published rate / comparable unit"],
        *[[row["smm2_section"], f"{row['sor_items']:,} / {row['with_rate']:,} / {row['bookable']:,}"]
          for row in summary],
        ["TOTAL", f"{totals['sor_items']:,} / {totals['with_rate']:,} / {totals['bookable']:,} "
                  f"(bookable = in a section the library prices AND measured in a comparable unit)"],
        ["", "The same summary is reported per upload in the app, against the lines you actually "
             "supply, so you can see how much of your bill the schedule and the rate library cover."],
        ["", ""],
        ["COLUMN REFERENCE", ""],
        ["sor_code", "The schedule item code this line was quoted from. Leave blank for a line you "
                     "added; the app treats a blank code as 'not in the schedule'."],
        ["description", "The schedule wording. Classified into a section by the app; a description "
                        "that matches a schedule item exactly is placed in that item's section."],
        ["section", "The app's classification of this description, supplied so you can filter. BLANK "
                    "means the item is outside the library's ten sections and cannot be benchmarked "
                    "without a manual rate. Ignored on upload - the app reclassifies."],
        ["UOM", "One of m, m2, m3, t, item: the unit the rate and the quantity are per. It must "
                "match the benchmark rate's unit or the line is excluded from variance testing. "
                "The parser also accepts unit, units and uom."],
        ["published_uom", "What the schedule of rates itself measured for this item, kept verbatim "
                          "so a conversion is visible."],
        ["uom_note", "Explains the conversion, says NOT COMPARABLE when the app cannot compare the "
                     "schedule's unit, or says NO UOM when the schedule states no unit at all."],
        ["quantity", "Measured quantity. Zero in the template - you must set it."],
        ["rate", f"Rate per unit, in {country.currency}. Pre-filled with the library rate for the "
                 f"section (the published schedule rate escalated to the library quarter); overwrite "
                 f"it with your own tendered rate if you have one."],
        ["currency", f"{country.currency} on every row, so a sheet lifted out of context still says "
                     f"which currency its rates are in. Ignored on upload: the market you upload to "
                     f"sets the currency."],
        ["", ""],
        ["SHOULD-COST COMPLETENESS", ""],
        ["", "A complete should-cost needs a rate for every line. Lines the library cannot price are "
             "reported in the app rather than silently dropped:"],
        ["Unclassified", "the classifier placed no section, so there is no benchmark rate. "
                         "Reclassify it, or supply a manual rate."],
        ["No library rate", "the section has no rate in the library for this market."],
        ["Unit mismatch", "the line's unit differs from the library rate's unit, so the comparison "
                          "would be meaningless. Re-measure it or supply a manual rate."],
        ["Not in the schedule", "sor_code is blank, so the line came from your own bill. Supply a "
                                "manual rate."],
        ["", ""],
        ["CREDIBLE SOURCES FOR THIS MARKET", ""],
        *[[s.name, s.url, s.what] for s in country.sources],
    ]


def template_csv_bytes(country_code: str) -> bytes:
    """The same item list without the instructions sheet, for scripted use."""
    country = get_country(country_code)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADERS)
    writer.writerows(_item_rows(country))
    return buffer.getvalue().encode("utf-8")


def template_xlsx_bytes(country_code: str, library_quarter: str = "") -> bytes:
    """THE template: the item list plus its instructions, in one file."""
    country = get_country(country_code)
    buffer = io.BytesIO()
    boq = pd.DataFrame(_item_rows(country), columns=HEADERS)
    instructions = pd.DataFrame(_instructions(country, library_quarter))
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        boq.to_excel(writer, index=False, sheet_name="BoQ")
        instructions.to_excel(writer, index=False, header=False, sheet_name="Instructions")
        # The BoQ sheet is 450-1,900 rows; freeze the header so the columns stay readable, and
        # size the columns so UOM, published_uom and uom_note are actually readable.
        sheet = writer.sheets["BoQ"]
        sheet.freeze_panes = "A2"
        for column, width in (
            ("A", 12),   # sor_code
            ("B", 110),  # description
            ("C", 18),   # section
            ("D", 8),    # UOM
            ("E", 14),   # published_uom
            ("F", 60),   # uom_note
            ("G", 10),   # quantity
            ("H", 12),   # rate
            ("I", 10),   # currency
        ):
            sheet.column_dimensions[column].width = width
    buffer.seek(0)
    return buffer.getvalue()


def template_filename(country_code: str, fmt: str) -> str:
    country = get_country(country_code)
    return f"shouldcost-boq-template-{country.code.lower()}.{fmt}"


def classify_for_upload(country_code: str, description: str) -> tuple[str, str | None]:
    """Section for an uploaded description, preferring the schedule item it matches.

    Returns (section, sor_code). A description that matches a schedule item exactly takes
    that item's section, so a bill built from the template always lands in the section the
    template advertised. Anything else goes through the keyword classifier, which is what
    free-text bills need.
    """
    item = match_description(country_code, description)
    if item is not None and item.section:
        return item.section, item.code
    section = classify(description, country_code).smm2_section
    return (UNCLASSIFIED if section == UNCLASSIFIED else section), (item.code if item else None)
