"""Verify that the seeded reference data still matches its published source.

    python tools/verify_seed_data.py

Run this after refreshing any download in ../.realdata/. It re-derives each seeded
series from the publisher's own file and diffs it against what is committed, so
"the data is up to date" is a checked claim rather than an assertion. It writes
nothing, and exits non-zero if anything drifts.

What it checks:

* **Singapore CPI** (`backend/data/cpi_series.csv`) against SingStat table M213751,
  row "All Items" - the series behind the index bridge.
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
import json
import re
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "backend" / "data"
REALDATA = REPO.parent / ".realdata"

SINGSTAT_CPI = REALDATA / "singstat_M213751_cpi.json"
SINGSTAT_MATERIALS = REALDATA / "singstat_M211671_fresh.json"
WPI_WORKBOOK = REALDATA / "wpi_monthly_2223.xlsx"

CPI_CSV = DATA / "cpi_series.csv"
MATERIALS_CSV = DATA / "material_prices.csv"
TPI_CSV = DATA / "tpi_series.csv"

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


def main() -> int:
    print(f"Verifying seeded reference data against {REALDATA}\n")
    check_cpi()
    check_india_cpi()
    check_materials()
    check_wpi()
    print(f"\n{checks} published value(s) checked; {len(problems)} problem(s).")
    if problems:
        print("The seed data has drifted from its source. Refresh it before trusting the app.")
        return 1
    print("Every seeded value re-derives from the publisher's own file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
