# Workflow Checkpoint

Business-level pipeline tracker. Session-level state remains in SESSION_HANDOFF.md.

Current Codex architecture/worktree-governance state is tracked in `goal.md`.
The model pipeline snapshot below is historical evidence for the completed
2026-05 long-running pipeline and must not be used as current GCP/Optuna state.

## Current Data Freshness Checkpoint

- updated_at: `2026-06-01 11:44:18 CST`
- current_state: `architecture/data freshness repair`
- K-line truth source: `price_kline_tdxhub` refreshed to trading calendar
  `2026-05-29` with tdxhub incremental sync.
- data audit: `audit_data_completeness.py` now exits PASS with WARN (0 FAIL /
  2 WARN). `price_kline_tdxhub`, `fact_alpha158_panel`,
  `fact_stock_technical_stage`, `fact_signal_context`,
  `fact_technical_trigger`, `fact_capital_flow_pit_daily`,
  `fact_risk_factors`, `fact_sector_momentum_daily`,
  `mart_stock_picture_daily`, `mart_stock_survey_features`,
  `mart_p0a_label_panel`, `mart_p0a_feature_label_panel_v3`,
  `mart_p0a_feature_label_panel_v4`, `mart_sniper_score_daily`, and
  `mart_institution_score_daily` now reach `2026-05-29`. The remaining WARN
  evidence is `fact_lhb_event` event coverage only 84 codes (1%);
  read-only checks show `raw_lhb_daily` and `fact_lhb_event` both max at
  `2026-05-29`, with latest day raw 94 rows / 84 codes and fact 84 rows /
  84 codes, so this is source sparsity rather than ETL lag. The other WARN is
  `fact_technical_trigger` event-table sparse-event coverage, and `need_027`
  main-force source still blocked/unknown; the akshare `individual_fund_flow`
  / `individual_fund_flow_rank` capability is registered, the live probe is
  still blocked by `ProxyError`, and blocked probe rows now persist in
  `mart_data_source_failure_queue` for follow-up triage.
- stage-opt audit: 2026-06-01 repaired the 2025-08-01→2026-05-29
  `fact_stock_technical_stage` / `fact_signal_context` discontinuity and
  reran `audit_stage_opt_candidate_supply.py`; full-history coverage is now
  `4929 raw_signal_rows / 1033 unique_keys / 398 ready_keys / 38.53% ready
  coverage / 635 below_min_signals`, while `2024-03-06` 起的
  `dropped_unknown_stage_rows` 降到 `454,158`. The remaining
  `technical_stage='?'` mass is now mostly structural classifier warmup /
  unknown, not a fresh ETL outage.
- end-to-end audit: `audit_end_to_end.py` now exits PASS with WARN
  (`24 total / 19 OK / 5 WARN / 0 FAIL`); WARN includes recommendation PIT
  coverage 0, recommendation row count, and freshness days_behind=3 for
  signal/context/picture/survey marts. `rank_and_size()` is already
  PIT-tier-first, but the current `2026-05-29` PIT exact candidates mostly
  fail `hp/n_signals/Wilson`, so final recommendations still come out as
  cross-stage fallback. Targeted PIT backfill only moved latest cutoff from 3
  rows to 4, and a 2-stock `optimize_per_stock_stage_strategy.py --min-signals 3`
  smoke still produced 0 governance-pass rows. This slice also upgraded
  `mart_daily_position_recommendation_pit_diagnostic` with
  `governance_reject_count` / latest reason / latest rejected_at and reran
  `build_daily_position_recommendations.py --date 2026-05-29`, so the latest
  diagnostic rows now show the governance reason beside each
  `stock_missing_pit` / `formula_missing_pit` row.
- survivorship gate: current default `p0a_v3_horizon_governance` PASS; the old
  `p0a_v2_governance_v1` gate remains available only for explicit historical
  review.
- next_step: follow `goal.md` 6.11 from the current state; the next true
  blocker is LHB event coverage, recommendation PIT candidate sparsity, and
  the `need_027` source probe. `fact_technical_trigger` remains WARN
  evidence, not a completeness blocker, and PIT-first ranking is already in
  place even though current output is still all cross-stage fallback. The PIT
  table is still underfilled for the current exact candidates, so the next
  meaningful step is upstream PIT coverage expansion rather than more ranking
  tweaks. Keep the `need_027` source probe / unknown status explicit; the
  `akshare.stock_individual_fund_flow` / `stock_individual_fund_flow_rank`
  capability is registered, the live probe is still blocked by `ProxyError`,
  blocked probe rows now persist in `mart_data_source_failure_queue`, and
  `audit_tdx_data_need_coverage.py` now emits a blocked need summary with
  label-vs-family registration so the current inventory stays explicit.
