"""BoQ upload, retrieval, manual reclassification, template download and export.

Uploads are parsed entirely in memory. Nothing is written to the Render
ephemeral filesystem, which is wiped on every redeploy.
"""

from __future__ import annotations

import csv
import io
import re

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import boq_template, classifier, schemas
from ..benchmark import (
    RegionLookupError,
    TPILookupError,
    build_benchmark,
    load_benchmark_rates,
    resolve_region,
)
from ..countries import DEFAULT_COUNTRY, UnknownCountryError, get_country
from ..db import get_db
from ..models import BoQUpload, BoQItem

router = APIRouter(prefix="/api/boq", tags=["boq"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB - Render free instances have 512 MB RAM.
EXPORT_LEVELS = ("items", "sections", "waterfall", "summary", "report")
EXPORT_PATTERN = "^(items|sections|waterfall|summary|report)$"

# Accepted header spellings, normalised. Keys are the canonical field names.
COLUMN_ALIASES: dict[str, set[str]] = {
    "raw_description": {
        "description", "raw description", "item", "item description",
        "description of work", "work description", "particulars", "boq description",
    },
    "unit": {"unit", "uom", "units"},
    "quantity": {"quantity", "qty", "quantities"},
    "boq_rate": {"rate", "boq rate", "rate sgd", "unit rate", "unit rate sgd", "price"},
    "amount": {"amount", "total", "amount sgd", "value", "total sgd"},
    "is_placeholder": {"is placeholder"},
    "replace_with": {"replace with"},
    # The schedule-of-rates code this line was quoted from, when it came from the template.
    # Blank means the analyst added the line, and the loaded schedule therefore has no rate
    # for it - which is exactly the set that needs a manual rate.
    "sor_code": {"sor code", "schedule code", "sor item", "code"},
}

REQUIRED_FIELDS = ("raw_description", "unit", "quantity", "boq_rate")

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _normalise_header(header: str) -> str:
    return re.sub(r"\s+", " ", str(header).lower().replace("_", " ").replace("-", " ")).strip()


def _as_float(value, field: str, row_number: int) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == "":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Row {row_number}: column '{field}' is empty.",
        )
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Row {row_number}: column '{field}' value {value!r} is not a number.",
        ) from exc


