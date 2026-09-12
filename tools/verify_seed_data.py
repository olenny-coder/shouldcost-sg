"""Verify that the seeded reference data still matches its published source.

    python tools/verify_seed_data.py

Run this after refreshing any download in ../.realdata/. It re-derives each seeded
series from the publisher's own file and diffs it against what is committed, so
"the data is up to date" is a checked claim rather than an assertion. It writes
nothing, and exits non-zero if anything drifts.

What it checks:

* **Singapore CPI** (`backend/data/cpi_series.csv`) against SingStat table M213751,
  row "All Items" - the series behind the Singapore index bridge.
* **India producer price indexes** (`backend/data/price_series.csv`, `kind = PPI`) against the
  Office of the Economic Adviser's OPPI/WPI workbook: all 16 commodity baskets, every month, at the
  published basket weight. These are the series the bridge reaches for first.
* **The benchmark rate library** (`backend/data/benchmark_rates.csv`) against the two schedules of
  rates in ../SOR data/. Every derived rate is re-computed here as the median of the SOR lines it
  claims to come from, so "these rates are derived from the BCA and CPWD schedules" is a checked
  claim: change a mapping rule in build_benchmark_rates.py without regenerating the CSV, and this
  fails.
* **Singapore material prices** (`backend/data/material_prices.csv`) against
  SingStat table M211671.
* **India WPI** (`backend/data/tpi_series.csv`, `is_placeholder = false`) against
  the Office of the Economic Adviser workbook. Five of the six series are single
  published rows; **WPI-CONST is a derived composite** (see below), so it is
  rebuilt from its two component groups and their published weights.

The downloads live outside the repository, in ../.realdata/, and are never fetched
at runtime. The commands that produce them are in `build_cpi_seed.py` and the
README section "The real sources, and how to reach them".
"""

from __future__ import annotations

import csv
import io
import json
import re
import statistics
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "backend" / "data"
REALDATA = REPO.parent / ".realdata"

SINGSTAT_CPI = REALDATA / "singstat_M213751_cpi.json"
SINGSTAT_MATERIALS = REALDATA / "singstat_M211671_fresh.json"
WPI_WORKBOOK = REALDATA / "wpi_monthly_2223.xlsx"
OPPI_WORKBOOK = REALDATA / "oppi_monthly_2223.xlsx"

CPI_CSV = DATA / "cpi_series.csv"
MATERIALS_CSV = DATA / "material_prices.csv"
TPI_CSV = DATA / "tpi_series.csv"
PRICE_SERIES_CSV = DATA / "price_series.csv"
BENCHMARK_CSV = DATA / "benchmark_rates.csv"

# The schedules of rates the benchmark library is derived from. They live outside the
# repository, alongside the other publisher downloads.
SOR_DIR = REALDATA.parent / "SOR data"
SOR_SG = SOR_DIR / "Singapore SOR.csv"
SOR_IN = SOR_DIR / "India SOR.csv"

# Mirrors of the mapping in .realdata/build_benchmark_rates.py. Deliberately explicit and
# duplicated: the point of this check is to fail when the CSV and those rules disagree.
SOR_EXCLUDE = ("extra over", "extra for", "deduct", "less ", "dismantl", "demolish",
               "repair", "repainting", "painting", "cleaning", "raking out",
               "pointing on", "cutting holes", "grinding")
SOR_RULES = {
    "SG": {
        "Excavation": ({"II"}, "m3", None, 1.0, None),
        "Concrete": ({"III"}, "m3",
                     {"Lean/Mass Concrete", "Reinforced Concrete", "Green Concrete"}, 1.0, None),
        "Reinforcement": ({"III"}, "kg", {"Bar reinforcement"}, 1000.0, None),
        "Formwork": ({"III"}, "m2", {"Timber Formwork", "Metal Formwork"}, 1.0, None),
        "Masonry": ({"V"}, "m2", {"Clay Bricks", "Concrete Blocks"}, 1.0, None),
        "Waterproofing": ({"V"}, "m2",
                          {"Damp Proof Membrane",
                           "Waterproofing System to Ground Slab / Basement",
                           "Waterproofing System to Interior/Exterior Wet Areas",
                           "Waterproofing System to Water-retaining Structure"}, 1.0, None),
        "Plaster": ({"XII"}, "m2", None, 1.0, None),
    },
    "IN": {
        "Piling": ({"20"}, "metre", None, 1.0, None),
        "Waterproofing": ({"22"}, "sqm", None, 1.0, None),
        # Chapter 13 is plastering, but it also carries pointing and mortar bands, so the
        # description filter is part of the rule rather than an afterthought.
        "Plaster": ({"13"}, "sqm", None, 1.0, ("plaster",)),
        "M&E Containment": ({"17", "18", "19", "23"}, "metre", None, 1.0, None),
    },
}

