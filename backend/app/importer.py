"""Import REAL published index data, replacing the synthetic placeholders.

The application never calls an index provider at runtime (hard rule 2). Instead
this command takes a file you downloaded yourself and loads it, stamping every
row with the real provenance:

    python -m app.importer --kind tpi --file wpi_quarters.csv \
        --source-url "https://eaindustry.nic.in/download_data_2223.asp" \
        --provenance "Wholesale Price Index, base 2022-23 = 100, calendar-quarter mean."

The file must use the same columns as the seed CSVs, so the upstream work is a
copy/paste from the published download into the documented template:

  tpi              country, series_name, quarter, base_year, base_value, currency, value,
                   scope_inclusions, scope_exclusions, source_url, is_placeholder,
                   provenance_note, replace_with
  materials        country, material, month, unit, price, currency, frequency, source_url,
                   is_placeholder, provenance_note, replace_with
  benchmark_rates  country, smm2_section, classification_standard, description, unit,
                   base_rate, currency, base_year, source, source_url, source_date,
                   scope_inclusions, scope_exclusions, confidence, is_placeholder,
                   provenance_note, replace_with
  regions          country, region_code, region_name, is_default, factor, currency, source,
                   source_url, source_date, notes, is_placeholder, provenance_note,
                   replace_with
  cpi              country, series_name, month, base_year, base_value, currency, value,
                   source_url, is_placeholder, provenance_note, replace_with

Every row is upserted on its natural key, so importing is idempotent and a
re-import of an updated file simply refreshes the values.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from .db import get_session_factory, init_db
from .etl import (
    load_benchmark_rates,
    load_price_series,
    load_materials,
    load_regional_factors,
    load_tpi,
    row_counts,
)

KINDS = {
    "tpi": load_tpi,
    "materials": load_materials,
    "benchmark_rates": load_benchmark_rates,
    "regions": load_regional_factors,
    "price_series": load_price_series,
}


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Import file not found: {path}")
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        frame = pd.read_excel(path, dtype=str, engine="openpyxl")
    else:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return frame.fillna("")


def import_file(
    path: Path,
    kind: str,
    *,
    provenance: str,
    source_url: str | None = None,
    mark_real: bool = True,
) -> dict[str, int]:
    """Load one file and stamp it as real published data."""
    loader = KINDS.get(kind)
    if loader is None:
        raise ValueError(f"Unknown kind {kind!r}. Choose from: {', '.join(sorted(KINDS))}.")
    if mark_real and not provenance.strip():
        raise ValueError(
            "--provenance is required when importing real data: state the publisher, the series "
            "and any transformation, so the value can be audited later."
        )

    frame = _read(path)
    if frame.empty:
        raise ValueError(f"{path} contains no rows.")

    for column in ("provenance_note",):
        if column not in frame.columns:
            frame[column] = ""
    if "replace_with" not in frame.columns:
        frame["replace_with"] = ""
    for column in ("is_placeholder",):
        if column not in frame.columns:
            frame[column] = "false"

    if mark_real:
        frame["provenance_note"] = provenance
        frame["is_placeholder"] = "false"
        frame["replace_with"] = ""
    if source_url:
        frame["source_url"] = source_url

    missing = [
        column
        for column in ("country",)
        if column not in frame.columns
    ]
    if missing:
        raise ValueError(f"Missing required column(s): {', '.join(missing)}.")

    init_db()
    session = get_session_factory()()
    try:
        inserted = loader(session, frame)
        session.commit()
        counts = row_counts(session)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return {"inserted": inserted, **counts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import real published index data.")
    parser.add_argument("--kind", required=True, choices=sorted(KINDS))
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--provenance", required=True,
                        help="Publisher, series and transformation. Recorded on every imported row.")
    parser.add_argument("--source-url", default=None, help="Overrides the source_url column.")
    parser.add_argument("--keep-placeholder-flag", action="store_true",
                        help="Do not force is_placeholder=false (only for testing).")
    args = parser.parse_args(argv)

    result = import_file(
        args.file,
        args.kind,
        provenance=args.provenance,
        source_url=args.source_url,
        mark_real=not args.keep_placeholder_flag,
    )
    print(f"Imported {args.kind} from {args.file}")
    print(f"  rows inserted this run : {result['inserted']}")
    print("  row counts now:")
    for table in ("tpi_series", "material_prices", "benchmark_rates", "regional_factors", "cpi_series"):
        print(f"    {table:<18} {result[table]}")
    print("  every imported row is is_placeholder = false with the provenance you supplied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
