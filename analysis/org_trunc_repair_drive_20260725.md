# Org truncation repair drive (2026-07-25)

> Evidence-only · log `analysis/org_trunc_repair_ops_20260724.log` · PID **40365** session

## Counts

| Metric | Value |
|---|---|
| **truncated_before** | **23** periods (listed in log `truncated_before`) |
| **truncated_after** (read-only audit) | **20** (`list_truncated_org_periods`, RO connect before lock contention) |
| **Session progress** | Repair loop reached **23/23** (`2025-06-30`) then **FAILED** |

## Outcome

**Label: PARTIAL**

- **22+ periods** processed in one `--max-periods 23` run (log lines `1/23` … `23/23`).
- **Final period** `2025-06-30`: `accept` **COMMIT** failed — `No space left on device` during DuckDB checkpoint (`smartmoney.duckdb` grew **~8.7GB → ~11GB** during run; volume **~100%** full, **~1.1GiB** avail).
- Post-crash: writer connection **invalidated**; **do not** resume repair until **disk headroom** restored and no stale writer holds lock (observed holder PID **813** after crash).
- **Residual truncated ≈20** (not ≈0): re-run bounded repair after hygiene — likely **≤3** periods still failing pagination audit plus any periods rolled back with failed commit.

## Next verification (ops)

1. Free disk on `/System/Volumes/Data` (target **≥15GB** headroom before large DuckDB checkpoint).
2. Ensure **no** DuckDB writer (`lsof data/smartmoney.duckdb`).
3. RO audit: `list_truncated_org_periods` → expect **0** when FIXED.
4. If >0: `org_holding_period_repair_truncated.py --max-periods N` (≤40), one writer.

## QFII

Not started (blocked on org trunc stable + DB free).