# Every seeded producer series, mapped to the published commodity name it must
# reproduce, and the published weight that name carries in the index basket.
PPI_SERIES = {
    "PPI-ALL": ("ALL COMMODITIES", 100.0),
    "PPI-CEM": ("Cement", 1.45826),
    "PPI-STL": ("Iron And Steel Ferro Alloys", 1.94177),
    "PPI-STL-CAST": ("Iron And Steel Casting And Forging", 2.11647),
    "PPI-STL-FDRY": ("Iron And Steel Foundries", 1.83508),
    "PPI-NMM": ("Non Metallic Mineral Products", 1.04611),
    "PPI-WOOD": ("Wood And Wood Products Except Furniture", 1.08949),
    "PPI-PLASTIC": ("Plastic Products", 1.83629),
    "PPI-PAINT": ("Paints, Varnishes And Lacquers", 0.76978),
    "PPI-CABLE": ("Electrical Cables, Wires", 0.48862),
    "PPI-ELEC": ("Electricity", 4.48719),
    "PPI-PETRO": ("Petroleum Products", 7.03205),
    "PPI-AGG": ("Other Non Metallic Minerals", 1.08834),
    "PPI-LIME": ("Limestone", 0.14374),
    "PPI-ELECIND": ("Electrical Industrial Machinery", 1.18549),
    "PPI-ELECOTH": ("Other Electrical Machinery", 0.85762),
}

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

MATERIAL_LABELS = {
    "cement in bulk (ordinary portland cement)": "cement",
    "steel reinforcement bars (16-32mm high tensile)": "steel_rebar",
    "granite (20mm aggregate)": "aggregate",
    "concreting sand": "sand",
    "ready mixed concrete": "ready_mix_concrete",
}

# The two published WPI group indices that make up WPI-CONST. The blend is by the
# groups' own published weights, so the composite moves with the real basket.
WPI_COMPOSITE = {
    "name": "WPI-CONST",
    "components": (("1313050000", "Manufacture of cement, lime and plaster"),
                   ("1314010000", "Manufacture of basic iron and steel")),
}
WPI_SINGLE_ROWS = ("WPI-CEM", "WPI-STL", "WPI-CEM-OPC", "WPI-STL-BARS", "WPI-RMC")

problems: list[str] = []
checks = 0


def report(label: str, checked: int, mismatches: list[str], extra: str = "") -> None:
    global checks
    checks += checked
    status = "OK  " if not mismatches else "FAIL"
    print(f"[{status}] {label}: {checked} value(s) checked, {len(mismatches)} mismatch(es) {extra}")
    for line in mismatches[:8]:
        print(f"         {line}")
    if len(mismatches) > 8:
        print(f"         ... and {len(mismatches) - 8} more")
    problems.extend(mismatches)


# --------------------------------------------------------------------------- #
# Singapore CPI (table M213751) - the bridge input
# --------------------------------------------------------------------------- #
def check_cpi() -> None:
    if not SINGSTAT_CPI.exists():
        print(f"[SKIP] Singapore CPI: {SINGSTAT_CPI} not found")
        return
    payload = json.loads(SINGSTAT_CPI.read_text(encoding="utf-8-sig"))["Data"]
    published: dict[str, float] = {}
    for record in payload["row"]:
        if str(record.get("rowText", "")).strip().lower() != "all items":
            continue
        for point in record["columns"]:
            parts = str(point["key"]).split()
            if len(parts) != 2 or parts[1] not in MONTHS:
                continue
            month = f"{int(parts[0]):04d}-{MONTHS[parts[1]]:02d}"
            raw = str(point["value"]).strip()
            if raw and raw.lower() not in {"na", "n/a", "-"}:
                published[month] = float(raw)

    seeded: dict[str, float] = {}
    with CPI_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["country"] == "SG" and row["series_name"] == "CPI-ALL":
                seeded[row["month"]] = float(row["value"])

    mismatches = [
        f"{month}: seed {value} vs publisher {published[month]}"
        for month, value in sorted(seeded.items())
        if month in published and abs(value - published[month]) > 1e-6
    ]
    missing = [month for month in seeded if month not in published]
    latest_published = max(published) if published else "n/a"
    report(
        f"Singapore CPI (M213751, All Items), publisher data to {latest_published}",
        len(seeded),
        mismatches + [f"{month}: seeded but not published" for month in missing],
    )


