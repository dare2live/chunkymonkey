# DB storage hygiene — free-block bloat & `data/archive/` (2026-07-21)

> **生命周期**：evidence-only / executed reclaim note（非 owner contract；不替代 eng_gov §10）
> Authority: `docs/engineering_governance.md` §10; `backend/scripts/db_lifecycle_delete.py`;
> `backend/scripts/db_compact.py`; `analysis/db_layering_toplevel_design_20260721.md` §3.1 / §5.3.

## Verdict

| Item | Reasonable? | Action |
|---|---|---|
| Free blocks after DROP (~1.28 GiB) | Yes — DuckDB does not shrink files on `DROP`/`DELETE` | Compacted via approved `db_compact.py` |
| `data/archive/**` | Yes — governed reversible-delete evidence | **Kept** (all four subdirs) |
| `etf.duckdb` husk (only `mart_data_deletion_record`) | Useless as live DB; evidence already in archive | **Deleted** after exporting deletion_record parquet |
| Panel / circular junk outside archive | None live | n/a |

## Mechanisms

| Mechanism | Where | Effect |
|---|---|---|
| Lifecycle / purge DROP | `backend/scripts/db_lifecycle_delete.py` (`--execute`) | Archives parquet (if `action=archive`) → writes `mart_data_deletion_record` → `DROP TABLE/VIEW`. Docstring + footer: **DROP does not reclaim file blocks → must run `db_compact`**. Occasional `CHECKPOINT` only refreshes catalog after mass DROP (anti-stale), not shrink. |
| Manifest-driven archive roots | analysis manifests (`archive_dir:`) | `data/archive/lifecycle`, `purge_processed`, `purge_batch2`, `purge_batch3` |
| Approved reclaim | `backend/scripts/db_compact.py --db <alias> --execute` | ATTACH-copy fidelity rewrite (DDL+INSERT+indexes+views); verify row/constraint/index parity; rename; leave `*_precompact_bak.duckdb` until verified then delete bak |
| `CHECKPOINT` alone | DuckDB 1.5.2 / this repo | Flushes WAL / catalog; **does not** reclaim free blocks into a smaller file |
| Policy | eng_gov §10; db_layering §5.3 | Delete only after coupling/impact; migrate durable evidence; archive parquet = offline fuse, not a 7th DuckDB; formal retention truth = `mart_data_deletion_record` + ledger |

### Why ~1.2 G “compactable holes” (measured before reclaim)

| DB | File | free_blocks | free bytes | free % |
|---|---|---|---|---|
| `market.duckdb` | 1.52 GiB | 2897 | ~759 MiB | 49.9% |
| `smartmoney.duckdb` | 2.16 GiB | 1525 | ~400 MiB | 18.5% |
| `feature_store.duckdb` | 183 MiB | 455 | ~119 MiB | 65.3% |
| **Sum free** | | | **~1.28 GiB** | |

Root cause: many 2026-06/07 lifecycle + purge batches dropped tables (qfq rebuild churn, U1 panel wipe, ETF retirement, etc.) and ran `CHECKPOINT` without a follow-up `db_compact`.

## `data/archive/` classification — keep

| Subdir | Size (approx) | Writers | Policy role |
|---|---|---|---|
| `lifecycle/` | ~473 MiB | `db_lifecycle_delete` + `archive_dir: data/archive/lifecycle` manifests | Cold parquet for archived drops (TDX/GPCW/ETF/dim/…); **required fuse** |
| `purge_processed/` | ~563 MiB | purge_* manifests (`purge_processed`) | U1–U5 / quality-gate / GT archaeology evidence; read by e.g. `analysis/d1_gt_archaeology_20260702.md` |
| `purge_batch2/` | ~7 MiB | batch2 manifests | LHB / institution_survey retirement evidence |
| `purge_batch3/` | ~121 MiB | batch3 manifests | ETF/market legacy / holders / fund_daily retirement evidence |

**Not** accidental junk. eng_gov forbids inventing a second “docs archive” directory for prose; **this** archive is the data-deletion fuse named by lifecycle tooling and db_layering. Do **not** mass-delete.

## Panel / circular junk outside archive

- Live DBs: **no** `fact_feature_panel` / `fact_signal_panel` / `fact_segment_panel` / circular-ref tables.
- Those panels live only under `data/archive/purge_processed/` (and related manifests). Manifest note in `database_manifest.yaml` `feature_store` role already states panel retirement.

## Actions taken (2026-07-21)

1. **Export + delete `data/etf.duckdb`** (524 KiB husk; only 10-row `mart_data_deletion_record`; already absent from `database_manifest.yaml`).
   - Evidence migrated to `data/archive/lifecycle/etf_duckdb_mart_data_deletion_record.parquet` (10 rows, round-trip verified).
   - Removed empty `etf.duckdb:` stub from `backend/config/field_dictionary.yaml`.
2. **Compact** (row/constraint/index parity OK) then **delete** `*_precompact_bak.duckdb`:
   - `feature_store`: 0.2 G → 0.1 G (free_blocks 455 → 0)
   - `market`: 1.5 G → 0.8 G (free_blocks 2897 → 1)
   - `smartmoney`: 2.2 G → 1.7 G (free_blocks 1525 → 4)
3. **Did not** touch `tushare_raw.duckdb`, live tables, or any `data/archive/` subdir contents (except the new etf deletion_record export).

### Bytes freed (local data plane; gitignored)

| Slice | Before | After | Freed |
|---|---|---|---|
| market + smartmoney + feature_store | ~3.86 GiB | ~2.53 GiB | **~1.33 GiB** |
| `etf.duckdb` | 524 KiB | gone | ~524 KiB |
| **Total** | | | **~1.33 GiB** |

(`data/archive/` still ~1.1 GiB — kept by policy.)

## Residuals

| Residual | Owner / next |
|---|---|
| `experiment_store.duckdb` ~30 free blocks (~7.5 MiB file bloat) | Optional tiny compact; not in this reclaim |
| Future DROP batches | Always follow with `python backend/scripts/db_compact.py --db <alias> --execute` then delete bak after parity check |
| **qfq DROP+CTAS (market)** | **FIXED 2026-07-23 Knife 3** — `build_price_kline_qfq_tushare` defaults to post-rebuild `db_compact --db market` + remove bak; escape `--no-compact` / `CHUNKY_QFQ_SKIP_COMPACT=1`. Manual compact still: `python backend/scripts/db_compact.py --db market --execute` |
| `data/archive/**` growth | Expected under lifecycle; prune only with per-subdir obsolete proof + eng_gov §10 |
| `tushare_raw.duckdb` free_blocks≈133 | Intentional skip this reclaim (Tier0 write plane; compact only with explicit owner focus) |

## Commands (reproduce)

```bash
# measure free blocks
python -c "import duckdb; c=duckdb.connect('data/market.duckdb',read_only=True); print(c.execute('SELECT * FROM pragma_database_size()').fetchall())"

# approved compact
python backend/scripts/db_compact.py --db market --execute
# after parity: rm data/market_precompact_bak.duckdb
```
