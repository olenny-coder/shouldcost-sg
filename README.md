# shouldcost

Should-cost BoQ benchmarking for the **Singapore and India construction markets**. Upload a Bill of
Quantities, get every line classified to a measurement section, re-priced against a benchmark rate
library adjusted by a published cost index, and reported as variance, section subtotals, a
reconciliation **waterfall** and a **sensitivity analysis**.

| Market | Measurement standard | Currency | Rate library source (derived to Q2 2026) | Price index used to carry a stale index forward |
|---|---|---|---|---|
| Singapore | **SMM2** (Standard Method of Measurement, 2nd Ed.) | SGD | **BCA Schedule of Rates, May 2022** x 1.171 to Q2 2026 - and **BCA is the only index** a benchmark may run against | no usable **PPI** (documented gap), so the **SingStat CPI, All Items** (2024 = 100) - **real** |
| India | **IS 1200** + **CPWD DSR** chapter structure | INR | **CPWD Delhi Schedule of Rates 2021 Vol-II** x 1.2364 to Q2 2026 - and **CPWD is the only index** a benchmark may run against | **OEA producer price index, 16 commodity baskets** (2022-23 = 100) - **real** - with the **MoSPI CPI, Combined, All-India** as fallback |

The **rate library is derived to current**: every rate comes from a named published schedule of rates,
cumulative-adjusted to **Q2 2026** by the factor the schedule's own source file states. Each row records
the quarter it is expressed at (`base_quarter`), and the app escalates from *that* quarter - never from
the index series' own base year, which would apply the same inflation twice.

