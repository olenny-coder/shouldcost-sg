# DEPLOY.md - GitHub -> Neon -> Render -> Vercel

The linear path. Follow it top to bottom; the order matters because each step needs a value that
the previous one produces.

**Why this order**

```
    1. GitHub    the repo Render and Vercel both pull from
    2. Neon      gives you a DATABASE_URL          (valid 30 days? no - it does not expire)
    3. Render    needs DATABASE_URL, gives you the API URL
    4. Vercel    needs the API URL, gives you the site URL
    5. Render    needs the site URL for CORS  <- back to step 3's service
```

Total time ~20 minutes. No credit card anywhere on this path.

---

## 0. Prerequisites

| Need | Check |
|---|---|
| A GitHub account | https://github.com/signup |
| A Neon account (free) | https://neon.tech - sign in with GitHub, no card |
| A Render account (free) | https://dashboard.render.com/register - sign in with GitHub, no card |
| A Vercel account (free) | https://vercel.com/signup - sign in with GitHub, no card |
| git installed locally | ```git --version``` |

---

## 1. GitHub - push the repository

The repo is already ```git init```-ed on branch ```main``` with no commits yet.

```bash
cd "C:\Users\Spare Parts\Should Cost\shouldcost-sg"
git add .
git commit -m "shouldcost: multi-market BoQ should-cost benchmarking"
git branch -M main
git remote add origin https://github.com/<your-username>/shouldcost-sg.git
git push -u origin main
```

Then on GitHub: **New repository** -> name it ```shouldcost-sg``` -> **Public** or Private, either works ->
**do not** initialise with a README (you already have one) -> Create.

### Before you push, confirm nothing secret goes up

```bash
git status --short --untracked-files=all | findstr /I ".env .db node_modules dist"
```

Expect only the three ```.env.example``` templates, which contain no secrets. Expect about **62 files**.

> **Branch name matters.** Both blueprints pin ```branch: main```. If your default branch is ```master```,
> either rename it or edit the blueprint.

---

## 2. Neon - create the PostgreSQL database

