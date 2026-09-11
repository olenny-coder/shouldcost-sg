"""SQLAlchemy 2.0 ORM models.

Column sets follow the agreed data model. Two provenance columns are added on
top of it - `replace_with` and `is_placeholder` - because hard rule 1 requires
every synthetic value to be traceable to the source it must eventually be
replaced from, and that traceability has to survive into the API and the UI.

Monetary columns are stored as FLOAT rather than NUMERIC. Rationale: the app
rounds to 2 decimal places only at the API boundary, SQLite has no true fixed
point type, and NUMERIC round-trips through psycopg2 as Decimal which Pydantic v2
serialises as a JSON *string* - that would silently break arithmetic in the
frontend. See README "Known deviations".
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TPISeries(Base):
    """Tender Price Index observation for one series in one quarter."""

    __tablename__ = "tpi_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True, default="SG")
    series_name: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    quarter: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    base_year: Mapped[int] = mapped_column(Integer, nullable=False)
    # Index value at base_year. Definitionally 100 for a rebased index, but stored
    # explicitly so a series published on another base can be carried without
    # silently assuming 100.
    base_value: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="SGD")
    value: Mapped[float] = mapped_column(Float, nullable=False)
    scope_inclusions: Mapped[str] = mapped_column(Text, nullable=False)
    scope_exclusions: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # For a REAL row this explains how the value was obtained (which published
    # series, what transformation). For a placeholder row this is empty and
    # replace_with carries the TODO instead.
    provenance_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    replace_with: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint(
            "country", "series_name", "quarter", "base_year", name="uq_tpi_series_country_name_quarter"
        ),
    )


class MaterialPrice(Base):
    """Monthly material price observation (SingStat / BCA material price series)."""

    __tablename__ = "material_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True, default="SG")
    material: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="SGD")
    # annual | quarterly | monthly. The period label in `month` follows the source,
    # so an annual series stores "2025" and a monthly one "2025-04".
    frequency: Mapped[str] = mapped_column(String(12), nullable=False, default="monthly")
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    provenance_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    replace_with: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint("country", "material", "month", name="uq_material_prices_country_material_month"),
    )


class CPISeries(Base):
    """Monthly consumer price index observation.

    Published TPI / WPI series lag the tender quarter: the BCA series is a
    quarterly release, and the WPI for a month appears about two months after the
    month ends. The CPI for the same country is published monthly and is
    therefore the most timely official price indicator available.

    When the requested tender quarter is later than the last observation of the
    selected index series, the engine carries that last observation forward by
    the observed CPI movement between the two quarters. That bridge is a MODELLED
    step, not an observation of construction cost, so it is disclosed as an
    assumption on every affected line. See README "Keeping the indexes current".
    """

    __tablename__ = "cpi_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True, default="SG")
    # e.g. CPI-ALL - the all-items series. The bridge uses one series per country.
    series_name: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    base_year: Mapped[int] = mapped_column(Integer, nullable=False, default=2024)
    base_value: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="SGD")
    value: Mapped[float] = mapped_column(Float, nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provenance_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    replace_with: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint("country", "series_name", "month", name="uq_cpi_series_country_series_month"),
    )


class BenchmarkRate(Base):
    """SMM2-section benchmark rate at a stated base year."""

    __tablename__ = "benchmark_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True, default="SG")
    # For Singapore this is an SMM2 section; for India the canonical section names are
    # shared but drawn from the IS 1200 / CPWD DSR chapter structure. The column keeps
    # its original name so the API stays backward compatible.
    smm2_section: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    classification_standard: Mapped[str] = mapped_column(String(32), nullable=False, default="SMM2")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    base_rate: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="SGD")
    base_year: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    source_date: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_inclusions: Mapped[str] = mapped_column(Text, nullable=False)
    scope_exclusions: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    provenance_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    replace_with: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint(
            "country", "smm2_section", "description", "unit", "base_year", "source",
            name="uq_benchmark_rates_natural_key",
        ),
    )


class BoQUpload(Base):
    """One uploaded Bill of Quantities file and the parameters it was parsed with."""

    __tablename__ = "boq_uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True, default="SG")
    region_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    tender_quarter: Mapped[str | None] = mapped_column(String(8), nullable=True)
    tpi_series_name: Mapped[str | None] = mapped_column(String(16), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="SGD")

    items: Mapped[list["BoQItem"]] = relationship(
        back_populates="upload", cascade="all, delete-orphan", order_by="BoQItem.id"
    )


class BoQItem(Base):
    """One measured line item from an uploaded BoQ."""

    __tablename__ = "boq_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upload_id: Mapped[int] = mapped_column(
        ForeignKey("boq_uploads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_description: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    boq_rate: Mapped[float] = mapped_column(Float, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    smm2_section: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    classified_by: Mapped[str] = mapped_column(String(8), nullable=False, default="auto")
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    replace_with: Mapped[str] = mapped_column(Text, nullable=False, default="")

    upload: Mapped[BoQUpload] = relationship(back_populates="items")


class RegionalFactor(Base):
    """Regional cost adjustment within a country.

    India's construction cost varies materially by city - labour rates are set by
    state minimum-wage notifications and materials carry different haulage and
    local-levy costs. A single national rate library would misprice every project
    outside the reference city, so each region carries a multiplier applied to the
    benchmark base rates.

    The multiplier is a SINGLE blended factor, not a material/labour split:
    labour-heavy sections (Formwork, Plaster, Masonry, Preliminaries) are more
    regionally variable than material-driven ones (Concrete, Reinforcement). That
    limitation is stated in `notes` and surfaced to the user.
    """

    __tablename__ = "regional_factors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    region_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    region_name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    factor: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source: Mapped[str] = mapped_column(String(160), nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    source_date: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    provenance_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    replace_with: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint("country", "region_code", name="uq_regional_factors_country_region"),
    )


Index("ix_boq_items_upload_section", BoQItem.upload_id, BoQItem.smm2_section)
