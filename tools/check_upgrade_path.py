"""Prove the upgrade path an already-populated database will take.

    python tools/check_upgrade_path.py


The deployed database predates the price_series table. create_all() adds missing
TABLES (it never ALTERs an existing one), so a plain ETL run must create
price_series and fill it, without a --reset that would drop uploaded BoQs. This
copies the local sqlite database, drops price_series from the copy, and runs the
ETL against it.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HERE = REPO / "backend"
SOURCE = HERE / "shouldcost.db"

if not SOURCE.exists():
    raise SystemExit("no local shouldcost.db to copy")

work = Path(tempfile.mkdtemp(prefix="shouldcost-upgrade-"))
target = work / "upgrade.db"
shutil.copy2(SOURCE, target)

# Drop the new table, so the copy looks like the deployed database: populated
# reference data, but no price_series and no PPI support.
with sqlite3.connect(target) as conn:
    conn.execute("DROP TABLE IF EXISTS price_series")
    conn.commit()
    tables = sorted(
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    )
    has_cpi = "cpi_series" in tables
    uploads_before = conn.execute("SELECT COUNT(*) FROM boq_uploads").fetchone()[0]
print("before: tables =", len(tables), "| legacy cpi_series present =", has_cpi)
print("before: uploaded BoQs =", uploads_before)

env = dict(os.environ)
env["DATABASE_URL"] = "sqlite:///" + target.as_posix()
result = subprocess.run(
    [sys.executable, "-m", "app.etl"], cwd=HERE, env=env, capture_output=True, text=True
)
print("etl exit code:", result.returncode)
if result.returncode != 0:
    print(result.stdout[-3000:])
    print(result.stderr[-3000:])
    raise SystemExit(1)

for line in result.stdout.splitlines():
    if "price_series" in line or "boq_uploads" in line:
        print(" ", line.strip())

with sqlite3.connect(target) as conn:
    tables = sorted(
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    )
    rows = conn.execute("SELECT COUNT(*) FROM price_series").fetchone()[0]
    ppi = conn.execute(
        "SELECT COUNT(*) FROM price_series WHERE kind = 'PPI'"
    ).fetchone()[0]
    uploads_after = conn.execute("SELECT COUNT(*) FROM boq_uploads").fetchone()[0]

print("after : price_series rows =", rows, "| of which PPI =", ppi)
print("after : uploaded BoQs =", uploads_after, "(must be unchanged)")
print("after : legacy cpi_series still present =", "cpi_series" in tables)

ok = rows == 786 and ppi == 640 and uploads_after == uploads_before
print("RESULT:", "PASS" if ok else "FAIL")
shutil.rmtree(work, ignore_errors=True)
raise SystemExit(0 if ok else 1)