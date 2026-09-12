"""Build the two demonstration BoQs from REAL schedule-of-rates lines.

    python tools/build_sample_boqs.py

WHY
---
The demonstration bills used to be invented descriptions with invented rates, calibrated
so the variance report looked interesting. Now that the rate library is derived from the
BCA and CPWD schedules, the bills can be built from the schedules too: the descriptions,
the units and the tendered rates are all real published lines, so the demonstration shows
the product working on the vocabulary it will actually meet.

WHAT IS REAL AND WHAT IS NOT
----------------------------
  description  VERBATIM from the schedule of rates. Nothing is paraphrased.
  unit         from the schedule, normalised to the library's unit (sqm -> m2, kg -> tonne).
  rate         the schedule's own 2026 rate for THAT item, cumulative-adjusted by the
               publisher-file factor. Not a section median, not a target variance.
  quantity     INDICATIVE. A schedule of rates has no quantities; a bill needs them. They
               are the only invented field, and they are chosen to look like a real
               building rather than to move the answer.
  section      whatever the app's classifier assigns. Any candidate where the classifier
               disagrees with the schedule's own part is DROPPED, so the demonstration
               never shows a misclassification on every page.

The consequence worth knowing: the benchmark is a SECTION rate (the median of the section's
lines) while a bill line is a SPECIFIC item, so perfect agreement is not the expectation.
The spread between the two is the real spread, which is the point of the variance report.

Two unclassifiable lines and one unit mismatch are included on purpose: they exercise the
re-pricing and exclusion paths, and they are real schedule lines outside the canonical ten
sections (glazing, painting, metalwork) rather than invented filler.
"""

from __future__ import annotations

import csv
import io
import os
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The default SQLite URL is relative, so the process must run from backend/ or it silently
# opens a different, empty database.
os.chdir(REPO / "backend")
sys.path.insert(0, str(REPO / "backend"))

from app.classifier import UNCLASSIFIED, classify  # noqa: E402

SOR_DIR = REPO.parent / "SOR data"
DATA = REPO / "backend" / "data"
HEADER = ["sor_code", "description", "unit", "quantity", "rate", "rate_basis",
          "is_placeholder", "replace_with"]

# Same mapping as the rate library, so a bill line is always tested against the rate that
# was derived from the same part of the same schedule.
# The schedules' unit vocabulary against the library's.
UNIT_ALIASES = {"sqm": "m2", "cum": "m3", "metre": "m", "each": "item", "no": "item"}

EXCLUDE = ("extra over", "extra for", "deduct", "less ", "dismantl", "demolish",
           "repair", "repainting", "painting", "cleaning", "raking out",
           "pointing on", "cutting holes", "grinding")

SG_RULES = {
    "Excavation": ({"II"}, "m3", None),
    "Concrete": ({"III"}, "m3", {"Lean/Mass Concrete", "Reinforced Concrete", "Green Concrete"}),
    # The schedule measures bar reinforcement in kg; the library states it in tonnes. The
    # rate is converted rather than the bill being written in a unit the library lacks.
    "Reinforcement": ({"III"}, "tonne", {"Bar reinforcement"}),
    "Formwork": ({"III"}, "m2", {"Timber Formwork", "Metal Formwork"}),
    "Masonry": ({"V"}, "m2", {"Clay Bricks", "Concrete Blocks"}),
    "Waterproofing": ({"V"}, "m2", {"Damp Proof Membrane",
        "Waterproofing System to Ground Slab / Basement",
        "Waterproofing System to Interior/Exterior Wet Areas",
        "Waterproofing System to Water-retaining Structure"}),
    "Plaster": ({"XII"}, "m2", None),
}
IN_RULES = {
    "Piling": ({"20"}, "m", None),
    "Waterproofing": ({"22"}, "m2", None),
    "Plaster": ({"13"}, "m2", None),
    "M&E Containment": ({"17", "18", "19", "23"}, "m", None),
}

