"""Recalibrate the two demonstration BoQs against the SOR-derived rate library.

    python tools/build_sample_boqs.py

WHY THIS EXISTS
---------------
The demonstration bills are synthetic, and their whole purpose is to exercise the
variance report: some lines clearly over, some clearly under, some inside tolerance,
plus a unit mismatch and two unclassified lines so those paths are visible too.

When the rate library moves, that calibration has to move with it, or the demo stops
demonstrating anything. The rate library was rebuilt from the BCA and CPWD schedules of
rates expressed at Q2 2026, which shifted several sections by 30-50%; the previous
sample rates were calibrated to the older, invented library.

HOW IT WORKS
------------
For each line, the adjusted benchmark rate is computed by the real engine at the
market's default region and the Q2 2026 tender quarter, and the sample rate is set to
that benchmark times a target variance stated per line below. The targets are chosen so
the bill still shows a believable spread of outcomes, not so the numbers look tidy.

Quantities and descriptions are untouched: this only re-prices the demonstration bills.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The default SQLite URL is relative, so the process must run from backend/ or it
# silently opens a different, empty database.
os.chdir(REPO / "backend")
sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import select  # noqa: E402

from app import benchmark, classifier  # noqa: E402
from app.db import get_session_factory, init_db  # noqa: E402
from app.models import RegionalFactor  # noqa: E402

TENDER_QUARTER = "2026Q2"

# One target variance per line, in file order. "" means the line is deliberately left
# alone because it is not benchmarked at all (unit mismatch, or unclassified).
TARGETS = {
    "SG": ["-18", "-8", "+22", "+6", "-15", "+9", "+31", "+18", "-11", "+25",
           "+7", "-12", "+16", "-21", "", "", "", "+19", "-27", "+34"],
    "IN": ["+16", "-9", "-24", "+18", "-19", "-12", "+21", "-17", "+27", "-29",
           "-10", "+16", "+8", "", "", "", "+23", "-22", "+13", "+31"],
}

FILES = {"SG": "sample_boq.csv", "IN": "sample_boq_india.csv"}
SERIES = {"SG": "BCA", "IN": "CPWD"}
REGION = {"SG": "SGP", "IN": "DEL"}


def ratio_for(session, country: str, section: str, rates: dict, tpi) -> float:
    """The index ratio the engine applies to one section."""
    row = rates[section]
    stated = (row.base_quarter or "").strip()
    if stated:
        resolved = benchmark.resolve_tpi(
            session, SERIES[country], stated, country=country, bridge="auto"
        )
        return tpi.value / resolved.value
    return tpi.value / tpi.base_value


def quote(rate: float) -> float:
    """Round to a precision a real bill would actually be quoted at.

    Three significant figures above 1,000; whole units from 100 to 1,000; two decimals
    below that. A bill priced at 101,704.32 reads as machine output, not as a tender.
    """
    if rate >= 1000:
        magnitude = 10 ** (len(str(int(rate))) - 3)
        return int(round(rate / magnitude) * magnitude)
    if rate >= 100:
        return int(round(rate))
    return round(rate, 2)


def adjusted_rate(session, country: str, section: str, unit: str,
                  rates: dict, tpi) -> float | None:
    row = rates.get(section)
    if row is None:
        return None
    if benchmark.normalise_unit(unit) != benchmark.normalise_unit(row.unit):
        return None
    stated = (row.base_quarter or "").strip()
    if stated:
        resolved = benchmark.resolve_tpi(
            session, SERIES[country], stated, country=country, bridge="auto"
        )
        ratio = tpi.value / resolved.value
    else:
        # The BRIDGED value, which is what the engine actually escalates by. Using the
        # published value here would mis-price every retained section by the bridge factor.
        ratio = tpi.value / tpi.base_value
    return row.base_rate * ratio


def main() -> int:
    init_db()
    session = get_session_factory()()

    for country, filename in FILES.items():
        path = REPO / "backend" / "data" / filename
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
        targets = TARGETS[country]
        if len(rows) != len(targets):
            raise SystemExit(
                f"{filename}: {len(rows)} rows but {len(targets)} targets. Update TARGETS."
            )

        tpi = benchmark.resolve_tpi(
            session, SERIES[country], TENDER_QUARTER, country=country, bridge="auto"
        )
        rates = benchmark.load_benchmark_rates(session, country=country)
        factor = benchmark.resolve_region(session, country, REGION[country]).factor
        # Sections the selected index excludes, which the engine holds at base level.
        present = {classifier.classify(r["description"], country).smm2_section for r in rows}
        excluded = benchmark.excluded_sections(tpi.scope_exclusions, present)
        if excluded:
            print(f"  scope-excluded by {SERIES[country]}: {', '.join(sorted(excluded))}")

        print(f"=== {filename} - {country}, {TENDER_QUARTER}, region {REGION[country]} "
              f"(factor {factor}) ===")
        out = []
        for row, target in zip(rows, targets):
            section = classifier.classify(row["description"], country).smm2_section
            adjusted = adjusted_rate(session, country, section, row["unit"], rates, tpi)
            if adjusted is None or not target:
                print(f"  {section:<17} left as-is  ({row['unit']}, not benchmarked)")
                out.append(row)
                continue
            adjusted *= factor
            # A section the selected index excludes is held at the library's base level:
            # the engine applies scope_factor 1/ratio, so the calibrated rate has to be
            # computed against the same figure or the target variance will not land.
            if section in excluded:
                adjusted /= ratio_for(session, country, section, rates, tpi)
            rate = adjusted * (1.0 + float(target) / 100.0)
            new_rate = quote(rate)
            print(f"  {section:<17} {row['unit']:<6} {float(row['rate']):>10,.2f} -> "
                  f"{new_rate:>10,.2f}   (benchmark {adjusted:>10,.2f}, target {target}%)")
            row["rate"] = str(new_rate)
            out.append(row)

        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n")
            writer.writeheader()
            writer.writerows(out)
        print(f"  wrote {path.name}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
