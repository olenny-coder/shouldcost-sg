# deploy-runbook.md

> **Starting from scratch? Read [DEPLOY.md](DEPLOY.md) instead.** It is the linear walkthrough for
> GitHub -> Neon -> Render -> Vercel. This document is the reference you reach for when something
> goes wrong, or when you need the option this walkthrough did not take.

Step-by-step deployment of **shouldcost-sg** to Render (backend + PostgreSQL) and Vercel
(frontend). Written because the capability probe found **no Render or Vercel credentials in the
environment**, so milestone M6 could not be executed and these steps are manual.

Total time: ~25 minutes. No step requires a paid plan.

Replace every `<placeholder>` with your own value. Never commit a real credential.

---

## 0. What gets created

| Component | Host | Name | Notes |
|---|---|---|---|
| Web service (FastAPI) | Render | `shouldcost-backend` | root dir `backend/` |
| PostgreSQL | Render | `shouldcost-db` | plan `free`, region `singapore` |
| Static SPA | Vercel | your project name | root dir `frontend/` |

---

## 1. Prerequisites

* A **GitHub** account (or GitLab/Bitbucket - Render and Vercel both support them).
* A **Render** account - https://dashboard.render.com/register
* A **Vercel** account - https://vercel.com/signup
* **git** installed locally: `git --version`
* Node.js 20+ and Python 3.11 for local verification before deploying.

Verify your local build passes **before** pushing. Deploying a broken repo wastes a build cycle:

```bash
make install && make seed && make test && make build
```

---

## 2. Push the repository to GitHub

```bash
cd shouldcost-sg
git init
git add .
git commit -m "shouldcost-sg: FastAPI + React BoQ should-cost benchmarking"

git branch -M main
git remote add origin https://github.com/<your-account>/shouldcost-sg.git
git push -u origin main
```

**Confirm nothing secret was pushed.** `.gitignore` excludes `.env`, `*.db`, `node_modules/` and
`dist/`. Double-check with:

```bash
git ls-files | grep -E "(^|/)\.env$|\.db$"    # must print nothing
```

Default branch **must be `main`** - `render.yaml` pins `branch: main`.

---

## 3. Render: create the Blueprint

1. Go to https://dashboard.render.com/ -> **New** -> **Blueprint**.
2. Connect your GitHub account and select the `shouldcost-sg` repository.
3. Render detects `render.yaml` at the repo root and shows a preview of the resources it will
   create. Confirm you see **1 web service** and **1 database**.
4. Name the Blueprint and click **Apply**.
5. Render prompts for the two variables marked `sync: false`:
   * `FRONTEND_URL` - **leave blank for now.** Step 9 sets it after the Vercel deploy exists.
   * `FRONTEND_PREVIEW_REGEX` - optional, leave blank to deny all preview origins.
6. Render starts building the backend. The **first build will fail its health check if the database
   has not been seeded** - that is expected, continue to step 5 below.

### What `render.yaml` declares, and why

```yaml
services:
  - type: web
    name: shouldcost-backend
    runtime: python            # Render deprecates the older `env:` field
    rootDir: backend
    plan: free
    region: singapore
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /api/healthz
```

> **Two deliberate deviations from a naive reading of the brief, both verified against
> https://render.com/docs/blueprint-spec:**
>
> * The brief asked for `env: python`. Render documents `runtime` as the current field and states
>   it *replaces the deprecated `env` field*. `env: python` still parses today but is legacy, so
>   `runtime: python` is used.
> * The brief asked for `PYTHON_VERSION=3.11`. Render's docs are explicit: with the environment-
>   variable method you *must* specify a **fully qualified** version. A bare `3.11` is ignored and
>   the service silently builds on Render's default Python instead. `3.11.6` is pinned.

---

## 4. Render: confirm PostgreSQL and `DATABASE_URL`

1. In the dashboard, open the **shouldcost-db** database. Its status should reach **Available**.
2. Open the **shouldcost-backend** web service -> **Environment**. Confirm `DATABASE_URL` is
   present and marked as coming from `shouldcost-db`.
