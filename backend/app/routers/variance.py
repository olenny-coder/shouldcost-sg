"""Benchmark and sensitivity-analysis endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..benchmark import (
    RegionLookupError,
    TPILookupError,
    build_benchmark,
    compute_sensitivity,
    resolve_region,
)
from ..db import get_db
from ..models import BoQUpload, BoQItem

router = APIRouter(prefix="/api/boq", tags=["variance"])


def _load(db: Session, upload_id: int) -> tuple[BoQUpload, list[BoQItem]]:
    upload = db.get(BoQUpload, upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"BoQ upload {upload_id} does not exist."
        )
    items = list(
        db.scalars(select(BoQItem).where(BoQItem.upload_id == upload_id).order_by(BoQItem.id))
    )
    if not items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"BoQ upload {upload_id} has no line items to benchmark.",
        )
    return upload, items


def _region(db: Session, upload: BoQUpload, requested: str | None) -> str | None:
    """Explicit request wins, else the upload's region, else the country default."""
    try:
        return resolve_region(db, upload.country, requested or upload.region_code).region_code
    except RegionLookupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{upload_id}/benchmark", response_model=schemas.BenchmarkResponse)
def benchmark_upload(
    upload_id: int, payload: schemas.BenchmarkRequest, db: Session = Depends(get_db)
) -> schemas.BenchmarkResponse:
    """Run the should-cost benchmark for one upload, optionally with index adjusters."""
    upload, items = _load(db, upload_id)
    try:
        computation = build_benchmark(
            db,
            upload_id=upload.id,
            filename=upload.filename,
            items=items,
            tender_quarter=payload.tender_quarter,
            tpi_series_name=payload.tpi_series_name,
            variance_threshold=payload.variance_threshold,
            country=upload.country,
            currency=upload.currency,
            region_code=_region(db, upload, payload.region_code),
            adjustments=payload.adjustments,
            manual_rates=payload.manual_rates,
            index_bridge=payload.index_bridge,
        )
    except RegionLookupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TPILookupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Remember the last-run parameters so GET /api/boq/{id} can echo them back.
    upload.tender_quarter = computation.tender_quarter
    upload.tpi_series_name = computation.tpi_series_name
    upload.region_code = computation.region_code
    db.commit()

    return schemas.BenchmarkResponse(
        upload_id=computation.upload_id,
        country=computation.country,
        country_name=computation.country_name,
        currency=computation.currency,
        classification_standard=computation.classification_standard,
        measurement_standard=computation.measurement_standard,
        filename=computation.filename,
        tender_quarter=computation.tender_quarter,
        tpi_series_name=computation.tpi_series_name,
        variance_threshold=computation.variance_threshold,
        tpi_series_scope_inclusions=computation.tpi_series_scope_inclusions,
        tpi_series_scope_exclusions=computation.tpi_series_scope_exclusions,
        region_code=computation.region_code,
        region_name=computation.region_name,
        regional_factor=computation.regional_factor,
        regional_factor_is_placeholder=computation.regional_factor_is_placeholder,
        regional_factor_source=computation.regional_factor_source,
        adjustments_applied=computation.adjustments_applied,
        index_bridge=computation.index_bridge,
        lines=[schemas.BenchmarkLine.model_validate(l) for l in computation.lines],
        sections=[schemas.SectionAggregate.model_validate(s) for s in computation.sections],
        totals=schemas.BenchmarkTotals.model_validate(computation.totals),
        waterfall=[schemas.WaterfallComponent.model_validate(w) for w in computation.waterfall],
        warnings=computation.warnings,
        assumptions=computation.assumptions,
    )


@router.post("/{upload_id}/sensitivity", response_model=schemas.SensitivityResponse)
def sensitivity_analysis(
    upload_id: int, payload: schemas.SensitivityRequest, db: Session = Depends(get_db)
) -> schemas.SensitivityResponse:
    """Sweep the index value and stress each section in isolation.

    Returns a sweep across the index (with a break-even point) and a per-section
    tornado ranked by swing. Every point is produced by the same engine as the
    headline benchmark, so the baseline row matches POST /benchmark to the cent.
    """
    upload, items = _load(db, upload_id)
    try:
        result = compute_sensitivity(
            db,
            upload_id=upload.id,
            items=items,
            tender_quarter=payload.tender_quarter,
            tpi_series_name=payload.tpi_series_name,
            variance_threshold=payload.variance_threshold,
            tpi_scale_min_pct=payload.tpi_scale_min_pct,
            tpi_scale_max_pct=payload.tpi_scale_max_pct,
            tpi_scale_step_pct=payload.tpi_scale_step_pct,
            section_scale_pct=payload.section_scale_pct,
            country=upload.country,
            region_code=_region(db, upload, payload.region_code),
            adjustments=payload.adjustments,
            manual_rates=payload.manual_rates,
            index_bridge=payload.index_bridge,
        )
    except RegionLookupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TPILookupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return schemas.SensitivityResponse(**result)
