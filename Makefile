# shouldcost-sg - developer entry points.
# Works with GNU make under sh, cmd.exe and PowerShell. Recipes avoid shell
# built-ins so behaviour is identical on macOS, Linux and Windows.

PY ?= python
BACKEND := backend
FRONTEND := frontend

.PHONY: help install install-backend install-frontend seed cpi-seed price-series-seed \
        verify-seed upgrade-check test test-backend build dev dev-backend dev-frontend clean reset-db

help:
	@echo "shouldcost-sg - available targets"
	@echo "  make install   Install backend (pip) and frontend (npm) dependencies"
	@echo "  make seed      Load the bundled CSV seed data (idempotent)"
	@echo "  make cpi-seed  Rebuild data/cpi_series.csv from the publisher downloads (.realdata/)"
	@echo "  make price-series-seed  Rebuild data/price_series.csv (CPI + 16 India PPI baskets)"
	@echo "  make verify-seed        Re-derive every seeded value from the publisher file"
	@echo "  make upgrade-check      ETL against a copy of a database with the old schema"
	@echo "  make test      Run the backend test suite"
	@echo "  make build     Production build of the frontend"
	@echo "  make dev-backend   Run FastAPI on http://localhost:8000"
	@echo "  make dev-frontend  Run Vite on http://localhost:5173"
	@echo "  make clean     Remove caches and build output"

install: install-backend install-frontend

install-backend:
	cd $(BACKEND) && $(PY) -m pip install -r requirements.txt

install-frontend:
	cd $(FRONTEND) && npm install

seed:
	cd $(BACKEND) && $(PY) -m app.etl

# Rebuild the real monthly CPI seed from the files downloaded into ../.realdata/
# (SingStat table M213751 and the MoSPI CPI release). Then `make seed` upserts it.
cpi-seed:
	$(PY) tools/build_cpi_seed.py

# Rebuild the combined price-series seed: the CPI rows carried over from
# cpi_series.csv plus the 16 real India producer baskets read out of the OPPI/WPI
# workbook in ../.realdata/. Run this after cpi-seed, then run seed.
price-series-seed:
	$(PY) ../.realdata/build_price_series.py

# Re-derive every seeded value from the publisher's own download and diff it against
# what is committed. Exits non-zero if anything has drifted.
verify-seed:
	$(PY) tools/verify_seed_data.py

# Simulate a deployed database whose schema predates the newest seed table, then run
# the ETL against the copy. Guards the deploy failure mode where the code is current
# but the database is stale. `make upgrade-check`
upgrade-check:
	$(PY) tools/check_upgrade_path.py

test: test-backend

test-backend:
	cd $(BACKEND) && $(PY) -m pytest tests -v

build:
	cd $(FRONTEND) && npm run build

dev: dev-backend

dev-backend:
	cd $(BACKEND) && $(PY) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

dev-frontend:
	cd $(FRONTEND) && npm run dev

reset-db:
	cd $(BACKEND) && $(PY) -m app.etl --reset

clean:
	cd $(BACKEND) && $(PY) -c "import shutil,pathlib;[shutil.rmtree(p,ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	cd $(BACKEND) && $(PY) -c "import shutil;shutil.rmtree('.pytest_cache',ignore_errors=True)"
	cd $(FRONTEND) && $(PY) -c "import shutil;shutil.rmtree('dist',ignore_errors=True)"