3. Do **not** edit it by hand. If you ever need to copy it manually, note that Render may present
   the legacy `postgres://` scheme; the backend normalises `postgres://` and `postgresql://` to
   `postgresql+psycopg2://` in `app/config.py`, so either form works.

FastAPI creates any missing tables on startup (`init_db()` in the lifespan handler), so a brand-new
database is immediately usable - it is simply empty until step 5.

---

## 5. Render: seed the database

**Chosen approach: (a) a one-off job.**

### Why not `preDeployCommand`?

The brief allows either. `preDeployCommand: python -m app.etl` is cleaner in principle, but
pre-deploy commands are only available on **paid** instance types, and this Blueprint deliberately
runs on the free plan so it can be deployed at zero cost. The line is present in `render.yaml`,
commented out, so you can enable it in one edit if you upgrade.

### Run the seed as a one-off job

1. Render dashboard -> **shouldcost-backend** -> **Shell** tab (available on free web services) and
   run:

   ```bash
   python -m app.etl
   ```

   > **After a schema change, use `python -m app.etl --reset` instead.** `create_all()` adds
   > missing tables but never ALTERs an existing one, so a new column stays invisible until the
   > table is rebuilt. **`--reset` drops every table and destroys any uploaded BoQs.**

   Expected output:

   ```
   ETL: reading seed CSVs from /opt/render/project/src/backend/data
   ETL: row counts after load
     tpi_series         32
     material_prices    36
     benchmark_rates    10
     boq_uploads        1
     boq_items          20
   ETL: complete (published index series are real; benchmark rates, regional
   multipliers and the construction cost index quarters are indicative seed values,
   every one carrying is_placeholder = true)
   ```

   If the Shell tab is unavailable to you, add a temporary **Job** to `render.yaml`
   (`type: job`, `runtime: python`, `rootDir: backend`, `startCommand: python -m app.etl`) and run it
   once from the dashboard, then remove it.

2. **The ETL is idempotent.** Every row is upserted on its natural key, and the sample BoQ upload is
   replaced rather than duplicated. Running it twice produces **identical row counts** - this is
   verified by the M1 gate (`python -m app.etl` twice, same counts). It is therefore safe to re-run
   after any redeploy.

3. Verify: `curl https://<your-service>.onrender.com/api/healthz` must return
   `{"status":"ok","db":"connected",...}`.

---

## 6. Vercel: import the repository

1. https://vercel.com/new -> import the same GitHub repository.
2. **Root Directory: `frontend`.** This is the single most common mistake - without it Vercel tries
   to build the repository root and fails.
3. **Framework Preset: Vite.**
4. **Build Command: `npm run build`** (also set in `frontend/vercel.json`).
5. **Output Directory: `dist`** (also set in `frontend/vercel.json`).
6. Do not deploy yet - set the environment variable first (step 7).

---

## 7. Vercel: set `VITE_API_BASE_URL`

1. Project -> **Settings** -> **Environment Variables**.
2. Add:

   | Name | Value | Environments |
   |---|---|---|
   | `VITE_API_BASE_URL` | `https://<your-service>.onrender.com` | Production, Preview, Development |

3. **No trailing slash.** The frontend strips any it finds, but keep it clean.
4. **This value is inlined into the JavaScript bundle at BUILD time.** Vite substitutes
   `import.meta.env.VITE_API_BASE_URL` during `npm run build`. It is not read at runtime, so
   changing it does **nothing** until you redeploy.

---

## 8. Vercel: deploy

1. Click **Deploy** (or push to `main` if you already connected the project).
2. Note the production URL, e.g. `https://shouldcost-sg.vercel.app`.
3. Open it. The status strip in the header should read `API ok | db connected | production`.
4. If it instead shows *"Cannot reach the backend"*, the cause is almost always CORS - the backend
   does not yet know this origin. Do step 9 and then reload.

**SPA routing.** `frontend/vercel.json` declares:

```json
{ "rewrites": [{ "source": "/(.*)", "destination": "/" }] }
```

so that entering a deep link or pressing refresh does not 404. This app does **not** proxy `/api`
through Vercel - the browser calls Render directly using the baked-in `VITE_API_BASE_URL`.

