"""Downloadable BoQ templates.

A template is the fastest way to remove upload friction: the analyst fills in the
sheet that the parser already understands, in the vocabulary of their own market,
rather than guessing column names.

Two sheets are produced for XLSX: the BoQ itself, and the instructions.
"""

from __future__ import annotations

import csv
import io

import pandas as pd

from .countries import Country, get_country

HEADERS = ["description", "unit", "quantity", "rate", "is_placeholder", "replace_with"]

_EXAMPLES: dict[str, list[list]] = {
    "SG": [
        ["Grade 35/20 ready-mixed concrete to pile caps and ground beams", "m3", 100, 205.00],
        ["Sawn timber formwork to soffits of suspended slabs", "m2", 500, 45.00],
        ["Bored piling 600mm diameter, including casing", "m", 50, 440.00],
    ],
    "IN": [
        ["Reinforced cement concrete M25 in pile caps and raft foundation", "m3", 100, 7950.00],
        ["Shuttering and formwork to soffits of suspended slabs", "m2", 500, 530.00],
        ["Bored cast in-situ pile 600mm diameter, including casing", "m", 50, 5300.00],
    ],
}

_EXAMPLE_TODO = (
    "# TODO: example row - replace with an actual measured BoQ line, or delete this row"
)


def _instructions(country: Country) -> list[list[str]]:
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
        ["1", "Delete the example rows once you have added your own. They are marked is_placeholder = true, meaning indicative seed values rather than a licensed schedule of rates."],
        ["2", "description is mandatory and is what the classifier reads. Use the wording of your own bill."],
        ["3", "unit is mandatory. Accepted: m, m2, m3, t/tonne, item/sum/nos. Aliases such as SQM and CUM are understood."],
        ["4", "quantity and rate are mandatory numbers. Commas as thousand separators are tolerated."],
        ["5", "amount is optional. If omitted it is computed as quantity x rate."],
        ["6", "Leave is_placeholder and replace_with in place if you are working with sample data."],
        [""],
        ["COLUMN REFERENCE"],
        ["description", "Free text. Classified into a section by keyword. Unmatched lines are flagged Unclassified and can be reclassified in the app."],
        ["unit", f"One of m, m2, m3, t, item. Units must match the benchmark rate unit or the line is excluded from variance testing."],
        ["quantity", "Measured quantity."],
        ["rate", f"Tendered rate per unit, in {country.currency}."],
        ["is_placeholder", "true when the row is synthetic demonstration data."],
        ["replace_with", "The value or source that must replace this row before it is used for a real decision."],
        [""],
        ["CREDIBLE SOURCES FOR THIS MARKET"],
        *[[s.name, s.url, s.what] for s in country.sources],
    ]


def _example_rows(country: Country) -> list[list]:
    rows = _EXAMPLES.get(country.code, _EXAMPLES["SG"])
    return [[*row, "true", _EXAMPLE_TODO] for row in rows]


def template_csv_bytes(country_code: str) -> bytes:
    country = get_country(country_code)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADERS)
    writer.writerows(_example_rows(country))
    return buffer.getvalue().encode("utf-8")


def template_xlsx_bytes(country_code: str) -> bytes:
    country = get_country(country_code)
    buffer = io.BytesIO()
    boq = pd.DataFrame(_example_rows(country), columns=HEADERS)
    instructions = pd.DataFrame(_instructions(country))
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        boq.to_excel(writer, index=False, sheet_name="BoQ")
        instructions.to_excel(writer, index=False, header=False, sheet_name="Instructions")
    buffer.seek(0)
    return buffer.getvalue()


def template_filename(country_code: str, fmt: str) -> str:
    country = get_country(country_code)
    return f"shouldcost-boq-template-{country.code.lower()}.{fmt}"