def _as_text(value, limit: int = 0) -> str:
    """A cell as text, with an empty cell staying empty.

    pandas reads a blank CSV cell as NaN, and `str(nan) or ""` is the string "nan" - which
    then gets stored as a provenance note or a schedule code. A blank cell has to mean
    blank.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return text[:limit] if limit else text


def _as_bool(value, default: bool = False) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    text = str(value).strip().lower()
    if text == "":
        return default
    return text in {"1", "true", "yes", "y"}


def resolve_country(code: str | None):
    try:
        return get_country(code or DEFAULT_COUNTRY)
    except UnknownCountryError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def parse_boq_bytes(content: bytes, filename: str) -> pd.DataFrame:
    """Parse CSV or XLSX content into a DataFrame. Raises HTTPException on failure."""
    lower = (filename or "").lower()
    try:
        if lower.endswith(".xls"):
            # openpyxl cannot read the legacy BIFF .xls format. Fail loudly with
            # instructions rather than raising an opaque parse error.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{filename!r} looks like a legacy Excel .xls file, which is not supported. "
                    "Re-save it as .xlsx or .csv and upload again."
                ),
            )
        if lower.endswith((".xlsx", ".xlsm")):
            frame = pd.read_excel(io.BytesIO(content), engine="openpyxl", dtype=object)
        else:
            try:
                frame = pd.read_csv(io.BytesIO(content), dtype=object, encoding="utf-8-sig")
            except UnicodeDecodeError:
                frame = pd.read_csv(io.BytesIO(content), dtype=object, encoding="latin-1")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Could not parse {filename!r} as CSV or XLSX: {exc}. "
                "Download a template from GET /api/boq/template and fill it in."
            ),
        ) from exc

    if frame.empty:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File {filename!r} contains no data rows.",
        )
    return frame


def map_columns(frame: pd.DataFrame) -> dict[str, str]:
    """Map actual headers onto canonical field names."""
    lookup: dict[str, str] = {}
    for column in frame.columns:
        lookup.setdefault(_normalise_header(column), str(column))
    mapping: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        if field in lookup:
            mapping[field] = lookup[field]
            continue
        for alias in aliases:
            if alias in lookup:
                mapping[field] = lookup[alias]
                break
    missing = [f for f in REQUIRED_FIELDS if f not in mapping]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Missing required column(s): {', '.join(missing)}. "
                f"Detected columns: {', '.join(str(c) for c in frame.columns)}. "
                "Rename the headers or start from GET /api/boq/template."
            ),
        )
    return mapping


def _row_value(row, mapping: dict[str, str], field: str):
    column = mapping.get(field)
    if column is None:
        return None
    return row.get(column)


def _build_item(row, mapping: dict[str, str], row_number: int, country: str) -> BoQItem:
    description = str(_row_value(row, mapping, "raw_description") or "").strip()
    if not description:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Row {row_number}: description is empty.",
        )
    unit = str(_row_value(row, mapping, "unit") or "").strip()
    if not unit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Row {row_number}: unit is empty.",
        )
    quantity = _as_float(_row_value(row, mapping, "quantity"), "quantity", row_number)
    rate = _as_float(_row_value(row, mapping, "boq_rate"), "boq_rate", row_number)
    amount = _row_value(row, mapping, "amount")
    if amount is None or str(amount).strip() == "":
        amount_value = round(quantity * rate, 2)
    else:
        amount_value = round(_as_float(amount, "amount", row_number), 2)

    # The section comes from the schedule item when the description IS a schedule item,
    # and from the keyword rules otherwise. See boq_template.classify_for_upload: a bill
    # built from the template therefore lands in the section the template advertised.
    section, matched_code = boq_template.classify_for_upload(country, description)
    # The schedule's own code wins when the description matches a schedule item exactly -
    # it names the item, whereas a file may carry only the chapter ("III") or a reference
    # of the analyst's own. A line the schedule does not hold keeps whatever code the file
    # supplied, which is what makes "not in the schedule" reportable.
    supplied_code = _as_text(_row_value(row, mapping, "sor_code"), limit=32)
    sor_code = matched_code or supplied_code
    return BoQItem(
        raw_description=description,
        unit=unit,
        quantity=quantity,
        boq_rate=rate,
        amount=amount_value,
        smm2_section=section,
        classified_by="auto",
        is_placeholder=_as_bool(_row_value(row, mapping, "is_placeholder"), default=False),
        replace_with=_as_text(_row_value(row, mapping, "replace_with")),
        sor_code=sor_code,
    )


# --------------------------------------------------------------------------- #
# Template download
# --------------------------------------------------------------------------- #
@router.get("/template")
def download_template(
    format: str = Query(
        "xlsx",
        pattern="^(csv|xlsx)$",
        description=(
            "xlsx (default) is THE template: the schedule of rates plus its instructions sheet. "
            "csv is the same item list without the instructions, for scripted use."
        ),
    ),
    country: str = Query(DEFAULT_COUNTRY, description="SG | IN"),
) -> StreamingResponse:
    """Download the BoQ template for one country.

    One template per market. It lists every item in that market's schedule of rates and
    carries its own instructions, because a 450-1,900 row sheet is unusable without them.
    """
    registry = resolve_country(country)
    if format == "xlsx":
        payload = boq_template.template_xlsx_bytes(registry.code)
        filename = boq_template.template_filename(registry.code, "xlsx")
        return StreamingResponse(
            io.BytesIO(payload),
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    payload = boq_template.template_csv_bytes(registry.code)
    filename = boq_template.template_filename(registry.code, "csv")
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# Upload / read / patch
# --------------------------------------------------------------------------- #
@router.post("/upload", response_model=schemas.UploadOut, status_code=status.HTTP_201_CREATED)
async def upload_boq(
    file: UploadFile = File(..., description="BoQ as CSV or XLSX"),
    currency: str | None = Query(None, min_length=3, max_length=3),
    country: str = Query(DEFAULT_COUNTRY, description="SG | IN"),
    region_code: str | None = Query(None, description="Region within the country, e.g. IN: MUM"),
    db: Session = Depends(get_db),
) -> schemas.UploadOut:
    """Parse, classify and persist an uploaded Bill of Quantities."""
    registry = resolve_country(country)
    try:
        region = resolve_region(db, registry.code, region_code)
    except RegionLookupError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Upload is {len(content) / 1024 / 1024:.1f} MB; the limit is "
                f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB. Split the BoQ or remove unused columns."
            ),
        )

    frame = parse_boq_bytes(content, file.filename or "upload.csv")
    mapping = map_columns(frame)

    upload = BoQUpload(
        country=registry.code,
        region_code=region.region_code,
        filename=file.filename or "upload.csv",
        currency=(currency or registry.currency).upper(),
    )
    db.add(upload)
    db.flush()

    items: list[BoQItem] = []
    for index, row in enumerate(frame.to_dict("records"), start=2):
        item = _build_item(row, mapping, index, registry.code)
        item.upload_id = upload.id
        db.add(item)
        items.append(item)
    db.commit()

    for item in items:
        db.refresh(item)

    counts: dict[str, int] = {}
    for item in items:
        counts[item.smm2_section] = counts.get(item.smm2_section, 0) + 1
    unclassified = counts.get(classifier.UNCLASSIFIED, 0)

    warnings: list[str] = []
    if unclassified:
        warnings.append(
            f"{unclassified} of {len(items)} line(s) could not be matched to a section and are "
            "shown as Unclassified. Reclassify them from the Variance Table before relying on "
            "the benchmark."
        )
    if any(item.is_placeholder for item in items):
        warnings.append(
            "Some lines in this file are flagged is_placeholder = true, i.e. indicative sample "
            "data rather than a real tender."
        )

    # Lines that came from the template carry the schedule code they were quoted from. A
    # line WITHOUT one was added by the analyst, so the loaded schedule has no rate for it
    # and it will need a manual rate before the should-cost is complete. Only worth saying
    # when the file actually used the template - on a plain upload every line is codeless,
    # and "1,876 lines are not in the schedule" would be noise rather than information.
    coded = [item for item in items if (item.sor_code or "").strip()]
    if coded and len(coded) < len(items):
        added = len(items) - len(coded)
        # ... minus any the file already told us about as unclassified, which is a
        # different problem with a different fix.
        warnings.append(
            f"{added} of {len(items)} line(s) are NOT in the loaded schedule of rates "
            f"(sor_code is blank), so they came from your own bill rather than the template. "
            f"Any that the library also cannot price - shown in the Coverage panel - need a "
            f"manual rate before the should-cost is complete."
        )

    return schemas.UploadOut(
        upload_id=upload.id,
        country=upload.country,
        region_code=upload.region_code,
        filename=upload.filename,
        uploaded_at=upload.uploaded_at,
        currency=upload.currency,
        row_count=len(items),
        classified_count=len(items) - unclassified,
        unclassified_count=unclassified,
        items=[schemas.BoQItemOut.model_validate(i) for i in items],
        counts_by_section=dict(sorted(counts.items())),
        sections_summary=_sections_summary(registry.code, items, counts, library_sections(db, registry.code)),
        sor_catalogue=boq_template.catalogue_totals(registry.code),
        warnings=warnings,
        assumptions=[
            f"Classification is an automated keyword match against the "
            f"{registry.measurement_standard} rule table, refined by the "
            f"{boq_template.catalogue_totals(registry.code)['sor_items']:,}-item schedule of rates for "
            f"this market: a description that matches a schedule item exactly is placed in that "
            f"item's section. It is basis=derived and must be reviewed by a quantity surveyor.",
            f"Region set to {region.region_name} ({region.region_code}), carrying a "
            f"{region.factor:.3f} multiplier on benchmark base rates. Change it on the "
            f"benchmark run if this BoQ is for a different location.",
        ],
    )


def library_sections(db: Session, country_code: str) -> set[str]:
    """Sections the benchmark rate library can actually price for this market."""
    return {name for name in load_benchmark_rates(db, country=country_code)}


def _sections_summary(
    country_code: str,
    items: list[BoQItem],
    counts: dict[str, int],
    priceable: set[str],
) -> list[dict]:
    """The per-section summary, with the schedule of rates factored in.

    One row per section in the upload, and it answers the questions a QS asks of an
    upload before trusting it:

      lines                     how much of the bill sits in this section
      from_sor_template         how many lines carry a schedule code, i.e. came from the
                                template rather than being written by hand
      matched_sor_description   how many lines are a schedule item's own wording, whether
                                or not the code column survived the edit
      sor_items_available       how many schedule items this market has for the section -
                                the ceiling on how much of the section the template covers
      sor_items_bookable        of those, how many are in a comparable unit
      benchmark_rate_available  whether the rate library can price the section at all

    A section the library cannot price is the actionable row: those lines can only be
    benchmarked against a manual rate.
    """
    per_section = {
        entry["smm2_section"]: entry for entry in boq_template.sections_summary(country_code)
    }
    matched_descriptions = {
        item.id
        for item in items
        if boq_template.match_description(country_code, item.raw_description) is not None
    }

    rows: list[dict] = []
    for section in sorted(counts, key=lambda name: (-counts[name], name)):
        members = [item for item in items if item.smm2_section == section]
        catalogue_entry = per_section.get(section, {})
        rows.append(
            {
                "smm2_section": section,
                "lines": len(members),
                "from_sor_template": sum(1 for item in members if (item.sor_code or "").strip()),
                "matched_sor_description": sum(1 for item in members if item.id in matched_descriptions),
                "sor_items_available": catalogue_entry.get("sor_items", 0),
                "sor_items_bookable": catalogue_entry.get("bookable", 0),
                "benchmark_rate_available": section in priceable,
            }
        )
    return rows


@router.get("", response_model=list[schemas.UploadSummaryOut])
def list_uploads(
    country: str | None = Query(None, description="SG | IN. Omit for every market."),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[schemas.UploadSummaryOut]:
    """Recent BoQ uploads, newest first.

    The UI uses this to offer the seeded demonstration bills without the user
    having to remember an id.
    """
    statement = select(BoQUpload).order_by(BoQUpload.id.desc()).limit(limit)
    if country:
        statement = select(BoQUpload).where(BoQUpload.country == resolve_country(country).code).order_by(
            BoQUpload.id.desc()
        ).limit(limit)
    uploads = list(db.scalars(statement))
    counts: dict[int, int] = {}
    if uploads:
        rows = db.execute(
            select(BoQItem.upload_id, func.count())
            .where(BoQItem.upload_id.in_([u.id for u in uploads]))
            .group_by(BoQItem.upload_id)
        ).all()
        counts = {upload_id: total for upload_id, total in rows}
    return [
        schemas.UploadSummaryOut(
            upload_id=upload.id,
            country=upload.country,
            region_code=upload.region_code,
            filename=upload.filename,
            uploaded_at=upload.uploaded_at,
            currency=upload.currency,
            row_count=counts.get(upload.id, 0),
            is_seeded_sample=upload.filename.startswith("sample_boq"),
        )
        for upload in uploads
    ]


@router.get("/{upload_id}", response_model=schemas.UploadDetailOut)
def get_upload(upload_id: int, db: Session = Depends(get_db)) -> schemas.UploadDetailOut:
    upload = db.get(BoQUpload, upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"BoQ upload {upload_id} does not exist."
        )
    items = list(db.scalars(select(BoQItem).where(BoQItem.upload_id == upload_id).order_by(BoQItem.id)))
    counts: dict[str, int] = {}
    for item in items:
        counts[item.smm2_section] = counts.get(item.smm2_section, 0) + 1
    return schemas.UploadDetailOut(
        upload_id=upload.id,
        country=upload.country,
        region_code=upload.region_code,
        filename=upload.filename,
        uploaded_at=upload.uploaded_at,
        tender_quarter=upload.tender_quarter,
        tpi_series_name=upload.tpi_series_name,
        currency=upload.currency,
        items=[schemas.BoQItemOut.model_validate(i) for i in items],
        counts_by_section=dict(sorted(counts.items())),
        # Reopening an upload shows the same sections summary as uploading it did, so the
        # schedule coverage can be read without re-uploading the file.
        sections_summary=_sections_summary(
            upload.country, items, counts, library_sections(db, upload.country)
        ),
        sor_catalogue=boq_template.catalogue_totals(upload.country),
    )


@router.patch("/item/{item_id}", response_model=schemas.BoQItemOut)
def patch_item(
    item_id: int, payload: schemas.ItemPatchIn, db: Session = Depends(get_db)
) -> schemas.BoQItemOut:
    """Manually reclassify one BoQ line. Marks the line classified_by = manual."""
    item = db.get(BoQItem, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"BoQ item {item_id} does not exist."
        )
    section = payload.smm2_section.strip()
    valid = set(classifier.SMM2_SECTIONS) | {classifier.UNCLASSIFIED}
    if section not in valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown section {section!r}. Valid sections: {', '.join(sorted(valid))}.",
        )
    item.smm2_section = section
    item.classified_by = "manual"
    db.commit()
    db.refresh(item)
    return schemas.BoQItemOut.model_validate(item)


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def _upload_region(db: Session, upload: BoQUpload, requested: str | None) -> str | None:
    """Region for a run: explicit request wins, else the upload's, else the country default."""
    try:
        return resolve_region(db, upload.country, requested or upload.region_code).region_code
    except RegionLookupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _benchmark_for_export(db: Session, upload: BoQUpload, items: list[BoQItem], request, filename: str):
    try:
        return build_benchmark(
            db,
            upload_id=upload.id,
            filename=filename,
            items=items,
            tender_quarter=request.tender_quarter,
            tpi_series_name=request.tpi_series_name,
            variance_threshold=request.variance_threshold,
            country=upload.country,
            currency=upload.currency,
            region_code=_upload_region(db, upload, getattr(request, "region_code", None)),
            adjustments=request.adjustments,
            manual_rates=getattr(request, "manual_rates", None),
            index_bridge=getattr(request, "index_bridge", "auto"),
        )
    except RegionLookupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TPILookupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _report_rows(computation, items: list[BoQItem]) -> list[dict]:
    """Build a single self-describing CSV that IS the report.

    Long format: block, ref, item, value, basis. One file carries the header,
    the totals, every section, the waterfall, every line, the adjustments
    applied, all warnings, all assumptions and the source list - so the file can
    be handed to someone who never saw the app and still be understood.
    """
    from datetime import datetime, timezone

    registry = get_country(computation.country)
    currency = computation.currency
    adjustments = computation.adjustments_applied or {}
    rows: list[dict] = []

    def add(block: str, ref, item: str, value, basis: str = "") -> None:
        rows.append(
            {
                "block": block,
                "ref": "" if ref is None else ref,
                "item": item,
                "value": "" if value is None else value,
                "basis": basis,
            }
        )

    # ---- header -----------------------------------------------------------------
    add("report", "generated_at_utc", "Timestamp this file was generated", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    add("report", "country", "Market", f"{registry.code} - {registry.name}")
    add("report", "currency", "Currency", currency)
    add("report", "region", "Region", f"{computation.region_code} - {computation.region_name}")
    add("report", "regional_factor", "Regional cost multiplier applied to every benchmark base rate", round(computation.regional_factor, 4), "assumed")
    if computation.regional_factor != 1.0:
        add("report", "regional_factor_source", "Source for the regional multiplier", computation.regional_factor_source)
        add("report", "regional_factor_placeholder", "Regional multiplier is an indicative seed value, not a published city index", computation.regional_factor_is_placeholder)
    add("report", "measurement_standard", "Measurement / classification standard", computation.classification_standard)
    add("report", "boq_file", "Bill of Quantities source file", computation.filename)
    add("report", "tender_quarter", "Tender quarter benchmarked", computation.tender_quarter)
    add("report", "index_series", "Index series used", computation.tpi_series_name)
    add("report", "index_scope_inclusions", "Index scope inclusions", computation.tpi_series_scope_inclusions)
    add("report", "index_scope_exclusions", "Index scope exclusions", computation.tpi_series_scope_exclusions)
    add("report", "variance_threshold_pct", "Breach threshold", computation.variance_threshold)
    # ---- overheads and margin: the inputs that turn the benchmark cost into a ----
    # ---- full should-cost, and the full cost itself ----------------------------
    totals = computation.totals or {}
    add(
        "report",
        "overhead_pct",
        "Overheads added to the benchmark cost (analyst input)",
        totals.get("overhead_pct", 0.0),
        "assumed",
    )
    add(
        "report",
        "margin_pct",
        "Profit margin added after overheads (analyst input)",
        totals.get("margin_pct", 0.0),
        "assumed",
    )
    add(
        "report",
        "overheads_in_tender",
        "Tendered rates are taken to already include overheads and profit",
        totals.get("overheads_in_tender", True),
        "assumed",
    )
    add(
        "report",
        "full_should_cost_total",
        "FULL should-cost: benchmark cost grossed up by overheads and margin, plus "
        "unbenchmarked lines at the tendered rate",
        totals.get("full_should_cost_total"),
        "assumed" if (totals.get("overhead_pct") or totals.get("margin_pct")) else "derived",
    )
    add("report", "full_should_cost_formula",
        "Full cost formula",
        "full_rate = adjusted_benchmark_rate x (1 + overhead_pct/100) x (1 + margin_pct/100)")
    if computation.lines:
        line = computation.lines[0]
        add("report", "index_quarter_used", "Index quarter actually used", line.tpi_quarter_used)
        add("report", "index_value_used", "Index value used", line.tpi_value)
        add("report", "index_value_published", "Index value as published", line.tpi_value_published)
    # ---- index freshness / the carried-forward index ----------------------------
    # The bridge runs on a PRODUCER price index where the market publishes one and a
    # CONSUMER price index only as the fallback, so the report names which kind was
    # used rather than assuming a CPI.
    freshness = computation.index_bridge or {}
    bridge_kind = (freshness.get("kind") or "").upper()
    bridge_kind_label = (
        "producer price index" if bridge_kind == "PPI"
        else ("consumer price index" if bridge_kind == "CPI" else "price index")
    )
    add(
        "report",
        "index_bridge_applied",
        "Index value for the tender quarter was derived by carrying the last published observation "
        "forward along the published trend (modelled, not observed)"
        + (f" - carried with the {bridge_kind_label}" if freshness.get("applied") else ""),
        bool(freshness.get("applied")),
        "assumed" if freshness.get("applied") else "derived",
    )
    if freshness:
        add("report", "index_observation_quarter", "Last published quarter of the index series",
            freshness.get("observation_quarter"), "measured")
        add("report", "index_lag_quarters", "Quarters between the last observation and the tender quarter",
            freshness.get("lag_quarters"), "measured")
        add("report", "index_bridge_formula",
            "Bridge formula",
            "index_value(tender) = index_value(last_observed_quarter) * price_index(covered_through) / price_index(last_observed_quarter)")
        if freshness.get("applied"):
            add("report", "index_bridge_kind",
                "Which price index carried the observation forward (PPI preferred, CPI fallback)",
                bridge_kind_label, "measured")
            add("report", "cpi_series",
                "Price series used to carry the index forward",
                freshness.get("cpi_series_name"), "measured")
            add("report", "cpi_months_from", "Months of that series at the index observation quarter",
                "|".join(freshness.get("cpi_from_months") or []), "measured")
            add("report", "cpi_value_from", "Mean of that series over those months",
                freshness.get("cpi_from_value"), "measured")
            add("report", "cpi_months_to", "Months of that series used at the bridge target",
                "|".join(freshness.get("cpi_to_months") or []), "measured")
            add("report", "cpi_value_to", "Mean of that series over those months",
                freshness.get("cpi_to_value"), "measured")
            add("report", "cpi_bridge_factor", "Factor applied to the index (derived to show the trend to date)",
                freshness.get("cpi_bridge_factor"), "assumed")
            add("report", "index_bridged_through", "Index is derived to this month after carrying forward",
                freshness.get("bridged_through_month"), "assumed")
            add("report", "index_bridge_shortfall_months",
                "Months between the carried-forward month and the end of the tender quarter",
                freshness.get("shortfall_months"), "assumed")
            add("report", "cpi_source_url", "Source of the price series used",
                freshness.get("cpi_source_url"), "measured")
            add("report", "cpi_provenance_note", "Provenance of that price series",
                freshness.get("cpi_provenance_note"), "measured")
        else:
            add("report", "index_bridge_reason", "Why the index was not carried forward",
                freshness.get("reason"), "derived")
    add("report", "formula", "Formula contract", "adjusted_benchmark_rate = base_rate * (index_used / index_base) * scope_factor")

    # ---- totals -----------------------------------------------------------------
    for key, value in computation.totals.items():
        add("totals", key, key.replace("_", " "), value, "derived")

    # ---- sections ---------------------------------------------------------------
    for section in computation.sections:
        name = section["smm2_section"]
        for key in ("item_count", "benchmarked_item_count", "boq_amount", "should_cost_amount",
                    "variance_amount", "variance_pct", "breaches_threshold"):
            add("section", name, key, section.get(key), section.get("basis", ""))

    # ---- waterfall --------------------------------------------------------------
    add("waterfall", "boq_total", "amount", computation.totals["boq_total"], "measured")
    for component in computation.waterfall:
        add("waterfall", component["component"], "amount", component["amount"], component["basis"])
        add("waterfall", component["component"], "method", component["method"])
        add("waterfall", component["component"], "justification", component["justification"])
    add("waterfall", "should_cost_total", "amount", computation.totals["should_cost_total"], "derived")

    # ---- lines ------------------------------------------------------------------
    for line in computation.lines:
        for key in ("raw_description", "unit", "quantity", "boq_rate", "boq_amount",
                    "smm2_section", "benchmark_base_rate", "adjusted_benchmark_rate",
                    "variance_abs", "variance_pct", "should_cost_amount", "variance_amount",
                    "scope_factor", "regional_factor", "rate_scale_pct", "exclusion_reason",
                    "tpi_quarter_used", "tpi_value", "tpi_value_published", "tpi_bridged",
                    "cpi_bridge_factor", "cpi_series_name", "cpi_month_used", "cpi_value_used",
                    "index_lag_quarters", "full_adjusted_benchmark_rate", "overhead_pct",
                    "margin_pct", "overhead_amount", "margin_amount", "full_should_cost_amount",
                    "compared_against_full"):
            add("line", line.item_id, key, getattr(line, key), line.basis)
        add("line", line.item_id, "flags", "|".join(line.flags), line.basis)
        if line.provenance:
            for key in ("source", "source_date", "base_year", "confidence", "is_placeholder"):
                add("line", line.item_id, "benchmark_" + key, line.provenance.get(key), line.basis)
            add("line", line.item_id, "benchmark_scope_inclusions", line.provenance.get("scope_inclusions"), line.basis)
            add("line", line.item_id, "benchmark_scope_exclusions", line.provenance.get("scope_exclusions"), line.basis)
            add("line", line.item_id, "benchmark_replace_with", line.provenance.get("replace_with"), line.basis)

    # ---- adjustments applied ----------------------------------------------------
    add("adjustment", "manual_rate_count", "Number of lines benchmarked on an analyst-supplied rate",
        adjustments.get("manual_rate_count", 0), "assumed")
    add("adjustment", "region_code", "region_code", computation.region_code, "assumed")
    add("adjustment", "regional_factor", "regional_factor", computation.regional_factor, "assumed")
    add("adjustment", "tpi_scale_pct", "Published index shifted by this percent", adjustments.get("tpi_scale_pct"), "assumed")
    add("adjustment", "tpi_value_override", "Absolute index override", adjustments.get("tpi_value_override"), "assumed")
    add("adjustment", "overhead_pct", "Overheads added to the benchmark cost", adjustments.get("overhead_pct"), "assumed")
    add("adjustment", "margin_pct", "Margin added after overheads", adjustments.get("margin_pct"), "assumed")
    add("adjustment", "overhead_amount_total", "Total overheads added", totals.get("overhead_amount_total"), "assumed")
    add("adjustment", "margin_amount_total", "Total margin added", totals.get("margin_amount_total"), "assumed")
    add("adjustment", "full_should_cost_total", "Should-cost including overheads and margin", totals.get("full_should_cost_total"), "assumed")
    add("adjustment", "base_rate_scale_pct", "Every benchmark rate shifted by this percent", adjustments.get("base_rate_scale_pct"), "assumed")
    for section, pct in sorted((adjustments.get("section_rate_scale_pct") or {}).items()):
        add("adjustment", f"section:{section}", "Section rate shift percent", pct, "assumed")

    # ---- warnings, assumptions, sources -----------------------------------------
    for index, warning in enumerate(computation.warnings, start=1):
        add("warning", index, "warning", warning)
    for index, assumption in enumerate(computation.assumptions, start=1):
        add("assumption", index, "assumption", assumption)
    for source in registry.sources:
        add("source", source.name, "url", source.url)
        add("source", source.name, "provides", source.what)
    return rows


def _export_rows(level: str, computation, items: list[BoQItem]) -> list[dict]:
    """Flatten one level of the benchmark result into tabular rows."""
    if level == "report":
        return _report_rows(computation, items)

    if level == "sections":
        return [dict(section) for section in computation.sections]

    if level == "waterfall":
        full_total = computation.totals.get(
            "full_should_cost_total", computation.totals["should_cost_total"]
        )
        rows = [
            {
                "component": "boq_total",
                "amount": computation.totals["boq_total"],
                "basis": "measured",
                "method": "sum(quantity x boq_rate)",
                "justification": "Total of the uploaded Bill of Quantities as tendered.",
            }
        ]
        rows.extend(dict(component) for component in computation.waterfall)
        rows.append(
            {
                "component": "should_cost_total",
                "amount": full_total,
                "basis": "derived",
                "method": "sum(quantity x full_adjusted_benchmark_rate)",
                "justification": (
                    "Full benchmark-derived should-cost for the same scope, including any "
                    "overheads and margin the analyst supplied."
                ),
            }
        )
        return rows

    if level == "summary":
        totals = computation.totals
        adjustments = computation.adjustments_applied
        rows = [{"key": key, "value": value} for key, value in totals.items()]
        rows.extend(
            [
                {"key": "country", "value": computation.country},
                {"key": "currency", "value": computation.currency},
                {"key": "classification_standard", "value": computation.classification_standard},
                {"key": "tender_quarter", "value": computation.tender_quarter},
                {"key": "tpi_series_name", "value": computation.tpi_series_name},
                {"key": "variance_threshold", "value": computation.variance_threshold},
                {"key": "tpi_scale_pct", "value": adjustments.get("tpi_scale_pct")},
                {"key": "base_rate_scale_pct", "value": adjustments.get("base_rate_scale_pct")},
                {"key": "tpi_value_override", "value": adjustments.get("tpi_value_override")},
                {"key": "index_bridge_applied",
                 "value": bool((computation.index_bridge or {}).get("applied"))},
                {"key": "index_observation_quarter",
                 "value": (computation.index_bridge or {}).get("observation_quarter")},
                {"key": "index_lag_quarters",
                 "value": (computation.index_bridge or {}).get("lag_quarters")},
                {"key": "cpi_bridge_factor",
                 "value": (computation.index_bridge or {}).get("cpi_bridge_factor")},
                {"key": "index_bridged_through_month",
                 "value": (computation.index_bridge or {}).get("bridged_through_month")},
                {"key": "overhead_pct", "value": totals.get("overhead_pct", 0.0)},
                {"key": "margin_pct", "value": totals.get("margin_pct", 0.0)},
                {"key": "overheads_in_tender", "value": totals.get("overheads_in_tender", True)},
                {"key": "overhead_amount_total", "value": totals.get("overhead_amount_total", 0.0)},
                {"key": "margin_amount_total", "value": totals.get("margin_amount_total", 0.0)},
                {"key": "full_should_cost_total", "value": totals.get("full_should_cost_total")},
                {"key": "full_variance_abs", "value": totals.get("full_variance_abs")},
                {"key": "full_variance_pct", "value": totals.get("full_variance_pct")},
            ]
        )
        for index, warning in enumerate(computation.warnings, start=1):
            rows.append({"key": f"warning_{index}", "value": warning})
        for index, assumption in enumerate(computation.assumptions, start=1):
            rows.append({"key": f"assumption_{index}", "value": assumption})
        return rows

    # Default: one row per BoQ line, with the benchmark columns embedded.
    by_item = {line.item_id: line for line in computation.lines}
    rows = []
    for item in items:
        row = {
            "item_id": item.id,
            "description": item.raw_description,
            "unit": item.unit,
            "quantity": item.quantity,
            "boq_rate": item.boq_rate,
            "amount": item.amount,
            "smm2_section": item.smm2_section,
            "classified_by": item.classified_by,
            "is_placeholder": item.is_placeholder,
            "replace_with": item.replace_with,
        }
        line = by_item.get(item.id)
        if line is not None:
            row.update(
                {
                    "benchmark_base_rate": line.benchmark_base_rate,
                    "adjusted_benchmark_rate": line.adjusted_benchmark_rate,
                    "variance_abs": line.variance_abs,
                    "variance_pct": line.variance_pct,
                    "should_cost_amount": line.should_cost_amount,
                    "variance_amount": line.variance_amount,
                    "tpi_ratio": line.tpi_ratio,
                    "tpi_quarter_used": line.tpi_quarter_used,
                    "tpi_value": line.tpi_value,
                    "tpi_value_published": line.tpi_value_published,
                    "tpi_bridged": line.tpi_bridged,
                    "cpi_bridge_factor": line.cpi_bridge_factor,
                    "cpi_series_name": line.cpi_series_name,
                    "cpi_month_used": line.cpi_month_used,
                    "index_lag_quarters": line.index_lag_quarters,
                    "full_adjusted_benchmark_rate": line.full_adjusted_benchmark_rate,
                    "overhead_pct": line.overhead_pct,
                    "margin_pct": line.margin_pct,
                    "overhead_amount": line.overhead_amount,
                    "margin_amount": line.margin_amount,
                    "full_should_cost_amount": line.full_should_cost_amount,
                    "scope_factor": line.scope_factor,
                    "rate_scale_pct": line.rate_scale_pct,
                    "basis": line.basis,
                    "flags": "|".join(line.flags),
                }
            )
        rows.append(row)
    return rows


def _stream(rows: list[dict], fmt: str, filename_stem: str) -> StreamingResponse:
    if fmt == "xlsx":
        buffer = io.BytesIO()
        pd.DataFrame(rows).to_excel(buffer, index=False, engine="openpyxl")
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type=XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.xlsx"'
            },
        )
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(
        io.BytesIO(text.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename_stem}.csv"'},
    )