- additional PIT evidence: 2026-06-01 reran `build_stage_opt_pit.py` on the 7
  current recommendation stock codes across cutoffs `2026-01-01,2026-05-19,
  2026-05-29`; latest recommendation PIT coverage remained 0 (8 total / 0 exact
  / 0 same_formula / 1 same_stock / 8 cross_stage), confirming structural
  candidate sparsity rather than a one-shot coverage gap. The
  `portfolio_sizer` short/mid/long thresholds now live in
  `backend/config/portfolio_sizer_profiles.yaml`, and
  `backend/scripts/audit_portfolio_sizer_profile_attrition.py` is now the
  evidence-gate for any tuning; the current profile filters still eliminate
  exact PIT candidates on `hp/n_signals/Wilson`, so coverage remains a
  gating concern rather than a ranking bug. A direct attrition audit on 353
  raw candidates found selected_rows only 5/1/2 for short/mid/long, all
  `cross_stage_fallback`, with `hp` and `wilson` as the dominant fail reasons.
  The new `fail_reasons_by_match_tier` breakdown makes the attrition path
  explicit: `stage_pit` mostly fails on `hp/n_signals/Wilson`,
  `stage_pit_formula_fallback` mostly fails on `hp/n_signals`, and
  `cross_stage_fallback` mostly fails on `hp/wilson`; the new
  `fail_holding_days_by_match_tier` shows those exact PIT `hp` failures
  cluster on off-anchor holding_days 20/30/60/90, which is the most useful
  hint for the next tuning decision. 2026-06-01 sensitivity auditing
  (`base`, `hold+20`, `min_n_signals-2`, `min_wilson_win-0.05`) did not change
  selected_rows, so the next useful tuning decision is upstream candidate
  supply / formula coverage, not profile micro-adjustment. The new need
  coverage audit also surfaces source registration facts: `need_027`'s
  preferred `akshare` is
  registered, while the declared fallback label `miaoxiang` resolves to the
  registered `aif10` family but that adapter still lacks
  `individual_fund_flow`, so the fallback is still conceptual in the current
  wiring. 2026-06-01 also ran `audit_stage_opt_candidate_supply.py` on the
  current audited slice (2023-01-01→2026-05-29, limit-stocks 50) and found
  3576 raw signal rows / 831 unique keys / 280 ready keys / 33.69% ready
  coverage / 551 below_min_signals; the helper now reuses the current
  connection's calendar truth source instead of opening a nested
  `latest_closed_or_raise()` connection. This reinforces the same conclusion:
  upstream candidate supply / formula coverage is the next lever, not another
  profile knob tweak. LHB side, the latest read-only check shows
  `raw_lhb_daily` and `fact_lhb_event` both max at `2026-05-29`; latest day
  raw 94 rows / 84 codes and fact 84 rows / 84 codes, so the remaining LHB
  WARN is source sparsity, not ETL lag. `audit_pit_coverage.py` is still 4/4
  PASS, with `fact_lhb_event` gain_20d coverage 83.9% > 60%, so the sparse
  LHB WARN is completeness-only, not PIT safety.

- generated_at: `2026-05-25T01:20:01Z`
- model_id: `lgbm_phase5_gcp_20260520T010718`
- current_step: `all_done`
- next_step: `all_done`
- resume_command: `echo all_done`

## Steps

| Step | Name | Status | Evidence Found |
|---:|---|---|---|
| 1 | verify local prediction artifacts | done | json:data/reports/phase5_chain/status.json step=gcp_disabled<br>db:mart_p0b_lambdamart_v6_predictions model_id rows=3396073 |
| 2 | pre-sim audit | done | json:data/reports/pit_audit_lgbm_phase5_gcp_20260520T010718.json (model_id mismatch)<br>json:data/reports/pit_audit.json fresh PASS |
| 3 | paper_sim execution | done | db:mart_paper_sim_lambdamart_v6_kpi_compare model_id rows=1<br>db:mart_paper_sim_nav sim_run_id contains model_id rows=614 |
| 4 | KPI ingestion | done | db:mart_paper_sim_kpi joined to model compare rows=1 |
| 5 | KPI comparison | done | db:mart_paper_sim_lambdamart_v6_kpi_compare model_id rows=1 |
| 6 | Pareto verdict gatekeeper | done | json:data/reports/phase4_gate_lgbm_phase5_gcp_20260520T010718.json<br>json:data/reports/phase4_gate_result.json matching model (model_id mismatch) |
| 7 | decision promote/reject/retrain | done | json:data/reports/decision_lgbm_phase5_gcp_20260520T010718.json |

## Blockers

- none

## Expected Evidence

### Step 1: verify local prediction artifacts
- file:data/smartmoney_post_lgbm_phase5_gcp_20260520T010718.duckdb.bak
- file:data/smartmoney_post_lgbm_phase5_gcp_20260520T010718.duckdb
- json:data/reports/phase5_chain/status.json step=pull_done
- file:data/reports/phase5_chain/monitor_done_lgbm_phase5_gcp_20260520T010718.sentinel (weak)
- db:mart_p0b_oos_predictions model_id
- db:mart_p0b_lambdamart_v6_predictions model_id

### Step 2: pre-sim audit
- json:analysis/pre_sim_audit_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pre_sim_audit_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pit_audit_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pit_audit.json fresh PASS
- db:mart_champion_candidate_evaluation PIT pass

### Step 3: paper_sim execution
- json:data/reports/msaf_ensemble_phase5_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/paper_sim_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/paper_sim_lgbm_phase5_gcp_20260520T010718.json
- db:mart_paper_sim_lambdamart_v6_kpi_compare model_id
- db:mart_paper_sim_nav sim_run_id contains model_id

### Step 4: KPI ingestion
- json:data/reports/msaf_ensemble_phase5_lgbm_phase5_gcp_20260520T010718.json kpi
- json:data/reports/kpi_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/kpi_lgbm_phase5_gcp_20260520T010718.json
- db:mart_paper_sim_kpi joined to model compare

### Step 5: KPI comparison
- json:data/reports/kpi_compare_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/kpi_compare_lgbm_phase5_gcp_20260520T010718.json
- db:mart_paper_sim_lambdamart_v6_kpi_compare model_id

### Step 6: Pareto verdict gatekeeper
- json:data/reports/phase4_gate_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/pareto_verdict_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pareto_verdict_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/phase4_gate_result.json matching model
- db:mart_tdx_keep_promotion_gate challenger_model_id

### Step 7: decision promote/reject/retrain
- json:analysis/decision_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/decision_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/promote_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/ensemble_decision_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/retrain_decision_lgbm_phase5_gcp_20260520T010718.json
- db:mart_champion_model model_id
- db:mart_champion_candidate_evaluation final status
- db:mart_tdx_keep_promotion_gate final decision
