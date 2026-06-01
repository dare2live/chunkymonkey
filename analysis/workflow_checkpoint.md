# Workflow Checkpoint

Business-level pipeline tracker. Session-level state remains in SESSION_HANDOFF.md.

Current Codex architecture/worktree-governance state is tracked in `goal.md`.
The model pipeline snapshot below is historical evidence for the completed
2026-05 long-running pipeline and must not be used as current GCP/Optuna state.

## Current Data Freshness Checkpoint

- updated_at: `2026-06-01 08:01:03 CST`
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
  evidence is `fact_lhb_event` event coverage only 84 codes (1%),
  `fact_technical_trigger` event-table sparse-event coverage, and
  `need_027` main-force source still blocked/unknown; the akshare
  `individual_fund_flow` / `individual_fund_flow_rank` capability is
  registered, but the live probe is still blocked by `ProxyError`.
- end-to-end audit: `audit_end_to_end.py` now exits PASS with WARN
  (`24 total / 19 OK / 5 WARN / 0 FAIL`); WARN includes recommendation PIT
  coverage 0, recommendation row count, and freshness days_behind=3 for
  signal/context/picture/survey marts. `rank_and_size()` is already
  PIT-tier-first, but the current `2026-05-29` PIT exact candidates mostly
  fail `hp/n_signals/Wilson`, so final recommendations still come out as
  cross-stage fallback. Targeted PIT backfill only moved latest cutoff from 3
  rows to 4, and a 2-stock `optimize_per_stock_stage_strategy.py --min-signals 3`
  smoke still produced 0 governance-pass rows.
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
  capability is registered, but the live probe is still blocked by `ProxyError`.

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
