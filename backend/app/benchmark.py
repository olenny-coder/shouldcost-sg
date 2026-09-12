"""Should-cost benchmark engine.

Formula contract (do not change without updating README and the tests):

    adjusted_benchmark_rate = base_rate * (current_tpi / base_tpi) * scope_factor
    variance_abs            = boq_rate - adjusted_benchmark_rate
    variance_pct            = (variance_abs / adjusted_benchmark_rate) * 100
    should_cost_amount      = quantity * adjusted_benchmark_rate

Three multipliers are layered on top of the published values. The first is the
CPI bridge, the other two are ANALYST ASSUMPTIONS rather than observations:

    current_tpi  = (published_tpi * cpi_bridge_factor) * (1 + tpi_scale_pct/100)
    base_rate    = published_base_rate * (1 + (base_rate_scale_pct + section_scale_pct)/100)

The CPI BRIDGE. Published construction cost indexes lag the tender quarter: the
BCA series is a quarterly release, and the WPI appears about two months after the
month it describes. When the requested quarter is later than the last observation
of the selected series, the last observation is carried forward by the observed
change in the country's monthly CONSUMER PRICE INDEX between the two quarters:

    index_value(requested) = index_value(last_observed_quarter)
                             * cpi(covered_through) / cpi(last_observed_quarter)

Both CPI endpoints are the mean of the months available in that quarter. This is
a MODELLED step: consumer prices are not construction costs. So every bridged line
is basis="assumed" and flagged index_bridged, the bridge appears as its own step in
the waterfall, and it is restated in assumptions[]. Set index_bridge="none" on the
request to switch it off, in which case the last observation is simply held and a
warning says so.

An absolute index override replaces the BRIDGED level, so an override always wins
over the bridge.

scope_factor is 1.0 by default. It only moves away from 1.0 when the selected TPI
series explicitly EXCLUDES a section that is present in the BoQ (the canonical
case being a BoQ containing Piling while the RLB or CPWD series excludes piling).
In that case the section is not re-priced by an index that does not measure it, so
scope_factor = 1 / tpi_ratio and the rate is held at base year.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import classifier
from .countries import DEFAULT_COUNTRY, Country, get_country
from .models import BenchmarkRate, BoQItem, PriceSeries, RegionalFactor, TPISeries

BASIS_MEASURED = "measured"
BASIS_DERIVED = "derived"
BASIS_ASSUMED = "assumed"

UNCLASSIFIED = classifier.UNCLASSIFIED

# Fallback index value at the base year when a series row does not carry one.
# A rebased index is 100 at its base year by construction, but the value is
# stored per row so a series published on another base can be carried explicitly.
DEFAULT_BASE_YEAR_INDEX_VALUE = 100.0

# The quarter the bundled rate library is expressed at. The demonstration bills default
# to pricing here, so the sample shows the library as built and the index ratio is 1.0
# for every SOR-derived section. Every other quarter is still selectable: the index then
# carries the rates forward from this quarter, and the movement is disclosed.
DEFAULT_TENDER_QUARTER = "2026Q2"

# Assumed split of the rate gap between the tendered BoQ rate and the base-year
# benchmark rate. This is an APPORTIONMENT, not a measurement, and the waterfall
# reports both bars as basis=assumed because of it.
MATERIAL_SHARE = 0.55
LABOUR_SHARE = 0.45

CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}

# How a stale index observation is brought up to the tender quarter.
#   "cpi"  - carry it forward by the observed CPI movement (default)
#   "none" - hold the last observation and say so
BRIDGE_PPI = "ppi"
BRIDGE_CPI = "cpi"
BRIDGE_AUTO = "auto"
BRIDGE_NONE = "none"


def _normalise_bridge_mode(mode: str | None) -> str:
    """Validate the requested bridge mode, defaulting to producer-then-consumer."""
    value = (mode or BRIDGE_AUTO).strip().lower()
    if value not in {BRIDGE_AUTO, BRIDGE_PPI, BRIDGE_CPI, BRIDGE_NONE}:
        raise ValueError(
            f"Unknown index_bridge mode {mode!r}. Use one of: "
            f"{BRIDGE_AUTO}, {BRIDGE_PPI}, {BRIDGE_CPI}, {BRIDGE_NONE}."
        )
    return value
BRIDGE_MODES = (BRIDGE_CPI, BRIDGE_NONE)

_QUARTER_RE = re.compile(r"^(\d{4})\s*[Qq]([1-4])$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")

# Exclusion phrases used by the published series, mapped to the canonical
# sections they remove from scope. An empty set means the exclusion has no
# counterpart in the benchmark rate library, so nothing fires.
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


def parse_month(month: str) -> tuple[int, int]:
    match = _MONTH_RE.match((month or "").strip())
    if not match:
        raise ValueError(f"Invalid month {month!r}. Expected the form YYYY-MM, for example 2026-07.")
    return int(match.group(1)), int(match.group(2))


def month_sort_key(month: str) -> int:
    year, number = parse_month(month)
    return year * 12 + number


def quarter_months(quarter: str) -> list[str]:
    """The three YYYY-MM labels that make up a quarter."""
    year, q = parse_quarter(quarter)
    first = (q - 1) * 3 + 1
    return [f"{year:04d}-{number:02d}" for number in range(first, first + 3)]


def quarter_of_month(month: str) -> str:
    year, number = parse_month(month)
    return format_quarter(year, (number - 1) // 3 + 1)


def months_between(start_month: str, end_month: str) -> int:
    """Whole months from start to end. Negative when end precedes start."""
    return month_sort_key(end_month) - month_sort_key(start_month)


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
class CPIBridge:
    """The modelled step that carries a stale index observation to the tender quarter.

    `applied` is False whenever the observation already covers the requested
    quarter, when the analyst switched the bridge off, or when no usable CPI
    series is loaded - in each case `reason` says which.
    """

    applied: bool
    reason: str
    country: str
    series_name: str
    requested_quarter: str
    observation_quarter: str
    lag_quarters: int
    requested_mode: str = BRIDGE_AUTO
    from_value: float | None = None
    from_months: list[str] = field(default_factory=list)
    to_value: float | None = None
    to_months: list[str] = field(default_factory=list)
    covered_through_month: str | None = None
    shortfall_months: int = 0
    factor: float = 1.0
    # PPI or CPI - which price index carried the observation forward.
    kind: str = "CPI"
    base_year: int | None = None
    base_value: float | None = None
    currency: str = ""
    source_url: str = ""
    is_placeholder: bool = False
    provenance_note: str = ""
    replace_with: str = ""
    published_index_value: float | None = None
    bridged_index_value: float | None = None
    # Coverage of the series actually used (first/last published month), and, when
    # no series could span both endpoints, what each candidate covered.
    first_month: str | None = None
    last_month: str | None = None
    alternatives: list[dict] = field(default_factory=list)

    @property
    def to_quarter(self) -> str:
        """The quarter the bridge actually reached (its last month's quarter)."""
        if not self.covered_through_month:
            return self.observation_quarter
        return quarter_of_month(self.covered_through_month)

    @property
    def partial(self) -> bool:
        """True when only some months of the target quarter were available."""
        if not self.to_months:
            return False
        return len(self.to_months) < 3

    @property
    def short(self) -> bool:
        """True when the bridge could not reach the end of the requested quarter."""
        return self.shortfall_months > 0

    def as_dict(self) -> dict:
        return {
            "applied": self.applied,
            "reason": self.reason,
            "mode": self.requested_mode,
            "kind": self.kind,
            "series_name": self.series_name,
            "requested_quarter": self.requested_quarter,
            "observation_quarter": self.observation_quarter,
            "lag_quarters": self.lag_quarters,
            "bridged_to_quarter": self.to_quarter if self.applied else None,
            "bridged_through_month": self.covered_through_month,
            "shortfall_months": self.shortfall_months,
            "partial_quarter": self.partial if self.applied else False,
            "cpi_series_name": self.series_name,
            "cpi_base_year": self.base_year,
            "cpi_base_value": self.base_value,
            "cpi_from_value": round(self.from_value, 4) if self.from_value is not None else None,
            "cpi_from_months": list(self.from_months),
            "cpi_to_value": round(self.to_value, 4) if self.to_value is not None else None,
            "cpi_to_months": list(self.to_months),
            "cpi_bridge_factor": round(self.factor, 6),
            "cpi_source_url": self.source_url,
            "cpi_is_placeholder": self.is_placeholder,
            "cpi_provenance_note": self.provenance_note,
            "cpi_first_month": self.first_month,
            "cpi_last_month": self.last_month,
            "cpi_series_considered": list(self.alternatives),
            "index_value_published": (
                round(self.published_index_value, 4)
                if self.published_index_value is not None
                else None
            ),
            "index_value_bridged": (
                round(self.bridged_index_value, 4)
                if self.bridged_index_value is not None
                else None
            ),
            "index_value_used": (
                round(self.bridged_index_value, 4)
                if self.applied and self.bridged_index_value is not None
                else (
                    round(self.published_index_value, 4)
                    if self.published_index_value is not None
                    else None
                )
            ),
        }


def _price_rows(session: Session, country: str, series_name: str = "") -> list[PriceSeries]:
    statement = select(PriceSeries).where(PriceSeries.country == country)
    if series_name:
        statement = statement.where(PriceSeries.series_name == series_name.strip().upper())
    return list(session.scalars(statement.order_by(PriceSeries.series_name, PriceSeries.month)))


def _bridge_with_series(
    rows: list[PriceSeries],
    *,
    requested_quarter: str,
    observation_quarter: str,
) -> CPIBridge:
    """Attempt the bridge against ONE consumer price series.

    Returns the bridge with `applied=False` and a reason when that particular series
    cannot span both endpoints.
    """
    series_name = rows[0].series_name
    bridge = CPIBridge(
        kind=getattr(rows[0], "kind", "CPI"),
        applied=False,
        reason="",
        country=rows[0].country,
        series_name=series_name,
        requested_quarter=requested_quarter,
        observation_quarter=observation_quarter,
        lag_quarters=quarter_sort_key(requested_quarter) - quarter_sort_key(observation_quarter),
        base_year=rows[-1].base_year,
        base_value=rows[-1].base_value,
        currency=rows[-1].currency,
        source_url=rows[-1].source_url,
        is_placeholder=any(row.is_placeholder for row in rows),
        provenance_note=rows[-1].provenance_note,
        replace_with=rows[-1].replace_with or "",
        first_month=rows[0].month,
        last_month=rows[-1].month,
    )

    by_month = {row.month: row.value for row in rows}
    available = sorted(by_month)

    from_months = [month for month in quarter_months(observation_quarter) if month in by_month]
    if not from_months:
        bridge.reason = "no_price_observation_for_the_observation_quarter"
        return bridge

    target_months = [month for month in quarter_months(requested_quarter) if month in by_month]
    if not target_months:
        # The requested quarter is not covered yet. Bridge as far as the data
        # allows - to the latest published month after the observation - rather
        # than silently pretending the index is current.
        quarter_end = quarter_months(requested_quarter)[-1]
        candidates = [
            month
            for month in available
            if month_sort_key(month) > month_sort_key(from_months[-1])
            and month_sort_key(month) <= month_sort_key(quarter_end)
        ]
        if not candidates:
            bridge.reason = "no_price_observation_after_the_index_observation"
            return bridge
        target_months = [candidates[-1]]

    from_value = sum(by_month[month] for month in from_months) / len(from_months)
    to_value = sum(by_month[month] for month in target_months) / len(target_months)
    if not from_value:
        bridge.reason = "price_value_at_the_observation_quarter_is_zero"
        return bridge

    bridge.applied = True
    bridge.reason = "bridged_with_price_index"
    bridge.from_months = from_months
    bridge.to_months = target_months
    bridge.from_value = from_value
    bridge.to_value = to_value
    bridge.covered_through_month = target_months[-1]
    bridge.shortfall_months = max(
        0, months_between(target_months[-1], quarter_months(requested_quarter)[-1])
    )
    bridge.factor = to_value / from_value
    return bridge


def resolve_index_bridge(
    session: Session,
    *,
    country: str,
    observation_quarter: str,
    requested_quarter: str,
    mode: str = BRIDGE_AUTO,
    published_index_value: float | None = None,
) -> CPIBridge:
    """Carry a stale index observation forward using the observed CPI movement.

    A market may have more than one consumer price series loaded - India carries the
    publisher's current 2024-based series and its predecessor, which do not overlap.
    The series the registry declares is tried first; any other series loaded for the
    country is tried next, and the first one that spans BOTH endpoints wins. Series
    are never chained or spliced across a base change: if no single series covers the
    two quarters, the bridge is reported as unavailable with that exact reason.

    Returns an unapplied bridge (with a machine-readable `reason`) whenever the
    bridge cannot or should not be computed. It never raises: a missing CPI series
    must degrade to "hold the last observation and warn", not break the run.
    """
    code = (country or DEFAULT_COUNTRY).strip().upper()
    registry: Country = get_country(code)
    lag = quarter_sort_key(requested_quarter) - quarter_sort_key(observation_quarter)
    preferred = (registry.default_cpi_series or "").upper()

    bridge = CPIBridge(
        applied=False,
        reason="",
        country=code,
        series_name=preferred,
        requested_quarter=requested_quarter,
        observation_quarter=observation_quarter,
        lag_quarters=lag,
        requested_mode=_normalise_bridge_mode(mode),
        published_index_value=published_index_value,
    )

    if lag <= 0:
        bridge.reason = "index_observation_covers_requested_quarter"
        return bridge
    if _normalise_bridge_mode(mode) == BRIDGE_NONE:
        bridge.reason = "bridge_disabled_by_analyst"
        return bridge

    rows = _price_rows(session, code)
    if not rows:
        bridge.reason = "no_price_observations_loaded"
        return bridge

    by_series: dict[str, list[PriceSeries]] = {}
    for row in rows:
        by_series.setdefault(row.series_name, []).append(row)

    # PRODUCER INDICES FIRST. A PPI measures what manufacturers and utilities
    # charge for cement, steel, non-metallic minerals and power - the inputs a
    # construction rate is actually made of. A CPI measures what households pay,
    # a weaker proxy, so it is only reached when no producer series spans both
    # endpoints. See PriceSeries.
    producer = sorted({n for n, rs in by_series.items() if rs[0].kind == "PPI"})
    consumer = sorted({n for n, rs in by_series.items() if rs[0].kind != "PPI"})

    requested = _normalise_bridge_mode(mode)
    if requested == BRIDGE_PPI:
        consumer = []
    elif requested == BRIDGE_CPI:
        producer = []

    preferred_ppi = (getattr(registry, "default_ppi_series", "") or "").upper()
    preferred_cpi = (preferred or "").upper()

    def _ordered(names: list[str], first: str) -> list[str]:
        return ([first] if first in names else []) + [n for n in names if n != first]

    order = _ordered(producer, preferred_ppi) + _ordered(consumer, preferred_cpi)

    attempts: list[dict] = []
    for name in order:
        candidate = _bridge_with_series(
            by_series[name],
            requested_quarter=requested_quarter,
            observation_quarter=observation_quarter,
        )
        candidate.requested_mode = bridge.requested_mode
        candidate.published_index_value = published_index_value
        if candidate.applied:
            candidate.alternatives = attempts
            return candidate
        attempts.append(
            {
                "series_name": name,
                "base_year": by_series[name][-1].base_year,
                "first_month": by_series[name][0].month,
                "last_month": by_series[name][-1].month,
                "reason": candidate.reason,
            }
        )

    bridge.reason = (
        "no_price_series_covers_both_quarters"
        if len(attempts) > 1
        else attempts[0]["reason"]
    )
    bridge.alternatives = attempts
    return bridge


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
    # The observation as published, before any CPI bridge or analyst adjustment.
    value_published: float = 0.0
    bridge: CPIBridge | None = None

    @property
    def bridge_applied(self) -> bool:
        return bool(self.bridge and self.bridge.applied)

    @property
    def bridge_factor(self) -> float:
        return self.bridge.factor if self.bridge_applied else 1.0

    @property
    def lag_quarters(self) -> int:
        return quarter_sort_key(self.requested_quarter) - quarter_sort_key(self.resolved_quarter)

    def ratio_for(self, current_value: float) -> float:
        if not self.base_value:
            raise TPILookupError("TPI base value is zero; cannot compute a ratio.")
        return current_value / self.base_value

    @property
    def ratio(self) -> float:
        return self.ratio_for(self.value)

    @property
    def published_ratio(self) -> float:
        """Ratio from the published observation alone, with no bridge applied."""
        return self.ratio_for(self.value_published or self.value)


def resolve_tpi(
    session: Session,
    series_name: str,
    tender_quarter: str,
    country: str = DEFAULT_COUNTRY,
    *,
    bridge: str = BRIDGE_AUTO,
) -> ResolvedTPI:
    """Resolve a TPI observation for one country.

    Fallback order: exact series + exact quarter, then exact series + nearest
    PRIOR quarter, carried forward to the requested quarter by the observed
    movement in a PRODUCER price index, falling back to a CONSUMER price index
    only when no producer series spans both quarters (see resolve_index_bridge).
    Pass bridge="none" to hold the last observation instead. Anything else raises
    TPILookupError with an explicit message.
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
    row = by_key[prior[0]]
    cpi_bridge = resolve_index_bridge(
        session,
        country=code,
        observation_quarter=row.quarter,
        requested_quarter=tender_quarter,
        mode=bridge,
        published_index_value=row.value,
    )
    if cpi_bridge.applied:
        cpi_bridge.bridged_index_value = row.value * cpi_bridge.factor
    return _build_resolved(row, tender_quarter, fallback=True, bridge=cpi_bridge)


def _build_resolved(
    row: TPISeries,
    requested_quarter: str,
    *,
    fallback: bool,
    bridge: CPIBridge | None = None,
) -> ResolvedTPI:
    value = row.value
    if bridge is not None and bridge.applied:
        value = row.value * bridge.factor
    return ResolvedTPI(
        country=row.country,
        currency=row.currency,
        series_name=row.series_name,
        requested_quarter=requested_quarter,
        resolved_quarter=row.quarter,
        value=value,
        base_value=row.base_value or DEFAULT_BASE_YEAR_INDEX_VALUE,
        base_year=row.base_year,
        scope_inclusions=row.scope_inclusions,
        scope_exclusions=row.scope_exclusions,
        source_url=row.source_url,
        is_placeholder=row.is_placeholder,
        replace_with=row.replace_with,
        fallback_used=fallback,
        value_published=row.value,
        bridge=bridge,
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


def _and_list(items: list[str]) -> str:
    """'a', 'a and b', 'a, b and c' - so a warning reads as a sentence."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _month_span(months: list[str]) -> str:
    """'2024-10' for one month, '2024-10..2024-12' for a run - short enough for a warning."""
    if not months:
        return "n/a"
    if len(months) == 1:
        return months[0]
    return f"{months[0]}..{months[-1]}"


# --------------------------------------------------------------------------- #
# Benchmark rate selection
# --------------------------------------------------------------------------- #
def load_benchmark_rates(
    session: Session, country: str = DEFAULT_COUNTRY
) -> dict[str, BenchmarkRate]:
    """Index benchmark rates by section for one country, preferring the highest confidence.

    Ties are broken deterministically rather than by whatever order the database returns:
    a row that states its own base quarter wins over one that does not (it is the more
    specific statement of what the rate means), and after that the more recent base year
    wins. Without this, two rows in one section and the same confidence would make the
    benchmark depend on row order.
    """
    code = (country or DEFAULT_COUNTRY).strip().upper()
    rows = list(session.scalars(select(BenchmarkRate).where(BenchmarkRate.country == code)).all())

    def rank(row: BenchmarkRate) -> tuple[int, int, int]:
        return (
            CONFIDENCE_ORDER.get(row.confidence, 9),
            # A stated base quarter is more specific than "somewhere in a base year".
            0 if (row.base_quarter or "").strip() else 1,
            -int(row.base_year or 0),
        )

    chosen: dict[str, BenchmarkRate] = {}
    for row in rows:
        current = chosen.get(row.smm2_section)
        if current is None or rank(row) < rank(current):
            chosen[row.smm2_section] = row
    return chosen


def library_as_of(session: Session, country: str = DEFAULT_COUNTRY) -> dict:
    """The quarter a market's rate library is stated at, read from the library rows.

    A row's `base_quarter` says when its rate is expressed: the published schedule rate
    escalated to that quarter by CPI. At that quarter the index ratio is exactly 1.000, so
    it is the quarter to benchmark at when you want the library as published rather than
    escalated; anything later is carried forward by the index and disclosed.

    Returns the quarter plus the counts that make the statement checkable: how many sections
    state a quarter, how many fall back to the series base year, the currencies involved, and
    the oldest/newest source dates. A market whose rows disagree falls back to the majority
    quarter, and says how many rows disagree rather than averaging them into a fiction.
    """
    code = (country or DEFAULT_COUNTRY).strip().upper()
    rows = list(session.scalars(select(BenchmarkRate).where(BenchmarkRate.country == code)).all())
    stated = [(row.base_quarter or "").strip() for row in rows if (row.base_quarter or "").strip()]
    counts = Counter(stated)
    quarter = counts.most_common(1)[0][0] if counts else ""
    source_dates = sorted({(row.source_date or "").strip() for row in rows if row.source_date})
    currencies = sorted({(row.currency or "").strip() for row in rows if row.currency})
    return {
        "library_quarter": quarter,
        "library_default_tender_quarter": quarter or DEFAULT_TENDER_QUARTER,
        "library_sections_stated": len(stated),
        "library_sections_retained": len(rows) - len(stated),
        "library_sections_disagreeing": len(stated) - counts.get(quarter, 0),
        "library_source_date": source_dates[-1] if source_dates else "",
        "library_currencies": currencies,
        "library_currency": currencies[0] if len(currencies) == 1 else "",
    }


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
    # The schedule-of-rates code the line was quoted from, when it came from the template.
    # Carried through so the variance table's section subtotals can say how much of each
    # section came from the market's own schedule. See README "The upload template".
    sor_code: str
    is_benchmarked: bool
    exclusion_reason: str | None
    benchmark_base_rate: float | None
    adjusted_benchmark_rate: float | None
    variance_abs: float | None
    variance_pct: float | None
    should_cost_amount: float
    variance_amount: float
    # Overheads and margin: the analyst percentages that turn the benchmark rate
    # into a FULL cost. Present on every benchmarked line; zero when unused, in
    # which case full_should_cost_amount == should_cost_amount exactly.
    full_adjusted_benchmark_rate: float
    overhead_pct: float
    margin_pct: float
    overhead_amount: float
    margin_amount: float
    full_should_cost_amount: float
    overheads_in_tender: bool
    compared_against_full: bool
    tpi_series_name: str
    tpi_quarter_requested: str
    tpi_quarter_used: str
    tpi_value: float
    tpi_value_published: float
    tpi_base_value: float
    tpi_ratio: float
    tpi_fallback_used: bool
    # The quarter the library rate is expressed at, when the library states one. Empty
    # means the rate is at the index series' own base year. Reported so a reviewer can
    # see which denominator the escalation used.
    rate_base_quarter: str
    # Index bridge: the modelled step that carried a stale observation forward. The
    # bridge runs on a producer price index where the market publishes one, so the
    # kind travels with the line instead of being assumed to be a CPI.
    tpi_bridged: bool
    cpi_bridge_factor: float
    index_bridge_kind: str
    cpi_series_name: str
    cpi_month_used: str
    cpi_value_used: float | None
    cpi_base_value: float | None
    cpi_source_url: str
    index_lag_quarters: int
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
    # The index ratio at full precision. `tpi_ratio` is rounded to 6dp for display,
    # and the waterfall must not use the rounded value: for an excluded section the
    # scope factor is 1/exact_ratio, so pairing it with a rounded ratio leaves a
    # residual proportional to quantity x base_rate x 5e-7 (material on a large BoQ)
    # that would otherwise land in `unexplained`.
    tpi_ratio_exact: float = 0.0
    # The benchmark base rate at full precision. benchmark_base_rate is rounded to 2dp for
    # display, and the waterfall must not build money out of a rounded rate: the difference
    # grows with quantity and the regional multiplier and lands in the unexplained residual,
    # which is meant to be a rounding artefact and nothing else.
    benchmark_base_rate_exact: float | None = None
    # The same ratio computed from the PUBLISHED observation only, so the waterfall can
    # split total index movement into "since the rate library's base quarter" and "the
    # modelled carry-forward on top of it".
    tpi_ratio_published_exact: float | None = None


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
    index_bridge: dict
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
            "overhead_pct": 0.0,
            "margin_pct": 0.0,
            "overheads_in_tender": True,
            "is_noop": True,
            "is_noop_excluding_ohp": True,
        }
    raw = adjustments.model_dump() if hasattr(adjustments, "model_dump") else dict(adjustments)
    section_map = {
        str(k): float(v) for k, v in (raw.get("section_rate_scale_pct") or {}).items() if v
    }
    overhead_pct = float(raw.get("overhead_pct") or 0.0)
    margin_pct = float(raw.get("margin_pct") or 0.0)
    # Whether the tendered BoQ rates already carry overheads and profit. It decides
    # which benchmark rate the variance is measured against; it changes no cost.
    overheads_in_tender = raw.get("overheads_in_tender")
    overheads_in_tender = True if overheads_in_tender is None else bool(overheads_in_tender)
    index_noop = (
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
        "overhead_pct": overhead_pct,
        "margin_pct": margin_pct,
        "overheads_in_tender": overheads_in_tender,
        # `is_noop` covers every analyst input, so a run whose only input is an
        # overhead percentage is reported as assumed, not derived.
        "is_noop": index_noop and not overhead_pct and not margin_pct,
        # Kept separately: the comparison basis only changes when the analyst has
        # actually supplied overheads or margin.
        "is_noop_excluding_ohp": index_noop,
        "ohp_active": bool(overhead_pct or margin_pct),
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
    index_bridge: str = BRIDGE_AUTO,
) -> BenchmarkComputation:
    registry: Country = get_country(country)
    applied = _adjustments_dict(adjustments)
    supplied_rates = _manual_rates_dict(manual_rates)
    applied["manual_rate_count"] = len(supplied_rates)
    region = resolve_region(session, registry.code, region_code)
    tpi = resolve_tpi(
        session, tpi_series_name, tender_quarter, country=registry.code, bridge=index_bridge
    )
    rates = load_benchmark_rates(session, country=registry.code)
    effective_tpi = _effective_tpi_value(tpi.value, applied)
    ratio = tpi.ratio_for(effective_tpi)

    # ------------------------------------------------------------------ base quarter --
    # A rate library row may state the quarter its price level is expressed at. The
    # SOR-derived rows do: they are already cumulative-adjusted to 2026Q2, so the index
    # denominator for those rows is the index AT 2026Q2, not the series' own base year.
    # Escalating from the series base as well would apply the same movement twice -
    # 2010->2026 on top of a rate that already contains 2022->2026.
    #
    # Rows with no base_quarter keep the previous behaviour exactly (the series base
    # value), so nothing that predates this feature changes.
    base_index_cache: dict[str, float] = {}

    def _index_at(quarter: str) -> float:
        """The index at a quarter, bridged the same way as the tender quarter.

        Deliberately WITHOUT the analyst's index shift: that shift is a statement about
        the tender quarter, so applying it to both ends of the ratio would cancel it out.
        """
        if quarter not in base_index_cache:
            resolved = resolve_tpi(
                session, tpi.series_name, quarter, country=registry.code, bridge=index_bridge
            )
            base_index_cache[quarter] = resolved.value
        return base_index_cache[quarter]

    def ratio_denominator(rate_row) -> tuple[float, str]:
        """(the index level the rate is expressed at, the quarter if the library states one).

        The second element is "" when the row does not state a quarter - the rate is then
        at the index series' own base YEAR, which is not a quarter and must not be parsed
        as one.
        """
        stated = (getattr(rate_row, "base_quarter", "") or "").strip()
        if stated:
            return _index_at(stated), stated
        if not tpi.base_value:
            raise TPILookupError("TPI base value is zero; cannot compute a ratio.")
        return tpi.base_value, ""

    def ratios_for(rate_row) -> tuple[float, float]:
        """(bridged ratio, published-only ratio) for one library row.

        The second is what the waterfall calls market risk: the movement the index has
        actually PUBLISHED since the rate library's base quarter. When the library is
        expressed at or after the last published observation - which is the case here,
        with rates at 2026Q2 against an index last published at 2024Q4 - nothing since
        the rate base has been published at all, so the whole difference is the modelled
        carry-forward and market risk is zero. Reporting the raw backwards ratio instead
        would book a negative "market risk" that is really the unwinding of the bridge.
        """
        denominator, stated = ratio_denominator(rate_row)
        bridged_ratio = effective_tpi / denominator
        if stated and quarter_sort_key(stated) >= quarter_sort_key(tpi.resolved_quarter):
            return bridged_ratio, 1.0
        return bridged_ratio, effective_tpi_published / denominator
    # The same composition, but starting from the PUBLISHED observation instead of
    # the bridged one. The difference between the two ratios is exactly the CPI
    # bridge, which the waterfall reports as its own step. An absolute override
    # replaces both levels, so it contributes nothing to the bridge step.
    effective_tpi_published = _effective_tpi_value(tpi.value_published, applied)
    ratio_published_used = tpi.ratio_for(effective_tpi_published)
    # Which quarters the loaded rate library is expressed at, for the disclosure text.
    stated_quarters = sorted(
        {r.base_quarter for r in rates.values() if (r.base_quarter or "").strip()}
    )
    bridge = tpi.bridge
    bridged = tpi.bridge_applied

    # Overheads and margin for the whole run. They convert the benchmark cost of the
    # BENCHMARKED lines into a full cost; lines carried at the tendered rate are left
    # alone, because that rate already carries the contractor's own OH&P.
    overhead_pct = float(applied["overhead_pct"])
    margin_pct = float(applied["margin_pct"])
    overheads_in_tender = bool(applied["overheads_in_tender"])
    ohp_applied = bool(overhead_pct or margin_pct)

    present_sections = {item.smm2_section for item in items}
    exclusion_hits = excluded_sections(tpi.scope_exclusions, present_sections)

    warns: list[str] = []
    assumes: list[str] = []

    if tpi.fallback_used:
        warns.append(
            f"No {tpi.series_name} TPI observation is published for {tpi.requested_quarter}. "
            f"The nearest prior quarter, {tpi.resolved_quarter}, was used instead."
        )
    if bridged and bridge is not None:
        through = bridge.covered_through_month
        # Name the index the bridge actually used - PPI where one is available, CPI
        # only as the fallback - and describe what that index measures, because the
        # strength of the assumption depends on which one it is.
        price_label = (
            "producer price index" if bridge.kind == "PPI" else "consumer price index"
        )
        from_span = _month_span(bridge.from_months)
        to_span = _month_span(bridge.to_months)
        detail = (
            f"Index freshness: {tpi.series_name} last published {tpi.resolved_quarter}, "
            f"{tpi.lag_quarters} quarter(s) before {tpi.requested_quarter}. Carried forward to "
            f"{through} on the {bridge.series_name} {price_label}: {from_span} "
            f"{bridge.from_value:.3f} -> {to_span} {bridge.to_value:.3f}, a factor of "
            f"{bridge.factor:.4f}. A MODELLED step, not a construction cost observation, so "
            f"every line it touches is basis='assumed' and flagged index_bridged."
        )
        # The caveats, one short clause each rather than a sentence apiece.
        notes = []
        if bridge.partial:
            notes.append(
                f"only {len(bridge.to_months)} of 3 months published in "
                f"{quarter_of_month(bridge.covered_through_month)}"
            )
        if bridge.short:
            notes.append(
                f"{bridge.shortfall_months} month(s) short of the end of {tpi.requested_quarter}"
            )
        if bridge.is_placeholder:
            notes.append(f"the {bridge.series_name} series is itself indicative")
        if notes:
            detail += " Note: " + "; ".join(notes) + "."
        warns.append(detail)
    elif tpi.fallback_used and (index_bridge or BRIDGE_AUTO).strip().lower() != BRIDGE_NONE:
        reason_text = {
            "no_price_series_configured_for_country": (
                "No producer or consumer price series is configured for this market"
            ),
            "no_price_observations_loaded": "No monthly price observations are loaded",
            "no_cpi_series_configured_for_country": (
                "No producer or consumer price series is configured for this market"
            ),
            "no_cpi_observations_loaded": "No monthly price observations are loaded",
            "no_price_observation_for_the_observation_quarter": (
                "No price series has an observation for the index quarter"
            ),
            "no_cpi_observation_for_the_observation_quarter": (
                "No price series has an observation for the index quarter"
            ),
            "no_price_observation_after_the_index_observation": (
                "No price series has an observation after the index quarter"
            ),
            "no_cpi_observation_after_the_index_observation": (
                "No price series has an observation after the index quarter"
            ),
            "no_price_series_covers_both_quarters": (
                "No price series loaded for this market spans both the index quarter and the "
                "tender quarter (the publisher rebased the index, so the older and newer series "
                "do not overlap and are not chained)"
            ),
            "no_cpi_series_covers_both_quarters": (
                "No price series loaded for this market spans both the index quarter and the "
                "tender quarter (the publisher rebased the index, so the older and newer series "
                "do not overlap and are not chained)"
            ),
            "price_value_at_the_observation_quarter_is_zero": (
                "The price index value at the index quarter is zero"
            ),
            "cpi_value_at_the_observation_quarter_is_zero": (
                "The price index value at the index quarter is zero"
            ),
        }.get(bridge.reason if bridge else "", "The index bridge could not be computed")
        warns.append(
            f"Index freshness: {reason_text.lower()}, so the {tpi.resolved_quarter} "
            f"{tpi.series_name} observation was held unchanged for {tpi.requested_quarter} "
            f"instead of being carried forward - the index is therefore stale by "
            f"{tpi.lag_quarters} quarter(s). Load a monthly producer price series for this market "
            f"(python -m app.importer --kind price_series) to bridge it."
        )
    if (index_bridge or BRIDGE_AUTO).strip().lower() == BRIDGE_NONE and tpi.lag_quarters > 0:
        warns.append(
            f"Index freshness: the index bridge is switched OFF for this run. The "
            f"{tpi.resolved_quarter} {tpi.series_name} observation was held unchanged for "
            f"{tpi.requested_quarter} ({tpi.lag_quarters} quarter(s) stale)."
        )
    if bridged:
        assumes.append(
            f"ASSUMED (modelled): the {tpi.series_name} index for {tpi.requested_quarter} is not "
            f"a published observation. Carried from {tpi.resolved_quarter} = {tpi.value_published:.4f} along the "
            f"{bridge.series_name} movement {_month_span(bridge.from_months)} "
            f"{bridge.from_value:.4f} -> {_month_span(bridge.to_months)} {bridge.to_value:.4f} "
            f"= {tpi.value:.4f} (x{bridge.factor:.4f}), so it shows the trend to date, not a "
            f"tender-quarter observation. Source: {bridge.source_url or 'not stated'}."
        )
    if tpi.is_placeholder:
        warns.append(
            f"TPI series {tpi.series_name} {tpi.resolved_quarter} is a retained index value for "
            f"this market rather than the latest published quarter, so the movement carried into "
            f"the tender quarter is derived from it. Source: {tpi.source_url or 'not stated'}."
        )
    if exclusion_hits:
        # ONE warning for the whole run. Emitting a paragraph per section said the same thing
        # three times and buried the sections that were actually affected.
        sections_hit = sorted(exclusion_hits)
        phrases = sorted({phrase for phrase in exclusion_hits.values()})
        warns.append(
            f"Scope exclusion: {tpi.series_name} excludes "
            f"{_and_list([repr(p) for p in phrases])}; "
            f"{_and_list(sections_hit)} "
            f"{'is' if len(sections_hit) == 1 else 'are'} present in this BoQ, so those rates are "
            f"held at the library level (scope factor 1/{ratio:.4f}). A modelling assumption, not "
            f"a measured value."
        )

    # Base-year mismatch: the index ratio is only meaningful when the rate and the index share
    # a base year. A row that states its own base quarter is exempt - the engine escalates it
    # from that quarter, so its base_year differing from the series is the intended design and
    # warning about it would be noise.
    mismatched_years = sorted(
        {
            row.base_year
            for row in rates.values()
            if row.base_year != tpi.base_year and not (row.base_quarter or "").strip()
        }
    )
    if mismatched_years:
        warns.append(
            f"Base-year mismatch: {registry.code} rate(s) at base year "
            f"{', '.join(str(y) for y in mismatched_years)} are escalated by the {tpi.series_name} "
            f"ratio, which is based on {tpi.base_year}. Valid only if those rates are rebased to "
            f"{tpi.base_year}. Verify before relying on them."
        )

    if region.is_placeholder and region.factor != 1.0:
        warns.append(
            f"Regional adjustment: {region.region_name} ({region.region_code}) carries a "
            f"{region.factor:.3f} multiplier on every benchmark base rate. It is a modelled "
            f"locational adjustment rather than a measured city index. Source: "
            f"{region.source or 'not stated'}."
        )
    if stated_quarters:
        # The rate library is expressed at a later quarter than the index series' own base
        # year. Say so, and say that the index at those quarters is itself derived, because
        # the reader cannot see the denominator from any single number on the page.
        listed = ", ".join(stated_quarters)
        assumes.append(
            f"ASSUMED: the {registry.code} rate library is expressed at {listed}, not at the "
            f"{tpi.series_name} base year of {tpi.base_year}. Sections stating a base quarter are "
            f"escalated from THAT quarter; retained estimates are not, which is why their ratios "
            f"differ. The schedules of rates were cumulative-adjusted to {listed} by the "
            f"publisher's own factor, and {tpi.series_name} has published only to "
            f"{tpi.resolved_quarter}, so its value at {listed} is DERIVED."
        )
    assumes.append(
        f"ASSUMED: benchmark base rates are multiplied by {region.factor:.3f} for "
        f"{region.region_name} ({region.region_code}). Source: {region.source}. A single "
        f"blended factor, not a material/labour split."
    )

    if applied["tpi_value_override"] is not None:
        assumes.append(
            f"ASSUMED: the analyst overrode the {tpi.series_name} index value for "
            f"{tpi.resolved_quarter} with {applied['tpi_value_override']:.4f}, replacing the "
            f"stored value of {tpi.value:.4f}. Every affected line is basis='assumed'. The "
            f"published series is the authority for this market; the override is a what-if."
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
                    sor_code=(item.sor_code or ""),
                    is_benchmarked=False,
                    exclusion_reason=exclusion_reason,
                    benchmark_base_rate=None,
                    adjusted_benchmark_rate=None,
                    variance_abs=None,
                    variance_pct=None,
                    # No benchmark evidence exists, so should-cost is held at the
                    # tendered rate and contributes zero tested variance. Overheads
                    # and margin are deliberately NOT added here: the tendered rate
                    # already carries the contractor's own OH&P, and grossing it up
                    # again would double-count them.
                    should_cost_amount=boq_amount,
                    variance_amount=0.0,
                    full_adjusted_benchmark_rate=None,
                    overhead_pct=0.0,
                    margin_pct=0.0,
                    overhead_amount=0.0,
                    margin_amount=0.0,
                    full_should_cost_amount=boq_amount,
                    overheads_in_tender=overheads_in_tender,
                    compared_against_full=False,
                    tpi_series_name=tpi.series_name,
                    tpi_quarter_requested=tpi.requested_quarter,
                    tpi_quarter_used=tpi.resolved_quarter,
                    tpi_value=round(effective_tpi, 4),
                    tpi_value_published=round(tpi.value_published, 4),
                    tpi_base_value=tpi.base_value,
                    tpi_ratio=round(ratio, 6),
                    rate_base_quarter=(rate_row.base_quarter or "") if rate_row is not None else "",
                    tpi_fallback_used=tpi.fallback_used,
                    tpi_bridged=bridged,
                    cpi_bridge_factor=round(tpi.bridge_factor, 6),
                    index_bridge_kind=(bridge.kind if bridged and bridge else ""),
                    cpi_series_name=(bridge.series_name if bridged and bridge else ""),
                    cpi_month_used=(bridge.covered_through_month or "") if bridged and bridge else "",
                    cpi_value_used=(round(bridge.to_value, 4) if bridged and bridge else None),
                    cpi_base_value=(bridge.base_value if bridged and bridge else None),
                    cpi_source_url=(bridge.source_url if bridged and bridge else ""),
                    index_lag_quarters=tpi.lag_quarters,
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

        # The index ratio this line is escalated by. A library row that states a base
        # quarter is escalated from that quarter; everything else, including an
        # analyst-supplied rate, keeps the series-level ratio.
        if manual is None and rate_row is not None:
            line_ratio, line_ratio_published = ratios_for(rate_row)
        else:
            line_ratio, line_ratio_published = ratio, ratio_published_used

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
            scope_factor = (1.0 / line_ratio) if scope_excluded else 1.0
            if scope_excluded:
                flags.append("scope_excluded")
            adjusted_base_rate = rate_row.base_rate * region.factor * (1.0 + section_scale / 100.0)
            adjusted = adjusted_base_rate * line_ratio * scope_factor

        # ---------------------------------------------------- overheads & margin --
        # Analyst-supplied percentages that turn a benchmark RATE into a FULL cost:
        #
        #     full_rate = adjusted_rate x (1 + overhead_pct/100) x (1 + margin_pct/100)
        #
        # Margin is applied AFTER overheads, i.e. it is compounded on them, which is
        # the usual commercial convention and is stated in assumptions[]. Neither is
        # evidence, so any line they touch is basis="assumed".
        quantity = item.quantity
        overhead_amount = adjusted * quantity * overhead_pct / 100.0
        margin_base = adjusted * quantity + overhead_amount
        margin_amount = margin_base * margin_pct / 100.0
        full_adjusted = adjusted * (1.0 + overhead_pct / 100.0) * (1.0 + margin_pct / 100.0)
        ohp_applied = bool(overhead_pct or margin_pct)
        # Which rate the tendered BoQ rate is compared against. A BoQ rate normally
        # already carries overheads and profit, so the default is a full-to-full
        # comparison; an analyst who knows their tender excludes them can say so.
        compare_against = full_adjusted if (ohp_applied and overheads_in_tender) else adjusted

        variance_abs_raw = item.boq_rate - compare_against
        variance_pct_raw = (variance_abs_raw / compare_against * 100.0) if compare_against else None

        if variance_pct_raw is not None:
            if variance_pct_raw >= variance_threshold:
                flags.append("over_threshold")
            elif variance_pct_raw <= -variance_threshold:
                flags.append("under_threshold")
            else:
                flags.append("within_threshold")

        if manual is None and rate_row is not None and rate_row.is_placeholder:
            flags.append("retained_library_rate")
        if bridged:
            flags.append("index_bridged")
        if overhead_pct:
            flags.append("overhead_applied")
        if margin_pct:
            flags.append("margin_applied")

        # A regional multiplier is a modelling input, not an observation, so any
        # line it touches is basis="assumed" - exactly like a manual adjuster. A
        # CPI bridge is modelled too: the index value at the tender quarter is not
        # a published construction cost observation. So are overheads and margin.
        user_adjusted = (
            manual is not None
            or bool(section_scale)
            or applied["tpi_value_override"] is not None
            or bool(applied["tpi_scale_pct"])
            or (is_indexed and region.factor != 1.0)
        )
        if user_adjusted:
            flags.append("user_adjusted")

        boq_amount = round(quantity * item.boq_rate, 2)
        should_cost_amount = round(quantity * adjusted, 2)
        full_should_cost_amount = round(quantity * full_adjusted, 2)
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
                sor_code=(item.sor_code or ""),
                is_benchmarked=True,
                exclusion_reason=None,
                benchmark_base_rate=round(adjusted_base_rate, 2),
                adjusted_benchmark_rate=round(adjusted, 2),
                variance_abs=round(variance_abs_raw, 2),
                variance_pct=round(variance_pct_raw, 2) if variance_pct_raw is not None else None,
                should_cost_amount=should_cost_amount,
                variance_amount=round(boq_amount - should_cost_amount, 2),
                full_adjusted_benchmark_rate=round(full_adjusted, 2),
                overhead_pct=round(overhead_pct, 4),
                margin_pct=round(margin_pct, 4),
                overhead_amount=round(overhead_amount, 2),
                margin_amount=round(margin_amount, 2),
                full_should_cost_amount=full_should_cost_amount,
                overheads_in_tender=overheads_in_tender,
                compared_against_full=bool(ohp_applied and overheads_in_tender),
                tpi_series_name=tpi.series_name,
                tpi_quarter_requested=tpi.requested_quarter,
                tpi_quarter_used=tpi.resolved_quarter,
                tpi_value=round(effective_tpi, 4),
                tpi_value_published=round(tpi.value_published, 4),
                tpi_base_value=tpi.base_value,
                tpi_ratio=round(line_ratio, 6),
                # Full precision for the waterfall; see the LineResult docstring.
                tpi_ratio_exact=line_ratio,
                tpi_ratio_published_exact=line_ratio_published,
                benchmark_base_rate_exact=adjusted_base_rate,
                rate_base_quarter=(rate_row.base_quarter or "") if rate_row is not None else "",
                tpi_fallback_used=tpi.fallback_used,
                tpi_bridged=bridged,
                cpi_bridge_factor=round(tpi.bridge_factor, 6),
                index_bridge_kind=(bridge.kind if bridged and bridge else ""),
                cpi_series_name=(bridge.series_name if bridged and bridge else ""),
                cpi_month_used=(bridge.covered_through_month or "") if bridged and bridge else "",
                cpi_value_used=(round(bridge.to_value, 4) if bridged and bridge else None),
                cpi_base_value=(bridge.base_value if bridged and bridge else None),
                cpi_source_url=(bridge.source_url if bridged and bridge else ""),
                index_lag_quarters=tpi.lag_quarters,
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
                basis=BASIS_ASSUMED if (user_adjusted or bridged or ohp_applied) else BASIS_DERIVED,
                flags=flags,
                provenance=(
                    {
                        "source": "Analyst-supplied manual rate",
                        "source_date": datetime.now(timezone.utc).date().isoformat(),
                        "base_year": (tpi.base_year if is_indexed else int(tpi.resolved_quarter[:4])),
                        "scope_inclusions": "As stated by the analyst for this individual line",
                        "scope_exclusions": "" if is_indexed else "Index not applied - rate stated at tender-quarter levels",
                        "confidence": "analyst",
                        "source_url": None,
                        "note": manual["note"] or "Analyst-supplied rate for this scope.",
                    }
                    if manual is not None
                    else {
                        "source": rate_row.source,
                        "source_date": rate_row.source_date,
                        "base_year": rate_row.base_year,
                        "scope_inclusions": rate_row.scope_inclusions,
                        "scope_exclusions": rate_row.scope_exclusions,
                        "confidence": rate_row.confidence,
                        "source_url": rate_row.source_url,
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
    # The FULL cost: benchmark cost of the benchmarked lines grossed up by the
    # analyst's overhead and margin percentages, plus the unbenchmarked lines at the
    # tendered rate (which already carries OH&P). Equal to should_cost_total when no
    # percentages were supplied.
    overhead_total = round(sum(l.overhead_amount for l in lines), 2)
    margin_total = round(sum(l.margin_amount for l in lines), 2)
    full_should_cost_total = round(sum(l.full_should_cost_amount for l in lines), 2)
    # Headline variance follows the SAME basis as the per-line variances: a
    # full-to-full comparison when the analyst says the tender carries OH&P, and the
    # benchmark cost when they say it does not. Without OH&P the two are identical.
    comparison_total = (
        full_should_cost_total if (ohp_applied and overheads_in_tender) else should_cost_total
    )
    total_variance_abs = round(boq_total - comparison_total, 2)
    total_variance_pct = (
        round(total_variance_abs / comparison_total * 100.0, 2) if comparison_total else None
    )
    full_variance_abs = round(boq_total - full_should_cost_total, 2)
    full_variance_pct = (
        round(full_variance_abs / full_should_cost_total * 100.0, 2) if full_should_cost_total else None
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
        sec_full = round(sum(m.full_should_cost_amount for m in members), 2)
        sec_var = round(sec_boq - sec_should, 2)
        sec_pct = round(sec_var / sec_should * 100.0, 2) if sec_should else None
        sec_full_var = round(sec_boq - sec_full, 2)
        benchmarked = [m for m in members if m.is_benchmarked]
        sections.append(
            {
                "smm2_section": name,
                "item_count": len(members),
                "benchmarked_item_count": len(benchmarked),
                "boq_amount": sec_boq,
                "should_cost_amount": sec_should,
                "full_should_cost_amount": sec_full,
                "overhead_amount": round(sum(m.overhead_amount for m in members), 2),
                "margin_amount": round(sum(m.margin_amount for m in members), 2),
                "full_variance_amount": sec_full_var,
                "full_variance_pct": (
                    round(sec_full_var / sec_full * 100.0, 2) if sec_full else None
                ),
                "variance_amount": sec_var,
                "variance_abs": sec_var,
                "variance_pct": sec_pct,
                "basis": (
                    BASIS_DERIVED
                    if (benchmarked and not any_user_adjusted and not bridged and not ohp_applied)
                    else BASIS_ASSUMED
                ),
                "breaches_threshold": any(
                    "over_threshold" in m.flags or "under_threshold" in m.flags for m in members
                ),
            }
        )

    # ------------------------------------------------------------- waterfall --
    waterfall = _build_waterfall(
        lines, boq_total, should_cost_total, full_should_cost_total, ratio_published_used
    )

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

    # ------------------------------------------------------- overheads & margin --
    if ohp_applied:
        currency_code = registry.currency
        assumes.append(
            f"ASSUMED: the analyst added {overhead_pct:.2f}% overheads and {margin_pct:.2f}% margin "
            f"on top of the benchmark cost, giving a FULL should-cost of {currency_code} "
            f"{full_should_cost_total:,.2f} against a benchmark cost of {currency_code} "
            f"{should_cost_total:,.2f} (overheads {currency_code} {overhead_total:,.2f} + margin "
            f"{currency_code} {margin_total:,.2f}). Margin is applied AFTER overheads, i.e. it is "
            f"compounded on them. Neither percentage is a published observation: they are commercial "
            f"inputs, so every benchmarked line is basis='assumed' and flagged overhead_applied / "
            f"margin_applied, and the two steps appear as their own bars in the waterfall."
        )
        assumes.append(
            "ASSUMED: overhead and margin are a SINGLE blended pair, not differentiated by trade. "
            "Split them per section if the estimate needs that resolution."
        )
        assumes.append(
            "ASSUMED: overheads and margin apply to BENCHMARKED lines only. Lines held at the "
            "tendered rate are not grossed up, because that rate already carries the contractor's "
            "own OH&P."
        )
        if overheads_in_tender:
            assumes.append(
                "ASSUMED: the tendered BoQ rates are taken to ALREADY INCLUDE overheads and profit, "
                "so each line's variance is measured full-to-full (tendered rate against the "
                "benchmark rate grossed up by the same percentages). Set 'overheads_in_tender' to "
                "false if the tender rates are net of OH&P, and the comparison reverts to the "
                "benchmark rate before overheads."
            )
        else:
            warns.append(
                "Overheads and margin are ADDED to the full should-cost but EXCLUDED from the "
                "variance test, because this run declares that the tendered BoQ rates do not carry "
                "OH&P. The full should-cost and the benchmark cost therefore differ by "
                f"{currency_code} {full_should_cost_total - should_cost_total:,.2f}; read the "
                f"full_should_cost_* figures for the commercial total."
            )
    if any(l.is_benchmarked for l in lines) and abs(waterfall[2]["amount"]) + abs(waterfall[3]["amount"]) > 0:
        assumes.append(
            f"ASSUMED: the tendered-to-benchmark rate gap was apportioned "
            f"{int(MATERIAL_SHARE * 100)}% material / {int(LABOUR_SHARE * 100)}% labour, so both "
            f"waterfall bars are basis='assumed'. The split is the app's standing convention for "
            f"breaking a rate gap into its two components; the gap itself is measured, the split "
            f"between them is not."
        )
    if exclusion_hits:
        assumes.append(
            "ASSUMED: for sections excluded by the selected TPI series ("
            + ", ".join(sorted(exclusion_hits))
            + "), scope_factor was set to 1/TPI ratio, holding those rates at base year rather "
            "than indexing them with an index that does not measure that scope."
        )
    if tpi.is_placeholder or any(
        "retained_library_rate" in l.flags for l in lines
    ):
        assumes.append(
            f"ASSUMED: some rates in this run are derived for {registry.name} rather than taken from "
            f"a published observation - each affected row names the source it came from and the "
            f"quarter it is expressed at, and is labelled basis = derived or assumed accordingly. "
            f"Where a rate is derived from the published schedule of rates for the section, it is "
            f"the library rate for that section, in the market's own currency."
        )

    index_bridge_summary = (
        bridge.as_dict()
        if bridge is not None
        else {
            "applied": False,
            "reason": "index_observation_covers_requested_quarter",
            "mode": (
                BRIDGE_AUTO
                if (index_bridge or BRIDGE_AUTO).strip().lower() != BRIDGE_NONE
                else BRIDGE_NONE
            ),
            "series_name": tpi.series_name,
            "requested_quarter": tender_quarter,
            "observation_quarter": tpi.resolved_quarter,
            "lag_quarters": 0,
            "bridged_to_quarter": None,
            "bridged_through_month": None,
            "shortfall_months": 0,
            "partial_quarter": False,
            "cpi_series_name": registry.default_cpi_series,
            "cpi_base_year": None,
            "cpi_base_value": None,
            "cpi_from_value": None,
            "cpi_from_months": [],
            "cpi_to_value": None,
            "cpi_to_months": [],
            "cpi_bridge_factor": 1.0,
            "cpi_source_url": "",
            "cpi_is_placeholder": False,
            "cpi_provenance_note": "",
            "index_value_published": round(tpi.value_published, 4),
            "index_value_bridged": round(tpi.value, 4),
        }
    )
    index_bridge_summary["index_series"] = tpi.series_name
    index_bridge_summary["requested_bridge_mode"] = (
        BRIDGE_AUTO if (index_bridge or BRIDGE_AUTO).strip().lower() != BRIDGE_NONE else BRIDGE_NONE
    )
    index_bridge_summary["index_observation_is_placeholder"] = tpi.is_placeholder

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
        index_bridge=index_bridge_summary,
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
            # Overheads and margin: the analyst inputs and the full cost they produce.
            "overhead_pct": round(overhead_pct, 4),
            "margin_pct": round(margin_pct, 4),
            "overheads_in_tender": overheads_in_tender,
            "overhead_amount_total": overhead_total,
            "margin_amount_total": margin_total,
            "full_should_cost_total": full_should_cost_total,
            "full_variance_abs": full_variance_abs,
            "full_variance_pct": full_variance_pct,
            # What the headline variance was measured against: the full cost, or the
            # benchmark cost before overheads. Same basis as the per-line variances.
            "variance_basis_total": comparison_total,
            "variance_basis": (
                "full_including_overheads"
                if (ohp_applied and overheads_in_tender)
                else "benchmark_before_overheads"
            ),
            # Whether the index actually used for the tender quarter is a published
            # observation (derived), or was carried forward with the CPI (assumed).
            "index_bridge_applied": bool(index_bridge_summary["applied"]),
            "index_lag_quarters": index_bridge_summary["lag_quarters"],
            "index_bridged_lines": sum(1 for l in lines if l.tpi_bridged),
            "basis": (
                BASIS_DERIVED
                if (applied["is_noop"] and not index_bridge_summary["applied"])
                else BASIS_ASSUMED
            ),
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


def _build_waterfall(
    lines: list[LineResult],
    boq_total: float,
    should_cost_total: float,
    full_should_cost_total: float | None = None,
    ratio_published_used: float | None = None,
) -> list[dict]:
    """Reconcile boq_total to the FULL should-cost total.

    Identity: boq_total + material + labour + market_risk + cpi_bridge + scope
              + overhead + margin + unexplained == full_should_cost_total

    `full_should_cost_total` equals `should_cost_total` whenever no overhead or margin
    percentages were supplied, and the two extra steps are then exactly zero, so a run
    without them is numerically identical to before.

    `market_risk` is the movement in the PUBLISHED index observation between the
    benchmark base year and the quarter the series last published. `cpi_bridge` is
    the modelled step that carries that observation forward to the tender quarter.
    The two always sum to the same figure the single market_risk term used to
    carry, so a run with no bridge is numerically identical to before.
    """
    matched = [l for l in lines if l.is_benchmarked]
    full_total = should_cost_total if full_should_cost_total is None else full_should_cost_total

    def ratio_of(line) -> float:
        """The index ratio at full precision (never the rounded display value)."""
        return line.tpi_ratio_exact or line.tpi_ratio

    def base_of(line) -> float:
        """The benchmark base rate at full precision (never the rounded display value)."""
        exact = getattr(line, "benchmark_base_rate_exact", None)
        return exact if exact is not None else line.benchmark_base_rate

    def published_ratio_of(line) -> float:
        """The same ratio from the PUBLISHED observation only.

        Per line, because a library row that states its own base quarter has a different
        denominator from one that does not. The movement from that denominator to the last
        published observation is market risk; everything above it is the modelled
        carry-forward.
        """
        published = getattr(line, "tpi_ratio_published_exact", None)
        if published:
            return published
        return ratio_published_used if ratio_published_used is not None else ratio_of(line)

    combined_raw = sum(
        l.quantity * base_of(l) * (ratio_of(l) - 1.0) for l in matched
    )
    market_risk = round(
        sum(
            l.quantity * base_of(l) * (published_ratio_of(l) - 1.0)
            for l in matched
        ),
        2,
    )
    # Rounded as the difference of the same total, so market_risk + cpi_bridge is
    # exactly the value the pre-bridge engine reported for market_risk.
    cpi_bridge = round(combined_raw, 2) - market_risk
    scope = round(
        sum(
            l.quantity * base_of(l) * ratio_of(l) * (l.scope_factor - 1.0)
            for l in matched
        ),
        2,
    )
    rate_gap = sum(l.quantity * (base_of(l) - l.boq_rate) for l in matched)
    material = round(rate_gap * MATERIAL_SHARE, 2)
    labour = round(rate_gap * LABOUR_SHARE, 2)
    # Overheads and margin are additive and computed from the benchmarked cost only,
    # so they bridge should_cost_total to the full total exactly.
    overhead = round(sum(l.overhead_amount for l in matched), 2)
    margin = round(sum(l.margin_amount for l in matched), 2)
    unexplained = round(
        full_total
        - boq_total
        - material
        - labour
        - market_risk
        - cpi_bridge
        - scope
        - overhead
        - margin,
        2,
    )

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
            "method": "sum(quantity x base_rate x (published_tpi_ratio - 1))",
            "justification": (
                "Price movement between the benchmark base year and the last PUBLISHED quarter of "
                "the selected index series, derived from that series. Indexed sections only. The "
                "analyst's own index shift (if any) is carried here too."
            ),
        },
        {
            "component": "cpi_bridge",
            "amount": cpi_bridge,
            "basis": BASIS_ASSUMED,
            "method": "sum(quantity x base_rate x (index_ratio_used - published_tpi_ratio))",
            "justification": (
                "Modelled step that carries the last published index observation forward to the "
                "tender quarter, using the observed change in the national consumer price index. "
                "Consumer prices are not construction costs, so this step is an assumption, not "
                "evidence. Zero when the index observation already covers the tender quarter or "
                "when the analyst switched the bridge off."
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
            "component": "overhead",
            "amount": overhead,
            "basis": BASIS_ASSUMED,
            "method": "sum(quantity x adjusted_rate) x overhead_pct/100",
            "justification": (
                "Analyst-supplied overhead percentage applied to the benchmark cost of the "
                "benchmarked lines. Site and head-office overheads are a commercial input, not a "
                "published observation. Zero when no percentage was supplied. Unbenchmarked lines "
                "are excluded, because they are carried at the tendered rate, which already "
                "includes the contractor's own overheads."
            ),
        },
        {
            "component": "margin",
            "amount": margin,
            "basis": BASIS_ASSUMED,
            "method": "(sum(quantity x adjusted_rate) + overhead) x margin_pct/100",
            "justification": (
                "Analyst-supplied profit margin, applied AFTER overheads (compounded) in the usual "
                "commercial convention. Not an observation. Zero when no percentage was supplied."
            ),
        },
        {
            "component": "unexplained",
            "amount": unexplained,
            "basis": BASIS_DERIVED,
            "method": (
                "full_should_cost_total - boq_total - (material + labour + market_risk "
                "+ cpi_bridge + scope + overhead + margin)"
            ),
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
    index_bridge: str = BRIDGE_AUTO,
) -> dict:
    """Sweep the index value and each section's rate, and report the effect.

    Everything here is derived from the same engine as the headline benchmark, so
    the baseline point is guaranteed to equal build_benchmark() to the cent.
    """
    registry = get_country(country)
    applied = _adjustments_dict(adjustments)
    # Resolve the published observation once; every scenario is derived from it.
    resolved = resolve_tpi(
        session, tpi_series_name, tender_quarter, country=registry.code, bridge=index_bridge
    )
    published_tpi = resolved.value_published
    bridged_tpi = resolved.value

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
        index_bridge=index_bridge,
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
            # The overheads and margin inputs ride along, so every sweep point is a
            # FULL should-cost on the same commercial assumptions as the headline run.
            "overhead_pct": applied["overhead_pct"],
            "margin_pct": applied["margin_pct"],
            "overheads_in_tender": applied["overheads_in_tender"],
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
            index_bridge=index_bridge,
        )

    def scenario_tpi_value(scale_pct: float) -> float:
        return _effective_tpi_value(
            bridged_tpi,
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
        f"{max(abs(tpi_scale_min_pct), abs(tpi_scale_max_pct)):.1f}% around the "
        f"{'bridged' if resolved.bridge_applied else 'published'} value "
        f"({bridged_tpi:.4f}) and each section's rate by +/-{section_scale_pct:.1f}% in isolation. "
        f"These are scenario inputs chosen to bracket plausible outcomes, not forecasts."
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
        "baseline_tpi_value_published": round(published_tpi, 4),
        "index_bridge": base.index_bridge,
        "baseline": base.totals,
        "break_even_scale_pct": break_even_scale_pct,
        "break_even_tpi_value": break_even_tpi_value,
        "tpi_sweep": sweep,
        "section_tornado": tornado,
        "most_sensitive_section": tornado[0]["smm2_section"] if tornado else None,
        "assumptions": assumptions,
        "warnings": list(base.warnings),
    }
