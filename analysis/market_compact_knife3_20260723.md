# Knife 3 — Market qfq free-block compact — 2026-07-23

> **生命周期**：evidence-only（execution evidence; no authority over contracts）
> Evidence for `analysis/global_cleanup_rebuild_plan_20260723.md` Knife 3.
> Label: **FIXED** (one-shot reclaim + **in-module** post-CTAS compact in update/derive/clean path).
> Ban honored: no margin thaw; no holders landing delete; no Continuity READY cosmetics; 补跑≠正解.

## Measured (Asia/Shanghai)

| Metric | Before (~14:50) | After (~14:51) |
|---|---|---|
| `data/market.duckdb` | 1 545 089 024 B (**1.439 GiB**) | 772 288 512 B (**0.719 GiB**) |
| `free_blocks` | **2940** | **1** |
| `used_blocks` | 2958 | 2945 |
| `price_kline_qfq_tushare` rows | 8 412 670 | 8 412 670 |
| Reclaimed | | **≈0.720 GiB** |
| `market_precompact_bak.duckdb` | n/a | removed after parity |

Command:

```bash
python backend/scripts/db_compact.py --db market --execute
# parity (rows/constraints/indexes) OK → rm data/market_precompact_bak.duckdb
```

Serialize: `lsof data/market.duckdb` empty; peer margin catchup targets other DuckDBs — no kill.

## Recurrence fix (owner correction — update flow, not one-shot补跑)

**Binding**: 补跑 `db_compact`  alone ≠ 正解. Reclaim must live **inside qfq/market module** so each derive/clean update does not re-bloat. Orchestrator keeps calling `derive_qfq` / clean script only — **no** free-page awareness in daily_update.

Landed in `backend/scripts/build_price_kline_qfq_tushare.py` (writer of `price_kline_qfq_tushare`):

- After successful rebuild (not `--check-only`), close market writer, then `compact_market_after_ctas(remove_bak=True)` → `db_compact.run("market", execute=True)` + unlink bak.
- Only when `MARKET_DB` resolves to production market path (tests/tmp skip).
- Escape: `--no-compact` or `CHUNKY_QFQ_SKIP_COMPACT=1`.
- Compact failure → rc=**3** (qfq rows OK; reclaim residual visible to pipeline).

Call graph (unchanged boundaries):

- `pipeline/clean.py` → `build_price_kline_qfq_tushare.py` (script)
- `derive_runtime.run_derive("qfq")` → `mod.main(...)`
- Ops `derive_qfq` → same

Neither clean nor orchestrator imports DuckDB free-block logic.

Incremental/partitioned qfq write remains a **deferred product knife** (optional later; hook makes full CTAS ops-safe in the update path).

## Foundation bar residual (honest)

| Slice | Status |
|---|---|
| Market free-block after qfq CTAS | **FIXED** (compact now + hook prevents instant return) |
| Holders landing ~32× historical | **KEEP** — retention/archive later; skip-land path already shipped |
| Continuity overall | Still **WARN** domain residuals — not READY by cosmetics |
| Margin product thaw / rzrqye | **Banned** / UNTRUSTED unchanged |

## Tests

- `backend/tests/scripts/test_build_price_kline_qfq_tushare.py::test_main_compacts_market_after_successful_rebuild`
