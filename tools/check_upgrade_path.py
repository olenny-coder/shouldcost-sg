"""Prove the upgrade paths an already-populated database will take.

    python tools/check_upgrade_path.py

The deployed database is always older than the repository: AUTO_SEED only fires on an
EMPTY database, and create_all() creates missing TABLES but never ALTERs an existing one.
So there are two distinct upgrade cases, and they have different consequences:

  1. A NEW TABLE (price_series). create_all() makes it; a plain ETL run fills it. No reset.
  2. A NEW COLUMN on an existing table (benchmark_rates.base_quarter). create_all() does
     nothing, so db.add_missing_columns() adds it. Without that the only fix would be
     --reset, which drops every uploaded BoQ.

Both are simulated here against a COPY of the local database, and both must leave the
uploaded BoQs alone. Run this before deploying a release that changes the schema.
"""

from __future__ import annotations

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
    raise SystemExit(f"no local database to copy at {SOURCE}")


def run_etl(database: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + database.as_posix()
    return subprocess.run(
        [sys.executable, "-m", "app.etl"], cwd=HERE, env=env, capture_output=True, text=True
    )


def fresh_copy(tag: str) -> Path:
    work = Path(tempfile.mkdtemp(prefix=f"shouldcost-upgrade-{tag}-"))
    target = work / "upgrade.db"
    shutil.copy2(SOURCE, target)
    return target


def check_new_table() -> bool:
    """price_series did not exist in the deployed schema."""
    print("=" * 74)
    print("CASE 1 - a table the deployed database does not have (price_series)")
    print("=" * 74)
    target = fresh_copy("table")
    with sqlite3.connect(target) as conn:
        conn.execute("DROP TABLE IF EXISTS price_series")
        conn.commit()
        uploads = conn.execute("SELECT COUNT(*) FROM boq_uploads").fetchone()[0]
    print(f"  before: price_series absent, uploaded BoQs = {uploads}")

    result = run_etl(target)
    if result.returncode != 0:
        print(result.stdout[-2000:], result.stderr[-2000:])
        return False

    with sqlite3.connect(target) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM price_series").fetchone()[0]
        ppi = conn.execute("SELECT COUNT(*) FROM price_series WHERE kind = 'PPI'").fetchone()[0]
        after = conn.execute("SELECT COUNT(*) FROM boq_uploads").fetchone()[0]
    print(f"  after : price_series rows = {rows} (PPI {ppi}), uploaded BoQs = {after}")
    ok = rows == 786 and ppi == 640 and after == uploads
    print("  ", "PASS" if ok else "FAIL")
    return ok


def check_new_column() -> bool:
    """benchmark_rates.base_quarter was added to an existing table."""
    print("=" * 74)
    print("CASE 2 - a column the deployed table does not have (benchmark_rates.base_quarter)")
    print("=" * 74)
    target = fresh_copy("column")
    with sqlite3.connect(target) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(benchmark_rates)")]
        if "base_quarter" not in columns:
            print("  the local database has no base_quarter; nothing to simulate")
            return True
        keep = [c for c in columns if c != "base_quarter"]
        uploads = conn.execute("SELECT COUNT(*) FROM boq_uploads").fetchone()[0]
        # SQLite cannot DROP COLUMN on older builds, so rebuild the table without it.
        conn.execute("ALTER TABLE benchmark_rates RENAME TO _old")
        conn.execute("CREATE TABLE benchmark_rates AS SELECT " + ", ".join(keep) + " FROM _old")
        conn.execute("DROP TABLE _old")
        conn.commit()
    print(f"  before: base_quarter absent, uploaded BoQs = {uploads}")

    result = run_etl(target)
    if result.returncode != 0:
        print(result.stdout[-2000:], result.stderr[-2000:])
        return False

    with sqlite3.connect(target) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(benchmark_rates)")]
        stated = conn.execute(
            "SELECT COUNT(*) FROM benchmark_rates WHERE base_quarter = '2026Q2'"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM benchmark_rates").fetchone()[0]
        after = conn.execute("SELECT COUNT(*) FROM boq_uploads").fetchone()[0]
    print(f"  after : base_quarter present = {'base_quarter' in columns}, "
          f"{stated} of {total} rows stated at 2026Q2, uploaded BoQs = {after}")
    ok = "base_quarter" in columns and stated > 0 and after == uploads
    print("  ", "PASS" if ok else "FAIL")
    return ok


def main() -> int:
    results = [check_new_table(), check_new_column()]
    print()
    if all(results):
        print("Every upgrade path applies without --reset, and uploaded BoQs survive.")
        return 0
    print("An upgrade path FAILED. Do not deploy this release without investigating.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
