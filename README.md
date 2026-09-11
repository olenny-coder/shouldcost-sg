# shouldcost

Should-cost BoQ benchmarking for the **Singapore and India construction markets**. Upload a Bill of
Quantities, get every line classified to a measurement section, re-priced against a benchmark rate
library adjusted by a published cost index, and reported as variance, section subtotals, a
reconciliation **waterfall** and a **sensitivity analysis**.

| Market | Measurement standard | Currency | Index series (all placeholder values) |
|---|---|---|---|
| Singapore | **SMM2** (Standard Method of Measurement, 2nd Ed.) | SGD | BCA, HDB, RLB, AECOM |
| India | **IS 1200** + **CPWD DSR** chapter structure | INR | CPWD, NBO, WPI-CON |

Split stack, designed for managed hosting:

```
  Browser
     |
     v
  Vercel  (static SPA, Vite + React + Recharts)   root dir: frontend/
     |   HTTPS + CORS allowlist
     v
  Render Web Service (FastAPI + uvicorn)          root dir: backend/
     |   DATABASE_URL
     v
  Render PostgreSQL (managed)
```

---

## Environment

Capability probe performed before any code was written (Step 0.3 of the brief). Results are
recorded verbatim here because they govern what was built and what was deliberately skipped.

| # | Question | Answer | Evidence |
|---|---|---|---|
| a | Can packages be installed (network egress)? | **YES** | `Invoke-WebRequest https://pypi.org/simple/` -> `200`; `https://registry.npmjs.org/` -> `200`; `npm install` added 127 packages |
| b | Render account / API key available as an env var? | **NO** | `RENDER_API_KEY`, `RENDER_TOKEN` both absent from the environment |
| c | Vercel token available as an env var? | **NO** | `VERCEL_TOKEN`, `VERCEL_API_TOKEN` both absent from the environment |
| d | Vision available for PDF input? | **NO** | Verified, not assumed: `read_image` fails with *"model deepseek-v4-flash does not declare image input; switch to an image-capable model to read images"*. Screenshots therefore cannot be inspected either. There is additionally no OCR / PDF-table extraction pipeline, and no runtime LLM call is permitted (hard rule 2). **CSV and XLSX only.** PDF BoQ upload is not supported in this build. |
| d2 | Are the Indian index sources genuine? | **YES** | Verified by name and by live URL reachability, not invented: `cpwd.gov.in` (CPWD Cost Index + Delhi Schedule of Rates), `eaindustry.nic.in` (WPI, Office of the Economic Adviser, DPIIT), `nbo.gov.in` (National Buildings Organisation), `mospi.gov.in`, `bis.gov.in` (IS 1200, IS 456), `rbi.org.in`. Reachability was checked with HTTP requests; the **values** are still synthetic placeholders - see "India" below. |
| e | Can work be delegated to sub-agents? | **YES** | `subagent` / `subagent_fork` are available. Not used: the work was strictly sequential (schema -> engine -> API -> UI) and delegating it would have added reconciliation risk without saving wall-clock time. |

> **How the UI was verified.** Because vision is unavailable (row d), the four views were **not**
> verified by looking at screenshots. They were verified by driving the real production bundle in
> headless Chrome and asserting on the rendered DOM. See "Rendered UI verification" under Testing.