### Optional: proxy `/api` through Vercel instead

There is a real trade-off here. The proxy hides the Render URL, gives you a same-origin setup with
no CORS preflight, and lets you swap backends without rebuilding the frontend. It also puts a CDN
between the user and a dynamic, per-request benchmark response, adds a hop of latency, and consumes
Vercel bandwidth - and you must defeat the caching explicitly.

If you want that pattern, add this to `frontend/vercel.json`, set `VITE_API_BASE_URL` to your
Vercel domain, and exclude `/api` from the SPA catch-all:

```json
"rewrites": [
  { "source": "/api/:path*", "destination": "https://<your-service>.onrender.com/api/:path*" },
  { "source": "/((?!api/).*)", "destination": "/" }
],
"headers": [
  {
    "source": "/api/(.*)",
    "headers": [{ "key": "x-vercel-enable-rewrite-caching", "value": "0" }]
  }
]
```

`x-vercel-enable-rewrite-caching: 0` is required - without it the CDN may serve a stale benchmark
response for the wrong upload. The default configuration in this repo does not need it because no
requests are rewritten to the backend.

---

## 9. Render: allow the Vercel origin through CORS

The backend builds its CORS allowlist from the environment. A wildcard is never used.

1. Render dashboard -> **shouldcost-backend** -> **Environment**.
2. Set `FRONTEND_URL` to the Vercel production URL, e.g. `https://shouldcost-sg.vercel.app`.
   No trailing slash.
3. *(Optional)* Set `FRONTEND_PREVIEW_REGEX` to allow Vercel preview deployments, e.g.

   ```
   ^https://shouldcost-.*\.vercel\.app$
   ```

   Leave it unset to deny every preview origin. Previews are **opt-in by design** - an open preview
   pattern lets anyone who can trigger a branch build talk to your API.
4. Save. Render redeploys the service automatically; wait for the deploy to go **Live**.

---

## 10. Verify end to end

### 10a. Health check

```bash
curl https://<your-service>.onrender.com/api/healthz
```

Expected: `{"status":"ok","db":"connected","environment":"production"}`

### 10b. CORS preflight (the step that catches most failures)

```bash
curl -X OPTIONS \
  -H "Origin: https://<your-vercel-url>" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" \
  https://<your-service>.onrender.com/api/boq/upload -v
```

Expect `HTTP/1.1 200 OK` and a response header:

```
< access-control-allow-origin: https://<your-vercel-url>
```

If `access-control-allow-origin` is **missing**, `FRONTEND_URL` does not match the calling origin
exactly. Compare it character by character - a trailing slash or `http://` instead of `https://` is
enough to break it.

### 10c. Upload the sample BoQ

```bash
curl -X POST https://<your-service>.onrender.com/api/boq/upload \
  -F "file=@backend/data/sample_boq.csv"
```

Expected: `row_count: 20`, `classified_count: 18`, `unclassified_count: 2`, `upload_id: 1`.

### 10d. Run the benchmark that proves the scope-exclusion path

```bash
curl -X POST https://<your-service>.onrender.com/api/boq/1/benchmark \
  -H "Content-Type: application/json" \
  -d '{"tender_quarter":"2024Q4","tpi_series_name":"RLB","variance_threshold":15}'
```

Expect `warnings[]` to contain a line naming **Piling** and **RLB**, and `waterfall[]` to reconcile:
`boq_total + sum(waterfall amounts) == should_cost_total` to the cent.

### 10d-2. Verify the India market too

```bash
curl https://<your-service>.onrender.com/api/countries
curl "https://<your-service>.onrender.com/api/indices/tpi?country=IN"
curl -X POST "https://<your-service>.onrender.com/api/boq/2/benchmark" \
  -H "Content-Type: application/json" \
  -d '{"tender_quarter":"2024Q4","tpi_series_name":"CPWD","variance_threshold":15}'
```

Expect INR amounts, `classification_standard: "IS 1200 / CPWD DSR"`, and a `warnings[]` entry
naming **Piling** and **CPWD**. Also check the downloads:

