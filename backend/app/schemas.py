"""Pydantic v2 request/response schemas.

Every value that is not directly measured carries an explicit basis:
"measured" | "derived" | "assumed". See README "Basis model".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Basis = Literal["measured", "derived", "assumed"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Country registry
# --------------------------------------------------------------------------- #
class SourceOut(BaseModel):
    name: str
    url: str
    what: str


class CountryOut(BaseModel):
    code: str
    name: str
    currency: str
    currency_symbol: str
    measurement_standard: str
    measurement_note: str
    default_tpi_series: str
    unit_convention: str
    sources: list[SourceOut]


# --------------------------------------------------------------------------- #
# Seed / reference data
# --------------------------------------------------------------------------- #
class TPIPointOut(ORMModel):
    id: int
    country: str
    series_name: str
    quarter: str
    base_year: int
    base_value: float
    currency: str
    value: float
    scope_inclusions: str
    scope_exclusions: str
    source_url: str
    is_placeholder: bool
    provenance_note: str = ""
    replace_with: str = ""


class RegionalFactorOut(ORMModel):
    id: int
    country: str
    region_code: str
    region_name: str
    is_default: bool
    factor: float
    currency: str
    source: str
    source_url: str
    source_date: str
    notes: str
    is_placeholder: bool
    provenance_note: str = ""
    replace_with: str = ""


class MaterialPointOut(ORMModel):
    id: int
    country: str
    material: str
    month: str
    unit: str
    price: float
    currency: str
    frequency: str = "monthly"
    source_url: str
    is_placeholder: bool
    provenance_note: str = ""
    replace_with: str = ""


class BenchmarkRateOut(ORMModel):
    id: int
    country: str
    smm2_section: str
    classification_standard: str
    description: str
    unit: str
    base_rate: float
    currency: str
    base_year: int
    source: str
    source_url: str
    source_date: str
    scope_inclusions: str
    scope_exclusions: str
    confidence: str
    is_placeholder: bool
    provenance_note: str = ""
    replace_with: str = ""


# --------------------------------------------------------------------------- #
# BoQ
# --------------------------------------------------------------------------- #
class BoQItemOut(ORMModel):
    id: int
    upload_id: int
    raw_description: str
    unit: str
    quantity: float
    boq_rate: float
    amount: float
    smm2_section: str
    classified_by: Literal["auto", "manual"]
    is_placeholder: bool
    replace_with: str = ""


class UploadOut(ORMModel):
    upload_id: int
    country: str
    region_code: str | None = None
    filename: str
    uploaded_at: datetime
    currency: str
    row_count: int
    classified_count: int
    unclassified_count: int
    items: list[BoQItemOut]
    counts_by_section: dict[str, int]
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class UploadDetailOut(BaseModel):
    upload_id: int
    country: str
    region_code: str | None = None
    filename: str
    uploaded_at: datetime
    tender_quarter: str | None
    tpi_series_name: str | None
    currency: str
    items: list[BoQItemOut]
    counts_by_section: dict[str, int]


class UploadSummaryOut(BaseModel):
    upload_id: int
    country: str
    region_code: str | None = None
    filename: str
    uploaded_at: datetime
    currency: str
    row_count: int
    is_seeded_sample: bool


class ItemPatchIn(BaseModel):
    smm2_section: str = Field(..., min_length=1, max_length=64)


# --------------------------------------------------------------------------- #
# Manual index adjusters
# --------------------------------------------------------------------------- #
class IndexAdjustments(BaseModel):
    """Analyst overrides applied on top of the published index values.

    Every field here is a user SUPPLY-SIDE ASSUMPTION, not an observation. Any
    line touched by a non-zero adjustment is reported with basis="assumed".
    """

    tpi_value_override: float | None = Field(
        None, gt=0, le=10000,
        description="Absolute override of the resolved index value, replacing the published value.",
    )
    tpi_scale_pct: float = Field(
        0.0, ge=-90, le=300,
        description="Percentage shift applied to the resolved index value. Positive = market hotter.",
    )
    base_rate_scale_pct: float = Field(
        0.0, ge=-90, le=300,
        description="Percentage shift applied to every benchmark base rate.",
    )
    section_rate_scale_pct: dict[str, float] = Field(
        default_factory=dict,
        description="Per-section percentage shift on the benchmark base rate, keyed by section name.",
    )

    def is_noop(self) -> bool:
        return (
            self.tpi_value_override is None
            and self.tpi_scale_pct == 0.0
            and self.base_rate_scale_pct == 0.0
            and not any(v for v in self.section_rate_scale_pct.values())
        )


class ManualRate(BaseModel):
    """An analyst-supplied benchmark rate for one BoQ line.

    Exists so that no line has to be left unbenchmarked. A line the classifier
    could not place, or whose unit did not match the library, is otherwise
    carried at the tendered rate and contributes no tested variance at all.
    """

    base_rate: float = Field(..., gt=0, le=100_000_000)
    indexed: bool = Field(
        True,
        description=(
            "True (default): the rate is at the benchmark library's base year and is indexed, "
            "scoped and regionally adjusted like a library rate. False: the analyst states the "
            "rate at tender-quarter price levels, so no index is applied."
        ),
    )
    note: str = Field("", max_length=500, description="Why this rate was chosen. Recorded in the report.")


# --------------------------------------------------------------------------- #
# Benchmarking
# --------------------------------------------------------------------------- #
class BenchmarkRequest(BaseModel):
    tender_quarter: str = Field(..., description="e.g. 2024Q4")
    tpi_series_name: str = Field(..., description="SG: BCA | HDB | RLB | AECOM. IN: CPWD | NBO | WPI-CON")
    variance_threshold: float = Field(15.0, ge=0.0, le=1000.0)
    region_code: str | None = Field(
        None, description="Regional cost multiplier within the country, e.g. IN: DEL | MUM | BLR."
    )
    adjustments: IndexAdjustments | None = None
    manual_rates: dict[str, ManualRate] = Field(
        default_factory=dict,
        description="Analyst-supplied benchmark rates keyed by BoQ item id, so every line is benchmarked.",
    )


class Provenance(BaseModel):
    source: str
    source_date: str
    base_year: int
    scope_inclusions: str
    scope_exclusions: str
    confidence: str
    is_placeholder: bool
    source_url: str | None = None
    replace_with: str = ""


class BenchmarkLine(ORMModel):
    item_id: int
    raw_description: str
    unit: str
    quantity: float
    boq_rate: float
    boq_amount: float
    smm2_section: str
    classified_by: Literal["auto", "manual"]

    is_benchmarked: bool
    exclusion_reason: str | None = None

    benchmark_base_rate: float | None = None
    adjusted_benchmark_rate: float | None = None
    variance_abs: float | None = None
    variance_pct: float | None = None
    should_cost_amount: float
    variance_amount: float

    tpi_series_name: str
    tpi_quarter_requested: str
    tpi_quarter_used: str
    tpi_value: float
    tpi_value_published: float
    tpi_base_value: float
    tpi_ratio: float
    tpi_fallback_used: bool

    scope_factor: float
    scope_excluded: bool
    rate_scale_pct: float = 0.0
    regional_factor: float = 1.0
    region_code: str = ""
    from_library: bool = True
    user_adjusted: bool = False

    basis: Basis
    flags: list[str] = Field(default_factory=list)
    provenance: Provenance | None = None


class SectionAggregate(ORMModel):
    smm2_section: str
    item_count: int
    benchmarked_item_count: int
    boq_amount: float
    should_cost_amount: float
    variance_amount: float
    variance_abs: float
    variance_pct: float | None
    basis: Basis
    breaches_threshold: bool


class WaterfallComponent(ORMModel):
    component: Literal["material", "labour", "market_risk", "scope", "unexplained"]
    amount: float
    basis: Basis
    method: str
    justification: str


class BenchmarkTotals(ORMModel):
    boq_total: float
    should_cost_total: float
    total_variance_abs: float
    total_variance_pct: float | None
    benchmarked_boq_total: float
    unbenchmarked_boq_total: float
    unbenchmarked_line_count: int
    line_count: int
    breached_line_count: int
    basis: Basis


class BenchmarkResponse(BaseModel):
    upload_id: int
    country: str
    country_name: str
    currency: str
    classification_standard: str
    measurement_standard: str
    filename: str
    tender_quarter: str
    tpi_series_name: str
    variance_threshold: float
    tpi_series_scope_inclusions: str
    tpi_series_scope_exclusions: str
    region_code: str
    region_name: str
    regional_factor: float
    regional_factor_is_placeholder: bool
    regional_factor_source: str
    adjustments_applied: dict
    lines: list[BenchmarkLine]
    sections: list[SectionAggregate]
    totals: BenchmarkTotals
    waterfall: list[WaterfallComponent]
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Sensitivity analysis
# --------------------------------------------------------------------------- #
class SensitivityRequest(BaseModel):
    tender_quarter: str
    tpi_series_name: str
    variance_threshold: float = Field(15.0, ge=0.0, le=1000.0)
    tpi_scale_min_pct: float = Field(-20.0, ge=-90, le=300)
    tpi_scale_max_pct: float = Field(20.0, ge=-90, le=300)
    tpi_scale_step_pct: float = Field(5.0, gt=0, le=100)
    section_scale_pct: float = Field(
        10.0, gt=0, le=100,
        description="Symmetric +/- percentage applied to one section at a time for the tornado.",
    )
    region_code: str | None = None
    adjustments: IndexAdjustments | None = None
    manual_rates: dict[str, ManualRate] = Field(default_factory=dict)


class SensitivityPoint(BaseModel):
    tpi_scale_pct: float
    tpi_value: float
    should_cost_total: float
    total_variance_abs: float
    total_variance_pct: float | None
    breached_line_count: int
    lines_over: int
    lines_under: int
    is_baseline: bool


class SectionSensitivity(BaseModel):
    smm2_section: str
    boq_amount: float
    should_cost_amount: float
    share_of_should_cost_pct: float
    delta_low: float
    delta_high: float
    swing: float


class SensitivityResponse(BaseModel):
    upload_id: int
    country: str
    country_name: str
    currency: str
    region_code: str
    region_name: str
    regional_factor: float
    tender_quarter: str
    tpi_series_name: str
    variance_threshold: float
    section_scale_pct: float
    baseline_tpi_value: float
    baseline: BenchmarkTotals
    break_even_scale_pct: float | None
    break_even_tpi_value: float | None
    tpi_sweep: list[SensitivityPoint]
    section_tornado: list[SectionSensitivity]
    most_sensitive_section: str | None
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class HealthOut(BaseModel):
    status: str
    db: str
    environment: str
