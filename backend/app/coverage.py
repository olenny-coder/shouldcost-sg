"""Section-by-section coverage of the published index data.

The benchmark can only re-price a measurement section when a published index that
measures that section's cost drivers is loaded for the market. This module answers
the reviewer's question directly: for every canonical section, which published
series actually covers it, and where is the gap?

Two kinds of gap are reported, and they are not the same thing:

  * NO SERIES AT ALL - nothing loaded re-prices the section, so the benchmark
    holds it at base year. This is fixable by wiring another published series.
  * NO LABOUR TERM - every price index on earth measures MATERIALS, FUEL or
    UTILITIES. None of them measures site labour, which is 25-40% of a building
    rate. A commodity index therefore under-states movement in a labour-heavy
    section even where it is "covered". The credible labour references are named
    per market below.

Nothing here is estimated. The series list comes from the database; the
publications named as gap-closers are real publications with real URLs, and each
one states whether it is wired in or still a documented gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .classifier import SMM2_SECTIONS, UNCLASSIFIED
from .countries import DEFAULT_COUNTRY, get_country
from .models import BenchmarkRate, PriceSeries


@dataclass(frozen=True)
class GapSource:
    """A real publication that would close a coverage gap, and its status."""

    name: str
    url: str
    what: str
    # "wired"   - already loaded in this database;
    # "partial" - loaded, but covers only part of the section cost;
    # "gap"     - named but not loaded, with the reason it is not.
    status: str


@dataclass(frozen=True)
class SectionCoverage:
    section: str
    producer: list[dict] = field(default_factory=list)
    consumer: list[dict] = field(default_factory=list)
    # True when no loaded series names this section in its scope_sections.
    uncovered: bool = False
    # True when the section is labour-dominated, so no commodity index can ever
    # price it fully. Reported separately from uncovered.
    labour_dominated: bool = False
    note: str = ""

    @property
    def status(self) -> str:
        if self.uncovered:
            return "uncovered"
        if self.producer and self.labour_dominated:
            return "producer_plus_labour_gap"
        if self.producer:
            return "producer_covered"
        if self.consumer and self.labour_dominated:
            return "consumer_plus_labour_gap"
        return "consumer_only"


# --------------------------------------------------------------------------- #
# Sections that are labour- and plant-dominated. A materials index cannot
# capture them, so the gap is structural rather than a missing download.
# --------------------------------------------------------------------------- #
LABOUR_DOMINATED: frozenset[str] = frozenset(
    {"Excavation", "Piling", "Preliminaries", "M&E Containment"}
)

# What each section is MADE OF. Deliberately market-neutral: whether a given market
# publishes a series that covers it is a separate question, answered by the data
# rather than asserted here.
SECTION_NOTES: dict[str, str] = {
    "Excavation": (
        "Plant and labour. The published driver of plant cost is fuel; there is no earthwork "
        "material to index."
    ),
    "Piling": (
        "Pile cages move with steel, but the driving cost is the piling rig and its crew."
    ),
    "Concrete": (
        "Cement, aggregate and water, plus placing labour and pump hire. The materials are "
        "bought in; the labour is not."
    ),
    "Reinforcement": (
        "Steel is bought as cut and bent bar rather than made on site, so this section tracks a "
        "commodity price closely."
    ),
    "Formwork": (
        "Plywood and sawn timber, plus the carpenter gang and however many times the panels are "
        "re-used."
    ),
    "Masonry": "Blocks or bricks and mortar, plus the bricklayer.",
    "Waterproofing": (
        "Sheet membrane, liquid coating or bitumen, plus the applicator. The membrane is a "
        "manufactured product; the application is labour."
    ),
    "Plaster": "Cement, sand, lime and the plasterer.",
    "M&E Containment": (
        "Cable, conduit, tray and switchgear - separately manufactured products - plus the "
        "installation labour."
    ),
    "Preliminaries": (
        "Site power, temporary works timber, site staff, insurance and scaffolding. Mostly "
        "labour and services rather than material."
    ),
    UNCLASSIFIED: (
        "Lines that no classification rule matched. No index can cover a section whose contents "
        "are unknown; classify them first."
    ),
}


def _confidence_rank(value: str) -> int:
    """Mirror of the engine's preference order, so both pick the same row."""
    return {"high": 0, "medium": 1, "low": 2}.get((value or "").lower(), 9)


