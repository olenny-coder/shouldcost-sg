# Compiled dataset

The seed files the ETL loads, and the reason each one exists. **These files, and everything derived
from them, are licensed CC BY 4.0** - see `LICENSE-DATA` in the repository root. The application
source code is MIT.

| file | rows | what it holds |
|---|---|---|
| `sor_items.csv` | 2,330 | both schedules of rates as one catalogue: every item code, description, section, unit (and the schedule's own unit), and the rate escalated to the library quarter. Built by `tools/build_sor_items.py` from the publisher extracts |
| `benchmark_rates.csv` | 20 | the ten-section rate library per market: eleven rows derived from the schedules of rates at `base_quarter` 2026Q2, nine retained estimates for sections the loaded extracts do not reach |
| `tpi_series.csv` | 126 | construction cost index quarters (BCA, HDB, RLB, AECOM, CPWD, NBO, and the India WPI baskets) |
| `cpi_series.csv` | 146 | consumer price observations, the series that carries a stale index to the tender quarter |
| `price_series.csv` | 786 | producer price indexes, the preferred series for that carry-forward |
| `material_prices.csv` | 180 | cement, steel reinforcement and ready-mixed concrete prices |
| `regional_factors.csv` | 13 | city cost multipliers |
| `sample_boq.csv`, `sample_boq_india.csv` | 25 / 22 | the two demonstration bills, built from real schedule lines |

Every row carries `source`, `source_url` and `source_date`, plus a `provenance_note` explaining how
the value was obtained and an `is_placeholder` flag distinguishing a published observation (or a rate
derived from one) from a retained or modelled value. `python tools/verify_seed_data.py` re-derives
each value from the publisher's own file and fails on any drift.

The dataset compiles third-party publications (BCA Schedule of Rates, CPWD Delhi Schedule of Rates,
NBO city cost indices, and the statistics releases of SingStat, the Office of the Economic Adviser,
MoSPI and the Labour Bureau). The CC BY 4.0 licence covers this compilation and the derivation work,
not those publications, which remain subject to their own publishers' terms. Each is cited rather
than reproduced, and named per row so a reader can go to the source. `LICENSE-DATA` lists them.