# Which quantile of each section's rate range to take the demonstration lines from. Spread
# across the range on purpose: a bill of nothing but median-priced items would show no
# variance at all. The extremes are avoided, though - the India M&E range runs from a
# 15mm CPVC pipe to a 1000mm ductile iron main, and quoting the top of that range in a
# demonstration bill says more about the DSR's breadth than about the app. The spread that
# remains is the real item-versus-section-median spread, which is what the report shows.
PICKS = {
    "SG": {
        "Excavation": [0.2, 0.5, 0.8], "Concrete": [0.15, 0.85], "Reinforcement": [0.5],
        "Formwork": [0.2, 0.5, 0.8], "Masonry": [0.25, 0.55, 0.85],
        "Waterproofing": [0.2, 0.5, 0.8], "Plaster": [0.3, 0.6, 0.9],
    },
    "IN": {
        "Piling": [0.25, 0.5, 0.75], "Plaster": [0.2, 0.45, 0.7, 0.85],
        "Waterproofing": [0.3, 0.55, 0.8],
        "M&E Containment": [0.3, 0.5, 0.7],
    },
}
QUANTITIES = {
    "m3": [1250, 640, 980, 720, 430], "m2": [5200, 3850, 2400, 1650, 1450],
    "tonne": [72, 96, 64, 118], "m": [2800, 520, 180, 320],
}

# Sections whose rate in the library is a RETAINED ESTIMATE, not derived from a schedule of
# rates: the loaded extracts do not reach them (DSR Vol-II has no earthwork, concrete,
# reinforcement or formwork; the BCA extract has no piling and no M&E; neither carries a
# general-requirements schedule). There is therefore no published line to quote, so the
# bill line is a demonstration line too, priced at a stated variance to the retained
# benchmark. The provenance of the description matches the provenance of the rate, which is
# the point: a real description against an invented rate would claim more than is known.
#
# Preliminaries is retained in both markets at the user's instruction, so it is here too.
RETAINED_LINES = {
    "SG": [
        ("Preliminaries including site setup, temporary works and insurance", "item", 1, -12),
        ("Bored piling 600mm diameter, including casing and temporary casing", "m", 320, -8),
        ("PVC conduit and trunking containment to electrical services", "m", 3600, 7),
        ("Reinforcement to bored piles, cut and bent, fixed", "tonne", 78, 16),
    ],
    "IN": [
        ("Preliminaries, site establishment, work charged establishment and insurance",
         "item", 1, -10),
        ("Earthwork in excavation in foundation trenches up to 1.5m depth, including disposal",
         "m3", 1250, 16),
        ("Reinforced cement concrete M25 in pile caps and raft foundation", "m3", 540, -24),
        ("TMT reinforcement bars Fe500D, cutting, bending and fixing", "tonne", 72, 18),
        ("Shuttering and formwork to soffits of suspended slabs", "m2", 3850, -19),
        ("Brickwork in cement mortar 1:6 in superstructure, 230mm thick", "m2", 1650, -17),
    ],
}

SOR_FILES = {"SG": "Singapore SOR.csv", "IN": "India SOR.csv"}
SERIES = {"SG": "BCA Schedule of Rates, May 2022", "IN": "CPWD Delhi Schedule of Rates 2021 Vol-II"}
# The index each market benchmarks against, for the retained-line rate.
SERIES_SERIES = {"SG": "BCA", "IN": "CPWD"}
REGION = {"SG": "SGP", "IN": "DEL"}
TENDER_QUARTER = "2026Q2"
FACTOR = {"SG": "1.171", "IN": "1.2364"}


def load(name: str) -> list[dict]:
    lines = (SOR_DIR / name).read_text(encoding="utf-8", errors="replace").splitlines()
    return list(csv.DictReader(io.StringIO("\n".join(l for l in lines if not l.startswith("#")))))


def label_of(desc: str) -> str:
    return desc.split(":")[0].strip() if ":" in desc else ""


def candidates(rows, rules, code_of, unit_of, rate_of, country):
    """Every schedule line that maps to a section AND that the classifier agrees on."""
    out: dict[str, list[dict]] = {section: [] for section in rules}
    outside: list[dict] = []
    for row in rows:
        desc = row["Description"].strip()
        # Normalise the schedule's unit FIRST: the schedules write sqm / metre / cum, the
        # library writes m2 / m / m3, and comparing before normalising silently drops every
        # India piling line.
        unit = UNIT_ALIASES.get(unit_of(row).strip(), unit_of(row).strip())
        try:
            rate = float(rate_of(row))
        except (TypeError, ValueError):
            continue
        if rate <= 0 or any(p in desc.lower() for p in EXCLUDE):
            continue
        assigned = classify(desc, country).smm2_section
        matched = None
        for section, (chapters, want_unit, labels) in rules.items():
            if code_of(row) not in chapters:
                continue
            # Reinforcement is the one section where the schedule unit (kg) still differs
            # from the library unit (tonne); the rate is converted below.
            if want_unit == "tonne":
                if unit != "kg":
                    continue
            elif unit != want_unit:
                continue
            if labels is not None and label_of(desc) not in labels:
                continue
            if country == "IN" and section == "Plaster" and "plaster" not in desc.lower():
                continue
            matched = section
            break

        if matched is None:
            if assigned == UNCLASSIFIED:
                outside.append({"desc": desc, "unit": unit, "rate": rate, "assigned": assigned})
            continue
        if assigned != matched:
            continue  # the app would classify this line elsewhere, so it is not a fair demo
        rate_out = rate * 1000.0 if (unit == "kg" and rules[matched][1] == "tonne") else rate
        out[matched].append(
            {"desc": desc, "unit": rules[matched][1], "rate": rate_out, "assigned": assigned,
             "code": code_of(row)}
        )
    for section in out:
        out[section].sort(key=lambda i: i["rate"])
    return out, outside


