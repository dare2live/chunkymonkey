# Phase 3.6 Pattern 9 audit — perception_absorbed engines

**Date**: 2026-05-24
**Scope**: backend/services/perception_absorbed/ — 7 engines
**Pattern 9 definition** (docs/leakage_pattern_catalog.md:145): `PARTITION BY date, tdx_l1` where `tdx_l1` is from FLAT NON-PIT mapping (e.g., `dim_stock_tdx_industry`) → retrospective industry assignment leakage. Phase D 反例: panel v3/v4 sector_*_tdx_l1_rel features → 92.43% IS-OOS drop.

## Verdict: CLEAN

## Audit method

1. `grep -rn "PARTITION BY"` all 7 engines (26 sites total)
2. Classify each PARTITION BY key by source:
   - Pure date/snapshot_date → no mapping → SAFE
   - stock_code/code for LAG (price returns) → no mapping → SAFE
   - theme_name from PIT-correct mart_stock_industry_pit → PIT-correct → SAFE
3. `grep -rn "dim_stock_tdx_industry"` for flat NON-PIT mapping references

## Findings

| Engine | PARTITION BY sites | All safe? | Notes |
|---|---|---|---|
| emotion_engine.py | 3 | Yes | PARTITION BY date / code (price LAG) only |
| regime_engine.py | 2 | Yes | PARTITION BY code (price LAG) only |
| leader_follower_engine.py | 6 | Yes | PARTITION BY stock_code (price LAG); theme_name via PIT industry JOIN |
| stock_context_engine.py | 2 | Yes | PARTITION BY snapshot_date / snapshot_date+follower_stock_code (raw code) |
| style_rotation_engine.py | 3 | Yes | PARTITION BY stock_code (price LAG) |
| theme_lifecycle_engine.py | 4 | Yes | PARTITION BY snapshot_date+theme_name where theme_name = ip.tdx_l1_name (PIT JOIN) |
| under_reaction_engine.py | 4 | Yes | PARTITION BY stock_code/code (price LAG) |

**Total PARTITION BY sites**: 24 in business SQL; 0 use flat NON-PIT mapping.

**Flat mapping reference count**: `dim_stock_tdx_industry` → **0 hits** across all 7 engines.

**PIT mapping source**: All `tdx_l1_name` derives from `mart_stock_industry_pit` JOIN with:
- `effective_from <= snapshot_date <= effective_to` (range PIT)
- `confidence_level = 'observed_snapshot'` (excludes inferred/imputed)
- `(built_at IS NULL OR built_at <= as_of)` (Phase 3.2 patch — silent rebuild guard)

## Tools used

- Manual `grep -rn "PARTITION BY"`
- Manual `grep -rn "dim_stock_tdx_industry"`
- audit_panel_leakage.py check 3 (Pattern 9) not run here because it requires a built panel table; absorbed engines produce marts dynamically, not a fixed panel. To be re-validated after Phase 4.1 builds unified panel.

## Phase 4.1 follow-up

When `mart_p0a_feature_label_panel_unified_v1` is built (Phase 4.1), run:

```bash
PYTHONPATH=backend python backend/scripts/audit_panel_leakage.py \
  --panel mart_p0a_feature_label_panel_unified_v1 --strict
```

Check 3 must pass (no flat current-mapping PARTITION BY).

## Conclusion

Phase 3.6 status: **CLEAN** — perception_absorbed has 0 Pattern 9 risk because:
1. No flat NON-PIT mapping referenced
2. All industry/theme partitions derive from PIT-correct `mart_stock_industry_pit`
3. Built_at filters from Phase 3.2 guard against silent rebuild rebound

Pre-condition for Phase 4.1 (unified panel) is satisfied on the perception side.
