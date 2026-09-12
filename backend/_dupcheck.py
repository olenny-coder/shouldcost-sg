"""Reproduce the deployed duplicate-rate bug and prove the ETL now cleans it up.

The deployed database ended up with BOTH the old invented library and the new
SOR-derived one, because the natural key includes the description and the source, which
changed. The engine then chose by confidence - and for Singapore Concrete the OLD row was
'medium' against the new 'low', so the deployed app was pricing against the invented rate.
"""
import os, sqlite3, subprocess, sys, tempfile, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
target = Path(tempfile.mkdtemp(prefix="shouldcost-dup-")) / "dup.db"
shutil.copy2(HERE / "shouldcost.db", target)

# Recreate the deployed mess: the OLD invented rows, copied in alongside the new ones.
OLD = [
    ("SG", "Concrete", "SMM2", "Reinforced concrete to pile caps and ground beams", "m3",
     145.0, "SGD", 2010, "BCA Construction InfoNet / SISV rate library", "medium"),
    ("SG", "Reinforcement", "SMM2", "High yield deformed bar reinforcement", "tonne",
     1150.0, "SGD", 2010, "BCA Construction InfoNet / SISV rate library", "medium"),
    ("SG", "Excavation", "SMM2", "Excavation to reduce level", "m3",
     18.0, "SGD", 2010, "BCA Construction InfoNet / SISV rate library", "medium"),
    ("IN", "Plaster", "IS 1200 / CPWD DSR", "Cement plaster 12mm to internal walls", "m2",
     340.0, "INR", 2023, "CPWD Delhi Schedule of Rates (DSR) 2023", "medium"),
]
with sqlite3.connect(target) as conn:
    for c, s, std, desc, unit, rate, cur, yr, src, conf in OLD:
        conn.execute(
            "INSERT INTO benchmark_rates (country, smm2_section, classification_standard,"
            " description, unit, base_rate, currency, base_year, base_quarter, source,"
            " source_url, source_date, scope_inclusions, scope_exclusions, confidence,"
            " is_placeholder, provenance_note, replace_with)"
            " VALUES (?,?,?,?,?,?,?,?,'',?,'','','','',?,1,'legacy row','# TODO: legacy')",
            (c, s, std, desc, unit, rate, cur, yr, src, conf),
        )
    conn.commit()
    before = conn.execute(
        "SELECT country, smm2_section, COUNT(*) FROM benchmark_rates GROUP BY 1,2 HAVING COUNT(*)>1"
    ).fetchall()
print("duplicated sections before:", before)
print("total rows before:", sqlite3.connect(target).execute("SELECT COUNT(*) FROM benchmark_rates").fetchone()[0])

env = dict(os.environ)
env["DATABASE_URL"] = "sqlite:///" + target.as_posix()
proc = subprocess.run([sys.executable, "-m", "app.etl"], cwd=HERE, env=env,
                      capture_output=True, text=True)
print("etl exit:", proc.returncode)
if proc.returncode != 0:
    print(proc.stdout[-1500:], proc.stderr[-1500:]); raise SystemExit(1)

with sqlite3.connect(target) as conn:
    total = conn.execute("SELECT COUNT(*) FROM benchmark_rates").fetchone()[0]
    left = conn.execute(
        "SELECT country, smm2_section, COUNT(*) FROM benchmark_rates GROUP BY 1,2 HAVING COUNT(*)>1"
    ).fetchall()
    sg_concrete = conn.execute(
        "SELECT base_rate, confidence, base_quarter FROM benchmark_rates"
        " WHERE country='SG' AND smm2_section='Concrete'"
    ).fetchall()
print("total rows after :", total)
print("duplicated after :", left)
print("SG Concrete rows :", sg_concrete)

ok = total == 20 and not left and sg_concrete == [(140.53, "low", "2026Q2")]
print("RESULT:", "PASS" if ok else "FAIL")
shutil.rmtree(target.parent, ignore_errors=True)
raise SystemExit(0 if ok else 1)