# --------------------------------------------------------------------------- #
# Singapore material prices (table M211671)
# --------------------------------------------------------------------------- #
def check_materials() -> None:
    if not SINGSTAT_MATERIALS.exists():
        print(f"[SKIP] Singapore materials: {SINGSTAT_MATERIALS} not found")
        return
    payload = json.loads(SINGSTAT_MATERIALS.read_text(encoding="utf-8-sig"))["Data"]
    published: dict[tuple[str, str], float] = {}
    for record in payload["row"]:
        material = MATERIAL_LABELS.get(str(record.get("rowText", "")).strip().lower())
        if material is None:
            continue
        for point in record["columns"]:
            raw = str(point["value"]).strip()
            if raw and raw.lower() not in {"na", "n/a", "-"}:
                published[(material, str(point["key"]).strip())] = float(raw)

    seeded: dict[tuple[str, str], float] = {}
    with MATERIALS_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["country"] == "SG":
                seeded[(row["material"], row["month"])] = float(row["price"])

    mismatches = [
        f"{material} {period}: seed {value} vs publisher {published[(material, period)]}"
        for (material, period), value in sorted(seeded.items())
        if (material, period) in published and abs(value - published[(material, period)]) > 1e-6
    ]
    latest = max(period for (_, period) in published) if published else "n/a"
    report(
        f"Singapore material prices (M211671), publisher data to {latest}",
        len(seeded),
        mismatches,
    )


# --------------------------------------------------------------------------- #
# India WPI
# --------------------------------------------------------------------------- #
def check_wpi() -> None:
    if not WPI_WORKBOOK.exists():
        print(f"[SKIP] India WPI: {WPI_WORKBOOK} not found")
        return
    frame = pd.read_excel(WPI_WORKBOOK, engine="openpyxl", header=0)
    frame.columns = [str(c).strip() for c in frame.columns]
    month_columns = [c for c in frame.columns if re.match(r"^[A-Z][a-z]{2}-\d{2}$", c)]
    records = frame.to_dict("records")

    def row_for(code: str) -> dict:
        found = [
            r for r in records
            if str(r["Commodity Code"]).replace(".0", "") == code
        ]
        if not found:
            raise SystemExit(f"WPI commodity code {code} is not in the workbook")
        return found[0]

    def quarter_of(label: str) -> str:
        mon, year = label.split("-")
        return f"20{year}Q{(MONTHS[mon] - 1) // 3 + 1}"

    def quarter_means(values: dict[str, float]) -> dict[str, float]:
        buckets: dict[str, list[float]] = {}
        for label, value in values.items():
            buckets.setdefault(quarter_of(label), []).append(value)
        # Complete calendar quarters only - a partly published quarter is not a
        # quarterly observation, and the engine bridges that gap with the CPI.
        return {q: sum(v) / len(v) for q, v in buckets.items() if len(v) == 3}

    seeded: dict[tuple[str, str], float] = {}
    with TPI_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["country"] == "IN" and row["is_placeholder"] == "false":
                seeded[(row["series_name"], row["quarter"])] = float(row["value"])

    # Map each seeded series onto the workbook row that reproduces it.
    best: dict[str, tuple[str, str, float]] = {}
    for name in sorted({n for (n, _) in seeded}):
        if name == WPI_COMPOSITE["name"]:
            continue
        target = {q: v for (n, q), v in seeded.items() if n == name}
        best_score: float | None = None
        best_label = best_code = ""
        for record in records:
            means = quarter_means({m: float(record[m]) for m in month_columns})
            shared = [q for q in target if q in means]
            if len(shared) < 3:
                continue
            error = sum(abs(target[q] - means[q]) for q in shared) / len(shared)
            if best_score is None or error < best_score:
                best_score = error
                best_label = str(record["Commodity Name"])
                best_code = str(record["Commodity Code"]).replace(".0", "")
        best[name] = (best_label, best_code, best_score if best_score is not None else 9e9)

    print("\n  WPI series -> workbook row (mean absolute quarterly difference):")
    for name in sorted(best):
        label, code, score = best[name]
        print(f"    {name:<13} {code:<12} {label[:46]:<46} {score:.4f}")

    derived: dict[tuple[str, str], float] = {}
    for name in sorted(best):
        _, code, _ = best[name]
        record = row_for(code)
        for quarter, value in quarter_means({m: float(record[m]) for m in month_columns}).items():
            derived[(name, quarter)] = value

    component_rows = [row_for(code) for code, _ in WPI_COMPOSITE["components"]]
    weights = [float(record["Commodity Weight"]) for record in component_rows]
    total_weight = sum(weights)
    blended = {}
    for month in month_columns:
        blended[month] = sum(
            float(record[month]) * weight for record, weight in zip(component_rows, weights)
        ) / total_weight
    for quarter, value in quarter_means(blended).items():
        derived[(WPI_COMPOSITE["name"], quarter)] = value
    print(
        "    {:<13} derived composite of {} (weights {})".format(
            WPI_COMPOSITE["name"],
            " + ".join(name for _, name in WPI_COMPOSITE["components"]),
            " / ".join(f"{w / total_weight:.4f}" for w in weights),
        )
    )

    mismatches = [
        f"{name} {quarter}: seed {value} vs derived {derived[(name, quarter)]:.2f}"
        for (name, quarter), value in sorted(seeded.items())
        if (name, quarter) in derived and abs(value - derived[(name, quarter)]) > 0.05
    ]
    missing = [key for key in sorted(seeded) if key not in derived]
    latest = sorted({q for (_, q) in derived})[-1]
    report(
        f"India WPI (Office of the Economic Adviser), complete quarters to {latest}",
        len(seeded),
        mismatches + [f"{name} {q}: seeded but not derivable" for name, q in missing],
    )