def _market_note(code: str, section: str, status: str) -> str:
    """One country-specific sentence stating what that status means in practice."""
    if section == UNCLASSIFIED:
        return "Reclassify these lines before relying on the benchmark."
    if status == "uncovered":
        return (
            f"No series loaded for {code} re-prices this section, so the benchmark holds it at "
            f"base year and the variance on those lines is not index-adjusted at all."
        )
    if status == "producer_plus_labour_gap":
        return (
            "Materials and fuel are re-priced by a producer index; the labour content is not "
            "indexed by any publication, so the escalation shown is a floor rather than a full "
            "cost movement."
        )
    if status == "producer_covered":
        return "Re-priced by a producer index, with no material gap identified for this section."
    if status == "consumer_plus_labour_gap":
        return (
            "No producer basket maps here, so the weaker consumer index is used, and the labour "
            "content is not indexed either."
        )
    return "Re-priced only by a consumer index, which is a proxy for the movement being estimated."


# --------------------------------------------------------------------------- #
# The credible publications named as gap-closers, per market. Names and URLs only:
# no figure from any of these is estimated or invented anywhere in this app.
# --------------------------------------------------------------------------- #
GAP_SOURCES: dict[str, dict[str, tuple[GapSource, ...]]] = {
    "SG": {
        # Applied to any section with no entry of its own, so an uncovered section is
        # never left saying "nothing outstanding".
        "*": (
            GapSource(
                "BCA Tender Price Index / Construction InfoNet",
                "https://www1.bca.gov.sg/",
                "The BCA series is the construction-specific reference for Singapore, but it is "
                "published by building type and elemental group, not by SMM2 section, and the "
                "elemental detail sits behind an InfoNet subscription. Mapping it onto a section "
                "would be an assumption, so it is not wired in.",
                "gap",
            ),
            GapSource(
                "SISV Tender Price Index circulars",
                "https://www.sisv.org.sg/",
                "Quarterly circulars covering the BCA, HDB, AECOM, RLB and Asia Infrastructure "
                "Solutions series. Read by an analyst rather than imported.",
                "gap",
            ),
        ),
        "Excavation": (
            GapSource(
                "SingStat - Domestic Supply Price Index, mineral fuels",
                "https://tablebuilder.singstat.gov.sg/",
                "Singapore producer-side basket. The machine-readable Domestic Supply Price "
                "Index is 1-digit, so it is too coarse to carry a quarterly earthwork index "
                "forward.",
                "gap",
            ),
            GapSource(
                "MOM - Ministry of Manpower wage data",
                "https://www.mom.gov.sg/",
                "The credible reference for construction labour cost in Singapore. Not wired in: "
                "it is published by occupation, not by SMM2 section, so mapping it to a trade "
                "would be an assumption rather than a measurement.",
                "gap",
            ),
        ),
        "Preliminaries": (
            GapSource(
                "BCA Construction InfoNet - material price indices",
                "https://www1.bca.gov.sg/",
                "Publishes cement, steel and ready-mixed concrete price indices intended for "
                "fluctuation clauses. The three headline materials are wired in as monthly "
                "prices; the full InfoNet index set requires a subscription.",
                "partial",
            ),
        ),
        "M&E Containment": (
            GapSource(
                "SISV Tender Price Index circulars",
                "https://www.sisv.org.sg/",
                "The SISV circulars consolidate the BCA, HDB, AECOM, RLB and Asia Infrastructure "
                "Solutions series, each with its own declared scope. They are published as PDF "
                "circulars, so they are read by an analyst rather than imported.",
                "gap",
            ),
        ),
    },
    "IN": {
        "*": (
            GapSource(
                "CPWD Delhi Schedule of Rates (DSR) and CPWD Cost Index",
                "https://cpwd.gov.in/",
                "The de-facto benchmark for Indian building works, and the publication the rate "
                "library's section rates are taken from (DSR Vol-II extract, escalated by CPI to "
                "the quarter the library is stated at).",
                "gap",
            ),
            GapSource(
                "Labour Bureau - Consumer Price Index for Industrial Workers (CPI-IW)",
                "https://www.labourbureau.gov.in/",
                "The wage-escalation index written into Indian construction contracts, and so the "
                "reference for whichever part of a section is labour. Not wired in: it is "
                "published per centre and per base year, and those centres do not map one-to-one "
                "onto the city multipliers used here.",
                "gap",
            ),
        ),
        "Excavation": (
            GapSource(
                "Office of the Economic Adviser - WPI, Petroleum Products",
                "https://eaindustry.nic.in/download_data_2223.asp",
                "Diesel, the direct running cost of excavation plant. WIRED IN as the PPI-PETRO "
                "series, base 2022-23 = 100, published weight 7.03205.",
                "wired",
            ),
            GapSource(
                "Labour Bureau - Consumer Price Index for Industrial Workers (CPI-IW)",
                "https://www.labourbureau.gov.in/",
                "The wage-escalation index written into Indian construction contracts, and so the "
                "correct reference for the labour half of an earthwork rate. Not wired in: it is "
                "published per centre and per base year, and those centres do not map one-to-one "
                "onto the city multipliers used here.",
                "gap",
            ),
        ),
        "Piling": (
            GapSource(
                "CPWD Delhi Schedule of Rates (DSR), piling chapter",
                "https://cpwd.gov.in/",
                "Carries the rig and crew rates for bored and driven piles, and is the source of "
                "the Piling rate in the library. A schedule of rates rather than an index, so the "
                "index bridge is what carries it to the tender quarter.",
                "gap",
            ),
        ),
        "Preliminaries": (
            GapSource(
                "Office of the Economic Adviser - WPI, Electricity",
                "https://eaindustry.nic.in/download_data_2223.asp",
                "Site power. WIRED IN as PPI-ELEC, published weight 4.48719.",
                "wired",
            ),
            GapSource(
                "Labour Bureau - CPI-IW",
                "https://www.labourbureau.gov.in/",
                "Site staff and supervision cost is labour. Same limitation as for Excavation: "
                "centre-level publication, with no direct section mapping.",
                "gap",
            ),
        ),
        "M&E Containment": (
            GapSource(
                "Office of the Economic Adviser - WPI, Electrical Cables and Wires",
                "https://eaindustry.nic.in/download_data_2223.asp",
                "WIRED IN as PPI-CABLE (weight 0.48862), together with PPI-ELECIND Electrical "
                "Industrial Machinery (1.18549) and PPI-ELECOTH Other Electrical Machinery "
                "(0.85762) for switchgear, boards and wiring accessories.",
                "wired",
            ),
        ),
        "Concrete": (
            GapSource(
                "Office of the Economic Adviser - WPI, Other Non Metallic Minerals",
                "https://eaindustry.nic.in/download_data_2223.asp",
                "Sand, gravel and crushed stone - the aggregate that is most of a concrete rate. "
                "WIRED IN as PPI-AGG (weight 1.08834) and PPI-LIME Limestone (0.14374).",
                "wired",
            ),
        ),
    },
}


