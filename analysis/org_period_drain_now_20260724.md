# Org period ops drain + Type-B publish catchup (2026-07-24)

> Status: evidence-only · **FIXED** (org holes drained to 0; Type-B already caught up)  
> Agent session: explicit ops catchup sprint (not daily_update N=1 semantics)

## After metrics (live DB, drain complete)

| Metric | Session before | After (v10 complete) |
|---|---:|---:|
| `missing_count` | 27 | **0** |
| `missing_older_count` | 27 | **0** |
| `fill_target_period` | 2018-12-31 | **null** (no older holes) |
| `plannable` | 2026-03-31 | 2026-03-31 (present) |
| `next_period` / unlock | 2026-06-30 / 2026-08-31 | unchanged (frontier skip) |

Evidence: `data/reports/org_holding_period_gap_latest.json` (`missing_older_count=0`, `missing_periods=[]`); dry-run `org_holding_period_drain.py` returns `status=completed` with zero remaining.

### Drain progress timeline

| Phase | Log | Before → after | Note |
|---|---|---:|---|
| v4 | `/tmp/org_drain_20260724_v4.log` | 25 → 22 | filled 2019-09-30…2020-03-31; failed on 2020-06-30 (`Response ended prematurely`) |
| v6 | `/tmp/org_drain_20260724_v6.log` | 22 → 14 | filled 2020-06-30…2022-03-31 (8 periods); process exited mid-session |
| v9 | `/tmp/org_drain_20260724_v9.log` | 14 → 14 | DNS failure on 2022-06-30 (`NameResolutionError`) |
| v10 | `/tmp/org_drain_20260724_v10.log` | 14 → **0** | filled 2022-06-30…2025-09-30 (14 periods); `status=completed` |

All local periods now present from `2018-12-31` through `2026-03-31` (quarter ends).

---

## User question answered

### Why did it look like "~27 daily runs"?

`ORG_PERIOD_CATCHUP_MAX=1` in `org_holding_period_catchup.py` is an **anti-mass guard for automatic `daily_update`**, not a ban on explicit ops draining. The prior evidence note (`org_period_bounded_fill_20260724.md`) described decrement-by-1 per daily run as the **auto** expectation; the owner challenge requires **immediate hole drain** via an explicit ops loop.

| Mode | Throughput | Entry |
|---|---|---|
| **Auto** (`daily_update` / pipeline acquire) | N=1 oldest missing period per run | `sync_org_holding_incremental` |
| **Ops** (this session) | Loop until `missing_older_count→0` (cap ≤40/session) | `backend/scripts/org_holding_period_drain.py` |

Anti-mass unchanged: each iteration = one `sync_period(..., allow_existing_refresh=False)`; never `backfill()`; never refresh populated periods.

### Type-B: why not "next daily_update"?

Type-B fact publish is **call-on-raw-ready**. Pipeline wiring (`run_acquire_type_b_publish_catchup` in `acquire.py`) is correct for future runs. This session invoked catchup **now** via `backend/scripts/type_b_fact_publish_catchup_cli.py`.

## Before → after (live DB)

### Type-B lag (6 domains)

| Domain | raw_max (before) | fact_max (before) | Lag? | After catchup |
|---|---:|---:|---|---|
| moneyflow | 20260723 | 20260723 | no | skipped (caught up) |
| moneyflow_dc | 20260723 | 20260723 | no | skipped |
| limit | 20260723 | 20260723 | no | skipped |
| index_daily | 20260723 | 20260723 | no | skipped |
| dc_member | 20260723 | 20260723 | no | skipped |
| top_inst_seat | 20260723 | 20260723 | no | skipped |

**Type-B label: FIXED** (already caught up; catchup executed and confirmed `fact_caught_up` for all 6).

### Org holes

| Metric | Before | After |
|---|---:|---:|
| `missing_count` | 27 | **0** |
| `missing_older_count` | 27 | **0** |
| `fill_target_period` | 2018-12-31 | **null** |

## Code fixes shipped (blockers found during live drain)

1. **`sync_period` mass guard** — refuse refresh by **report_date row presence** (raw or canonical), not shared `available_date` accepted pointer (2018-12-31 was blocked by sibling 2019-03-31 on partition 20190430).
2. **Org formal accept merge** — `canonical_delete_scope=report_dates_in_batch` so accepting one quarter does not wipe sibling report_dates sharing the same `available_date` partition.
3. **Provider duplicate grains** — `_normalize_rows` dedupes by grain (last wins) before formal land (observed ~3 dupes / 200k rows on 2018-12-31 probe).

## Commands run

```bash
# Type-B catchup (all 6 domains)
python3 backend/scripts/type_b_fact_publish_catchup_cli.py

# Org ops drain (oldest-first; resumed after connection/DNS failures)
python3 backend/scripts/org_holding_period_drain.py --max-partitions 22   # v6
python3 backend/scripts/org_holding_period_drain.py --max-partitions 14   # v10 → 0 remaining

# Targeted tests
python3 -m pytest backend/tests/services/test_org_holding_acceptance.py \
  backend/tests/test_org_holding_aif10.py \
  backend/tests/services/test_org_holding_period_catchup.py -q
# → 32 passed
```

Env override for ops cap: `ORG_PERIOD_DRAIN_MAX` (default 40 in script; daily auto unchanged at `ORG_PERIOD_CATCHUP_MAX=1`).

## Residuals

- Landing table may retain rejected batch rows from pre-fix probes (evidence only; not canonical truth).
- Some provider pages returned ~200000 row caps on deep periods (written counts in v10 log); population probe still reports `under_populated=false` for plannable frontier.
- Next unlock: `2026-06-30` after `2026-08-31` (normal frontier; not a hole).

## Label

| Item | Status |
|---|---|
| Type-B same-run publish (live invoke) | **FIXED** |
| Ops drain entrypoint | **FIXED** (script added) |
| Org hole count → 0 | **FIXED** (27 → 0) |
| Partition collision accept | **FIXED** (merge by report_date) |
