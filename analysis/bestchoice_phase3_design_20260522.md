# BestChoice Phase 3 Design — 2026-05-22

## Why design doc first (not implement)

2026-05-22 11:30 attempt at quick-and-dirty Phase 3 paper_sim gave:
- ann_ret +285.79%, max_dd -100%, sharpe 16.78
- CLAUDE.md §4.2 leakage alarm: ann > 100% red-line
- Root cause: no portfolio constraint (each candidate = independent trade), 1238 trades/year (vs realistic ~50-100/yr for top-5)
- Confirmed `回测异常高 = leakage 警报, 不该兴奋`

**Don't ship first-cut**. Phase 3 needs proper portfolio paper_sim engine.

## Required components (per plan §5 Phase 3)

| Need | Status |
|---|---|
| T+1 buy at open | feed has `buy_date`, kline has open price |
| Portfolio top-K constraint (5/10/20 concurrent) | **need engine** to manage concurrent positions |
| Same-stock dedup (no double-buy same stock) | engine handles |
| Cost model (tx 2bps/side + slip 5bps + 涨跌停/停牌) | main project paper_sim already has; need to reuse |
| Sell rules: fixed_N + formula_exit_or_N | fixed_N trivial; formula_exit_or_N needs formula_engine exit check |
| KPI: NAV curve / Sharpe / Calmar / max_dd / 月胜率 / excess vs HS300 / turnover / overlap with champion picks | main paper_sim has; reuse |
| Output to `mart_paper_sim_kpi` + `mart_strategy_result_registry` | per plan §5 Phase 3 exit criteria |

## Two viable implementations

### Approach 1: Adapt main project paper_sim to BestChoice feed

Main project paper_sim entry: `backend/scripts/run_paper_sim_lambdamart_v6_compare.py` consumes `mart_p0b_lambdamart_v6_predictions` (model_id + signal_date + stock_code + score).

**Adapter path**:
1. Create `mart_bestchoice_predictions_v1` from `mart_daily_formula_candidate_bestchoice_v1`:
   - `model_id = bestchoice_formula_challenger_v1`
   - `signal_date / stock_code / score = confidence_score`
   - Add `train_*/test_*/walk_forward_mode` dummy fields to satisfy schema
2. Run `run_paper_sim_lambdamart_v6_compare.py --lambdamart-model-id bestchoice_formula_challenger_v1`
3. paper_sim 引擎自动 top-K + 持仓约束 + 成本

**Risk**: BestChoice sell_rule (fixed_N / formula_exit_or_N) 跟 main project paper_sim sell logic (trailing/HARD STOP/ML_RANK) mismatch. 可能需要 force `--max-holding-days` 或 override sell rule.

### Approach 2: Write dedicated BestChoice paper_sim engine

Reuse main project utility libs (cost model, kline loader), but write top-K + sell_rule + portfolio logic from scratch with BestChoice semantics.

**Risk**: 2-4h work, 重复 main project paper_sim 大部分逻辑. Not DRY.

## Recommended: Approach 1 (adapter)

主项目 paper_sim engine 已 battle-tested (5/22 跑 stability model 432 days, Sharpe 2.09 sane). BestChoice 是 candidate selector, 不是 portfolio simulator. 不要重写已 working code.

## Implementation steps

1. **写 adapter script** `backend/scripts/import_bestchoice_phase3_predictions.py`:
   - 读 `mart_daily_formula_candidate_bestchoice_v1` (run_id=bestchoice_formula_optuna_20260521_v1)
   - 转写为 `mart_p0b_lambdamart_v6_predictions` schema 兼容 (model_id=bestchoice_formula_challenger_v1)
   - `score = confidence_score` (paper_sim 用 score ranking top-K)
   - dummy train/test cols (paper_sim 不依赖 train_start)

2. **run paper_sim**:
   - `MODEL_ID=bestchoice_formula_challenger_v1 PYTHONPATH=backend python backend/scripts/run_paper_sim_lambdamart_v6_compare.py --lambdamart-model-id bestchoice_formula_challenger_v1`
   - paper_sim 自动 top-K = 5 (default), 跑 432 days

3. **看 KPI**:
   - `mart_paper_sim_lambdamart_v6_kpi_compare` 新增 row (challenger=bestchoice_formula_challenger_v1)
   - Sharpe / ann / max_dd / monthly win_rate / excess vs HS300

4. **比对 stability model**:
   - 当前 stability model: Sharpe 2.09 / ann +71.92% / dd -16.84% / 胜率 70%
   - BestChoice: ??? (expect 不一定 beat stability; check 互补 alpha 路径)

5. **走 plan §5 Phase 4**: Complementarity check (overlap, correlation, drawdown timing 等) — 见 plan §5 Phase 4 详细 metrics.

## Tradeoffs

- **Approach 1 缺陷**: BestChoice sell_rule (fixed_N hold) ≠ main paper_sim sell (trailing exit / score floor). 若用 score floor, 短期 candidate 可能立刻 sell. **解决**: paper_sim 加 `--min-holding-days` override = BestChoice holding_days max (60 days), 让 sell_rule 主导 sell timing.

- **风险**: BestChoice candidate score (37-95) 跟 LambdaMART score (-6.27 to 5.52) 量纲不同. paper_sim top-K ranking 用 raw score, BestChoice score 已 normalized 0-100, 不影响 top-K ranking.

## 时机

- 当前 plan C retrain 跑中 (pid 1588), Phase 3 不依赖 retrain 进度, 可并行启
- 但 plan C 出 new best 时, **可能更新 stability model 跑 final fit**, 这时 main paper_sim 应跑 stability vs BestChoice 双 challenger. Phase 3 等 plan C verdict 再启更省事.

**推荐**: plan C 完成 (~30-60 min wait) → 同时 commit 此 design doc → plan C 出新 best 后启 Phase 3 (Approach 1 adapter + paper_sim).

## Backlog cleanup

- 删 `backend/scripts/run_paper_sim_bestchoice_phase3.py` (broken first-cut, has been deleted)
- 不 commit broken first-cut script (避免 stale code)