```bash
curl -o template.csv  "https://<your-service>.onrender.com/api/boq/template?format=csv&country=IN"
curl -o template.xlsx "https://<your-service>.onrender.com/api/boq/template?format=xlsx&country=SG"
```

Both must be non-empty; the CSV must upload cleanly through `/api/boq/upload?country=IN`.

### 10d-3. Verify regions and the full report

```bash
# India's regional multipliers
curl "https://<your-service>.onrender.com/api/indices/regions?country=IN"

# The same BoQ at two regional multipliers - should-cost must differ
curl -X POST "https://<your-service>.onrender.com/api/boq/2/benchmark" -H "Content-Type: application/json" \
  -d '{"tender_quarter":"2024Q4","tpi_series_name":"WPI-CONST","variance_threshold":15,"region_code":"DEL"}'
curl -X POST "https://<your-service>.onrender.com/api/boq/2/benchmark" -H "Content-Type: application/json" \
  -d '{"tender_quarter":"2024Q4","tpi_series_name":"WPI-CONST","variance_threshold":15,"region_code":"MUM"}'

# The full self-describing report
curl -X POST "https://<your-service>.onrender.com/api/boq/2/export?format=csv&level=report" \
  -H "Content-Type: application/json" \
  -d '{"tender_quarter":"2024Q4","tpi_series_name":"WPI-CONST","variance_threshold":15,"region_code":"MUM"}' \
  -o report.csv
```

Expect Mumbai's should-cost to be ~12.8% above Delhi's, and `report.csv` to contain the blocks
`report`, `totals`, `section`, `waterfall`, `line`, `adjustment`, `warning`, `assumption` and
`source`.

### 10d-4. Verify the published-vs-indicative labelling

```bash
curl "https://<your-service>.onrender.com/api/indices/materials?country=SG" | head -c 400
curl "https://<your-service>.onrender.com/api/indices/coverage?country=IN" | head -c 400
```

Every Singapore material row must carry `"is_placeholder": false` and a `provenance_note`
starting `REAL DATA.`. If any row says `true`, the seed did not load correctly.

The coverage call must show `"producer_series_count": 16` and `"uncovered_sections": []` (or just
`Unclassified`) for India: that proves the producer price index seed loaded and that every canonical
section has a published series able to re-price it. Singapore must show
`"producer_series_count": 0` and a `notes` entry naming M213461/M213411 - the documented gap, not a
failed seed.

### 10e. Full UI check

1. Open the Vercel URL. The header strip shows `API ok | db connected | production`.
2. **Upload** tab -> upload `backend/data/sample_boq.csv` -> classification counts render.
3. **Variance table** -> threshold-breaching rows are highlighted red/blue, section subtotals appear,
   click any description to expand its benchmark provenance.
4. **Waterfall** -> the reconciliation banner reads *"Reconciled ... within S$0.01"*.
5. Switch **TPI series** to `RLB` -> the benchmark re-runs live and the scope warning for Piling
   appears in the assumptions panel.
