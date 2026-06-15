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

## 判断法典 (Judgment Codex) — owner: this file

立法层 (2026-06-15 8-lens 对抗复审根因 R1/R2 + 用户"考核收益率不止胜率, 目的是真能赚钱"反哺)。
每条人话+机器话双语; 机器话由 `check_strategy_validation_integrity.py` gate + moth `validation-*` 断言执法。
完整缺陷体系 owner=`analysis/design_deficiencies_extension2_20260615.md` (N1-N30 + 根因链)。

| 法典条 | 人话 (任何人能懂) | 机器话 (gate/assert 执行) |
|---|---|---|
| **C-R1 验证空间≠盈利空间** | 每日截面 rank-IC 数学上减掉了 cohort 绝对漂移, 而 long-only 赚的恰是它 → 验证空间(rank) ⟂ 盈利空间(含成本绝对NAV)。任何 edge 的**充分**证据 = 含成本绝对收益, IC 仅 necessary 快筛。IC 真 ≠ 能赚钱 (Phase B: 33σ REAL_EDGE 仍 gross -34.6%)。 | `tradability_verdict(ic, net)` 对称门: IC>0 且 net≤0 → `IC_POSITIVE_BUT_UNTRADABLE`; `record_verdict` 拒 `confirmed_by_owner=1` 无含成本 net_return 证据; 验证阶梯 Gate 须含**绝对收益 null** (block bootstrap NAV 符号, 非纯 rank/sharpe 置换)。moth `validation-r1-symmetric-gate` / `validation-promotion-needs-money`。 |
| **C-R2 信号≠可交易头寸** | 信号是排序(数学对象), 头寸是受涨跌停/T+1/停牌/印花税/流动性约束的物理对象。回测把二者等同 = 假设无摩擦市场, 绝对收益系统性乐观。 | 回测引擎须 execution-aware: 涨跌停一字板剔篮 + 非对称成本栈(卖+印花) + 容量/冲击 + T+1 open 入场(非 close 假成交)。`check_strategy_validation_integrity.engine_execution_aware`=PASS; moth `validation-engine-execution-aware`。 |
| **C-WinReturn 胜率诊断/收益目标** | 单笔期望=胜率×平均盈−败率×平均亏; 胜率脱离盈亏比无意义 (40%×3:1 完胜 60%×0.5:1)。**胜率=诊断量, 收益率+max_dd=目标量**。仓位管理是把 {edge,胜率,盈亏分布} 转成 {实现收益,回撤} 的传递函数, 是一等设计轴非事后系数。最终目的是真能赚钱, 不是证明策略有效。 | `kpi_verdict(metrics)` 联合门: 年化 AND max_dd AND 月胜率 AND 胜率×盈亏比期望(`positive_expectancy`), 全 AND, 单项不放行; 引擎须报 payoff_ratio/avg_win/avg_loss。moth `validation-winreturn-codex`。 |

死亡条款 (wired, 非文本): **感知死** = confirmed cell forward 不兑现自动冻结 (forward_reconciliation job, 读 `fact_experiment_verdict`); **自欺死** = 任何 edge 无含成本绝对收益证据即 BLOCK (C-R1 转正 guard)。

验证范式 (R1 修正): IC = necessary 快筛 (降级); 含成本 backtest 绝对收益 = sufficient gate (升级)。早期插廉价绝对收益门 (Tier-1.5 可交易性筛: 半衰期→换手预算→成本可活性→容量), 选 cell/因子一律按含成本 OOS 绝对收益, 不按 IC。

## Required Gates

| Gate | Required checks |
|---|---|
| Strategy validation integrity | `anomaly_symmetric`(C-R1), `promotion_needs_money`(C-R1), `kpi_joint_codex`(C-WinReturn), `engine_execution_aware`(C-R2) — `check_strategy_validation_integrity.py` |
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

`../analysis/zhushenglang_hunter_research_log_20260528.md` (moved docs/->analysis/
2026-06-15) is preserved as the product north star and research evidence, not as
a production certificate. Its 70%,
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