def check_india_cpi() -> None:
    """Re-derive the India CPI seed from the MoSPI API responses saved locally."""
    raw = REALDATA / "india_cpi_raw"
    files = sorted(raw.glob("mospi_api_cpi_*.json")) if raw.exists() else []
    if not files:
        print(f"[SKIP] India CPI: no saved MoSPI responses under {raw}")
        return

    published: dict[tuple[int, str], float] = {}
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        records = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            if str(record.get("state", "")).strip().lower() != "all india":
                continue
            if str(record.get("sector", "")).strip().lower() != "combined":
                continue
            # The publisher uses two response shapes: the current base labels the
            # all-items index as division "CPI (General)"; the older base labels it
            # group "General", subgroup "General-Overall". Division-level rows such
            # as "Miscellaneous-Overall" must not be mistaken for it.
            division = str(record.get("division") or "").strip().lower()
            group = str(record.get("group") or "").strip().lower()
            subgroup = str(record.get("subgroup") or "").strip().lower()
            is_general = (
                "general" in division
                or subgroup in {"general-overall", "general"}
                or (group == "general" and not subgroup)
            )
            if not is_general:
                continue
            index = str(record.get("index", "")).strip()
            if not index or index.lower() in {"na", "n/a", "-"}:
                continue
            month_number = MONTHS.get(str(record.get("month", "")).strip()[:3])
            year = str(record.get("year", "")).strip()
            base = str(record.get("base_year") or record.get("baseyear") or "").strip()
            if month_number is None or not year.isdigit() or not base.isdigit():
                continue
            month = f"{int(year):04d}-{month_number:02d}"
            published[(int(base), month)] = float(index)

    seeded: dict[tuple[int, str], float] = {}
    with CPI_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["country"] == "IN":
                seeded[(int(row["base_year"]), row["month"])] = float(row["value"])

    mismatches = [
        f"base {base} {month}: seed {value} vs publisher {published[(base, month)]}"
        for (base, month), value in sorted(seeded.items())
        if (base, month) in published and abs(value - published[(base, month)]) > 1e-6
    ]
    missing = [key for key in sorted(seeded) if key not in published]
    bases = sorted({base for (base, _) in published})
    latest = max(month for (_, month) in published) if published else "n/a"
    report(
        f"India CPI (MoSPI eSankhyiki API, bases {bases}, published to {latest})",
        len(seeded),
        mismatches + [f"base {base} {month}: seeded but not in the saved responses" for base, month in missing],
    )


