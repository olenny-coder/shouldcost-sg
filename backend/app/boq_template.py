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
  rate          the schedule's rate escalated to 2026 by CPI, so a filled quantity
                analyses immediately. It is a SCHEDULE rate, not a tendered rate, and
                every row says so in replace_with
  is_placeholder, replace_with
                the provenance markers hard rule 1 requires: what this row is, and what
                must replace it before the figure is used for a real decision

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

HEADERS = ["sor_code", "description", "section", "unit", "quantity", "rate",
           "is_placeholder", "replace_with"]

CATALOGUE = Path(__file__).resolve().parent.parent / "data" / "sor_items.csv"

RATE_TODO = (
    "# TODO: the rate is the SCHEDULE rate escalated to 2026 by CPI, not a tendered rate. "
    "Replace it with your own, and set quantity, before reading the variance."
)
NO_RATE_TODO = (
    "# TODO: the published schedule carries no rate for this item. Supply your own tendered "
    "rate and set quantity; until you do, this row is a description with no price."
)
UNIT_TODO = (
    "# TODO: the schedule measures this item in a unit the app cannot compare (see the unit "
    "column). Re-measure it in m, m2, m3, t or item, or supply a manual rate in the app's "
    "Coverage panel; otherwise the line is reported as a unit mismatch."
)


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
def _todo_for(item: SorItem) -> str:
    if not item.unit_comparable:
        return UNIT_TODO
    if item.rate is None:
        return NO_RATE_TODO
    return RATE_TODO


def _item_rows(country: Country) -> list[list]:
    rows = []
    for item in catalogue(country.code):
        rows.append(
            [
                item.code,
                item.description,
                item.section,
                item.unit,
                0,                                  # quantity: the analyst's to set
                item.rate if item.rate is not None else 0,
                "true",
                _todo_for(item),
            ]
        )
    return rows


def _instructions(country: Country) -> list[list[str]]:
    items = catalogue(country.code)
    totals = catalogue_totals(country.code)
    summary = sections_summary(country.code)
    sources = totals["sources"]
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
        ["4", "Replace rate with YOUR tendered rate. It is pre-filled with the SCHEDULE rate so the "
              "file analyses straight away - see WHERE THE RATES COME FROM below, because a schedule "
              "rate is not a tender - and every row is marked is_placeholder = true until you "
              "replace it. Rows whose item has no published rate start at 0 and say so."],
        ["5", "To add a line that is NOT in the schedule, leave sor_code blank. The app reports those "
              "lines separately: the schedule has no rate for them, so they need a manual rate before "
              "the should-cost is complete."],
        ["6", "description and unit are what the classifier reads. Use the wording of your own bill "
              "if it differs - the schedule wording classifies accurately because the rules were "
              "tuned on it, and anything the classifier cannot place is flagged Unclassified for you "
              "to reclassify."],
        ["7", "Check the unit column. The schedule measures a few items in units the app does not "
              "compare (litre, hour, per metre span); those rows keep the published unit and are "
              "reported as a unit mismatch, not silently dropped. Re-measure them or supply a manual "
              "rate."],
        ["", ""],
        ["WHERE THE RATES COME FROM", ""],
        ["", "The pre-filled rate is the published schedule rate ESCALATED TO 2026 BY CPI:"],
        *[["", source] for source in sources],
        ["", "Singapore: BCA Schedule of Rates May 2022 x 1.171 (cumulative CPI 2022 to 2026)."],
        ["", "India: CPWD Delhi Schedule of Rates 2021 Vol-II x 1.2364 (cumulative CPI 2021 to 2026)."],
        ["", "Both extracts state the same caveat, which applies here: this is an estimate - "
             "validate it with current market quotations and construction-specific indices. The "
             "escalation is the same technique the app uses to carry a stale index forward to the "
             "tender quarter, and it is disclosed for the same reason."],
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
        ["unit", "One of m, m2, m3, t, item. The unit must match the benchmark rate's unit or the "
                 "line is excluded from variance testing."],
        ["quantity", "Measured quantity. Zero in the template - you must set it."],
        ["rate", f"Tendered rate per unit, in {country.currency}. Pre-filled with the escalated "
                 f"schedule rate; replace it with your own."],
        ["is_placeholder", "true when the row is not a real tendered line. Leave it true until you "
                           "have replaced the rate and quantity."],
        ["replace_with", "What must replace this row before it is used for a real decision."],
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


def template_xlsx_bytes(country_code: str) -> bytes:
    """THE template: the item list plus its instructions, in one file."""
    country = get_country(country_code)
    buffer = io.BytesIO()
    boq = pd.DataFrame(_item_rows(country), columns=HEADERS)
    instructions = pd.DataFrame(_instructions(country))
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        boq.to_excel(writer, index=False, sheet_name="BoQ")
        instructions.to_excel(writer, index=False, header=False, sheet_name="Instructions")
        # The BoQ sheet is 450-1,900 rows; freeze the header so the columns stay readable.
        sheet = writer.sheets["BoQ"]
        sheet.freeze_panes = "A2"
        sheet.column_dimensions["A"].width = 12
        sheet.column_dimensions["B"].width = 110
        sheet.column_dimensions["C"].width = 18
        sheet.column_dimensions["D"].width = 8
        sheet.column_dimensions["E"].width = 10
        sheet.column_dimensions["F"].width = 12
        sheet.column_dimensions["H"].width = 60
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