def engine_rate(session, country: str, section: str, unit: str, rates, tpi, region_factor) -> float:
    """The rate the engine will actually use for a retained section, at the demo quarter.

    Computed by calling the engine, not by copying a number, so the demonstration line
    cannot drift away from what the app does. Scope-excluded sections are held at the
    library level by the engine, so that is applied here too.
    """
    from app import benchmark

    row = rates[section]
    stated = (row.base_quarter or "").strip()
    if stated:
        resolved = benchmark.resolve_tpi(
            session, SERIES_SERIES[country], stated, country=country, bridge="auto"
        )
        ratio = tpi.value / resolved.value
    else:
        ratio = tpi.value / tpi.base_value
    adjusted = row.base_rate * region_factor * ratio
    if section in benchmark.excluded_sections(tpi.scope_exclusions, {section}):
        adjusted /= ratio
    return adjusted


def at_quantile(items: list[dict], q: float) -> dict:
    if len(items) == 1:
        return items[0]
    index = min(len(items) - 1, max(0, round(q * (len(items) - 1))))
    return items[index]


def retained_lines(country: str, session) -> list[dict]:
    """One demonstration line per section whose library rate is a retained estimate."""
    from app import benchmark

    tpi = benchmark.resolve_tpi(
        session, SERIES_SERIES[country], TENDER_QUARTER, country=country, bridge="auto"
    )
    rates = benchmark.load_benchmark_rates(session, country=country)
    region_factor = benchmark.resolve_region(session, country, REGION[country]).factor

    out = []
    for description, unit, quantity, target in RETAINED_LINES[country]:
        section = classify(description, country).smm2_section
        if section not in rates:
            raise SystemExit(f"{country}: no library rate for the retained line {section!r}")
        if rates[section].unit != unit:
            raise SystemExit(
                f"{country}: retained line unit {unit!r} does not match the library unit "
                f"{rates[section].unit!r} for {section}"
            )
        base = engine_rate(session, country, section, unit, rates, tpi, region_factor)
        rate = base * (1.0 + target / 100.0)
        out.append({
            # Deliberately blank: these sections have no line in the loaded schedule, which is
            # exactly what sor_code blank means. The app reports them as needing input, and
            # that is the honest signal rather than a code borrowed from another trade.
            "sor_code": "",
            "description": description,
            "unit": unit,
            "quantity": quantity,
            "rate": round(rate, 2) if rate < 1000 else round(rate / 10.0) * 10,
            "rate_basis": f"retained benchmark x {target:+d}% (demonstration line)",
            "is_placeholder": "true",
            "replace_with": (
                "# TODO: demonstration line. The rate library has only a RETAINED estimate for "
                f"{section} in this market - the loaded schedule-of-rates extract does not cover "
                "it - so there is no published line to quote. Replace both the rate and this "
                "description from a licensed schedule when one is available."
            ),
        })
    return out


