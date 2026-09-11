"""Read-only index and reference-data endpoints.

All data is served from the local database. The application makes no outbound
network call to BCA, SISV, SingStat, CPWD, NBO, the Office of the Economic
Adviser or any other index provider at runtime.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import classifier, schemas
from ..countries import DEFAULT_COUNTRY, UnknownCountryError, get_country
from ..db import get_db
from ..models import BenchmarkRate, MaterialPrice, RegionalFactor, TPISeries

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
