"""Rewrite the provenance_note on the WPI-CONST rows of the seed CSV.

WPI-CONST is not a single published WPI row: it is a weight-blended composite of
two published group indices, then averaged over the months of the calendar
quarter. The original note mentioned only the quarterly averaging, which
understated how the number was produced. This script makes the note state the full
derivation, leaving every value untouched.

    python tools/fix_wpi_const_provenance.py
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TPI_CSV = REPO / "backend" / "data" / "tpi_series.csv"

CEMENT = "Manufacture of cement, lime and plaster (WPI commodity code 1313050000, weight 1.68125)"
STEEL = "Manufacture of basic iron and steel (commodity code 1314010000, weight 6.31601)"

TEMPLATE = (
    "REAL DATA. DERIVED SERIES - Office of the Economic Adviser, DPIIT, Ministry of Commerce and "
    "Industry, Wholesale Price Index, base 2022-23 = 100. WPI-CONST is not a single published WPI "
    "row: it is a weight-blended composite of the published group indices '{cement}' and '{steel}' "
    "- 21.02% cement / 78.98% steel - averaged over the published months of calendar quarter "
    "{quarter}. The blended monthly values come straight from the publisher's workbook; the "
    "weighting is the publisher's own commodity weights. Reproduce with "
    "python tools/verify_seed_data.py, which re-derives this series and every other seeded value "
    "from the source file."
)


def main() -> int:
    with TPI_CSV.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0].keys())

    changed = 0
    for row in rows:
        if row["country"] != "IN" or row["series_name"] != "WPI-CONST":
            continue
        note = TEMPLATE.format(cement=CEMENT, steel=STEEL, quarter=row["quarter"])
        if row["provenance_note"] != note:
            row["provenance_note"] = note
            changed += 1

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    TPI_CSV.write_text(buffer.getvalue(), encoding="utf-8", newline="")
    print(f"updated {changed} WPI-CONST provenance note(s) in {TPI_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
