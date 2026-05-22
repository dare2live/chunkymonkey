# Bestchoice × ChunkyMonkey Validation Plan

Date: 2026-05-21

## 1. Executive Decision

当前不建议直接把 `bestchoice` 和主项目 `chunkymonkey` 做代码级合并，也不建议先在 `bestchoice` 内单独跑 GCP。

推荐路径是：

1. 先把 `bestchoice` 的公式寻优结果接入主项目的数据、PIT、paper_sim、结果注册体系。
2. 用主项目的组合级验证框架评估 `bestchoice` 是否被增强。
3. 再反向评估 `bestchoice` 信号是否补强主项目 champion/challenger。
4. 最后根据验证结果决定合并、模块化接入，还是保持独立研究项目。

核心判断：`bestchoice` 是公式/信号研究引擎，`chunkymonkey` 是生产级量化验证与组合管理平台。两者方向一致，但处在不同层级。

## 2. Current Evidence

### 2.1 Bestchoice 已具备的资产

| Asset | Value |
|---|---:|
| Stocks covered | 5201 |
| Formula count | 5 |
| Research cache rows | 45908 |
| Dry-run replacement candidates | 1146 |
| Latest data date | 2026-05-19 |
| Production CSV overwritten | No |

`bestchoice` 已完成全市场本地 Optuna dry-run，形成 `stock_code × formula_id × params × sell_rule` 级别的候选参数库。

### 2.2 ChunkyMonkey 已具备的资产

| Asset | Value |
|---|---:|
| `market.duckdb::v_price_kline_qfq` | 5214615 rows / 5205 codes / 2022-01-01 to 2026-05-19 |
| `smartmoney.duckdb` | 335 tables |
| `fact_feature_panel` | 4099596 rows / 2023-01-03 to 2026-05-19 |
| `fact_technical_trigger` | 1363707 rows |
| `fact_signal_context` | 2084084 rows |
| `fact_paper_sim_trade` | 13352 rows |
| `mart_paper_sim_kpi` | 43 runs |
| Phase 5 GCP prediction import | 3396073 rows |

交叉检查结果：`bestchoice` 的 5201 只股票全部能在主项目行情库中找到。主项目 active universe 额外多出 311 个代码，后续可作为覆盖差异审计项。

## 3. Why Main Project Validation Is Required

`bestchoice` 当前指标主要是股票/公式级别：

- 平均收益
- 平均回撤
- 胜率
- 信号数
- 持仓周期
- 参数相对 baseline 的提升

这些指标能说明某个公式在某只股票上历史表现更好，但不能直接说明实盘组合有效。生产判断必须补齐：

- T+1 入场
- 涨跌停和停牌约束
- 滑点和交易成本
- 每日 top-K 组合选择
- 单票去重
- 最大持仓数
- 换手率
- 组合净值曲线
- 最大回撤
- Sharpe / Calmar
- 月胜率
- 超额收益
- 与现有 champion 的重合度和互补性

这些能力主项目已有，应该复用。

## 4. Target Architecture

### 4.1 Short-term Architecture

```
bestchoice
  formula_local_optuna_batch_adoption.csv
  formula_local_optuna_batch_stock_best_replacements.csv
  research_cache.duckdb
        |
        | import / normalize / lineage
        v
chunkymonkey
  mart_stock_formula_optuna_bestchoice_v1
  mart_daily_formula_candidate_bestchoice_v1
        |
        | paper_sim / PIT / T+1 / costs / limit rules
        v
  mart_paper_sim_kpi
  mart_strategy_result_registry
```

### 4.2 Long-term Architecture

If the POC passes, `bestchoice` formulas should become a formula alpha family inside `chunkymonkey`, not a separate production surface.

Possible production shape:

- `bestchoice_formula_alpha_v1`: formula candidate score.
- `bestchoice_formula_context_v1`: formula + stock + context features.
- `bestchoice_formula_challenger_v1`: paper_sim challenger.
- `bestchoice_formula_ensemble_component_v1`: if it complements current champion.

## 5. Phase Plan

### Phase 0: Freeze And Lineage

Goal: make the current `bestchoice` output reproducible and importable.

Tasks:

