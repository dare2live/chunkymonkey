# org_holding accepted pointer partition semantics — 2026-07-27

> Lifecycle: evidence-only · Label: **FIXED** (class-A write path + live backfill)
> Authority: FOUNDATION residual / E0 accept evidence

## Defect

`canonical_delete_scope=report_dates_in_batch` correctly merges multiple
`report_date` into one `available_date` partition, but `accepted_partition`
stored **last batch** `row_count`/`content_hash`. PK `(dataset_id, partition_value)`
cannot hold multi-batch pointers — the pointer must describe the **merged**
canonical partition.

Live before repair: **8/22** mismatch (all `*0430` dual-report windows).
Example `20260430`: pointer 832906 vs canonical 944837
(`20251231`+`20260331`).

## Fix

1. Accept path: after merge insert, `partition_accepted_pointer_stats()` sets
   pointer from full canonical partition; ingest_batch keeps batch-scoped counts.
2. Test: sibling merge asserts pointer `row_count==2` and hash ≠ either batch hash.
3. F6: 双向 count + full canonical content hash `org_pointer_mismatches` fail-closed.
4. Live repair: `repair_org_holding_accepted_pointers.py` → 8 partitions updated；后续版本已补全单事务、missing-side fail-closed 与 post-verify。

## Live after

- count mismatches: **0**
- `20260430` pointer: **944837**
- F6 detail includes `org_pointer_mismatches=0`

Artifact: `analysis/org_holding_pointer_repair_20260727.json`

## Residual (not this knife)

- Strategy reader 已绑定 snapshot accepted + canonical hash；但 disclosure freeze 仍缺 nominal accepted，main-rally freeze 越过 holdout，formal RX 继续 **BLOCKED**
- Optuna / Release still banned