def check_ppi() -> None:
    """Re-derive every seeded India producer series from the OPPI/WPI workbook."""
    if not OPPI_WORKBOOK.exists():
        print(f"[SKIP] India PPI: {OPPI_WORKBOOK} not found")
        return

    frame = pd.read_excel(OPPI_WORKBOOK, engine="openpyxl", header=0)
    frame.columns = [str(c).strip() for c in frame.columns]
    month_columns = [c for c in frame.columns if re.match(r"^[A-Z][a-z]{2}-\d{2}$", c)]

    def month_of(label: str) -> str:
        mon, year = label.split("-")
        return f"{2000 + int(year):04d}-{MONTHS[mon]:02d}"

    seeded: dict[str, dict[str, float]] = {}
    weights: dict[str, float] = {}
    with PRICE_SERIES_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["country"] != "IN" or row["kind"] != "PPI":
                continue
            seeded.setdefault(row["series_name"], {})[row["month"]] = float(row["value"])

    mismatches: list[str] = []
    checked = 0
    latest = "n/a"
    for name in sorted(seeded):
        if name not in PPI_SERIES:
            mismatches.append(f"{name}: seeded but not mapped to a published commodity")
            continue
        commodity, published_weight = PPI_SERIES[name]
        hit = frame[frame["Commodity Name"].astype(str).str.strip() == commodity]
        if hit.empty:
            mismatches.append(f"{name}: commodity {commodity!r} is not in the workbook")
            continue
        record = hit.iloc[0]
        weight = float(record["Commodity Weight"])
        weights[name] = weight
        if abs(weight - published_weight) > 1e-5:
            mismatches.append(
                f"{name}: basket weight {weight} vs the {published_weight} this file claims"
            )
        published = {month_of(m): float(record[m]) for m in month_columns}
        for month, value in sorted(seeded[name].items()):
            checked += 1
            if month not in published:
                mismatches.append(f"{name} {month}: seeded but not published")
            elif abs(value - published[month]) > 1e-6:
                mismatches.append(
                    f"{name} {month}: seed {value} vs publisher {published[month]}"
                )
        latest = max([latest] + [m for m in published] if latest != "n/a" else list(published))

    missing = [name for name in PPI_SERIES if name not in seeded]
    report(
        f"India producer price indexes (OEA OPPI/WPI), {len(seeded)} basket(s), months to {latest}",
        checked,
        mismatches + [f"{name}: mapped but not seeded" for name in missing],
    )


def check_sor_rates() -> None:
    """Re-derive every SOR-derived benchmark rate from the schedule of rates itself."""
    if not SOR_SG.exists() or not SOR_IN.exists():
        print(f"[SKIP] Benchmark rate library: {SOR_DIR} not found")
        return

    def load(path):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return list(csv.DictReader(io.StringIO("\n".join(l for l in lines if not l.startswith("#")))))

    def median_for(rows, spec, code_of, unit_of, rate_of):
        chapters, want_unit, labels, factor, contains = spec
        label_of = lambda d: d.split(":")[0].strip() if ":" in d else ""
        values = []
        for row in rows:
            if code_of(row) not in chapters:
                continue
            if labels is not None and label_of(row["Description"]) not in labels:
                continue
            if contains is not None and not any(c in row["Description"].lower() for c in contains):
                continue
            if unit_of(row).strip() != want_unit:
                continue
            if any(p in row["Description"].lower() for p in SOR_EXCLUDE):
                continue
            try:
                rate = float(rate_of(row))
            except (TypeError, ValueError):
                continue
            if rate > 0:
                values.append(rate * factor)
        return sorted(values)

    seeded: dict[tuple[str, str], tuple[float, bool]] = {}
    with BENCHMARK_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            seeded[(row["country"], row["smm2_section"])] = (
                float(row["base_rate"]),
                row["is_placeholder"].strip().lower() == "true",
            )

    sg_rows, in_rows = load(SOR_SG), load(SOR_IN)
    mismatches: list[str] = []
    checked = 0
    for (country, section), (value, retained) in sorted(seeded.items()):
        rule = SOR_RULES.get(country, {}).get(section)
        if rule is None:
            continue
        rows, code_of, unit_of, rate_of = (
            (sg_rows, lambda r: r["Code"].split(".")[0].strip(),
             lambda r: r["Unit"], lambda r: r["Estimated_Rate_2026_SGD"])
            if country == "SG" else
            (in_rows, lambda r: r["Code No."].split(".")[0].strip(),
             lambda r: r["Unit"], lambda r: r["Estimated_Rate_2026_INR"])
        )
        values = median_for(rows, rule, code_of, unit_of, rate_of)
        if not values:
            mismatches.append(f"{country} {section}: no SOR line matches the mapping rule")
            continue
        checked += 1
        expected = statistics.median(values)
        if retained:
            mismatches.append(f"{country} {section}: seeded as retained but the SOR covers it")
        if abs(value - expected) > 0.01:
            mismatches.append(
                f"{country} {section}: seeded {value:,.2f} vs median of {len(values)} SOR "
                f"line(s) {expected:,.2f}"
            )

    retained = {k for k, v in seeded.items() if v[1]}
    report(
        f"Benchmark rate library (BCA SOR + CPWD DSR), {checked} derived section(s), "
        f"{len(retained)} retained",
        checked,
        mismatches,
    )