**Consequence of (b) and (c):** milestone **M6 (deployment execution) was skipped**. `render.yaml`,
`frontend/vercel.json`, both `.env.example` files and `deploy-runbook.md` were written instead, and
the runbook contains the exact manual steps. See [Deployment](#deployment) below.

### Toolchain actually used

```
Python  3.11.6   (C:/Users/.../Programs/Python/Python311)
Node.js 24.19.0  (installed via winget OpenJS.NodeJS.LTS)
npm     11.17.0
GNU make 4.4.1   (installed via winget ezwinports.make)
git     2.55.0.windows.5
```

`npm` and `make` were **not** on `PATH` at recon time; both were installed as part of setup. If you
are on a machine without GNU make, every `make` target in the table below maps to a one-line
command you can run directly.

---

## Quick start (local)

```bash
git clone <your-repo> shouldcost-sg && cd shouldcost-sg

make install     # pip install -r backend/requirements.txt  +  npm install in frontend/
make seed        # python -m app.etl   (idempotent - safe to run repeatedly)
make test        # pytest, 88 tests

# terminal 1
make dev-backend   # http://localhost:8000  (docs at /docs)

# terminal 2
make dev-frontend  # http://localhost:5173
```

If you do not have GNU make:

| make target | equivalent |
|---|---|
| `make install` | `cd backend && python -m pip install -r requirements.txt` then `cd frontend && npm install` |
| `make seed` | `cd backend && python -m app.etl` |
| `make test` | `cd backend && python -m pytest tests -v` |
| `make build` | `cd frontend && npm run build` |
| `make dev-backend` | `cd backend && python -m uvicorn app.main:app --reload --port 8000` |
| `make dev-frontend` | `cd frontend && npm run dev` |

The SQLite database is created at `backend/shouldcost.db` because the default `DATABASE_URL` is
`sqlite:///./shouldcost.db`, a path relative to the process working directory. **Run the backend
from `backend/`** (every `make` target and the commands above do this for you).

---

## Where the data comes from

Every row in the database declares which of two things it is. There is no third state.

| | `is_placeholder` | Carries | Meaning |
|---|---|---|---|
| **Real** | `false` | `provenance_note` | An actual published observation. The note names the publisher, the series and any transformation. |
| **Synthetic** | `true` | `replace_with` | A stand-in. The marker names the exact value that must replace it. |

Current split in the bundled seed data:

| table | real | total | |
|---|---|---|---|
| `material_prices` | **180** | 180 | 100% real - BCA via SingStat (Singapore prices) and WPI (India cost indices) |
| `tpi_series` | **78** | 126 | India WPI series are real; the Singapore TPI and the CPWD/NBO city indices are not yet licensed |
| `benchmark_rates` | 0 | 20 | CPWD DSR and BCA Construction InfoNet are **sold publications** - no free machine-readable schedule of rates exists |
| `regional_factors` | 0 | 13 | CPWD/NBO publish city indices as circulars and PDFs - not machine-readable here |

### The real sources, and how to reach them

| Source | Publisher | What is used | Where |
|---|---|---|---|
| **Wholesale Price Index**, base 2022-23 = 100 | Office of the Economic Adviser, DPIIT, Ministry of Commerce and Industry | Monthly XLSX of every WPI item and sub-group. Drives **WPI-CONST**, **WPI-CEM**, **WPI-STL** and the item-level **WPI-CEM-OPC**, **WPI-STL-BARS**, **WPI-RMC** series | [eaindustry.nic.in/download_data_2223.asp](https://eaindustry.nic.in/download_data_2223.asp) |
| **Construction Material Market Prices** (table M211671) | **Building and Construction Authority**, published through SingStat Table Builder | Cement, steel reinforcement, granite aggregate, concreting sand and ready-mixed concrete, annual 1999-2025 | [tablebuilder.singstat.gov.sg/table/TS/M211671](https://tablebuilder.singstat.gov.sg/table/TS/M211671) - the API endpoint used is `/api/table/tabledata/M211671` |

Both are freely downloadable. Neither is fetched at runtime: the values are extracted once and
committed as seed data, so the app stays fully offline (hard rule 2).

**What is deliberately still synthetic, and why.** The *indexes* can be real because governments
publish them. The *benchmark unit rates* cannot: the CPWD Delhi Schedule of Rates, the state PWD
schedules and BCA Construction InfoNet are all licensed publications. Shipping a plausible-looking
rupee rate per SMM2 section would be inventing data. Those 20 rows stay placeholders with a named
target, and the app is honest about it on every screen.

### Refreshing with real data

```bash
# 1. Download the publisher's file (see the table above).
# 2. Convert it to the documented column layout - the seed CSVs are the template:
#      backend/data/tpi_series.csv
#      backend/data/material_prices.csv
# 3. Import it. Every row is stamped with the provenance you supply.
cd backend
python -m app.importer --kind tpi --file ../wpi_quarters.csv \
  --source-url "https://eaindustry.nic.in/download_data_2223.asp" \
  --provenance "Wholesale Price Index, base 2022-23 = 100, calendar-quarter mean."
```

`--kind` accepts `tpi`, `materials`, `benchmark_rates` or `regions`. Importing forces
`is_placeholder: false`, stores your provenance note, clears `replace_with`, and upserts on the
natural key - so re-importing a refreshed file is idempotent. **`--provenance` is mandatory**: a
real-looking number with no stated origin is worse than an honest placeholder.

---

## Markets

The country registry lives in `backend/app/countries.py`. Adding a market means one entry there
plus rows carrying that country code in the four seed CSVs - no other code change.

Each market carries its own currency, measurement standard, index series, benchmark rate library and
classifier vocabulary. **Index series are never shared across markets**: asking for `CPWD` against
a Singapore BoQ returns a 400 naming the market, not a silently wrong answer.

### India

Indian BoQs are written in a different vocabulary from Singapore ones. The classifier layers
India-specific patterns on top of the shared rule table, leaving the Singapore rules untouched:

| Section | India adds |
|---|---|
| Concrete | `RCC`, `PCC`, `reinforced cement concrete`, `M25` / `M30` (IS 456 grades) |
| Reinforcement | `TMT`, `tor steel`, `HYSD`, `Fe500`, `Fe415` |
| Formwork | `shuttering` |
| Excavation | `earthwork`, `earth work` |
| Masonry | `AAC block`, `fly ash brick` |
| Plaster | `rendering` |
| Waterproofing | `bituminous`, `APP membrane`, `damp proof` |
| M&E Containment | `casing capping`, `cable tray` |
| Preliminaries | `work charged`, `contingency`, `site establishment` |

Both standards partition building work into the same ten canonical sections, so the section
vocabulary is shared and only the wording differs.

### India is not one market: regional adjusters

India's construction cost varies materially by city. Labour rates follow state minimum-wage
notifications and materials carry different haulage and local-levy costs, so a single national rate
library misprices every project outside the reference city.

Each country therefore carries **regions**, and each region carries a multiplier applied to every
benchmark base rate:

```
regional bench rate = published base rate x region.factor
```

```
IN  DEL  Delhi (NCR)   1.000  <- default, the reference city for the national rate library
IN  MUM  Mumbai        1.128
IN  PUN  Pune          1.034
IN  BLR  Bengaluru     1.022
IN  KOC  Kochi         1.016
IN  CHN  Chennai       0.994
IN  HYD  Hyderabad     0.981
IN  AHM  Ahmedabad     0.972
IN  BHO  Bhopal        0.963
IN  KOL  Kolkata       0.958
IN  LKO  Lucknow       0.948
IN  JAI  Jaipur        0.941
SG  SGP  Singapore     1.000  <- single-region market, so the model is uniform across countries
```

On the seeded India sample at 2024Q4, moving the region alone moves the answer a long way:

| Region | Should-cost | Variance vs the tendered BoQ |
|---|---|---|
| Jaipur (0.941) | INR 2,91,91,807 | **+10.46%** |
| Delhi (1.000) | INR 3,09,75,977 | +4.10% |
| Mumbai (1.128) | INR 3,48,46,720 | **-7.46%** |

Two things about the multipliers are worth being blunt about:

1. **They are placeholders.** CPWD publishes a city-wise cost index and NBO publishes city building
   cost indices, but neither was available in machine-readable form here. Each region's row carries
   the source it should come from and a `# TODO`. A placeholder factor that is not 1.0 raises a
   named warning on every run.
2. **It is one blended factor, not a material/labour split.** Labour-heavy sections (Formwork,
   Plaster, Masonry, Preliminaries) are more regionally variable than material-driven ones
   (Concrete, Reinforcement). The single multiplier is a simplification, and it is stated in
   `assumptions[]` on every affected run.

Because a regional multiplier is a modelling input and not an observation, **every line it touches
is `basis: "assumed"`** - exactly like a manual index adjuster. A factor of exactly 1.0 changes
nothing and leaves the line `derived`.

---

## Formula contract

These four expressions are the contract. They are implemented in `backend/app/benchmark.py` and
asserted in `backend/tests/test_benchmark.py`:

```
adjusted_benchmark_rate = base_rate * (current_index / base_index) * scope_factor
variance_abs            = boq_rate - adjusted_benchmark_rate
variance_pct            = (variance_abs / adjusted_benchmark_rate) * 100
should_cost_amount      = quantity * adjusted_benchmark_rate
```

Two **analyst** multipliers may be layered on top of the published values:

```
current_index = (published_index or tpi_value_override) * (1 + tpi_scale_pct / 100)
base_rate     = published_base_rate * (1 + (base_rate_scale_pct + section_scale_pct) / 100)
```

Any line touched by one of those multipliers is reported with `basis: "assumed"` and flagged
`user_adjusted`. See "Manual index adjusters" below.

`scope_factor` defaults to **1.0**. It is set away from 1.0 **only** when the selected index series
explicitly excludes a section that is present in the BoQ. See below.

---

## Basis model

Every number that is not directly measured is tagged with an explicit `basis` field, in the API
response **and** in the UI:

| basis | meaning |
|---|---|
| `measured` | Taken directly from the uploaded BoQ, or a published observation. |
| `derived` | Calculated from measured inputs using the documented formula. |
| `assumed` | Apportioned, overridden or modelled. **No measured evidence exists.** The justification is rendered visibly. |

A rate an analyst has moved with an index adjuster is `assumed`, not `derived` - its input is no
longer purely an observation.

The UI never hides an `assumed` value in a tooltip. `AssumptionsPanel` renders `warnings[]` and
`assumptions[]` above the fold on every view, and the waterfall colours bars by basis.

---

## Scope factor semantics

Published TPI series do not all measure the same scope. The BCA TPI excludes piling, substructure,
external works and M&E; the RLB series additionally excludes preliminaries. Comparing a BoQ that
contains Piling against an index that does not measure piling is a category error.

When the selected series excludes a section that is present in the BoQ:

```
scope_factor = 1 / tpi_ratio        for that section only
adjusted_benchmark_rate = base_rate * tpi_ratio * (1 / tpi_ratio) = base_rate
```

That is, the excluded section is **held at base year** instead of being re-priced by an index that
does not measure it. This is a defensible, disclosed modelling choice - the brief only required
`scope_factor != 1.0`, without specifying the value - and it is recorded in `assumptions[]`.

A consequence worth knowing: the `scope` waterfall bar exactly cancels the `market_risk` bar for
excluded sections. That identity is asserted by
`test_scope_and_market_risk_cancel_for_excluded_sections`.

The exclusion matcher lives in `EXCLUSION_ALIASES` in `benchmark.py` and maps phrases such as
`Substructure` to `{Piling, Excavation}` and `M&E Services` to `{M&E Containment}`. Adjust it to
match the exact wording of the published series you license.

---

## Data model

```
tpi_series       id, country, series_name, quarter, base_year, base_value, currency, value,
                 scope_inclusions, scope_exclusions, source_url, is_placeholder, replace_with
material_prices  id, country, material, month, unit, price, currency, source_url,
                 is_placeholder, replace_with
benchmark_rates  id, country, smm2_section, classification_standard, description, unit,
                 base_rate, currency, base_year, source, source_url, source_date,
                 scope_inclusions, scope_exclusions, confidence, is_placeholder, replace_with
boq_uploads      id, country, filename, uploaded_at, tender_quarter, tpi_series_name, currency
boq_items        id, upload_id, raw_description, unit, quantity, boq_rate, amount,
                 smm2_section, classified_by, is_placeholder, replace_with
```

Deviations from the originally agreed column sets, each deliberate:

* **`country`** on all four reference tables and on `boq_uploads`, so both markets can coexist in
  one database. Every natural key is scoped by country.
* **`currency`** on every money-bearing table, and the money columns are named `base_rate` and
  `price` rather than `base_rate_sgd` / `price_sgd`. The original names were written for a
  Singapore-only app; leaving an Indian rupee rate in a column called `*_sgd` would be a defect.
* **`base_value`** on `tpi_series` - the index value at `base_year`. It is 100 for a rebased
  index, but stating it explicitly means a series published on another base can be carried without
  silently assuming 100.
* **`classification_standard`** on `benchmark_rates`, so a row declares whether its section comes
  from SMM2 or from IS 1200 / CPWD DSR.
* **`source_url`, `is_placeholder` and `replace_with`**, required by hard rule 1: every synthetic
  value must be traceable to the source it stands in for and the marker that must replace it, and
  that traceability has to survive all the way into the UI.

The column is still called `smm2_section` even though India does not use SMM2. Both standards
partition building work into the same ten sections, and the name is retained for API backward
compatibility; `classification_standard` carries the truth.

---

## Seed data and the placeholder policy

**Every seeded value in this repository is synthetic.** No BCA, SISV or SingStat index value has
been invented and presented as real, and no real value has been copied in. Every row carries:

* `is_placeholder: true`
* a `source_url` naming the real publication it stands in for
* a `replace_with` string beginning `# TODO: replace with actual <source> <period>`

| file | rows | content |
|---|---|---|
| `backend/data/tpi_series.csv` | 126 | **SG (32, placeholder):** BCA, HDB, RLB, AECOM - 8 quarters, base 2010 = 100. **IN (78, REAL):** WPI-CONST, WPI-CEM, WPI-STL, WPI-CEM-OPC, WPI-STL-BARS, WPI-RMC - 13 quarters, 2023Q2-2026Q2, base 2022-23 = 100. **IN (16, placeholder):** CPWD, NBO - the city cost indices |
| `backend/data/material_prices.csv` | 180 | **SG (60, REAL):** cement, steel_rebar, aggregate, sand, ready_mix_concrete - annual 2014-2025, SGD prices. **IN (120, REAL):** cement, steel_rebar, ready_mix_concrete - monthly 2023-04 to 2026-07, INR cost INDEX (2022-23 = 100) |
| `backend/data/benchmark_rates.csv` | 20 | the ten sections per market. SG base year 2010 (SGD), IN base year 2023 (INR). All placeholder |
| `backend/data/regional_factors.csv` | 13 | 12 Indian cities plus Singapore, with the multiplier, its source and its limitations |
| `backend/data/sample_boq.csv` | 20 | Singapore demonstration BoQ, SMM2 wording, SGD |
| `backend/data/sample_boq_india.csv` | 20 | India demonstration BoQ, IS 1200 / DSR wording, INR |

The India index base is the WPI's own **2022-23 = 100**, and the India benchmark rates are stated
at base year 2023. Those line up by construction rather than by fudge: the WPI is 100 across the
2022-23 financial year, and the CPWD DSR 2023 schedule is priced at 2023 levels.

The India WPI series are **real**, derived from the published monthly index by unweighted
calendar-quarter mean. That derivation is recorded in each row's `provenance_note`, so a reader can
reproduce it from the source file without guessing.

A **base-year mismatch guard** exists in the engine: if the benchmark rate base year differs from
the index base year, the response carries a named warning saying the ratio is only valid if the
rate library has been rebased to the index base year. It never fails silently.

Both sample BoQs are engineered so variance reporting is visibly exercised. Against the default
view for each market:

| | Singapore (BCA 2024Q4) | India (CPWD 2024Q4) |
|---|---|---|
| Lines | 20 | 20 |
| Over +15% | 5 | 6 |
| Under -15% | 3 | 4 |
| Unclassified | 2 | 2 |
| Unit mismatch | 1 | 1 |
| Total variance | +1.66% | +3.70% |

Both contain Piling, so the scope-exclusion path fires in both markets.

### No runtime network calls

Hard rule 2: the application performs **zero** outbound calls to index providers at runtime. All
index data is served from the local database, seeded from the CSVs above. The only network
connection the backend opens at runtime is to its own database.

---

## API

| method | path | purpose |
|---|---|---|
| `GET` | `/api/countries` | the market registry: currency, measurement standard, credible sources |
| `GET` | `/api/boq` | recent uploads, newest first. Query: `country`, `limit` |
| `POST` | `/api/boq/upload` | multipart CSV/XLSX -> parse, classify, persist. Query: `country`, `currency` |
| `GET` | `/api/boq/template` | **downloadable BoQ template.** Query: `format=csv\|xlsx`, `country` |
| `GET` | `/api/boq/{upload_id}` | parsed items for an upload |
| `PATCH` | `/api/boq/item/{item_id}` | manual reclassification; sets `classified_by = "manual"` |
| `POST` | `/api/boq/{upload_id}/benchmark` | body `{tender_quarter, tpi_series_name, variance_threshold, region_code, adjustments, manual_rates}` |
| `POST` | `/api/boq/{upload_id}/sensitivity` | index sweep + per-section tornado + break-even |
| `GET` | `/api/boq/{upload_id}/export` | streamed export. Query: `format`, `level` (items\|sections\|waterfall\|summary\|**report**), plus optional benchmark params |
| `POST` | `/api/boq/{upload_id}/export` | same, but takes the full benchmark body **including index adjusters** |
| `GET` | `/api/indices/tpi` | query: `country`, `series`, `from_quarter`, `to_quarter` |
| `GET` | `/api/indices/materials` | query: `country`, `material`, `from_month`, `to_month` |
| `GET` | `/api/indices/benchmark-rates` | query: `country`. The rate library with full provenance |
| `GET` | `/api/indices/regions` | query: `country`. Regional cost multipliers with their source and limitations |
| `GET` | `/api/indices/classifier-rules` | query: `country`. The effective rule table, so the classifier is auditable |
| `GET` | `/api/healthz` | `{status, db, environment}` - also the Render health check |
| `GET` | `/api/config` | non-secret runtime facts (dialect, CORS allowlist) for the UI status strip |

### Manual index adjusters

```json
"adjustments": {
  "tpi_scale_pct": 12.5,
  "base_rate_scale_pct": 0,
  "tpi_value_override": null,
  "section_rate_scale_pct": { "Concrete": -5 }
}
```

Every one of these is a **supply-side assumption**, not an observation. The engine:

* reports each affected line with `basis: "assumed"` and the `user_adjusted` flag,
* echoes the full set back in `adjustments_applied`,
* restates each adjustment in `assumptions[]` with the before and after index value,
* keeps the exact market-risk / scope cancellation for excluded sections intact, and
* keeps the waterfall reconciling to the cent.

### Completing the benchmark: analyst-supplied rates

A line the library cannot price - because the classifier could not place it, because no rate
exists for its section, or because its unit does not match - is carried at the tendered rate and
contributes **zero tested variance**. That is easy to miss and quietly flatters the result, so it
is surfaced rather than hidden:

* every such row is highlighted amber in the variance table with a **NOT BENCHMARKED** flag,
* the **benchmark coverage** panel above the table states how many lines are untested and what
  they are worth, and
* a rate can be supplied per line to close the gap.

```json
"manual_rates": {
  "216": { "base_rate": 24000, "indexed": true,  "note": "From a comparable completed project" },
  "217": { "base_rate": 1450,  "indexed": false, "note": "Contractor quotation, current prices" }
}
```

| `indexed` | Meaning |
|---|---|
| `true` (default) | The rate is stated at the benchmark library's **base year**. It is indexed, scope-adjusted and regionally adjusted exactly like a library rate. |
| `false` | The analyst states the rate at **tender-quarter price levels**. No index is applied, because indexing it again would double-count movement already inside it. |

Either way the line is:

* reported with `basis: "assumed"` and flagged `manual_rate`,
* given `from_library: false` so it is never confused with a published rate,
* recorded with `source: "Analyst-supplied manual rate"` and the analyst's note in `replace_with`,
* named in a warning stating how many lines were priced this way, and
* counted in the report as `adjustment / manual_rate_count`.

A manual rate does **not** override the scope rules: supplying a Piling rate under the BCA series
still leaves that line held at base year, because the index does not measure piling.

### Sensitivity analysis

`POST /api/boq/{id}/sensitivity` returns:

| Field | Meaning |
|---|---|
| `tpi_sweep[]` | should-cost, variance and breach counts at each index shift across the requested range |
| `break_even_scale_pct` | the index shift at which should-cost equals the tendered BoQ total |
| `break_even_tpi_value` | the same point expressed as an index value |
| `section_tornado[]` | each section's rate moved +/-N% in isolation, with the swing, ranked |
| `most_sensitive_section` | the top of the tornado |

Every point is produced by the same engine as the headline benchmark, so the baseline row equals
`POST /benchmark` to the cent - asserted by
`test_sensitivity_baseline_matches_the_headline_benchmark`. Setting `tpi_scale_pct` to the reported
break-even drives total variance to zero, asserted by `test_break_even_drives_total_variance_to_zero`.

### Downloads

Two families, both streamed from the backend:

* **BoQ template** - a ready-to-fill CSV or XLSX in the vocabulary of the selected market. The XLSX
  carries a second `Instructions` sheet listing the columns, the accepted unit spellings, and the
  credible sources for that market. Round-trip tested: every template example uploads and classifies
  cleanly (`test_template_csv_downloads_and_round_trips`).
* **Benchmark export** at four levels: `items`, `sections`, `waterfall`, `summary`. The UI posts the
  full benchmark body, so a download always matches the screen - **including** any active index
  adjusters and the assumptions that go with them.
* **Full report** (`level=report`, CSV or XLSX) - a single self-describing file. Long format:
  `block, ref, item, value, basis`. Nine blocks:

  | block | contents |
  |---|---|
  | `report` | generated-at, market, currency, region and its factor, measurement standard, BoQ file, tender quarter, index series and scope, index value used vs published, the formula itself |
  | `totals` | every figure from the totals block |
  | `section` | one row per section per measure, with the section's basis |
  | `waterfall` | amount, method and justification for every component |
  | `line` | every measure of every line, plus its benchmark provenance |
  | `adjustment` | every index adjuster **and** the regional factor that was applied |
  | `warning` | every warning |
  | `assumption` | every assumption in full |
  | `source` | the credible sources for that market, with URLs |

  A 20-line India report is ~625 rows and ~38 KB. Hand it to someone who never opened the app and
  they can reconstruct the entire analysis, including which numbers were measured, derived and
  assumed.

The benchmark response contains `lines[]`, `sections[]`, `totals`, `waterfall[]`, `warnings[]` and
`assumptions[]`. Every line carries the full benchmark provenance (`source`, `source_date`,
`base_year`, `scope_inclusions`, `scope_exclusions`, `confidence`, `is_placeholder`, `source_url`,
`replace_with`) plus the index actually applied.

### Waterfall reconciliation

```
boq_total + material + labour + market_risk + scope + unexplained = should_cost_total
```

| component | basis | method |
|---|---|---|
| `market_risk` | derived | `sum(quantity x base_rate x (tpi_ratio - 1))` |
| `scope` | derived | `sum(quantity x base_rate x tpi_ratio x (scope_factor - 1))` |
| `material` | **assumed** | `rate_gap x 0.55` |
| `labour` | **assumed** | `rate_gap x 0.45` |
| `unexplained` | derived | residual that closes the identity exactly; ~0.00 when every line is benchmarked |

`rate_gap = sum(quantity x (base_rate - boq_rate))` over benchmarked lines. The 55/45 split is an
**apportionment with no measured basis** and is returned in `assumptions[]` with its justification.
Replacing it with a measured rate build-up is the highest-value next step - see below.

---

## Classifier

Case-insensitive keyword match on `raw_description`, first matching rule wins, in this order:

```
concrete|grade \d+                     -> Concrete
rebar|reinforcement|steel bar          -> Reinforcement
formwork|shutter                       -> Formwork
brick|blockwork|masonry                -> Masonry
plaster|render|screed                  -> Plaster
excavate|excavation|dig                -> Excavation
pile|piling|bored                      -> Piling
waterproof|membrane                    -> Waterproofing
conduit|containment|trunking           -> M&E Containment
preliminar|site setup|insurance        -> Preliminaries
```

Anything unmatched becomes **`Unclassified`**. Unclassified lines are surfaced in the variance
table with an inline dropdown that issues `PATCH /api/boq/item/{item_id}` and re-runs the benchmark
immediately. Nothing is silently bucketed.

**Known consequence of first-match-wins:** `"Waterproof membrane to pile cap tops"` classifies as
**Piling**, because the Piling rule precedes the Waterproofing rule. This is asserted in
`test_first_match_wins_is_documented_caveat` so that changing the order is a deliberate act, and it
is exactly why manual reclassification exists.

---

## Unit safety

Before comparing rates, the BoQ unit is normalised and checked against the benchmark rate's unit
(`m2`, `m3`, `m`, `t`, `item`, with aliases such as `SQM`, `CUM`, `nr`, `lm`, `tonne`). A line whose
unit is incompatible is **excluded from variance testing** rather than compared - comparing an
`m2` rate against an `m` rate would be meaningless. Such lines are flagged `unit_mismatch`, held at
the tendered rate, and named in `warnings[]`.

---

## Frontend

Six views plus an always-visible assumptions panel:

1. **Upload** - market-aware drag-and-drop, template download, recent-upload picker, then the
   classification result by section.
2. **Variance table** - sortable, threshold highlighting, per-section subtotals, and **sections
   that collapse and expand** (individually, or all at once). Unbenchmarked lines are highlighted
   amber and flagged. Click any line to expand the full provenance of the benchmark rate behind it,
   and reclassify Unclassified lines inline.
3. **Waterfall** - Recharts, reconciling BoQ tender total to should-cost. One blue family from
   light to dark: the deepest bars are the opening BoQ total and the closing should-cost total, and
   each step in between is a progressively lighter blue. Rounded corners, the amount labelled above
   every bar, and a row of basis chips under the axis so an **assumed** step stays visible even
   though the bars are all blue. Includes a reconciliation table and an explicit
   reconciled / not-reconciled banner.
4. **Sensitivity** - the index sweep against the BoQ reference line, the break-even callout, the
   per-section tornado, and both detail tables.
5. **Index dashboard** - the country's index series, a material price trend with a display-only
   scenario slider, the benchmark rate library with provenance and TODO markers, and the credible
   sources for that market.
6. **Download** - BoQ template (CSV/XLSX), benchmark exports at four levels, and the **full CSV report**.

A **region selector** appears beside the index series whenever the market has more than one region
(India has twelve; Singapore has one, so it is hidden). Changing it re-runs the benchmark live and
the should-cost tile shows the region and its multiplier.

The index dashboard separates **real** from **placeholder** data with a count of each and a
per-series badge, so you can see at a glance which numbers came from a government publication and
which are still waiting to be licensed.

Every section carries a **"How to use this" pop-out** - a floating instruction card that stays open
while you work, rather than a tooltip that vanishes when you move the mouse. There are nine of them:
upload, benchmark parameters, index adjusters, coverage, variance table, waterfall, sensitivity,
index dashboard and download. Each ends with the caveats that matter for that view.

A **market switcher** sits above the tabs. Changing market reloads that market's index data, resets
to its default series and latest quarter, and auto-loads its seeded sample BoQ.

The **index adjusters panel** appears on every benchmark view. It re-runs the benchmark live
(300 ms debounce) as you move a slider, so the effect on variance is immediate. It can also be set
straight to the break-even level once a sensitivity run exists.

Tender quarter, index series and the variance threshold are selectors that re-run the benchmark
live.

### Theme and responsive behaviour

Light blue throughout: a gradient page background, white cards at 20px radius, soft blue shadows,
pill-shaped tabs, chips and badges, and rounded inputs. Colours are driven by CSS custom properties
at the top of `styles.css`, so the whole palette can be re-themed in one place.

Mobile behaviour is verified in a real browser rather than asserted:

| Width | Result |
|---|---|
| 1500px | full desktop layout, tiles in a grid, all tables visible |
| 390px | page overflow **0px**; tabs scroll horizontally; wide tables scroll inside their own container so the page never side-scrolls; controls stack full width; tiles drop to one column for currency figures |
| 360px | page overflow **0px** |

`prefers-reduced-motion` is honoured.

The backend URL is read **exclusively** from `import.meta.env.VITE_API_BASE_URL` in
`frontend/src/api.js`. `http://localhost:8000` appears exactly once, as the documented local-dev
fallback used when the variable is unset. Because Vite inlines this at build time, **changing it on
Vercel requires a redeploy**.

---

## Testing

```
cd backend && python -m pytest tests -v      # 205 tests

# The same suite passes against real PostgreSQL - the engine production runs:
docker compose up -d --build
docker compose exec -T \
  -e SHOULDCOST_TEST_DATABASE_URL=postgresql://shouldcost:shouldcost@db:5432/shouldcost_test \
  backend python -m pytest tests -q
docker compose down -v
```

| file | tests | covers |
|---|---|---|
| `tests/test_classifier.py` | 31 | every SG keyword family, case-insensitivity, unclassified handling, rule-order caveat |
| `tests/test_benchmark.py` | 28 | index up, index down, scope_factor != 1.0 with Piling present, missing-quarter fallback, unclassified handling, unit mismatch, reconciliation, provenance completeness |
| `tests/test_api.py` | 29 | upload/classify/patch, benchmark reconciliation within 0.01, threshold breaches, RLB scope warning, export bytes, health check, CORS posture, index endpoints |
| `tests/test_multi_country.py` | 34 | market registry and credible sources, India vocabulary, WPI behaviour, per-market index isolation, INR amounts, country-scoped endpoints, **real rows carry provenance and placeholders carry a TODO** |
| `tests/test_adjustments_sensitivity.py` | 33 | every adjuster, override/scale composition, per-section isolation, basis and assumption disclosure, sweep monotonicity, break-even, tornado ranking, validation limits, template round-trip, all four export levels |
| `tests/test_regions_report_import.py` | 26 | region defaults and errors, linearity of the multiplier, basis/assumption disclosure, region through sensitivity, all nine report blocks, report reconciliation, **importer provenance enforcement and idempotency**, and spot-checks of the real WPI values against the published series |
| `tests/test_manual_rates.py` | 20 | coverage gap is real and closes to 100%, manual rates are tagged assumed and never from the library, indexed vs stated-at-tender behaviour, region applies only to indexed rates, scope exclusions still win, unusable rates ignored, reconciliation holds, sensitivity carries them, and the report counts them |

Tests run against a throwaway SQLite file created in a temp directory by `tests/conftest.py`, which
sets `DATABASE_URL` **before** importing any application module. The developer database is never
touched by the suite.

### Rendered UI verification

Because vision is unavailable in this environment, the frontend was verified by driving the **built**
production bundle (`vite preview`) in headless Chrome and asserting on the rendered DOM, instead of
by inspecting screenshots. Against the seeded samples:

```
DESKTOP - Singapore, auto-loaded on boot
  header status      : API ok | db connected | development | Singapore | SGD
  market bar         : SMM2 - Metric SI. Rates are per m, m2, m3, tonne or lump sum.
  KPI tiles          : BoQ S$2,414,861.00 | should-cost S$2,375,328.64 | variance +S$39,532.36 (+1.66%)
                       8 of 20 breaching | 3 not benchmarked (S$98,460.00)
  variance table     : 41 rows, 11 section subtotals
  waterfall          : 3 recharts surfaces
  sensitivity run    : 6 charts, 19 rows, break-even +2.01% (index 142.00),
                       most sensitive section Concrete (swing S$73,469.76), 9 sweep points
  index adjuster     : variance -S$195,980.68 (-7.51%) after moving the index +12%
                       assumptions 4 -> 5, adjusted lines flagged basis=assumed
  template download  : "Downloaded shouldcost-boq-template-sg.csv"

DESKTOP - switched to India
  market bar         : IS 1200 / CPWD DSR - rates in Indian Rupees
  KPI tiles          : BoQ INR 4,00,45,550.00 | should-cost INR 3,86,16,628.75
                       variance +INR 14,28,921.25 (+3.70%) | 10 of 20 breaching
  index dashboard    : 7 recharts surfaces, 20 rate-library rows

MOBILE 390px   : page overflow 0px, tabs scrollable, tables contained, tiles single column
MOBILE 360px   : page overflow 0px
CONSOLE ERRORS : 0
```

Zero console errors is also the CORS proof: the page runs on `http://127.0.0.1:4173` and calls the
API on `http://localhost:8000` - a genuine cross-origin request - with no console or network error.

---

## Deployment

**Not executed.** The capability probe found no Render or Vercel credentials in the environment, so
per the brief M6 was skipped and the configuration plus a manual runbook were written instead:

* `render.yaml` - Render Blueprint: FastAPI web service + managed PostgreSQL
* `frontend/vercel.json` - SPA rewrites, Vite build settings, asset caching
* `backend/.env.example`, `frontend/.env.example`, `.env.example`
* `backend/Dockerfile` + `docker-compose.yml` - a portable image and a local Postgres stack, which
  unlock any container host and let the production database path be tested locally
* **`DEPLOY.md` - the linear four-service checklist: GitHub -> Neon -> Render -> Vercel. Start here.**
* `render.neon.yaml` - a Blueprint variant with no `databases:` block, for when PostgreSQL lives on Neon
  (whose free plan does not expire, unlike Render's, which is deleted after 30 days)
* `deploy-runbook.md` - the deep reference: seeding options, rollback, the PostgreSQL-vs-SQLite test
  matrix, the full troubleshooting table, and a survey of free hosting alternatives (section 14)

Key configuration facts, each verified against the current Render documentation rather than assumed:

* The Blueprint uses `runtime: python`. Render documents `runtime` as the current field and states it
  replaces the deprecated `env` field, so the legacy `env: python` spelling is not used.
* `PYTHON_VERSION` is set to the **fully qualified** `3.11.6`. Render's docs are explicit that the
  environment-variable method requires a fully qualified version; a bare `3.11` is ignored.
* The database uses `plan: free` (0.1 CPU / 256 MB). That plan id is still current in Render's plan
  table. Free PostgreSQL instances are deleted after 30 days - the runbook covers this.
* `FRONTEND_URL` and `FRONTEND_PREVIEW_REGEX` use `sync: false`, so Render prompts for them in the
  dashboard and **no secret or environment-specific value is committed**.
* Seeding is a one-off Shell command (`python -m app.etl`); after any future schema change it must
  be `python -m app.etl --reset`, which **destroys uploaded BoQs** - see deviation 16.

CORS is an explicit allowlist read from `FRONTEND_URL`, plus an optional regex from
`FRONTEND_PREVIEW_REGEX`. Development adds localhost origins. **A wildcard `*` is never used, in
any environment** - asserted by `test_cors_never_uses_a_wildcard`, and the production posture
(fail-closed with no `FRONTEND_URL`, never falling back to the dev origins) is asserted by three
unit tests that build `Settings` directly.

### Free hosting alternatives

`deploy-runbook.md` section 14 surveys what is actually free in 2026. The short version:
**Fly.io's free tier is gone for new accounts** and **Koyeb's Starter plan closed to new signups**
after the Mistral acquisition, so Render + Vercel remains one of the better genuinely-free options.
Its one real weakness - Render's free PostgreSQL is **deleted after 30 days** - is fixed by moving
the database to **Neon**, whose free plan suspends on idle rather than expiring. No code change is
needed: the app was verified against real PostgreSQL 16, and the full suite passes on both engines.

The end-to-end proof the production path works:

```
postgresql dialect, ENVIRONMENT=production, CORS = ['http://localhost:4173'], no wildcard
ETL on Postgres      : 126 tpi_series, 180 material_prices, 20 benchmark_rates, 13 regional_factors
India benchmark      : boq 32,246,130 | should-cost 34,846,719.65 | variance -7.46% | reconcile 0.0
full test suite      : 205 passed on PostgreSQL 16  (and 205 passed on SQLite)
legacy postgres://   : normalised and connected
```

---

## Known deviations and decisions

Each of these is a deliberate choice, not an oversight.

1. **`runtime: python`, not `env: python`.** Render deprecated `env`. Using the legacy spelling
   would be following the brief off a cliff.
2. **`PYTHON_VERSION=3.11.6`, not `3.11`.** Render ignores a non-fully-qualified value, which would
   silently build on its default Python instead.
3. **No material-escalation term in `adjusted_benchmark_rate`.** The narrative
   `technical_specification.md` proposes `(base rate x TPI factor) + material escalation`. Section 2
   of the brief states its four formulas "are the contract" and does not include escalation, and
   adding it on top of TPI adjustment would double-count the same input cost. The contract wins.
   Material prices are seeded, served and charted, but are **not** added into the rate.
4. **`FLOAT` rather than `NUMERIC` for money.** SQLite has no true fixed-point type, and `NUMERIC`
   round-trips through psycopg2 as `Decimal`, which Pydantic v2 serialises as a JSON **string** and
   would silently break arithmetic in the browser. Values are rounded to 2dp at the API boundary.
5. **Three provenance columns added beyond the agreed schema** (`source_url`, `is_placeholder`,
   `replace_with` on the seed tables; the latter two also on `boq_items`), required by hard rule 1.
6. **Unit-compatibility guard added** (not in the brief). Without it the engine would happily
   compare an `m2` rate with an `m` rate and report a confident, meaningless variance.
7. **Unbenchmarked lines are held at the tendered BoQ rate**, contributing zero *tested* variance.
   The alternative - dropping them from the should-cost total - makes the waterfall stop
   reconciling. The carve-out is disclosed in `warnings[]`, `assumptions[]` and the totals
   (`unbenchmarked_boq_total`, `unbenchmarked_line_count`).
8. **`scope_factor = 1 / tpi_ratio`** for excluded sections (see above); the brief specified only
   that it be `!= 1.0`.
9. **Legacy `.xls` is rejected with instructions**, not parsed. `openpyxl` cannot read the BIFF
   format and silently accepting the extension would produce an opaque error.
10. **`replace_with` TODO markers are stored in the database, not just written in comments.**
    Hard rule 1 requires the marker to reach the UI, and a code comment cannot do that.
11. **`base_rate_sgd` / `price_sgd` renamed to `base_rate` / `price`.** The original names were
    specified for a Singapore-only app. Once India exists, holding a rupee rate in a column called
    `*_sgd` is a defect, not a naming preference. `currency` on the row carries the truth.
12. **The material price scenario slider is display-only.** It re-scales the chart and nothing else,
    and the UI says so twice. The formula contract has no material-escalation term, so letting it
    move should-cost would be a lie dressed as a feature.
13. **The India index base is the WPI's native 2022-23 = 100**, and the India rate library is at
    base year 2023. Those align by construction, not by rebasing. A base-year mismatch between a
    series and a rate library still raises a named warning rather than passing silently.
14. **India's "material prices" are cost indices, not rupee prices.** WPI publishes index numbers.
    The `unit` column says "index (2022-23 = 100)" and the dashboard switches its heading and axis
    label accordingly, rather than showing an index in a chart captioned as a price.
15. **The regional multipliers are placeholders.** They are the one number in this build that is
    both influential and invented. Every row names the source it must come from, a non-1.0 factor
    raises a warning on every run, and every line it touches is `basis: assumed`. Nothing else
    described as real is synthetic, and nothing described as synthetic is presented as real.
16. **Schema changes need a reset.** `create_all()` adds missing tables but never ALTERs an existing
    one, so a new column is invisible until the table is rebuilt: `python -m app.etl --reset` (or
    `make reset-db`). That drops every table, so **uploaded BoQs are destroyed** - reference data is
    reproducible from the CSVs, uploads are not. A real deployment wants a migration tool.

---

## Limitations - what this is not

* **Not usable for a real tender decision as shipped.** Every index value and benchmark rate is a
  synthetic placeholder. The UI says so on every screen.
* **No PDF upload.** No OCR pipeline, and no runtime LLM call is permitted.
* **No authentication or audit trail.** Every endpoint is open. Do not expose this to the internet
  with real commercial BoQ data until auth, per-tenant isolation and an audit log are added.
* **The material/labour split is assumed**, not measured. It is the largest single source of
  unearned confidence in the output, and it is labelled as such everywhere it appears.
* **No back-testing.** The engine has not been validated against completed Singapore projects.
* **Scope-exclusion matching is phrase-based.** It will need tuning against the exact wording of the
  index series you license.

---

## Sources referenced (by name, not by value)

BCA Tender Price Index (base 2010 = 100) - SISV Tender Price Index circulars, which consolidate the
BCA, HDB, AECOM, RLB and Asia Infrastructure Solutions series - BCA Construction InfoNet elemental
unit rates and material price indices for fluctuation clauses - SingStat / BCA material price
series for cement, steel reinforcement and ready-mixed concrete, published monthly from Jan 1999 -
RLB Rider's Digest and the Arcadis Quarterly Cost Review for building-type rates and TPI series -
SMM2, the Standard Method of Measurement of Building Works, 2nd Edition, for BoQ classification.

---

## Next steps to production

1. **Replace every placeholder with a licensed feed** - BCA InfoNet and SISV circulars under
   licence, keyed by the `replace_with` marker already stored on each row.
2. **Replace the assumed 55/45 material/labour split with a measured rate build-up** so those
   waterfall bars become `basis: measured`.
3. **Authentication, per-tenant isolation and an audit trail** before any real commercial BoQ is
   uploaded.
4. **Back-test the engine** against completed Singapore projects to calibrate confidence ratings.
5. **Upgrade the Render PostgreSQL plan** before the free instance expires (30 days).
6. **License the Indian rate and city-index feeds.** The WPI is public and already in use; the
   **CPWD DSR** (rates) and the **CPWD / NBO city cost indices** (the regional multipliers) are sold
   publications. Those are the two remaining synthetic inputs, and they are the two that matter most
   for a real estimate.
7. **Add more markets.** The registry is designed for it: one entry in `countries.py` plus seed rows.
   Malaysia (JKR schedules), Hong Kong (ArchSD), and the Gulf are the obvious next candidates.
8. **Replace the single-rate-per-section library with a rate build-up**, so India and Singapore can
   differ by material content rather than only by a section-level multiplier.
