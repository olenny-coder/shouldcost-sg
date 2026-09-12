"""Turn the two schedule-of-rates extracts into a committed item catalogue.

    python tools/build_sor_items.py

WHY THIS FILE EXISTS
--------------------
The upload template should list every item in the schedule of rates for the market, so an
analyst fills in quantities against the real vocabulary instead of retyping descriptions.
The extracts themselves live OUTSIDE the repository (../SOR data), so a deployed app cannot
read them - hence this catalogue, which is committed like the other seed data and is what
the template is built from.

UNITS
-----
The schedules write sqm / metre / cum / kg / each; the app writes m2 / m / m3 / t / item.
Every row is converted to the app's unit so the template uploads without unit mismatches,
and the published unit is kept alongside so the conversion is auditable. A row whose unit
cannot be converted unambiguously is left out and counted, not silently mangled.
"""

from __future__ import annotations

import csv
import io
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO / "backend")
sys.path.insert(0, str(REPO / "backend"))

from app.classifier import UNCLASSIFIED, classify  # noqa: E402

SOR_DIR = REPO.parent / "SOR data"
OUT = REPO / "backend" / "data" / "sor_items.csv"

HEADER = ["country", "sor_code", "description", "unit", "published_unit",
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
    "SG": ("Singapore SOR.csv", "SGD", "BCA Schedule of Rates, May 2022",
           lambda r: r["Code"], lambda r: r["Rate_2022_SGD"], lambda r: r["Estimated_Rate_2026_SGD"]),
    "IN": ("India SOR.csv", "INR", "CPWD Delhi Schedule of Rates 2021 Vol-II",
           lambda r: r["Code No."], lambda r: r["Rate_2021_INR"], lambda r: r["Estimated_Rate_2026_INR"]),
}


def load(name: str) -> list[dict]:
    lines = (SOR_DIR / name).read_text(encoding="utf-8", errors="replace").splitlines()
    return list(csv.DictReader(io.StringIO("\n".join(l for l in lines if not l.startswith("#")))))


def main() -> int:
    rows_out: list[list] = []
    for country, (filename, currency, source, code_of, _, est_of) in FILES.items():
        rows = load(filename)
        skipped: dict[str, int] = {}
        kept = 0
        for row in rows:
            description = (row["Description"] or "").strip()
            if not description:
                skipped["blank description"] = skipped.get("blank description", 0) + 1
                continue
            published_unit = (row["Unit"] or "").strip()
            conversion = UNIT_CONVERSION.get(published_unit.lower())
            if conversion is None:
                key = f"unconvertible unit {published_unit!r}"
                skipped[key] = skipped.get(key, 0) + 1
                continue
            try:
                rate = float(est_of(row))
            except (TypeError, ValueError):
                skipped["unreadable rate"] = skipped.get("unreadable rate", 0) + 1
                continue
            if rate <= 0:
                # The schedules carry blank rate cells that became 0. A template row priced
                # at zero is worse than no row, so they are dropped and counted.
                skipped["zero or blank rate"] = skipped.get("zero or blank rate", 0) + 1
                continue
            unit, multiplier = conversion
            section = classify(description, country).smm2_section
            rows_out.append([
                country, code_of(row).strip(), description, unit, published_unit,
                f"{rate * multiplier:.4f}".rstrip("0").rstrip("."),
                currency, "" if section == UNCLASSIFIED else section, source,
            ])
            kept += 1
        print(f"{country}: {kept} of {len(rows)} schedule rows kept")
        for reason, count in sorted(skipped.items()):
            print(f"    dropped {count:>4}  {reason}")

    rows_out.sort(key=lambda r: (r[0], r[7] or "zz", r[1]))
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows_out)
    print(f"\nwrote {OUT.name}: {len(rows_out)} items, {OUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