Published construction cost indexes lag the tender quarter - the BCA series is a quarterly release and
the WPI appears about two months after the month it describes, while the producer and consumer price
indexes are monthly. So the app carries the last published index observation forward along the
published **trend to date** of a price index, and discloses that step as an assumption on every
affected line. A **producer price index is used first** - it measures what suppliers actually charge
for the cement, steel, minerals, fuel and power a construction rate is made of - and a consumer price
index is the fallback. See
[Keeping the indexes current](#keeping-the-indexes-current-ppi-first-cpi-as-fallback).

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
make test        # pytest, 361 tests

# optional, if you have refreshed the publisher downloads in ../.realdata/:
python tools/verify_seed_data.py   # re-derives every seeded value from its source
make cpi-seed                      # rebuilds data/cpi_series.csv from those downloads

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
| `make cpi-seed` | `python tools/build_cpi_seed.py` |
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
| **Real** | `false` | `provenance_note` | An actual published observation, or a rate derived from one. The note names the publisher, the series and any transformation. |
| **Retained** | `true` | `provenance_note` | A value carried from an older base, from a named source, for scope the loaded extracts do not reach - and, for the index seed quarters, a modelled value standing in for a licensed release. |

Current split in the bundled seed data:

| table | real | total | |
|---|---|---|---|
| `material_prices` | **180** | 180 | 100% real - BCA via SingStat (Singapore prices) and WPI (India cost indices) |
| `cpi_series` | **146** | 146 | 100% real - 55 Singapore months (SingStat table M213751) and 91 India months (MoSPI: 43 on the current base with its back-cast, 48 on the predecessor base) |
| `tpi_series` | **78** | 126 | India WPI series are real; the Singapore TPI and the CPWD/NBO city indices are not yet licensed |
| `benchmark_rates` | 0 | 20 | CPWD DSR and BCA Construction InfoNet are **sold publications** - no free machine-readable schedule of rates exists |
| `regional_factors` | 0 | 13 | CPWD/NBO publish city indices as circulars and PDFs - not machine-readable here |

### The real sources, and how to reach them

| Source | Publisher | What is used | Where |
|---|---|---|---|
| **Wholesale Price Index**, base 2022-23 = 100 | Office of the Economic Adviser, DPIIT, Ministry of Commerce and Industry | Monthly XLSX of every WPI item and sub-group. Drives **WPI-CONST**, **WPI-CEM**, **WPI-STL** and the item-level **WPI-CEM-OPC**, **WPI-STL-BARS**, **WPI-RMC** series | [eaindustry.nic.in/download_data_2223.asp](https://eaindustry.nic.in/download_data_2223.asp) |
| **Construction Material Market Prices** (table M211671) | **Building and Construction Authority**, published through SingStat Table Builder | Cement, steel reinforcement, granite aggregate, concreting sand and ready-mixed concrete, annual 1999-2025 | [tablebuilder.singstat.gov.sg/table/TS/M211671](https://tablebuilder.singstat.gov.sg/table/TS/M211671) - the API endpoint used is `/api/table/tabledata/M211671` |
| **Consumer Price Index** (table M213751) | **Singapore Department of Statistics**, SingStat Table Builder | Monthly All Items CPI, 2024 = 100. **This is the series the CPI bridge uses** | [tablebuilder.singstat.gov.sg/table/TS/M213751](https://tablebuilder.singstat.gov.sg/table/TS/M213751) - API: `/api/table/tabledata/M213751` |
| **Consumer Price Index** (Combined, All-India General) | **MoSPI / National Statistical Office**, Government of India | Monthly CPI on **base 2024 = 100**, published together with the publisher's own **back-cast** months on that base (2023-01 onward), and the predecessor **base 2012 = 100** series (2013-01 to 2025-12). **These are the series the India CPI bridge uses** | [api.mospi.gov.in](https://api.mospi.gov.in/) (`/api/cpi/getCPIData`, sector `Combined`, state `All India`) and [mospi.gov.in](https://www.mospi.gov.in/) |

All four are freely downloadable. None is fetched at runtime: the values are extracted once by
`tools/build_cpi_seed.py` (CPI) or the documented derivation in `.realdata/` (WPI, materials) and
committed as seed data, so the app stays fully offline (hard rule 2).

To refresh the CPI seed from the publishers' own files:

```bash
# Singapore: table M213751, row "All Items"
curl -H "User-Agent: Mozilla/5.0" -H "Referer: https://tablebuilder.singstat.gov.sg/" \
     -H "Origin: https://tablebuilder.singstat.gov.sg" \
     -o ../.realdata/singstat_M213751_cpi.json \
     "https://tablebuilder.singstat.gov.sg/api/table/tabledata/M213751"

# India: the MoSPI eSankhyiki CPI API, one call per month (see the module docstring
# of tools/build_cpi_seed.py for the parameters and the row selector), merged into
# ../.realdata/india_cpi_monthly.csv with the documented header:
#   month,value,series_name,base_year,source_url,publication_date
#   https://api.mospi.gov.in/api/cpi/getCPIData?base_year=2024&year=2026&month_code=7
#     &limit=100&page=1&sector_code=3&state_code=99     -> 2026-07 = 107.94

python tools/build_cpi_seed.py     # rewrites backend/data/cpi_series.csv
cd backend && python -m app.etl     # upserts it, idempotently
python tools/verify_seed_data.py   # re-derives every seeded value from the raw files
```

**What the rate library is built from.** The benchmark rates are no longer invented. Eleven of the
twenty come from a real published schedule of rates - the **BCA Schedule of Rates, May 2022** for
Singapore and the **CPWD Delhi Schedule of Rates 2021 Vol-II** for India - cumulative-adjusted to
**Q2 2026** by the factor each source file states (x1.171 and x1.2364, the cumulative CPI inflation
from the schedule's base year). Each is the **median of the qualifying SOR lines** that map to its
canonical section, and `make verify-seed` re-derives all eleven from the publisher files.

**What is deliberately still an estimate, and why.** The extracts are partial. The India file is DSR
*Vol-II* (chapters 13-26), so earthwork, concrete, reinforcement and formwork - which live in Vol-I -
are not in it; the Singapore file carries no piling and no M&E; and neither carries a
general-requirements schedule. Those nine rows keep a **retained estimate**, each carrying
`is_placeholder: true` internally and a `provenance_note` naming the source and why the extract does
not reach it. The **preliminaries estimate is retained in both markets at the user's instruction**, so
the section is never silently dropped. The app labels derived and retained separately on every screen.

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

`--kind` accepts `tpi`, `materials`, `benchmark_rates`, `regions` or `cpi`. Importing forces
`is_placeholder: false`, stores your provenance note, clears `replace_with`, and upserts on the
natural key - so re-importing a refreshed file is idempotent. **`--provenance` is mandatory**: a
real-looking number with no stated origin is worse than an honestly labelled indicative value.

---

## Keeping the indexes current: PPI first, CPI as fallback

A tender is priced at a quarter. The index series you select may not have published that quarter
yet. Before this feature the app simply fell back to the nearest prior observation and said so, which
left every recent tender priced at an out-of-date index level. Now the gap is closed, explicitly:

```
index_value(tender quarter) = index_value(last observed quarter)
                              x price_index(covered through) / price_index(last observed quarter)
```

`price_index` is a **producer** price index wherever the market publishes one, and a **consumer**
price index only where it does not.

* **Producer indices come first.** A PPI measures what manufacturers and utilities charge for the
  commodity baskets a construction rate is built from, so it is the closest published proxy for the
  movement being estimated. India publishes 16 construction-relevant baskets monthly (cement,
  aggregate, limestone, iron and steel, castings, foundries, wood, plastics, paints, cable, electrical
  machinery, petroleum products, electricity, and the all-commodities composite), all on base
  2022-23 = 100. The registry's `default_ppi_series` names the preferred one and the others are
  tried in turn.
* **The consumer index is the fallback**, reached only when no producer series spans both endpoints -
  as in Singapore, which publishes no machine-readable commodity-level PPI. A bridged line therefore
  reports which kind carried it (`index_bridge_kind`, `kind` in `index_bridge`), and the warning and
  assumption text say which, because the strength of the assumption differs between the two.
* `"index_bridge"` accepts **`auto`** (the default: PPI then CPI), **`ppi`**, **`cpi`** and
  **`none`**. The UI's **Stale index** selector exposes all four.
* Both endpoints are the **mean of the months available in that quarter** - the same convention the
  India WPI quarters are built with - so a quarter that is only partly published still works.
* The bridge **never runs past the latest published month of the series it is using**. If the tender
  quarter is later than that, the index is derived as far as real data allows and the response reports
  `shortfall_months` plus a warning saying the index is current to a month short of the quarter end.
* It is **switched off** with `"index_bridge": "none"` on the benchmark request, or with the
  **Stale index** selector in the UI. The last observation is then held unchanged and a warning
  states how stale the index is.
* An **absolute index override replaces the bridged level**: your override always wins, and the
  bridge step in the waterfall falls to zero.
* A market may carry **more than one series of a kind**, and the engine picks the one that spans
  the bridge. India carries MoSPI's **current base 2024 = 100** consumer series - which the publisher
  issues together with its own **back-cast** months on that base, so it reaches back to 2023-01 - plus
  the **predecessor base 2012 = 100** series, which ends at 2025-12 and does **not** overlap the
  current one (196.5 on the old base against ~103 on the new one for adjacent months). The registry's
  declared series for each kind is tried first, then any other series of that kind, and the first one
  that spans **both** endpoints wins.
* Series are **never chained or spliced across a base change**. Without the publisher's official
  linking factor, a splice would invent a level shift. If no single series covers both quarters the
  bridge reports `no_price_series_covers_both_quarters` (or the specific coverage reason), names what
  each candidate covered, and the run falls back to holding the last observation.
* The **back-cast months are labelled as such** in each row's `provenance_note`, because they are the
  publisher's own re-estimation on the new base rather than a measurement first published on it.

Why it is a modelled step and not a measurement, whichever kind is used. Even a producer basket is
not the construction cost index itself: it prices the material content of a section and no part of
its labour, plant or productivity. And a consumer basket is a still weaker proxy, because it moves
with household consumption - food, housing, transport, services - rather than with what is bought for
a building. Either way the bridged value is **derived to show the trend to date**, so:

* every bridged line is `basis: "assumed"` and flagged `index_bridged`,
* the bridge is its **own step in the waterfall** (`cpi_bridge`, basis `assumed`), always zero when
  no bridging took place, so a bridged run and an observed run are directly comparable,
* it is restated in `assumptions[]` with the months, values, factor and source URL, and
* the response carries an `index_bridge` block (also written into the CSV report) with the published
  value, the bridged value, the lag in quarters and a machine-readable reason when no bridge ran.

The engine never invents a price index either: if no usable series is loaded for the market, the
bridge degrades to "hold the last observation" plus a warning naming the reason, and the run still
completes.

### The library states its own quarter, and one index per market

The rate library is **cumulative-adjusted to Q2 2026**, which creates a double-counting trap: the
ordinary ratio is `index(tender) / index(series base year)`, and applying that to a rate which already
contains 2022 -> 2026 movement would apply the same inflation twice. So every library row can state
the quarter it is expressed at:

```
base_quarter = "2026Q2"   ->   ratio = index(tender) / index(2026Q2)
base_quarter = ""         ->   ratio = index(tender) / index(series base year)   [prior behaviour]
```

At a Q2 2026 tender the SOR-derived sections therefore have a ratio of exactly **1.0**, and a later
quarter escalates only by the movement since Q2 2026. A **retained** estimate has no stated quarter, so
it keeps the old behaviour and is escalated from the series base year - which is why two sections in
one run can carry different ratios, each correct for its own basis. The waterfall splits the movement
accordingly: `market_risk` is the movement the index has actually **published** since the rate's base
quarter, and `cpi_bridge` is the modelled carry-forward on top. When the library is expressed *after*
the last published observation - rates at 2026Q2 against an index last published at 2024Q4 - nothing
since the rate base has been published at all, so market risk is exactly zero and the whole movement is
the modelled step.

Two consequences worth stating plainly:

* **A benchmark runs against one series per market** (`selectable_tpi_series`: BCA for Singapore,
  CPWD for India). The library was derived from one published schedule of rates per market, so pricing
  it with a different index would escalate DSR-derived rates with a series they do not belong to.
  WPI-CONST in particular is a materials-only composite with no labour, plant or preliminaries content.
  Every other series stays loaded and charted on the index dashboard.
* **The waterfall builds every step from the base rate at full precision** (`benchmark_base_rate_exact`),
  not the 2dp display value. Using the rounded rate left a residual that grew with quantity and the
  regional multiplier and landed in `unexplained`, which is meant to be a rounding artefact and nothing
  else. It is now exactly zero.

`GET /api/indices/freshness` reports the same machinery without running a benchmark: per series, the
last published quarter, the lag against a reference quarter, the kind and name of the index that
would carry it, and `bridge_preference` (`producer` | `consumer` | `none`). The **index dashboard**
renders it as a table, next to charts of both the producer and the consumer series.

### Which sections the published data actually covers

`GET /api/indices/coverage` answers the question the freshness table raises: **for every canonical
measurement section, which published series re-prices it, and where is the gap?** It is read from the
database, so it reports what is loaded rather than what is intended. Each row carries a status:

| Status | Meaning |
|---|---|
| `producer_covered` | a loaded producer index names this section in its `scope_sections` |
| `producer_plus_labour_gap` | the materials and fuel are indexed; site labour is not, by any publication |
| `consumer_only` | no producer basket maps here, so the weaker consumer proxy is used |
| `uncovered` | nothing loaded re-prices the section - the benchmark holds it at base year |

Two kinds of gap are deliberately distinguished, because they are not the same thing. An **uncovered
section** is fixable by wiring in another published series. A **labour gap** is structural: no price
index on earth measures site labour, which is 25-40% of a building rate, so a labour-dominated
section's escalation is a floor rather than a full cost movement. Each section also names the credible
publication that would close its gap and whether it is `wired`, `partial` or a documented `gap` -
the CPI-IW from the Labour Bureau and the CPWD DSR for India, BCA InfoNet and the SISV circulars for
Singapore.

As loaded, **all ten canonical India sections are covered by a published producer index**, including
Excavation, which was the last one with no coverage at all until the Petroleum Products basket (diesel,
the direct running cost of plant) was wired in. Singapore has no producer coverage, and says so.

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

1. **They are indicative seed values.** CPWD publishes a city-wise cost index and NBO publishes city
   building cost indices, but neither was available in machine-readable form here. Each region's row
   carries the source it should come from and a `# TODO`. An indicative factor that is not 1.0
   raises a named warning on every run.
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

Two **analyst** multipliers, the **overheads and margin** percentages and the **CPI bridge** may be
layered on top of the published values:

```
current_index = (published_index * cpi_bridge_factor or tpi_value_override) * (1 + tpi_scale_pct / 100)
cpi_bridge_factor = cpi(covered through) / cpi(last observed quarter)      # 1.0 when not bridged
base_rate     = published_base_rate * (1 + (base_rate_scale_pct + section_scale_pct) / 100)
full_rate     = adjusted_benchmark_rate * (1 + overhead_pct / 100) * (1 + margin_pct / 100)
```

`full_rate` is the rate that produces a **full commercial should-cost**; see "Overheads and margin"
below. Every one of these inputs is an assumption, so any line they touch is reported with
`basis: "assumed"` and flagged (`index_bridged`, `user_adjusted`, `overhead_applied`,
`margin_applied`).

---

## Overheads and margin: from a benchmark cost to a full should-cost

A rate library prices work. It does not price the contractor's site and head-office overheads or
their profit. Two analyst inputs close that gap, on the **Index adjusters** panel (or in
`adjustments` on the API):

```json
"adjustments": { "overhead_pct": 12.0, "margin_pct": 6.0, "overheads_in_tender": true }
```

```
full_rate  = adjusted_benchmark_rate x (1 + overhead_pct/100) x (1 + margin_pct/100)
full_total = sum(quantity x full_rate) over benchmarked lines
             + unbenchmarked lines held at the tendered rate
```

The contract, and the reasoning behind each choice:

| decision | why |
|---|---|
| **Margin compounds on overheads** | `1.12 x 1.06 = 1.1872`, not `1.18`. It is the usual commercial convention, it is asserted numerically in `test_full_rate_compounds_margin_on_overheads`, and the compounding order is stated in `assumptions[]` rather than left to the reader. |
| **Benchmarked lines only** | A line carried at the tendered rate already contains the contractor's own OH&P. Grossing it up again would double-count it, so those lines contribute zero overhead and zero margin - and say so. |
| **Additive, exactly** | `overhead` and `margin` are their own waterfall steps, so `boq_total + ... + overhead + margin + unexplained = full_should_cost_total`, closing to the cent. |
| **Variance basis is explicit** | `overheads_in_tender` (default `true`) declares that the tendered rates already carry OH&P, so each line's variance is measured **full-to-full**: tendered rate against the grossed-up benchmark rate. Set it to `false` when the tender is net of OH&P, and the variance reverts to the benchmark rate before overheads while the full cost is still reported. The headline variance follows the same basis as the lines, so the tiles can never disagree with the table. |
| **Always assumed** | Neither percentage is evidence. Every benchmarked line becomes `basis: "assumed"`, flagged `overhead_applied` / `margin_applied`, and the run's `totals.basis` becomes `assumed`. |
| **Zero means unchanged** | With both percentages at zero the two steps are exactly `0.00`, the full total equals the benchmark total to the cent, and the run is numerically identical to before this feature existed. |

What you see when they are set: a **Full should-cost (incl. OH&P)** tile (the benchmark tile is
relabelled *benchmark cost*), a full-cost row in the variance table footer, a full-cost line in every
line's expanded detail and in each section subtotal, two extra waterfall bars, and a live readout on
the adjusters panel. The CSV report carries `overhead_pct`, `margin_pct`, `overheads_in_tender`,
`full_should_cost_total` and the formula, plus per-line `full_adjusted_benchmark_rate`,
`overhead_amount`, `margin_amount` and `full_should_cost_amount`.

**The limitation, stated plainly:** the percentages are a single blended pair for the whole BoQ.
Preliminaries-heavy and M&E sections normally recover overheads differently from structural work, so
a per-section split is the next refinement - the assumption text says this on every run.

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
cpi_series       id, country, series_name, month, base_year, base_value, currency, value,
                 source_url, is_placeholder, provenance_note, replace_with
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
* **`source_url`, `is_placeholder` and `replace_with`**, required by hard rule 1: every value must be
  traceable to the source it came from, and where a value is a stand-in for a licensed release it
  must say which one. Provenance and basis are what the UI reads out; the `is_placeholder` boolean is
  an internal distinction (derived versus retained) and is never printed as a flag to the analyst.

The column is still called `smm2_section` even though India does not use SMM2. Both standards
partition building work into the same ten sections, and the name is retained for API backward
compatibility; `classification_standard` carries the truth.

---

## Seed data: published observations, derived figures, retained estimates

Every seeded row declares which of the three things it is, and the split is stated here rather than
implied. The API carries one boolean for it - `is_placeholder` - because that is what the row-level
contract requires; the UI never uses the word, because "not a published observation" covers three
genuinely different situations that deserve different labels:

| | Flag | What it is | How the UI labels it |
|---|---|---|---|
| **Published** | `is_placeholder: false` | the number is printed in the named publication | *published* |
| **Derived** | computed at run time | the engine computed it from published values - most often by carrying a stale index forward along the published trend of a price index | *derived to date* / *basis: assumed* |
| **Retained estimate** | `is_placeholder: true` | a rate or index value carried from an older base, from a named source, for scope the loaded extracts do not reach | *retained* |

**The rate library is not a placeholder.** Its section rates are derived from the published schedules
of rates (BCA SOR May 2022, CPWD DSR 2021 Vol-II) escalated by CPI to the quarter the library is
stated at - a sourced, derived figure, labelled `basis: derived`, which is the rate the app benchmarks
against. The nine **retained estimates** are the sections those extracts do not reach (BCA piling and
M&E, DSR Vol-I earthwork, concrete, reinforcement, formwork and masonry, and preliminaries in both
markets); each names its source and, where it needs one, the index that carries it. Neither kind
carries a `# TODO` telling the analyst to come back with a licensed document: the library as loaded
*is* the app's rate basis, and the disclosure that matters - source, quarter, and measured/derived/
assumed - is on every row.

**Index seed rows** (the construction cost index quarters for BCA/HDB/RLB/AECOM/CPWD/NBO) are the one
place where a synthetic stand-in remained: those quarterly index values are modelled on the published
trend rather than transcribed from a release, so they keep `is_placeholder: true`, a `source_url`
naming the publication they stand in for, and a `replace_with` marker naming the value that should
replace them. They are index inputs, not rates, and the app's own bridge is what turns them into the
movement it uses.

**Published rows** (the India WPI quarters, all material prices, the monthly producer price indexes
and the monthly CPI) carry `is_placeholder: false` and a `provenance_note` naming the publisher, the
table and the transformation applied.

**Derived rows are never stored**, so they cannot be mistaken for observations: the bridged index
value, the adjusted benchmark rate, the variance, the waterfall steps and the should-cost total are
all computed from the above on every run, and every one of them that rests on a modelled step is
tagged `basis: assumed` in the response.

| file | rows | content |
|---|---|---|
| `backend/data/tpi_series.csv` | 126 | **SG (32, indicative):** BCA, HDB, RLB, AECOM - 8 quarters, base 2010 = 100. **IN (78, REAL):** WPI-CONST, WPI-CEM, WPI-STL, WPI-CEM-OPC, WPI-STL-BARS, WPI-RMC - 13 quarters, 2023Q2-2026Q2, base 2022-23 = 100. **IN (16, indicative):** CPWD, NBO - the city cost indices |
| `backend/data/price_series.csv` | 786 | **IN PPI (640, REAL): 16 producer baskets**, monthly 2023-04 to 2026-07, base 2022-23 = 100, from the Office of the Economic Adviser's published OPPI/WPI workbook. Each carries its published basket weight, the sections it may re-price in `scope_sections`, and the mapping rationale in `provenance_note`. **SG CPI (55, REAL):** All Items, monthly 2022-01 to 2026-07, base 2024 = 100 (SingStat table M213751). **IN CPI (91, REAL):** Combined All-India General - 43 months on the current base 2024 = 100 (2023-01, the publisher's back-cast, through 2026-07) and 48 on the predecessor base 2012 = 100 (2022-01 to 2025-12). Supersedes `cpi_series.csv` when the table gained a `kind` column |
| `backend/data/cpi_series.csv` | 146 | the pre-`kind` CPI seed, retained as the input the PPI build reads and as the record of the original CPI rows |
| `backend/data/material_prices.csv` | 180 | **SG (60, REAL):** cement, steel_rebar, aggregate, sand, ready_mix_concrete - annual 2014-2025, SGD prices. **IN (120, REAL):** cement, steel_rebar, ready_mix_concrete - monthly 2023-04 to 2026-07, INR cost INDEX (2022-23 = 100) |
| `backend/data/benchmark_rates.csv` | 20 | the ten sections per market, rebuilt from the schedules of rates. **11 are SOR-derived** (`is_placeholder: false`, expressed at `base_quarter` 2026Q2): Singapore Excavation, Concrete, Reinforcement, Formwork, Masonry, Waterproofing and Plaster from the BCA Schedule of Rates May 2022; India Piling, Plaster, Waterproofing and M&E Containment from CPWD DSR 2021 Vol-II. **9 are retained estimates** (`is_placeholder: true`) for sections the loaded extracts do not reach. Generated by `make rates`; `make verify-seed` re-derives each of the 11 from the SOR lines it claims |
| `backend/data/regional_factors.csv` | 13 | 12 Indian cities plus Singapore, with the multiplier, its source and its limitations |
| `backend/data/sample_boq.csv` | 25 | Singapore demonstration BoQ, **built from real BCA schedule lines** (see below), SGD |
| `backend/data/sample_boq_india.csv` | 22 | India demonstration BoQ, **built from real CPWD DSR lines**, INR |

The Singapore material prices were re-verified against a fresh download of table M211671
(2025 is still the latest published year), and the Singapore CPI seed is current to **2026-07**, the
latest month the publisher has released. All of it is checkable in one command:

```bash
python tools/verify_seed_data.py
# [OK  ] Singapore CPI (M213751, All Items), publisher data to 2026-07: 55 value(s) checked, 0 mismatch(es)
# [OK  ] India CPI (MoSPI eSankhyiki API, bases [2012, 2024], published to 2026-07): 91 value(s) checked, 0 mismatch(es)
# [OK  ] Singapore material prices (M211671), publisher data to 2025: 60 value(s) checked, 0 mismatch(es)
# [OK  ] India WPI (Office of the Economic Adviser), complete quarters to 2026Q2: 78 value(s) checked, 0 mismatch(es)
# 284 published value(s) checked; 0 problem(s).
```

It re-derives every seeded real series from the publisher's own downloaded file and exits non-zero if
anything has drifted, so "the data is up to date" is a checked claim rather than a comment.

**One series is derived, not published.** `WPI-CONST` is the app's headline India series, and no
single published WPI row corresponds to it: it is a **weight-blended composite** of the published
group indices *Manufacture of cement, lime and plaster* (commodity code 1313050000, weight 1.68125)
and *Manufacture of basic iron and steel* (1314010000, weight 6.31601) - 21.02% cement / 78.98%
steel, which are the publisher's own commodity weights - averaged over the months of the calendar
quarter. The other five India series (`WPI-CEM`, `WPI-STL`, `WPI-CEM-OPC`, `WPI-STL-BARS`, `WPI-RMC`)
are single published rows. Each row's `provenance_note` says which of the two it is, and
`tools/verify_seed_data.py` rebuilds the composite from the workbook every time it runs.

The India index base is the WPI's own **2022-23 = 100**, and the India benchmark rates are stated
at base year 2023. Those line up by construction rather than by fudge: the WPI is 100 across the
2022-23 financial year, and the CPWD DSR 2023 schedule is priced at 2023 levels.

The India WPI series are **real**. Five of them are a single published row averaged to complete
calendar quarters; the sixth, `WPI-CONST`, is a weight-blended composite of two published group
indices (see above). Either way the derivation is recorded in each row's `provenance_note`, so a
reader can reproduce it from the source file without guessing.

A **base-year mismatch guard** exists in the engine: if the benchmark rate base year differs from
the index base year, the response carries a named warning saying the ratio is only valid if the
rate library has been rebased to the index base year. It never fails silently.

### The demonstration bills are real schedule lines

`tools/build_sample_boqs.py` builds both bills from the **real** schedules of rates, so the
demonstration runs on the vocabulary it will actually meet rather than on invented filler:

| field | source |
|---|---|
| description | **verbatim** from the BCA schedule or the DSR. Nothing is paraphrased |
| unit | from the schedule, normalised to the library's (`sqm` -> `m2`, `kg` -> `tonne`) |
| rate | the schedule's **own 2026 rate for that item** - not a section median, not a target variance |
| quantity | **indicative**. A schedule of rates carries no quantities; a bill needs them |
| section | whatever the classifier assigns, and any candidate where it disagrees with the schedule's own part is dropped |

For the nine sections whose library rate is a **retained estimate** - the ones the loaded extracts
do not reach - there is no published line to quote, so the bill line is a demonstration line too,
priced at a stated variance to the retained benchmark. The provenance of the description matches the
provenance of the rate: a real description against an invented rate would claim more than is known.

Two lines fall outside the canonical ten sections and one is measured in a unit the library does not
use, so the reclassify, manual-rate and exclusion paths are all visible. Those are real schedule
lines too (glazing, joinery, metalwork, a pile load test), not contrived filler.

Because a bill line is a **specific item** and the benchmark is a **section rate**, the two are not
expected to agree. That spread is the report.

Both sample BoQs are engineered so variance reporting is visibly exercised. Against the default
view for each market:

| | Singapore (BCA 2026Q2) | India (WPI-CONST 2026Q2, Delhi) |
|---|---|---|
| Lines | 25 | 22 |
| Over +15% | 4 | 5 |
| Under -15% | 3 | 8 |
| Unclassified | 2 | 2 |
| Unit mismatch | 1 | 1 |
| Not benchmarked | 3 | 3 |
| Total variance | +1.66% | -7.49% |

The bills are built from real schedule lines (see "The demonstration bills are real schedule lines"),
so those figures move whenever the schedule extracts or the rate library are refreshed. They are the
current numbers, measured through the API rather than remembered.

India reads **-7.49%** at the library quarter: the demonstration bill's rates are keener than the 2021
DSR escalated to 2026Q2, which is what benchmarking those lines against the schedule says. The
sensitivity view puts the same fact as a break-even: Singapore's variance turns at **+1.93%** of index
movement (index 144.97 from 142.23), India's at **-16.63%** (index 99.16 from 118.93).

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
| `POST` | `/api/boq/upload` | multipart CSV/XLSX -> parse, classify, persist. Query: `country`, `currency`. The response carries `sections_summary[]` (per section: lines, from the template, schedule wording, schedule items available, whether the library prices it) and `sor_catalogue` |
| `GET` | `/api/boq/template` | **the BoQ template: the market's whole schedule of rates plus its instructions sheet.** Query: `format` (`xlsx`, the default, or `csv` for the item list alone), `country`. 454 BCA SOR lines for Singapore, 1,876 CPWD DSR lines for India |
| `GET` | `/api/boq/{upload_id}` | parsed items for an upload |
| `PATCH` | `/api/boq/item/{item_id}` | manual reclassification; sets `classified_by = "manual"` |
| `POST` | `/api/boq/{upload_id}/benchmark` | body `{tender_quarter, tpi_series_name, variance_threshold, region_code, index_bridge, adjustments, manual_rates}`. `index_bridge` is `cpi` (default) or `none` |
| `POST` | `/api/boq/{upload_id}/sensitivity` | index sweep + per-section tornado + break-even |
| `GET` | `/api/boq/{upload_id}/export` | streamed export. Query: `format`, `level` (items\|sections\|waterfall\|summary\|**report**), plus optional benchmark params |
| `POST` | `/api/boq/{upload_id}/export` | same, but takes the full benchmark body **including index adjusters and the bridge setting** |
| `GET` | `/api/indices/tpi` | query: `country`, `series`, `from_quarter`, `to_quarter` |
| `GET` | `/api/indices/price-series` | query: `country`, `series`, `from_month`, `to_month`. Every monthly price observation behind the bridge, producer and consumer alike, each with `kind` (`PPI`\|`CPI`), `title` and `scope_sections`. **Renamed from `/api/indices/cpi`** when the table gained producer rows |
| `GET` | `/api/indices/coverage` | query: `country`. Section-by-section coverage: which published series re-prices each canonical section, its status, and the credible publication that would close each remaining gap |
| `GET` | `/api/indices/freshness` | query: `country`, `reference_quarter`. Last published quarter per series, its lag, and the CPI-bridged value the engine would use |
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
* recorded with `source: "Analyst-supplied manual rate"` and the analyst's note in the provenance,
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

### The upload template is the schedule of rates

There is **one template per market**, and its instructions travel inside it:
`GET /api/boq/template` (default `format=xlsx`) returns a workbook with a `BoQ` sheet and an
`Instructions` sheet. A 450-1,900 row sheet is unusable without an explanation of its columns, which
rows the library can price, and where the pre-filled rates came from, so the instructions are not a
separate download. (`format=csv` still returns the same item list without the instructions sheet, for
scripting.)

`backend/data/sor_items.csv` (built by `tools/build_sor_items.py` from the two published extracts) is
the committed catalogue of every item in both schedules:

| column | what it is |
|---|---|
| `sor_code` | the schedule item code. **Blank means the analyst added the line**, so the loaded schedule has no rate for it |
| `description` | verbatim from the schedule |
| `section` | the app's classification, **supplied so you can filter**. Blank = a trade outside the library's ten sections |
| `UOM` | **the unit the `rate` and the `quantity` are per**, in the app's own vocabulary: `m`, `m2`, `m3`, `t` or `item`. `unit`, `units` and `uom` are accepted as alternative spellings, but the sheet ships `UOM` |
| `published_uom` | the unit the schedule itself printed, verbatim. Kept beside `UOM` so a conversion is visible instead of implied |
| `uom_note` | blank when the two agree, otherwise it says what happened: `converted from the schedule's 'sqm' to the app's 'm2', and the rate was converted with it`, or `NOT COMPARABLE: ...`, or `NO UOM: ...` |
| `quantity` | **zero**. A schedule of rates has no quantities; an untouched template therefore totals zero on purpose |
| `rate` | the library rate for the section: the published schedule rate escalated to 2026Q2 by CPI, so a filled quantity analyses straight away. Source and quarter are on the row's neighbours (`source`/`base_quarter` in the library, the instructions sheet here); an analyst with tendered prices overwrites it |
| `currency` | **`SGD` or `INR` on every row**, so no rate can be read as the other market's money |

**No placeholder flag, and no TODO column.** The rate in the sheet *is* the rate the app benchmarks
that section against, derived from a named published schedule and stated at a named quarter, so the
sheet does not ask the analyst to replace it with something more real: `is_placeholder` and
`replace_with` are not columns. An earlier revision marked every row `is_placeholder = true` with a
`# TODO: replace with your own rate`, which contradicted the sheet's own rate and made a complete
library look provisional.

**The unit is stated, not implied.** A rate without its unit is not a rate, and the schedules write
their units their own way - `sqm`, `cum`, `kg`, `per metre span`, `litre`, `hour`. Every row therefore
carries three related facts: the unit the app will compare (`UOM`), the unit the schedule printed
(`published_uom`), and an explanation when they differ (`uom_note`). Measured on the shipped catalogue:

| | Singapore | India |
|---|---|---|
| Rows | 454 | 1,876 |
| `UOM` converted from the schedule's wording | 37 | 1,846 |
| Rows the app cannot compare (`NOT COMPARABLE`, unit carried verbatim) | 0 | 30 (`litre`, `per bag`, `cm per metre`, `per letter per cm height`, ...) |
| Rows whose schedule states no unit at all (`NO UOM`, cell left empty) | 1 | 0 |

A unit the app cannot compare is never silently converted or coerced into a comparable one, and a row
the schedule gives no unit for is **left empty rather than filled with a guess** - both are flagged in
words and both are excluded from benchmarking rather than quietly compared. The `Instructions` sheet
carries the same legend (`m`, `m2`, `m3`, `t`, `item` with their accepted spellings) under the heading
*UOM - THE UNIT YOUR RATE AND QUANTITY MUST BE IN*.

**Every description the extracts carry is listed** - 454 for Singapore and 1,876 for India, 2,330 in
total. An earlier revision dropped the rows it could not price, which silently removed 60 Singapore
items (no published rate) and 30 India items (a unit the app cannot compare) from a list whose whole
purpose is completeness. Those rows are now kept and flagged per row instead: a missing rate writes a
zero, and an unconvertible unit is carried verbatim - `litre`, `hour`, "per metre
span" - so the analyst can see what the schedule said and re-measure. Only a row with no description
at all is dropped, and the count is printed.

Of Singapore's 454 items, **169 fall in a section the library covers** and 285 do not; for India it is
**1,042 of 1,876**. The `section` column is what makes a 1,876-row sheet navigable: filter on it and
you have the lines the app can benchmark. The rest are trades the library has no section for -
glazing, painting, metalwork, roofing, joinery, finishes, demolition, repairs - and they are reported
as needing a manual rate rather than being silently dropped.

**Where the pre-filled rates come from**, stated on the instructions sheet as well:

| market | source | escalation | stated at | currency |
|---|---|---|---|---|
| Singapore | BCA Schedule of Rates, May 2022 | x 1.171 (cumulative CPI 2022 to 2026) | 2026Q2 | SGD |
| India | CPWD Delhi Schedule of Rates 2021 Vol-II | x 1.2364 (cumulative CPI 2021 to 2026) | 2026Q2 | INR |

Both extracts carry the same caveat - *validate with current market quotations* - and the template
repeats it. The escalation is the same technique the app uses to carry a stale index to the tender
quarter, disclosed for the same reason.

### The quarter and the currency the rates are stated at

The rates above are not "somewhere in 2026": every SOR-derived library row carries a `base_quarter`,
and for both markets it is **2026Q2**. `/api/countries` reports it per market, with the currency and the
counts that make it checkable rather than asserted:

| field | Singapore | India |
|---|---|---|
| `library_quarter` | 2026Q2 | 2026Q2 |
| `library_default_tender_quarter` | 2026Q2 | 2026Q2 |
| `library_sections_stated` | 7 of 10 | 4 of 10 |
| `library_sections_retained` | 3 (older base, still escalated) | 6 (older base, still escalated) |
| `library_currency` | SGD | INR |

**2026Q2 is therefore the benchmark quarter the app opens on**, and the frontend treats it as a floor
for its defaults: an upload saved before that quarter existed - or a market switch - cannot open the app
on an older quarter and escalate a schedule rate *backwards* without saying so. The analyst can still
pick any quarter deliberately; the selector does not fight a choice, and the rate-basis line then reads
*"benchmarking at 2026Q3 instead carries every library rate from 2026Q2 to 2026Q3 by the index ratio
between them, so each priced line takes one more modelled step."*

Benchmarking at the library quarter is materially different from benchmarking at an earlier one, and
that difference is the whole point of stating the quarter: at 2026Q2 the index ratio applied to a
library-stated rate is **exactly 1.000** (measured: `tpi_ratio = 1.0`, `rate_base_quarter = 2026Q2` on
both markets' seeded samples), so the rate is used as published. The *index comparison* is still
bridged - the last published BCA observation is 2024Q4, so the index itself is carried forward to
2026-06 with CPI-ALL (x1.021772, 139.20 -> 142.2307) - but no rate is escalated a second time.

The rate-basis line in the benchmark panel states this in one sentence per market, and it changes with
the selected quarter: at 2026Q2 it reads *"prices the schedule rates as they are published: the index
ratio from the library quarter is 1.000"* (`LIBRARY AS PUBLISHED`), and at any other quarter it reads
*"carries every library rate from 2026Q2 to <quarter> by the index ratio between them"*
(`DERIVED - RATE ESCALATED FROM THE LIBRARY`). The upload panel states the same basis before you even
download the template, including the section counts and the source date.

A file that mixes coded and uncoded lines gets a warning naming how many are not in the schedule,
because those are the lines that need input before the should-cost is complete. A file with no
`sor_code` column at all is not nagged: every line is codeless, so saying so would be noise.

The two bundled demonstration bills carry their schedule codes too, so a demo line traces back to the
BCA or DSR item it was quoted from.

### The sections summary factors the schedule in

Uploading a bill reports a per-section summary, and it is built from the schedule of rates rather than
from the section counts alone:

| field | meaning |
|---|---|
| `lines` | how much of the bill sits in this section |
| `from_sor_template` | lines carrying a schedule code, i.e. taken from the template |
| `matched_sor_description` | lines that **are** a schedule item's own wording, whether or not the code column survived the edit |
| `sor_items_available` | how many schedule items this market holds for the section - the ceiling on coverage |
| `sor_items_bookable` | of those, how many are in a unit the app can compare |
| `benchmark_rate_available` | whether the rate library can price the section at all |

A description that matches a schedule item exactly takes **that item's section**, before the keyword
rules run, so a bill built from the template always lands in the section the template advertised. The
same summary comes back when an upload is reopened, and the variance table's section headers repeat
the fact per section (`12 line(s) · 9 from the schedule of rates`), with each line flagged
`schedule item <code>`.

* **BoQ template** - one XLSX per market, instructions built in. Round-trip tested: the template
  uploads and every line keeps its schedule section (`test_template_csv_downloads_and_round_trips`,
  `test_sor_template.py`).
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
`base_year`, `scope_inclusions`, `scope_exclusions`, `confidence`, `source_url`) plus the index
actually applied. Provenance names where the rate came from; the line's `basis` (measured, derived or
assumed) says what kind of figure it is. The response ships no placeholder flag and no
`replace_with` instruction.

### Waterfall reconciliation

```
boq_total + material + labour + market_risk + cpi_bridge + scope + overhead + margin + unexplained
  = full_should_cost_total
```

| component | basis | method |
|---|---|---|
| `market_risk` | derived | `sum(quantity x base_rate x (published_tpi_ratio - 1))` - movement in the last **published** observation, plus any analyst index shift |
| `cpi_bridge` | **assumed** | `sum(quantity x base_rate x (index_ratio_used - published_tpi_ratio))` - the modelled step to the tender quarter. Exactly zero when the index already covers it |
| `scope` | derived | `sum(quantity x base_rate x tpi_ratio x (scope_factor - 1))` |
| `overhead` | **assumed** | `sum(quantity x adjusted_rate) x overhead_pct/100` - the analyst's overhead percentage. Zero when none was supplied |
| `margin` | **assumed** | `(sum(quantity x adjusted_rate) + overhead) x margin_pct/100` - applied after overheads, i.e. compounded. Zero when none was supplied |
| `material` | **assumed** | `rate_gap x 0.55` |
| `labour` | **assumed** | `rate_gap x 0.45` |
| `unexplained` | derived | residual that closes the identity exactly; ~0.00 when every line is benchmarked |

The chart closes on `full_should_cost_total`, which equals `should_cost_total` when no overheads or
margin were supplied - in that case both extra steps are exactly `0.00` and the run is numerically
identical to before they existed.

`rate_gap = sum(quantity x (base_rate - boq_rate))` over benchmarked lines. The 55/45 split is an
**apportionment with no measured basis** and is returned in `assumptions[]` with its justification.
Replacing it with a measured rate build-up is the highest-value next step - see below.

`market_risk + cpi_bridge` is **identical** to the single market-risk figure a non-bridged run
produces: `cpi_bridge` is computed as the rounded difference of the same total, so the split changes
disclosure and nothing else. The bridge is reported as its own bar (teal, amber label) and its own
row in the reconciliation table. The index ratio is used at **full precision** in the waterfall (the
`tpi_ratio` in the response is the rounded display value), so a bridged run reconciles exactly as
tightly as an observed one - asserted by `test_bridged_runs_reconcile_with_a_negligible_residual`.

---

## Classifier

Case-insensitive keyword match on `raw_description`, first matching rule wins, in this order:

```
concrete|grade \d+                     -> Concrete
rebar|reinforcement|steel bar          -> Reinforcement
formwork|shutter                       -> Formwork
brick|blockwork|blocks|masonry         -> Masonry
plaster|render|screed                  -> Plaster
excavate|excavation|\bdig              -> Excavation
\bpile|\bpiling|\bbored                -> Piling
waterproof|membrane                    -> Waterproofing
conduit|containment|trunking           -> M&E Containment
preliminar|site setup|insurance        -> Preliminaries
```

Anything unmatched becomes **`Unclassified`**. Unclassified lines are surfaced in the variance
table with an inline dropdown that issues `PATCH /api/boq/item/{item_id}` and re-runs the benchmark
immediately. Nothing is silently bucketed.

A description that **is** a schedule item takes that item's section before the rules run (see "The
sections summary factors the schedule in"), so the template's own vocabulary is authoritative for the
template's own lines.

### Guards: the work measured is not always the section it mentions

A trade rule can fire on a keyword that names the *substrate*, the *subject* or the *thing being
removed* rather than the work. Every section therefore carries a guard, and a rule whose guard
matches stands down while the search continues down the table:

| one guard, shared by every section | because the work measured is |
|---|---|
| `painting`, `paint`, `polish`, `varnish` | decorating |
| `demolishing`, `demolition`, `dismantling`, `chipping`, `raking out`, `grinding` | stripping out |
| `repair`, `repairs`, `rehabilitation` | remedial |

plus two section-specific ones: **Concrete** stands down when the line names pipework, cable, a
sanitary fitting, formwork or masonry, and **Masonry** stands down when the work is plastering or
screeding on the masonry.

The schedule catalogue is what exposed how much this mattered - 284 items were priced under the wrong
section, and each family was verified by listing every move:

| was classified as | is now | example | items |
|---|---|---|---|
| M&E Containment | outside the ten | `Painting on rain water, soil waste and vent pipes ... bitumastic paint` | 90 |
| Concrete | outside the ten | `Finishing with Epoxy paint (On concrete work)`, `Demolishing lime concrete` | 27 |
| Concrete | M&E Containment | `Providing and fixing ... Stainless Steel Fitting of press fit design of grade 316L` | 114 |
| Concrete | Masonry | `Hollow concrete block laid in cement mortar (1:4)` | 17 |
| Masonry | Plaster | `15 mm cement plaster on the rough side of single or half brick wall` | 12 |
| Masonry / Plaster / Formwork / Waterproofing | outside the ten | `Demolishing brick work`, `Dismantling old plaster`, `Repairs to plaster` | 20 |
| Concrete | Plaster | `Plastering in cement and sand (1:4) mortar` | 6 |
| Piling | outside the ten | `Demolishing R.C.C. work ... and stockpiling at designated locations` | 1 |

Two of those are worth naming as bugs rather than judgement calls. The 114 stainless-steel fittings
matched because the Concrete rule reads `grade \d+` and the description says **grade 316L** - a steel
grade, not a concrete one. And the demolition item matched because `piling` is a substring of
**stockpiling**, which is why the Piling rule is now word-bounded. The app has no Painting,
Demolition or Repair section, so those rows fall outside the ten and are reported as needing a manual
rate - which is the honest outcome: a rate library with no Painting item cannot price painting.

**Known consequence of first-match-wins:** `"Waterproof membrane to pile cap tops"` classifies as
**Piling**, because the Piling rule precedes the Waterproofing rule and the guard does not stand it
down. This is asserted in `test_first_match_wins_is_documented_caveat` so that changing the order is a
deliberate act, and it is exactly why manual reclassification exists.

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
5. **Index dashboard** - the country's index series, a **data-currency table** (last published quarter,
   lag, bridged-to month, bridge factor and the index value actually used, per series), a chart of the
   monthly **CPI** behind the bridge, a material price trend with a display-only scenario slider, the
   benchmark rate library with provenance and TODO markers, and the credible sources for that market.
6. **Download** - BoQ template (CSV/XLSX), benchmark exports at four levels, and the **full CSV report**.

A **region selector** appears beside the index series whenever the market has more than one region
(India has twelve; Singapore has one, so it is hidden). Changing it re-runs the benchmark live and
the should-cost tile shows the region and its multiplier.

The index dashboard separates **published** data from data that is **derived** or an **indicative
seed**, with a count of each and a per-series badge, so you can see at a glance which numbers came
from a government publication, which the engine computed from them, and which are still waiting to be
licensed. Its **section coverage** table goes further and states, for every canonical section, which
published series re-prices it and where the gap is - including the structural gap that no price index
measures site labour.

### Index currency in the UI

* The **tender quarter selector** offers every quarter up to the current calendar quarter, not just
  the ones the index has published. A quarter beyond the last published observation is labelled
  `2026Q3 - index carried forward with PPI` (or `with CPI` for a market with no usable producer
  index), or `- index held, 7q stale` when bridging is switched off.
* A **Stale index** selector next to it chooses between *carry forward with PPI, CPI as fallback*
  (the default), *PPI only*, *CPI only*, and *hold the last published observation*.
* The **benchmark parameters** panel states the published quarter, the lag, which price index was used
  and of what kind, the months and values at each endpoint, the factor, and the resulting index level -
  before you read any number derived from it.
* The **index currency tile** on the results grid shows the last published quarter and what it was
  carried forward to.
* Every bridged line in the **variance table** is flagged *index carried forward* and, when expanded,
  shows which kind of price index was used, the published value, the derived value, the month used, the
  factor and the source URL.
* The **waterfall** carries the bridge as its own step, and the row explains in words what it is.
* A newly uploaded BoQ is priced at the **quarter its market's rate library is stated at** (2026Q2 for
  both markets) by default, because the template's pre-filled rates are schedule rates escalated to
  that quarter and benchmarking there applies an index ratio of exactly 1.000 rather than escalating
  them a second time. A saved upload opens on the quarter it was last benchmarked at, but never on one
  *older* than the library quarter: that would deflate a rate the schedule already states. Any quarter
  remains selectable by hand, and the rate-basis line says which of the two is happening.

### Collapsible sections

Two sections hold reference material rather than results, and both fold away at the user's request.
Collapsing is never silent - each keeps a summary strip on screen:

* the **Assumptions & warnings** panel (every apportioned or modelled figure stays in visible text,
  never a tooltip, but you are no longer made to scroll past the same warnings on every re-run). One
  **Collapse / Expand** control folds the body away and the choice is remembered in `localStorage`;
  the header keeps the live counts ("6 warning(s), 7 assumption(s)") and the amber chips for
  *index carried forward with PPI* and *index adjusters active*,
  with a one-click link back to the full text. The **Warnings** and **Assumptions** lists also collapse independently.
* the **Classification rules** table (the effective rule table for the selected market). Collapsed,
  it still shows the measurement standard, the rule count and an
  *automated classification - review it* chip, so which vocabulary is in force - and that a
  first-match-wins keyword matcher placed every line - is never hidden. Its state is remembered
  separately from the assumptions panel.

Every section carries a **"How to use this" pop-out** - a floating instruction card that stays open
while you work, rather than a tooltip that vanishes when you move the mouse. There are nine of them:
upload, benchmark parameters, index adjusters, coverage, variance table, waterfall, sensitivity,
index dashboard and download. Each ends with the caveats that matter for that view.

A **market switcher** sits above the tabs. Changing market reloads that market's index data, resets
to its default series and latest quarter, and auto-loads its seeded sample BoQ.

The **index adjusters panel** appears on every benchmark view. It re-runs the benchmark live
(300 ms debounce) as you move a slider, so the effect on variance is immediate. It carries the index
shift, the all-rates shift, the per-section shifts, the absolute index override, and the two
**overheads and margin** percentages that build the full should-cost (with a live readout of the
figure the engine computes). It can also be set straight to the break-even level once a sensitivity
run exists.

Tender quarter, index series and the variance threshold are selectors that re-run the benchmark
live.

### Theme and responsive behaviour

Light blue throughout: a gradient page background, white cards at 20px radius, soft blue shadows,
pill-shaped tabs, chips and badges, and rounded inputs. Colours are driven by CSS custom properties
at the top of `styles.css`, so the whole palette can be re-themed in one place.

**Navigation collapses to a hamburger below 901px.** The six views are a horizontal pill row on a
desktop and a panel behind a toggle in a sticky app bar on a phone, because six pills at 360px either
scroll off screen or shrink below a tap target. The toggle carries `aria-expanded` and
`aria-controls`; the panel closes on a view choice, on Escape, on a backdrop tap, and on growing the
window past the breakpoint (so an open state cannot outlive the panel's reason to exist). The panel is
`position: fixed` under the sticky bar, so opening it after scrolling 1,800 rows puts it where the
thumb is, and every item is a 46px tap target. The market and API status line sits in the panel on a
phone, where the header has no room for it, and is hidden on a desktop where the header pills show it.

**Nothing pushes the page sideways.** Several things conspired to make text overrun on a narrow
screen, and each is fixed at the cause rather than hidden:

* grid and flex children get `min-width: 0`, so one unbreakable string - a schedule code, a source
  URL, a supplier's raw description - cannot widen its panel past the viewport;
* text-bearing containers wrap (`overflow-wrap: break-word`), and codes, URLs and `code` spans break
  anywhere;
* table headers wrap on a phone instead of forcing a `min-width` (a header that cannot wrap is a
  header that widens the table);
* every wide table sits in `.table-scroll`, which scrolls inside its own box with an inset right-edge
  shadow as the only affordance - the Waterfall reconciliation table was the one table outside a
  scroller, and it dragged the whole page to 747px on a 320px phone until it was wrapped;
* badges, flags and section pills stop being `nowrap` on a phone, so a long section name wraps rather
  than running off the card.

Mobile behaviour is verified in a real browser rather than asserted, at six widths, over all six
views, with two measurements per view: the document may not be wider than the **viewport** (not
`window.innerWidth`, which silently widens with the content under emulation and would report a
320px phone that scrolls sideways as a pass), and no text may be clipped inside a box it cannot
scroll (`scrollWidth > clientWidth` on a leaf with `overflow: visible`).

| Width | Result |
|---|---|
| 1440px | full desktop layout: tab row visible, no hamburger, tiles in a grid, all tables visible |
| 768px | hamburger navigation; every view fits; no clipped text |
| 414px | ditto, market chips and controls stack full width |
| 390px | ditto - page overflow **0px** on all six views |
| 360px | ditto - page overflow **0px** on all six views |
| 320px | ditto - the narrowest phone still renders every view without side-scrolling |

`prefers-reduced-motion` is honoured. The app bar is 61px tall at every phone width, so the fixed
panel always lands directly under it, and the toggle drops its label below 400px to keep the bar on
one line.

The backend URL is read **exclusively** from `import.meta.env.VITE_API_BASE_URL` in
`frontend/src/api.js`. `http://localhost:8000` appears exactly once, as the documented local-dev
fallback used when the variable is unset. Because Vite inlines this at build time, **changing it on
Vercel requires a redeploy**.

---

## Testing

```
cd backend && python -m pytest tests -v      # 361 tests

# The same suite passes against real PostgreSQL - the engine production runs:
docker compose up -d --build
docker compose exec -T \
  -e SHOULDCOST_TEST_DATABASE_URL=postgresql://shouldcost:shouldcost@db:5432/shouldcost_test \
  backend python -m pytest tests -q
docker compose down -v
```

**The PostgreSQL leg was not re-run for this change.** The Docker daemon was not running in this
environment, so the PostgreSQL 16 record further down is from the previous build. The CPI bridge adds
one table (`cpi_series` - plain columns and a unique constraint, no dialect-specific types) and no
raw SQL, so nothing in it is engine-specific; run the block above to re-confirm where Docker is
available.

| file | tests | covers |
|---|---|---|
| `tests/test_classifier.py` | 31 | every SG keyword family, case-insensitivity, unclassified handling, rule-order caveat |
| `tests/test_benchmark.py` | 28 | index up, index down, scope_factor != 1.0 with Piling present, missing-quarter fallback, unclassified handling, unit mismatch, reconciliation, provenance completeness |
| `tests/test_api.py` | 29 | upload/classify/patch, benchmark reconciliation within 0.01, threshold breaches, RLB scope warning, export bytes, health check, CORS posture, index endpoints |
| `tests/test_multi_country.py` | 34 | market registry and credible sources, India vocabulary, WPI behaviour, per-market index isolation, INR amounts, country-scoped endpoints, **every row carries provenance and no benchmark rate ships a placeholder flag or a TODO** |
| `tests/test_adjustments_sensitivity.py` | 33 | every adjuster, override/scale composition, per-section isolation, basis and assumption disclosure, sweep monotonicity, break-even, tornado ranking, validation limits, template round-trip, all four export levels |
| `tests/test_regions_report_import.py` | 26 | region defaults and errors, linearity of the multiplier, basis/assumption disclosure, region through sensitivity, all nine report blocks, report reconciliation, **importer provenance enforcement and idempotency**, and spot-checks of the real WPI values against the published series |
| `tests/test_manual_rates.py` | 20 | coverage gap is real and closes to 100%, manual rates are tagged assumed and never from the library, indexed vs stated-at-tender behaviour, region applies only to indexed rates, scope exclusions still win, unusable rates ignored, reconciliation holds, sensitivity carries them, and the report counts them |
| `tests/test_overheads_margin.py` | 16 | overheads and margin off by default changing nothing, the compounded arithmetic (`1.12 x 1.06`, not `1.18`), totals adding benchmark + overheads + margin, the waterfall closing on the full total, section aggregates carrying the full cost, assumed basis plus flags plus the stated compounding, one-percentage-only runs, the full-to-full default comparison and the net-of-OHP alternative, unbenchmarked lines NOT being grossed up, sensitivity and export coverage, validation limits, and the full cost stacking correctly with a region and a CPI bridge on an India run |
| `tests/test_cpi_bridge.py` | 32 | **the CPI seeds are real published data** with spot checks against SingStat table M213751 and the MoSPI API, the bridge arithmetic, the months used and the partial-quarter case, **the bridge never running past the latest published CPI month**, bridged lines being assumed and flagged, the bridge as its own waterfall step, market-risk + bridge equalling the unbridged market risk, **a bridged run reconciling as tightly as an observed one**, the off switch, the no-CPI-market degradation, the unknown-preferred-series fallback, **India's published back-cast spanning the rebase, and the older base being used only when the current one cannot span**, override precedence, scope cancellation under a bridge, sensitivity baseline equality, the report and summary exports, and the new endpoints |
| `tests/test_sor_template.py` | 19 | **the template lists every schedule item** (454 / 1,876, unique codes, no description dropped), rows with no published rate and odd units are kept and flagged, **every catalogue description still classifies to its stored section** (the drift tripwire behind the sections summary), the sections summary accounts for every item, exact-normalised description matching, one XLSX with instructions stating the source and escalation of the pre-filled rates plus the sections summary, the CSV variant being the item list alone, an upload from the template keeping each line's schedule section, the sections summary separating hand-written lines from schedule lines, reopening an upload keeping its summary, benchmark lines carrying their schedule code, and the classifier guards against the mis-fires the catalogue exposed |

Tests run against a throwaway SQLite file created in a temp directory by `tests/conftest.py`, which
sets `DATABASE_URL` **before** importing any application module. The developer database is never
touched by the suite.

The bridge arithmetic is asserted against the **publisher's own numbers**, not against a stored
copy of itself: `test_singapore_cpi_values_match_the_published_table` pins 2024-10, 2024-12, 2026-06
and 2026-07 to the values in SingStat table M213751, and
`test_stale_index_is_carried_forward_by_the_observed_cpi_move` recomputes the factor from those rows.
If the seed is refreshed, those tests are the tripwire that says so.

### Rendered UI verification

Because vision is unavailable in this environment, the frontend was verified by driving the **built**
production bundle (`vite preview`) in headless Chrome and asserting on the rendered DOM, instead of
by inspecting screenshots. Against the seeded samples:

```
DESKTOP - Singapore, auto-loaded on boot, at the quarter the rate library is stated at
  header status      : API ok | db connected | development | Singapore | SGD
  market bar         : SMM2 - Metric SI. Rates are per m, m2, m3, tonne or lump sum.
  rate basis         : "Rate basis: the rate library is stated at 2026Q2 in SGD (2026-06-30).
                        Benchmarking at that quarter prices the schedule rates as they are
                        published: the index ratio from the library quarter is 1.000, so they are
                        not escalated again. LIBRARY AS PUBLISHED. 7 of the library's 10 sections
                        state that quarter; the other 3 are retained values from an older base and
                        are still escalated from it."
  KPI tiles          : BoQ S$3,043,410.50 | should-cost S$2,993,828.58 | variance +S$49,581.92 (+1.66%)
                       7 of 25 breaching | 3 not benchmarked (S$34,839.00)
                       index currency 2024Q4 - carried forward to 2026-06 with CPI-ALL (x1.0218)
  variance table     : 41 rows, 11 section subtotals
  waterfall          : 3 recharts surfaces, reconciled; CPI bridge row S$15,395.75 (basis assumed)
  sensitivity run    : break-even +1.93% (index 144.97 from a baseline of 142.23 on the seeded
                       sample), most sensitive section Formwork (swing S$153,109.40 across the
                       10-section tornado), 9 sweep points on the index
  index adjuster     : variance -S$259,302.53 (-7.85%) after moving the index +12%
                       assumptions 7 -> 8, adjusted lines flagged basis=assumed
  template download  : "Downloaded shouldcost-boq-template-sg.xlsx" (one button, instructions inside)

DESKTOP - the same BoQ priced at 2026Q3, a quarter no construction index has published
                       (Singapore has no usable producer index, so this market falls back to CPI)
  tender quarter list: 2026Q3 .. 2023Q1 (quarters beyond the last observation are selectable
                       and labelled "index carried forward with CPI")
  rate basis         : "...Benchmarking at 2026Q3 instead carries every library rate from 2026Q2 to
                       2026Q3 by the index ratio between them, so each priced line takes one more
                       modelled step. DERIVED - RATE ESCALATED FROM THE LIBRARY"
  index tile         : index currency 2024Q4 - last published observation, carried forward to 2026-07
                       with CPI-ALL (x1.0230)
  benchmark panel    : "the BCA series last published 2024Q4 - 7 quarter(s) before 2026Q3. It was
                       carried forward to 2026-07 along the published trend of CPI-ALL (consumer
                       price index, 2024-10-2024-12 100.389 -> 2026-07 102.696), a factor of 1.0230:
                       index 139.20 becomes 142.40. DERIVED - BASIS ASSUMED"
  KPI tiles          : BoQ S$3,043,410.50 | should-cost S$2,996,873.36 | variance +S$46,537.14 (+1.55%)
  line detail        : "Index carried forward with the consumer price index. Published observation
                       139.20 at 2024Q4 (7 quarter(s) stale) was carried forward to 2026-07 along the
                       published trend of CPI-ALL (CPI) = 102.696, a factor of 1.0230. The result is
                       derived to show the trend to date ... basis: assumed"
                       tpi_ratio 1.001183, rate_base_quarter 2026Q2 - one quarter of index movement
                       on top of the library rate
  waterfall          : reconciled; chips BoQ measured | Material assumed | Labour assumed |
                       Market risk derived | CPI bridge assumed | Scope derived | Unexplained derived
                       bridge row: S$19,029.75, basis assumed, method "sum(quantity x base_rate x
                       (index_ratio_used - published_tpi_ratio))"
  index dashboard    : "Data currency and the index bridge" table (4 series: last published, lag,
                       carried forward with, factor, index used), section-coverage table (11 rows,
                       all "no published series" for Singapore), CPI chart, 14 recharts surfaces
  assumptions panel  : Collapse -> aria-expanded false, body unmounted, strip keeps
                       "Show 6 warning(s) and 7 assumption(s)";
                       remembered across a reload; Warnings and Assumptions blocks each collapse
  rules table        : Classification rules - SMM2, 10 rules, first rule
                       "Concrete | concrete OR grade <number>"; Collapse -> aria-expanded false,
                       table unmounted, strip keeps "10 RULE(S)" + "AUTOMATED CLASSIFICATION - REVIEW
                       IT"; remembered across a reload

DESKTOP - overheads 12% and margin 6% set on the adjusters panel
  adjuster readout   : "Full should-cost: $2,993,828.58 benchmark cost grossed up by 12.0% overheads
                       and then 6.0% margin = $3,547,751.44 (the engine's own figure: benchmarked
                       lines only...)"
  KPI tiles          : should-cost $2,993,828.58 labelled "(benchmark cost) ... before overheads and
                       margin" | FULL SHOULD-COST (INCL. OH&P) $3,547,751.44
                       "+$355,078.75 overheads (12.0%) + $198,844.10 margin (6.0%) | compared
                       full-to-full" | VARIANCE -$504,340.94 (-14.22%)
  variance footer    : "Full should-cost - benchmark $2,993,828.58 + $355,078.75 overheads (12.0%)
                       + $198,844.10 margin (6.0%) | OH&P | -14.22% | -$504,340.94 | $3,547,751.44"
  line detail        : "Full cost: overheads 12.0% and margin 6.0%. Benchmark rate $... becomes
                       $... per m3 (margin compounded on overheads)... The variance above is
                       measured full-to-full against that rate."
  waterfall          : "Reconciled: BoQ total + adjustments = full should-cost total, within 0.01 SGD"
                       chips BoQ measured | Material assumed | Labour assumed | Market risk derived |
                       CPI bridge assumed | Scope derived | Overheads assumed | Margin assumed |
                       Unexplained derived | Full should-cost assumed
                       Overheads row $355,078.75, Margin row $198,844.10, closing row
                       "Full should-cost total $3,547,751.44" basis ASSUMED
  untick "include OH&P": variance tile reverts to +$49,581.92 (+1.66%) and the full tile reads
                       "variance excludes OH&P" - the full cost is unchanged

DESKTOP - switched to India, at the quarter the rate library is stated at (2026Q2)
  market bar         : IS 1200 / CPWD DSR - rates in Indian Rupees
  rate basis         : "Rate basis: the rate library is stated at 2026Q2 in INR (2026-06-30) ...
                        LIBRARY AS PUBLISHED. 4 of the library's 10 sections state that quarter; the
                        other 6 are retained values from an older base and are still escalated."
  KPI tiles          : BoQ INR 6,25,30,109.50 | should-cost INR 6,75,96,223.99
                       variance -INR 50,66,114.49 (-7.49%) | 13 of 22 breaching
  index dashboard    : 20 rate-library rows

DESKTOP - switched to India, priced at 2026Q3 (a quarter the WPI has not published)
  market bar         : IS 1200 / CPWD DSR - rates in Indian Rupees
  tender quarter list: 2026Q3 labeled "index carried forward with PPI"
  benchmark panel    : "the CPWD series last published 2024Q4 - 7 quarter(s) before 2026Q3.
                        It was carried forward to 2026-07 along the published trend of PPI-ALL
                        (producer price index, 2024-10-2024-12 101.767 -> 2026-07 109.900), a factor
                        of 1.0799: index 110.50 becomes 119.33. DERIVED - BASIS ASSUMED"
  KPI tiles          : BoQ INR 6,25,30,109.50 | should-cost INR 6,76,98,218.36
                       variance -INR 51,68,108.86 (-7.63%) | 13 of 22 breaching
                       index currency 2024Q4 - carried forward to 2026-07 with PPI-ALL (x1.0799)
  index dashboard    : "Data currency and the index bridge" table plus a section-coverage table
                       (11 rows, 10 of them "producer index"), a 16-basket producer table with the
                       preferred PPI-ALL charted, and a two-series CPI fallback table
                       (CPI-ALL base 2024 preferred, CPI-ALL-2012 fallback)

DESKTOP - switched to India before the CPI was loaded (the degradation path)
  benchmark panel    : "...no bridge was applied (no consumer price observations are loaded).
                       Those lines are held at the last published index level."
  index tile         : held at the last published observation, 1 quarter(s) stale
  index dashboard    : "No consumer price series is loaded for this market, so a stale index cannot
                       be bridged... Import one with python -m app.importer --kind cpi."

DESKTOP - the upload view: one template, and a sections summary that names the schedule
  template buttons   : ["Download the Singapore BoQ template (.xlsx, 2 sheets: the schedule of
                        rates + instructions)"] - exactly one, previously two (.csv and .xlsx)
  click result       : "Downloaded shouldcost-boq-template-sg.xlsx"
  template via API   : csv 553,287 bytes | default download is the XLSX media type
  upload from it     : 14 lines, 0 unclassified, 14 carrying a schedule code
  sections summary   : heading + intro "The Singapore schedule of rates holds 454 items; 169 fall in
                        a section the rate library prices and 285 are outside the ten sections
                        (painting, glazing, metalwork, roofing, joinery, finishes, demolition,
                        repairs) and need a manual rate. This table shows how your lines map onto it.
                        Schedule: BCA Schedule of Rates, May 2022, escalated to 2026 by CPI."
                       columns SECTION | LINES | FROM TEMPLATE | SCHEDULE WORDING | SCHEDULE ITEMS |
                        RATE LIBRARY
                       rows e.g. "Excavation 4 3 4 16 PRICED", "Unclassified 2 0 2 0 MANUAL RATE
                        NEEDED", "M&E Containment 1 0 0 7 PRICED"
  variance table     : section headers carry the same fact per section -
                       "Concrete 2 line(s) · 2 from the schedule of rates",
                       "Excavation 4 line(s) · 3 from the schedule of rates · 1 not benchmarked";
                       lines flagged "schedule item III"

MOBILE 320/360/390/414px: hamburger shown, panel closed on load, opens on tap (6 tabs, 46px each),
                       Escape and backdrop close it, choosing a view navigates and closes it,
                       app bar 61px and one line; all six views: page overflow 0px, no clipped text
TABLET 768px   : hamburger navigation, all six views fit, no clipped text
DESKTOP 1440px : no hamburger, tab row visible with all six views
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

The end-to-end proof the production path works (recorded on the previous build; the PostgreSQL leg
was not re-run for the CPI bridge - see Testing):

```
postgresql dialect, ENVIRONMENT=production, CORS = ['http://localhost:4173'], no wildcard
ETL on Postgres      : 126 tpi_series, 180 material_prices, 20 benchmark_rates, 13 regional_factors
India benchmark      : boq 62,530,110 | should-cost 66,999,123 | variance -6.67% (Mumbai) | reconcile 0.0
full test suite      : 205 passed on PostgreSQL 16  (and 253 passed on SQLite for this build)
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
15. **The regional multipliers are indicative seed values.** They are the one number in this build
    that is both influential and not published in machine-readable form. Every row names the source it
    must come from, a non-1.0 factor raises a warning on every run, and every line it touches is
    `basis: assumed`. The published index series are real, the figures the engine derives from them are
    labelled as derived, and the indicative seed values are labelled as indicative - no row is
    presented as something it is not.
16. **Schema changes need a reset.** `create_all()` adds missing tables but never ALTERs an existing
    one, so a new column is invisible until the table is rebuilt: `python -m app.etl --reset` (or
    `make reset-db`). That drops every table, so **uploaded BoQs are destroyed** - reference data is
    reproducible from the CSVs, uploads are not. A real deployment wants a migration tool.
    `cpi_series` was added as a NEW table, so a plain `python -m app.etl` picks it up without a reset;
    only a change to an existing table's columns needs one.
17. **The index bridge is a modelled step, and it is labelled as one everywhere.** No price index is
    the construction cost index itself. Rather than hold a stale index (which under-prices every
    recent tender) or silently extrapolate the index's own trend (which invents a trend the publisher
    never published), the app carries the last observation forward along the movement in a **real,
    dated, published** price index and marks every affected line `basis: assumed` with the
    `index_bridged` flag. A **producer** price index is used first because it prices the commodity
    baskets a construction rate is built from; a consumer index is the fallback, and the response
    reports which kind was used (`kind` in `index_bridge`, `index_bridge_kind` per line) so the
    strength of the assumption is visible rather than implied. The bridge is its own waterfall step so
    it can never be mistaken for the observed market movement, and it can be switched off. Asserted by
    the `test_cpi_bridge.py` suite.
18. **The bridge stops at the last published month of the series it is using rather than
    extrapolating.** If the tender quarter is later than the index itself, the index is carried
    forward only as far as real data allows, `shortfall_months` reports the gap, and a warning says the
    index is derived to a month short of the quarter end. Inventing the missing months is exactly the
    failure mode this whole feature exists to avoid.
19. **An absolute index override replaces the bridged level, not the published one.** So the analyst
    always wins, and the bridge step in the waterfall falls to exactly zero when an override is set -
    a fully replaced index has no modelled component. Asserted by
    `test_absolute_override_replaces_the_bridged_level`.
20. **The quarter selector offers quarters the index has not published.** Pricing a live tender means
    pricing the current quarter, so those quarters are selectable and labelled
    `- index carried forward with PPI` or `with CPI` (or `- index held, Nq stale` when the bridge is
    off). A newly uploaded
    BoQ opens on the quarter its rate library is stated at (2026Q2), which is the quarter the
    documented demo numbers are measured at; an upload saved on an older quarter is lifted to the
    library quarter rather than priced below it, and a deliberate choice of any quarter is respected.
21. **Overheads and margin compound, and the choice is asserted rather than described.** Margin is
    applied after overheads (`1.12 x 1.06 = 1.1872`), which is the usual commercial convention. The
    alternative - adding the two percentages - would understate the full cost, so the compounding is
    stated in `assumptions[]` on every run and pinned by a numeric test.
22. **Overheads and margin are never applied to unbenchmarked lines.** Those lines are carried at the
    tendered rate, which already includes the contractor's own OH&P; grossing them up would
    double-count it. They therefore contribute zero overhead and zero margin, and the assumption text
    says so.
23. **One blended OH&P pair, not a per-section split.** Overhead recovery genuinely differs between
    trades - preliminaries and M&E rarely carry the same percentage as structural work. The single
    pair is a simplification, disclosed in `assumptions[]` on every affected run, and the obvious
    next refinement.
24. **`overheads_in_tender` changes the comparison, not the cost.** With it set the default way, the
    tendered rates are taken to include OH&P and every variance is full-to-full. Set it false and the
    variance reverts to the benchmark rate before overheads while the full should-cost is still
    reported. Either way the full cost is identical - only what it is compared against moves, which is
    exactly why the two figures are reported separately (`should_cost_total` and
    `full_should_cost_total`).
25. **One template, with its instructions inside it.** The upload view offers a single download: the
    XLSX whose second sheet explains the columns, the rate provenance and the sections summary. The
    CSV variant still exists on the API as the item list alone, because scripting a 1,876-row sheet
    through a browser button is not a workflow anybody wants; it is not offered in the UI, where two
    template buttons were just a choice between the same file with and without its instructions.
26. **Every schedule description is listed, including the ones the app cannot price in one go.**
    Missing rates become a zero, and a unit the app cannot compare (`litre`,
    `hour`, "per metre span") is carried verbatim and flagged so the row is reported as a unit
    mismatch rather than dropped. Dropping them made the template shorter than the schedule it claims
    to be - 60 Singapore and 30 India items - which is exactly the kind of quiet omission this app
    exists to avoid.
27. **A line whose description IS a schedule item is classified from the schedule, not by keyword.**
    The catalogue's section for that description is authoritative, so a bill built from the template
    always lands where the template said it would. Free text still goes through the keyword rules, and
    a schedule code supplied by the file is kept only when the description is not a schedule item -
    which is what keeps "not in the schedule" reportable.
28. **The catalogue is checked against the classifier on every verification run.** The sections summary
    is only as good as the agreement between the two, so `tools/verify_seed_data.py` re-classifies all
    2,330 catalogue descriptions and fails if any no longer lands in its stored section, naming the
    fix (`python tools/build_sor_items.py`). A rule change that silently invalidated the template's
    `section` column would otherwise be invisible.
29. **The unit and the currency are columns on every template row, and the quarter is a market fact.**
    A rate is meaningless without the unit it is per and the money it is in, and "2026" is not a
    quarter. The template therefore ships `UOM` (the app's vocabulary), `published_uom` (the
    schedule's own wording) and a per-row `uom_note` explaining any conversion or refusing to make
    one, plus `currency` on every row; `/api/countries` reports the `library_quarter` (2026Q2, both
    markets), the currency and the stated/retained section counts, and the app opens on that quarter.
    The alternative - a bare `unit` column and a rate that says "2026" while the reader assumes
    whatever quarter they have in mind - is exactly the silent ambiguity this file exists to remove.
30. **The benchmark quarter has a floor, and the floor is stated rather than enforced invisibly.** A
    saved upload or a market switch cannot open the app on a quarter older than the library quarter,
    because that escalates a rate the schedule already states at the library quarter *backwards* and
    reports it as a finding. The floor only constrains defaults: a quarter the analyst picks by hand
    is honoured, and the rate-basis line switches to `DERIVED - RATE ESCALATED FROM THE LIBRARY` so the
    extra modelled step is visible on the face of the panel.

31. **The rate library carries no placeholder flag and no TODO, and the template no longer asks the
    analyst to replace its rates.** An earlier revision marked every template row `is_placeholder =
    true` with a `# TODO: replace with your own rate`, and warned on every run that some lines were
    "priced from a retained estimate ... do not use for a real tender decision". That was wrong twice
    over: the rate in the sheet *is* the library rate for the section - the published schedule rate
    escalated to the library quarter, from a named source, at a stated quarter - and a warning that
    fires on every run is a warning nobody reads. What remains is the part that carries information:
    the source, the quarter, the unit and the currency on every row, and the `basis` label
    (measured / derived / assumed) on every figure. The nine retained estimates keep their
    `is_placeholder` boolean internally so the UI can distinguish a retained rate from an SOR-derived
    one, but nothing in the app, the API payload, the export or the template reads out as
    "placeholder", "indicative seed" or a TODO.

---

## Limitations - what this is not

* **Not audited for a real tender decision as shipped.** The monthly **producer** price indexes, the
  monthly **consumer** price indexes, the India WPI quarters and the material price series are real
  published data. The **benchmark rate library** is derived from the published schedules of rates
  (BCA SOR May 2022 and CPWD DSR 2021 Vol-II) escalated by CPI to 2026Q2, with nine sections carried
  as retained estimates from an older base where those extracts do not reach; the **regional
  multipliers** are modelled locational adjustments. On top of that, a carried-forward index is a
  **derived** figure rather than an observation. The UI labels published, derived, retained and
  assumed separately on every screen, and every rate names its source - which is what a reader needs
  in order to judge it, rather than a flag telling them the number is provisional.
* **The index bridge is not a construction cost forecast.** It carries an index to the tender quarter
  along the movement in a producer - or, failing that, consumer - price index, because those are the
  only current official series published monthly for both markets. Even a producer basket prices only
  the material and fuel content of a section: no published index measures site labour, which is why
  the coverage table flags labour-dominated sections explicitly. It brackets the gap; it does not
  measure it. Where a licensed monthly construction cost index exists (BCA InfoNet indices for
  fluctuation clauses, for instance), use that instead - and prefer a published quarter over a
  derived one whenever the tender allows it.
* **No PDF upload.** No OCR pipeline, and no runtime LLM call is permitted.
* **No authentication or audit trail.** Every endpoint is open. Do not expose this to the internet
  with real commercial BoQ data until auth, per-tenant isolation and an audit log are added.
* **The material/labour split is assumed**, not measured. It is the largest single source of
  unearned confidence in the output, and it is labelled as such everywhere it appears.
* **The overhead and margin percentages are analyst inputs with no evidence behind them.** They are
  the right place for a commercial judgement, but the app cannot validate them: a 12% overhead
  assumption is a 12% assumption. They are a single blended pair, unvalidated by trade, and every
  figure they touch is reported as `assumed`.
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

Used **by value** in this build (real published observations): the Singapore Department of Statistics
Consumer Price Index, All Items, table M213751 (2024 = 100, monthly, current to 2026-07); the BCA
Construction Material Market Prices table M211671 (annual, current to 2025); the India **Wholesale /
Producer Price Index**, base 2022-23 = 100 (Office of the Economic Adviser, DPIIT) - including **16
construction-relevant commodity baskets** published monthly, from Cement and Iron And Steel through
Petroleum Products, Electricity, Electrical Cables and the all-commodities composite; and the MoSPI /
NSO Consumer Price Index, Combined, All-India General - base 2024 = 100 with the publisher's own
back-cast months on that base, plus the predecessor base 2012 = 100. (Note: there is no 2016 = 100
retail CPI in India; that base belongs to the Labour Bureau's CPI-IW, a different basket.)

Named as coverage gap-closers, cited but **not** imported: the Labour Bureau **CPI-IW** (the
wage-escalation index written into Indian construction contracts, published per centre and per base
year, so it does not map one-to-one onto the city multipliers); the **CPWD Delhi Schedule of Rates**
and its piling chapter; **BCA Construction InfoNet** and the **SISV** tender price circulars for
Singapore; and the **MOM** wage data for construction labour. Each is labelled `wired`, `partial` or
`gap` against the section it would close, on `GET /api/indices/coverage`.

---

## Next steps to production

1. **Replace every placeholder with a licensed feed** - BCA InfoNet and SISV circulars under
   licence, keyed by the `replace_with` marker already stored on each row. That also retires the CPI
   bridge from the default path: a licensed monthly construction cost index removes the need to
   borrow consumer price movement at all.
2. **Keep the CPI seed refreshed on a schedule.** It is the input that decides how current every
   bridged index is. `tools/build_cpi_seed.py` plus `python -m app.importer --kind cpi` does it in two
   commands; the freshness endpoint (or the dashboard's data-currency table) tells you when it is due.
   Adding the publisher's release calendar as a reminder is the obvious next increment.
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
9. **Bridge with the index's own monthly data where the publisher releases it.** The India WPI is
   published monthly, so a quarterly WPI series can be extended from its own most recent months
   before falling back to the CPI. The engine is already structured for it: `resolve_cpi_bridge()` is
   one function, and `index_bridge.reason` already reports which path was taken. This would make the
   India bridge a nowcast of the same index rather than a cross-series approximation.
10. **Surface the bridge in the sensitivity view.** The index sweep already runs over the bridged
    level; showing the bridged-vs-published band as a shaded range would tell an analyst how much of
    the answer rests on the modelled step.
11. **Per-section overheads and margin.** The single blended pair is the crude part of the full-cost
    build-up: preliminaries and M&E sections normally recover overheads differently from structural
    work. `section_overhead_pct` / `section_margin_pct` maps would mirror the existing per-section
    rate shifts, and the waterfall already has the hooks to show them.
12. **Record the OH&P basis per market.** Singapore and India conventions differ on what a tendered
    rate includes and on how margin is normally stated (prime cost sum, attendance, contingency).
    Storing the default `overheads_in_tender` and typical ranges per market would remove a question
    the analyst currently has to answer from memory on every run.