6. Open the browser console. **There must be no CORS errors.**

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Console: *"blocked by CORS policy: No 'Access-Control-Allow-Origin' header"* | `FRONTEND_URL` on Render does not exactly match the browser origin | Set it to the exact origin: scheme + host, **no trailing slash**, no path. Redeploy. Re-run 10b. |
| Console: *"No response from ... Is the backend running..."* and the network tab is empty | `VITE_API_BASE_URL` was set **after** the last build | Vite inlines it at build time. Redeploy on Vercel (a redeploy, not just an env change). |
| Frontend calls `http://localhost:8000` in production | `VITE_API_BASE_URL` is unset, so the documented local-dev fallback kicked in | Set it in Vercel -> Settings -> Environment Variables -> Production, then redeploy. |
| 404 when refreshing a deep link, or on any route other than `/` | SPA rewrite missing or overridden | Confirm `frontend/vercel.json` contains the `/(.*)` -> `/` rewrite and that Vercel's **Root Directory** is `frontend`, so the file is actually read from there. |
| `/api/healthz` returns `{"status":"degraded","db":"error: ..."}` | `DATABASE_URL` missing or the database is unavailable | Check the env var exists on the web service and references `shouldcost-db`. Confirm the database status is **Available**. On a free instance that has passed 30 days, the database is deleted - see below. |
| Deploy fails: *"Application exited early"* or the port never binds | The start command does not bind `$PORT` | It must be exactly `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. A hardcoded port fails on Render. |
| Deploy fails: *"No module named app"* | Render is building from the repo root | `rootDir: backend` must be set (it is, in `render.yaml`). |
| Upload returns 500 or *"no space left on device"* | The upload was written to Render's ephemeral disk | This app never writes uploads to disk - it parses in memory. If you added code that does, remove it; Render wipes the disk on every redeploy and the file will vanish mid-request. |
| Upload returns 413 | File exceeds the 10 MB in-memory limit | Split the BoQ, or remove unused columns. The limit protects the 512 MB free instance. |
| Upload returns 422 naming a missing column | The CSV headers were not recognised | Required: `description`, `unit`, `quantity`, `rate`. Common aliases are accepted; see `COLUMN_ALIASES` in `backend/app/routers/boq.py`. |
| Upload returns 422 for a `.xls` file | Legacy BIFF Excel is not supported | Re-save as `.xlsx` or `.csv`. |
| Health check passes but the first page load is slow | Free Render web services sleep after 15 minutes of inactivity | Expected on the free plan. The first request after a sleep takes ~30-60 s. Upgrade to a paid instance to avoid it. |
| Everything worked, then stopped ~30 days later | **Free Render PostgreSQL instances are deleted after 30 days** | Create a paid database (`plan: 0.1c-256mb` or higher) and update the Blueprint, or re-provision and re-run step 5. Data is not recoverable after deletion. |
| `make test` fails but the service runs fine | Locally the tests use a throwaway SQLite database | Check nothing else is holding `backend/shouldcost.db`. The suite itself never touches it. |

---

## 12. Rolling back

```bash
# Render: dashboard -> shouldcost-backend -> Events -> pick a previous deploy -> Rollback
# Vercel: dashboard -> Deployments -> pick a previous deployment -> Promote to Production
```

Because the seed data is idempotent and the schema is created on boot, a rollback needs no manual
database step unless the schema itself changed.

---

## 13. Post-deploy checklist

- [ ] `curl .../api/healthz` returns `status: ok`, `db: connected`
- [ ] CORS preflight returns `access-control-allow-origin` for the Vercel origin
- [ ] Sample BoQ uploads and classifies 18/20 rows
- [ ] Benchmark with `RLB` emits the named Piling scope warning
- [ ] Waterfall reconciles within S$0.01
- [ ] No CORS errors in the browser console
- [ ] `FRONTEND_URL` set on Render and marked as a secret (`sync: false` in `render.yaml`)
- [ ] No `.env` file or credential committed: `git ls-files | grep -E "(^|/)\.env$"` prints nothing
- [ ] Reminder set for day 25 - free Render PostgreSQL expires on day 30 (or move it to Neon now - see section 14)

---

## 14. Free hosting alternatives (verified September 2026)

The free-tier landscape contracted sharply during 2025-2026. Two of the options most often
recommended in older blog posts are **no longer available to new users**:

| Service | Status |
|---|---|
| **Fly.io** | Free tier **removed for new accounts**. Machines bill per second; a small app realistically lands at $8-25/mo once egress is counted. |
| **Koyeb** | Mistral acquired Koyeb (Feb 2026). The Starter plan is **closed to new signups**; existing orgs keep it "for the coming months". Cheapest listed plan is Pro at $29/mo. |
| **Heroku** | Free plans shut down in 2022. |
| **Glitch / Deta Space / ElephantSQL** | Shut down. |

### Options that are still genuinely free

| Option | Backend | Database | Sleeps? | Card? |
|---|---|---|---|---|
| **Render + Vercel + Neon** ⭐ | Render free (512 MB) | **Neon free** | API after 15 min; static site never | No |
| **Render + Vercel + Render Postgres** (this runbook) | Render free | Render free | API after 15 min | No |
| **All on Render** | Render free web | Render free | API after 15 min; static site never | No |
| **Cloudflare Pages + Render + Neon** | Render free | Neon free | API after 15 min; Pages never | No |
| **Hugging Face Spaces (Docker)** | Free CPU basic | Neon free (external) | After 48 h inactivity | No |
| **Google Cloud Run** | Always-free tier | Neon free | Cold starts, not sleep | **Yes** |
| **Oracle Cloud Always Free** | ARM VM, 4 cores / 24 GB, no expiry | self-hosted Postgres | No | **Yes** |
| **Railway** | $5 trial credit, ~500 h | included | No | Trial only |

### The single best free change: move the database to Neon

Render's free PostgreSQL instance is **deleted after 30 days**. Neon's free plan does not expire:
an idle project **suspends compute and wakes on the next query**, so it is slow on the first request
but nothing is lost. The free plan covers 100 projects, 10 branches per project and a 6-hour
restore window.

To switch:

1. Create a project at https://neon.tech and copy its connection string.
2. On Render, edit the `shouldcost-backend` service and replace `DATABASE_URL`. If you keep the
   Blueprint's `fromDatabase` reference, remove it first - otherwise Render overwrites your value
   on the next deploy. Easiest is to delete the `databases:` block from `render.yaml` and set
   `DATABASE_URL` as a plain `sync: false` variable.
3. Redeploy, then run `python -m app.etl` in the Render Shell.

**No code change is needed.** `app/config.py` normalises both `postgres://` and `postgresql://`,
and the app was verified against real PostgreSQL 16 - the full 205-test suite then in the repository passed on Postgres (the suite is now 229 tests; the Postgres leg has not been re-run since the CPI bridge was added)
as well as SQLite.

