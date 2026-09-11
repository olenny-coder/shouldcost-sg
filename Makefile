# shouldcost-sg - developer entry points.
# Works with GNU make under sh, cmd.exe and PowerShell. Recipes avoid shell
# built-ins so behaviour is identical on macOS, Linux and Windows.

PY ?= python
BACKEND := backend
FRONTEND := frontend

.PHONY: help install install-backend install-frontend seed test test-backend build \
        dev dev-backend dev-frontend clean reset-db

help:
	@echo "shouldcost-sg - available targets"
	@echo "  make install   Install backend (pip) and frontend (npm) dependencies"
	@echo "  make seed      Load the bundled CSV seed data (idempotent)"
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