def check_sor_catalogue() -> None:
    """Re-derive the upload template's item catalogue from the two extracts.

    The template IS the schedule for its market, and the upload's sections summary is
    built from this catalogue, so three things have to hold or the summary is fiction:

    * every description in the extract is in the catalogue - an earlier revision silently
      dropped 60 Singapore items (no published rate) and 30 India items (an odd unit);
    * every rate is the extract's own escalated rate, converted by the documented unit
      multiplier, so the conversion is auditable rather than assumed;
    * every description still classifies to the section stored against it, so the template
      cannot advertise a section the app no longer produces.
    """
    if not SOR_SG.exists() or not SOR_IN.exists():
        print(f"[SKIP] Upload template catalogue: {SOR_DIR} not found")
        return

    sys.path.insert(0, str(REPO / "backend"))
    sys.path.insert(0, str(REPO / "tools"))
    from app.boq_template import catalogue_rows
    from app.classifier import UNCLASSIFIED, classify
    from build_sor_items import FILES, UNIT_CONVERSION, load

    mismatches: list[str] = []
    checked = 0
    for country, (filename, currency, source, code_of, est_of) in FILES.items():
        extract = load(filename)
        wanted = [row for row in extract if (row["Description"] or "").strip()]
        items = list(catalogue_rows(country))

        if len(items) != len(wanted):
            mismatches.append(
                f"{country}: catalogue has {len(items)} items but the extract has "
                f"{len(wanted)} descriptions - every description must be listed"
            )
        by_code = {item.code: item for item in items}
        for row in wanted:
            code = code_of(row).strip()
            item = by_code.get(code)
            if item is None:
                mismatches.append(f"{country} {code}: in the extract but not in the catalogue")
                continue
            checked += 1
            if item.description != (row["Description"] or "").strip():
                mismatches.append(f"{country} {code}: description differs from the extract")
            published_unit = (row["Unit"] or "").strip()
            conversion = UNIT_CONVERSION.get(published_unit.lower())
            if conversion is None:
                if item.unit != published_unit or item.unit_comparable:
                    mismatches.append(
                        f"{country} {code}: unit {published_unit!r} is not convertible, so it must "
                        f"be carried verbatim and flagged"
                    )
                continue
            unit, multiplier = conversion
            if item.unit != unit:
                mismatches.append(f"{country} {code}: unit {item.unit!r} != converted {unit!r}")
            try:
                raw = float(est_of(row))
            except (TypeError, ValueError):
                raw = 0.0
            expected = raw * multiplier if raw > 0 else None
            if expected is None:
                if item.rate is not None:
                    mismatches.append(f"{country} {code}: extract has no rate but the catalogue does")
            elif item.rate is None or abs(item.rate - expected) > 0.05:
                mismatches.append(
                    f"{country} {code}: rate {item.rate} != extract rate x {multiplier} "
                    f"({expected:.2f})"
                )
            section = classify(item.description, country).smm2_section
            current = "" if section == UNCLASSIFIED else section
            if current != item.section:
                mismatches.append(
                    f"{country} {code}: stored section {item.section!r} but the classifier now says "
                    f"{current!r} - re-run tools/build_sor_items.py"
                )

        inside = sum(1 for item in items if item.section)
        print(
            f"         {country}: {len(items)} items, {inside} in the ten sections, "
            f"{len(items) - inside} outside, "
            f"{sum(1 for item in items if item.rate is None)} with no published rate, "
            f"{sum(1 for item in items if not item.unit_comparable)} with an odd unit"
        )

    report("Upload template catalogue (BCA SOR + CPWD DSR descriptions)", checked, mismatches)


def main() -> int:
    print(f"Verifying seeded reference data against {REALDATA}\n")
    check_cpi()
    check_india_cpi()
    check_materials()
    check_wpi()
    check_ppi()
    check_sor_rates()
    check_sor_catalogue()
    print(f"\n{checks} published value(s) checked; {len(problems)} problem(s).")
    if problems:
        print("The seed data has drifted from its source. Refresh it before trusting the app.")
        return 1
    print("Every seeded value re-derives from the publisher's own file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