Supabase is the alternative, but its free plan **pauses a project after one week of inactivity**
(restorable from the dashboard), allows only 2 active projects, and includes no backups.

### All on Render (simplest, one platform)

Add a static site to `render.yaml` and drop Vercel entirely:

```yaml
  - type: web
    name: shouldcost-frontend
    runtime: static
    rootDir: frontend
    buildCommand: npm install && npm run build
    staticPublishPath: ./dist
    envVars:
      - key: VITE_API_BASE_URL
        value: https://shouldcost-backend.onrender.com
    routes:
      - type: rewrite
        source: /*
        destination: /index.html
```

**Render static sites do not sleep**, so only the API pays the 15-minute cold-start penalty.
One dashboard, one bill (zero), one place to configure CORS.

### Any container host: the Dockerfile

`backend/Dockerfile` builds a portable image, which unlocks Render, Cloud Run, Railway,
Northflank, Hugging Face Spaces and any plain VM. It runs as a non-root user, honours `$PORT`,
and needs no write access because uploads are parsed in memory.

```bash
docker build -t shouldcost-backend ./backend
docker run -p 8000:8000 -e DATABASE_URL=postgresql://... shouldcost-backend
```

### Test the PostgreSQL path locally

SQLite and PostgreSQL are not the same database, so the production path should be exercised
before you rely on it:

```bash
docker compose up --build
docker compose exec backend python -m app.etl
# full suite against real PostgreSQL:
docker compose exec -T \
  -e SHOULDCOST_TEST_DATABASE_URL=postgresql://shouldcost:shouldcost@db:5432/shouldcost_test \
  backend python -m pytest tests -q
docker compose down -v
```

### What "free" costs you

Every free option above shares the same three problems, and they are worth stating plainly:

1. **Cold starts.** A slept API takes 30-60 s to answer the first request. Fine for a demo,
   unacceptable for a tender review with a client watching.
2. **The app has no authentication.** Every endpoint is open. Do not put a real commercial Bill of
   Quantities on a public free URL.
3. **The benchmark rates are indicative seed values, not published data.** CPWD DSR and BCA
   Construction InfoNet are licensed publications, so the 20 rate rows carry `is_placeholder: true`,
   a source URL and a TODO. The published index series are real, and the figures the engine derives
   from them are labelled as derived. Free hosting is not the blocker to production here - licensing
   and auth are.

If this is going in front of real tender work, budget for a paid instance (Render from ~$7/mo) for
the always-on API, and treat the hosting spend as the smallest line in the project.