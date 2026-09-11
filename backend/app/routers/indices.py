"""Read-only index and reference-data endpoints.

All data is served from the local database. The application makes no outbound
network call to BCA, SISV, SingStat, CPWD, NBO, the Office of the Economic
Adviser or any other index provider at runtime.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import benchmark, classifier, schemas
from ..countries import DEFAULT_COUNTRY, UnknownCountryError, get_country
from ..db import get_db
from ..models import BenchmarkRate, PriceSeries, MaterialPrice, RegionalFactor, TPISeries

router = APIRouter(prefix="/api/indices", tags=["indices"])


def _country(code: str | None) -> str:
    try:
        return get_country(code or DEFAULT_COUNTRY).code
    except UnknownCountryError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/tpi", response_model=list[schemas.TPIPointOut])
def list_tpi(
    series: str | None = Query(None, description="SG: BCA | HDB | RLB | AECOM. IN: CPWD | NBO | WPI-CON"),
    country: str = Query(DEFAULT_COUNTRY, description="SG | IN"),
    from_quarter: str | None = Query(None, description="Inclusive, e.g. 2023Q1"),
    to_quarter: str | None = Query(None, description="Inclusive, e.g. 2024Q4"),
    db: Session = Depends(get_db),
) -> list[schemas.TPIPointOut]:
    code = _country(country)
    statement = select(TPISeries).where(TPISeries.country == code)
    if series:
        statement = statement.where(TPISeries.series_name == series.strip().upper())
    rows = list(db.scalars(statement.order_by(TPISeries.series_name, TPISeries.quarter)))
    if from_quarter:
        rows = [r for r in rows if r.quarter >= from_quarter.strip()]
    if to_quarter:
        rows = [r for r in rows if r.quarter <= to_quarter.strip()]
    return [schemas.TPIPointOut.model_validate(r) for r in rows]


@router.get("/materials", response_model=list[schemas.MaterialPointOut])
def list_materials(
    material: str | None = Query(None, description="cement | steel_rebar | ready_mix_concrete"),
    country: str = Query(DEFAULT_COUNTRY, description="SG | IN"),
    from_month: str | None = Query(None, description="Inclusive, e.g. 2024-01"),
    to_month: str | None = Query(None, description="Inclusive, e.g. 2024-12"),
    db: Session = Depends(get_db),
) -> list[schemas.MaterialPointOut]:
    code = _country(country)
    statement = select(MaterialPrice).where(MaterialPrice.country == code)
    if material:
        statement = statement.where(MaterialPrice.material == material.strip().lower())
    rows = list(db.scalars(statement.order_by(MaterialPrice.material, MaterialPrice.month)))
    if from_month:
        rows = [r for r in rows if r.month >= from_month.strip()]
    if to_month:
        rows = [r for r in rows if r.month <= to_month.strip()]
    return [schemas.MaterialPointOut.model_validate(r) for r in rows]


@router.get("/price-series", response_model=list[schemas.PricePointOut])
def list_price_series(
    series: str | None = Query(None, description="e.g. CPI-ALL"),
    country: str = Query(DEFAULT_COUNTRY, description="SG | IN"),
    from_month: str | None = Query(None, description="Inclusive, e.g. 2022-01"),
    to_month: str | None = Query(None, description="Inclusive, e.g. 2026-07"),
    db: Session = Depends(get_db),
) -> list[schemas.PricePointOut]:
    """The monthly consumer price series used to carry a stale index forward.

    Real published data for both markets. Served from the local database: the app
    never calls the statistics office at runtime.
    """
    code = _country(country)
    registry = get_country(code)
    name = (series or registry.default_cpi_series or "").strip().upper()
    statement = select(PriceSeries).where(PriceSeries.country == code)
    if name:
        statement = statement.where(PriceSeries.series_name == name)
    rows = list(db.scalars(statement.order_by(PriceSeries.series_name, PriceSeries.month)))
    if from_month:
        rows = [r for r in rows if r.month >= from_month.strip()]
    if to_month:
        rows = [r for r in rows if r.month <= to_month.strip()]
    return [schemas.PricePointOut.model_validate(r) for r in rows]


@router.get("/freshness", response_model=schemas.IndexFreshnessOut)
def index_freshness(
    country: str = Query(DEFAULT_COUNTRY, description="SG | IN"),
    reference_quarter: str | None = Query(
        None,
        description=(
            "Quarter to measure staleness against, e.g. 2026Q3. Defaults to the current "
            "calendar quarter."
        ),
    ),
    db: Session = Depends(get_db),
) -> schemas.IndexFreshnessOut:
    """How current every index series in this market is, and what the CPI bridge does.

    This is the same machinery the benchmark engine uses, exposed so the freshness
    of the underlying data is visible without running a benchmark. Nothing here is
    adjusted: it states the last published quarter per series, the lag against the
    reference quarter, and the CPI-bridged value the engine would use.
    """
    from datetime import date

    code = _country(country)
    registry = get_country(code)
    if reference_quarter:
        try:
            benchmark.parse_quarter(reference_quarter)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        reference = reference_quarter.strip()
    else:
        today = date.today()
        reference = benchmark.format_quarter(today.year, (today.month - 1) // 3 + 1)

    cpi_rows = list(
        db.scalars(
            select(PriceSeries)
            .where(PriceSeries.country == code)
            .order_by(PriceSeries.series_name, PriceSeries.month)
        )
    )
    default_cpi = (registry.default_cpi_series or "").upper()
    # The registry names the PREFERRED consumer price series. Other bases may be
    # loaded too (India carries the 2024-based series and its predecessor, which do
    # not overlap), and the engine will use whichever one spans both bridge
    # endpoints. All of them are reported here with their coverage.
    grouped_cpi: dict[str, list[PriceSeries]] = {}
    for row in cpi_rows:
        grouped_cpi.setdefault(row.series_name, []).append(row)
    default_rows = grouped_cpi.get(default_cpi, [])
    other_series = sorted(name for name in grouped_cpi if name != default_cpi)
    cpi_series_list = [
        {
            "series_name": name,
            "base_year": rows[-1].base_year,
            "observations": len(rows),
            "first_month": rows[0].month,
            "last_month": rows[-1].month,
            "latest_value": rows[-1].value,
            "is_preferred": name == default_cpi,
            "is_placeholder": any(r.is_placeholder for r in rows),
            "source_url": rows[-1].source_url,
        }
        for name, rows in sorted(grouped_cpi.items())
    ]

    series_rows = list(
        db.scalars(
            select(TPISeries)
            .where(TPISeries.country == code)
            .order_by(TPISeries.series_name, TPISeries.quarter)
        )
    )
    grouped: dict[str, list[TPISeries]] = {}
    for row in series_rows:
        grouped.setdefault(row.series_name, []).append(row)

    series_out: list[dict] = []
    for name in sorted(grouped):
        rows = grouped[name]
        latest = rows[-1]
        bridge = benchmark.resolve_index_bridge(
            db,
            country=code,
            observation_quarter=latest.quarter,
            requested_quarter=reference,
            mode=benchmark.BRIDGE_CPI,
            published_index_value=latest.value,
        )
        if bridge.applied:
            bridge.bridged_index_value = latest.value * bridge.factor
        series_out.append(
            {
                "series_name": name,
                "observations": len(rows),
                "first_quarter": rows[0].quarter,
                "latest_quarter": latest.quarter,
                "latest_value": latest.value,
                "base_year": latest.base_year,
                "lag_quarters": benchmark.quarter_sort_key(reference)
                - benchmark.quarter_sort_key(latest.quarter),
                "is_placeholder": latest.is_placeholder,
                "source_url": latest.source_url,
                "bridge": bridge.as_dict(),
            }
        )

    return schemas.IndexFreshnessOut(
        country=code,
        country_name=registry.name,
        currency=registry.currency,
        reference_quarter=reference,
        cpi_series_name=default_cpi,
        cpi_series_available=bool(default_rows),
        cpi_latest_month=default_rows[-1].month if default_rows else None,
        cpi_latest_value=default_rows[-1].value if default_rows else None,
        cpi_base_year=default_rows[-1].base_year if default_rows else None,
        cpi_observations=len(default_rows),
        cpi_is_placeholder=any(r.is_placeholder for r in default_rows),
        cpi_source_url=default_rows[-1].source_url if default_rows else "",
        cpi_other_series=other_series,
        cpi_series_list=cpi_series_list,
        series=series_out,
    )


@router.get("/benchmark-rates", response_model=list[schemas.BenchmarkRateOut])
def list_benchmark_rates(
    country: str = Query(DEFAULT_COUNTRY, description="SG | IN"),
    db: Session = Depends(get_db),
) -> list[schemas.BenchmarkRateOut]:
    """The benchmark rate library for one country, with full provenance on every row."""
    code = _country(country)
    rows = list(
        db.scalars(
            select(BenchmarkRate)
            .where(BenchmarkRate.country == code)
            .order_by(BenchmarkRate.smm2_section)
        )
    )
    return [schemas.BenchmarkRateOut.model_validate(r) for r in rows]


@router.get("/regions", response_model=list[schemas.RegionalFactorOut])
def list_regions(
    country: str = Query(DEFAULT_COUNTRY, description="SG | IN"),
    db: Session = Depends(get_db),
) -> list[schemas.RegionalFactorOut]:
    """Regional cost multipliers for one country, with their provenance."""
    code = _country(country)
    rows = list(
        db.scalars(
            select(RegionalFactor)
            .where(RegionalFactor.country == code)
            .order_by(RegionalFactor.is_default.desc(), RegionalFactor.region_name)
        )
    )
    return [schemas.RegionalFactorOut.model_validate(r) for r in rows]


@router.get("/classifier-rules")
def list_classifier_rules(
    country: str = Query(DEFAULT_COUNTRY, description="SG | IN"),
) -> dict:
    """The effective keyword rule table, so the classifier is auditable from the UI."""
    code = _country(country)
    registry = get_country(code)
    return {
        "country": registry.code,
        "country_name": registry.name,
        "classification_standard": registry.measurement_standard,
        "measurement_note": registry.measurement_note,
        "unclassified_label": classifier.UNCLASSIFIED,
        "rules": classifier.rules_as_dicts(code),
        "note": (
            "Case-insensitive keyword match, first matching rule wins. basis=derived. "
            "Override any line with PATCH /api/boq/item/{item_id}."
        ),
    }
