# Strategy Validation And Promotion Contract

This is the active contract for strategy research, backtests, paper simulation,
forward monitoring, and promotion. Historical strategy drafts and evidence are
archived under `analysis/docs_archive_20260531/`.

## Risk First

| Risk | Rule |
|---|---|
| In-sample or proxy result promoted as production | Blocked; use `unknown` until current evidence exists |
| PIT leakage | Any time `t` decision may only use data available at or before `t` |
| Unrealistic execution | Include costs, slippage, T+1, limit-up buyability, one-line boards, capacity, and overlap |
| Search without search space | `plan_validator` must pass before Optuna/provider jobs |
| Suspiciously good numbers | Sharpe >5, win rate >95%, or annualized >100% triggers leakage/PIT ablation |

## Required Gates

| Gate | Required checks |
|---|---|
| Backtest preflight | `universe_clean`, `limit_pct_per_board`, `cost_model`, `data_freshness`, `walk_forward`, `signal_pit_spotcheck`, `code_leakage_scan`, `excluded_stocks` |
| Plan validator | `search_space`, `trial_value`, `formula_runnable`, `cost_efficiency`, `param_scope`, `sample_size_coverage`, `board_coverage`, `output_usable` |
| Data audit | Run after data sync; stale critical data blocks production evidence |
| Paper sim | Must use current universe, PIT features, costs, constraints, and explicit excluded stocks |
| Forward monitor | Promotion requires current, non-proxy forward or accepted paper evidence |

## Optuna Governance (durable rules, owner: this file)

All Optuna work goes through `services.optimization`; never call `study.optimize`
bare. Thresholds/ranges/weights/table names live in `backend/config/optuna_config.yaml`.

Three mandatory gates:

| Gate | Rule |
|---|---|
| 时序切分 | `walk_forward.split_dispatch(signals)` (default R1 = `expanding_monthly`); Optuna only sees early-window train |
| 预校验 | `governance.enforce_pre_optimize(n_trials, has_seed=True)` — 50 <= n_trials <= 500, fixed seed |
| OOS 验证 | best params rerun on test -> `governance.enforce_pre_insert(record)`; rejects `walk_forward_mode='none'`, missing OOS fields, sharpe>5, win>0.95 |

R1 `expanding_monthly` standard: cut at month end; first `min_train_months` (default 6)
months are train base; best params from earliest window run on each later OOS month;
multi-window trades aggregate via `oos_aggregator.aggregate_oos_metrics`; the stored
sharpe is multi-window OOS truth, never in-sample fit.

Business-table contract: every `mart_per_stock_*_optimal` table must carry OOS columns
(`oos_sharpe/oos_win_rate/oos_avg_ret/oos_n_traded/oos_period_*/walk_forward_mode/`
`train_n_signals/test_n_signals`); selectors/scoring read only `oos_*`; legacy columns
(`sharpe/win_rate/avg_ret`) are descriptive. New optimization tables copy this contract.

No-future-function defense in depth: (1) data split via `split_expanding_monthly`;
(2) search space contains strategy behavior params (hp/stop/target/trailing/pattern
thresholds), never data lookups; (3) insert gate as above. Every reject is logged to
`fact_optuna_governance_log` (PK=`run_id`, full `record_json` + reason).

## Mainline After Governance

Framework governance comes first. After architecture/docs/test/data/tooling gates
pass, recover the business mainline in this order:

| Order | Work | Rule |
|---:|---|---|
| 0 | 主升浪猎手 serious research and validation | Reproduce the research log, verify data/code boundaries, then run PIT/cost/walk-forward/paper_sim/forward checks |
| 1 | BestChoice artifact freeze and challenger import | Follow namespaced challenger plan; do not merge directly into champion logic |
| 2 | 300616 original formula replay | Use 300616 as sentinel: god-view diagnosis first, then PIT-safe rewrite |
| 3 | 300616 derived formula/search space | `plan_validator` must prove non-empty search space |
| 4 | Main-project paper_sim | Cost-aware, limit-aware, T+1-aware, with overlap and capacity constraints |
| 5 | Candidate and holding monitor | Unknown/proxy/stale fields remain explicit |
| 6 | Profiles/API/frontend | Only after backend evidence and lineage are stable |

## 主升浪猎手 Validation Boundary

`docs/zhushenglang_hunter_research_log_20260528.md` is preserved as the product
north star and research evidence, not as a production certificate. Its 70%,
78%, and 86% figures are hypotheses until revalidated under current gates.

Minimum validation before using it for real candidates:

| Area | Requirement |
|---|---|
| Data | Rebuild or locate ground-truth files and confirm hashes/date windows |
| PIT | Disclosure dates, K-line windows, adjustment factors, and universe membership checked |
| Execution | Costs, slippage, T+1, limit-up buyability, one-line boards, overlap, capacity |
| Model | Walk-forward with purge/embargo, seed sensitivity, regime stratification |
| Evidence | Paper sim + forward monitor before promotion |

## Archived Sources

This contract supersedes or summarizes:

| Former doc group | Current state |
|---|---|
| `../analysis/docs_archive_20260531/backtester_mcp_integration_20260517.md`, `../analysis/docs_archive_20260531/leakage_pattern_catalog.md` | Gate rules consolidated here |
| `../analysis/docs_archive_20260531/paper_sim_kpi_compare_plan.md`, `../analysis/docs_archive_20260531/paper_sim_overview_20260520.md`, `../analysis/docs_archive_20260531/v7_forward_decision_framework.md` | Paper/forward rules consolidated here; dated evidence archived |
| `../analysis/docs_archive_20260531/phase4_alpha_root_cause_roadmap.md`, `../analysis/docs_archive_20260531/retrain_stall_fix1_patch_draft.md` | Archived as implementation evidence |
| `../analysis/docs_archive_20260531/sue_pit_design_20260517.md` | Archived as feature design evidence |
| `../analysis/docs_archive_20260531/msaf_top_design_20260517.md`, `../analysis/docs_archive_20260531/msaf_p1_institution_baseline_20260518.md`, `../analysis/docs_archive_20260531/msaf_p1b_institution_composite_20260518.md`, `../analysis/docs_archive_20260531/msaf_p4_vol_sizing_research_20260518.md`, `../analysis/docs_archive_20260531/only_stock_scheme_design_20260517.md` | Archived as historical strategy research, not current direction |
