"""Country registry: currency, measurement standard and the published sources
that each country's placeholder seed data stands in for.

Adding a country means adding one entry here plus rows in the four seed CSVs
carrying that country code. No other code change is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    what: str


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    currency: str
    currency_symbol: str
    measurement_standard: str
    measurement_note: str
    default_tpi_series: str
    unit_convention: str
    # The monthly price series used to carry a stale index observation forward to
    # the tender quarter. A PRODUCER price index is preferred - it measures what
    # manufacturers charge for cement, steel, minerals and power, which is what a
    # construction rate is made of - and the consumer index is the fallback.
    # Empty means that kind is unavailable for the market.
    default_ppi_series: str = ""
    default_cpi_series: str = ""
    sources: tuple[Source, ...] = field(default_factory=tuple)


SINGAPORE = Country(
    code="SG",
    name="Singapore",
    currency="SGD",
    currency_symbol="S$",
    measurement_standard="SMM2",
    measurement_note=(
        "SMM2 - Standard Method of Measurement of Building Works, 2nd Edition. "
        "The industry classification standard used for Singapore building Bills of Quantities."
    ),
    default_tpi_series="BCA",
    # Singapore publishes producer price indices, but the machine-readable ones are
    # annual and 1-digit (SingStat M213461 / M213411) - too coarse to carry a
    # quarterly observation forward. No usable PPI, so the bridge falls back to CPI.
    default_ppi_series="",
    default_cpi_series="CPI-ALL",
    unit_convention="Metric SI. Rates are per m, m2, m3, tonne or lump sum.",
    sources=(
        Source("BCA Tender Price Index (TPI)", "https://www1.bca.gov.sg/",
               "Tender price movement for building works, base 2010 = 100. Excludes piling, "
               "substructure, external works and M&E."),
        Source("SISV Tender Price Index circulars", "https://www.sisv.org.sg/",
               "Consolidates the BCA, HDB, AECOM, RLB and Asia Infrastructure Solutions series. "
               "Each series declares different scope exclusions."),
        Source("BCA Construction InfoNet", "https://www1.bca.gov.sg/",
               "Elemental unit rates and material price indices for fluctuation clauses."),
        Source("SingStat / BCA material price series", "https://www.singstat.gov.sg/",
               "Cement, steel reinforcement and ready-mixed concrete; monthly from Jan 1999."),
        Source("Consumer Price Index (CPI), Singapore Department of Statistics",
               "https://tablebuilder.singstat.gov.sg/table/TS/M213751",
               "USED IN THIS APP: the monthly All Items CPI (2024 = 100), table M213751, is "
               "real data in this database. It is the series used to carry the last BCA tender "
               "price index observation forward to the tender quarter, because the CPI is "
               "published monthly and the TPI only quarterly."),
        Source("RLB Rider's Digest", "https://www.rlb.com/",
               "Building-type rates and TPI series for Singapore."),
        Source("Arcadis Quarterly Cost Review", "https://www.arcadis.com/",
               "Building-type rates and tender price movement."),
    ),
)

INDIA = Country(
    code="IN",
    name="India",
    currency="INR",
    currency_symbol="\u20b9",
    measurement_standard="IS 1200 / CPWD DSR",
    measurement_note=(
        "IS 1200 - Indian Standard, Methods of Measurement of Building and Civil Engineering "
        "Works (BIS), together with the CPWD Delhi Schedule of Rates (DSR) chapter structure. "
        "The canonical section names are shared with SMM2 because both standards partition "
        "building work the same way; the field is still called smm2_section for backward "
        "compatibility of the API."
    ),
    default_tpi_series="WPI-CONST",
    # India publishes a producer price index monthly and at commodity level
    # (Office of the Economic Adviser, base 2022-23). CPI-ALL is the fallback.
    default_ppi_series="PPI-ALL",
    default_cpi_series="CPI-ALL",
    unit_convention="Metric SI. Rates are per m, m2, m3, tonne or lump sum, in Indian Rupees.",
    sources=(
        Source("CPWD Cost Index", "https://cpwd.gov.in/",
               "Central Public Works Department construction cost index, used with DSR and "
               "Plinth Area Rates (PAR) to escalate estimates to current price levels."),
        Source("CPWD Delhi Schedule of Rates (DSR)", "https://cpwd.gov.in/",
               "The de-facto benchmark schedule of rates for building works in India. "
               "Revised annually (e.g. DSR 2023)."),
        Source("Wholesale Price Index (WPI), Office of the Economic Adviser",
               "https://eaindustry.nic.in/download_data_2223.asp",
               "DPIIT, Ministry of Commerce and Industry. Monthly WPI, base 2022-23 = 100, "
               "downloadable as XLSX. USED IN THIS APP: the WPI-CEM, WPI-STL, WPI-CEM-OPC, "
               "WPI-STL-BARS and WPI-RMC series are real values from this file, averaged to "
               "calendar quarters. The standard reference for escalation and fluctuation "
               "clauses in Indian contracts."),
        Source("National Buildings Organisation (NBO)", "https://nbo.gov.in/",
               "Ministry of Housing and Urban Affairs. Publishes building cost indices for "
               "major Indian cities. This is the intended source for the per-city regional "
               "multipliers, which are placeholders until those circulars are licensed."),
        Source("Ministry of Statistics and Programme Implementation (MoSPI)",
               "https://www.mospi.gov.in/",
               "National statistical series supporting price and construction output data. "
               "USED IN THIS APP: the monthly Consumer Price Index (Combined, All-India General) is "
               "real data in this database and is the series used to carry the last WPI quarter "
               "forward to the tender quarter. MoSPI publishes it on base 2024 = 100, together with "
               "its own back-cast months on that base, and on the predecessor base 2012 = 100. "
               "There is no 2016 = 100 retail CPI in India - that base belongs to the Labour "
               "Bureau's CPI-IW, a different basket."),
        Source("Bureau of Indian Standards (BIS) - IS 1200", "https://www.bis.gov.in/",
               "Publisher of IS 1200 (Methods of Measurement of Building and Civil Engineering "
               "Works) and of the IS 456 concrete grades (M20, M25, M30 ...) used in Indian Bills "
               "of Quantities."),
        Source("Reserve Bank of India - Handbook of Statistics", "https://www.rbi.org.in/",
               "Consolidated macro and price statistics for the Indian economy."),
    ),
)

COUNTRIES: dict[str, Country] = {SINGAPORE.code: SINGAPORE, INDIA.code: INDIA}

DEFAULT_COUNTRY = SINGAPORE.code


class UnknownCountryError(ValueError):
    """Raised when a country code is not registered."""


def get_country(code: str | None) -> Country:
    key = (code or DEFAULT_COUNTRY).strip().upper()
    country = COUNTRIES.get(key)
    if country is None:
        raise UnknownCountryError(
            f"Unknown country code {code!r}. Supported countries: {', '.join(sorted(COUNTRIES))}."
        )
    return country


def country_codes() -> list[str]:
    return sorted(COUNTRIES)


def registry_as_dicts() -> list[dict]:
    """Serialisable registry for the API and the UI."""
    out = []
    for country in COUNTRIES.values():
        out.append(
            {
                "code": country.code,
                "name": country.name,
                "currency": country.currency,
                "currency_symbol": country.currency_symbol,
                "measurement_standard": country.measurement_standard,
                "measurement_note": country.measurement_note,
                "default_tpi_series": country.default_tpi_series,
                "default_cpi_series": country.default_cpi_series,
                "default_ppi_series": country.default_ppi_series,
                "unit_convention": country.unit_convention,
                "sources": [
                    {"name": s.name, "url": s.url, "what": s.what} for s in country.sources
                ],
            }
        )
    return out
