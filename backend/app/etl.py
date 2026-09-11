"""Idempotent ETL for the bundled seed CSVs.

Run with:  python -m app.etl

Loading twice must leave identical row counts - each row is upserted on its
natural key, so a second run inserts nothing.

Two kinds of row live side by side, and each says which it is:

* is_placeholder = false - REAL published data. `provenance_note` records the
  publisher, the series and any transformation applied.
* is_placeholder = true  - SYNTHETIC placeholder. `replace_with` names the exact
  value that must replace it.

See README "Where the data comes from" for the split by table.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .countries import DEFAULT_COUNTRY, get_country
from .db import get_engine, get_session_factory, init_db
from .models import (
    BenchmarkRate,
    BoQUpload,
    BoQItem,
    PriceSeries,
    MaterialPrice,
    RegionalFactor,
    TPISeries,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

TPI_CSV = "tpi_series.csv"
MATERIAL_CSV = "material_prices.csv"
BENCHMARK_CSV = "benchmark_rates.csv"
REGIONAL_CSV = "regional_factors.csv"
PRICE_CSV = "price_series.csv"

# One demonstration upload per supported country. Each file is replaced (never
# appended), so re-running the ETL cannot accumulate uploads.
SAMPLE_BOQS: tuple[tuple[str, str], ...] = (
    ("sample_boq.csv", "SG"),
    ("sample_boq_india.csv", "IN"),
)


def _read_csv(name: str, data_dir: Path) -> pd.DataFrame:
    path = data_dir / name
    if not path.exists():
        raise FileNotFoundError(f"Seed file not found: {path}")
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _as_float(value: str) -> float:
    return float(str(value).replace(",", "").strip())


def _country(value: str | None) -> str:
    return (value or DEFAULT_COUNTRY).strip().upper()


def _upsert(session: Session, model, key: dict, values: dict) -> int:
    """Insert or update one row on its natural key.

    Returns 1 when a new row was inserted and 0 when an existing row was updated,
    so the caller can report how much work the ETL actually did. On a second run
    against unchanged CSVs every index count must be 0.
    """
    existing = session.scalar(select(model).filter_by(**key))
    if existing is None:
        session.add(model(**key, **values))
        return 1
    for field, value in values.items():
        if getattr(existing, field) != value:
            setattr(existing, field, value)
    return 0


def load_tpi(session: Session, df: pd.DataFrame) -> int:
    inserted = 0
    for row in df.to_dict("records"):
        country = _country(row.get("country"))
        key = {
            "country": country,
            "series_name": row["series_name"].strip().upper(),
            "quarter": row["quarter"].strip(),
            "base_year": int(row["base_year"]),
        }
        values = {
            "base_value": _as_float(row.get("base_value") or 100.0),
            "currency": (row.get("currency") or get_country(country).currency).strip().upper(),
            "value": _as_float(row["value"]),
            "scope_inclusions": row["scope_inclusions"].strip(),
            "scope_exclusions": row["scope_exclusions"].strip(),
            "source_url": row["source_url"].strip(),
            "is_placeholder": _as_bool(row.get("is_placeholder", "true")),
            "provenance_note": row.get("provenance_note", "").strip(),
            "replace_with": row.get("replace_with", "").strip(),
        }
        inserted += _upsert(session, TPISeries, key, values)
    return inserted


def load_price_series(session: Session, df: pd.DataFrame) -> int:
    """Load monthly producer and consumer price index observations.

    These are the most timely official series available, and the engine uses them
    to carry a stale construction index observation forward to the tender quarter.
    Producer indices take precedence over consumer ones - see PriceSeries.
    """
    inserted = 0
    for row in df.to_dict("records"):
        country = _country(row.get("country"))
        key = {
            "country": country,
            "series_name": row["series_name"].strip().upper(),
            "month": row["month"].strip(),
        }
        kind = (row.get("kind") or "CPI").strip().upper()
        if kind not in {"PPI", "CPI"}:
            raise ValueError(
                f"{key['series_name']} {key['month']}: kind must be PPI or CPI, got {kind!r}."
            )
        values = {
            "kind": kind,
            "title": row.get("title", "").strip(),
            "scope_sections": row.get("scope_sections", "").strip(),
            "base_year": int(row["base_year"]),
            "base_value": _as_float(row.get("base_value") or 100.0),
            "currency": (row.get("currency") or get_country(country).currency).strip().upper(),
            "value": _as_float(row["value"]),
            "source_url": row["source_url"].strip(),
            "is_placeholder": _as_bool(row.get("is_placeholder", "true")),
            "provenance_note": row.get("provenance_note", "").strip(),
            "replace_with": row.get("replace_with", "").strip(),
        }
        inserted += _upsert(session, PriceSeries, key, values)
    return inserted


def load_materials(session: Session, df: pd.DataFrame) -> int:
    inserted = 0
    for row in df.to_dict("records"):
        country = _country(row.get("country"))
        key = {
            "country": country,
            "material": row["material"].strip().lower(),
            "month": row["month"].strip(),
        }
        values = {
            "unit": row["unit"].strip(),
            "price": _as_float(row["price"]),
            "currency": (row.get("currency") or get_country(country).currency).strip().upper(),
            "frequency": (row.get("frequency") or "monthly").strip().lower(),
            "source_url": row["source_url"].strip(),
            "is_placeholder": _as_bool(row.get("is_placeholder", "true")),
            "provenance_note": row.get("provenance_note", "").strip(),
            "replace_with": row.get("replace_with", "").strip(),
        }
        inserted += _upsert(session, MaterialPrice, key, values)
    return inserted


def load_benchmark_rates(session: Session, df: pd.DataFrame) -> int:
    inserted = 0
    for row in df.to_dict("records"):
        country = _country(row.get("country"))
        key = {
            "country": country,
            "smm2_section": row["smm2_section"].strip(),
            "description": row["description"].strip(),
            "unit": row["unit"].strip(),
            "base_year": int(row["base_year"]),
            "source": row["source"].strip(),
        }
        values = {
            "classification_standard": row.get("classification_standard", "SMM2").strip(),
            "base_rate": _as_float(row["base_rate"]),
            "currency": (row.get("currency") or get_country(country).currency).strip().upper(),
            "source_url": row["source_url"].strip(),
            "source_date": row["source_date"].strip(),
            "scope_inclusions": row["scope_inclusions"].strip(),
            "scope_exclusions": row["scope_exclusions"].strip(),
            "confidence": row["confidence"].strip(),
            "is_placeholder": _as_bool(row.get("is_placeholder", "true")),
            "provenance_note": row.get("provenance_note", "").strip(),
            "replace_with": row.get("replace_with", "").strip(),
        }
        inserted += _upsert(session, BenchmarkRate, key, values)
    return inserted


def load_regional_factors(session: Session, df: pd.DataFrame) -> int:
    """Load the per-region cost multipliers. Every country needs exactly one default."""
    inserted = 0
    defaults: dict[str, int] = {}
    for row in df.to_dict("records"):
        country = _country(row.get("country"))
        code = row["region_code"].strip().upper()
        is_default = _as_bool(row.get("is_default", "false"))
        if is_default:
            defaults[country] = defaults.get(country, 0) + 1
        key = {"country": country, "region_code": code}
        values = {
            "region_name": row["region_name"].strip(),
            "is_default": is_default,
            "factor": _as_float(row["factor"]),
            "currency": (row.get("currency") or get_country(country).currency).strip().upper(),
            "source": row["source"].strip(),
            "source_url": row["source_url"].strip(),
            "source_date": row["source_date"].strip(),
            "notes": row.get("notes", "").strip(),
            "is_placeholder": _as_bool(row.get("is_placeholder", "true")),
            "provenance_note": row.get("provenance_note", "").strip(),
            "replace_with": row.get("replace_with", "").strip(),
        }
        inserted += _upsert(session, RegionalFactor, key, values)

    for country, count in defaults.items():
        if count != 1:
            raise ValueError(
                f"{country} declares {count} default regions; exactly one is required."
            )
    return inserted


def default_region_code(session: Session, country: str) -> str | None:
    return session.scalar(
        select(RegionalFactor.region_code).where(
            RegionalFactor.country == country, RegionalFactor.is_default.is_(True)
        )
    )


def load_sample_boq(
    session: Session, df: pd.DataFrame, filename: str, country_code: str
) -> tuple[int | None, int]:
    """Load one bundled sample BoQ as a demo upload.

    Idempotent: EVERY previous upload with this filename is replaced, so a
    duplicate created through POST /api/boq/upload cannot accumulate.
    """
    from .classifier import classify

    country = get_country(country_code)

    previous = list(session.scalars(select(BoQUpload).where(BoQUpload.filename == filename)))
    for upload in previous:
        session.delete(upload)
    if previous:
        session.flush()

    upload = BoQUpload(
        country=country.code,
        region_code=default_region_code(session, country.code),
        filename=filename,
        tender_quarter="2024Q4",
        tpi_series_name=country.default_tpi_series,
        currency=country.currency,
    )
    session.add(upload)
    session.flush()

    count = 0
    for row in df.to_dict("records"):
        description = row["description"].strip()
        quantity = _as_float(row["quantity"])
        rate = _as_float(row["rate"])
        result = classify(description, country=country.code)
        session.add(
            BoQItem(
                upload_id=upload.id,
                raw_description=description,
                unit=row["unit"].strip(),
                quantity=quantity,
                boq_rate=rate,
                amount=round(quantity * rate, 2),
                smm2_section=result.smm2_section,
                classified_by="auto",
                is_placeholder=_as_bool(row.get("is_placeholder", "true")),
                replace_with=row.get("replace_with", "").strip(),
            )
        )
        count += 1
    session.flush()
    return upload.id, count


def reset_schema() -> None:
    """Drop and recreate every table.

    Required after a schema change: create_all() adds missing TABLES but never
    ALTERs an existing one, so a new column is invisible until the table is
    rebuilt. All reference data is reproducible from the CSVs, but any uploaded
    BoQs in the database are LOST - hence the explicit flag and the confirmation
    in the CLI output.
    """
    from .db import Base, get_engine

    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def run_etl(data_dir: Path | None = None, *, session: Session | None = None) -> dict[str, dict[str, int]]:
    """Load every seed CSV.

    Returns {"inserted": {...}, "rows": {...}}. On a second run against unchanged
    CSVs every "inserted" count is 0 and every "rows" count is unchanged.
    """
    directory = data_dir or DATA_DIR
    owns_session = session is None
    if owns_session:
        init_db()
        session = get_session_factory()()

    if session is None:  # pragma: no cover - defensive
        raise RuntimeError("Could not obtain a database session.")
    try:
        inserted = {
            "tpi_series": load_tpi(session, _read_csv(TPI_CSV, directory)),
            "material_prices": load_materials(session, _read_csv(MATERIAL_CSV, directory)),
            "benchmark_rates": load_benchmark_rates(session, _read_csv(BENCHMARK_CSV, directory)),
            "regional_factors": load_regional_factors(session, _read_csv(REGIONAL_CSV, directory)),
            "price_series": load_price_series(session, _read_csv(PRICE_CSV, directory)),
        }
        sample_items = 0
        for filename, country_code in SAMPLE_BOQS:
            _, items = load_sample_boq(
                session, _read_csv(filename, directory), filename, country_code
            )
            sample_items += items
        inserted["boq_items"] = sample_items
        session.commit()
        return {"inserted": inserted, "rows": row_counts(session)}
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def row_counts(session: Session) -> dict[str, int]:
    return {
        "tpi_series": session.scalar(select(func.count()).select_from(TPISeries)) or 0,
        "material_prices": session.scalar(select(func.count()).select_from(MaterialPrice)) or 0,
        "benchmark_rates": session.scalar(select(func.count()).select_from(BenchmarkRate)) or 0,
        "regional_factors": session.scalar(select(func.count()).select_from(RegionalFactor)) or 0,
        "price_series": session.scalar(select(func.count()).select_from(PriceSeries)) or 0,
        "boq_uploads": session.scalar(select(func.count()).select_from(BoQUpload)) or 0,
        "boq_items": session.scalar(select(func.count()).select_from(BoQItem)) or 0,
    }


def real_data_summary(session: Session) -> dict[str, int]:
    """How many rows in each reference table are real published data."""
    out = {}
    for name, model in (
        ("tpi_series", TPISeries),
        ("material_prices", MaterialPrice),
        ("benchmark_rates", BenchmarkRate),
        ("regional_factors", RegionalFactor),
        ("price_series", PriceSeries),
    ):
        total = session.scalar(select(func.count()).select_from(model)) or 0
        real = (
            session.scalar(
                select(func.count()).select_from(model).where(model.is_placeholder.is_(False))
            )
            or 0
        )
        out[name] = real
        out[name + "_total"] = total
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load shouldcost seed data (idempotent).")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate every table before loading. Destroys uploaded BoQs.",
    )
    args = parser.parse_args(argv)

    init_db()
    if args.reset:
        print("ETL: --reset requested - dropping and recreating every table.")
        print("ETL: WARNING any previously uploaded BoQs in this database are destroyed.")
        reset_schema()
    print(f"ETL: reading seed CSVs from {args.data_dir}")
    result = run_etl(args.data_dir)
    inserted = result["inserted"]
    print("ETL: rows inserted this run (0 means the CSV was already applied)")
    for table in ("tpi_series", "material_prices", "benchmark_rates", "regional_factors", "price_series"):
        print(f"  {table:<18} {inserted[table]}")
    print(f"  {'boq_items':<18} {inserted['boq_items']} (sample uploads are rewritten each run)")
    print("ETL: row counts after load")
    for table, count in result["rows"].items():
        print(f"  {table:<18} {count}")

    session = get_session_factory()()
    try:
        summary = real_data_summary(session)
    finally:
        session.close()
    print("ETL: real published data vs synthetic placeholder")
    for table in ("tpi_series", "material_prices", "benchmark_rates", "regional_factors", "price_series"):
        real = summary[table]
        total = summary[table + "_total"]
        flag = "REAL" if real == total else ("placeholder" if real == 0 else "MIXED")
        print(f"  {table:<18} {real:>4} real / {total:>4} total   [{flag}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