@router.get("/{upload_id}/export")
def export_upload(
    upload_id: int,
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    level: str = Query("items", pattern="^(items|sections|waterfall|summary|report)$"),
    tender_quarter: str | None = Query(None),
    tpi_series_name: str | None = Query(None),
    variance_threshold: float = Query(15.0, ge=0.0, le=1000.0),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Stream the BoQ, section subtotals, waterfall or summary back out.

    Supplying tender_quarter and tpi_series_name embeds the benchmark columns.
    This GET variant carries no index adjustments; use POST for that.
    """
    upload = db.get(BoQUpload, upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"BoQ upload {upload_id} does not exist."
        )
    items = list(db.scalars(select(BoQItem).where(BoQItem.upload_id == upload_id).order_by(BoQItem.id)))
    if not items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"BoQ upload {upload_id} has no line items to export.",
        )

    if tender_quarter and tpi_series_name:
        request = schemas.BenchmarkRequest(
            tender_quarter=tender_quarter,
            tpi_series_name=tpi_series_name,
            variance_threshold=variance_threshold,
        )
        computation = _benchmark_for_export(db, upload, items, request, upload.filename)
        rows = _export_rows(level, computation, items)
    else:
        rows = [
            {
                "item_id": item.id,
                "description": item.raw_description,
                "unit": item.unit,
                "quantity": item.quantity,
                "boq_rate": item.boq_rate,
                "amount": item.amount,
                "smm2_section": item.smm2_section,
                "classified_by": item.classified_by,
                "is_placeholder": item.is_placeholder,
                "replace_with": item.replace_with,
            }
            for item in items
        ]
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"There is nothing to export at level {level!r} for upload {upload_id}.",
        )
    return _stream(rows, format, f"shouldcost-upload-{upload_id}-{level}")


@router.post("/{upload_id}/export")
def export_upload_post(
    upload_id: int,
    payload: schemas.BenchmarkRequest,
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    level: str = Query("items", pattern="^(items|sections|waterfall|summary|report)$"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export the benchmark exactly as configured, INCLUDING index adjustments.

    The UI uses this so the downloaded file always matches what is on screen.
    """
    upload = db.get(BoQUpload, upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"BoQ upload {upload_id} does not exist."
        )
    items = list(db.scalars(select(BoQItem).where(BoQItem.upload_id == upload_id).order_by(BoQItem.id)))
    if not items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"BoQ upload {upload_id} has no line items to export.",
        )
    computation = _benchmark_for_export(db, upload, items, payload, upload.filename)
    rows = _export_rows(level, computation, items)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"There is nothing to export at level {level!r} for upload {upload_id}.",
        )
    return _stream(rows, format, f"shouldcost-upload-{upload_id}-{level}")