def _series_card(rows: list[PriceSeries]) -> dict:
    """One card per series, carrying the mapping rationale the CSV recorded."""
    head = rows[0]
    return {
        "series_name": head.series_name,
        "kind": head.kind,
        "title": head.title,
        "scope_sections": head.scope_sections,
        "base_year": head.base_year,
        "currency": head.currency,
        "source_url": head.source_url,
        "provenance_note": head.provenance_note,
        "is_placeholder": any(r.is_placeholder for r in rows),
        "first_month": rows[0].month,
        "last_month": rows[-1].month,
        "observations": len(rows),
    }


def build_coverage(session: Session, country: str | None = None) -> dict:
    """Section-by-section coverage of the loaded price series for one market."""
    code = (country or DEFAULT_COUNTRY).strip().upper()
    registry = get_country(code)

    rows = list(
        session.scalars(
            select(PriceSeries)
            .where(PriceSeries.country == code)
            .order_by(PriceSeries.series_name, PriceSeries.month)
        )
    )

    grouped: dict[str, list[PriceSeries]] = {}
    for row in rows:
        grouped.setdefault(row.series_name, []).append(row)

    def cards_for(section: str, kind: str) -> list[dict]:
        hits = []
        for name in sorted(grouped):
            series_rows = grouped[name]
            if (series_rows[0].kind == "PPI") != (kind == "PPI"):
                continue
            scoped = {
                part.strip()
                for part in (series_rows[0].scope_sections or "").split(";")
                if part.strip()
            }
            if section in scoped:
                hits.append(_series_card(series_rows))
        return hits

    market_gaps = GAP_SOURCES.get(code, {})

    sections = [
        SectionCoverage(
            section=section,
            producer=cards_for(section, "PPI"),
            consumer=cards_for(section, "CPI"),
            uncovered=not cards_for(section, "PPI") and not cards_for(section, "CPI"),
            labour_dominated=section in LABOUR_DOMINATED,
            note=SECTION_NOTES.get(section, ""),
        )
        for section in (*SMM2_SECTIONS, UNCLASSIFIED)
    ]

    # The rate library, per section. This is what actually prices a BoQ, so the coverage
    # table states how each section's rate reaches the quarter being priced: derived from a
    # published schedule of rates at a stated quarter, or a retained estimate still at the
    # index series' own base year.
    rates_by_section: dict[str, BenchmarkRate] = {}
    for rate_row in session.scalars(
        select(BenchmarkRate).where(BenchmarkRate.country == code)
    ):
        current = rates_by_section.get(rate_row.smm2_section)
        if current is None or _confidence_rank(rate_row.confidence) < _confidence_rank(
            current.confidence
        ):
            rates_by_section[rate_row.smm2_section] = rate_row

    def rate_card(section: str) -> dict | None:
        row = rates_by_section.get(section)
        if row is None:
            return None
        stated = (row.base_quarter or "").strip()
        return {
            "base_rate": row.base_rate,
            "unit": row.unit,
            "base_year": row.base_year,
            "base_quarter": stated,
            "source": row.source,
            "source_url": row.source_url,
            "confidence": row.confidence,
            "is_placeholder": row.is_placeholder,
            "provenance_note": row.provenance_note,
            # What the app does with it. A rate stated at a quarter is carried forward from
            # that quarter by the index; a retained one is carried from the series' own base
            # year, which is a longer and weaker step.
            "carried_from": stated or str(row.base_year),
        }

    # Which price index the market uses to carry a rate forward, and of what kind.
    if registry.default_ppi_series:
        carry_kind, carry_series = "producer", registry.default_ppi_series
    elif registry.default_cpi_series:
        carry_kind, carry_series = "consumer", registry.default_cpi_series
    else:
        carry_kind, carry_series = "none", ""

    def gaps_for(section_coverage: SectionCoverage) -> list[dict]:
        own = market_gaps.get(section_coverage.section, ())
        # Fall back to the market-wide list rather than reporting nothing to do.
        chosen = own if own else market_gaps.get("*", ())
        return [
            {"name": g.name, "url": g.url, "what": g.what, "status": g.status} for g in chosen
        ]

    gap_sources = {s.section: gaps_for(s) for s in sections}

    return {
        "country": registry.code,
        "country_name": registry.name,
        "classification_standard": registry.measurement_standard,
        "preferred_producer_series": registry.default_ppi_series,
        "preferred_consumer_series": registry.default_cpi_series,
        # How a benchmark rate reaches the quarter being priced, for this market.
        "carry_index_kind": carry_kind,
        "carry_index_series": carry_series,
        "carry_index_note": (
            f"Each section's rate is carried from its stated base quarter to the tender "
            f"quarter by the {carry_series} {carry_kind} price index."
            if carry_series
            else "No price index is configured for this market."
        ),
        "producer_series_count": sum(1 for n in grouped if grouped[n][0].kind == "PPI"),
        "consumer_series_count": sum(1 for n in grouped if grouped[n][0].kind != "PPI"),
        "sections": [
            {
                "section": s.section,
                "status": s.status,
                "uncovered": s.uncovered,
                "labour_dominated": s.labour_dominated,
                "note": s.note,
                "market_note": _market_note(code, s.section, s.status),
                "rate": rate_card(s.section),
                "producer_series": s.producer,
                "consumer_series": s.consumer,
                "gap_sources": gap_sources.get(s.section, []),
            }
            for s in sections
        ],
        "uncovered_sections": [s.section for s in sections if s.uncovered],
        "producer_covered_sections": [s.section for s in sections if s.producer],
        "labour_dominated_sections": [s.section for s in sections if s.labour_dominated],
        "notes": [
            "A producer price index (PPI) measures what manufacturers and utilities charge for "
            "the commodity baskets a construction rate is made of, so it is the bridge of first "
            "resort. A consumer price index (CPI) measures what households pay and is used only "
            "when no producer series spans the window.",
            "Every index here is derived to show the trend to date: where the tender quarter has "
            "not been published, the last observation is carried forward along the published "
            "movement of the covering series, and every line it touches is reported as "
            "basis=assumed.",
            "No price index measures site labour. Sections flagged labour-dominated are covered "
            "for their material content only, so their bridged movement under-states their full "
            "cost movement. The credible labour reference for each market is named against those "
            "sections.",
            "Singapore has no usable producer price index: the machine-readable SingStat series "
            "M213461 and M213411 are annual and 1-digit, which is too coarse to carry a quarterly "
            "observation, so the Singapore bridge falls back to the consumer index. That is a "
            "documented gap, not a missing download.",
        ],
    }
