# Foundation full audit + test (2026-07-25)

> Status: evidence-only
> Scope: data foundation gates + live frontiers + blocking pytest (no RX).
> Label: **PARTIAL** (gates green; class-B observes remain; Continuity ≠ READY claim)

## Verdict

| Layer | Result |
|---|---|
| Foundation F1–F10 (`check_foundation_done`) | **PASS** · `phase_closure_ready=True` |
| Continuity integrity | **PASS** 112/0/0 · skipped=3 · `latest_expected=20260724` |
| Population readiness (doctor) | **READY** (`accepted_calendar_kline_st_sources_visible`) |
| Doctor overall | **WARN** (1 yellow asset; 0 red; 0 blocking_yellow) |
| Blocking pytest | **1176 passed** |
| Moth assert | **PASS** 35/0/0 |
| Static transport/contract gates | **PASS** (see matrix) |
| Live domain holes (org hard / QFII missing) | **0 / 0** |
| Continuity READY / RX | **not claimed** / **BLOCKED** until `goal.md` schedule |

**Overall**: foundation exit still **MET** as previously closed; this re-audit finds **no class-A reopen**. Residual = class-B freshness observes + soft org baseline observe + raw tip lag note.

## Gate matrix (measured 2026-07-25)

| Gate | Verdict |
|---|---|
| moth assert | PASS |
| serve_read_layer D1–D5 | PASS · violations=0 |
| calendar_usage | PASS |
| lineage_drift | PASS · 441 nodes |
| brick_registry | PASS · orphans=0 |
| legacy_raw_plane | PASS · ssot=20 · retired=3 |
| factor_family inventory | PASS · families=7 |
| factor_family gates | PASS · modes=6 |
| serve_derive_closed_loop | PASS *(after checker fix: Type-B `binding=daily_acquire` owned by acquire.py)* |
| foundation_done F1–F10 | PASS |
| continuity_integrity | PASS |
| grain_uniqueness | PASS · 51/51 |
| doctor --fast | WARN |
| run_ci_pytest --tier blocking | 1176 passed |

Logs: `/tmp/foundation_audit_20260725/`

## Live frontiers (read-only DuckDB)

### Formal accepted (`data/tushare_raw.duckdb`)

| dataset_id | min | max | n |
|---|---|---|---|
| `tier0.market_data.nominal_ohlcv_daily` | 20190102 | **20260724** | 1833 |
| `tier0.security_identity.stock_st_daily` | 20220104 | **20260724** | 1103 |
| `tier0.market_data.margin_exchange_daily` | 20190102 | **20260723** | 1828 |
| `tier0.disclosure.stock_holder_trade_announcement` | 20190102 | 20260723 | 2223 |

Canonical OHLCV tip matches accepted: `canonical_nominal_ohlcv_daily` max **2026-07-24**.

### Disclosure (`data/smartmoney.duckdb`)

| dataset_id | min | max | n |
|---|---|---|---|
| org_holding_detail_period (available_date partitions) | 20190430 | 20260430 | **22** |
| top10_float_holders_period | 20190104 | 20260725 | 1957 |

Org **raw report periods**: 30/30 calendar · hard_trunc=0 · soft `under_modern_baseline`=19 (observe only).  
QFII: **30 periods · missing=[]** (2018-12-31→2026-03-31).

### Doctor yellow (class-B)

| table | severity | note |
|---|---|---|
| `fact_top10_holder_period` | yellow | `last_data_date=20260715` · SLA 168h · freshness≈245h · **not** blocking_yellow |

Accepted holders frontier (20260725) ahead of fact `report_date` tip — treat as serve/fact freshness observe, not continuity FAIL (continuity PASS).

### Observe (not fail)

1. `raw_tushare_daily` MAX=`20260716` while accepted/canonical OHLCV=`20260724` — dual-path / stage land; formal tip authoritative for continuity.
2. Margin accepted tip **20260723** vs OHLCV **20260724** — 1 trading-day lag; continuity skipped margin cross-section insufficient history earlier.
3. Org soft under_modern_baseline=19 — intentional after `d7bf8111b`.

## Checker fix in this audit

`check_serve_derive_closed_loop.py` previously required every `process_step` string inside `plan_process_steps` source. Type-B surface uses `process_step: acquire_after_registry_drain` with `binding: daily_acquire` (wired in `acquire.py` via `run_acquire_type_b_publish_catchup`). That was a **false FAIL**. Checker now routes `daily_acquire` bindings to acquire.py wiring.

## Hygiene

Added `> Status: evidence-only` to dirty `analysis/ui_daily_update_e2e_20260724.md` (was breaking moth doc-governance).

## What this does **not** open

- RX / Optuna / StrategyRelease
- Continuity READY claim
- Mass org re-pull / announcement-title invent

## Residual owner

| Item | Owner next |
|---|---|
| fact_top10 yellow freshness | holders fact publish / notice lag diagnosis (bounded) |
| raw_daily tip vs accepted | confirm land path uses stage≠`raw_tushare_daily` tip or catch up raw |
| soft org baseline 19 | observe only |
| RX | `goal.md` explicit schedule |
