"""Should-cost benchmark engine.

Formula contract (do not change without updating README and the tests):

    adjusted_benchmark_rate = base_rate * (current_tpi / base_tpi) * scope_factor
    variance_abs            = boq_rate - adjusted_benchmark_rate
    variance_pct            = (variance_abs / adjusted_benchmark_rate) * 100
    should_cost_amount      = quantity * adjusted_benchmark_rate

Two multipliers are layered on top of the published values, both of which are
ANALYST ASSUMPTIONS rather than observations:

    current_tpi  = (published_tpi or tpi_value_override) * (1 + tpi_scale_pct/100)
    base_rate    = published_base_rate * (1 + (base_rate_scale_pct + section_scale_pct)/100)

Any line touched by a non-zero multiplier is reported with basis="assumed" and
flagged user_adjusted, and the whole adjustment set is restated in assumptions[].

scope_factor is 1.0 by default. It only moves away from 1.0 when the selected TPI
series explicitly EXCLUDES a section that is present in the BoQ (the canonical
case being a BoQ containing Piling while the RLB or CPWD series excludes piling).
In that case the section is not re-priced by an index that does not measure it, so
scope_factor = 1 / tpi_ratio and the rate is held at base year.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import classifier
from .countries import DEFAULT_COUNTRY, Country, get_country
from .models import BenchmarkRate, BoQItem, RegionalFactor, TPISeries

BASIS_MEASURED = "measured"
BASIS_DERIVED = "derived"
BASIS_ASSUMED = "assumed"

UNCLASSIFIED = classifier.UNCLASSIFIED

# Fallback index value at the base year when a series row does not carry one.
# A rebased index is 100 at its base year by construction, but the value is
# stored per row so a series published on another base can be carried explicitly.
# TODO: replace with the actual base value published for each index series.
DEFAULT_BASE_YEAR_INDEX_VALUE = 100.0

# Assumed split of the rate gap between the tendered BoQ rate and the base-year
# benchmark rate. This is an APPORTIONMENT, not a measurement.
# TODO: replace with measured material/labour/plant splits from a rate build-up
# so the material and labour waterfall bars become basis=measured.
MATERIAL_SHARE = 0.55
LABOUR_SHARE = 0.45

CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}

_QUARTER_RE = re.compile(r"^(\d{4})\s*[Qq]([1-4])$")

# Exclusion phrases used by the published series, mapped to the canonical
# sections they remove from scope. An empty set means the exclusion has no
# counterpart in the benchmark rate library, so nothing fires.
# TODO: replace with the actual scope wording from the BCA TPI release notes, the
# SISV Tender Price Index circulars, the CPWD cost index circular and the WPI
# technical notes.
EXCLUSION_ALIASES: dict[str, set[str]] = {
    "piling": {"Piling"},
    "substructure": {"Piling", "Excavation"},
    "external works": set(),
    "external works and ancillaries": set(),
    "m&e": {"M&E Containment"},
    "m&e services": {"M&E Containment"},
    "m&e works": {"M&E Containment"},
    "mechanical & electrical": {"M&E Containment"},
    "mechanical and electrical services": {"M&E Containment"},
    "preliminaries": {"Preliminaries"},
    "preliminaries and general requirements": {"Preliminaries"},
}

UNIT_ALIASES: dict[str, str] = {
    "m2": "m2", "sqm": "m2", "m^2": "m2", "square metre": "m2", "square meter": "m2",
    "m3": "m3", "cum": "m3", "m^3": "m3", "cubic metre": "m3", "cubic meter": "m3",
    "m": "m", "lm": "m", "rm": "m", "metre": "m", "meter": "m", "linear metre": "m",
    "t": "t", "ton": "t", "tonne": "t", "mt": "t",
    "item": "item", "sum": "item", "lsum": "item", "no": "item", "nr": "item",
    "nos": "item", "number": "item", "lump sum": "item",
}


class TPILookupError(ValueError):
    """Raised when no usable TPI observation exists for the requested input."""


# --------------------------------------------------------------------------- #
# Quarter helpers
# --------------------------------------------------------------------------- #
def parse_quarter(quarter: str) -> tuple[int, int]:
    match = _QUARTER_RE.match((quarter or "").strip())
    if not match:
        raise ValueError(
            f"Invalid quarter {quarter!r}. Expected the form YYYYQn, for example 2024Q4."
        )
    return int(match.group(1)), int(match.group(2))


def quarter_sort_key(quarter: str) -> int:
    year, q = parse_quarter(quarter)
    return year * 4 + q


def format_quarter(year: int, q: int) -> str:
    return f"{year}Q{q}"


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #
def normalise_unit(unit: str | None) -> str:
    raw = (unit or "").strip().lower()
    # Normalise unicode superscripts (m2, m3) and the caret form (m^2) before lookup.
    for superscript, digit in (("\u00b2", "2"), ("\u00b3", "3"), ("^2", "2"), ("^3", "3")):
        raw = raw.replace(superscript, digit)
    return UNIT_ALIASES.get(raw, raw)


# --------------------------------------------------------------------------- #
# TPI resolution
# --------------------------------------------------------------------------- #
@dataclass
class ResolvedTPI:
    country: str
    currency: str
    series_name: str
    requested_quarter: str
    resolved_quarter: str
    value: float
    base_value: float
    base_year: int
    scope_inclusions: str
    scope_exclusions: str
    source_url: str
    is_placeholder: bool
    replace_with: str
    fallback_used: bool

    def ratio_for(self, current_value: float) -> float:
        if not self.base_value:
            raise TPILookupError("TPI base value is zero; cannot compute a ratio.")
        return current_value / self.base_value

    @property
    def ratio(self) -> float:
        return self.ratio_for(self.value)


def resolve_tpi(
    session: Session, series_name: str, tender_quarter: str, country: str = DEFAULT_COUNTRY
) -> ResolvedTPI:
    """Resolve a TPI observation for one country.

    Fallback order: exact series + exact quarter, then exact series + nearest
    PRIOR quarter. Anything else raises TPILookupError with an explicit message.
    """
    requested = quarter_sort_key(tender_quarter)  # validates the format
    code = (country or DEFAULT_COUNTRY).strip().upper()
    name = (series_name or "").strip().upper()
    rows = list(
        session.scalars(
            select(TPISeries).where(
                TPISeries.country == code, TPISeries.series_name == name
            )
        ).all()
    )
    if not rows:
        available = sorted(
            {
                s
                for s in session.scalars(
                    select(TPISeries.series_name).where(TPISeries.country == code)
                ).all()
            }
        )
        raise TPILookupError(
            f"No TPI series named {series_name!r} is loaded for country {code}. Available "
            f"series: {', '.join(available) if available else 'none - run python -m app.etl'}."
        )

    by_key = {quarter_sort_key(r.quarter): r for r in rows}
    if requested in by_key:
        return _build_resolved(by_key[requested], tender_quarter, fallback=False)

    prior = sorted((k for k in by_key if k < requested), reverse=True)
    if not prior:
        earliest = min(by_key)
        raise TPILookupError(
            f"No {code} TPI observation for series {name} on or before {tender_quarter}. "
            f"The earliest {name} observation loaded is "
            f"{format_quarter(earliest // 4, earliest % 4)}."
        )
    return _build_resolved(by_key[prior[0]], tender_quarter, fallback=True)


def _build_resolved(row: TPISeries, requested_quarter: str, *, fallback: bool) -> ResolvedTPI:
    return ResolvedTPI(
        country=row.country,
        currency=row.currency,
        series_name=row.series_name,
        requested_quarter=requested_quarter,
        resolved_quarter=row.quarter,
        value=row.value,
        base_value=row.base_value or DEFAULT_BASE_YEAR_INDEX_VALUE,
        base_year=row.base_year,
        scope_inclusions=row.scope_inclusions,
        scope_exclusions=row.scope_exclusions,
        source_url=row.source_url,
        is_placeholder=row.is_placeholder,
        replace_with=row.replace_with,
        fallback_used=fallback,
    )


# --------------------------------------------------------------------------- #
# Scope-exclusion detection
# --------------------------------------------------------------------------- #
def _normalise_token(token: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9&]+", " ", (token or "").lower())).strip()


def excluded_sections(scope_exclusions: str, present_sections) -> dict[str, str]:
    """Return {section: exclusion_phrase} for sections removed by the series."""
    present = {s for s in present_sections if s and s != UNCLASSIFIED}
    tokens = [t for t in re.split(r"[;,]", scope_exclusions or "") if t.strip()]
    hits: dict[str, str] = {}
    for token in tokens:
        phrase = token.strip()
        key = _normalise_token(phrase)
        targets = set(EXCLUSION_ALIASES.get(key, set()))
        if not targets:
            for section in present:
                section_key = _normalise_token(section)
                if section_key and (section_key in key or key in section_key):
                    targets.add(section)
        for section in targets & present:
            hits.setdefault(section, phrase)
    return hits


# --------------------------------------------------------------------------- #
# Benchmark rate selection
# --------------------------------------------------------------------------- #
def load_benchmark_rates(
    session: Session, country: str = DEFAULT_COUNTRY
) -> dict[str, BenchmarkRate]:
    """Index benchmark rates by section for one country, preferring the highest confidence."""
    code = (country or DEFAULT_COUNTRY).strip().upper()
    rows = list(session.scalars(select(BenchmarkRate).where(BenchmarkRate.country == code)).all())
    chosen: dict[str, BenchmarkRate] = {}
    for row in rows:
        current = chosen.get(row.smm2_section)
        if current is None:
            chosen[row.smm2_section] = row
            continue
        if CONFIDENCE_ORDER.get(row.confidence, 9) < CONFIDENCE_ORDER.get(current.confidence, 9):
            chosen[row.smm2_section] = row
    return chosen


# --------------------------------------------------------------------------- #
# Regional cost adjustment
# --------------------------------------------------------------------------- #
class RegionLookupError(ValueError):
    """Raised when a region cannot be resolved for a country."""


@dataclass
class ResolvedRegion:
    country: str
    region_code: str
    region_name: str
    factor: float
    currency: str
    source: str
    source_url: str
    source_date: str
    notes: str
    is_placeholder: bool
    provenance_note: str
    replace_with: str


def resolve_region(
    session: Session, country: str, region_code: str | None = None
) -> ResolvedRegion:
    """Resolve the regional cost multiplier for a country.

    Falls back to the country's declared default region when none is given.
    """
    code = (country or DEFAULT_COUNTRY).strip().upper()
    statement = select(RegionalFactor).where(RegionalFactor.country == code)
    if region_code:
        statement = statement.where(RegionalFactor.region_code == region_code.strip().upper())
    else:
        statement = statement.where(RegionalFactor.is_default.is_(True))
    row = session.scalar(statement)
    if row is None:
        available = sorted(
            session.scalars(
                select(RegionalFactor.region_code).where(RegionalFactor.country == code)
            ).all()
        )
        if not available:
            raise RegionLookupError(
                f"No regions are loaded for country {code}. Run python -m app.etl."
            )
        raise RegionLookupError(
            f"Unknown region {region_code!r} for country {code}. "
            f"Available regions: {', '.join(available)}."
        )
    return ResolvedRegion(
        country=row.country,
        region_code=row.region_code,
        region_name=row.region_name,
        factor=row.factor,
        currency=row.currency,
        source=row.source,
        source_url=row.source_url,
        source_date=row.source_date,
        notes=row.notes,
        is_placeholder=row.is_placeholder,
        provenance_note=row.provenance_note,
        replace_with=row.replace_with,
    )


# --------------------------------------------------------------------------- #
# Result dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class LineResult:
    item_id: int
    raw_description: str
    unit: str
    quantity: float
    boq_rate: float
    boq_amount: float
    smm2_section: str
    classified_by: str
    is_benchmarked: bool
    exclusion_reason: str | None
    benchmark_base_rate: float | None
    adjusted_benchmark_rate: float | None
    variance_abs: float | None
    variance_pct: float | None
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
    rate_scale_pct: float
    regional_factor: float
    region_code: str
    from_library: bool
    user_adjusted: bool
    basis: str
    flags: list[str] = field(default_factory=list)
    provenance: dict | None = None


@dataclass
class BenchmarkComputation:
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
    lines: list[LineResult]
    sections: list[dict]
    totals: dict
    waterfall: list[dict]
    warnings: list[str]
    assumptions: list[str]


# --------------------------------------------------------------------------- #
# Adjustment helpers
# --------------------------------------------------------------------------- #
def _adjustments_dict(adjustments) -> dict:
    if adjustments is None:
        return {
            "tpi_value_override": None,
            "tpi_scale_pct": 0.0,
            "base_rate_scale_pct": 0.0,
            "section_rate_scale_pct": {},
            "is_noop": True,
        }
    raw = adjustments.model_dump() if hasattr(adjustments, "model_dump") else dict(adjustments)
    section_map = {
        str(k): float(v) for k, v in (raw.get("section_rate_scale_pct") or {}).items() if v
    }
    is_noop = (
        raw.get("tpi_value_override") is None
        and not raw.get("tpi_scale_pct")
        and not raw.get("base_rate_scale_pct")
        and not section_map
    )
    return {
        "tpi_value_override": raw.get("tpi_value_override"),
        "tpi_scale_pct": float(raw.get("tpi_scale_pct") or 0.0),
        "base_rate_scale_pct": float(raw.get("base_rate_scale_pct") or 0.0),
        "section_rate_scale_pct": section_map,
        "is_noop": is_noop,
    }


def _effective_tpi_value(published: float, applied: dict) -> float:
    """Compose the two index adjusters.

    An absolute override replaces the PUBLISHED value; the percentage shift is
    then applied on top of whichever level was chosen. Composing them this way
    keeps a sensitivity sweep meaningful even when an override is in force.
    """
    level = (
        float(applied["tpi_value_override"])
        if applied["tpi_value_override"] is not None
        else float(published)
    )
    return level * (1.0 + float(applied["tpi_scale_pct"]) / 100.0)


def _section_scale(applied: dict, section: str) -> float:
    return applied["base_rate_scale_pct"] + applied["section_rate_scale_pct"].get(section, 0.0)


def _manual_rates_dict(manual_rates) -> dict[int, dict]:
    """Normalise analyst-supplied per-line benchmark rates.

    These exist so that a BoQ can be fully benchmarked: a line the classifier could
    not place, or whose unit did not match the library, would otherwise be carried
    at the tendered rate and contribute no tested variance at all.

    Every supplied rate is an analyst ASSUMPTION, so the line it rescues becomes
    basis="assumed" and is flagged manual_rate.
    """
    out: dict[int, dict] = {}
    if not manual_rates:
        return out
    for key, value in manual_rates.items():
        try:
            item_id = int(key)
        except (TypeError, ValueError):
            continue
        raw = value.model_dump() if hasattr(value, "model_dump") else dict(value)
        try:
            rate = float(raw.get("base_rate") or 0)
        except (TypeError, ValueError):
            continue
        if rate <= 0:
            continue
        out[item_id] = {
            "base_rate": rate,
            "indexed": bool(raw.get("indexed", True)),
            "note": str(raw.get("note") or "").strip(),
        }
    return out


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
def build_benchmark(
    session: Session,
    *,
    upload_id: int,
    filename: str,
    items: list[BoQItem],
    tender_quarter: str,
    tpi_series_name: str,
    variance_threshold: float = 15.0,
    country: str = DEFAULT_COUNTRY,
    currency: str | None = None,
    region_code: str | None = None,
    adjustments=None,
    manual_rates=None,
) -> BenchmarkComputation:
    registry: Country = get_country(country)
    applied = _adjustments_dict(adjustments)
    supplied_rates = _manual_rates_dict(manual_rates)
    applied["manual_rate_count"] = len(supplied_rates)
    region = resolve_region(session, registry.code, region_code)
    tpi = resolve_tpi(session, tpi_series_name, tender_quarter, country=registry.code)
    rates = load_benchmark_rates(session, country=registry.code)
    effective_tpi = _effective_tpi_value(tpi.value, applied)
    ratio = tpi.ratio_for(effective_tpi)

    present_sections = {item.smm2_section for item in items}
    exclusion_hits = excluded_sections(tpi.scope_exclusions, present_sections)

    warns: list[str] = []
    assumes: list[str] = []

    if tpi.fallback_used:
        warns.append(
            f"No {tpi.series_name} TPI observation is published for {tpi.requested_quarter}. "
            f"The nearest prior quarter, {tpi.resolved_quarter}, was used instead."
        )
    if tpi.is_placeholder:
        warns.append(
            f"TPI series {tpi.series_name} {tpi.resolved_quarter} is a SYNTHETIC PLACEHOLDER "
            f"(is_placeholder = true). {tpi.replace_with}"
        )
    for section, phrase in sorted(exclusion_hits.items()):
        warns.append(
            f"Scope exclusion: the {section} section is present in this BoQ but the "
            f"{tpi.series_name} series excludes {phrase!r}. Scope factor 1/{ratio:.4f} was applied "
            f"to {section}, holding those rates at base year {tpi.base_year}. This is an explicit "
            f"modelling assumption, not a measured value."
        )

    # Base-year mismatch: the index ratio is only meaningful when the benchmark
    # rate has been rebased to the same base year as the index.
    mismatched_years = sorted(
        {
            row.base_year
            for row in rates.values()
            if row.base_year != tpi.base_year
        }
    )
    if mismatched_years:
        warns.append(
            f"Base-year mismatch: the {registry.code} benchmark rates are stated at base year "
            f"{', '.join(str(y) for y in mismatched_years)} but the {tpi.series_name} index is "
            f"based on {tpi.base_year}. The index ratio is applied directly, which is only valid "
            f"if the rate library has been rebased to {tpi.base_year}. Verify before relying on "
            f"these figures."
        )

    if region.is_placeholder and region.factor != 1.0:
        warns.append(
            f"Regional adjustment: {region.region_name} ({region.region_code}) carries a "
            f"{region.factor:.3f} multiplier on every benchmark base rate, and that multiplier is "
            f"a PLACEHOLDER estimate, not a published city index. {region.replace_with}"
        )
    assumes.append(
        f"ASSUMED: benchmark base rates are multiplied by {region.factor:.3f} for "
        f"{region.region_name} ({region.region_code}). Source of the factor: {region.source}. "
        f"{region.notes} This is a single blended factor, not a material/labour split: "
        f"labour-heavy sections (Formwork, Plaster, Masonry, Preliminaries) are more regionally "
        f"variable than material-driven ones (Concrete, Reinforcement)."
    )

    if applied["tpi_value_override"] is not None:
        assumes.append(
            f"ASSUMED: the analyst overrode the {tpi.series_name} index value for "
            f"{tpi.resolved_quarter} with {applied['tpi_value_override']:.4f}, replacing the "
            f"stored value of {tpi.value:.4f}. Every affected line is basis='assumed'. "
            f"TODO: keep the published series authoritative; use this override only for "
            f"what-if analysis."
        )
    elif applied["tpi_scale_pct"]:
        assumes.append(
            f"ASSUMED: the analyst applied a {applied['tpi_scale_pct']:+.2f}% shift to the "
            f"{tpi.series_name} index value, moving it from {tpi.value:.4f} to "
            f"{effective_tpi:.4f}. Every affected line is basis='assumed'."
        )
    if applied["base_rate_scale_pct"]:
        assumes.append(
            f"ASSUMED: the analyst applied a {applied['base_rate_scale_pct']:+.2f}% shift to every "
            f"{registry.currency} benchmark base rate. Every benchmarked line is basis='assumed'."
        )
    for section, pct in sorted(applied["section_rate_scale_pct"].items()):
        assumes.append(
            f"ASSUMED: the analyst applied a {pct:+.2f}% shift to the {section} benchmark base "
            f"rate. Lines in this section are basis='assumed'."
        )
    if supplied_rates:
        indexed_count = sum(1 for m in supplied_rates.values() if m["indexed"])
        warns.append(
            f"{len(supplied_rates)} line(s) were benchmarked against an ANALYST-SUPPLIED rate "
            f"rather than a published library rate. Those rates carry no third-party evidence; "
            f"they are assumptions tagged basis='assumed' and flagged manual_rate. "
            f"{indexed_count} of them were indexed from the library base year, "
            f"{len(supplied_rates) - indexed_count} were taken as stated at tender-quarter levels."
        )

    lines: list[LineResult] = []
    for item in items:
        rate_row = rates.get(item.smm2_section)
        flags: list[str] = []
        exclusion_reason: str | None = None

        if item.classified_by == "manual":
            flags.append("reclassified_manually")

        if item.smm2_section == UNCLASSIFIED:
            exclusion_reason = "unclassified_section"
        elif rate_row is None:
            exclusion_reason = "no_benchmark_rate_for_section"
        elif normalise_unit(item.unit) != normalise_unit(rate_row.unit):
            exclusion_reason = "unit_mismatch"

        manual = supplied_rates.get(item.id)

        if exclusion_reason is not None and manual is None:
            if exclusion_reason == "unit_mismatch":
                flags.append("unit_mismatch")
            elif exclusion_reason == "unclassified_section":
                flags.append("unclassified")
            else:
                flags.append("no_benchmark_rate")
            boq_amount = round(item.quantity * item.boq_rate, 2)
            lines.append(
                LineResult(
                    item_id=item.id,
                    raw_description=item.raw_description,
                    unit=item.unit,
                    quantity=round(item.quantity, 4),
                    boq_rate=round(item.boq_rate, 2),
                    boq_amount=boq_amount,
                    smm2_section=item.smm2_section,
                    classified_by=item.classified_by,
                    is_benchmarked=False,
                    exclusion_reason=exclusion_reason,
                    benchmark_base_rate=None,
                    adjusted_benchmark_rate=None,
                    variance_abs=None,
                    variance_pct=None,
                    # No benchmark evidence exists, so should-cost is held at the
                    # tendered rate and contributes zero tested variance.
                    should_cost_amount=boq_amount,
                    variance_amount=0.0,
                    tpi_series_name=tpi.series_name,
                    tpi_quarter_requested=tpi.requested_quarter,
                    tpi_quarter_used=tpi.resolved_quarter,
                    tpi_value=round(effective_tpi, 4),
                    tpi_value_published=round(tpi.value, 4),
                    tpi_base_value=tpi.base_value,
                    tpi_ratio=round(ratio, 6),
                    tpi_fallback_used=tpi.fallback_used,
                    scope_factor=1.0,
                    scope_excluded=False,
                    rate_scale_pct=0.0,
                    regional_factor=region.factor,
                    region_code=region.region_code,
                    from_library=False,
                    user_adjusted=False,
                    basis=BASIS_ASSUMED,
                    flags=flags,
                    provenance=None,
                )
            )
            continue

        section_scale = _section_scale(applied, item.smm2_section)

        if manual is not None:
            # An analyst-supplied rate benchmarks the line regardless of why the
            # library could not, which is the whole point of supplying one.
            supplied_base = manual["base_rate"]
            is_indexed = manual["indexed"]
            flags.append("manual_rate")
            if not is_indexed:
                flags.append("not_indexed")
            scope_excluded = is_indexed and item.smm2_section in exclusion_hits
            scope_factor = (1.0 / ratio) if scope_excluded else 1.0
            if scope_excluded:
                flags.append("scope_excluded")
            effective_region_factor = region.factor if is_indexed else 1.0
            adjusted_base_rate = supplied_base * effective_region_factor * (1.0 + section_scale / 100.0)
            if is_indexed:
                adjusted = adjusted_base_rate * ratio * scope_factor
            else:
                # The analyst has stated the rate at tender-quarter price levels, so
                # indexing it again would double-count movement already inside it.
                adjusted = adjusted_base_rate
        else:
            assert rate_row is not None  # a library rate exists
            is_indexed = True
            effective_region_factor = region.factor
            scope_excluded = item.smm2_section in exclusion_hits
            scope_factor = (1.0 / ratio) if scope_excluded else 1.0
            if scope_excluded:
                flags.append("scope_excluded")
            adjusted_base_rate = rate_row.base_rate * region.factor * (1.0 + section_scale / 100.0)
            adjusted = adjusted_base_rate * ratio * scope_factor
        variance_abs_raw = item.boq_rate - adjusted
        variance_pct_raw = (variance_abs_raw / adjusted * 100.0) if adjusted else None

        if variance_pct_raw is not None:
            if variance_pct_raw >= variance_threshold:
                flags.append("over_threshold")
            elif variance_pct_raw <= -variance_threshold:
                flags.append("under_threshold")
            else:
                flags.append("within_threshold")

        if manual is None and rate_row is not None and rate_row.is_placeholder:
            flags.append("placeholder_benchmark_rate")

        # A regional multiplier is a modelling input, not an observation, so any
        # line it touches is basis="assumed" - exactly like a manual adjuster.
        user_adjusted = (
            manual is not None
            or bool(section_scale)
            or applied["tpi_value_override"] is not None
            or bool(applied["tpi_scale_pct"])
            or (is_indexed and region.factor != 1.0)
        )
        if user_adjusted:
            flags.append("user_adjusted")

        quantity = item.quantity
        boq_amount = round(quantity * item.boq_rate, 2)
        should_cost_amount = round(quantity * adjusted, 2)
        lines.append(
            LineResult(
                item_id=item.id,
                raw_description=item.raw_description,
                unit=item.unit,
                quantity=round(quantity, 4),
                boq_rate=round(item.boq_rate, 2),
                boq_amount=boq_amount,
                smm2_section=item.smm2_section,
                classified_by=item.classified_by,
                is_benchmarked=True,
                exclusion_reason=None,
                benchmark_base_rate=round(adjusted_base_rate, 2),
                adjusted_benchmark_rate=round(adjusted, 2),
                variance_abs=round(variance_abs_raw, 2),
                variance_pct=round(variance_pct_raw, 2) if variance_pct_raw is not None else None,
                should_cost_amount=should_cost_amount,
                variance_amount=round(boq_amount - should_cost_amount, 2),
                tpi_series_name=tpi.series_name,
                tpi_quarter_requested=tpi.requested_quarter,
                tpi_quarter_used=tpi.resolved_quarter,
                tpi_value=round(effective_tpi, 4),
                tpi_value_published=round(tpi.value, 4),
                tpi_base_value=tpi.base_value,
                tpi_ratio=round(ratio, 6),
                tpi_fallback_used=tpi.fallback_used,
                # Reported at full precision: rounding here breaks the exact
                # market-risk/scope cancellation for excluded sections.
                scope_factor=scope_factor,
                scope_excluded=scope_excluded,
                rate_scale_pct=round(section_scale, 4),
                regional_factor=effective_region_factor,
                region_code=region.region_code,
                # Whether the rate actually came from the published library.
                from_library=manual is None,
                user_adjusted=user_adjusted,
                basis=BASIS_ASSUMED if user_adjusted else BASIS_DERIVED,
                flags=flags,
                provenance=(
                    {
                        "source": "Analyst-supplied manual rate",
                        "source_date": datetime.now(timezone.utc).date().isoformat(),
                        "base_year": (tpi.base_year if is_indexed else int(tpi.resolved_quarter[:4])),
                        "scope_inclusions": "As stated by the analyst for this individual line",
                        "scope_exclusions": "" if is_indexed else "Index not applied - rate stated at tender-quarter levels",
                        "confidence": "analyst",
                        "is_placeholder": False,
                        "source_url": None,
                        "replace_with": manual["note"] or (
                            "Analyst-supplied rate. Replace with a published library rate when one exists "
                            "for this scope."
                        ),
                    }
                    if manual is not None
                    else {
                        "source": rate_row.source,
                        "source_date": rate_row.source_date,
                        "base_year": rate_row.base_year,
                        "scope_inclusions": rate_row.scope_inclusions,
                        "scope_exclusions": rate_row.scope_exclusions,
                        "confidence": rate_row.confidence,
                        "is_placeholder": rate_row.is_placeholder,
                        "source_url": rate_row.source_url,
                        "replace_with": rate_row.replace_with,
                    }
                ),
            )
        )

    # ---------------------------------------------------------------- totals --
    boq_total = round(sum(l.boq_amount for l in lines), 2)
    should_cost_total = round(sum(l.should_cost_amount for l in lines), 2)
    benchmarked_boq_total = round(sum(l.boq_amount for l in lines if l.is_benchmarked), 2)
    unbenchmarked_lines = [l for l in lines if not l.is_benchmarked]
    unbenchmarked_boq_total = round(sum(l.boq_amount for l in unbenchmarked_lines), 2)
    total_variance_abs = round(boq_total - should_cost_total, 2)
    total_variance_pct = (
        round(total_variance_abs / should_cost_total * 100.0, 2) if should_cost_total else None
    )
    breached = [l for l in lines if "over_threshold" in l.flags or "under_threshold" in l.flags]

    # -------------------------------------------------------------- sections --
    any_user_adjusted = any(l.user_adjusted for l in lines)
    section_names = sorted({l.smm2_section for l in lines}, key=_section_sort_key)
    sections: list[dict] = []
    for name in section_names:
        members = [l for l in lines if l.smm2_section == name]
        sec_boq = round(sum(m.boq_amount for m in members), 2)
        sec_should = round(sum(m.should_cost_amount for m in members), 2)
        sec_var = round(sec_boq - sec_should, 2)
        sec_pct = round(sec_var / sec_should * 100.0, 2) if sec_should else None
        benchmarked = [m for m in members if m.is_benchmarked]
        sections.append(
            {
                "smm2_section": name,
                "item_count": len(members),
                "benchmarked_item_count": len(benchmarked),
                "boq_amount": sec_boq,
                "should_cost_amount": sec_should,
                "variance_amount": sec_var,
                "variance_abs": sec_var,
                "variance_pct": sec_pct,
                "basis": BASIS_DERIVED if (benchmarked and not any_user_adjusted) else BASIS_ASSUMED,
                "breaches_threshold": any(
                    "over_threshold" in m.flags or "under_threshold" in m.flags for m in members
                ),
            }
        )

    # ------------------------------------------------------------- waterfall --
    waterfall = _build_waterfall(lines, boq_total, should_cost_total)

    # ------------------------------------------------------ warnings/assumes --
    unclassified = [l for l in lines if l.smm2_section == UNCLASSIFIED]
    if unclassified:
        warns.append(
            f"{len(unclassified)} line(s) could not be classified to a section and were not "
            f"benchmarked. Reclassify them via PATCH /api/boq/item/{{item_id}} and re-run."
        )
    unit_mismatches = [l for l in lines if l.exclusion_reason == "unit_mismatch"]
    if unit_mismatches:
        warns.append(
            f"{len(unit_mismatches)} line(s) were excluded from variance testing because the BoQ "
            f"unit does not match the benchmark rate unit. Comparing unlike units would be "
            f"meaningless, so these lines are held at the tendered rate."
        )
    no_rate = [l for l in lines if l.exclusion_reason == "no_benchmark_rate_for_section"]
    if no_rate:
        warns.append(
            f"{len(no_rate)} line(s) have no benchmark rate for their section and were not "
            f"benchmarked."
        )
    if unbenchmarked_lines:
        assumes.append(
            f"ASSUMED: {len(unbenchmarked_lines)} line(s) totalling {registry.currency} "
            f"{unbenchmarked_boq_total:,.2f} could not be benchmarked. Their should-cost is held "
            f"equal to the tendered BoQ rate, so they contribute zero tested variance. Their cost "
            f"is carried but NOT validated."
        )
    if any(l.is_benchmarked for l in lines) and abs(waterfall[2]["amount"]) + abs(waterfall[3]["amount"]) > 0:
        assumes.append(
            f"ASSUMED: the rate gap between the tendered BoQ rate and the base-year benchmark "
            f"rate was apportioned {int(MATERIAL_SHARE * 100)}% material / "
            f"{int(LABOUR_SHARE * 100)}% labour. No measured rate build-up is available, so the "
            f"material and labour waterfall bars are basis='assumed', not measured. "
            f"TODO: replace with measured material/labour/plant splits."
        )
    if exclusion_hits:
        assumes.append(
            "ASSUMED: for sections excluded by the selected TPI series ("
            + ", ".join(sorted(exclusion_hits))
            + "), scope_factor was set to 1/TPI ratio, holding those rates at base year rather "
            "than indexing them with an index that does not measure that scope."
        )
    if tpi.is_placeholder or any(
        l.provenance and l.provenance.get("is_placeholder") for l in lines
    ):
        assumes.append(
            f"ASSUMED: benchmark rates and index values in this run are SYNTHETIC PLACEHOLDERS "
            f"for {registry.name}. Every affected row carries is_placeholder = true and a TODO "
            f"naming the source it must be replaced from. Do not use these figures for a real "
            f"tender decision."
        )

    return BenchmarkComputation(
        upload_id=upload_id,
        country=registry.code,
        country_name=registry.name,
        currency=(currency or registry.currency),
        classification_standard=registry.measurement_standard,
        measurement_standard=registry.measurement_standard,
        filename=filename,
        tender_quarter=tender_quarter,
        tpi_series_name=tpi.series_name,
        variance_threshold=variance_threshold,
        tpi_series_scope_inclusions=tpi.scope_inclusions,
        tpi_series_scope_exclusions=tpi.scope_exclusions,
        region_code=region.region_code,
        region_name=region.region_name,
        regional_factor=region.factor,
        regional_factor_is_placeholder=region.is_placeholder,
        regional_factor_source=region.source,
        adjustments_applied=applied,
        lines=lines,
        sections=sections,
        totals={
            "boq_total": boq_total,
            "should_cost_total": should_cost_total,
            "total_variance_abs": total_variance_abs,
            "total_variance_pct": total_variance_pct,
            "benchmarked_boq_total": benchmarked_boq_total,
            "unbenchmarked_boq_total": unbenchmarked_boq_total,
            "unbenchmarked_line_count": len(unbenchmarked_lines),
            "line_count": len(lines),
            "breached_line_count": len(breached),
            "basis": BASIS_ASSUMED if applied["is_noop"] is False else BASIS_DERIVED,
        },
        waterfall=waterfall,
        warnings=warns,
        assumptions=assumes,
    )


_SECTION_ORDER = [
    "Preliminaries", "Excavation", "Piling", "Concrete", "Reinforcement", "Formwork",
    "Masonry", "Waterproofing", "Plaster", "M&E Containment", UNCLASSIFIED,
]


def _section_sort_key(name: str) -> tuple[int, str]:
    try:
        return (_SECTION_ORDER.index(name), name)
    except ValueError:
        return (len(_SECTION_ORDER), name)


def _build_waterfall(lines: list[LineResult], boq_total: float, should_cost_total: float) -> list[dict]:
    """Reconcile boq_total to should_cost_total.

    Identity: boq_total + material + labour + market_risk + scope + unexplained
              == should_cost_total
    """
    matched = [l for l in lines if l.is_benchmarked]

    market_risk = round(
        sum(l.quantity * l.benchmark_base_rate * (l.tpi_ratio - 1.0) for l in matched), 2
    )
    scope = round(
        sum(
            l.quantity * l.benchmark_base_rate * l.tpi_ratio * (l.scope_factor - 1.0)
            for l in matched
        ),
        2,
    )
    rate_gap = sum(l.quantity * (l.benchmark_base_rate - l.boq_rate) for l in matched)
    material = round(rate_gap * MATERIAL_SHARE, 2)
    labour = round(rate_gap * LABOUR_SHARE, 2)
    unexplained = round(should_cost_total - boq_total - material - labour - market_risk - scope, 2)

    return [
        {
            "component": "material",
            "amount": material,
            "basis": BASIS_ASSUMED,
            "method": f"rate_gap x {MATERIAL_SHARE}",
            "justification": (
                "Apportioned share of the gap between the tendered BoQ rate and the base-year "
                "benchmark rate. Assumed ratio - no measured material content is available."
            ),
        },
        {
            "component": "labour",
            "amount": labour,
            "basis": BASIS_ASSUMED,
            "method": f"rate_gap x {LABOUR_SHARE}",
            "justification": (
                "Apportioned share of the gap between the tendered BoQ rate and the base-year "
                "benchmark rate. Assumed ratio - no measured labour content is available."
            ),
        },
        {
            "component": "market_risk",
            "amount": market_risk,
            "basis": BASIS_DERIVED,
            "method": "sum(quantity x base_rate x (tpi_ratio - 1))",
            "justification": (
                "Price movement between the benchmark base year and the tender quarter, derived "
                "from the selected index series. Indexed sections only."
            ),
        },
        {
            "component": "scope",
            "amount": scope,
            "basis": BASIS_DERIVED,
            "method": "sum(quantity x base_rate x tpi_ratio x (scope_factor - 1))",
            "justification": (
                "Effect of scope-excluded sections being held at base year instead of being "
                "indexed by a series that does not measure them. Zero unless a scope exclusion "
                "was detected."
            ),
        },
        {
            "component": "unexplained",
            "amount": unexplained,
            "basis": BASIS_DERIVED,
            "method": "should_cost_total - boq_total - (material + labour + market_risk + scope)",
            "justification": (
                "Residual that closes the identity exactly so the chart reconciles to the cent. "
                "Expected to be ~0.00 when every line is benchmarked; a material residual means "
                "some lines could not be benchmarked."
            ),
        },
    ]


# --------------------------------------------------------------------------- #
# Sensitivity analysis
# --------------------------------------------------------------------------- #
def compute_sensitivity(
    session: Session,
    *,
    upload_id: int,
    items: list[BoQItem],
    tender_quarter: str,
    tpi_series_name: str,
    variance_threshold: float = 15.0,
    tpi_scale_min_pct: float = -20.0,
    tpi_scale_max_pct: float = 20.0,
    tpi_scale_step_pct: float = 5.0,
    section_scale_pct: float = 10.0,
    country: str = DEFAULT_COUNTRY,
    region_code: str | None = None,
    adjustments=None,
    manual_rates=None,
) -> dict:
    """Sweep the index value and each section's rate, and report the effect.

    Everything here is derived from the same engine as the headline benchmark, so
    the baseline point is guaranteed to equal build_benchmark() to the cent.
    """
    registry = get_country(country)
    applied = _adjustments_dict(adjustments)
    # Resolve the published observation once; every scenario is derived from it.
    resolved = resolve_tpi(session, tpi_series_name, tender_quarter, country=registry.code)
    published_tpi = resolved.value

    if tpi_scale_max_pct < tpi_scale_min_pct:
        raise ValueError(
            f"tpi_scale_max_pct ({tpi_scale_max_pct}) must be >= tpi_scale_min_pct "
            f"({tpi_scale_min_pct})."
        )
    span = tpi_scale_max_pct - tpi_scale_min_pct
    steps = int(round(span / tpi_scale_step_pct)) + 1
    if steps > 81:
        raise ValueError(
            f"That sweep would produce {steps} points; the limit is 81. Increase "
            f"tpi_scale_step_pct or narrow the range."
        )

    base = build_benchmark(
        session,
        upload_id=upload_id,
        filename="",
        items=items,
        tender_quarter=tender_quarter,
        tpi_series_name=tpi_series_name,
        variance_threshold=variance_threshold,
        country=registry.code,
        region_code=region_code,
        adjustments=adjustments,
        manual_rates=manual_rates,
    )

    def run_with(tpi_scale: float, section: str | None, delta_pct: float) -> BenchmarkComputation:
        section_map = dict(applied["section_rate_scale_pct"])
        if section is not None:
            section_map[section] = section_map.get(section, 0.0) + delta_pct
        merged = {
            "tpi_value_override": applied["tpi_value_override"],
            "tpi_scale_pct": applied["tpi_scale_pct"] + tpi_scale,
            "base_rate_scale_pct": applied["base_rate_scale_pct"],
            "section_rate_scale_pct": section_map,
        }
        return build_benchmark(
            session,
            upload_id=upload_id,
            filename="",
            items=items,
            tender_quarter=tender_quarter,
            tpi_series_name=tpi_series_name,
            variance_threshold=variance_threshold,
            country=registry.code,
            region_code=region_code,
            adjustments=merged,
            manual_rates=manual_rates,
        )

    def scenario_tpi_value(scale_pct: float) -> float:
        return _effective_tpi_value(
            published_tpi,
            {**applied, "tpi_scale_pct": applied["tpi_scale_pct"] + scale_pct},
        )

    sweep: list[dict] = []
    for index in range(steps):
        scale = tpi_scale_min_pct + index * tpi_scale_step_pct
        if scale > tpi_scale_max_pct + 1e-9:
            break
        computation = run_with(scale, None, 0.0)
        totals = computation.totals
        over = sum(1 for l in computation.lines if "over_threshold" in l.flags)
        under = sum(1 for l in computation.lines if "under_threshold" in l.flags)
        sweep.append(
            {
                "tpi_scale_pct": round(scale, 4),
                "tpi_value": round(scenario_tpi_value(scale), 4),
                "should_cost_total": totals["should_cost_total"],
                "total_variance_abs": totals["total_variance_abs"],
                "total_variance_pct": totals["total_variance_pct"],
                "breached_line_count": totals["breached_line_count"],
                "lines_over": over,
                "lines_under": under,
                "is_baseline": abs(scale) < 1e-9,
            }
        )

    # Break-even: the index shift that makes should-cost equal the BoQ total.
    # should_cost is linear in (1 + scale) because every benchmarked line is
    # proportional to the index ratio.
    baseline_totals = base.totals
    break_even_scale_pct: float | None = None
    break_even_tpi_value: float | None = None
    probe = run_with(1.0, None, 0.0)  # +1 percentage point
    slope = probe.totals["should_cost_total"] - baseline_totals["should_cost_total"]
    if abs(slope) > 1e-9:
        # 6dp: on a multi-million BoQ a 4dp break-even lands ~0.5 currency units
        # away from zero, which is outside the reconciliation tolerance.
        break_even_scale_pct = round(
            (baseline_totals["boq_total"] - baseline_totals["should_cost_total"]) / slope, 6
        )
        break_even_tpi_value = round(scenario_tpi_value(break_even_scale_pct), 4)

    # Tornado: move one section at a time by +/- section_scale_pct.
    tornado: list[dict] = []
    should_total = baseline_totals["should_cost_total"] or 1.0
    for section in sorted({l.smm2_section for l in base.lines}, key=_section_sort_key):
        members = [l for l in base.lines if l.smm2_section == section]
        if not any(m.is_benchmarked for m in members):
            continue
        low = run_with(0.0, section, -section_scale_pct).totals["should_cost_total"]
        high = run_with(0.0, section, section_scale_pct).totals["should_cost_total"]
        delta_low = round(low - baseline_totals["should_cost_total"], 2)
        delta_high = round(high - baseline_totals["should_cost_total"], 2)
        section_should = round(sum(m.should_cost_amount for m in members), 2)
        tornado.append(
            {
                "smm2_section": section,
                "boq_amount": round(sum(m.boq_amount for m in members), 2),
                "should_cost_amount": section_should,
                "share_of_should_cost_pct": round(section_should / should_total * 100.0, 2),
                "delta_low": delta_low,
                "delta_high": delta_high,
                "swing": round(delta_high - delta_low, 2),
            }
        )
    tornado.sort(key=lambda row: row["swing"], reverse=True)

    assumptions = list(base.assumptions)
    assumptions.append(
        f"ASSUMED: the sensitivity sweep varies the index by up to "
        f"{max(abs(tpi_scale_min_pct), abs(tpi_scale_max_pct)):.1f}% around the published value "
        f"and each section's rate by +/-{section_scale_pct:.1f}% in isolation. These are scenario "
        f"inputs chosen to bracket plausible outcomes, not forecasts."
    )
    assumptions.append(
        "ASSUMED: the break-even point is the index shift at which should-cost equals the "
        "tendered BoQ total. It assumes every other input is unchanged and that all benchmarked "
        "lines stay benchmarked."
    )

    return {
        "upload_id": upload_id,
        "country": registry.code,
        "country_name": registry.name,
        "currency": registry.currency,
        "region_code": base.region_code,
        "region_name": base.region_name,
        "regional_factor": base.regional_factor,
        "tender_quarter": tender_quarter,
        "tpi_series_name": base.tpi_series_name,
        "variance_threshold": variance_threshold,
        "section_scale_pct": section_scale_pct,
        "baseline_tpi_value": round(scenario_tpi_value(0.0), 4),
        "baseline": base.totals,
        "break_even_scale_pct": break_even_scale_pct,
        "break_even_tpi_value": break_even_tpi_value,
        "tpi_sweep": sweep,
        "section_tornado": tornado,
        "most_sensitive_section": tornado[0]["smm2_section"] if tornado else None,
        "assumptions": assumptions,
        "warnings": list(base.warnings),
    }