- Record source artifacts and hashes:
  - `analysis/research_cache.duckdb`
  - `analysis/formula_local_optuna_batch_adoption.csv`
  - `analysis/formula_local_optuna_batch_stock_best_replacements.csv`
  - `analysis/formula_local_optuna_aggregate_audit.md`
  - `analysis/operational_delivery_readiness.md`
- Assign run id: `bestchoice_formula_optuna_20260521_v1`.
- Do not overwrite `analysis/stock_formula_best.csv`.
- Do not promote any candidate to production.

Exit criteria:

- Source row counts match current audit.
- 1146 replacements are reproducible from source artifacts.
- Schema mapping to main project is documented.

### Phase 1: Import To Main Project As Challenger Data

Goal: load `bestchoice` results into `chunkymonkey` as read-only challenger evidence.

Candidate target table:

`mart_stock_formula_optuna_bestchoice_v1`

Minimum fields:

- `run_id`
- `stock_code`
- `formula_id`
- `variant_id`
- `params_json`
- `params_hash`
- `sell_rule`
- `holding_days`
- `signal_count`
- `win_rate`
- `avg_ret`
- `avg_dd`
- `score`
- `validation_signal_count`
- `validation_win_rate`
- `validation_avg_ret`
- `validation_score`
- `score_delta`
- `validation_score_delta`
- `adoption_decision`
- `merge_decision`
- `source_artifact`
- `source_data_latest_date`
- `created_at`

Exit criteria:

- 1146 replacement candidates imported.
- 5201-stock source coverage can be audited.
- All imported rows carry lineage back to `bestchoice`.
- No current champion/challenger table is overwritten.

### Phase 2: Build Daily Candidate Simulation Feed

Goal: convert static per-stock optimized formula evidence into daily tradable candidates.

For each trading day:

1. Detect whether a `bestchoice` formula triggers.
2. Join the stock/formula to the optimized params and sell rule.
3. Rank same-day candidates by confidence score.
4. Emit a paper-sim candidate feed.

Candidate target table:

`mart_daily_formula_candidate_bestchoice_v1`

Required fields:

- `signal_date`
- `buy_date`
- `stock_code`
- `formula_id`
- `sell_rule`
- `holding_days`
- `confidence_score`
- `expected_return`
- `expected_drawdown`
- `historical_win_rate`
- `validation_win_rate`
- `rank_in_date`
- `run_id`

Exit criteria:

- Candidate feed covers historical dates required by paper_sim.
- Same stock duplicate signals are deduplicated or explicitly ranked.
- Missing formula triggers and missing kline rows are auditable.

### Phase 3: Main Project Paper Sim

Goal: determine whether `bestchoice` gets materially stronger after applying realistic execution and portfolio rules.

Recommended first simulation:

- Run id: `bestchoice_formula_challenger_v1`
- Start: 2023-01-03 or earliest date with complete candidate feed
- End: 2026-05-19
- Buy: T+1
- Costs: use main project fee/slippage assumptions
- Limit rules: use main project limit-up/limit-down handling
- Universe: main active A-share universe
- Portfolio size: test 5, 10, 20
- Ranking: confidence score, then validation score delta, then expected drawdown
- Holding: candidate `sell_rule` / `holding_days`

KPIs:

- Annual return
- Total return
- Max drawdown
- Sharpe
- Calmar
- Monthly win rate
- Annual turnover
- Average holding days
- Excess vs HS300
- Trade count
- Position concentration
- Overlap with current champion picks

Exit criteria:

- Results are written to `mart_paper_sim_kpi`.
- Result is registered in `mart_strategy_result_registry`.
- Leakage/PIT audit passes or blockers are explicit.

### Phase 4: Measure Whether It Complements Main Project

Goal: determine if `bestchoice` is a replacement candidate, enhancer, or irrelevant.

Run these comparisons:

1. `bestchoice_only`
2. `current_champion_only`
3. `champion_plus_bestchoice_score_boost`
4. `champion_plus_bestchoice_tiebreaker`
5. `champion_plus_bestchoice_filter`

Complementarity metrics:

- Daily top-K overlap.
- Sector overlap.
- Return correlation.
- Drawdown overlap.
- Incremental alpha during champion weak months.
- Whether `bestchoice` improves max drawdown without destroying return.
- Whether `bestchoice` raises return with acceptable turnover.

Interpretation:

| Result | Decision |
|---|---|
| Bestchoice beats champion standalone | Consider promotion as challenger |
| Bestchoice improves champion ensemble | Integrate as formula alpha component |
| Bestchoice lowers drawdown but lowers return | Use as risk filter or regime-specific component |
| Bestchoice only works in a few regimes | Gate by regime/stage |
| Bestchoice fails after costs/T+1 | Keep as research-only |

### Phase 5: Decide GCP Scope

Only run GCP after Phase 3 or Phase 4 shows portfolio-level promise.

GCP should answer targeted questions:

- More trials: 24 -> 100/200.
- Walk-forward stability by formula.
- Formula-specific search spaces.
- Regime/stage-specific params.
- Candidate compression: maximize stability, not just return.

Recommended trigger:

| Condition | GCP Decision |
|---|---|
| Paper sim annual return > 50% and max_dd better than -25% | Run expanded trials |
| Sharpe > 1.3 and low overlap with champion | Run complementarity grid |
| Standalone weak but improves champion | Run ensemble-weight grid |
| Only single-stock metrics look good | Do not run full GCP |
| Candidate count too sparse or concentrated | Run diagnostics first |

Use `chunkymonkey/gcp`, not a new GCP framework inside `bestchoice`.

### Phase 6: Merge Decision

Make the merge decision after paper_sim and complementarity evidence, not before.

| Evidence | Recommended Structure |
|---|---|
| Strong standalone and strong ensemble | Move formula engine into `chunkymonkey`, archive most of `bestchoice` |
| Strong ensemble only | Keep `bestchoice` formulas as a module inside main project |
| Research useful but production weak | Keep `bestchoice` as independent research sandbox |
| No portfolio value | Preserve artifacts, do not merge |

## 6. Production Guardrails

Do not:

- Overwrite `bestchoice/analysis/stock_formula_best.csv` without explicit approval.
- Promote `bestchoice` candidates directly to main project champion.
- Run GCP before a local paper_sim POC establishes portfolio promise.
- Treat single-stock average return as portfolio annual return.
- Use any result without lineage to source artifacts and data date.

Must:

- Preserve source artifact hashes.
- Keep imported data challenger-only.
- Register paper_sim results.
- Record failed experiments as first-class results.
- Compare against current champion and HS300.
- Include trading costs and T+1 execution.

## 7. First POC Proposal

Scope:

- Import only the 1146 `merge_decision=replace` candidates.
- Build a simple daily candidate feed.
- Run three portfolios:
  - top 5
  - top 10
  - top 20
- Rank by:
  1. `validation_score_delta`
  2. `score_delta`
  3. less negative `avg_dd`

Success threshold for continuing:

- Sharpe >= 1.3, or
- annual return >= 50% with max_dd no worse than -25%, or
- materially improves champion ensemble drawdown/return profile.

If this fails, do not spend GCP on expanded trials yet. Diagnose whether failure is caused by execution friction, signal crowding, poor timing, insufficient candidates, or formula overfit.

## 8. Open Questions

1. Should `bestchoice` formulas be replayed directly from formula code inside main project, or should the first POC consume exported signal/candidate artifacts?
2. Should the first paper_sim use fixed top-K or current champion portfolio constraints?
3. Should `ma_base_breakout` be investigated separately, since it produced zero accepted candidates in this run?
4. Should formula candidates be used as buy signals, ranking features, or filters?
5. Should final integration target `mart_stock_formula_optuna` or a namespaced `mart_stock_formula_optuna_bestchoice_v1` first?

## 9. Recommended Next Action

Build the POC importer in the main project:

1. Read `bestchoice/analysis/formula_local_optuna_batch_stock_best_replacements.csv`.
2. Normalize into a namespaced main-project mart table.
3. Create a daily candidate feed.
4. Run one local paper_sim.
5. Register the result as `bestchoice_formula_challenger_v1`.

Only after that result is available should we decide whether to run GCP or merge code.