1. Go to **https://console.neon.tech** and sign in with GitHub.
2. **Create project**. Name it ```shouldcost```. Pick the region closest to your Render region
   (Render is set to **Singapore** in the blueprint; Neon's closest is **AWS ap-southeast-1**).
3. Postgres version: accept the default (16 or 17).
4. On the project dashboard, find the **Connection string** panel (there is a **Connect** button).
5. **Copy the connection string.** It looks like:

```
postgresql://neondb_owner:npg_XXXXXXXX@ep-cool-name-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
```

6. Keep it somewhere safe for the next step. **This is a secret** - never commit it.

### Pooled or direct?

Neon shows both. The host with **```-pooler```** in it is the pooled (PgBouncer) endpoint.

| Use | When |
|---|---|
| **Direct** (no ```-pooler```) | **Recommended here.** Render runs one long-lived process, so you do not need pooling and you avoid every pooler caveat. |
| Pooled | Only if you later move the API to a serverless host that opens many short-lived connections. |

Both work with this app: ```app/config.py``` preserves the ```?sslmode=require``` (and ```channel_binding```) query
parameters, and this was verified against the bundled libpq 18.

### About scale-to-zero

Neon's free plan **suspends compute after a few minutes of inactivity** and wakes on the next query.
Nothing is deleted - but combined with Render's own 15-minute spin-down, the first request after a
long idle period can take 30-60 seconds. That is normal, not a failure.

---

## 3. Render - deploy the backend

### 3a. Choose which database blueprint to use

The repo ships two. Pick one **before** you create the Blueprint:

| File | Database | Expires? |
|---|---|---|
| ```render.yaml``` | A Render-managed free PostgreSQL, created for you | **Yes - deleted after 30 days** |
| ```render.neon.yaml``` | The Neon database from step 2 | No |

**Recommended: use the Neon variant**, since you have just created the database.

> Do this **after** the first commit in step 1. ```git mv``` refuses to run on a file that is not yet
> under version control (```fatal: not under version control```), which is the state the repo is in
> before that commit.

> ```git commit``` also needs an identity. Check with ```git config --global user.name```; if it is
> empty, run ```git config --global user.name "Your Name"``` and
> ```git config --global user.email "you@example.com"``` first.

```bash
cd "C:\Users\Spare Parts\Should Cost\shouldcost-sg"
git mv render.yaml render.with-render-postgres.yaml
git mv render.neon.yaml render.yaml
git commit -m "deploy: use Neon for PostgreSQL"
git push
```

If you would rather keep the all-Render path, change nothing and skip to 3c - but set a reminder
for day 25, because the database is deleted on day 30.

### 3b. Create the Blueprint

1. Go to **https://dashboard.render.com** -> **New +** -> **Blueprint**.
2. Connect GitHub if prompted, then select the ```shouldcost-sg``` repository.
3. Render reads ```render.yaml``` and shows a preview. You should see **1 web service**
   (and **1 database** too, if you kept the all-Render variant).
4. Click **Apply**.
5. Render prompts for the variables marked ```sync: false```. Fill them in:

| Variable | Value |
|---|---|
| ```DATABASE_URL``` | *(Neon variant only)* paste the connection string from step 2 |
| ```FRONTEND_URL``` | **leave blank for now** - it does not exist until step 5 |
| ```FRONTEND_PREVIEW_REGEX``` | leave blank (optional) |

6. The first build starts. It may fail its health check if the database is empty - that is expected,
   because the next step seeds it.

### 3c. Seeding

**On the free Render plan there is no Shell tab**, so the ETL cannot be run from the dashboard.
```render.yaml``` therefore sets ```AUTO_SEED=true```: the service loads the reference data
itself on first boot, if and only if the database is empty.

That means **you may not have to do anything**. The first successful deploy seeds itself. Confirm
with:

```bash
curl "https://<your-service>.onrender.com/api/indices/regions?country=IN"
curl "https://<your-service>.onrender.com/api/indices/coverage?country=IN"
```

Twelve Indian cities means it worked. ```[]``` means it did not - check the deploy log for a line
beginning ```AUTO_SEED:```. The coverage call is the sharper check of the two: it should report
```"uncovered_sections": []``` (or just `Unclassified`) and ```"producer_series_count": 16```, which
proves both the seed and the producer-index wiring survived the deploy.

To seed deliberately instead, set ```AUTO_SEED=false``` and use one of these:

1. **Render Shell** (paid plans only): ```cd backend 2>/dev/null; python -m app.etl```
2. **From your machine**:

   ```powershell
   .\tools\seed_remote.ps1
   ```

   It prompts for the connection string with the input hidden, seeds that database, and verifies
   through the deployed API. Pass ```-ApiBase https://<your-service>.onrender.com```
   to point it at a different service.

> **Do not run ```python -m app.etl``` in a plain shell without setting ```DATABASE_URL```.**
> With no ```DATABASE_URL``` the app falls back to local SQLite, so the command succeeds, prints
> correct-looking row counts, and writes to ```backend/shouldcost.db``` on your machine. Production
> stays empty and nothing tells you. ```AUTO_SEED``` exists so that step is not needed at all.

#### The manual route (kept for reference)

Render dashboard -> **shouldcost-backend** -> **Shell** tab:

```bash
python -m app.etl
```

Expected output:

```
ETL: row counts after load
  tpi_series         126
  material_prices    180
  benchmark_rates    20
  regional_factors   13
  price_series       786
  boq_uploads        2
  boq_items          40
ETL: published data vs indicative seed values
  tpi_series           78 real /  126 total   [MIXED]
  material_prices     180 real /  180 total   [PUBLISHED]
  benchmark_rates       0 real /   20 total   [indicative]
  regional_factors      0 real /   13 total   [indicative]
  price_series        786 real /  786 total   [PUBLISHED]
```

(`price_series` is seeded from `backend/data/price_series.csv`: the 16 real monthly **India producer
price index** baskets (Office of the Economic Adviser, base 2022-23 = 100) plus the monthly consumer
price index for both markets. Those are the series that carry a stale index observation forward to the
tender quarter - see README "Keeping the indexes current: PPI first, CPI as fallback".)

It is idempotent, so re-running is always safe.

#### If the Shell tab is not offered, or the app loads but shows nothing

`/api/healthz` reports ```db: connected``` even against an **empty** database - ```SELECT 1``` succeeds
on a schema with no rows in it. So the symptom of an unseeded deploy is an app that loads and
shows zero indexes and no sample bills, while health looks perfectly fine.

Check it:

```bash
curl "https://<your-service>.onrender.com/api/indices/regions?country=IN"
```

`[]` means it has not been seeded. To seed from your own machine instead of the browser Shell:

```powershell
cd "C:\Users\Spare Parts\Should Cost\shouldcost-sg"
.\tools\seed_remote.ps1
```

It prompts for the connection string with the input hidden, runs the ETL against that database,
then verifies the result through the deployed API and prints the row counts. Pass
```-ApiBase https://<your-service>.onrender.com``` to point it at your service, or
```-SkipVerify``` to seed without checking.

> **Do not run ```python -m app.etl``` in a plain PowerShell window without setting**
> ```DATABASE_URL```**.** With no ```DATABASE_URL``` the app defaults to local SQLite, so the command
> succeeds, prints happy row counts, and writes to ```backend/shouldcost.db``` on your machine.
> Production stays empty and nothing tells you why. That is what the script exists to prevent.

If the Shell tab is not offered, add a temporary **Job** to the blueprint (```type: job```, ```runtime: python```, ```rootDir: backend```,
```startCommand: python -m app.etl```), run it once, then remove it.

### 3d. Note your API URL

```
https://shouldcost-backend.onrender.com
```

Check it now:

```bash
curl https://shouldcost-backend.onrender.com/api/healthz
```

```
{"status":"ok","db":"connected","environment":"production"}
```

---

## 4. Vercel - deploy the frontend

1. Go to **https://vercel.com/new** and import the same ```shouldcost-sg``` repository.
2. **Root Directory: ```frontend```** <- click Edit and set this. This is the single most common
   mistake; without it Vercel builds the repo root and fails.
3. **Framework Preset: Vite** (Vercel usually detects it).
4. Build Command ```npm run build```, Output Directory ```dist``` - both are already in
   ```frontend/vercel.json```, so confirm rather than retype.
5. Expand **Environment Variables** and add this **before** the first deploy:

| Name | Value | Environments |
|---|---|---|
| ```VITE_API_BASE_URL``` | ```https://shouldcost-backend.onrender.com``` | Production, Preview, Development |

   No trailing slash.
6. Click **Deploy** and note the URL, e.g. ```https://shouldcost-sg.vercel.app```.

> **```VITE_API_BASE_URL``` is baked into the bundle at BUILD time.** Vite substitutes it during
> ```npm run build```. Changing it later does nothing until you **redeploy**. This catches everyone.

---

## 5. Render - close the CORS loop

With no ```FRONTEND_URL``` in production the CORS allowlist is deliberately **empty**, so the API
currently refuses your Vercel origin. Fix that now:

1. Render dashboard -> **shouldcost-backend** -> **Environment**.
2. Set **```FRONTEND_URL```** to the Vercel production URL, exactly:

```
https://shouldcost-sg.vercel.app
```

   No trailing slash. ```https://```, not ```http://```.
3. *(Optional)* set **```FRONTEND_PREVIEW_REGEX```** to allow preview deployments:

```
^https://shouldcost-.*\.vercel\.app$
```

   Leave it unset to deny every preview origin. Previews are opt-in on purpose - an open pattern
   lets anyone who can trigger a branch build talk to your API.
4. **Save**. Render redeploys automatically. Wait for **Live**.

---

## 6. Verify

### 6a. Health

```bash
curl https://shouldcost-backend.onrender.com/api/healthz
```

Expect ```{"status":"ok","db":"connected","environment":"production"}```.

### 6b. CORS preflight - the step that catches most failures

```bash
curl -X OPTIONS \
  -H "Origin: https://shouldcost-sg.vercel.app" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" \
  https://shouldcost-backend.onrender.com/api/boq/upload -v
```

Expect ```HTTP/1.1 200 OK``` and a response header:

```
< access-control-allow-origin: https://shouldcost-sg.vercel.app
```

If that header is **missing**, ```FRONTEND_URL``` does not match the calling origin character for character.

### 6c. End to end through the API

```bash
# upload the Singapore sample
curl -X POST "https://shouldcost-backend.onrender.com/api/boq/upload?country=SG" \
  -F "file=@backend/data/sample_boq.csv"

# India sample at two regional multipliers - should-cost must differ
curl -X POST "https://shouldcost-backend.onrender.com/api/boq/2/benchmark" \
  -H "Content-Type: application/json" \
  -d '{"tender_quarter":"2024Q4","tpi_series_name":"WPI-CONST","variance_threshold":15,"region_code":"DEL"}'

curl -X POST "https://shouldcost-backend.onrender.com/api/boq/2/benchmark" \
  -H "Content-Type: application/json" \
  -d '{"tender_quarter":"2024Q4","tpi_series_name":"WPI-CONST","variance_threshold":15,"region_code":"MUM"}'
```

Mumbai's should-cost should be ~12.8% above Delhi's.

### 6d. In the browser

Open the Vercel URL and check:

- [ ] The header strip reads ```API ok | db connected | production```
- [ ] The Upload tab lists **Recent uploads**; the two buttons prefixed **Sample:** load the Singapore and India demonstration bills (the app also auto-loads one when you switch market)
- [ ] The variance table shows amber **NOT BENCHMARKED** rows and a coverage panel
- [ ] Switching the market to India reveals the **Region** selector (12 cities)
- [ ] The waterfall reconciles, with labelled rounded blue bars
- [ ] **Download -> Full benchmark report (.csv)** downloads ~600 rows
- [ ] **The browser console shows no CORS errors**

---

## Where every value lives

| Value | Set on | Used by | Contains a secret? |
|---|---|---|---|
| ```DATABASE_URL``` | Render -> shouldcost-backend -> Environment | Backend | **Yes** - never commit |
| ```ENVIRONMENT``` | ```render.yaml``` | Backend | no |
| ```PYTHON_VERSION``` | ```render.yaml``` | Render build | no |
| ```FRONTEND_URL``` | Render -> Environment | Backend CORS | no |
| ```FRONTEND_PREVIEW_REGEX``` | Render -> Environment (optional) | Backend CORS | no |
| ```VITE_API_BASE_URL``` | Vercel -> Settings -> Environment Variables | Frontend bundle | no |

Nothing above is committed to the repo. ```render.yaml``` marks the two CORS values
```sync: false```, which makes Render prompt for them in the dashboard instead of reading them from
the file.

---

## When something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| Console: *blocked by CORS policy* | ```FRONTEND_URL``` does not exactly match the browser origin | Set it with no trailing slash and the right scheme, save, re-run 6b |
| Console: *No response from ...* and an empty network tab | ```VITE_API_BASE_URL``` was set after the last build | Redeploy on Vercel - it is inlined at build time |
| The app calls ```http://localhost:8000``` in production | ```VITE_API_BASE_URL``` unset, so the documented dev fallback kicked in | Set it and redeploy |
| 404 on refresh or on any deep link | SPA rewrite missing | Confirm ```frontend/vercel.json``` has the ```/(.*) -> /``` rewrite **and** that Vercel's Root Directory is ```frontend```
| ```/api/healthz``` returns ```degraded``` with a db error | ```DATABASE_URL``` wrong, or Neon is cold | Check the value; retry - Neon wakes in a few seconds |
| Deploy fails: *Application exited early* | The start command does not bind ```$PORT``` | It must be exactly ```uvicorn app.main:app --host 0.0.0.0 --port $PORT```
| Deploy fails: *No module named app* | Building from the repo root | ```rootDir: backend``` must be set (it is) |
| Upload returns 422 naming a column | Headers not recognised | Required: ```description, unit, quantity, rate```. Or start from ```/api/boq/template``` |
| Everything worked, then broke ~30 days later | **You used the Render-Postgres blueprint** | Switch to ```render.neon.yaml``` - see section 3a |
| First request of the day takes 30-60 s | Render free spins down after 15 min, and Neon suspends compute | Expected on free plans. A paid Render instance removes it |
| ```git push``` rejected | Render or Vercel wrote to the branch | ```git pull --rebase``` then push |

For anything else, ```deploy-runbook.md``` has the full reference: database seeding options,
rollback, the PostgreSQL-vs-SQLite test matrix, and a survey of free hosting alternatives.

---

## After it is live

Two things to understand before you put this in front of anyone:

1. **The benchmark rates are indicative seed values, not published data.** The 20 rate rows stand in
   for the CPWD Delhi Schedule of Rates and BCA Construction InfoNet, both licensed publications, so
   they carry `is_placeholder: true`, a source URL and a TODO. The published **index** series are real,
   and figures the engine derives from them are labelled as derived. The UI says which is which on
   every screen. Refresh the real index data with ```python -m app.importer```.
2. **There is no authentication.** Every endpoint is open. Do not upload a real commercial Bill of
   Quantities to a public URL until you have added auth and an audit trail.

Hosting is the easy part. Licensing and auth are the work.
