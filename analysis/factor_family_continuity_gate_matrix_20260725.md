# Factor-family continuity gate matrix (K2) — 2026-07-25

> Status: evidence-only · Label: **FIXED（结构门）** · live DuckDB frontier 投影 = 下一刀  
> Authority: `analysis/factor_family_governance_toplevel_20260724.md`  
> Owner: `backend/scripts/check_factor_family_gates.py` · `backend/services/factor_family_continuity_gates.py`

## What shipped

Frequency-typed **continuity/readiness** expectations on `factor_family_inventory.yaml`:

| family | mode | stack |
|---|---|---|
| `price_volume_daily` | `calendar_gaps` | ready |
| `stock_state_form` | `derive_accepted_frontier` | ready |
| `market_sensing_breadth` | `derive_accepted_frontier` | ready |
| `vendor_flow_proxy` | `type_b_publish_defer` | defer |
| `disclosure_holders_event` | `event_notice_partitions` | ready |
| `org_disclosure_period` | `period_gap_bounded` | defer |
| `formula_single` | `inventory_blocked` | blocked |

Machine gate validates: mode ↔ frequency/axis; event/quarterly **forbid** `calendar_gaps`; defer/blocked require `wired` + reasons; `gate_matrix` check ids ↔ per-family modes.

## Evidence

```bash
PYTHONPATH=backend .venv/bin/python3 backend/scripts/check_factor_family_gates.py --json
PYTHONPATH=backend .venv/bin/python3 -m pytest backend/tests/services/test_factor_family_continuity_gates.py -q
```

Moth: `factor-family-continuity-gate-matrix` claim in `.moth/assertions/claims.yaml`.

## Residual

- **PARTIAL（live）**: no DuckDB family frontier projection yet (K2 frontier report in backlog).
- Org heuristic trunc flags (~19) unchanged — not mass-repair.

## Git

SHA: _(fill after safe_commit)_
