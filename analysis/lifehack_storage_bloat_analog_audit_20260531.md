# LifeHack Storage Bloat Analog Audit - 2026-05-31

## Verdict

| Check | Result | Evidence |
|---|---|---|
| Same table pattern | Not found | No `fa_fact_agent_workflow_*`, `agent_workflow`, `latest_dispatch`, or `queue_items` table/code path was found in this repo. CodeGraph only surfaced workflow checkpoint constants under `backend/services/bc_absorbed/scripts/workflow_checkpoint.py`. |
| Same recursive payload pattern | Not found | Targeted DB JSON scan found no `latest_dispatch`, `queue_items`, `preview`, `audit_json` self-embedding pattern in the largest suspicious text columns. |
| Same product-unused ledger pattern | Not found | Suspicious audit tables are either bounded governance state, market-perception run summaries, or product/cache paths with explicit readers. |
| Immediate 24GB-style cleanup need | No | No LifeHack-style autopilot ledger cleanup is indicated from current evidence. Do not clear/VACUUM production tables on this basis. |
| Storage governance risk | Yes | `mart_today_signal_cache.signals_json` stores one 20.2MB JSON row with 9,286 signal dicts. It is not recursive, but it needs a storage contract and compatible migration plan. |

## Read-Only Evidence

Commands used:

```bash
git status --short
codegraph status .
codegraph context "LifeHack analog recursive audit_json workflow ledger DB bloat dispatch queue preview agent workflow"
/Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey --format markdown
rg -n "agent_workflow|workflow_task|workflow_run|workflow.*audit|dispatch.*audit|latest_dispatch|queue_items|audit_json|payload_json|preview_json|details_json" backend data docs scripts analysis -g '*.py' -g '*.md' -g '*.yaml' -g '*.json'
```

Key DB facts from read-only DuckDB/SQLite inspection:

| Object | Rows | Max text/blob | Total text/blob | Recursion/path signal |
|---|---:|---:|---:|---|
| `data/smartmoney.duckdb` | n/a | n/a | DB file 29.4 GiB | Size is dominated by large prediction/feature/financial panel tables, not audit ledger rows. |
| `mart_today_signal_cache.signals_json` | 1 | 20,220,095 bytes | 20,220,095 bytes | JSON list of 9,286 dicts; no `audit_json`, `latest_dispatch`, `queue_items`, `preview`, or `signals_json` self-reference in sample. |
| `mart_global_data_quality_gate.evidence_json` | 128 | 185,645 bytes | 4,025,706 bytes | Dict keys include `calendar`, `data_processing_monitor`, `feature_tables`, `institution_events`, `kline`, `pipeline_performance`, `stage_timings`; no recursive keywords. |
| `mart_audit_snapshot_state.audit_json` | 1 | 4,122 bytes | 4,122 bytes | Dict keys `score`, `baselines`, `layers`; no recursive keywords. |
| `mart_market_perception_audit_log` | 8 | 125 bytes `input_row_counts_json` | 681 bytes | Bounded run summary, not nested payload ledger. |
| `mart_pipeline_run_manifest.perf_summary_json` | 676 | 19,814 bytes | 1,326,806 bytes | Bounded run manifest evidence; no repeated giant blob group. |

Large DB context:

| Table | Estimated rows | Interpretation |
|---|---:|---|
| `mart_p0b_lambdamart_v6_predictions` | 23,247,364 | Model prediction rows. |
| `fact_tdx_gpcw_auto_feature_quarterly` | 16,371,042 | TDX financial feature rows. |
| `mart_p0b_oos_predictions` | 7,118,999 | OOS prediction rows. |
| `fact_risk_factors` | 4,825,425 | Daily risk factor rows. |
| `fact_feature_panel` | 4,099,596 | Feature panel rows. |

## Writer / Reader Boundaries

| Area | Writer | Reader | Assessment |
|---|---|---|---|
| Quality audit snapshot | `backend/services/audit.py:252` persists `audit_payload`; `backend/services/audit.py:280` rebuilds from `run_quality_audit()` | `backend/services/audit.py:225`, `backend/routers/market.py`, `backend/routers/updater_audit.py` | Uses a single state row, current size 4KB. No evidence that it embeds prior `audit_json`. |
| Global data quality gate | `backend/services/data_quality.py:4205` inserts `blockers_json`, `warnings_json`, `evidence_json` | Gate/report consumers and pipeline manifests | Largest evidence row 185KB. Needs threshold monitoring, not emergency cleanup. |
| Today signal cache | `backend/services/signals_v2.py:362` builds signals, `backend/services/signals_v2.py:398` stores `signals_json`; DDL at `backend/services/signals_v2.py:53` | `backend/services/signals_v2.py:255`, `backend/routers/signals.py:187`, `backend/routers/updater_calc.py:174`, `backend/services/workbench_signal_cache_read.py:169` | Real product/cache path, not unused dev ledger. Main storage smell: whole 9,286-item payload in one row. |
| Market perception audit log | `backend/scripts/build_market_perception_daily.py:228` writes bounded run summary; DDL at `backend/services/schema_marts.py:82` | `backend/routers/v3_market_perception.py:157` reads latest audit | Current 8 rows, small text. No same-pattern risk. |

## Decision

Do not apply the LifeHack cleanup action (`clear 3 tables + VACUUM`) to this project. The matching root cause is absent.

Keep the follow-up as P0/P1 storage governance:

1. Add a repeatable read-only storage-payload audit with size thresholds for large TEXT/BLOB, repeated large blobs, path-to-report/log rows, and nested report/log dirs.
2. Migrate `mart_today_signal_cache.signals_json` behind a compatible design: summary/index in DB plus queryable detail table or governed artifact reference.
3. Add regression coverage for `/api/signals/today` cache hit/miss and updater `refresh_today_signals`.
4. Only after consumer coverage and migration evidence exist, consider pruning old cache rows or running a scoped DuckDB maintenance/VACUUM plan.
