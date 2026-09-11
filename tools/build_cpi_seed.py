"""Build backend/data/cpi_series.csv from the publishers' own downloads.

WHY THIS EXISTS
---------------
The engine brings a stale index observation up to the tender quarter by applying
the observed change in the country's CONSUMER PRICE INDEX between the last
observation and the requested quarter (see README "Keeping the indexes current").
That bridge needs a real, monthly CPI series in the database. This script turns
the publisher downloads into the seed CSV, so the derivation is reproducible and
no CPI value is ever typed in by hand.

INPUTS (downloaded once, never fetched at runtime - the app stays offline)
--------------------------------------------------------------------------
Singapore - SingStat Table Builder, table M213751
    "Consumer Price Index (CPI), 2024 As Base Year, Monthly", row "All Items",
    publisher: Singapore Department of Statistics. dataLastUpdated 24/08/2026.

        curl -H "User-Agent: Mozilla/5.0" -H "Referer: https://tablebuilder.singstat.gov.sg/" \
             -H "Origin: https://tablebuilder.singstat.gov.sg" \
             -o .realdata/singstat_M213751_cpi.json \
             "https://tablebuilder.singstat.gov.sg/api/table/tabledata/M213751"

India - .realdata/india_cpi_monthly.csv, extracted from the MoSPI / NSO Consumer
    Price Index API (eSankhyiki). The endpoint is:

        https://api.mospi.gov.in/api/cpi/getCPIData
            ?base_year=2024 & year=<YYYY> & month_code=<1..12> & limit=100 & page=<n>
            & sector_code=3            # 3 = Combined (rural + urban)
            & state_code=99            # 99 = All India

    and the same endpoint with base_year=2012 for the predecessor series. The
    all-items row is the one whose division is "CPI (General)" on the 2024 base, or
    whose group/subgroup is "General" / "General-Overall" on the 2012 base - NOT the
    division-level rows such as "Miscellaneous-Overall".

    IMPORTANT: the two bases do not overlap (the 2024-based series starts at 2026-01,
    the 2012-based one ends at 2025-12), so they are written as two separate series
    and the engine picks whichever one spans both bridge endpoints. They are never
    spliced: without the publisher's official linking factor a splice would invent a
    level shift (196.5 on the old base vs ~105 on the new one for adjacent months).

    The CSV must carry the documented header:
        month,value,series_name,base_year,source_url,publication_date
    The raw API responses are kept under .realdata/india_cpi_raw/ and
    `tools/verify_seed_data.py` re-derives this seed from them.

OUTPUT
------
backend/data/cpi_series.csv with columns:
    country, series_name, month, base_year, base_value, currency, value,
    source_url, is_placeholder, provenance_note, replace_with

Every row written here is REAL published data (is_placeholder=false) and carries
a provenance_note naming the publisher, the table and the retrieval date.

    python tools/build_cpi_seed.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKSPACE = REPO.parent
REALDATA = WORKSPACE / ".realdata"
OUT_CSV = REPO / "backend" / "data" / "cpi_series.csv"

SINGSTAT_JSON = REALDATA / "singstat_M213751_cpi.json"

# How far back to carry the monthly CPI. The bridge only needs the months from
# the oldest index observation forward, but a little history makes the index
# dashboard chart readable.
START_MONTH = "2022-01"

SINGSTAT_URL = "https://tablebuilder.singstat.gov.sg/api/table/tabledata/M213751"
SINGSTAT_TABLE = (
    "SingStat Table Builder table M213751, 'Consumer Price Index (CPI), 2024 As Base Year, "
    "Monthly', row 'All Items'"
)
RETRIEVED = "2026-09-11"

# The base year MoSPI currently publishes. Rows on this base become the preferred
# "CPI-ALL" series for India; rows on an older base keep their year in the name.
CURRENT_INDIA_CPI_BASE = 2024

MONTH_NUMBER = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

FIELDS = [
    "country",
    "series_name",
    "month",
    "base_year",
    "base_value",
    "currency",
    "value",
    "source_url",
    "is_placeholder",
    "provenance_note",
    "replace_with",
]


def _month_key(label: str) -> str | None:
    """'2026 Jul' -> '2026-07'. Returns None for anything else."""
    parts = str(label).strip().split()
    if len(parts) != 2 or parts[1] not in MONTH_NUMBER:
        return None
    try:
        year = int(parts[0])
    except ValueError:
        return None
    return f"{year:04d}-{MONTH_NUMBER[parts[1]]:02d}"


def singapore_rows() -> list[dict]:
    if not SINGSTAT_JSON.exists():
        raise FileNotFoundError(
            f"{SINGSTAT_JSON} is missing. Download table M213751 from the SingStat Table "
            f"Builder API first - the exact command is in this file's docstring."
        )
    payload = json.loads(SINGSTAT_JSON.read_text(encoding="utf-8-sig"))["Data"]
    table_title = payload.get("title", "")
    last_updated = payload.get("dataLastUpdated", "")
    rows: list[dict] = []
    for record in payload["row"]:
        if str(record.get("rowText", "")).strip().lower() != "all items":
            continue
        for point in record["columns"]:
            month = _month_key(point["key"])
            if month is None or month < START_MONTH:
                continue
            raw = str(point["value"]).strip()
            if raw == "" or raw.lower() in {"na", "n/a", "-"}:
                continue
            rows.append(
                {
                    "country": "SG",
                    "series_name": "CPI-ALL",
                    "month": month,
                    "base_year": 2024,
                    "base_value": 100.0,
                    "currency": "SGD",
                    "value": float(raw),
                    "source_url": SINGSTAT_URL,
                    "is_placeholder": "false",
                    "provenance_note": (
                        f"REAL. {SINGSTAT_TABLE}, publisher Singapore Department of Statistics. "
                        f"{table_title}; source data last updated {last_updated}. Value taken as "
                        f"published, no transformation. Retrieved {RETRIEVED}."
                    ),
                    "replace_with": "",
                }
            )
    rows.sort(key=lambda row: row["month"])
    return rows


def india_rows() -> list[dict]:
    """India CPI rows, one series per publisher base.

    MoSPI publishes the Consumer Price Index on base 2024 = 100 together with its own
    **back-cast** rows for the earlier months on that base (API `series=Back`). Those
    are the publisher's re-estimation of 2023-2024 on the new base, so the current
    base spans 2023-01 onward and no splice is needed for a bridge that starts at any
    of this app's index observations (the earliest is 2023Q1).

    The predecessor 2012-based series is kept as a separate series. It and the
    2024-based one do not overlap, and the engine never chains them: without the
    publisher's official linking factor a splice would invent a level shift (196.5 on
    the old base against ~103.5 on the new one for adjacent months).

    Input files: every `india_cpi_monthly*.csv` in ../.realdata/ is merged, keyed by
    (base_year, month). If two files disagree about a month, this fails loudly rather
    than picking one.
    """
    inputs = sorted(REALDATA.glob("india_cpi_monthly*.csv"))
    if not inputs:
        print(f"  NOTE: no india_cpi_monthly*.csv found in {REALDATA} - no India rows written.")
        return []

    merged: dict[tuple[int, str], dict] = {}
    for path in inputs:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for record in csv.DictReader(handle):
                month = str(record.get("month", "")).strip()
                value = str(record.get("value", "")).strip()
                if not month or not value:
                    continue
                if month < START_MONTH:
                    continue
                base_year = int(str(record.get("base_year") or "2016").strip() or 2016)
                published = str(record.get("publication_date") or "").strip()
                label = str(record.get("series_name") or "")
                back_cast = "back" in label.lower()
                key = (base_year, month)
                candidate = {
                    "value": float(value),
                    "publication_date": published,
                    "source_url": str(record.get("source_url") or "").strip(),
                    "back_cast": back_cast,
                    "file": path.name,
                }
                existing = merged.get(key)
                if existing is None:
                    merged[key] = candidate
                    continue
                if abs(existing["value"] - candidate["value"]) > 1e-9:
                    raise SystemExit(
                        f"India CPI conflict for base {base_year} {month}: "
                        f"{existing['value']} in {existing['file']} vs "
                        f"{candidate['value']} in {candidate['file']}. Resolve the inputs."
                    )
                # Same value in two files: keep the row that knows it is a
                # back-cast, and keep any publication date we have.
                merged[key] = {
                    "value": existing["value"],
                    "publication_date": existing["publication_date"] or candidate["publication_date"],
                    "source_url": existing["source_url"] or candidate["source_url"],
                    "back_cast": existing["back_cast"] and candidate["back_cast"],
                    "file": existing["file"],
                }

    rows: list[dict] = []
    for (base_year, month), entry in sorted(merged.items()):
        # The publisher's CURRENT base is the preferred series; an older base is
        # suffixed with its year so both can live side by side.
        preferred = str(base_year) == str(CURRENT_INDIA_CPI_BASE)
        series_name = "CPI-ALL" if preferred else f"CPI-ALL-{base_year}"
        status = (
            " These months are the publisher's own BACK-CAST series on this base (`series=Back` "
            "from the same API), not a rescaled older series, so they are published values."
            if entry["back_cast"]
            else ""
        )
        base_note = (
            ""
            if preferred
            else (
                " NOTE: the publisher rebased the CPI to 2024 = 100; this series carries the earlier "
                "base and does NOT overlap the current one, so it is stored separately rather than "
                "spliced. The engine uses it only when no single series spans both bridge endpoints."
            )
        )
        rows.append(
            {
                "country": "IN",
                "series_name": series_name,
                "month": month,
                "base_year": base_year,
                "base_value": 100.0,
                "currency": "INR",
                "value": entry["value"],
                "source_url": entry["source_url"],
                "is_placeholder": "false",
                "provenance_note": (
                    f"REAL. Consumer Price Index, Combined (rural + urban), All-India General index, "
                    f"base {base_year} = 100, published monthly by the Ministry of Statistics and "
                    f"Programme Implementation / National Statistical Office, Government of India "
                    f"(eSankhyiki API, sector Combined, state All India)."
                    + (f" Release dated {entry['publication_date']}." if entry["publication_date"] else "")
                    + f" Value taken as published, no transformation. Retrieved {RETRIEVED}."
                    + status
                    + base_note
                ),
                "replace_with": "",
            }
        )
    rows.sort(key=lambda row: (row["series_name"], row["month"]))
    return rows


def main() -> int:
    sg = singapore_rows()
    india = india_rows()
    rows = sg + india
    if not rows:
        raise SystemExit("No CPI rows could be built - nothing written.")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {OUT_CSV}")
    for code in ("SG", "IN"):
        subset = [row for row in rows if row["country"] == code]
        if not subset:
            continue
        print(f"  {code}: {len(subset)} rows")
        for name in sorted({row["series_name"] for row in subset}):
            series = [row for row in subset if row["series_name"] == name]
            series.sort(key=lambda row: row["month"])
            print(
                f"    {name:<13} base {series[0]['base_year']} = 100 | {len(series)} rows | "
                f"{series[0]['month']} .. {series[-1]['month']} | latest {series[-1]['value']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
