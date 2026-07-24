# Org truncation repair drive (2026-07-25)

> Evidence-only · log `analysis/org_trunc_repair_ops_20260724.log` · PID **40365** session

## Counts

| Metric | Value |
|---|---|
| **truncated_before** | **23** periods (listed in log `truncated_before`) |
| **truncated_after** (read-only audit) | **20** (`list_truncated_org_periods`, RO connect before lock contention) |
| **Session progress** | Repair loop reached **23/23** (`2025-06-30`) then **FAILED** |

## Outcome

**Label: PARTIAL** (page-cap **`2025-06-30` FIXED** 2026-07-25)

- **2025-06-30:** `745991` rows · `5464` stocks · `truncated=false` · 9 shards (`20260724T235226Z` report).
- Heuristic truncated count **20→19**; remaining flags = baseline-ratio on older periods (not ~200k cap land).

## Next verification (ops)

1. Free disk on `/System/Volumes/Data` (target **≥15GB** headroom before large DuckDB checkpoint).
2. Ensure **no** DuckDB writer (`lsof data/smartmoney.duckdb`).
3. RO audit: `list_truncated_org_periods` → expect **0** when FIXED.
4. If >0: `org_holding_period_repair_truncated.py --max-periods N` (≤40), one writer.

## QFII

Not started (blocked on org trunc stable + DB free).