def build(country: str, session) -> list[dict]:
    rows = load(SOR_FILES[country])
    if country == "SG":
        rules = SG_RULES
        code_of = lambda r: r["Code"].split(".")[0].strip()
        unit_of = lambda r: r["Unit"]
        rate_of = lambda r: r["Estimated_Rate_2026_SGD"]
        currency = "SGD"
    else:
        rules = IN_RULES
        code_of = lambda r: r["Code No."].split(".")[0].strip()
        unit_of = lambda r: r["Unit"]
        rate_of = lambda r: r["Estimated_Rate_2026_INR"]
        currency = "INR"

    cand, outside = candidates(rows, rules, code_of, unit_of, rate_of, country)
    out: list[dict] = []
    used_quantities: dict[str, int] = {}

    for section, quantiles in PICKS[country].items():
        items = cand.get(section) or []
        if not items:
            raise SystemExit(f"{country} {section}: no schedule line survived the mapping")
        for q in quantiles:
            item = at_quantile(items, q)
            unit = item["unit"]
            pool = QUANTITIES.get(unit, [100, 250, 500])
            used_quantities[unit] = used_quantities.get(unit, 0)
            quantity = pool[used_quantities[unit] % len(pool)]
            used_quantities[unit] += 1
            out.append({
                "sor_code": item.get("code", ""),
                "description": item["desc"][:400],
                "unit": unit,
                "quantity": quantity,
                "rate": item["rate"],
                "rate_basis": f"{SERIES[country]} x {FACTOR[country]} to 2026",
                "is_placeholder": "true",
                "replace_with": (
                    "# TODO: replace with actual measured BoQ lines and tender rates from a real "
                    f"{'Singapore' if country == 'SG' else 'Indian'} tender. The descriptions and "
                    f"rates here ARE real schedule-of-rates lines ({SERIES[country]}); the "
                    f"quantities are indicative, because a schedule of rates carries none."
                ),
            })

    # TWO lines the canonical vocabulary does not cover - both real schedule lines outside
    # the ten sections (glazing, painting, metalwork, woodwork) rather than invented filler.
    # They exercise the reclassify-or-supply-a-rate path.
    for q in (0.3, 0.75):
        if not outside:
            break
        pick = at_quantile(outside, q)
        out.append({
            "sor_code": pick.get("code", ""),
            "description": pick["desc"][:400], "unit": pick["unit"], "quantity": 180,
            "rate": pick["rate"], "rate_basis": f"{SERIES[country]} x {FACTOR[country]} to 2026",
            "is_placeholder": "true",
            "replace_with": (
                "# TODO: real schedule line outside the ten canonical sections. Reclassify it "
                "from the Variance table, or supply a manual rate, to bring it into the test."
            ),
        })

    # ONE line whose unit differs from the library rate for its section, so the exclusion
    # path is visible. Found in the schedule rather than contrived: the BCA schedule
    # measures formwork to edges per metre while the library states formwork per m2, which
    # is a genuine reason a real bill will not match the library unit.
    mismatch = None
    for section, (chapters, want_unit, labels) in (
        SG_RULES.items() if country == "SG" else IN_RULES.items()
    ):
        for row in rows:
            if code_of(row) not in chapters:
                continue
            raw_unit = unit_of(row).strip()
            normalised = UNIT_ALIASES.get(raw_unit, raw_unit)
            if normalised == want_unit or normalised == "kg":
                continue
            desc = row["Description"].strip()
            if any(p in desc.lower() for p in EXCLUDE):
                continue
            try:
                rate = float(rate_of(row))
            except (TypeError, ValueError):
                continue
            if rate <= 0 or label_of(desc) not in (labels or {label_of(desc)}):
                continue
            if classify(desc, country).smm2_section != section:
                continue
            mismatch = {"desc": desc, "unit": raw_unit, "rate": rate}
            break
        if mismatch:
            break
    if mismatch:
        out.append({
            "sor_code": mismatch.get("code", ""),
            "description": mismatch["desc"][:400], "unit": mismatch["unit"], "quantity": 220,
            "rate": mismatch["rate"], "rate_basis": f"{SERIES[country]} x {FACTOR[country]} to 2026",
            "is_placeholder": "true",
            "replace_with": (
                "# TODO: the schedule measures this item in a different unit from the library "
                "rate for its section, so the app excludes it from the variance test. Either "
                "re-measure it in the library unit or supply a manual rate."
            ),
        })

    # Sections the schedules do not reach, whose library rate is a retained estimate.
    out.extend(retained_lines(country, session))
    return out


def main() -> int:
    from app.db import get_session_factory, init_db

    init_db()
    session = get_session_factory()()
    try:
        return _main(session)
    finally:
        session.close()


def _main(session) -> int:
    for country, filename in (("SG", "sample_boq.csv"), ("IN", "sample_boq_india.csv")):
        rows = build(country, session)
        path = DATA / filename
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADER, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        currency = "SGD" if country == "SG" else "INR"
        print(f"=== {filename}: {len(rows)} lines from {SERIES[country]} ===")
        for row in rows:
            section = classify(row["description"], country).smm2_section
            print("  {:<17} {:<5} {:>9} {:>12,.2f} {}".format(
                section, row["unit"], row["quantity"], row["rate"], row["description"][:58]))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
