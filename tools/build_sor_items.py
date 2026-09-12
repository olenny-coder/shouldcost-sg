"""Turn the two schedule-of-rates extracts into a committed item catalogue.

    python tools/build_sor_items.py

WHY THIS FILE EXISTS
--------------------
The upload template should list every item in the schedule of rates for the market, so an
analyst fills in quantities against the real vocabulary instead of retyping descriptions.
The extracts themselves live OUTSIDE the repository (../SOR data), so a deployed app cannot
read them - hence this catalogue, which is committed like the other seed data and is what
the template and the classification summary are built from.

    ../SOR data/Singapore SOR.csv   BCA Schedule of Rates, May 2022, rates escalated to 2026
    ../SOR data/India SOR.csv       CPWD Delhi Schedule of Rates 2021 Vol-II, same treatment

Both extracts escalate their published rate by cumulative CPI (x1.171 for Singapore 2022-2026,
x1.2364 for India 2021-2026) and both say "validate with current market quotations". That is
why the template pre-fills the rate but marks every row is_placeholder with a TODO: it is an
ESCALATED SCHEDULE rate, not a tendered rate.

EVERY DESCRIPTION IS KEPT
-------------------------
An earlier revision dropped rows it could not price - no published rate, or a unit the app
cannot compare. That silently removed 60 Singapore items and 30 India items from a list whose
whole purpose is completeness, and it made the template shorter than the schedule it claims to
be. Those rows are now KEPT, with the problem recorded per row instead:

  unit_comparable=false   the schedule measures this item in a unit the app does not compare
                          (litre, hour, "per metre span"). The template keeps the published
                          unit and writes a TODO telling the analyst to re-measure or supply a
                          manual rate. The line will be reported as unit_mismatch, not dropped.
  blank rate_2026         the extract carries no rate for the item. The template writes a zero
                          rate and a TODO, so the row is still listed and still fillable.

Only rows with no description at all - which cannot be identified, let alone priced - are
dropped, and the count is printed.

UNITS
-----
The schedules write sqm / metre / cum / kg / each; the app writes m2 / m / m3 / t / item.
Every convertible row is converted to the app's unit so the template uploads without unit
mismatches, and the published unit is kept alongside so the conversion is auditable.

SECTIONS
--------
Each description is classified with the app's own classifier, and the section is stored. The
template shows it so the analyst can filter, the upload summary reports it per section, and
`tools/verify_seed_data.py` re-checks that the classifier still agrees with the catalogue.
"""

from __future__ import annotations

import csv
import io
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# `app` lives in backend/; this import has to work when the module is imported for its
# constants (tools/verify_seed_data.py re-derives the catalogue), so it is done here
# rather than inside main(). Changing the working directory, by contrast, is a side
# effect that belongs to main() - importing a module must not move the process.
sys.path.insert(0, str(REPO / "backend"))

from app.classifier import UNCLASSIFIED, classify  # noqa: E402

SOR_DIR = REPO.parent / "SOR data"
OUT = REPO / "backend" / "data" / "sor_items.csv"

HEADER = ["country", "sor_code", "description", "unit", "published_unit", "unit_comparable",
          "rate_2026", "currency", "section", "source"]

# published unit -> (app unit, multiplier applied to the published rate)
UNIT_CONVERSION = {
    "m2": ("m2", 1.0), "sqm": ("m2", 1.0),
    "m3": ("m3", 1.0), "cum": ("m3", 1.0),
    "m": ("m", 1.0), "metre": ("m", 1.0), "100 metre": ("m", 0.01),
    "kg": ("t", 0.001), "quintal": ("t", 0.01),
    "mt": ("t", 1.0),
    "no": ("item", 1.0), "each": ("item", 1.0), "joint": ("item", 1.0),
    "per test": ("item", 1.0), "each cut": ("item", 1.0), "pair": ("item", 1.0),
    "each hole": ("item", 1.0), "each job": ("item", 1.0), "each set": ("item", 1.0),
    "1000 nos": ("item", 0.001),
}

FILES = {
    "SG": ("Singapore SOR.csv", "SGD", "BCA Schedule of Rates, May 2022, escalated to 2026 by CPI",
           lambda r: r["Code"], lambda r: r["Estimated_Rate_2026_SGD"]),
    "IN": ("India SOR.csv", "INR",
           "CPWD Delhi Schedule of Rates 2021 Vol-II, escalated to 2026 by CPI",
           lambda r: r["Code No."], lambda r: r["Estimated_Rate_2026_INR"]),
}


def load(name: str) -> list[dict]:
    lines = (SOR_DIR / name).read_text(encoding="utf-8", errors="replace").splitlines()
    return list(csv.DictReader(io.StringIO("\n".join(l for l in lines if not l.startswith("#")))))


def build() -> tuple[list[list], dict[str, dict]]:
    """Return the catalogue rows and a per-market build report."""
    rows_out: list[list] = []
    report: dict[str, dict] = {}

    for country, (filename, currency, source, code_of, est_of) in FILES.items():
        rows = load(filename)
        stats = Counter()
        sections: dict[str, Counter] = defaultdict(Counter)
        for row in rows:
            description = (row["Description"] or "").strip()
            if not description:
                stats["dropped: no description"] += 1
                continue
            published_unit = (row["Unit"] or "").strip()
            conversion = UNIT_CONVERSION.get(published_unit.lower())
            if conversion is None:
                unit, multiplier, comparable = published_unit, 1.0, False
                stats["kept with a unit the app cannot compare"] += 1
            else:
                unit, multiplier, comparable = conversion[0], conversion[1], True
            try:
                raw_rate = float(est_of(row))
            except (TypeError, ValueError):
                raw_rate = 0.0
            has_rate = raw_rate > 0
            if not has_rate:
                stats["kept with no published rate"] += 1
            section = classify(description, country).smm2_section
            section_name = "" if section == UNCLASSIFIED else section
            sections[section_name or "(outside the ten sections)"]["items"] += 1
            sections[section_name or "(outside the ten sections)"]["with_rate"] += 1 if has_rate else 0
            sections[section_name or "(outside the ten sections)"]["comparable"] += 1 if comparable else 0
            rows_out.append([
                country,
                code_of(row).strip(),
                description,
                unit,
                published_unit,
                "true" if comparable else "false",
                "" if not has_rate else f"{raw_rate * multiplier:.4f}".rstrip("0").rstrip("."),
                currency,
                section_name,
                source,
            ])
            stats["kept"] += 1
        report[country] = {"source_rows": len(rows), "stats": stats, "sections": sections}

    rows_out.sort(key=lambda r: (r[0], r[8] or "zz", r[1]))
    return rows_out, report


def main() -> int:
    rows_out, report = build()
    for country, info in report.items():
        print(f"{country}: {info['stats']['kept']} of {info['source_rows']} schedule rows kept")
        for key in sorted(k for k in info["stats"] if k != "kept"):
            print(f"    {info['stats'][key]:>4}  {key}")
        print("    sections (items / with a published rate / comparable unit):")
        for name in sorted(info["sections"]):
            counts = info["sections"][name]
            print(f"      {name:<32} {counts['items']:>4} / {counts['with_rate']:>4} / {counts['comparable']:>4}")

    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows_out)
    print(f"\nwrote {OUT.name}: {len(rows_out)} items, {OUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
