# Type-B same-run publish catchup (2026-07-24)

> Status: evidence-only · **SHIPPED** (Knife 2) · Closes raw→fact tip lag on daily acquire  
> Bound: ≤40 calendar days per domain per run; never `start/end=None`

## Problem

Registry drain lands raw rows but Type-B `fact_*` tables lag 3–7d (PUBLISH_LAG in
`partition_leap_integrity_20260724.md`).

## Solution

After `_sync_registry_drain`, `catchup_type_b_fact_publish` compares
`MAX(trade_date)` raw vs fact for:

- moneyflow, moneyflow_dc, limit, index_daily, dc_member, top_inst_seat

When raw leads fact, publish `[fact_max+1 .. min(raw_max, start+39)]` only.

Manifest: `delta_manifest.acquire_summary.type_b_publish`.

## Residual verification

Live: compare MAX(trade_date) raw vs fact per domain after one daily_update;
lag should shrink within published window (not instant full history).
