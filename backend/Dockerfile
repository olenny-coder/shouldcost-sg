# shouldcost backend - portable image.
#
# Works on any container host: Render, Google Cloud Run, Railway, Northflank,
# Hugging Face Spaces, or a plain VM behind Caddy/nginx.
#
#   docker build -t shouldcost-backend ./backend
#   docker run -p 8000:8000 -e DATABASE_URL=postgresql://... shouldcost-backend

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# psycopg2-binary ships manylinux wheels, so no build toolchain is needed.
# libpq5 is only required by the source distribution, which we do not use.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Seed data is part of the application: the ETL is what makes a fresh
# database usable, and the CSVs carry the provenance the app displays.
COPY app ./app
COPY data ./data
COPY pytest.ini .
COPY tests ./tests

# Drop to a non-root user. The app writes nothing to disk - uploads are
# parsed in memory - so the process needs no write access at all.
RUN useradd --create-home --uid 10001 shouldcost \
 && chown -R shouldcost:shouldcost /app
USER shouldcost

EXPOSE 8000

# Honour $PORT when the platform injects one (Cloud Run, Render), else 8000.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
