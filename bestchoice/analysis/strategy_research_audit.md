# Strategy Research Audit

- date: `2026-05-19`
- scope: strategy research summary, parameter-search API, formula backtest audit visibility

## Issue

`goal.md` defines `策略研究` as the place to inspect formula reliability, parameter search, and backtest audit data. The project already generated CSV artifacts, but the UI-facing `/api/parameter-search` only returned top parameter variants. It did not expose cache readiness, formula coverage, average untradable rate, or execution audit totals.

## Changes

- Extended `/api/parameter-search` to merge:
  - `analysis/formula_variant_metrics.csv`
  - `analysis/formula_parameter_search_summary.csv`
  - `analysis/execution_model_audit.csv`
- Each formula response now includes:
  - cache readiness;
  - stock count;
  - stocks with historical signals;
  - average signal count;
  - average win rate;
  - average return;
  - average drawdown;
  - average Calmar;
  - average untradable rate;
  - execution audit totals;
  - top parameter variants with buy/sell delay rates.
- The existing parameter-search panel now renders as `策略研究 · 参数寻优与回测审计`.
- Formula cards show:
  - cache status;
  - coverage;
  - average untradable rate;
  - average win rate and return;
  - aggregate completion rate;
  - top parameter variants.

## API Verification

```text
/api/parameter-search 200
ready True formulas 5 metrics 114
activity_breakout ready True coverage 5131 / 5201 untradable 0.025192 exec_total 415760 top 5
gs_pullback_confirm ready True coverage 3801 / 5201 untradable 0.012736 exec_total 32193 top 5
gs_raw_buy ready True coverage 5131 / 5201 untradable 0.016698 exec_total 239112 top 5
ma_base_breakout ready True coverage 905 / 5201 untradable 0.055801 exec_total 1260 top 5
volume_base_breakout ready True coverage 5131 / 5201 untradable 0.015992 exec_total 181610 top 5
```

## Regression Checks

```text
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
node --check /tmp/bestchoice_index_scripts.js
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
```

HTTP checks:

```text
/ 200
/api/status 200
/api/unified 200
/api/parameter-search 200
```

## Residual Risk

- The strategy research panel is still a compact dashboard, not a full separate tab with sortable detailed tables.
- Per-stock formula parameter comparison is still shown mainly inside strategy cards and chart views; a dedicated research table can be added later if the compact dashboard is insufficient.

## 2026-05-19 Follow-up: Variant Detail Table

Changes:

- `/api/parameter-search` now returns every parameter-search variant in each formula's `variants` list, not only the top rows.
- The strategy research panel now renders a parameter-variant detail table below the summary cards.
- The table includes:
  - formula name;
  - variant id;
  - holding days;
  - score;
  - trade count;
  - win rate;
  - average return;
  - average drawdown;
  - Calmar;
  - buy delay rate;
  - sell delay rate;
  - parameter summary.

Verification:

```text
/api/parameter-search 200
ready True formulas 5 metrics 114
variant_total 114
first_formula activity_breakout variants 18 top 5
```

Regression checks:

```text
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
node --check /tmp/bestchoice_index_scripts.js
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
```

Updated residual risk:

- The research table is score-sorted but not interactively sortable yet. It exposes all parameter-search rows, so the remaining work is UI convenience rather than missing data.

## 2026-05-19 Follow-up: Interactive Variant Sorting

Changes:

- Added client-side sort state for the parameter-variant detail table.
- The table now supports click-to-sort column headers for:
  - formula;
  - variant;
  - holding days;
  - score;
  - trade count;
  - win rate;
  - average return;
  - average drawdown;
  - Calmar;
  - buy delay rate;
  - sell delay rate.
- Sorting re-renders only the strategy research panel and does not affect the main unified stock list.

Verification:

```text
node --check /tmp/bestchoice_index_scripts.js
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
/api/parameter-search 200
variant_total 114
/ 200
/api/status 200
/api/unified 200
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
```

Updated residual risk:

- Parameter-search rows are now complete and interactively sortable in the UI. Remaining strategy-research work is deeper functionality such as comparing sell rules and per-stock local optimization tables.

## 2026-05-19 Follow-up: Sell Rule Audit

Changes:

- Added `scripts/formula_sell_rule_audit.py`.
- Generated `analysis/formula_sell_rule_audit.csv`.
- Generated `analysis/formula_sell_rule_audit.md`.
- The audit compares fixed holding periods with formula exit signals capped at 60 trading days.
- `/api/parameter-search` now returns `sell_rules` and `best_sell_rule` per formula.
- Strategy research cards now show the best audited sell rule, score, win rate, average return, and trade count.

Full-market audit result:

```text
formula_sell_rule_audit:done rows=35 elapsed=190.6s
```

Best sell rule by formula:

```text
activity_breakout fixed_60 score=44.819891
gs_pullback_confirm fixed_60 score=46.568716
gs_raw_buy fixed_60 score=47.146118
ma_base_breakout fixed_60 score=23.074084
volume_base_breakout fixed_60 score=42.918617
```

API verification:

```text
/api/parameter-search 200
variant_total 114
activity_breakout fixed_60 44.819891 7
gs_pullback_confirm fixed_60 46.568716 7
gs_raw_buy fixed_60 47.146118 7
ma_base_breakout fixed_60 23.074084 7
volume_base_breakout fixed_60 42.918617 7
```

Regression checks:

```text
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
node --check /tmp/bestchoice_index_scripts.js
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
```

Updated residual risk:

- Sell-rule comparison is now available as an audit artifact and UI summary. Production recommendation still uses fixed-holding cache metrics; wiring per-formula/per-stock sell-rule selection into caches remains future work.

## 2026-05-19 Follow-up: Sell Rule Metadata in Unified Signals

Changes:

- Unified cache schema bumped to `10`.
- Unified cache signature now includes:
  - `analysis/stock_formula_best.csv`
  - `analysis/formula_sell_rule_audit.csv`
- `compute.py` now merges the best audited sell rule per formula into each stock/formula optimized payload.
- Strategy signals now expose:
  - `optimized_sell_rule`
  - `optimized_sell_rule_score`
- Strategy detail cards display the optimized sell rule next to the optimized parameter variant.
- `scripts/strategy_rebuild_audit.py` now writes `best_sell_rule` and `best_sell_rule_score` into `analysis/formula_stock_best_params.csv`.

Verification:

```text
python -m py_compile compute.py main.py execution_model.py formula_engine.py scripts/*.py
python scripts/strategy_rebuild_audit.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
node inline script syntax check
/api/parameter-search 200
formula_count 5
metric_count 114
variant_total 114
```

Unified pool verification:

```text
summary {'total': 5201, 'today_recommended': 91, 'buy_window': 2425, 'current_signal': 5190, 'multi_signal': 54, 'multi_family': 54, 'current_multi_family': 4439, 'profiles': 10}
301511 [('GS原始买点', 'fixed_60', 47.146118), ('活跃度大牛突破', 'fixed_60', 44.819891), ('巨量蓄势启动', 'fixed_60', 42.918617)]
301658 [('GS原始买点', 'fixed_60', 47.146118), ('活跃度大牛突破', 'fixed_60', 44.819891), ('巨量蓄势启动', 'fixed_60', 42.918617)]
688700 [('GS回调确认', 'fixed_60', 46.568716), ('GS原始买点', 'fixed_60', 47.146118), ('活跃度大牛突破', 'fixed_60', 44.819891), ('巨量蓄势启动', 'fixed_60', 42.918617)]
002718 [('GS回调确认', 'fixed_60', 46.568716), ('GS原始买点', 'fixed_60', 47.146118), ('活跃度大牛突破', 'fixed_60', 44.819891), ('巨量蓄势启动', 'fixed_60', 42.918617)]
```

Updated residual risk:

- Sell-rule optimization is now visible in unified strategy signals and cards. Production trade generation still needs a follow-up pass to apply the selected sell rule when recomputing per-stock formula trades, instead of only attaching the audited best rule as metadata.

## 2026-05-19 Follow-up: Per-Stock Formula Optimization in Production

Correction:

- Formula production paths no longer use the profile-level fixed `holding_days` when a per-stock optimized row exists.
- `compute_historical()` now loads `analysis/stock_formula_best.csv` and uses `(stock_code, formula_id)` to select:
  - optimized variant params;
  - optimized holding period;
  - optimized strategy metadata.
- `compute_current()` uses the same per-stock optimized params and holding period before evaluating current formula signals.
- `get_chart_data()` also uses the same optimized params and holding period for formula entry/exit markers and holding intervals.
- Missing per-stock optimization is no longer silently replaced by profile defaults:
  - historical rows are marked `missing_optimized_result`;
  - chart payloads return `optimization_missing=True`;
  - the reason is `缺少每股公式参数寻优结果，未使用默认参数回退`.
- Profile cache schema and unified cache schema were bumped to `11` to prevent stale fixed-period caches from being reused.

Sample verification:

```text
formula_volume_base_breakout
301511 opt_hp=20 hist_best_hp=20 status=ok signals=20
301658 opt_hp=30 hist_best_hp=30 status=too_few_signals signals=2
688700 opt_hp=5  hist_best_hp=5  status=too_few_signals signals=4
002718 opt_hp=60 hist_best_hp=60 status=ok signals=43
000001 opt_hp=5  hist_best_hp=5  status=too_few_signals signals=3
missing_count 70
missing_row 001220 缺少每股公式参数寻优结果，未使用默认参数回退 None
```

Unified pool verification:

```text
summary {'total': 5201, 'today_recommended': 37, 'buy_window': 1857, 'current_signal': 5190, 'multi_signal': 35, 'multi_family': 35, 'current_multi_family': 4267, 'profiles': 10}
formula_signal_missing_optimized 0
```

Selected unified signal payloads:

```text
301511 [('GS原始买点', 20, 'fast_x3_cooldown'), ('活跃度大牛突破', 30, 'strict_capped'), ('巨量蓄势启动', 20, 'default_broad')]
301658 [('GS回调确认', 30, 'loose'), ('GS原始买点', 60, 'wide_band_cooldown'), ('活跃度大牛突破', 30, 'classic_capped'), ('巨量蓄势启动', 30, 'long_quiet_box')]
688700 [('GS回调确认', 30, 'default'), ('GS原始买点', 15, 'medium_x3_cooldown'), ('均线筑底突破', 10, 'strict_120_180'), ('活跃度大牛突破', 20, 'classic_capped'), ('巨量蓄势启动', 5, 'case_301511_cooldown')]
002718 [('GS回调确认', 10, 'strict'), ('GS原始买点', 20, 'medium_x3_cooldown'), ('活跃度大牛突破', 60, 'default'), ('巨量蓄势启动', 60, 'default_broad')]
```

Regression checks:

```text
python -m py_compile compute.py main.py execution_model.py formula_engine.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
node inline script syntax check
/ 200
/api/status 200
/api/unified 200
/api/parameter-search 200
/api/chart/301511?strategy=formula_volume_base_breakout 200
```

Updated residual risk:

- Per-stock optimized formula params and holding periods are now used in production. The remaining optimization gap is sell-rule productionization: if the audited best sell rule becomes formula-exit based, production trade generation still needs to apply that selected rule per formula/stock instead of only exposing it as metadata.

## 2026-05-20 Follow-up: Sell Rule Production Path

Correction:

- Per-stock fixed-holding optimization now maps to per-stock sell rules:
  - `best_holding_days=5` -> `fixed_5`
  - `best_holding_days=10` -> `fixed_10`
  - `best_holding_days=15` -> `fixed_15`
  - `best_holding_days=20` -> `fixed_20`
  - `best_holding_days=30` -> `fixed_30`
  - `best_holding_days=60` -> `fixed_60`
- `_load_stock_formula_best()` no longer overlays the full-market `fixed_60` audit result onto every stock.
- `execution_model.build_sell_rule_trades()` now supports:
  - `fixed_N`;
  - `formula_exit_or_N`.
- `compute_historical()`, `compute_current()`, and `get_chart_data()` use the selected per-stock sell rule for formula strategies.
- `formula_stock_best_params.csv` now writes per-stock sell rules and optimized params from `analysis/stock_formula_best.csv`.
- Cache schema bumped to `13`.

Sample verification:

```text
301511 default_broad fixed_20 72.824181 ['fixed_20']
301658 long_quiet_box fixed_30 77.572216 ['fixed_30']
688700 case_301511_cooldown fixed_5 85.680982 ['fixed_5']
002718 default_broad fixed_60 80.719134 ['fixed_60']
```

Unified pool verification:

```text
summary {'total': 5201, 'today_recommended': 37, 'buy_window': 1857, 'current_signal': 5190, 'multi_signal': 35, 'multi_family': 35, 'current_multi_family': 4267, 'profiles': 10}
301511 [('GS原始买点', 20, 'fixed_20', 'fast_x3_cooldown'), ('活跃度大牛突破', 30, 'fixed_30', 'strict_capped'), ('巨量蓄势启动', 20, 'fixed_20', 'default_broad')]
301658 [('GS回调确认', 30, 'fixed_30', 'loose'), ('GS原始买点', 60, 'fixed_60', 'wide_band_cooldown'), ('活跃度大牛突破', 30, 'fixed_30', 'classic_capped'), ('巨量蓄势启动', 30, 'fixed_30', 'long_quiet_box')]
688700 [('GS回调确认', 30, 'fixed_30', 'default'), ('GS原始买点', 15, 'fixed_15', 'medium_x3_cooldown'), ('均线筑底突破', 10, 'fixed_10', 'strict_120_180'), ('活跃度大牛突破', 20, 'fixed_20', 'classic_capped'), ('巨量蓄势启动', 5, 'fixed_5', 'case_301511_cooldown')]
002718 [('GS回调确认', 10, 'fixed_10', 'strict'), ('GS原始买点', 20, 'fixed_20', 'medium_x3_cooldown'), ('活跃度大牛突破', 60, 'fixed_60', 'default'), ('巨量蓄势启动', 60, 'fixed_60', 'default_broad')]
```

Regression checks:

```text
python -m py_compile compute.py execution_model.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
node inline script syntax check
/ 200
/api/status 200
/api/strategies 200
/api/unified 200
/api/parameter-search 200
/api/chart/301511?strategy=formula_volume_base_breakout 200
```

Updated residual risk:

- Production can now execute selected sell rules. The remaining research gap is that `formula_exit_or_N` is only evaluated in the sell-rule audit, not yet included in each stock's parameter-search competition. To make formula-exit a true per-stock optimized rule, `formula_parameter_search.py` must score `formula_exit_or_N` per `(stock_code, formula_id, variant)`.

## 2026-05-20 Follow-up: Per-Stock Sell Rule Search

Changes:

- `scripts/formula_parameter_search.py` now evaluates sell rules during parameter search.
- Each formula parameter variant now compares:
  - `fixed_5`
  - `fixed_10`
  - `fixed_15`
  - `fixed_20`
  - `fixed_30`
  - `fixed_60`
  - `formula_exit_or_5`
  - `formula_exit_or_10`
  - `formula_exit_or_15`
  - `formula_exit_or_20`
  - `formula_exit_or_30`
  - `formula_exit_or_60`
- `analysis/formula_variant_metrics.csv` now includes `sell_rule`.
- `analysis/stock_formula_best.csv` now includes `sell_rule`.
- `/api/parameter-search` includes `sell_rule` in variant rows.
- Full-market parameter search was rerun with:

```text
BESTCHOICE_PARAM_PROGRESS_EVERY=1000 python scripts/formula_parameter_search.py --workers 2 --max-signals-per-stock 120
```

Full-market result:

```text
formula_parameter_search:done variants=228 stock_best=21302 elapsed=957.3s
analysis/formula_variant_metrics.csv rows 228 formula_exit_rows 114
analysis/stock_formula_best.csv rows 21302 formula_exit_rows 5083
```

API verification:

```text
/api/parameter-search 200
metric_count 228
variant_total 228
activity_breakout variants 36 exit 18 top fixed_60 45.801898
gs_pullback_confirm variants 36 exit 18 top fixed_60 47.383426
gs_raw_buy variants 48 exit 24 top fixed_60 48.64126
ma_base_breakout variants 48 exit 24 top fixed_60 51.167143
volume_base_breakout variants 60 exit 30 top fixed_60 45.063558
```

Selected per-stock best rows:

```text
301511 activity_breakout strict_capped formula_exit_or_20 20 66.250880
301511 gs_raw_buy wide_band_cooldown formula_exit_or_20 20 57.485391
301511 volume_base_breakout default_broad fixed_20 20 72.824181
002718 gs_raw_buy medium_x3_cooldown formula_exit_or_30 30 76.513540
```

Unified pool verification:

```text
summary {'total': 5201, 'today_recommended': 37, 'buy_window': 1857, 'current_signal': 5190, 'multi_signal': 35, 'multi_family': 35, 'current_multi_family': 4253, 'profiles': 10}
301511 [('GS原始买点', 20, 'formula_exit_or_20', 'wide_band_cooldown'), ('活跃度大牛突破', 20, 'formula_exit_or_20', 'strict_capped'), ('巨量蓄势启动', 20, 'fixed_20', 'default_broad')]
002718 [('GS回调确认', 10, 'fixed_10', 'strict'), ('GS原始买点', 30, 'formula_exit_or_30', 'medium_x3_cooldown'), ('活跃度大牛突破', 60, 'fixed_60', 'default'), ('巨量蓄势启动', 60, 'fixed_60', 'default_broad')]
```

Chart verification:

```text
301511 formula_activity_breakout strict_capped formula_exit_or_20 ['formula_exit_or_20']
301511 formula_gs_raw_buy wide_band_cooldown formula_exit_or_20 ['formula_exit_or_20']
002718 formula_gs_raw_buy medium_x3_cooldown formula_exit_or_30 ['formula_exit_or_30']
```

Regression checks:

```text
python -m py_compile compute.py execution_model.py main.py formula_engine.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
node inline script syntax check
/ 200
/api/status 200
/api/strategies 200
/api/unified 200
/api/parameter-search 200
/api/chart/301511?strategy=formula_activity_breakout 200
```

Updated residual risk:

- Sell-rule search is now in the per-stock parameter competition. Remaining research depth is local per-stock continuous optimization, not the core grid-search pipeline.

## 2026-05-20 Follow-up: Sell Rule Visibility in Strategy Research UI

Changes:

- Parameter variant table now shows `sell_rule` as its own sortable column.
- Strategy research top rows now display `variant_id · sell_rule` instead of only `variant_id · holding_days`.
- The card label for `best_sell_rule` was changed to `全市场卖出审计` to avoid confusing the full-market audit winner with per-stock optimized sell rules.

Verification:

```text
node inline script syntax check
/api/parameter-search 200
metric_count 228
variant_total 228
activity_breakout strict_capped fixed_60 60
gs_pullback_confirm loose fixed_60 60
gs_raw_buy fast_x3_cooldown fixed_60 60
ma_base_breakout early_60_120 fixed_60 60
volume_base_breakout case_301511_cooldown fixed_60 60
```

## 2026-05-20 Follow-up: Local Optuna Sample Audit

Changes:

- Added `scripts/formula_local_optuna.py`.
- The script runs local per-stock Optuna trials for formula parameters and sell-rule selection.
- Outputs:
  - `analysis/formula_local_optuna_samples.csv`
  - `analysis/formula_local_optuna_samples.md`
- The pilot does not overwrite production `analysis/stock_formula_best.csv`.

Command:

```text
python scripts/formula_local_optuna.py --codes 301511 301658 688700 002718 --trials 24 --max-signals-per-stock 120
```

Result:

```text
formula_local_optuna:done rows=20 elapsed=3.2s
```

Top observed deltas:

```text
688700 gs_pullback_confirm baseline=-14.04 optuna=50.07 delta=64.11
301658 activity_breakout baseline=65.79 optuna=84.67 delta=18.88
301658 volume_base_breakout baseline=77.57 optuna=86.16 delta=8.59
301511 gs_raw_buy baseline=57.49 optuna=62.11 delta=4.63
301511 volume_base_breakout baseline=72.82 optuna=76.16 delta=3.33
```

Interpretation:

- Local continuous search can find improvements outside the named parameter grid for some sample stock/formula pairs.
- Missing production rows are no longer converted to default scores; they are tracked via `baseline_status` / `baseline_reason`.
- This is still an audit artifact, not production data.

Updated residual risk:

- Local Optuna is proven on key samples but is not yet integrated into the full-market production parameter table. A production pass needs guardrails against overfitting, minimum sample counts, and a merge policy for replacing grid results.

## 2026-05-20 Follow-up: Local Optuna Adoption Guardrails

Changes:

- Added `scripts/formula_local_optuna_adoption.py`.
- Generated `analysis/formula_local_optuna_adoption_candidates.csv`.
- Generated `analysis/formula_local_optuna_adoption_candidates.md`.
- `/api/parameter-search` now returns `local_optuna` summary and candidate rows.
- Strategy research UI now shows a `局部 Optuna 候选` card above the formula cards.

Guardrails:

```text
baseline_score >= 0
optuna_signal_count >= 6
score_delta >= 3
optuna_win_rate >= 45%
optuna_avg_ret > 0
trials >= 20
```

Result:

```text
formula_local_optuna_adoption: rows=20 candidates=4 rejected=16
```

Candidates:

```text
301658 activity_breakout baseline=65.79 optuna=84.67 delta=18.88 signals=15
002718 gs_pullback_confirm baseline=86.16 optuna=93.53 delta=7.37 signals=9
002718 activity_breakout baseline=79.45 optuna=83.97 delta=4.52 signals=38
301511 activity_breakout baseline=66.25 optuna=69.82 delta=3.57 signals=28
```

API/UI verification:

```text
/api/parameter-search 200
metric_count 228
local_optuna row_count=20 candidate_count=4 rejected_count=16
local_optuna status_counts={'missing_baseline_result': 4, 'missing_optuna_result': 2}
node inline script syntax check
```

Updated residual risk:

- Local Optuna now has an explicit adoption gate, but candidates are still not production replacements. A full-market run and second validation split are required before merging local Optuna rows into `stock_formula_best.csv`.

## 2026-05-20 Correction: Missing Result Diagnostics

Issue:

- The first local Optuna sample report used `-999` as a missing baseline score.
- That made missing production rows look like very large positive deltas.
- Missing optimization results should be diagnosed, not converted into default scores.

Changes:

- `scripts/formula_local_optuna.py` now writes:
  - `baseline_status`
  - `baseline_reason`
  - `optuna_status`
  - `optuna_reason`
- Missing baseline rows are recorded as `missing_baseline_result`.
- Optuna runs that produce no usable result are recorded as `missing_optuna_result`, with failure counts such as `no_entry_signal`.
- `score_delta` is blank unless both baseline and Optuna scores exist.
- `scripts/formula_local_optuna_adoption.py` rejects any row whose baseline or Optuna status is not `ok`.
- `/api/parameter-search` preserves null numeric fields and returns local Optuna status/rejection counts.
- Strategy research UI shows local Optuna missing status counts instead of hiding them.

Re-run:

```text
python scripts/formula_local_optuna.py --codes 301511 301658 688700 002718 --trials 24 --max-signals-per-stock 120
python scripts/formula_local_optuna_adoption.py
```

Result:

```text
formula_local_optuna:done rows=20 elapsed=3.2s
formula_local_optuna_adoption: rows=20 candidates=4 rejected=16
missing_baseline_result=4
missing_optuna_result=2
```

Diagnosed missing rows:

```text
301658 ma_base_breakout baseline=missing_baseline_result optuna=ok
002718 ma_base_breakout baseline=missing_baseline_result optuna=ok
301511 gs_pullback_confirm baseline=missing_baseline_result optuna=missing_optuna_result reason=no_entry_signal
301511 ma_base_breakout baseline=missing_baseline_result optuna=missing_optuna_result reason=no_entry_signal
```

Updated interpretation:

- Rows with missing baseline are discovery/data-quality leads, not scored improvements.
- Rows with missing Optuna result identify formula/parameter spaces that produced no entry signal in the sampled trials.
- At this correction stage the full-sample candidate count was `4`, and the rejected count became `16` because missing cases were retained for investigation instead of being dropped or default-scored. This was later superseded by the validation-split gate below.

## 2026-05-20 Follow-up: Local Optuna Validation Split

Issue:

- Full-sample local Optuna candidates were still too weak for production adoption because parameter search and evaluation used the same sample.
- Candidate rows needed a chronological validation split before being treated as adoption candidates.

Changes:

- `scripts/formula_local_optuna.py` now splits executable trades chronologically:
  - earlier 70%: training set;
  - later 30%: validation set.
- Optuna objective now maximizes training-set score, not full-sample score.
- The script writes both baseline and Optuna split metrics:
  - `baseline_source_score` for the raw production CSV score;
  - `baseline_score` for the same-script recomputed full-sample baseline score used in deltas;
  - `*_train_signal_count`, `*_train_win_rate`, `*_train_avg_ret`, `*_train_score`
  - `*_validation_signal_count`, `*_validation_win_rate`, `*_validation_avg_ret`, `*_validation_score`
  - `validation_score_delta`
- `scripts/formula_local_optuna_adoption.py` now requires validation-set guardrails:
  - validation signal count >= `3`
  - validation win rate >= `45%`
  - validation average return > `0`
  - validation score delta >= `0`
- `/api/parameter-search` and the strategy research UI expose validation metrics for local Optuna candidates.

Re-run:

```text
python scripts/formula_local_optuna.py --codes 301511 301658 688700 002718 --trials 24 --max-signals-per-stock 120
python scripts/formula_local_optuna_adoption.py
```

Result:

```text
formula_local_optuna:done rows=20 elapsed=3.4s
formula_local_optuna_adoption: rows=20 candidates=2 rejected=18
local_optuna status_counts={'missing_baseline_result': 4, 'missing_optuna_result': 2}
```

Score-delta basis:

```text
baseline_source_score is retained from stock_formula_best.csv for audit only.
baseline_score is recomputed from the production params/sell_rule in this script.
score_delta = optuna_score - baseline_score
validation_score_delta = optuna_validation_score - baseline_validation_score
```

Candidates after validation:

```text
002718 activity_breakout delta=4.518378 validation_delta=0.769231 validation_n=11 validation_avg_ret=1.006273
688700 activity_breakout delta=3.247896 validation_delta=4.748095 validation_n=17 validation_avg_ret=0.103588
```

Rejected examples:

```text
301658 activity_breakout was a prior full-sample candidate, but now fails validation_delta<0.
002718 gs_pullback_confirm was a prior full-sample candidate, but now fails validation_delta<0.
301511 activity_breakout was a prior full-sample candidate, but now fails full-sample delta after train-only selection.
```

Updated residual risk:

- The local Optuna pilot now has a train/validation adoption gate, but it is still a 4-stock sample. Full-market local optimization must be run before any production merge.

## 2026-05-20 Follow-up: Resumable Local Optuna Batch Runner

Issue:

- The local Optuna pilot could validate key samples, but it was not yet operationally safe to expand toward the full market.
- A full-market pass needs resumability, independent artifacts, and no writes to production `stock_formula_best.csv`.

Changes:

- Added `scripts/formula_local_optuna_batch.py`.
- The batch runner supports:
  - `--max-stocks`
  - `--offset`
  - `--codes`
  - `--formulas`
  - `--trials`
  - `--max-signals-per-stock`
  - `--resume`
  - custom `--output` and `--report`
- It reuses the same local Optuna evaluation code as the sample runner:
  - production baseline params and sell rule are recomputed;
  - Optuna maximizes training-set score;
  - validation split metrics are written;
  - missing baseline/Optuna results keep status and reason fields.
- It writes independent batch artifacts and does not overwrite:
  - `analysis/formula_local_optuna_samples.csv`
  - `analysis/formula_local_optuna_adoption_candidates.csv`
  - `analysis/stock_formula_best.csv`
- `scripts/formula_local_optuna_adoption.py` now accepts:
  - `--input`
  - `--output`
  - `--report`

Smoke command:

```text
python scripts/formula_local_optuna_batch.py --max-stocks 3 --formulas activity_breakout volume_base_breakout --trials 8 --max-signals-per-stock 80 --output analysis/formula_local_optuna_batch_smoke.csv --report analysis/formula_local_optuna_batch_smoke.md
python scripts/formula_local_optuna_batch.py --max-stocks 3 --formulas activity_breakout volume_base_breakout --trials 8 --max-signals-per-stock 80 --output analysis/formula_local_optuna_batch_smoke.csv --report analysis/formula_local_optuna_batch_smoke.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch_smoke.csv --output analysis/formula_local_optuna_batch_smoke_adoption.csv --report analysis/formula_local_optuna_batch_smoke_adoption.md
```

Smoke result:

```text
formula_local_optuna_batch:done rows=6 new_rows=6 elapsed=0.4s
formula_local_optuna_batch:done rows=6 new_rows=0 elapsed=0.0s
formula_local_optuna_adoption: rows=6 candidates=0 rejected=6
```

Interpretation:

- Batch output and resume behavior are working.
- The smoke run used only 8 trials, so adoption correctly rejected every row with `trials<20`.
- Several rows also failed validation sample/return/delta gates, confirming that the batch path applies the same conservative adoption policy.

Updated residual risk:

- The batch runner makes full-market expansion operationally possible, but a full-market run has not been executed yet.
- Production merge logic remains intentionally absent; accepted batch rows still require a separate guarded merge policy before any `stock_formula_best.csv` replacement.

## 2026-05-20 Follow-up: Local Optuna Dry-run Merge Plan

Issue:

- Validated local Optuna candidates still needed an auditable path into the production `stock_formula_best.csv` schema.
- The path must not modify production results until a full-market run and explicit merge policy exist.

Changes:

- Added `scripts/formula_local_optuna_merge_plan.py`.
- The script reads an adoption CSV and production `analysis/stock_formula_best.csv`.
- It writes:
  - `analysis/formula_local_optuna_merge_plan.csv`
  - `analysis/formula_local_optuna_merge_plan.md`
  - `analysis/formula_local_optuna_stock_best_replacements.csv`
- Replacement rows use the existing production schema:
  - `formula_id`
  - `variant_id`
  - `stock_code`
  - `sell_rule`
  - `holding_days`
  - `signal_count`
  - `win_rate`
  - `avg_ret`
  - `avg_dd`
  - `calmar`
  - `delay_buy_rate`
  - `delay_sell_rate`
  - `score`
  - `params`
- Replacement `variant_id` is set to `local_optuna_t<trials>_vsplit`.
- The script is dry-run only and does not write to `analysis/stock_formula_best.csv`.

Command:

```text
python scripts/formula_local_optuna.py --codes 301511 301658 688700 002718 --trials 24 --max-signals-per-stock 120
python scripts/formula_local_optuna_adoption.py
python scripts/formula_local_optuna_merge_plan.py
```

Result:

```text
formula_local_optuna_adoption: rows=20 candidates=2 rejected=18
formula_local_optuna_merge_plan: rows=20 replacements=2
```

Replacement preview:

```text
002718 activity_breakout default -> local_optuna_t24_vsplit score 79.45 -> 83.97 validation_delta=0.77
688700 activity_breakout classic_capped -> local_optuna_t24_vsplit score 65.02 -> 68.26 validation_delta=4.75
```

Data-quality check:

```text
analysis/formula_local_optuna_stock_best_replacements.csv includes delay_buy_rate/delay_sell_rate.
002718 activity_breakout delay_buy_rate=0.000000 delay_sell_rate=0.000000
688700 activity_breakout delay_buy_rate=0.000000 delay_sell_rate=0.017544
```

API/UI integration:

```text
/api/parameter-search local_optuna.merge_plan row_count=20 replacement_count=2 replacement_schema_rows=2 dry_run=True
002718 activity_breakout default -> local_optuna_t24_vsplit score_delta=4.518377 validation_delta=0.769231
688700 activity_breakout classic_capped -> local_optuna_t24_vsplit score_delta=3.247896 validation_delta=4.748095
```

UI:

- The strategy research panel now shows a `局部 Optuna 合并预案` card.
- The card displays dry-run status, replacement count, schema-compatible row count, old/new variants, score delta, and validation delta.
- The card explicitly states that production merge requires full-market batch audit first.

Updated residual risk:

- The dry-run replacement rows are schema-compatible, but they are still sample-only. Do not merge them into production until the batch runner covers the intended market scope and the resulting adoption set is reviewed.

## 2026-05-20 Follow-up: First Real Local Optuna Batch

Scope:

- First 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- 100 total `(stock_code, formula_id)` rows.
- Output is independent from the 4-stock sample files and does not write production `stock_formula_best.csv`.

Commands:

```text
python scripts/formula_local_optuna_batch.py --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=100 new_rows=100 elapsed=20.4s
formula_local_optuna_batch:done rows=100 new_rows=0 elapsed=0.0s
```

Adoption result:

```text
formula_local_optuna_adoption: rows=100 candidates=6 rejected=94
formula_local_optuna_merge_plan: rows=100 replacements=6
```

Candidates:

```text
000001 activity_breakout score_delta=26.07 validation_delta=6.07
000010 activity_breakout score_delta=10.62 validation_delta=17.04
000028 activity_breakout score_delta=9.64 validation_delta=3.49
000026 volume_base_breakout score_delta=7.32 validation_delta=1.01
000006 activity_breakout score_delta=4.82 validation_delta=26.39
000026 gs_pullback_confirm score_delta=3.50 validation_delta=11.39
```

Rejection reason counts:

```json
{
  "avg_ret<=0": 4,
  "baseline_score<0.0": 1,
  "baseline_status=missing_baseline_result": 14,
  "delta<3.0": 47,
  "optuna_status=missing_optuna_result": 6,
  "signals<6": 35,
  "validation_avg_ret<=0": 57,
  "validation_delta<0.0": 63,
  "validation_signals<3": 46,
  "validation_win_rate<0.45": 55,
  "win_rate<0.45": 18
}
```

API/UI integration:

```text
/api/parameter-search local_optuna.batch row_count=100 candidate_count=6 rejected_count=94
/api/parameter-search local_optuna.batch.merge_plan replacement_count=6 replacement_schema_rows=6 dry_run=True
```

UI:

- The strategy research panel now shows a `局部 Optuna 批量` card.
- The card displays 20-stock batch row count, candidate count, dry-run replacement count, and replacement previews.

Updated residual risk:

- This is still only the first 20-stock batch. It proves the operational path and produces real candidates, but full-market production still requires continuing batches across the remaining market and reviewing aggregate replacement quality.

## 2026-05-20 Follow-up: Second Real Local Optuna Batch

Scope:

- Offset 20, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 40 stocks and 200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 20 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=200 new_rows=100 elapsed=20.3s
formula_local_optuna_batch:done rows=200 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=200 candidates=9 rejected=191
formula_local_optuna_merge_plan: rows=200 replacements=9
```

Cumulative candidate distribution:

```text
activity_breakout: 6
volume_base_breakout: 1
gs_raw_buy: 1
gs_pullback_confirm: 1
```

New replacement examples from the second batch:

```text
000058 activity_breakout score_delta=21.14 validation_delta=16.67
000035 activity_breakout score_delta=15.63 validation_delta=9.02
000037 gs_raw_buy score_delta=6.89 validation_delta=14.71
```

API/UI verification:

```text
/api/parameter-search local_optuna.batch row_count=200 candidate_count=9 rejected_count=191
/api/parameter-search local_optuna.batch.merge_plan replacement_count=9 replacement_schema_rows=9 dry_run=True
```

Updated residual risk:

- The batch path has now covered 40 stocks but still far from full-market coverage. Continue offset-based batches before considering any production merge.

## 2026-05-20 Follow-up: Third Real Local Optuna Batch

Scope:

- Offset 40, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 60 stocks and 300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 40 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=300 new_rows=100 elapsed=19.6s
formula_local_optuna_batch:done rows=300 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=300 candidates=13 rejected=287
formula_local_optuna_merge_plan: rows=300 replacements=13
```

Cumulative candidate distribution:

```text
activity_breakout: 8
gs_raw_buy: 3
volume_base_breakout: 1
gs_pullback_confirm: 1
```

New replacement examples from the third batch:

```text
000156 activity_breakout score_delta=28.01 validation_delta=40.87
000065 activity_breakout score_delta=9.11 validation_delta=30.01
000153 gs_raw_buy score_delta=7.66 validation_delta=8.28
000089 gs_raw_buy score_delta=4.28 validation_delta=4.38
```

API/UI verification:

```text
/api/parameter-search local_optuna.batch row_count=300 candidate_count=13 rejected_count=287
/api/parameter-search local_optuna.batch.merge_plan replacement_count=13 replacement_schema_rows=13 dry_run=True
```

Updated residual risk:

- The batch path has now covered 60 stocks. Continue offset-based batches; do not production-merge until coverage and aggregate quality are sufficient.

## 2026-05-20 Follow-up: Fourth Real Local Optuna Batch

Scope:

- Offset 60, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 80 stocks and 400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 60 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=400 new_rows=100 elapsed=19.8s
formula_local_optuna_batch:done rows=400 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=400 candidates=17 rejected=383
formula_local_optuna_merge_plan: rows=400 replacements=17
```

Cumulative candidate distribution:

```text
activity_breakout: 9
gs_raw_buy: 4
gs_pullback_confirm: 2
volume_base_breakout: 2
```

New replacement examples from the fourth batch:

```text
000409 activity_breakout score_delta=24.43 validation_delta=35.02
000404 gs_pullback_confirm score_delta=15.52 validation_delta=2.37
000408 volume_base_breakout score_delta=11.45 validation_delta=5.58
000301 gs_raw_buy score_delta=3.81 validation_delta=1.12
```

API/UI verification:

```text
/api/parameter-search local_optuna.batch row_count=400 candidate_count=17 rejected_count=383
/api/parameter-search local_optuna.batch.merge_plan replacement_count=17 replacement_schema_rows=17 dry_run=True
```

Updated residual risk:

- The batch path has now covered 80 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.

## 2026-05-20 Follow-up: Fifth Real Local Optuna Batch

Scope:

- Offset 80, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 100 stocks and 500 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 80 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=500 new_rows=100 elapsed=20.5s
formula_local_optuna_batch:done rows=500 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=500 candidates=19 rejected=481
formula_local_optuna_merge_plan: rows=500 replacements=19
```

Cumulative candidate distribution:

```text
activity_breakout: 10
gs_raw_buy: 5
gs_pullback_confirm: 2
volume_base_breakout: 2
```

New replacement examples from the fifth batch:

```text
000425 activity_breakout score_delta=12.27 validation_delta=9.40
000516 gs_raw_buy score_delta=9.53 validation_delta=6.96
```

API/UI verification:

```text
/api/parameter-search local_optuna.batch row_count=500 candidate_count=19 rejected_count=481
/api/parameter-search local_optuna.batch.merge_plan replacement_count=19 replacement_schema_rows=19 dry_run=True
```

Updated residual risk:

- The batch path has now covered 100 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.

## 2026-05-20 Follow-up: Sixth Real Local Optuna Batch

Scope:

- Offset 100, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 120 stocks and 600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 100 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=600 new_rows=100 elapsed=20.7s
formula_local_optuna_batch:done rows=600 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=600 candidates=22 rejected=578
formula_local_optuna_merge_plan: rows=600 replacements=22
```

Cumulative candidate distribution:

```text
activity_breakout: 13
gs_raw_buy: 5
gs_pullback_confirm: 2
volume_base_breakout: 2
```

New replacement examples from the sixth batch:

```text
000530 activity_breakout score_delta=33.00 validation_delta=33.16
000519 activity_breakout score_delta=9.53 validation_delta=11.46
000537 activity_breakout score_delta=7.01 validation_delta=20.22
```

API/UI verification:

```text
/api/parameter-search local_optuna.batch row_count=600 candidate_count=22 rejected_count=578
/api/parameter-search local_optuna.batch.merge_plan replacement_count=22 replacement_schema_rows=22 dry_run=True
```

Updated residual risk:

- The batch path has now covered 120 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.

## 2026-05-20 Follow-up: Missing Result Investigation Schema

Scope:

- Tightened local Optuna missing-result handling after review.
- Missing baseline or Optuna results are now investigation leads, not default-valued backtest rows.
- Existing cumulative batch artifact remains at 600 rows / 120 stocks; no new optimization rows were added in this step.

Code changes:

- `scripts/formula_local_optuna.py` now writes `baseline_investigation` and `optuna_investigation` JSON payloads.
- `scripts/formula_local_optuna_batch.py` writes the same investigation fields and backfills them when `--resume` rewrites old rows.
- `scripts/formula_local_optuna_adoption.py` now parses empty numeric metrics as missing, not zero, and emits `missing_metric=...` rejection reasons.

Verification commands:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/formula_local_optuna_batch.py --codes 301511 --formulas gs_pullback_confirm --trials 2 --max-signals-per-stock 20 --output analysis/formula_local_optuna_batch_smoke.csv --report analysis/formula_local_optuna_batch_smoke.md
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch_smoke.csv --output analysis/formula_local_optuna_batch_smoke_adoption.csv --report analysis/formula_local_optuna_batch_smoke_adoption.md
python scripts/formula_local_optuna_batch.py --offset 100 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Smoke evidence:

```text
baseline_status=missing_baseline_result
baseline_investigation={"reason": "stock_formula_best.csv has no row for this stock/formula", "status": "missing_baseline_result"}
optuna_status=missing_optuna_result
optuna_investigation={"reason": {"failure_counts": {"no_entry_signal": 2}, "failure_examples": {"no_entry_signal": "formula produced no entry signals"}}, "status": "missing_optuna_result"}
```

Cumulative batch migration:

```text
formula_local_optuna_batch:done rows=600 new_rows=0 elapsed=0.0s
formula_local_optuna_adoption: rows=600 candidates=22 rejected=578
formula_local_optuna_merge_plan: rows=600 replacements=22
```

Current missing-result investigation counts:

```text
missing_baseline_result: stock_formula_best.csv has no row for this stock/formula = 105
missing_optuna_result: no_entry_signal in all 24 trials = 31
missing_metric=optuna_validation_signal_count = 71
missing_metric=optuna_validation_win_rate = 71
missing_metric=optuna_validation_avg_ret = 71
```

Updated policy:

- Do not fill missing baseline, Optuna, or validation metrics with `0`.
- Do not promote rows with missing investigation fields into replacement candidates.
- Investigate the missing reason first: missing production best row, formula produced no entry signals, no executable validation split, or another concrete status chain.

## 2026-05-20 Follow-up: Seventh Real Local Optuna Batch

Scope:

- Offset 120, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 140 stocks and 700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 120 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=700 new_rows=100 elapsed=20.2s
formula_local_optuna_batch:done rows=700 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=700 candidates=28 rejected=672
formula_local_optuna_merge_plan: rows=700 replacements=28
```

Cumulative candidate distribution:

```text
activity_breakout: 16
gs_raw_buy: 6
gs_pullback_confirm: 4
volume_base_breakout: 2
```

New replacement examples from the seventh batch:

```text
000553 gs_pullback_confirm score_delta=36.85 validation_delta=52.48 sell_rule=fixed_5
000543 gs_raw_buy score_delta=11.20 validation_delta=4.13 sell_rule=fixed_60
000550 activity_breakout score_delta=8.73 validation_delta=36.71 sell_rule=fixed_15
000545 activity_breakout score_delta=8.47 validation_delta=3.69 sell_rule=fixed_10
000548 activity_breakout score_delta=5.58 validation_delta=27.94 sell_rule=formula_exit_or_10
000558 gs_pullback_confirm score_delta=4.34 validation_delta=8.84 sell_rule=formula_exit_or_30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 84
missing_baseline_result / missing_optuna_result = 33
ok / missing_optuna_result = 2
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=700 candidate_count=28 rejected_count=672
/api/parameter-search local_optuna.batch.merge_plan replacement_count=28 replacement_schema_rows=28 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 140 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Eighth Real Local Optuna Batch

Scope:

- Offset 140, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 160 stocks and 800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 140 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=800 new_rows=100 elapsed=21.1s
formula_local_optuna_batch:done rows=800 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=800 candidates=37 rejected=763
formula_local_optuna_merge_plan: rows=800 replacements=37
```

Cumulative candidate distribution:

```text
activity_breakout: 24
gs_raw_buy: 7
gs_pullback_confirm: 4
volume_base_breakout: 2
```

New replacement examples from the eighth batch:

```text
000596 activity_breakout score_delta=53.18 validation_delta=77.52 sell_rule=fixed_10
000567 activity_breakout score_delta=30.44 validation_delta=25.99 sell_rule=fixed_5
000582 activity_breakout score_delta=29.41 validation_delta=4.21 sell_rule=fixed_15
000573 activity_breakout score_delta=18.67 validation_delta=15.12 sell_rule=fixed_60
000572 activity_breakout score_delta=18.33 validation_delta=10.54 sell_rule=formula_exit_or_30
000565 activity_breakout score_delta=15.75 validation_delta=12.18 sell_rule=fixed_20
000573 gs_raw_buy score_delta=5.91 validation_delta=5.08 sell_rule=fixed_15
000595 activity_breakout score_delta=5.89 validation_delta=40.46 sell_rule=formula_exit_or_15
000571 activity_breakout score_delta=4.27 validation_delta=0.01 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 96
missing_baseline_result / missing_optuna_result = 35
ok / missing_optuna_result = 2
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=800 candidate_count=37 rejected_count=763
/api/parameter-search local_optuna.batch.merge_plan replacement_count=37 replacement_schema_rows=37 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 160 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Ninth Real Local Optuna Batch

Scope:

- Offset 160, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 180 stocks and 900 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 160 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=900 new_rows=100 elapsed=20.6s
formula_local_optuna_batch:done rows=900 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=900 candidates=43 rejected=857
formula_local_optuna_merge_plan: rows=900 replacements=43
```

Cumulative candidate distribution:

```text
activity_breakout: 27
gs_raw_buy: 7
gs_pullback_confirm: 5
volume_base_breakout: 4
```

New replacement examples from the ninth batch:

```text
000623 gs_pullback_confirm score_delta=45.54 validation_delta=5.17 sell_rule=fixed_60
000623 volume_base_breakout score_delta=28.99 validation_delta=0.12 sell_rule=fixed_5
000612 volume_base_breakout score_delta=8.26 validation_delta=5.12 sell_rule=fixed_60
000603 activity_breakout score_delta=6.63 validation_delta=6.62 sell_rule=fixed_30
000629 activity_breakout score_delta=3.34 validation_delta=30.85 sell_rule=formula_exit_or_10
000607 activity_breakout score_delta=3.27 validation_delta=10.24 sell_rule=fixed_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 109
missing_baseline_result / missing_optuna_result = 35
ok / missing_optuna_result = 2
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=900 candidate_count=43 rejected_count=857
/api/parameter-search local_optuna.batch.merge_plan replacement_count=43 replacement_schema_rows=43 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 180 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Tenth Real Local Optuna Batch

Scope:

- Offset 180, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 200 stocks and 1000 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 180 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=1000 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=1000 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=1000 candidates=48 rejected=952
formula_local_optuna_merge_plan: rows=1000 replacements=48
```

Cumulative candidate distribution:

```text
activity_breakout: 30
gs_raw_buy: 8
gs_pullback_confirm: 6
volume_base_breakout: 4
```

New replacement examples from the tenth batch:

```text
000637 gs_pullback_confirm score_delta=38.09 validation_delta=53.83 sell_rule=fixed_60
000650 activity_breakout score_delta=29.92 validation_delta=50.61 sell_rule=formula_exit_or_5
000663 activity_breakout score_delta=14.48 validation_delta=29.45 sell_rule=fixed_60
000665 activity_breakout score_delta=13.08 validation_delta=2.12 sell_rule=fixed_5
000663 gs_raw_buy score_delta=7.24 validation_delta=9.12 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 120
missing_baseline_result / missing_optuna_result = 38
ok / missing_optuna_result = 2
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=1000 candidate_count=48 rejected_count=952
/api/parameter-search local_optuna.batch.merge_plan replacement_count=48 replacement_schema_rows=48 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 200 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Eleventh Real Local Optuna Batch

Scope:

- Offset 200, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 220 stocks and 1100 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 200 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=1100 new_rows=100 elapsed=20.8s
formula_local_optuna_batch:done rows=1100 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=1100 candidates=52 rejected=1048
formula_local_optuna_merge_plan: rows=1100 replacements=52
```

Cumulative candidate distribution:

```text
activity_breakout: 33
gs_raw_buy: 8
gs_pullback_confirm: 7
volume_base_breakout: 4
```

New replacement examples from the eleventh batch:

```text
000672 activity_breakout score_delta=9.54 validation_delta=5.14 sell_rule=fixed_10
000680 activity_breakout score_delta=8.71 validation_delta=5.46 sell_rule=fixed_30
000678 activity_breakout score_delta=4.50 validation_delta=15.75 sell_rule=fixed_15
000688 gs_pullback_confirm score_delta=4.34 validation_delta=4.11 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 135
missing_baseline_result / missing_optuna_result = 41
ok / missing_optuna_result = 2
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=1100 candidate_count=52 rejected_count=1048
/api/parameter-search local_optuna.batch.merge_plan replacement_count=52 replacement_schema_rows=52 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 220 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Twelfth Real Local Optuna Batch

Scope:

- Offset 220, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 240 stocks and 1200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 220 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=1200 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=1200 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=1200 candidates=59 rejected=1141
formula_local_optuna_merge_plan: rows=1200 replacements=59
```

Cumulative candidate distribution:

```text
activity_breakout: 36
volume_base_breakout: 8
gs_raw_buy: 8
gs_pullback_confirm: 7
```

New replacement examples from the twelfth batch:

```text
000702 volume_base_breakout score_delta=21.58 validation_delta=30.74 sell_rule=formula_exit_or_15
000709 activity_breakout score_delta=15.46 validation_delta=5.89 sell_rule=formula_exit_or_5
000719 activity_breakout score_delta=12.13 validation_delta=8.32 sell_rule=fixed_5
000719 volume_base_breakout score_delta=10.37 validation_delta=6.27 sell_rule=fixed_60
000708 activity_breakout score_delta=9.84 validation_delta=6.67 sell_rule=formula_exit_or_5
000700 volume_base_breakout score_delta=9.09 validation_delta=5.58 sell_rule=fixed_20
000703 volume_base_breakout score_delta=6.64 validation_delta=2.01 sell_rule=formula_exit_or_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 150
missing_baseline_result / missing_optuna_result = 42
ok / missing_optuna_result = 3
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=1200 candidate_count=59 rejected_count=1141
/api/parameter-search local_optuna.batch.merge_plan replacement_count=59 replacement_schema_rows=59 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 240 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Thirteenth Real Local Optuna Batch

Scope:

- Offset 240, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 260 stocks and 1300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 240 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=1300 new_rows=100 elapsed=19.9s
formula_local_optuna_batch:done rows=1300 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=1300 candidates=65 rejected=1235
formula_local_optuna_merge_plan: rows=1300 replacements=65
```

Cumulative candidate distribution:

```text
activity_breakout: 39
gs_raw_buy: 11
volume_base_breakout: 8
gs_pullback_confirm: 7
```

New replacement examples from the thirteenth batch:

```text
000757 activity_breakout score_delta=23.53 validation_delta=22.28 sell_rule=fixed_15
000739 activity_breakout score_delta=14.93 validation_delta=2.19 sell_rule=formula_exit_or_5
000733 gs_raw_buy score_delta=6.96 validation_delta=5.84 sell_rule=formula_exit_or_10
000755 activity_breakout score_delta=6.06 validation_delta=9.35 sell_rule=fixed_15
000738 gs_raw_buy score_delta=3.86 validation_delta=5.82 sell_rule=formula_exit_or_5
000725 gs_raw_buy score_delta=3.55 validation_delta=8.39 sell_rule=fixed_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 165
missing_baseline_result / missing_optuna_result = 43
ok / missing_optuna_result = 3
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=1300 candidate_count=65 rejected_count=1235
/api/parameter-search local_optuna.batch.merge_plan replacement_count=65 replacement_schema_rows=65 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 260 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Fourteenth Real Local Optuna Batch

Scope:

- Offset 260, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 280 stocks and 1400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 260 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=1400 new_rows=100 elapsed=19.8s
formula_local_optuna_batch:done rows=1400 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=1400 candidates=71 rejected=1329
formula_local_optuna_merge_plan: rows=1400 replacements=71
```

Cumulative candidate distribution:

```text
activity_breakout: 43
gs_raw_buy: 12
gs_pullback_confirm: 8
volume_base_breakout: 8
```

New replacement examples from the fourteenth batch:

```text
000786 activity_breakout score_delta=48.16 validation_delta=33.98 sell_rule=fixed_10
000792 gs_pullback_confirm score_delta=18.07 validation_delta=10.32 sell_rule=fixed_30
000778 activity_breakout score_delta=12.41 validation_delta=16.74 sell_rule=formula_exit_or_5
000779 gs_raw_buy score_delta=8.54 validation_delta=15.14 sell_rule=fixed_20
000776 activity_breakout score_delta=8.09 validation_delta=10.37 sell_rule=formula_exit_or_5
000766 activity_breakout score_delta=6.77 validation_delta=9.14 sell_rule=fixed_30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 182
missing_baseline_result / missing_optuna_result = 47
ok / missing_optuna_result = 3
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=1400 candidate_count=71 rejected_count=1329
/api/parameter-search local_optuna.batch.merge_plan replacement_count=71 replacement_schema_rows=71 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 280 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Fifteenth Real Local Optuna Batch

Scope:

- Offset 280, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 300 stocks and 1500 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 280 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=1500 new_rows=100 elapsed=21.3s
formula_local_optuna_batch:done rows=1500 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=1500 candidates=74 rejected=1426
formula_local_optuna_merge_plan: rows=1500 replacements=74
```

Cumulative candidate distribution:

```text
activity_breakout: 43
gs_raw_buy: 14
volume_base_breakout: 9
gs_pullback_confirm: 8
```

New replacement examples from the fifteenth batch:

```text
000798 volume_base_breakout score_delta=12.89 validation_delta=2.32 sell_rule=fixed_20
000807 gs_raw_buy score_delta=3.65 validation_delta=7.41 sell_rule=fixed_30
000819 gs_raw_buy score_delta=3.50 validation_delta=10.40 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 192
missing_baseline_result / missing_optuna_result = 49
ok / missing_optuna_result = 4
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=1500 candidate_count=74 rejected_count=1426
/api/parameter-search local_optuna.batch.merge_plan replacement_count=74 replacement_schema_rows=74 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 300 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Sixteenth Real Local Optuna Batch

Scope:

- Offset 300, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 320 stocks and 1600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 300 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=1600 new_rows=100 elapsed=20.2s
formula_local_optuna_batch:done rows=1600 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=1600 candidates=76 rejected=1524
formula_local_optuna_merge_plan: rows=1600 replacements=76
```

Cumulative candidate distribution:

```text
activity_breakout: 44
gs_raw_buy: 14
volume_base_breakout: 10
gs_pullback_confirm: 8
```

New replacement examples from the sixteenth batch:

```text
000820 volume_base_breakout score_delta=16.23 validation_delta=21.77 sell_rule=formula_exit_or_5
000850 activity_breakout score_delta=12.63 validation_delta=10.63 sell_rule=formula_exit_or_15
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 204
missing_baseline_result / missing_optuna_result = 51
ok / missing_optuna_result = 4
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=1600 candidate_count=76 rejected_count=1524
/api/parameter-search local_optuna.batch.merge_plan replacement_count=76 replacement_schema_rows=76 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 320 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Seventeenth Real Local Optuna Batch

Scope:

- Offset 320, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 340 stocks and 1700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 320 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=1700 new_rows=100 elapsed=20.1s
formula_local_optuna_batch:done rows=1700 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=1700 candidates=80 rejected=1620
formula_local_optuna_merge_plan: rows=1700 replacements=80
```

Cumulative candidate distribution:

```text
activity_breakout: 47
gs_raw_buy: 14
volume_base_breakout: 11
gs_pullback_confirm: 8
```

New replacement examples from the seventeenth batch:

```text
000876 activity_breakout score_delta=17.19 validation_delta=10.04 sell_rule=formula_exit_or_5
000889 activity_breakout score_delta=15.19 validation_delta=17.21 sell_rule=formula_exit_or_5
000892 activity_breakout score_delta=11.16 validation_delta=7.96 sell_rule=fixed_60
000892 volume_base_breakout score_delta=3.96 validation_delta=9.90 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 218
missing_baseline_result / missing_optuna_result = 59
ok / missing_optuna_result = 5
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=1700 candidate_count=80 rejected_count=1620
/api/parameter-search local_optuna.batch.merge_plan replacement_count=80 replacement_schema_rows=80 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 340 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Eighteenth Real Local Optuna Batch

Scope:

- Offset 340, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 360 stocks and 1800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 340 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=1800 new_rows=100 elapsed=19.4s
formula_local_optuna_batch:done rows=1800 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=1800 candidates=86 rejected=1714
formula_local_optuna_merge_plan: rows=1800 replacements=86
```

Cumulative candidate distribution:

```text
activity_breakout: 49
gs_raw_buy: 18
volume_base_breakout: 11
gs_pullback_confirm: 8
```

New replacement examples from the eighteenth batch:

```text
000917 activity_breakout score_delta=16.83 validation_delta=0.18 sell_rule=fixed_10
000912 activity_breakout score_delta=14.29 validation_delta=6.06 sell_rule=fixed_15
000900 gs_raw_buy score_delta=7.74 validation_delta=2.59 sell_rule=formula_exit_or_5
000908 gs_raw_buy score_delta=4.26 validation_delta=3.85 sell_rule=formula_exit_or_60
000906 gs_raw_buy score_delta=4.11 validation_delta=4.46 sell_rule=formula_exit_or_60
000899 gs_raw_buy score_delta=3.05 validation_delta=5.73 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 227
missing_baseline_result / missing_optuna_result = 65
ok / missing_optuna_result = 5
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=1800 candidate_count=86 rejected_count=1714
/api/parameter-search local_optuna.batch.merge_plan replacement_count=86 replacement_schema_rows=86 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 360 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Nineteenth Real Local Optuna Batch

Scope:

- Offset 360, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 380 stocks and 1900 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 360 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=1900 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=1900 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=1900 candidates=94 rejected=1806
formula_local_optuna_merge_plan: rows=1900 replacements=94
```

Cumulative candidate distribution:

```text
activity_breakout: 55
gs_raw_buy: 19
volume_base_breakout: 12
gs_pullback_confirm: 8
```

New replacement examples from the nineteenth batch:

```text
000950 volume_base_breakout score_delta=34.88 validation_delta=36.18 sell_rule=fixed_15
000931 activity_breakout score_delta=25.23 validation_delta=22.77 sell_rule=fixed_20
000930 activity_breakout score_delta=18.62 validation_delta=11.17 sell_rule=fixed_5
000925 activity_breakout score_delta=15.32 validation_delta=11.36 sell_rule=fixed_10
000926 activity_breakout score_delta=12.21 validation_delta=22.55 sell_rule=formula_exit_or_5
000936 activity_breakout score_delta=11.92 validation_delta=16.78 sell_rule=formula_exit_or_10
000923 activity_breakout score_delta=8.59 validation_delta=10.72 sell_rule=formula_exit_or_5
000937 gs_raw_buy score_delta=4.48 validation_delta=8.02 sell_rule=fixed_15
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 242
missing_baseline_result / missing_optuna_result = 66
ok / missing_optuna_result = 5
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=1900 candidate_count=94 rejected_count=1806
/api/parameter-search local_optuna.batch.merge_plan replacement_count=94 replacement_schema_rows=94 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 380 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Twentieth Real Local Optuna Batch

Scope:

- Offset 380, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 400 stocks and 2000 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 380 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=2000 new_rows=100 elapsed=20.3s
formula_local_optuna_batch:done rows=2000 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=2000 candidates=95 rejected=1905
formula_local_optuna_merge_plan: rows=2000 replacements=95
```

Cumulative candidate distribution:

```text
activity_breakout: 56
gs_raw_buy: 19
volume_base_breakout: 12
gs_pullback_confirm: 8
```

New replacement example from the twentieth batch:

```text
000967 activity_breakout score_delta=6.67 validation_delta=54.89 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 257
missing_baseline_result / missing_optuna_result = 68
ok / missing_optuna_result = 5
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=2000 candidate_count=95 rejected_count=1905
/api/parameter-search local_optuna.batch.merge_plan replacement_count=95 replacement_schema_rows=95 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 400 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled and are not eligible for replacement candidates.

## 2026-05-20 Follow-up: Twenty-First Real Local Optuna Batch

Scope:

- Offset 400, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 420 stocks and 2100 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 400 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=2100 new_rows=100 elapsed=20.4s
formula_local_optuna_batch:done rows=2100 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=2100 candidates=103 rejected=1997
formula_local_optuna_merge_plan: rows=2100 replacements=103
```

Cumulative candidate distribution:

```text
activity_breakout: 59
gs_raw_buy: 21
volume_base_breakout: 13
gs_pullback_confirm: 10
```

New replacement examples from the twenty-first batch:

```text
000989 activity_breakout score_delta=19.69 validation_delta=7.75 sell_rule=formula_exit_or_5
000980 gs_pullback_confirm score_delta=18.66 validation_delta=75.02 sell_rule=fixed_30
000999 gs_pullback_confirm score_delta=18.25 validation_delta=0.00 sell_rule=fixed_30
000980 activity_breakout score_delta=9.71 validation_delta=12.64 sell_rule=fixed_15
001202 gs_raw_buy score_delta=8.23 validation_delta=0.10 sell_rule=fixed_60
000989 gs_raw_buy score_delta=7.50 validation_delta=12.30 sell_rule=fixed_20
001207 volume_base_breakout score_delta=6.55 validation_delta=27.46 sell_rule=fixed_15
000988 activity_breakout score_delta=3.28 validation_delta=9.65 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 268
missing_baseline_result / missing_optuna_result = 73
ok / missing_optuna_result = 6
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=2100 candidate_count=103 rejected_count=1997
/api/parameter-search local_optuna.batch.merge_plan replacement_count=103 replacement_schema_rows=103 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 420 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Twenty-Second Real Local Optuna Batch

Scope:

- Offset 420, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 440 stocks and 2200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 420 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=2200 new_rows=100 elapsed=18.7s
formula_local_optuna_batch:done rows=2200 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=2200 candidates=108 rejected=2092
formula_local_optuna_merge_plan: rows=2200 replacements=108
```

Cumulative candidate distribution:

```text
activity_breakout: 62
gs_raw_buy: 21
volume_base_breakout: 15
gs_pullback_confirm: 10
```

New replacement examples from the twenty-second batch:

```text
001222 activity_breakout score_delta=20.31 validation_delta=1.34 sell_rule=fixed_60
001226 volume_base_breakout score_delta=14.73 validation_delta=0.42 sell_rule=fixed_15
001208 volume_base_breakout score_delta=13.19 validation_delta=7.37 sell_rule=fixed_5
001215 activity_breakout score_delta=12.81 validation_delta=9.70 sell_rule=fixed_30
001227 activity_breakout score_delta=4.21 validation_delta=22.43 sell_rule=formula_exit_or_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 283
missing_baseline_result / missing_optuna_result = 82
ok / missing_optuna_result = 7
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=2200 candidate_count=108 rejected_count=2092
/api/parameter-search local_optuna.batch.merge_plan replacement_count=108 replacement_schema_rows=108 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 440 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Twenty-Third Real Local Optuna Batch

Scope:

- Offset 440, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 460 stocks and 2300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 440 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=2300 new_rows=100 elapsed=17.4s
formula_local_optuna_batch:done rows=2300 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=2300 candidates=111 rejected=2189
formula_local_optuna_merge_plan: rows=2300 replacements=111
```

Cumulative candidate distribution:

```text
activity_breakout: 64
gs_raw_buy: 21
volume_base_breakout: 16
gs_pullback_confirm: 10
```

New replacement examples from the twenty-third batch:

```text
001278 activity_breakout score_delta=21.46 validation_delta=3.68 sell_rule=formula_exit_or_5
001266 activity_breakout score_delta=19.83 validation_delta=21.92 sell_rule=fixed_5
001260 volume_base_breakout score_delta=14.30 validation_delta=9.12 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 297
missing_baseline_result / missing_optuna_result = 91
ok / missing_optuna_result = 7
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=2300 candidate_count=111 rejected_count=2189
/api/parameter-search local_optuna.batch.merge_plan replacement_count=111 replacement_schema_rows=111 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 460 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Twenty-Fourth Real Local Optuna Batch

Scope:

- Offset 460, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 480 stocks and 2400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 460 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=2400 new_rows=100 elapsed=16.1s
formula_local_optuna_batch:done rows=2400 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=2400 candidates=115 rejected=2285
formula_local_optuna_merge_plan: rows=2400 replacements=115
```

Cumulative candidate distribution:

```text
activity_breakout: 67
gs_raw_buy: 22
volume_base_breakout: 16
gs_pullback_confirm: 10
```

New replacement examples from the twenty-fourth batch:

```text
001311 activity_breakout score_delta=24.27 validation_delta=29.81 sell_rule=fixed_10
001299 activity_breakout score_delta=21.37 validation_delta=20.62 sell_rule=fixed_60
001313 activity_breakout score_delta=11.70 validation_delta=15.44 sell_rule=fixed_5
001288 gs_raw_buy score_delta=4.29 validation_delta=1.14 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 317
missing_baseline_result / missing_optuna_result = 106
ok / missing_optuna_result = 7
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=2400 candidate_count=115 rejected_count=2285
/api/parameter-search local_optuna.batch.merge_plan replacement_count=115 replacement_schema_rows=115 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 480 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Twenty-Fifth Real Local Optuna Batch

Scope:

- Offset 480, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 500 stocks and 2500 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 480 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=2500 new_rows=100 elapsed=17.1s
formula_local_optuna_batch:done rows=2500 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=2500 candidates=124 rejected=2376
formula_local_optuna_merge_plan: rows=2500 replacements=124
```

Cumulative candidate distribution:

```text
activity_breakout: 74
gs_raw_buy: 24
volume_base_breakout: 16
gs_pullback_confirm: 10
```

New replacement examples from the twenty-fifth batch:

```text
001337 activity_breakout score_delta=29.16 validation_delta=6.12 sell_rule=fixed_20
001314 activity_breakout score_delta=27.55 validation_delta=47.23 sell_rule=fixed_10
001318 activity_breakout score_delta=22.61 validation_delta=24.84 sell_rule=formula_exit_or_5
001336 activity_breakout score_delta=21.55 validation_delta=17.49 sell_rule=fixed_5
001332 activity_breakout score_delta=18.53 validation_delta=10.57 sell_rule=formula_exit_or_15
001336 gs_raw_buy score_delta=9.80 validation_delta=11.71 sell_rule=fixed_15
001319 activity_breakout score_delta=9.31 validation_delta=13.76 sell_rule=fixed_15
001326 gs_raw_buy score_delta=7.58 validation_delta=2.61 sell_rule=fixed_5
001339 activity_breakout score_delta=6.23 validation_delta=4.96 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 329
missing_baseline_result / missing_optuna_result = 118
ok / missing_optuna_result = 7
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=2500 candidate_count=124 rejected_count=2376
/api/parameter-search local_optuna.batch.merge_plan replacement_count=124 replacement_schema_rows=124 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 500 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Twenty-Sixth Real Local Optuna Batch

Scope:

- Offset 500, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 520 stocks and 2600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 500 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=2600 new_rows=100 elapsed=12.7s
formula_local_optuna_batch:done rows=2600 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=2600 candidates=130 rejected=2470
formula_local_optuna_merge_plan: rows=2600 replacements=130
```

Cumulative candidate distribution:

```text
activity_breakout: 79
gs_raw_buy: 25
volume_base_breakout: 16
gs_pullback_confirm: 10
```

New replacement examples from the twenty-sixth batch:

```text
001366 activity_breakout score_delta=29.30 validation_delta=0.24 sell_rule=fixed_5
001378 activity_breakout score_delta=25.99 validation_delta=25.68 sell_rule=formula_exit_or_15
001376 activity_breakout score_delta=24.86 validation_delta=27.38 sell_rule=formula_exit_or_5
001368 activity_breakout score_delta=21.17 validation_delta=21.19 sell_rule=fixed_20
001359 activity_breakout score_delta=8.42 validation_delta=1.89 sell_rule=fixed_60
001367 gs_raw_buy score_delta=7.41 validation_delta=7.95 sell_rule=formula_exit_or_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 347
missing_baseline_result / missing_optuna_result = 138
ok / missing_optuna_result = 7
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=2600 candidate_count=130 rejected_count=2470
/api/parameter-search local_optuna.batch.merge_plan replacement_count=130 replacement_schema_rows=130 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 520 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Twenty-Seventh Real Local Optuna Batch

Scope:

- Offset 520, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 540 stocks and 2700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 520 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=2700 new_rows=100 elapsed=18.1s
formula_local_optuna_batch:done rows=2700 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=2700 candidates=136 rejected=2564
formula_local_optuna_merge_plan: rows=2700 replacements=136
```

Cumulative candidate distribution:

```text
activity_breakout: 82
gs_raw_buy: 26
volume_base_breakout: 17
gs_pullback_confirm: 11
```

New replacement examples from the twenty-seventh batch:

```text
002011 activity_breakout score_delta=14.50 validation_delta=34.44 sell_rule=formula_exit_or_20
002004 gs_pullback_confirm score_delta=11.57 validation_delta=0.87 sell_rule=fixed_20
002005 activity_breakout score_delta=9.25 validation_delta=15.19 sell_rule=fixed_10
002001 volume_base_breakout score_delta=6.95 validation_delta=1.84 sell_rule=fixed_60
002010 activity_breakout score_delta=6.58 validation_delta=2.87 sell_rule=formula_exit_or_5
001696 gs_raw_buy score_delta=5.52 validation_delta=25.00 sell_rule=formula_exit_or_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 366
missing_baseline_result / missing_optuna_result = 146
ok / missing_optuna_result = 7
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=2700 candidate_count=136 rejected_count=2564
/api/parameter-search local_optuna.batch.merge_plan replacement_count=136 replacement_schema_rows=136 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 540 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Twenty-Eighth Real Local Optuna Batch

Scope:

- Offset 540, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 560 stocks and 2800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 540 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=2800 new_rows=100 elapsed=20.0s
formula_local_optuna_batch:done rows=2800 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=2800 candidates=140 rejected=2660
formula_local_optuna_merge_plan: rows=2800 replacements=140
```

Cumulative candidate distribution:

```text
activity_breakout: 85
gs_raw_buy: 27
volume_base_breakout: 17
gs_pullback_confirm: 11
```

New replacement examples from the twenty-eighth batch:

```text
002026 activity_breakout score_delta=28.88 validation_delta=20.85 sell_rule=fixed_60
002025 activity_breakout score_delta=22.57 validation_delta=9.44 sell_rule=fixed_60
002030 activity_breakout score_delta=6.10 validation_delta=9.46 sell_rule=formula_exit_or_5
002019 gs_raw_buy score_delta=4.46 validation_delta=0.00 sell_rule=formula_exit_or_30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 377
missing_baseline_result / missing_optuna_result = 149
ok / missing_optuna_result = 7
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=2800 candidate_count=140 rejected_count=2660
/api/parameter-search local_optuna.batch.merge_plan replacement_count=140 replacement_schema_rows=140 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 560 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Twenty-Ninth Real Local Optuna Batch

Scope:

- Offset 560, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 580 stocks and 2900 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 560 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=2900 new_rows=100 elapsed=20.5s
formula_local_optuna_batch:done rows=2900 new_rows=0 elapsed=0.0s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=2900 candidates=141 rejected=2759
formula_local_optuna_merge_plan: rows=2900 replacements=141
```

Cumulative candidate distribution:

```text
activity_breakout: 86
gs_raw_buy: 27
volume_base_breakout: 17
gs_pullback_confirm: 11
```

New replacement example from the twenty-ninth batch:

```text
002046 activity_breakout score_delta=4.92 validation_delta=2.13 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 393
missing_baseline_result / missing_optuna_result = 152
ok / missing_optuna_result = 8
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=2900 candidate_count=141 rejected_count=2759
/api/parameter-search local_optuna.batch.merge_plan replacement_count=141 replacement_schema_rows=141 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 580 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Thirtieth Real Local Optuna Batch

Scope:

- Offset 580, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 600 stocks and 3000 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 580 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=3000 new_rows=100 elapsed=19.9s
formula_local_optuna_batch:done rows=3000 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=3000 candidates=145 rejected=2855
formula_local_optuna_merge_plan: rows=3000 replacements=145
```

Cumulative candidate distribution:

```text
activity_breakout: 87
gs_raw_buy: 29
volume_base_breakout: 17
gs_pullback_confirm: 12
```

New replacement examples from the thirtieth batch:

```text
002061 activity_breakout score_delta=24.86 validation_delta=10.23 sell_rule=formula_exit_or_5
002072 gs_pullback_confirm score_delta=19.83 validation_delta=3.99 sell_rule=fixed_30
002057 gs_raw_buy score_delta=10.23 validation_delta=1.32 sell_rule=fixed_60
002055 gs_raw_buy score_delta=5.91 validation_delta=2.74 sell_rule=formula_exit_or_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 408
missing_baseline_result / missing_optuna_result = 156
ok / missing_optuna_result = 8
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=3000 candidate_count=145 rejected_count=2855
/api/parameter-search local_optuna.batch.merge_plan replacement_count=145 replacement_schema_rows=145 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 600 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Thirty-First Real Local Optuna Batch

Scope:

- Offset 600, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 620 stocks and 3100 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 600 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=3100 new_rows=100 elapsed=20.2s
formula_local_optuna_batch:done rows=3100 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=3100 candidates=151 rejected=2949
formula_local_optuna_merge_plan: rows=3100 replacements=151
```

Cumulative candidate distribution:

```text
activity_breakout: 91
gs_raw_buy: 29
volume_base_breakout: 19
gs_pullback_confirm: 12
```

New replacement examples from the thirty-first batch:

```text
002093 activity_breakout score_delta=18.08 validation_delta=26.90 sell_rule=fixed_60
002080 volume_base_breakout score_delta=17.53 validation_delta=5.58 sell_rule=fixed_60
002096 activity_breakout score_delta=14.11 validation_delta=14.96 sell_rule=fixed_60
002084 activity_breakout score_delta=13.63 validation_delta=2.15 sell_rule=formula_exit_or_10
002085 volume_base_breakout score_delta=11.82 validation_delta=51.26 sell_rule=formula_exit_or_30
002077 activity_breakout score_delta=7.10 validation_delta=34.82 sell_rule=formula_exit_or_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 422
missing_baseline_result / missing_optuna_result = 159
ok / missing_optuna_result = 8
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=3100 candidate_count=151 rejected_count=2949
/api/parameter-search local_optuna.batch.merge_plan replacement_count=151 replacement_schema_rows=151 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 620 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Thirty-Second Real Local Optuna Batch

Scope:

- Offset 620, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 640 stocks and 3200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 620 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=3200 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=3200 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=3200 candidates=155 rejected=3045
formula_local_optuna_merge_plan: rows=3200 replacements=155
```

Cumulative candidate distribution:

```text
activity_breakout: 94
gs_raw_buy: 29
volume_base_breakout: 19
gs_pullback_confirm: 13
```

New replacement examples from the thirty-second batch:

```text
002105 activity_breakout score_delta=14.30 validation_delta=37.74 sell_rule=fixed_10
002107 activity_breakout score_delta=10.87 validation_delta=6.78 sell_rule=fixed_20
002104 gs_pullback_confirm score_delta=9.16 validation_delta=1.91 sell_rule=fixed_15
002119 activity_breakout score_delta=4.95 validation_delta=2.10 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 432
missing_baseline_result / missing_optuna_result = 163
ok / missing_optuna_result = 8
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
optuna_investigation: entry signals produced no executable trades
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=3200 candidate_count=155 rejected_count=3045
/api/parameter-search local_optuna.batch.merge_plan replacement_count=155 replacement_schema_rows=155 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 640 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Thirty-Third Real Local Optuna Batch

Scope:

- Offset 640, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 660 stocks and 3300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 640 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=3300 new_rows=100 elapsed=20.7s
formula_local_optuna_batch:done rows=3300 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=3300 candidates=156 rejected=3144
formula_local_optuna_merge_plan: rows=3300 replacements=156
```

Cumulative candidate distribution:

```text
activity_breakout: 95
gs_raw_buy: 29
volume_base_breakout: 19
gs_pullback_confirm: 13
```

New replacement example from the thirty-third batch:

```text
002126 activity_breakout score_delta=4.38 validation_delta=5.00 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 442
missing_baseline_result / missing_optuna_result = 167
ok / missing_optuna_result = 8
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=3300 candidate_count=156 rejected_count=3144
/api/parameter-search local_optuna.batch.merge_plan replacement_count=156 replacement_schema_rows=156 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 660 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Thirty-Fourth Real Local Optuna Batch

Scope:

- Offset 660, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 680 stocks and 3400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 660 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=3400 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=3400 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=3400 candidates=160 rejected=3240
formula_local_optuna_merge_plan: rows=3400 replacements=160
```

Cumulative candidate distribution:

```text
activity_breakout: 97
gs_raw_buy: 29
volume_base_breakout: 21
gs_pullback_confirm: 13
```

New replacement examples from the thirty-fourth batch:

```text
002162 volume_base_breakout score_delta=29.74 validation_delta=7.80 sell_rule=fixed_60
002155 volume_base_breakout score_delta=12.17 validation_delta=0.49 sell_rule=fixed_60
002160 activity_breakout score_delta=8.69 validation_delta=2.72 sell_rule=fixed_60
002157 activity_breakout score_delta=3.87 validation_delta=12.64 sell_rule=formula_exit_or_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 450
missing_baseline_result / missing_optuna_result = 174
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=3400 candidate_count=160 rejected_count=3240
/api/parameter-search local_optuna.batch.merge_plan replacement_count=160 replacement_schema_rows=160 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 680 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Thirty-Fifth Real Local Optuna Batch

Scope:

- Offset 680, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 700 stocks and 3500 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 680 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=3500 new_rows=100 elapsed=20.7s
formula_local_optuna_batch:done rows=3500 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=3500 candidates=164 rejected=3336
formula_local_optuna_merge_plan: rows=3500 replacements=164
```

Cumulative candidate distribution:

```text
activity_breakout: 99
gs_raw_buy: 30
volume_base_breakout: 22
gs_pullback_confirm: 13
```

New replacement examples from the thirty-fifth batch:

```text
002179 volume_base_breakout score_delta=12.09 validation_delta=11.92 sell_rule=formula_exit_or_5
002170 gs_raw_buy score_delta=5.11 validation_delta=2.98 sell_rule=fixed_60
002170 activity_breakout score_delta=4.90 validation_delta=3.44 sell_rule=fixed_60
002173 activity_breakout score_delta=3.02 validation_delta=2.66 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 459
missing_baseline_result / missing_optuna_result = 177
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=3500 candidate_count=164 rejected_count=3336
/api/parameter-search local_optuna.batch.merge_plan replacement_count=164 replacement_schema_rows=164 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 700 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Thirty-Sixth Real Local Optuna Batch

Scope:

- Offset 700, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 720 stocks and 3600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 700 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=3600 new_rows=100 elapsed=20.8s
formula_local_optuna_batch:done rows=3600 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=3600 candidates=168 rejected=3432
formula_local_optuna_merge_plan: rows=3600 replacements=168
```

Cumulative candidate distribution:

```text
activity_breakout: 101
gs_raw_buy: 32
volume_base_breakout: 22
gs_pullback_confirm: 13
```

New replacement examples from the thirty-sixth batch:

```text
002185 activity_breakout score_delta=22.70 validation_delta=3.72 sell_rule=fixed_10
002195 activity_breakout score_delta=5.09 validation_delta=24.19 sell_rule=fixed_60
002191 gs_raw_buy score_delta=4.12 validation_delta=0.05 sell_rule=formula_exit_or_5
002193 gs_raw_buy score_delta=4.08 validation_delta=4.04 sell_rule=fixed_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 472
missing_baseline_result / missing_optuna_result = 177
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=3600 candidate_count=168 rejected_count=3432
/api/parameter-search local_optuna.batch.merge_plan replacement_count=168 replacement_schema_rows=168 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 720 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Thirty-Seventh Real Local Optuna Batch

Scope:

- Offset 720, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 740 stocks and 3700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 720 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=3700 new_rows=100 elapsed=20.7s
formula_local_optuna_batch:done rows=3700 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=3700 candidates=174 rejected=3526
formula_local_optuna_merge_plan: rows=3700 replacements=174
```

Cumulative candidate distribution:

```text
activity_breakout: 105
gs_raw_buy: 33
volume_base_breakout: 23
gs_pullback_confirm: 13
```

New replacement examples from the thirty-seventh batch:

```text
002211 activity_breakout score_delta=9.17 validation_delta=7.54 sell_rule=fixed_20
002126 activity_breakout score_delta=4.38 validation_delta=5.00 sell_rule=fixed_60
002157 activity_breakout score_delta=3.87 validation_delta=12.64 sell_rule=formula_exit_or_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 481
missing_baseline_result / missing_optuna_result = 182
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=3700 candidate_count=174 rejected_count=3526
/api/parameter-search local_optuna.batch.merge_plan replacement_count=174 replacement_schema_rows=174 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 740 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Thirty-Eighth Real Local Optuna Batch

Scope:

- Offset 740, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 760 stocks and 3800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 740 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=3800 new_rows=100 elapsed=20.2s
formula_local_optuna_batch:done rows=3800 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=3800 candidates=177 rejected=3623
formula_local_optuna_merge_plan: rows=3800 replacements=177
```

Cumulative candidate distribution:

```text
activity_breakout: 108
gs_raw_buy: 33
volume_base_breakout: 23
gs_pullback_confirm: 13
```

New replacement examples from the thirty-eighth batch:

```text
002236 activity_breakout score_delta=18.69 validation_delta=12.16 sell_rule=formula_exit_or_60
002226 activity_breakout score_delta=6.15 validation_delta=19.90 sell_rule=fixed_60
002244 activity_breakout score_delta=5.60 validation_delta=31.04 sell_rule=fixed_10
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 493
missing_baseline_result / missing_optuna_result = 185
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 687
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=3800 candidate_count=177 rejected_count=3623
/api/parameter-search local_optuna.batch.merge_plan replacement_count=177 replacement_schema_rows=177 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 760 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Thirty-Ninth Real Local Optuna Batch

Scope:

- Offset 760, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 780 stocks and 3900 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 760 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=3900 new_rows=100 elapsed=20.5s
formula_local_optuna_batch:done rows=3900 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=3900 candidates=178 rejected=3722
formula_local_optuna_merge_plan: rows=3900 replacements=178
```

Cumulative candidate distribution:

```text
activity_breakout: 108
gs_raw_buy: 33
volume_base_breakout: 23
gs_pullback_confirm: 14
```

New replacement example from the thirty-ninth batch:

```text
002256 gs_pullback_confirm score_delta=6.07 validation_delta=3.62 sell_rule=fixed_15
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 509
missing_baseline_result / missing_optuna_result = 189
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 707
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=3900 candidate_count=178 rejected_count=3722
/api/parameter-search local_optuna.batch.merge_plan replacement_count=178 replacement_schema_rows=178 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 780 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Fortieth Real Local Optuna Batch

Scope:

- Offset 780, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 800 stocks and 4000 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 780 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=4000 new_rows=100 elapsed=21.1s
formula_local_optuna_batch:done rows=4000 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=4000 candidates=185 rejected=3815
formula_local_optuna_merge_plan: rows=4000 replacements=185
```

Cumulative candidate distribution:

```text
activity_breakout: 113
gs_raw_buy: 35
volume_base_breakout: 23
gs_pullback_confirm: 14
```

New replacement examples from the fortieth batch:

```text
002268 activity_breakout score_delta=28.78 validation_delta=26.53 sell_rule=fixed_15
002274 activity_breakout score_delta=18.76 validation_delta=9.13 sell_rule=fixed_5
002284 activity_breakout score_delta=15.46 validation_delta=9.54 sell_rule=fixed_60
002273 activity_breakout score_delta=14.82 validation_delta=1.18 sell_rule=fixed_30
002274 gs_raw_buy score_delta=6.85 validation_delta=2.51 sell_rule=fixed_15
002271 gs_raw_buy score_delta=3.92 validation_delta=1.58 sell_rule=fixed_5
002281 activity_breakout score_delta=3.42 validation_delta=9.01 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 522
missing_baseline_result / missing_optuna_result = 195
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 726
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=4000 candidate_count=185 rejected_count=3815
/api/parameter-search local_optuna.batch.merge_plan replacement_count=185 replacement_schema_rows=185 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 800 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Forty-First Real Local Optuna Batch

Scope:

- Offset 800, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 820 stocks and 4100 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 800 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=4100 new_rows=100 elapsed=20.6s
formula_local_optuna_batch:done rows=4100 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=4100 candidates=189 rejected=3911
formula_local_optuna_merge_plan: rows=4100 replacements=189
```

Cumulative candidate distribution:

```text
activity_breakout: 115
gs_raw_buy: 37
volume_base_breakout: 23
gs_pullback_confirm: 14
```

New replacement examples from the forty-first batch:

```text
002303 activity_breakout score_delta=15.64 validation_delta=18.38 sell_rule=fixed_10
002303 gs_raw_buy score_delta=7.97 validation_delta=11.75 sell_rule=formula_exit_or_5
002307 gs_raw_buy score_delta=5.02 validation_delta=4.51 sell_rule=fixed_60
002290 activity_breakout score_delta=3.05 validation_delta=0.28 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 533
missing_baseline_result / missing_optuna_result = 198
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 740
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=4100 candidate_count=189 rejected_count=3911
/api/parameter-search local_optuna.batch.merge_plan replacement_count=189 replacement_schema_rows=189 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 820 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Forty-Second Real Local Optuna Batch

Scope:

- Offset 820, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 840 stocks and 4200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 820 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=4200 new_rows=100 elapsed=21.2s
formula_local_optuna_batch:done rows=4200 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=4200 candidates=193 rejected=4007
formula_local_optuna_merge_plan: rows=4200 replacements=193
```

Cumulative candidate distribution:

```text
activity_breakout: 116
gs_raw_buy: 38
volume_base_breakout: 24
gs_pullback_confirm: 15
```

New replacement examples from the forty-second batch:

```text
002320 gs_pullback_confirm score_delta=62.44 validation_delta=52.26 sell_rule=fixed_20
002328 volume_base_breakout score_delta=9.46 validation_delta=3.22 sell_rule=formula_exit_or_20
002324 gs_raw_buy score_delta=4.15 validation_delta=0.23 sell_rule=fixed_30
002310 activity_breakout score_delta=3.10 validation_delta=5.27 sell_rule=formula_exit_or_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 550
missing_baseline_result / missing_optuna_result = 201
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 760
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=4200 candidate_count=193 rejected_count=4007
/api/parameter-search local_optuna.batch.merge_plan replacement_count=193 replacement_schema_rows=193 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 840 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Forty-Third Real Local Optuna Batch

Scope:

- Offset 840, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 860 stocks and 4300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 840 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=4300 new_rows=100 elapsed=20.7s
formula_local_optuna_batch:done rows=4300 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=4300 candidates=195 rejected=4105
formula_local_optuna_merge_plan: rows=4300 replacements=195
```

Cumulative candidate distribution:

```text
activity_breakout: 118
gs_raw_buy: 38
volume_base_breakout: 24
gs_pullback_confirm: 15
```

New replacement examples from the forty-third batch:

```text
002339 activity_breakout score_delta=12.61 validation_delta=20.02 sell_rule=fixed_60
002335 activity_breakout score_delta=5.37 validation_delta=0.68 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 565
missing_baseline_result / missing_optuna_result = 202
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 776
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=4300 candidate_count=195 rejected_count=4105
/api/parameter-search local_optuna.batch.merge_plan replacement_count=195 replacement_schema_rows=195 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 860 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Forty-Fourth Real Local Optuna Batch

Scope:

- Offset 860, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 880 stocks and 4400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 860 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=4400 new_rows=100 elapsed=21.7s
formula_local_optuna_batch:done rows=4400 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=4400 candidates=200 rejected=4200
formula_local_optuna_merge_plan: rows=4400 replacements=200
```

Cumulative candidate distribution:

```text
activity_breakout: 121
gs_raw_buy: 39
volume_base_breakout: 25
gs_pullback_confirm: 15
```

New replacement examples from the forty-fourth batch:

```text
002367 volume_base_breakout score_delta=18.12 validation_delta=6.51 sell_rule=fixed_10
002363 activity_breakout score_delta=15.32 validation_delta=9.68 sell_rule=fixed_60
002361 activity_breakout score_delta=7.05 validation_delta=12.60 sell_rule=fixed_60
002360 gs_raw_buy score_delta=6.18 validation_delta=7.67 sell_rule=fixed_5
002358 activity_breakout score_delta=3.40 validation_delta=8.38 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 578
missing_baseline_result / missing_optuna_result = 202
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 789
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=4400 candidate_count=200 rejected_count=4200
/api/parameter-search local_optuna.batch.merge_plan replacement_count=200 replacement_schema_rows=200 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 880 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Forty-Fifth Real Local Optuna Batch

Scope:

- Offset 880, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 900 stocks and 4500 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 880 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=4500 new_rows=100 elapsed=20.3s
formula_local_optuna_batch:done rows=4500 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=4500 candidates=204 rejected=4296
formula_local_optuna_merge_plan: rows=4500 replacements=204
```

Cumulative candidate distribution:

```text
activity_breakout: 124
gs_raw_buy: 39
volume_base_breakout: 26
gs_pullback_confirm: 15
```

New replacement examples from the forty-fifth batch:

```text
002393 volume_base_breakout score_delta=18.17 validation_delta=30.71 sell_rule=fixed_5
002380 activity_breakout score_delta=7.94 validation_delta=7.69 sell_rule=fixed_60
002383 activity_breakout score_delta=7.25 validation_delta=7.91 sell_rule=fixed_30
002375 activity_breakout score_delta=4.15 validation_delta=7.23 sell_rule=formula_exit_or_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 588
missing_baseline_result / missing_optuna_result = 209
ok / missing_optuna_result = 9
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 806
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=4500 candidate_count=204 rejected_count=4296
/api/parameter-search local_optuna.batch.merge_plan replacement_count=204 replacement_schema_rows=204 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 900 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Forty-Sixth Real Local Optuna Batch

Scope:

- Offset 900, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 920 stocks and 4600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 900 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=4600 new_rows=100 elapsed=20.2s
formula_local_optuna_batch:done rows=4600 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=4600 candidates=206 rejected=4394
formula_local_optuna_merge_plan: rows=4600 replacements=206
```

Cumulative candidate distribution:

```text
activity_breakout: 125
gs_raw_buy: 40
volume_base_breakout: 26
gs_pullback_confirm: 15
```

New replacement examples from the forty-sixth batch:

```text
002408 gs_raw_buy score_delta=10.65 validation_delta=4.96 sell_rule=fixed_20
002395 activity_breakout score_delta=5.67 validation_delta=1.49 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 604
missing_baseline_result / missing_optuna_result = 211
ok / missing_optuna_result = 10
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 825
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=4600 candidate_count=206 rejected_count=4394
/api/parameter-search local_optuna.batch.merge_plan replacement_count=206 replacement_schema_rows=206 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 920 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Forty-Seventh Real Local Optuna Batch

Scope:

- Offset 920, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 940 stocks and 4700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 920 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=4700 new_rows=100 elapsed=21.3s
formula_local_optuna_batch:done rows=4700 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=4700 candidates=209 rejected=4491
formula_local_optuna_merge_plan: rows=4700 replacements=209
```

Cumulative candidate distribution:

```text
activity_breakout: 128
gs_raw_buy: 40
volume_base_breakout: 26
gs_pullback_confirm: 15
```

New replacement examples from the forty-seventh batch:

```text
002424 activity_breakout score_delta=23.78 validation_delta=29.46 sell_rule=fixed_30
002419 activity_breakout score_delta=17.58 validation_delta=18.10 sell_rule=fixed_5
002415 activity_breakout score_delta=12.57 validation_delta=40.94 sell_rule=formula_exit_or_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 618
missing_baseline_result / missing_optuna_result = 213
ok / missing_optuna_result = 10
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 841
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=4700 candidate_count=209 rejected_count=4491
/api/parameter-search local_optuna.batch.merge_plan replacement_count=209 replacement_schema_rows=209 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 940 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Forty-Eighth Real Local Optuna Batch

Scope:

- Offset 940, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 960 stocks and 4800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 940 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=4800 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=4800 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=4800 candidates=214 rejected=4586
formula_local_optuna_merge_plan: rows=4800 replacements=214
```

Cumulative candidate distribution:

```text
activity_breakout: 131
gs_raw_buy: 40
volume_base_breakout: 27
gs_pullback_confirm: 16
```

New replacement examples from the forty-eighth batch:

```text
002453 activity_breakout score_delta=31.97 validation_delta=29.10 sell_rule=fixed_5
002446 gs_pullback_confirm score_delta=25.90 validation_delta=0.00 sell_rule=fixed_60
002446 volume_base_breakout score_delta=11.16 validation_delta=14.62 sell_rule=fixed_30
002455 activity_breakout score_delta=7.23 validation_delta=9.44 sell_rule=formula_exit_or_20
002438 activity_breakout score_delta=6.61 validation_delta=3.36 sell_rule=formula_exit_or_10
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 630
missing_baseline_result / missing_optuna_result = 217
ok / missing_optuna_result = 10
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 857
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=4800 candidate_count=214 rejected_count=4586
/api/parameter-search local_optuna.batch.merge_plan replacement_count=214 replacement_schema_rows=214 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 960 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Forty-Ninth Real Local Optuna Batch

Scope:

- Offset 960, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 980 stocks and 4900 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 960 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=4900 new_rows=100 elapsed=20.7s
formula_local_optuna_batch:done rows=4900 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=4900 candidates=216 rejected=4684
formula_local_optuna_merge_plan: rows=4900 replacements=216
```

Cumulative candidate distribution:

```text
activity_breakout: 132
gs_raw_buy: 41
volume_base_breakout: 27
gs_pullback_confirm: 16
```

New replacement examples from the forty-ninth batch:

```text
002480 activity_breakout score_delta=8.68 validation_delta=2.45 sell_rule=fixed_60
002462 gs_raw_buy score_delta=4.80 validation_delta=1.58 sell_rule=fixed_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 637
missing_baseline_result / missing_optuna_result = 223
ok / missing_optuna_result = 10
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 870
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=4900 candidate_count=216 rejected_count=4684
/api/parameter-search local_optuna.batch.merge_plan replacement_count=216 replacement_schema_rows=216 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 980 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Fiftieth Real Local Optuna Batch

Scope:

- Offset 980, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1000 stocks and 5000 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 980 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=5000 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=5000 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=5000 candidates=222 rejected=4778
formula_local_optuna_merge_plan: rows=5000 replacements=222
```

Cumulative candidate distribution:

```text
activity_breakout: 135
gs_raw_buy: 41
volume_base_breakout: 27
gs_pullback_confirm: 19
```

New replacement examples from the fiftieth batch:

```text
002485 gs_pullback_confirm score_delta=32.87 validation_delta=83.65 sell_rule=fixed_30
002494 gs_pullback_confirm score_delta=13.99 validation_delta=17.44 sell_rule=fixed_60
002494 activity_breakout score_delta=12.14 validation_delta=13.65 sell_rule=formula_exit_or_15
002492 gs_pullback_confirm score_delta=9.82 validation_delta=0.86 sell_rule=fixed_60
002496 activity_breakout score_delta=8.10 validation_delta=3.73 sell_rule=formula_exit_or_20
002491 activity_breakout score_delta=3.15 validation_delta=1.80 sell_rule=fixed_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 653
missing_baseline_result / missing_optuna_result = 226
ok / missing_optuna_result = 10
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 889
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=5000 candidate_count=222 rejected_count=4778
/api/parameter-search local_optuna.batch.merge_plan replacement_count=222 replacement_schema_rows=222 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1000 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Fifty-First Real Local Optuna Batch

Scope:

- Offset 1000, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1020 stocks and 5100 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1000 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=5100 new_rows=100 elapsed=21.0s
formula_local_optuna_batch:done rows=5100 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=5100 candidates=228 rejected=4872
formula_local_optuna_merge_plan: rows=5100 replacements=228
```

Cumulative candidate distribution:

```text
activity_breakout: 140
gs_raw_buy: 41
volume_base_breakout: 27
gs_pullback_confirm: 20
```

New replacement examples from the fifty-first batch:

```text
002515 gs_pullback_confirm score_delta=45.15 validation_delta=65.22 sell_rule=formula_exit_or_10
002508 activity_breakout score_delta=25.46 validation_delta=23.55 sell_rule=fixed_10
002513 activity_breakout score_delta=13.85 validation_delta=3.39 sell_rule=fixed_60
002523 activity_breakout score_delta=13.39 validation_delta=20.45 sell_rule=fixed_30
002528 activity_breakout score_delta=9.22 validation_delta=2.56 sell_rule=fixed_30
002517 activity_breakout score_delta=9.10 validation_delta=0.05 sell_rule=fixed_30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 665
missing_baseline_result / missing_optuna_result = 234
ok / missing_optuna_result = 10
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 909
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228
/api/parameter-search local_optuna.batch row_count=5100 candidate_count=228 rejected_count=4872
/api/parameter-search local_optuna.batch.merge_plan replacement_count=228 replacement_schema_rows=228 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1020 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and must be investigated through `baseline_investigation` / `optuna_investigation`.

## 2026-05-20 Follow-up: Fifty-Second Real Local Optuna Batch

Scope:

- Offset 1020, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1040 stocks and 5200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1020 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=5200 new_rows=100 elapsed=21.2s
formula_local_optuna_batch:done rows=5200 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=5200 candidates=234 rejected=4966
formula_local_optuna_merge_plan: rows=5200 replacements=234
```

Cumulative candidate distribution:

```text
activity_breakout: 144
gs_raw_buy: 42
volume_base_breakout: 27
gs_pullback_confirm: 21
```

New replacement examples from the fifty-second batch:

```text
002531 activity_breakout score_delta=22.27 validation_delta=8.08 sell_rule=fixed_10
002535 activity_breakout score_delta=14.99 validation_delta=34.38 sell_rule=fixed_60
002541 activity_breakout score_delta=12.54 validation_delta=2.67 sell_rule=fixed_20
002546 gs_pullback_confirm score_delta=6.89 validation_delta=0.00 sell_rule=fixed_60
002540 gs_raw_buy score_delta=6.16 validation_delta=7.54 sell_rule=formula_exit_or_30
002536 activity_breakout score_delta=4.12 validation_delta=5.70 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 680
missing_baseline_result / missing_optuna_result = 235
ok / missing_optuna_result = 10
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 925
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=5200 candidate_count=234 rejected_count=4966
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=915 missing_optuna_result=245
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1160
/api/parameter-search local_optuna.batch.merge_plan replacement_count=234 replacement_schema_rows=234 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1040 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and are now exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Fifty-Third Real Local Optuna Batch

Scope:

- Offset 1040, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1060 stocks and 5300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1040 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=5300 new_rows=100 elapsed=20.3s
formula_local_optuna_batch:done rows=5300 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=5300 candidates=238 rejected=5062
formula_local_optuna_merge_plan: rows=5300 replacements=238
```

Cumulative candidate distribution:

```text
activity_breakout: 145
gs_raw_buy: 42
volume_base_breakout: 27
gs_pullback_confirm: 24
```

New replacement examples from the fifty-third batch:

```text
002558 gs_pullback_confirm score_delta=40.87 validation_delta=14.51 sell_rule=fixed_60
002553 gs_pullback_confirm score_delta=23.97 validation_delta=102.11 sell_rule=fixed_60
002561 activity_breakout score_delta=16.37 validation_delta=20.48 sell_rule=formula_exit_or_10
002569 gs_pullback_confirm score_delta=4.54 validation_delta=4.11 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 691
missing_baseline_result / missing_optuna_result = 238
ok / missing_optuna_result = 11
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 940
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=5300 candidate_count=238 rejected_count=5062
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=929 missing_optuna_result=249
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1178
/api/parameter-search local_optuna.batch.merge_plan replacement_count=238 replacement_schema_rows=238 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1060 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Fifty-Fourth Real Local Optuna Batch

Scope:

- Offset 1060, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1080 stocks and 5400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1060 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=5400 new_rows=100 elapsed=21.4s
formula_local_optuna_batch:done rows=5400 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=5400 candidates=242 rejected=5158
formula_local_optuna_merge_plan: rows=5400 replacements=242
```

Cumulative candidate distribution:

```text
activity_breakout: 148
gs_raw_buy: 43
volume_base_breakout: 27
gs_pullback_confirm: 24
```

New replacement examples from the fifty-fourth batch:

```text
002589 activity_breakout score_delta=23.73 validation_delta=21.09 sell_rule=formula_exit_or_5
002578 activity_breakout score_delta=20.12 validation_delta=13.20 sell_rule=fixed_5
002573 gs_raw_buy score_delta=8.46 validation_delta=18.06 sell_rule=fixed_15
002575 activity_breakout score_delta=4.08 validation_delta=13.74 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 700
missing_baseline_result / missing_optuna_result = 244
ok / missing_optuna_result = 13
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 957
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=5400 candidate_count=242 rejected_count=5158
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=944 missing_optuna_result=257
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1201
/api/parameter-search local_optuna.batch.merge_plan replacement_count=242 replacement_schema_rows=242 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1080 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Fifty-Fifth Real Local Optuna Batch

Scope:

- Offset 1080, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1100 stocks and 5500 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1080 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=5500 new_rows=100 elapsed=21.4s
formula_local_optuna_batch:done rows=5500 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=5500 candidates=248 rejected=5252
formula_local_optuna_merge_plan: rows=5500 replacements=248
```

Cumulative candidate distribution:

```text
activity_breakout: 153
gs_raw_buy: 44
volume_base_breakout: 27
gs_pullback_confirm: 24
```

New replacement examples from the fifty-fifth batch:

```text
002593 activity_breakout score_delta=13.49 validation_delta=19.38 sell_rule=formula_exit_or_10
002600 activity_breakout score_delta=10.28 validation_delta=6.55 sell_rule=fixed_10
002609 activity_breakout score_delta=9.73 validation_delta=1.86 sell_rule=fixed_10
002594 activity_breakout score_delta=8.90 validation_delta=15.79 sell_rule=fixed_5
002592 activity_breakout score_delta=5.97 validation_delta=18.64 sell_rule=formula_exit_or_10
002595 gs_raw_buy score_delta=4.19 validation_delta=5.70 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 710
missing_baseline_result / missing_optuna_result = 247
ok / missing_optuna_result = 13
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 970
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=5500 candidate_count=248 rejected_count=5252
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=957 missing_optuna_result=260
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1217
/api/parameter-search local_optuna.batch.merge_plan replacement_count=248 replacement_schema_rows=248 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1100 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Fifty-Sixth Real Local Optuna Batch

Scope:

- Offset 1100, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1120 stocks and 5600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1100 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=5600 new_rows=100 elapsed=20.8s
formula_local_optuna_batch:done rows=5600 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=5600 candidates=257 rejected=5343
formula_local_optuna_merge_plan: rows=5600 replacements=257
```

Cumulative candidate distribution:

```text
activity_breakout: 157
gs_raw_buy: 45
volume_base_breakout: 30
gs_pullback_confirm: 25
```

New replacement examples from the fifty-sixth batch:

```text
002616 volume_base_breakout score_delta=28.27 validation_delta=44.19 sell_rule=fixed_15
002615 activity_breakout score_delta=19.66 validation_delta=23.25 sell_rule=fixed_60
002629 gs_pullback_confirm score_delta=17.50 validation_delta=67.56 sell_rule=fixed_60
002632 activity_breakout score_delta=17.28 validation_delta=4.22 sell_rule=fixed_60
002620 activity_breakout score_delta=17.24 validation_delta=10.90 sell_rule=fixed_60
002623 activity_breakout score_delta=14.99 validation_delta=12.35 sell_rule=formula_exit_or_20
002633 volume_base_breakout score_delta=7.93 validation_delta=0.83 sell_rule=formula_exit_or_20
002632 volume_base_breakout score_delta=4.20 validation_delta=20.10 sell_rule=fixed_5
002628 gs_raw_buy score_delta=3.21 validation_delta=5.01 sell_rule=fixed_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 723
missing_baseline_result / missing_optuna_result = 254
ok / missing_optuna_result = 13
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 990
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=5600 candidate_count=257 rejected_count=5343
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=977 missing_optuna_result=267
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1244
/api/parameter-search local_optuna.batch.merge_plan replacement_count=257 replacement_schema_rows=257 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1120 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Fifty-Seventh Real Local Optuna Batch

Scope:

- Offset 1120, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1140 stocks and 5700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1120 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=5700 new_rows=100 elapsed=21.2s
formula_local_optuna_batch:done rows=5700 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=5700 candidates=261 rejected=5439
formula_local_optuna_merge_plan: rows=5700 replacements=261
```

Cumulative candidate distribution:

```text
activity_breakout: 159
gs_raw_buy: 47
volume_base_breakout: 30
gs_pullback_confirm: 25
```

New replacement examples from the fifty-seventh batch:

```text
002641 activity_breakout score_delta=26.45 validation_delta=14.86 sell_rule=formula_exit_or_5
002637 activity_breakout score_delta=13.32 validation_delta=5.85 sell_rule=formula_exit_or_10
002637 gs_raw_buy score_delta=7.85 validation_delta=12.19 sell_rule=fixed_10
002642 gs_raw_buy score_delta=7.83 validation_delta=7.43 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 732
missing_baseline_result / missing_optuna_result = 260
ok / missing_optuna_result = 13
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1005
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=5700 candidate_count=261 rejected_count=5439
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=992 missing_optuna_result=273
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1265
/api/parameter-search local_optuna.batch.merge_plan replacement_count=261 replacement_schema_rows=261 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1140 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Fifty-Eighth Real Local Optuna Batch

Scope:

- Offset 1140, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1160 stocks and 5800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1140 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=5800 new_rows=100 elapsed=21.9s
formula_local_optuna_batch:done rows=5800 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=5800 candidates=266 rejected=5534
formula_local_optuna_merge_plan: rows=5800 replacements=266
```

Cumulative candidate distribution:

```text
activity_breakout: 163
gs_raw_buy: 47
volume_base_breakout: 30
gs_pullback_confirm: 26
```

New replacement examples from the fifty-eighth batch:

```text
002662 activity_breakout score_delta=34.45 validation_delta=1.46 sell_rule=fixed_5
002660 activity_breakout score_delta=16.25 validation_delta=18.08 sell_rule=fixed_60
002672 gs_pullback_confirm score_delta=11.57 validation_delta=4.30 sell_rule=fixed_60
002664 activity_breakout score_delta=9.24 validation_delta=0.54 sell_rule=fixed_20
002670 activity_breakout score_delta=8.70 validation_delta=5.05 sell_rule=fixed_30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 745
missing_baseline_result / missing_optuna_result = 261
ok / missing_optuna_result = 13
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1019
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=5800 candidate_count=266 rejected_count=5534
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1006 missing_optuna_result=274
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1280
/api/parameter-search local_optuna.batch.merge_plan replacement_count=266 replacement_schema_rows=266 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1160 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Fifty-Ninth Real Local Optuna Batch

Scope:

- Offset 1160, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1180 stocks and 5900 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1160 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=5900 new_rows=100 elapsed=21.0s
formula_local_optuna_batch:done rows=5900 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=5900 candidates=271 rejected=5629
formula_local_optuna_merge_plan: rows=5900 replacements=271
```

Cumulative candidate distribution:

```text
activity_breakout: 165
gs_raw_buy: 49
volume_base_breakout: 31
gs_pullback_confirm: 26
```

New replacement examples from the fifty-ninth batch:

```text
002678 activity_breakout score_delta=37.29 validation_delta=4.94 sell_rule=fixed_20
002695 activity_breakout score_delta=15.59 validation_delta=25.94 sell_rule=fixed_60
002696 gs_raw_buy score_delta=6.30 validation_delta=3.44 sell_rule=fixed_60
002679 volume_base_breakout score_delta=5.26 validation_delta=7.72 sell_rule=fixed_60
002689 gs_raw_buy score_delta=4.69 validation_delta=17.06 sell_rule=fixed_30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 757
missing_baseline_result / missing_optuna_result = 263
ok / missing_optuna_result = 13
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1033
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=5900 candidate_count=271 rejected_count=5629
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1020 missing_optuna_result=276
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1296
/api/parameter-search local_optuna.batch.merge_plan replacement_count=271 replacement_schema_rows=271 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1180 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Sixtieth Real Local Optuna Batch

Scope:

- Offset 1180, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1200 stocks and 6000 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1180 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=6000 new_rows=100 elapsed=21.1s
formula_local_optuna_batch:done rows=6000 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=6000 candidates=276 rejected=5724
formula_local_optuna_merge_plan: rows=6000 replacements=276
```

Cumulative candidate distribution:

```text
activity_breakout: 169
gs_raw_buy: 49
volume_base_breakout: 31
gs_pullback_confirm: 27
```

New replacement examples from the sixtieth batch:

```text
002707 activity_breakout score_delta=16.40 validation_delta=2.86 sell_rule=fixed_60
002707 gs_pullback_confirm score_delta=15.14 validation_delta=52.92 sell_rule=fixed_15
002719 activity_breakout score_delta=14.06 validation_delta=19.39 sell_rule=formula_exit_or_5
002715 activity_breakout score_delta=8.56 validation_delta=26.43 sell_rule=fixed_60
002718 activity_breakout score_delta=4.52 validation_delta=0.77 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 768
missing_baseline_result / missing_optuna_result = 266
ok / missing_optuna_result = 13
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1047
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=6000 candidate_count=276 rejected_count=5724
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1034 missing_optuna_result=279
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1313
/api/parameter-search local_optuna.batch.merge_plan replacement_count=276 replacement_schema_rows=276 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1200 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Sixty-First Real Local Optuna Batch

Scope:

- Offset 1200, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1220 stocks and 6100 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1200 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=6100 new_rows=100 elapsed=20.5s
formula_local_optuna_batch:done rows=6100 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=6100 candidates=280 rejected=5820
formula_local_optuna_merge_plan: rows=6100 replacements=280
```

Cumulative candidate distribution:

```text
activity_breakout: 172
gs_raw_buy: 49
volume_base_breakout: 31
gs_pullback_confirm: 28
```

New replacement examples from the sixty-first batch:

```text
002725 activity_breakout score_delta=24.39 validation_delta=23.06 sell_rule=fixed_60
002729 activity_breakout score_delta=11.89 validation_delta=1.66 sell_rule=formula_exit_or_5
002742 activity_breakout score_delta=10.85 validation_delta=9.72 sell_rule=formula_exit_or_10
002738 gs_pullback_confirm score_delta=4.33 validation_delta=4.12 sell_rule=fixed_10
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 781
missing_baseline_result / missing_optuna_result = 268
ok / missing_optuna_result = 14
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1063
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=6100 candidate_count=280 rejected_count=5820
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1049 missing_optuna_result=282
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1331
/api/parameter-search local_optuna.batch.merge_plan replacement_count=280 replacement_schema_rows=280 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1220 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Sixty-Second Real Local Optuna Batch

Scope:

- Offset 1220, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1240 stocks and 6200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1220 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=6200 new_rows=100 elapsed=20.6s
formula_local_optuna_batch:done rows=6200 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=6200 candidates=284 rejected=5916
formula_local_optuna_merge_plan: rows=6200 replacements=284
```

Cumulative candidate distribution:

```text
activity_breakout: 174
gs_raw_buy: 49
volume_base_breakout: 33
gs_pullback_confirm: 28
```

New replacement examples from the sixty-second batch:

```text
002747 volume_base_breakout score_delta=20.42 validation_delta=8.09 sell_rule=fixed_10
002766 activity_breakout score_delta=12.30 validation_delta=0.44 sell_rule=fixed_10
002745 activity_breakout score_delta=11.90 validation_delta=12.77 sell_rule=formula_exit_or_5
002746 volume_base_breakout score_delta=7.32 validation_delta=5.45 sell_rule=formula_exit_or_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 795
missing_baseline_result / missing_optuna_result = 271
ok / missing_optuna_result = 15
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
optuna_investigation: entry signals produced no executable trades
missing_investigation_ok: 1081
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=6200 candidate_count=284 rejected_count=5916
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1066 missing_optuna_result=286
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1352
/api/parameter-search local_optuna.batch.merge_plan replacement_count=284 replacement_schema_rows=284 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1240 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Sixty-Third Real Local Optuna Batch

Scope:

- Offset 1240, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1260 stocks and 6300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1240 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=6300 new_rows=100 elapsed=21.4s
formula_local_optuna_batch:done rows=6300 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=6300 candidates=290 rejected=6010
formula_local_optuna_merge_plan: rows=6300 replacements=290
```

Cumulative candidate distribution:

```text
activity_breakout: 177
gs_raw_buy: 51
volume_base_breakout: 33
gs_pullback_confirm: 29
```

New replacement examples from the sixty-third batch:

```text
002778 activity_breakout score_delta=14.00 validation_delta=31.40 sell_rule=fixed_30
002777 activity_breakout score_delta=10.58 validation_delta=15.50 sell_rule=fixed_20
002772 activity_breakout score_delta=6.97 validation_delta=0.72 sell_rule=fixed_60
002769 gs_raw_buy score_delta=6.39 validation_delta=0.44 sell_rule=fixed_60
002778 gs_pullback_confirm score_delta=6.20 validation_delta=18.98 sell_rule=fixed_30
002786 gs_raw_buy score_delta=6.05 validation_delta=50.65 sell_rule=fixed_10
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 808
missing_baseline_result / missing_optuna_result = 274
ok / missing_optuna_result = 16
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1098
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=6300 candidate_count=290 rejected_count=6010
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1082 missing_optuna_result=290
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1372
/api/parameter-search local_optuna.batch.merge_plan replacement_count=290 replacement_schema_rows=290 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1260 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Sixty-Fourth Real Local Optuna Batch

Scope:

- Offset 1260, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1280 stocks and 6400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1260 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=6400 new_rows=100 elapsed=21.5s
formula_local_optuna_batch:done rows=6400 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=6400 candidates=296 rejected=6104
formula_local_optuna_merge_plan: rows=6400 replacements=296
```

Cumulative candidate distribution:

```text
activity_breakout: 181
gs_raw_buy: 52
volume_base_breakout: 34
gs_pullback_confirm: 29
```

New replacement examples from the sixty-fourth batch:

```text
002805 activity_breakout score_delta=25.96 validation_delta=15.94 sell_rule=fixed_15
002793 activity_breakout score_delta=24.32 validation_delta=8.07 sell_rule=fixed_5
002806 volume_base_breakout score_delta=22.63 validation_delta=4.97 sell_rule=fixed_10
002799 activity_breakout score_delta=12.82 validation_delta=16.75 sell_rule=fixed_5
002815 activity_breakout score_delta=11.87 validation_delta=3.53 sell_rule=fixed_5
002806 gs_raw_buy score_delta=7.58 validation_delta=2.46 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 821
missing_baseline_result / missing_optuna_result = 277
ok / missing_optuna_result = 17
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1115
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=6400 candidate_count=296 rejected_count=6104
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1098 missing_optuna_result=294
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1392
/api/parameter-search local_optuna.batch.merge_plan replacement_count=296 replacement_schema_rows=296 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1280 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Sixty-Fifth Real Local Optuna Batch

Scope:

- Offset 1280, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1300 stocks and 6500 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1280 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=6500 new_rows=100 elapsed=21.1s
formula_local_optuna_batch:done rows=6500 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=6500 candidates=302 rejected=6198
formula_local_optuna_merge_plan: rows=6500 replacements=302
```

Cumulative candidate distribution:

```text
activity_breakout: 185
gs_raw_buy: 52
volume_base_breakout: 34
gs_pullback_confirm: 31
```

New replacement examples from the sixty-fifth batch:

```text
002827 gs_pullback_confirm score_delta=23.64 validation_delta=18.36 sell_rule=fixed_20
002820 activity_breakout score_delta=19.62 validation_delta=3.74 sell_rule=formula_exit_or_10
002828 activity_breakout score_delta=18.44 validation_delta=11.61 sell_rule=fixed_60
002817 activity_breakout score_delta=10.52 validation_delta=19.76 sell_rule=formula_exit_or_10
002816 gs_pullback_confirm score_delta=6.07 validation_delta=7.98 sell_rule=formula_exit_or_10
002827 activity_breakout score_delta=3.89 validation_delta=1.36 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 836
missing_baseline_result / missing_optuna_result = 279
ok / missing_optuna_result = 17
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1132
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=6500 candidate_count=302 rejected_count=6198
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1115 missing_optuna_result=296
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1411
/api/parameter-search local_optuna.batch.merge_plan replacement_count=302 replacement_schema_rows=302 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1300 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Sixty-Sixth Real Local Optuna Batch

Scope:

- Offset 1300, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1320 stocks and 6600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1300 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=6600 new_rows=100 elapsed=20.8s
formula_local_optuna_batch:done rows=6600 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=6600 candidates=309 rejected=6291
formula_local_optuna_merge_plan: rows=6600 replacements=309
```

Cumulative candidate distribution:

```text
activity_breakout: 187
gs_raw_buy: 53
volume_base_breakout: 36
gs_pullback_confirm: 33
```

New replacement examples from the sixty-sixth batch:

```text
002849 activity_breakout score_delta=28.33 validation_delta=36.09 sell_rule=fixed_15
002848 volume_base_breakout score_delta=20.84 validation_delta=31.05 sell_rule=fixed_20
002847 activity_breakout score_delta=17.13 validation_delta=14.37 sell_rule=fixed_15
002839 gs_raw_buy score_delta=9.97 validation_delta=8.75 sell_rule=formula_exit_or_5
002856 volume_base_breakout score_delta=8.11 validation_delta=10.03 sell_rule=formula_exit_or_60
002842 gs_pullback_confirm score_delta=6.67 validation_delta=4.11 sell_rule=fixed_60
002852 gs_pullback_confirm score_delta=6.35 validation_delta=5.58 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 853
missing_baseline_result / missing_optuna_result = 281
ok / missing_optuna_result = 17
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1151
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=6600 candidate_count=309 rejected_count=6291
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1134 missing_optuna_result=298
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1432
/api/parameter-search local_optuna.batch.merge_plan replacement_count=309 replacement_schema_rows=309 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1320 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Sixty-Seventh Real Local Optuna Batch

Scope:

- Offset 1320, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1340 stocks and 6700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1320 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=6700 new_rows=100 elapsed=21.6s
formula_local_optuna_batch:done rows=6700 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=6700 candidates=315 rejected=6385
formula_local_optuna_merge_plan: rows=6700 replacements=315
```

Cumulative candidate distribution:

```text
activity_breakout: 189
gs_raw_buy: 54
volume_base_breakout: 38
gs_pullback_confirm: 34
```

New replacement examples from the sixty-seventh batch:

```text
002876 activity_breakout score_delta=19.27 validation_delta=17.99 sell_rule=fixed_15
002866 volume_base_breakout score_delta=19.09 validation_delta=22.88 sell_rule=fixed_5
002868 gs_pullback_confirm score_delta=9.56 validation_delta=71.51 sell_rule=fixed_60
002868 volume_base_breakout score_delta=8.60 validation_delta=2.17 sell_rule=fixed_60
002866 activity_breakout score_delta=4.70 validation_delta=1.53 sell_rule=fixed_10
002877 gs_raw_buy score_delta=3.21 validation_delta=12.61 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 867
missing_baseline_result / missing_optuna_result = 284
ok / missing_optuna_result = 18
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1169
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=6700 candidate_count=315 rejected_count=6385
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1151 missing_optuna_result=302
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1453
/api/parameter-search local_optuna.batch.merge_plan replacement_count=315 replacement_schema_rows=315 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1340 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Sixty-Eighth Real Local Optuna Batch

Scope:

- Offset 1340, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1360 stocks and 6800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1340 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=6800 new_rows=100 elapsed=21.3s
formula_local_optuna_batch:done rows=6800 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=6800 candidates=321 rejected=6479
formula_local_optuna_merge_plan: rows=6800 replacements=321
```

Cumulative candidate distribution:

```text
activity_breakout: 192
gs_raw_buy: 55
volume_base_breakout: 38
gs_pullback_confirm: 36
```

New replacement examples from the sixty-eighth batch:

```text
002887 activity_breakout score_delta=40.40 validation_delta=28.25 sell_rule=fixed_5
002886 gs_pullback_confirm score_delta=28.38 validation_delta=4.06 sell_rule=fixed_60
002893 activity_breakout score_delta=20.11 validation_delta=17.54 sell_rule=fixed_10
002886 activity_breakout score_delta=16.33 validation_delta=2.28 sell_rule=fixed_5
002880 gs_pullback_confirm score_delta=12.67 validation_delta=1.14 sell_rule=formula_exit_or_10
002890 gs_raw_buy score_delta=6.12 validation_delta=0.00 sell_rule=fixed_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 879
missing_baseline_result / missing_optuna_result = 287
ok / missing_optuna_result = 18
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1184
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=6800 candidate_count=321 rejected_count=6479
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1166 missing_optuna_result=305
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1471
/api/parameter-search local_optuna.batch.merge_plan replacement_count=321 replacement_schema_rows=321 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1360 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Sixty-Ninth Real Local Optuna Batch

Scope:

- Offset 1360, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1380 stocks and 6900 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1360 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=6900 new_rows=100 elapsed=21.5s
formula_local_optuna_batch:done rows=6900 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=6900 candidates=326 rejected=6574
formula_local_optuna_merge_plan: rows=6900 replacements=326
```

Cumulative candidate distribution:

```text
activity_breakout: 196
gs_raw_buy: 56
volume_base_breakout: 38
gs_pullback_confirm: 36
```

New replacement examples from the sixty-ninth batch:

```text
002903 activity_breakout score_delta=14.43 validation_delta=4.10 sell_rule=fixed_60
002901 gs_raw_buy score_delta=9.43 validation_delta=2.48 sell_rule=fixed_60
002905 activity_breakout score_delta=9.30 validation_delta=4.30 sell_rule=fixed_60
002906 activity_breakout score_delta=5.40 validation_delta=3.82 sell_rule=formula_exit_or_30
002909 activity_breakout score_delta=5.19 validation_delta=14.01 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 893
missing_baseline_result / missing_optuna_result = 290
ok / missing_optuna_result = 18
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1201
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=6900 candidate_count=326 rejected_count=6574
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1183 missing_optuna_result=308
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1491
/api/parameter-search local_optuna.batch.merge_plan replacement_count=326 replacement_schema_rows=326 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1380 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Seventieth Real Local Optuna Batch

Scope:

- Offset 1380, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1400 stocks and 7000 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1380 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=7000 new_rows=100 elapsed=21.0s
formula_local_optuna_batch:done rows=7000 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=7000 candidates=329 rejected=6671
formula_local_optuna_merge_plan: rows=7000 replacements=329
```

Cumulative candidate distribution:

```text
activity_breakout: 198
gs_raw_buy: 57
volume_base_breakout: 38
gs_pullback_confirm: 36
```

New replacement examples from the seventieth batch:

```text
002927 activity_breakout score_delta=21.06 validation_delta=15.81 sell_rule=fixed_60
002932 activity_breakout score_delta=10.08 validation_delta=11.53 sell_rule=formula_exit_or_10
002941 gs_raw_buy score_delta=3.84 validation_delta=2.13 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 904
missing_baseline_result / missing_optuna_result = 290
ok / missing_optuna_result = 18
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
missing_investigation_ok: 1212
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=7000 candidate_count=329 rejected_count=6671
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1194 missing_optuna_result=308
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1502
/api/parameter-search local_optuna.batch.merge_plan replacement_count=329 replacement_schema_rows=329 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1400 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Seventy-First Real Local Optuna Batch

Scope:

- Offset 1400, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1420 stocks and 7100 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1400 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=7100 new_rows=100 elapsed=21.0s
formula_local_optuna_batch:done rows=7100 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=7100 candidates=335 rejected=6765
formula_local_optuna_merge_plan: rows=7100 replacements=335
```

Cumulative candidate distribution:

```text
activity_breakout: 199
gs_raw_buy: 60
volume_base_breakout: 39
gs_pullback_confirm: 37
```

New replacement examples from the seventy-first batch:

```text
002949 volume_base_breakout score_delta=20.64 validation_delta=43.93 sell_rule=fixed_5
002956 gs_pullback_confirm score_delta=14.34 validation_delta=60.43 sell_rule=fixed_60
002953 activity_breakout score_delta=12.04 validation_delta=2.51 sell_rule=fixed_5
002950 gs_raw_buy score_delta=9.26 validation_delta=5.39 sell_rule=fixed_20
002958 gs_raw_buy score_delta=5.42 validation_delta=9.70 sell_rule=formula_exit_or_5
002946 gs_raw_buy score_delta=5.20 validation_delta=5.93 sell_rule=fixed_30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 916
missing_baseline_result / missing_optuna_result = 291
ok / missing_optuna_result = 19
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1226
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=7100 candidate_count=335 rejected_count=6765
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1207 missing_optuna_result=310
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1517
/api/parameter-search local_optuna.batch.merge_plan replacement_count=335 replacement_schema_rows=335 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1420 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Seventy-Second Real Local Optuna Batch

Scope:

- Offset 1420, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1440 stocks and 7200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1420 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=7200 new_rows=100 elapsed=21.2s
formula_local_optuna_batch:done rows=7200 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=7200 candidates=339 rejected=6861
formula_local_optuna_merge_plan: rows=7200 replacements=339
```

Cumulative candidate distribution:

```text
activity_breakout: 201
gs_raw_buy: 61
volume_base_breakout: 40
gs_pullback_confirm: 37
```

New replacement examples from the seventy-second batch:

```text
002987 activity_breakout score_delta=11.95 validation_delta=8.96 sell_rule=fixed_5
002979 activity_breakout score_delta=9.10 validation_delta=0.43 sell_rule=fixed_15
002970 volume_base_breakout score_delta=8.00 validation_delta=4.85 sell_rule=fixed_60
002972 gs_raw_buy score_delta=7.64 validation_delta=4.29 sell_rule=fixed_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 925
missing_baseline_result / missing_optuna_result = 294
ok / missing_optuna_result = 19
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1238
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=7200 candidate_count=339 rejected_count=6861
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1219 missing_optuna_result=313
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1532
/api/parameter-search local_optuna.batch.merge_plan replacement_count=339 replacement_schema_rows=339 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1440 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Seventy-Third Real Local Optuna Batch

Scope:

- Offset 1440, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1460 stocks and 7300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1440 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=7300 new_rows=100 elapsed=21.5s
formula_local_optuna_batch:done rows=7300 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=7300 candidates=345 rejected=6955
formula_local_optuna_merge_plan: rows=7300 replacements=345
```

Cumulative candidate distribution:

```text
activity_breakout: 205
gs_raw_buy: 62
volume_base_breakout: 40
gs_pullback_confirm: 38
```

New replacement examples from the seventy-third batch:

```text
003003 activity_breakout score_delta=27.99 validation_delta=23.49 sell_rule=fixed_60
002998 gs_pullback_confirm score_delta=26.70 validation_delta=45.77 sell_rule=fixed_10
002995 activity_breakout score_delta=20.65 validation_delta=31.99 sell_rule=fixed_60
003008 gs_raw_buy score_delta=11.49 validation_delta=6.86 sell_rule=formula_exit_or_5
002989 activity_breakout score_delta=5.26 validation_delta=13.63 sell_rule=fixed_60
003002 activity_breakout score_delta=3.77 validation_delta=9.49 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 939
missing_baseline_result / missing_optuna_result = 297
ok / missing_optuna_result = 19
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1255
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=7300 candidate_count=345 rejected_count=6955
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1236 missing_optuna_result=316
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1552
/api/parameter-search local_optuna.batch.merge_plan replacement_count=345 replacement_schema_rows=345 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1460 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Seventy-Fourth Real Local Optuna Batch

Scope:

- Offset 1460, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1480 stocks and 7400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1460 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=7400 new_rows=100 elapsed=21.2s
formula_local_optuna_batch:done rows=7400 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=7400 candidates=352 rejected=7048
formula_local_optuna_merge_plan: rows=7400 replacements=352
```

Cumulative candidate distribution:

```text
activity_breakout: 208
gs_raw_buy: 66
volume_base_breakout: 40
gs_pullback_confirm: 38
```

New replacement examples from the seventy-fourth batch:

```text
003011 activity_breakout score_delta=43.92 validation_delta=25.67 sell_rule=fixed_15
003028 activity_breakout score_delta=21.70 validation_delta=10.67 sell_rule=fixed_10
003029 activity_breakout score_delta=20.31 validation_delta=21.95 sell_rule=fixed_15
003027 gs_raw_buy score_delta=7.95 validation_delta=0.66 sell_rule=fixed_60
003015 gs_raw_buy score_delta=7.72 validation_delta=15.89 sell_rule=fixed_60
003017 gs_raw_buy score_delta=7.10 validation_delta=16.98 sell_rule=fixed_20
003013 gs_raw_buy score_delta=3.48 validation_delta=3.66 sell_rule=fixed_10
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 951
missing_baseline_result / missing_optuna_result = 299
ok / missing_optuna_result = 20
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1270
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=7400 candidate_count=352 rejected_count=7048
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1250 missing_optuna_result=319
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1569
/api/parameter-search local_optuna.batch.merge_plan replacement_count=352 replacement_schema_rows=352 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1480 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Seventy-Fifth Real Local Optuna Batch

Scope:

- Offset 1480, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1500 stocks and 7500 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1480 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=7500 new_rows=100 elapsed=20.5s
formula_local_optuna_batch:done rows=7500 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=7500 candidates=359 rejected=7141
formula_local_optuna_merge_plan: rows=7500 replacements=359
```

Cumulative candidate distribution:

```text
activity_breakout: 211
gs_raw_buy: 68
gs_pullback_confirm: 40
volume_base_breakout: 40
```

New replacement examples from the seventy-fifth batch:

```text
300007 gs_pullback_confirm score_delta=49.58 validation_delta=40.97 sell_rule=formula_exit_or_30
003042 activity_breakout score_delta=31.54 validation_delta=47.34 sell_rule=fixed_15
003040 activity_breakout score_delta=29.93 validation_delta=35.07 sell_rule=formula_exit_or_5
003037 gs_pullback_confirm score_delta=25.89 validation_delta=14.65 sell_rule=fixed_30
300007 activity_breakout score_delta=8.24 validation_delta=2.41 sell_rule=fixed_5
300006 gs_raw_buy score_delta=7.55 validation_delta=6.84 sell_rule=fixed_60
003816 gs_raw_buy score_delta=7.52 validation_delta=3.12 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 964
missing_baseline_result / missing_optuna_result = 304
ok / missing_optuna_result = 20
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1288
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=7500 candidate_count=359 rejected_count=7141
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1268 missing_optuna_result=324
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1592
/api/parameter-search local_optuna.batch.merge_plan replacement_count=359 replacement_schema_rows=359 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1500 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Seventy-Sixth Real Local Optuna Batch

Scope:

- Offset 1500, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1520 stocks and 7600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1500 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=7600 new_rows=100 elapsed=21.7s
formula_local_optuna_batch:done rows=7600 new_rows=0 elapsed=0.1s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=7600 candidates=364 rejected=7236
formula_local_optuna_merge_plan: rows=7600 replacements=364
```

Cumulative candidate distribution:

```text
activity_breakout: 214
gs_raw_buy: 68
gs_pullback_confirm: 42
volume_base_breakout: 40
```

New replacement examples from the seventy-sixth batch:

```text
300021 activity_breakout score_delta=21.76 validation_delta=18.00 sell_rule=fixed_15
300022 gs_pullback_confirm score_delta=14.65 validation_delta=38.05 sell_rule=fixed_60
300026 activity_breakout score_delta=11.67 validation_delta=11.36 sell_rule=fixed_20
300018 gs_pullback_confirm score_delta=6.80 validation_delta=0.00 sell_rule=fixed_60
300017 activity_breakout score_delta=6.15 validation_delta=1.64 sell_rule=fixed_15
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 979
missing_baseline_result / missing_optuna_result = 309
ok / missing_optuna_result = 20
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1308
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=7600 candidate_count=364 rejected_count=7236
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1288 missing_optuna_result=329
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1617
/api/parameter-search local_optuna.batch.merge_plan replacement_count=364 replacement_schema_rows=364 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1520 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Seventy-Seventh Real Local Optuna Batch

Scope:

- Offset 1520, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1540 stocks and 7700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1520 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=7700 new_rows=100 elapsed=21.2s
formula_local_optuna_batch:done rows=7700 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=7700 candidates=370 rejected=7330
formula_local_optuna_merge_plan: rows=7700 replacements=370
```

Cumulative candidate distribution:

```text
activity_breakout: 218
gs_raw_buy: 69
gs_pullback_confirm: 43
volume_base_breakout: 40
```

New replacement examples from the seventy-seventh batch:

```text
300035 activity_breakout score_delta=16.93 validation_delta=1.97 sell_rule=fixed_30
300045 gs_pullback_confirm score_delta=16.62 validation_delta=9.26 sell_rule=fixed_60
300040 activity_breakout score_delta=15.48 validation_delta=19.35 sell_rule=fixed_60
300032 activity_breakout score_delta=12.54 validation_delta=5.27 sell_rule=formula_exit_or_30
300040 gs_raw_buy score_delta=7.92 validation_delta=4.57 sell_rule=fixed_30
300036 activity_breakout score_delta=5.38 validation_delta=1.89 sell_rule=formula_exit_or_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 993
missing_baseline_result / missing_optuna_result = 311
ok / missing_optuna_result = 21
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
missing_investigation_ok: 1325
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=7700 candidate_count=370 rejected_count=7330
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1304 missing_optuna_result=332
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1636
/api/parameter-search local_optuna.batch.merge_plan replacement_count=370 replacement_schema_rows=370 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1540 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Seventy-Eighth Real Local Optuna Batch

Scope:

- Offset 1540, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1560 stocks and 7800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1540 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=7800 new_rows=100 elapsed=20.5s
formula_local_optuna_batch:done rows=7800 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=7800 candidates=371 rejected=7429
formula_local_optuna_merge_plan: rows=7800 replacements=371
```

Cumulative candidate distribution:

```text
activity_breakout: 219
gs_raw_buy: 69
gs_pullback_confirm: 43
volume_base_breakout: 40
```

New replacement examples from the seventy-eighth batch:

```text
300068 activity_breakout score_delta=18.25 validation_delta=28.90 sell_rule=formula_exit_or_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1008
missing_baseline_result / missing_optuna_result = 311
ok / missing_optuna_result = 22
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=15
new_batch_missing_optuna_no_entry_signal=1
missing_investigation_ok: 1341
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=7800 candidate_count=371 rejected_count=7429
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1319 missing_optuna_result=333
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1652
/api/parameter-search local_optuna.batch.merge_plan replacement_count=371 replacement_schema_rows=371 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1560 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Seventy-Ninth Real Local Optuna Batch

Scope:

- Offset 1560, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1580 stocks and 7900 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1560 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=7900 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=7900 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=7900 candidates=377 rejected=7523
formula_local_optuna_merge_plan: rows=7900 replacements=377
```

Cumulative candidate distribution:

```text
activity_breakout: 222
gs_raw_buy: 70
gs_pullback_confirm: 44
volume_base_breakout: 41
```

New replacement examples from the seventy-ninth batch:

```text
300075 volume_base_breakout score_delta=42.27 validation_delta=6.68 sell_rule=formula_exit_or_5
300075 activity_breakout score_delta=30.12 validation_delta=22.62 sell_rule=formula_exit_or_5
300093 gs_pullback_confirm score_delta=11.25 validation_delta=8.70 sell_rule=fixed_30
300074 activity_breakout score_delta=10.97 validation_delta=10.44 sell_rule=fixed_30
300079 activity_breakout score_delta=8.03 validation_delta=8.33 sell_rule=formula_exit_or_5
300083 gs_raw_buy score_delta=4.18 validation_delta=8.36 sell_rule=formula_exit_or_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1025
missing_baseline_result / missing_optuna_result = 314
ok / missing_optuna_result = 22
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=17
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=3
missing_investigation_ok: 1361
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=7900 candidate_count=377 rejected_count=7523
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1339 missing_optuna_result=336
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1675
/api/parameter-search local_optuna.batch.merge_plan replacement_count=377 replacement_schema_rows=377 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1580 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Eightieth Real Local Optuna Batch

Scope:

- Offset 1580, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1600 stocks and 8000 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1580 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=8000 new_rows=100 elapsed=20.8s
formula_local_optuna_batch:done rows=8000 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=8000 candidates=384 rejected=7616
formula_local_optuna_merge_plan: rows=8000 replacements=384
```

Cumulative candidate distribution:

```text
activity_breakout: 225
gs_raw_buy: 73
gs_pullback_confirm: 44
volume_base_breakout: 42
```

New replacement examples from the eightieth batch:

```text
300099 volume_base_breakout score_delta=31.57 validation_delta=24.90 sell_rule=fixed_20
300096 activity_breakout score_delta=11.65 validation_delta=7.02 sell_rule=fixed_5
300115 activity_breakout score_delta=9.45 validation_delta=5.54 sell_rule=fixed_10
300097 activity_breakout score_delta=8.45 validation_delta=8.00 sell_rule=formula_exit_or_5
300105 gs_raw_buy score_delta=5.35 validation_delta=4.91 sell_rule=formula_exit_or_15
300096 gs_raw_buy score_delta=4.56 validation_delta=6.37 sell_rule=formula_exit_or_5
300098 gs_raw_buy score_delta=4.45 validation_delta=11.45 sell_rule=fixed_30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1038
missing_baseline_result / missing_optuna_result = 318
ok / missing_optuna_result = 23
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
optuna_investigation: entry signals produced no executable trades
new_batch_missing_baseline_no_row=13
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=3
new_batch_missing_baseline_no_row_and_optuna_no_executable_trade=1
new_batch_missing_optuna_no_entry_signal=1
missing_investigation_ok: 1379
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=8000 candidate_count=384 rejected_count=7616
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1356 missing_optuna_result=341
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1697
/api/parameter-search local_optuna.batch.merge_plan replacement_count=384 replacement_schema_rows=384 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1600 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Eighty-First Real Local Optuna Batch

Scope:

- Offset 1600, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1620 stocks and 8100 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1600 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=8100 new_rows=100 elapsed=20.7s
formula_local_optuna_batch:done rows=8100 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=8100 candidates=388 rejected=7712
formula_local_optuna_merge_plan: rows=8100 replacements=388
```

Cumulative candidate distribution:

```text
activity_breakout: 227
gs_raw_buy: 74
gs_pullback_confirm: 45
volume_base_breakout: 42
```

New replacement examples from the eighty-first batch:

```text
300128 activity_breakout score_delta=17.44 validation_delta=0.43 sell_rule=fixed_60
300126 activity_breakout score_delta=9.86 validation_delta=9.93 sell_rule=fixed_15
300128 gs_pullback_confirm score_delta=9.06 validation_delta=23.53 sell_rule=fixed_60
300124 gs_raw_buy score_delta=3.39 validation_delta=2.74 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1051
missing_baseline_result / missing_optuna_result = 324
ok / missing_optuna_result = 23
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=13
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=6
missing_investigation_ok: 1398
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=8100 candidate_count=388 rejected_count=7712
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1375 missing_optuna_result=347
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1722
/api/parameter-search local_optuna.batch.merge_plan replacement_count=388 replacement_schema_rows=388 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1620 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Eighty-Second Real Local Optuna Batch

Scope:

- Offset 1620, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1640 stocks and 8200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1620 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=8200 new_rows=100 elapsed=20.6s
formula_local_optuna_batch:done rows=8200 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=8200 candidates=396 rejected=7804
formula_local_optuna_merge_plan: rows=8200 replacements=396
```

Cumulative candidate distribution:

```text
activity_breakout: 233
gs_raw_buy: 75
gs_pullback_confirm: 46
volume_base_breakout: 42
```

New replacement examples from the eighty-second batch:

```text
300142 activity_breakout score_delta=21.78 validation_delta=14.50 sell_rule=formula_exit_or_5
300151 activity_breakout score_delta=18.42 validation_delta=15.35 sell_rule=formula_exit_or_20
300143 activity_breakout score_delta=17.50 validation_delta=2.86 sell_rule=fixed_10
300141 gs_pullback_confirm score_delta=12.21 validation_delta=1.26 sell_rule=fixed_15
300148 activity_breakout score_delta=10.93 validation_delta=5.28 sell_rule=fixed_20
300155 activity_breakout score_delta=7.17 validation_delta=6.66 sell_rule=formula_exit_or_30
300145 activity_breakout score_delta=6.86 validation_delta=19.27 sell_rule=fixed_20
300161 gs_raw_buy score_delta=3.60 validation_delta=0.48 sell_rule=formula_exit_or_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1068
missing_baseline_result / missing_optuna_result = 327
ok / missing_optuna_result = 23
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=17
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=3
missing_investigation_ok: 1418
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=8200 candidate_count=396 rejected_count=7804
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1395 missing_optuna_result=350
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1745
/api/parameter-search local_optuna.batch.merge_plan replacement_count=396 replacement_schema_rows=396 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1640 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Eighty-Third Real Local Optuna Batch

Scope:

- Offset 1640, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1660 stocks and 8300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1640 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=8300 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=8300 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=8300 candidates=399 rejected=7901
formula_local_optuna_merge_plan: rows=8300 replacements=399
```

Cumulative candidate distribution:

```text
activity_breakout: 234
gs_raw_buy: 75
gs_pullback_confirm: 46
volume_base_breakout: 44
```

New replacement examples from the eighty-third batch:

```text
300165 activity_breakout score_delta=17.19 validation_delta=18.05 sell_rule=formula_exit_or_15
300165 volume_base_breakout score_delta=6.31 validation_delta=5.29 sell_rule=fixed_60
300176 volume_base_breakout score_delta=5.24 validation_delta=0.01 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1080
missing_baseline_result / missing_optuna_result = 331
ok / missing_optuna_result = 23
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=12
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=4
missing_investigation_ok: 1434
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=8300 candidate_count=399 rejected_count=7901
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1411 missing_optuna_result=354
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1765
/api/parameter-search local_optuna.batch.merge_plan replacement_count=399 replacement_schema_rows=399 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1660 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Eighty-Fourth Real Local Optuna Batch

Scope:

- Offset 1660, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1680 stocks and 8400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1660 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=8400 new_rows=100 elapsed=20.4s
formula_local_optuna_batch:done rows=8400 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=8400 candidates=400 rejected=8000
formula_local_optuna_merge_plan: rows=8400 replacements=400
```

Cumulative candidate distribution:

```text
activity_breakout: 235
gs_raw_buy: 75
gs_pullback_confirm: 46
volume_base_breakout: 44
```

New replacement examples from the eighty-fourth batch:

```text
300191 activity_breakout score_delta=14.24 validation_delta=1.36 sell_rule=fixed_30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1091
missing_baseline_result / missing_optuna_result = 337
ok / missing_optuna_result = 24
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=11
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=6
new_batch_missing_optuna_no_entry_signal=1
missing_investigation_ok: 1452
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=8400 candidate_count=400 rejected_count=8000
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1428 missing_optuna_result=361
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1789
/api/parameter-search local_optuna.batch.merge_plan replacement_count=400 replacement_schema_rows=400 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1680 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Eighty-Fifth Real Local Optuna Batch

Scope:

- Offset 1680, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1700 stocks and 8500 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1680 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=8500 new_rows=100 elapsed=20.5s
formula_local_optuna_batch:done rows=8500 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=8500 candidates=404 rejected=8096
formula_local_optuna_merge_plan: rows=8500 replacements=404
```

Cumulative candidate distribution:

```text
activity_breakout: 237
gs_raw_buy: 76
gs_pullback_confirm: 47
volume_base_breakout: 44
```

New replacement examples from the eighty-fifth batch:

```text
300206 activity_breakout score_delta=14.58 validation_delta=4.99 sell_rule=formula_exit_or_10
300207 activity_breakout score_delta=7.77 validation_delta=2.31 sell_rule=formula_exit_or_15
300224 gs_raw_buy score_delta=5.10 validation_delta=4.10 sell_rule=fixed_60
300221 gs_pullback_confirm score_delta=4.79 validation_delta=7.29 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1105
missing_baseline_result / missing_optuna_result = 343
ok / missing_optuna_result = 24
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=14
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=6
missing_investigation_ok: 1472
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=8500 candidate_count=404 rejected_count=8096
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1448 missing_optuna_result=367
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1815
/api/parameter-search local_optuna.batch.merge_plan replacement_count=404 replacement_schema_rows=404 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1700 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Eighty-Sixth Real Local Optuna Batch

Scope:

- Offset 1700, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1720 stocks and 8600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1700 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=8600 new_rows=100 elapsed=20.7s
formula_local_optuna_batch:done rows=8600 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=8600 candidates=409 rejected=8191
formula_local_optuna_merge_plan: rows=8600 replacements=409
```

Cumulative candidate distribution:

```text
activity_breakout: 241
gs_raw_buy: 76
gs_pullback_confirm: 47
volume_base_breakout: 45
```

New replacement examples from the eighty-sixth batch:

```text
300239 activity_breakout score_delta=19.32 validation_delta=8.83 sell_rule=fixed_10
300241 volume_base_breakout score_delta=13.51 validation_delta=13.81 sell_rule=fixed_30
300228 activity_breakout score_delta=11.41 validation_delta=6.83 sell_rule=fixed_60
300235 activity_breakout score_delta=10.93 validation_delta=6.91 sell_rule=formula_exit_or_15
300241 activity_breakout score_delta=8.82 validation_delta=10.22 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1110
missing_baseline_result / missing_optuna_result = 347
ok / missing_optuna_result = 26
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=5
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=4
new_batch_missing_optuna_no_entry_signal=2
missing_investigation_ok: 1483
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=8600 candidate_count=409 rejected_count=8191
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1457 missing_optuna_result=373
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1830
/api/parameter-search local_optuna.batch.merge_plan replacement_count=409 replacement_schema_rows=409 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1720 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Eighty-Seventh Real Local Optuna Batch

Scope:

- Offset 1720, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1740 stocks and 8700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1720 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=8700 new_rows=100 elapsed=21.5s
formula_local_optuna_batch:done rows=8700 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=8700 candidates=415 rejected=8285
formula_local_optuna_merge_plan: rows=8700 replacements=415
```

Cumulative candidate distribution:

```text
activity_breakout: 247
gs_raw_buy: 76
gs_pullback_confirm: 47
volume_base_breakout: 45
```

New replacement examples from the eighty-seventh batch:

```text
300247 activity_breakout score_delta=21.92 validation_delta=10.78 sell_rule=fixed_30
300266 activity_breakout score_delta=15.78 validation_delta=38.56 sell_rule=formula_exit_or_10
300267 activity_breakout score_delta=15.72 validation_delta=11.61 sell_rule=fixed_10
300261 activity_breakout score_delta=8.72 validation_delta=10.61 sell_rule=fixed_5
300259 activity_breakout score_delta=7.58 validation_delta=1.02 sell_rule=fixed_60
300264 activity_breakout score_delta=3.67 validation_delta=4.59 sell_rule=fixed_30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1120
missing_baseline_result / missing_optuna_result = 350
ok / missing_optuna_result = 27
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=10
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=3
new_batch_missing_optuna_no_entry_signal=1
missing_investigation_ok: 1497
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=8700 candidate_count=415 rejected_count=8285
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1470 missing_optuna_result=377
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1847
/api/parameter-search local_optuna.batch.merge_plan replacement_count=415 replacement_schema_rows=415 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1740 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Eighty-Eighth Real Local Optuna Batch

Scope:

- Offset 1740, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1760 stocks and 8800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1740 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=8800 new_rows=100 elapsed=21.2s
formula_local_optuna_batch:done rows=8800 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=8800 candidates=419 rejected=8381
formula_local_optuna_merge_plan: rows=8800 replacements=419
```

Cumulative candidate distribution:

```text
activity_breakout: 251
gs_raw_buy: 76
gs_pullback_confirm: 47
volume_base_breakout: 45
```

New replacement examples from the eighty-eighth batch:

```text
300281 activity_breakout score_delta=15.10 validation_delta=47.55 sell_rule=fixed_10
300283 activity_breakout score_delta=12.73 validation_delta=18.75 sell_rule=fixed_10
300286 activity_breakout score_delta=12.73 validation_delta=19.13 sell_rule=formula_exit_or_60
300278 activity_breakout score_delta=7.25 validation_delta=42.81 sell_rule=formula_exit_or_10
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1131
missing_baseline_result / missing_optuna_result = 352
ok / missing_optuna_result = 27
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=11
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=2
missing_investigation_ok: 1510
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=8800 candidate_count=419 rejected_count=8381
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1483 missing_optuna_result=379
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1862
/api/parameter-search local_optuna.batch.merge_plan replacement_count=419 replacement_schema_rows=419 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1760 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Eighty-Ninth Real Local Optuna Batch

Scope:

- Offset 1760, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1780 stocks and 8900 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1760 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=8900 new_rows=100 elapsed=21.1s
formula_local_optuna_batch:done rows=8900 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=8900 candidates=425 rejected=8475
formula_local_optuna_merge_plan: rows=8900 replacements=425
```

Cumulative candidate distribution:

```text
activity_breakout: 255
gs_raw_buy: 77
gs_pullback_confirm: 48
volume_base_breakout: 45
```

New replacement examples from the eighty-ninth batch:

```text
300291 activity_breakout score_delta=23.19 validation_delta=29.74 sell_rule=fixed_60
300313 activity_breakout score_delta=13.72 validation_delta=5.44 sell_rule=fixed_60
300303 gs_pullback_confirm score_delta=12.90 validation_delta=2.48 sell_rule=formula_exit_or_15
300295 activity_breakout score_delta=6.98 validation_delta=31.31 sell_rule=formula_exit_or_60
300302 activity_breakout score_delta=6.97 validation_delta=2.23 sell_rule=fixed_10
300305 gs_raw_buy score_delta=6.50 validation_delta=2.11 sell_rule=formula_exit_or_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1142
missing_baseline_result / missing_optuna_result = 358
ok / missing_optuna_result = 27
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=11
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=6
missing_investigation_ok: 1527
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=8900 candidate_count=425 rejected_count=8475
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1500 missing_optuna_result=385
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1885
/api/parameter-search local_optuna.batch.merge_plan replacement_count=425 replacement_schema_rows=425 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1780 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Ninetieth Real Local Optuna Batch

Scope:

- Offset 1780, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1800 stocks and 9000 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1780 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=9000 new_rows=100 elapsed=20.5s
formula_local_optuna_batch:done rows=9000 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=9000 candidates=428 rejected=8572
formula_local_optuna_merge_plan: rows=9000 replacements=428
```

Cumulative candidate distribution:

```text
activity_breakout: 255
gs_raw_buy: 79
gs_pullback_confirm: 49
volume_base_breakout: 45
```

New replacement examples from the ninetieth batch:

```text
300326 gs_pullback_confirm score_delta=29.14 validation_delta=21.19 sell_rule=formula_exit_or_15
300320 gs_raw_buy score_delta=9.17 validation_delta=18.43 sell_rule=formula_exit_or_60
300333 gs_raw_buy score_delta=4.84 validation_delta=14.46 sell_rule=fixed_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1152
missing_baseline_result / missing_optuna_result = 361
ok / missing_optuna_result = 28
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=10
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=3
new_batch_missing_optuna_no_entry_signal=1
missing_investigation_ok: 1541
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=9000 candidate_count=428 rejected_count=8572
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1513 missing_optuna_result=389
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1902
/api/parameter-search local_optuna.batch.merge_plan replacement_count=428 replacement_schema_rows=428 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1800 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Ninety-First Real Local Optuna Batch

Scope:

- Offset 1800, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1820 stocks and 9100 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1800 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=9100 new_rows=100 elapsed=21.2s
formula_local_optuna_batch:done rows=9100 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=9100 candidates=434 rejected=8666
formula_local_optuna_merge_plan: rows=9100 replacements=434
```

Cumulative candidate distribution:

```text
activity_breakout: 259
gs_raw_buy: 81
gs_pullback_confirm: 49
volume_base_breakout: 45
```

New replacement examples from the ninety-first batch:

```text
300349 activity_breakout score_delta=38.25 validation_delta=31.09 sell_rule=fixed_20
300352 activity_breakout score_delta=18.37 validation_delta=35.29 sell_rule=fixed_10
300346 gs_raw_buy score_delta=9.40 validation_delta=6.27 sell_rule=fixed_20
300358 activity_breakout score_delta=8.52 validation_delta=4.75 sell_rule=fixed_20
300351 gs_raw_buy score_delta=7.88 validation_delta=8.42 sell_rule=formula_exit_or_5
300341 activity_breakout score_delta=7.16 validation_delta=3.66 sell_rule=formula_exit_or_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1158
missing_baseline_result / missing_optuna_result = 364
ok / missing_optuna_result = 29
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=6
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=3
new_batch_missing_optuna_no_entry_signal=1
missing_investigation_ok: 1551
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=9100 candidate_count=434 rejected_count=8666
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1522 missing_optuna_result=393
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1915
/api/parameter-search local_optuna.batch.merge_plan replacement_count=434 replacement_schema_rows=434 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1820 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Ninety-Second Real Local Optuna Batch

Scope:

- Offset 1820, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1840 stocks and 9200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1820 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=9200 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=9200 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=9200 candidates=437 rejected=8763
formula_local_optuna_merge_plan: rows=9200 replacements=437
```

Cumulative candidate distribution:

```text
activity_breakout: 261
gs_raw_buy: 81
gs_pullback_confirm: 50
volume_base_breakout: 45
```

New replacement examples from the ninety-second batch:

```text
300368 gs_pullback_confirm score_delta=18.28 validation_delta=23.01 sell_rule=fixed_30
300359 activity_breakout score_delta=15.33 validation_delta=25.06 sell_rule=fixed_10
300382 activity_breakout score_delta=8.30 validation_delta=27.82 sell_rule=fixed_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1171
missing_baseline_result / missing_optuna_result = 366
ok / missing_optuna_result = 29
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=13
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=2
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=9200 candidate_count=437 rejected_count=8763
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1537 missing_optuna_result=395
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1932
/api/parameter-search local_optuna.batch.merge_plan source_row_count=9200 replacement_count=437 replacement_schema_rows=437 dry_run=True
replacement_fields_ok=True
```

API parsing fix:

```text
main.py _optional_int now returns int(float(value)) for non-empty integer-like values.
main.py local_optuna merge summaries now expose source_row_count and replacement_fields_ok.
first_replacement_ints: new_signal_count=int(14), trials=int(24)
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1840 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Ninety-Third Real Local Optuna Batch

Scope:

- Offset 1840, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1860 stocks and 9300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1840 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=9300 new_rows=100 elapsed=21.2s
formula_local_optuna_batch:done rows=9300 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=9300 candidates=441 rejected=8859
formula_local_optuna_merge_plan: rows=9300 replacements=441
```

Cumulative candidate distribution:

```text
activity_breakout: 262
gs_raw_buy: 81
gs_pullback_confirm: 52
volume_base_breakout: 46
```

New replacement examples from the ninety-third batch:

```text
300404 volume_base_breakout score_delta=39.68 validation_delta=35.86 sell_rule=fixed_5
300405 activity_breakout score_delta=28.71 validation_delta=11.16 sell_rule=fixed_10
300393 gs_pullback_confirm score_delta=10.90 validation_delta=12.85 sell_rule=fixed_60
300399 gs_pullback_confirm score_delta=6.79 validation_delta=38.22 sell_rule=fixed_10
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1185
missing_baseline_result / missing_optuna_result = 369
ok / missing_optuna_result = 30
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=14
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=3
new_batch_missing_optuna_no_entry_signal=1
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=9300 candidate_count=441 rejected_count=8859
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1554 missing_optuna_result=399
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1953
/api/parameter-search local_optuna.batch.merge_plan source_row_count=9300 replacement_count=441 replacement_schema_rows=441 dry_run=True
replacement_fields_ok=True
first_replacement_ints: new_signal_count=int(14), trials=int(24)
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1860 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Ninety-Fourth Real Local Optuna Batch

Scope:

- Offset 1860, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1880 stocks and 9400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1860 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=9400 new_rows=100 elapsed=21.0s
formula_local_optuna_batch:done rows=9400 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=9400 candidates=444 rejected=8956
formula_local_optuna_merge_plan: rows=9400 replacements=444
```

Cumulative candidate distribution:

```text
activity_breakout: 265
gs_raw_buy: 81
gs_pullback_confirm: 52
volume_base_breakout: 46
```

New replacement examples from the ninety-fourth batch:

```text
300422 activity_breakout score_delta=16.08 validation_delta=15.15 sell_rule=fixed_5
300408 activity_breakout score_delta=12.68 validation_delta=8.35 sell_rule=fixed_60
300418 activity_breakout score_delta=3.24 validation_delta=0.20 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1193
missing_baseline_result / missing_optuna_result = 372
ok / missing_optuna_result = 30
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=8
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=3
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=9400 candidate_count=444 rejected_count=8956
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1565 missing_optuna_result=402
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1967
/api/parameter-search local_optuna.batch.merge_plan source_row_count=9400 replacement_count=444 replacement_schema_rows=444 dry_run=True
replacement_fields_ok=True
first_replacement_ints: new_signal_count=int(14), trials=int(24)
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1880 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Ninety-Fifth Real Local Optuna Batch

Scope:

- Offset 1880, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1900 stocks and 9500 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1880 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=9500 new_rows=100 elapsed=21.1s
formula_local_optuna_batch:done rows=9500 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=9500 candidates=447 rejected=9053
formula_local_optuna_merge_plan: rows=9500 replacements=447
```

Cumulative candidate distribution:

```text
activity_breakout: 266
gs_raw_buy: 81
gs_pullback_confirm: 53
volume_base_breakout: 47
```

New replacement examples from the ninety-fifth batch:

```text
300445 activity_breakout score_delta=13.17 validation_delta=2.94 sell_rule=fixed_60
300444 gs_pullback_confirm score_delta=7.05 validation_delta=7.37 sell_rule=fixed_60
300442 volume_base_breakout score_delta=4.60 validation_delta=2.32 sell_rule=fixed_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1199
missing_baseline_result / missing_optuna_result = 379
ok / missing_optuna_result = 30
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=7
new_batch_missing_baseline_no_row=6
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=9500 candidate_count=447 rejected_count=9053
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1578 missing_optuna_result=409
/api/parameter-search local_optuna.batch.missing_investigation_counts total=1987
/api/parameter-search local_optuna.batch.merge_plan source_row_count=9500 replacement_count=447 replacement_schema_rows=447 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1900 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Ninety-Sixth Real Local Optuna Batch

Scope:

- Offset 1900, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1920 stocks and 9600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1900 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=9600 new_rows=100 elapsed=20.8s
formula_local_optuna_batch:done rows=9600 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=9600 candidates=451 rejected=9149
formula_local_optuna_merge_plan: rows=9600 replacements=451
```

Cumulative candidate distribution:

```text
activity_breakout: 268
gs_raw_buy: 82
gs_pullback_confirm: 53
volume_base_breakout: 48
```

New replacement examples from the ninety-sixth batch:

```text
300458 activity_breakout score_delta=13.85 validation_delta=11.60 sell_rule=fixed_60
300461 activity_breakout score_delta=10.57 validation_delta=7.01 sell_rule=fixed_60
300448 gs_raw_buy score_delta=7.42 validation_delta=7.16 sell_rule=fixed_60
300461 volume_base_breakout score_delta=6.55 validation_delta=10.00 sell_rule=fixed_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1207
missing_baseline_result / missing_optuna_result = 384
ok / missing_optuna_result = 30
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=8
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=5
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=9600 candidate_count=451 rejected_count=9149
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1591 missing_optuna_result=414
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2005
/api/parameter-search local_optuna.batch.merge_plan source_row_count=9600 replacement_count=451 replacement_schema_rows=451 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1920 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Ninety-Seventh Real Local Optuna Batch

Scope:

- Offset 1920, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1940 stocks and 9700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1920 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=9700 new_rows=100 elapsed=21.7s
formula_local_optuna_batch:done rows=9700 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=9700 candidates=453 rejected=9247
formula_local_optuna_merge_plan: rows=9700 replacements=453
```

Cumulative candidate distribution:

```text
activity_breakout: 269
gs_raw_buy: 82
gs_pullback_confirm: 54
volume_base_breakout: 48
```

New replacement examples from the ninety-seventh batch:

```text
300481 activity_breakout score_delta=10.48 validation_delta=12.47 sell_rule=formula_exit_or_10
300471 gs_pullback_confirm score_delta=4.40 validation_delta=14.28 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1220
missing_baseline_result / missing_optuna_result = 385
ok / missing_optuna_result = 30
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=13
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=1
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=9700 candidate_count=453 rejected_count=9247
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1605 missing_optuna_result=415
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2020
/api/parameter-search local_optuna.batch.merge_plan source_row_count=9700 replacement_count=453 replacement_schema_rows=453 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1940 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Ninety-Eighth Real Local Optuna Batch

Scope:

- Offset 1940, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1960 stocks and 9800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1940 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=9800 new_rows=100 elapsed=21.1s
formula_local_optuna_batch:done rows=9800 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=9800 candidates=457 rejected=9343
formula_local_optuna_merge_plan: rows=9800 replacements=457
```

Cumulative candidate distribution:

```text
activity_breakout: 271
gs_raw_buy: 83
gs_pullback_confirm: 55
volume_base_breakout: 48
```

New replacement examples from the ninety-eighth batch:

```text
300501 activity_breakout score_delta=19.09 validation_delta=18.69 sell_rule=formula_exit_or_15
300487 activity_breakout score_delta=14.79 validation_delta=16.34 sell_rule=fixed_15
300490 gs_pullback_confirm score_delta=11.48 validation_delta=0.00 sell_rule=fixed_60
300487 gs_raw_buy score_delta=9.63 validation_delta=16.92 sell_rule=fixed_5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1233
missing_baseline_result / missing_optuna_result = 387
ok / missing_optuna_result = 32
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=13
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=2
new_batch_missing_optuna_no_entry_signal=2
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=9800 candidate_count=457 rejected_count=9343
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1620 missing_optuna_result=419
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2039
/api/parameter-search local_optuna.batch.merge_plan source_row_count=9800 replacement_count=457 replacement_schema_rows=457 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1960 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Ninety-Ninth Real Local Optuna Batch

Scope:

- Offset 1960, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 1980 stocks and 9900 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1960 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=9900 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=9900 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=9900 candidates=462 rejected=9438
formula_local_optuna_merge_plan: rows=9900 replacements=462
```

Cumulative candidate distribution:

```text
activity_breakout: 273
gs_raw_buy: 84
gs_pullback_confirm: 57
volume_base_breakout: 48
```

New replacement examples from the ninety-ninth batch:

```text
300514 gs_pullback_confirm score_delta=19.54 validation_delta=9.71 sell_rule=formula_exit_or_30
300508 activity_breakout score_delta=12.59 validation_delta=28.33 sell_rule=fixed_5
300509 gs_pullback_confirm score_delta=9.69 validation_delta=5.58 sell_rule=fixed_60
300512 gs_raw_buy score_delta=3.77 validation_delta=0.40 sell_rule=fixed_5
300521 activity_breakout score_delta=3.32 validation_delta=14.48 sell_rule=fixed_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1241
missing_baseline_result / missing_optuna_result = 391
ok / missing_optuna_result = 32
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=8
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=4
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=9900 candidate_count=462 rejected_count=9438
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1632 missing_optuna_result=423
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2055
/api/parameter-search local_optuna.batch.merge_plan source_row_count=9900 replacement_count=462 replacement_schema_rows=462 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 1980 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundredth Real Local Optuna Batch

Scope:

- Offset 1980, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2000 stocks and 10000 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 1980 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=10000 new_rows=100 elapsed=21.3s
formula_local_optuna_batch:done rows=10000 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=10000 candidates=468 rejected=9532
formula_local_optuna_merge_plan: rows=10000 replacements=468
```

Cumulative candidate distribution:

```text
activity_breakout: 275
gs_raw_buy: 87
gs_pullback_confirm: 58
volume_base_breakout: 48
```

New replacement examples from the one-hundredth batch:

```text
300546 activity_breakout score_delta=25.04 validation_delta=7.28 sell_rule=formula_exit_or_10
300542 activity_breakout score_delta=10.46 validation_delta=7.61 sell_rule=formula_exit_or_15
300530 gs_raw_buy score_delta=8.64 validation_delta=3.87 sell_rule=fixed_15
300538 gs_pullback_confirm score_delta=6.97 validation_delta=35.76 sell_rule=fixed_60
300546 gs_raw_buy score_delta=5.56 validation_delta=1.23 sell_rule=fixed_20
300538 gs_raw_buy score_delta=5.47 validation_delta=2.64 sell_rule=fixed_20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1249
missing_baseline_result / missing_optuna_result = 395
ok / missing_optuna_result = 32
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=8
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=4
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=10000 candidate_count=468 rejected_count=9532
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1644 missing_optuna_result=427
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2071
/api/parameter-search local_optuna.batch.merge_plan source_row_count=10000 replacement_count=468 replacement_schema_rows=468 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2000 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-First Real Local Optuna Batch

Scope:

- Offset 2000, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2020 stocks and 10100 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2000 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=10100 new_rows=100 elapsed=22.0s
formula_local_optuna_batch:done rows=10100 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=10100 candidates=471 rejected=9629
formula_local_optuna_merge_plan: rows=10100 replacements=471
```

Cumulative candidate distribution:

```text
activity_breakout: 276
gs_raw_buy: 88
gs_pullback_confirm: 59
volume_base_breakout: 48
```

New replacement examples from the one-hundred-first batch:

```text
300562 activity_breakout score_delta=12.85 validation_delta=12.13 sell_rule=fixed_60
300566 gs_raw_buy score_delta=10.51 validation_delta=7.26 sell_rule=fixed_60
300562 gs_pullback_confirm score_delta=7.05 validation_delta=1.40 sell_rule=fixed_15
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1260
missing_baseline_result / missing_optuna_result = 399
ok / missing_optuna_result = 33
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=11
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=4
new_batch_missing_optuna_no_entry_signal=1
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=10100 candidate_count=471 rejected_count=9629
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1659 missing_optuna_result=432
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2091
/api/parameter-search local_optuna.batch.merge_plan source_row_count=10100 replacement_count=471 replacement_schema_rows=471 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2020 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Second Real Local Optuna Batch

Scope:

- Offset 2020, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2040 stocks and 10200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2020 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=10200 new_rows=100 elapsed=21.3s
formula_local_optuna_batch:done rows=10200 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=10200 candidates=475 rejected=9725
formula_local_optuna_merge_plan: rows=10200 replacements=475
```

Cumulative candidate distribution:

```text
activity_breakout: 277
gs_raw_buy: 90
gs_pullback_confirm: 60
volume_base_breakout: 48
```

New replacement examples from the one-hundred-second batch:

```text
300581 gs_raw_buy score_delta=11.26 validation_delta=1.31 sell_rule=formula_exit_or_5
300572 gs_pullback_confirm score_delta=7.40 validation_delta=7.37 sell_rule=fixed_20
300576 gs_raw_buy score_delta=7.02 validation_delta=8.08 sell_rule=fixed_5
300585 activity_breakout score_delta=6.48 validation_delta=0.03 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1274
missing_baseline_result / missing_optuna_result = 405
ok / missing_optuna_result = 33
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=14
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=6
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=10200 candidate_count=475 rejected_count=9725
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1679 missing_optuna_result=438
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2117
/api/parameter-search local_optuna.batch.merge_plan source_row_count=10200 replacement_count=475 replacement_schema_rows=475 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2040 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Third Real Local Optuna Batch

Scope:

- Offset 2040, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2060 stocks and 10300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2040 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=10300 new_rows=100 elapsed=22.0s
formula_local_optuna_batch:done rows=10300 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=10300 candidates=478 rejected=9822
formula_local_optuna_merge_plan: rows=10300 replacements=478
```

Cumulative candidate distribution:

```text
activity_breakout: 277
gs_raw_buy: 90
gs_pullback_confirm: 62
volume_base_breakout: 49
```

New replacement examples from the one-hundred-third batch:

```text
300604 gs_pullback_confirm score_delta=28.04 validation_delta=24.46 sell_rule=formula_exit_or_30
300599 volume_base_breakout score_delta=9.07 validation_delta=25.46 sell_rule=formula_exit_or_15
300609 gs_pullback_confirm score_delta=5.71 validation_delta=7.37 sell_rule=fixed_60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1282
missing_baseline_result / missing_optuna_result = 411
ok / missing_optuna_result = 34
missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=8
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=6
new_batch_missing_optuna_no_entry_signal=1
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=10300 candidate_count=478 rejected_count=9822
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1693 missing_optuna_result=445
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2138
/api/parameter-search local_optuna.batch.merge_plan source_row_count=10300 replacement_count=478 replacement_schema_rows=478 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2060 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Fourth Real Local Optuna Batch

Scope:

- Offset 2060, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2080 stocks and 10400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2060 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=10400 new_rows=100 elapsed=20.9s
formula_local_optuna_batch:done rows=10400 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=10400 candidates=480 rejected=9920
formula_local_optuna_merge_plan: rows=10400 replacements=480
```

Cumulative candidate distribution:

```text
activity_breakout: 278
gs_raw_buy: 91
gs_pullback_confirm: 62
volume_base_breakout: 49
```

New replacement examples from the one-hundred-fourth batch:

```text
300625 activity_breakout score_delta=14.80 validation_delta=8.63 sell_rule=formula_exit_or_10 holding_days=10
300629 gs_raw_buy score_delta=3.88 validation_delta=6.54 sell_rule=fixed_15 holding_days=15
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1296
missing_baseline_result / missing_optuna_result = 412
ok / missing_optuna_result = 35
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=14
new_batch_missing_optuna_no_entry_signal=1
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=1
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=10400 candidate_count=480 rejected_count=9920
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1708 missing_optuna_result=447
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2155
/api/parameter-search local_optuna.batch.merge_plan source_row_count=10400 replacement_count=480 replacement_schema_rows=480 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2080 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Fifth Real Local Optuna Batch

Scope:

- Offset 2080, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2100 stocks and 10500 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2080 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=10500 new_rows=100 elapsed=20.4s
formula_local_optuna_batch:done rows=10500 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=10500 candidates=487 rejected=10013
formula_local_optuna_merge_plan: rows=10500 replacements=487
```

Cumulative candidate distribution:

```text
activity_breakout: 283
gs_raw_buy: 93
gs_pullback_confirm: 62
volume_base_breakout: 49
```

New replacement examples from the one-hundred-fifth batch:

```text
300650 activity_breakout score_delta=17.84 validation_delta=22.18 sell_rule=formula_exit_or_5 holding_days=5
300636 activity_breakout score_delta=13.71 validation_delta=7.63 sell_rule=formula_exit_or_10 holding_days=10
300645 activity_breakout score_delta=13.47 validation_delta=14.32 sell_rule=fixed_15 holding_days=15
300647 activity_breakout score_delta=10.92 validation_delta=38.67 sell_rule=formula_exit_or_5 holding_days=5
300633 activity_breakout score_delta=10.48 validation_delta=45.83 sell_rule=fixed_20 holding_days=20
300643 gs_raw_buy score_delta=5.15 validation_delta=2.06 sell_rule=fixed_20 holding_days=20
300650 gs_raw_buy score_delta=3.34 validation_delta=9.38 sell_rule=fixed_20 holding_days=20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1308
missing_baseline_result / missing_optuna_result = 418
ok / missing_optuna_result = 35
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=12
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=6
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=10500 candidate_count=487 rejected_count=10013
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1726 missing_optuna_result=453
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2179
/api/parameter-search local_optuna.batch.merge_plan source_row_count=10500 replacement_count=487 replacement_schema_rows=487 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2100 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Sixth Real Local Optuna Batch

Scope:

- Offset 2100, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2120 stocks and 10600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2100 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=10600 new_rows=100 elapsed=20.7s
formula_local_optuna_batch:done rows=10600 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=10600 candidates=492 rejected=10108
formula_local_optuna_merge_plan: rows=10600 replacements=492
```

Cumulative candidate distribution:

```text
activity_breakout: 285
gs_raw_buy: 95
gs_pullback_confirm: 62
volume_base_breakout: 50
```

New replacement examples from the one-hundred-sixth batch:

```text
300670 activity_breakout score_delta=15.49 validation_delta=10.09 sell_rule=fixed_5 holding_days=5
300663 activity_breakout score_delta=11.25 validation_delta=12.08 sell_rule=formula_exit_or_15 holding_days=15
300669 volume_base_breakout score_delta=8.09 validation_delta=4.65 sell_rule=fixed_60 holding_days=60
300670 gs_raw_buy score_delta=7.37 validation_delta=9.92 sell_rule=fixed_20 holding_days=20
300656 gs_raw_buy score_delta=5.75 validation_delta=2.30 sell_rule=formula_exit_or_20 holding_days=20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1322
missing_baseline_result / missing_optuna_result = 425
ok / missing_optuna_result = 36
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=14
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=7
new_batch_missing_optuna_no_entry_signal=1
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=10600 candidate_count=492 rejected_count=10108
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1747 missing_optuna_result=461
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2208
/api/parameter-search local_optuna.batch.merge_plan source_row_count=10600 replacement_count=492 replacement_schema_rows=492 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2120 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Seventh Real Local Optuna Batch

Scope:

- Offset 2120, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2140 stocks and 10700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2120 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=10700 new_rows=100 elapsed=20.7s
formula_local_optuna_batch:done rows=10700 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=10700 candidates=496 rejected=10204
formula_local_optuna_merge_plan: rows=10700 replacements=496
```

Cumulative candidate distribution:

```text
activity_breakout: 288
gs_raw_buy: 96
gs_pullback_confirm: 62
volume_base_breakout: 50
```

New replacement examples from the one-hundred-seventh batch:

```text
300687 activity_breakout score_delta=16.99 validation_delta=3.78 sell_rule=formula_exit_or_5 holding_days=5
300679 activity_breakout score_delta=14.63 validation_delta=1.28 sell_rule=fixed_60 holding_days=60
300675 activity_breakout score_delta=11.60 validation_delta=25.72 sell_rule=fixed_15 holding_days=15
300690 gs_raw_buy score_delta=4.68 validation_delta=5.58 sell_rule=fixed_60 holding_days=60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1334
missing_baseline_result / missing_optuna_result = 428
ok / missing_optuna_result = 37
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=12
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=3
new_batch_missing_optuna_no_entry_signal=1
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=10700 candidate_count=496 rejected_count=10204
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1762 missing_optuna_result=465
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2227
/api/parameter-search local_optuna.batch.merge_plan source_row_count=10700 replacement_count=496 replacement_schema_rows=496 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2140 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Eighth Real Local Optuna Batch

Scope:

- Offset 2140, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2160 stocks and 10800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2140 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=10800 new_rows=100 elapsed=21.2s
formula_local_optuna_batch:done rows=10800 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=10800 candidates=499 rejected=10301
formula_local_optuna_merge_plan: rows=10800 replacements=499
```

Cumulative candidate distribution:

```text
activity_breakout: 289
gs_raw_buy: 98
gs_pullback_confirm: 62
volume_base_breakout: 50
```

New replacement examples from the one-hundred-eighth batch:

```text
300711 activity_breakout score_delta=9.83 validation_delta=50.52 sell_rule=formula_exit_or_5 holding_days=5
300708 gs_raw_buy score_delta=6.41 validation_delta=19.12 sell_rule=fixed_20 holding_days=20
300713 gs_raw_buy score_delta=5.48 validation_delta=2.43 sell_rule=fixed_10 holding_days=10
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1349
missing_baseline_result / missing_optuna_result = 432
ok / missing_optuna_result = 37
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=15
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=4
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=10800 candidate_count=499 rejected_count=10301
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1781 missing_optuna_result=469
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2250
/api/parameter-search local_optuna.batch.merge_plan source_row_count=10800 replacement_count=499 replacement_schema_rows=499 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2160 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Ninth Real Local Optuna Batch

Scope:

- Offset 2160, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2180 stocks and 10900 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2160 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=10900 new_rows=100 elapsed=21.4s
formula_local_optuna_batch:done rows=10900 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=10900 candidates=501 rejected=10399
formula_local_optuna_merge_plan: rows=10900 replacements=501
```

Cumulative candidate distribution:

```text
activity_breakout: 290
gs_raw_buy: 98
gs_pullback_confirm: 63
volume_base_breakout: 50
```

New replacement examples from the one-hundred-ninth batch:

```text
300725 activity_breakout score_delta=28.85 validation_delta=32.74 sell_rule=formula_exit_or_10 holding_days=10
300732 gs_pullback_confirm score_delta=11.64 validation_delta=31.80 sell_rule=fixed_20 holding_days=20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1359
missing_baseline_result / missing_optuna_result = 439
ok / missing_optuna_result = 38
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=10
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=7
new_batch_missing_optuna_no_entry_signal=1
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=10900 candidate_count=501 rejected_count=10399
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1798 missing_optuna_result=477
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2275
/api/parameter-search local_optuna.batch.merge_plan source_row_count=10900 replacement_count=501 replacement_schema_rows=501 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2180 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Tenth Real Local Optuna Batch

Scope:

- Offset 2180, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2200 stocks and 11000 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2180 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=11000 new_rows=100 elapsed=21.4s
formula_local_optuna_batch:done rows=11000 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=11000 candidates=508 rejected=10492
formula_local_optuna_merge_plan: rows=11000 replacements=508
```

Cumulative candidate distribution:

```text
activity_breakout: 295
gs_raw_buy: 99
gs_pullback_confirm: 64
volume_base_breakout: 50
```

New replacement examples from the one-hundred-tenth batch:

```text
300743 activity_breakout score_delta=11.67 validation_delta=19.55 sell_rule=fixed_60 holding_days=60
300760 activity_breakout score_delta=11.08 validation_delta=11.10 sell_rule=formula_exit_or_10 holding_days=10
300757 activity_breakout score_delta=6.33 validation_delta=8.94 sell_rule=fixed_60 holding_days=60
300745 gs_pullback_confirm score_delta=6.11 validation_delta=19.40 sell_rule=fixed_60 holding_days=60
300740 activity_breakout score_delta=4.20 validation_delta=0.38 sell_rule=fixed_60 holding_days=60
300741 gs_raw_buy score_delta=3.81 validation_delta=7.70 sell_rule=fixed_20 holding_days=20
300746 activity_breakout score_delta=3.50 validation_delta=4.59 sell_rule=fixed_30 holding_days=30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1374
missing_baseline_result / missing_optuna_result = 444
ok / missing_optuna_result = 38
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=15
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=5
new_batch_missing_optuna_no_entry_signal=0
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=11000 candidate_count=508 rejected_count=10492
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1818 missing_optuna_result=482
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2300
/api/parameter-search local_optuna.batch.merge_plan source_row_count=11000 replacement_count=508 replacement_schema_rows=508 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2200 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Eleventh Real Local Optuna Batch

Scope:

- Offset 2200, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2220 stocks and 11100 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2200 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=11100 new_rows=100 elapsed=21.4s
formula_local_optuna_batch:done rows=11100 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=11100 candidates=513 rejected=10587
formula_local_optuna_merge_plan: rows=11100 replacements=513
```

Cumulative candidate distribution:

```text
activity_breakout: 299
gs_raw_buy: 99
gs_pullback_confirm: 64
volume_base_breakout: 51
```

New replacement examples from the one-hundred-eleventh batch:

```text
300761 activity_breakout score_delta=34.63 validation_delta=19.24 sell_rule=fixed_5 holding_days=5
300767 activity_breakout score_delta=26.24 validation_delta=10.96 sell_rule=fixed_15 holding_days=15
300770 volume_base_breakout score_delta=19.84 validation_delta=14.83 sell_rule=fixed_60 holding_days=60
300775 activity_breakout score_delta=16.95 validation_delta=5.23 sell_rule=fixed_5 holding_days=5
300769 activity_breakout score_delta=9.03 validation_delta=19.91 sell_rule=formula_exit_or_5 holding_days=5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1385
missing_baseline_result / missing_optuna_result = 447
ok / missing_optuna_result = 40
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=11
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=3
new_batch_missing_optuna_no_entry_signal=2
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=11100 candidate_count=513 rejected_count=10587
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1832 missing_optuna_result=487
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2319
/api/parameter-search local_optuna.batch.merge_plan source_row_count=11100 replacement_count=513 replacement_schema_rows=513 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2220 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Twelfth Real Local Optuna Batch

Scope:

- Offset 2220, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2240 stocks and 11200 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2220 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=11200 new_rows=100 elapsed=21.0s
formula_local_optuna_batch:done rows=11200 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=11200 candidates=519 rejected=10681
formula_local_optuna_merge_plan: rows=11200 replacements=519
```

Cumulative candidate distribution:

```text
activity_breakout: 303
gs_raw_buy: 99
gs_pullback_confirm: 66
volume_base_breakout: 51
```

New replacement examples from the one-hundred-twelfth batch:

```text
300797 gs_pullback_confirm score_delta=23.57 validation_delta=23.07 sell_rule=formula_exit_or_10 holding_days=10
300792 activity_breakout score_delta=17.14 validation_delta=8.34 sell_rule=fixed_10 holding_days=10
300802 activity_breakout score_delta=15.68 validation_delta=11.02 sell_rule=fixed_5 holding_days=5
300785 activity_breakout score_delta=15.27 validation_delta=28.89 sell_rule=fixed_60 holding_days=60
300788 activity_breakout score_delta=14.95 validation_delta=10.37 sell_rule=fixed_60 holding_days=60
300790 gs_pullback_confirm score_delta=5.58 validation_delta=4.11 sell_rule=fixed_20 holding_days=20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1401
missing_baseline_result / missing_optuna_result = 448
ok / missing_optuna_result = 41
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=16
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=1
new_batch_missing_optuna_no_entry_signal=1
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=11200 candidate_count=519 rejected_count=10681
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1849 missing_optuna_result=489
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2338
/api/parameter-search local_optuna.batch.merge_plan source_row_count=11200 replacement_count=519 replacement_schema_rows=519 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2240 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Thirteenth Real Local Optuna Batch

Scope:

- Offset 2240, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2260 stocks and 11300 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2240 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=11300 new_rows=100 elapsed=23.1s
formula_local_optuna_batch:done rows=11300 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=11300 candidates=528 rejected=10772
formula_local_optuna_merge_plan: rows=11300 replacements=528
```

Cumulative candidate distribution:

```text
activity_breakout: 306
gs_raw_buy: 101
gs_pullback_confirm: 70
volume_base_breakout: 51
```

New replacement examples from the one-hundred-thirteenth batch:

```text
300818 activity_breakout score_delta=33.80 validation_delta=2.76 sell_rule=fixed_20 holding_days=20
300823 activity_breakout score_delta=19.92 validation_delta=1.76 sell_rule=formula_exit_or_10 holding_days=10
300818 gs_pullback_confirm score_delta=14.28 validation_delta=10.29 sell_rule=fixed_60 holding_days=60
300812 gs_pullback_confirm score_delta=12.55 validation_delta=3.48 sell_rule=fixed_60 holding_days=60
300811 gs_raw_buy score_delta=12.50 validation_delta=13.82 sell_rule=formula_exit_or_15 holding_days=15
300805 gs_raw_buy score_delta=7.83 validation_delta=5.42 sell_rule=fixed_60 holding_days=60
300814 gs_pullback_confirm score_delta=6.07 validation_delta=1.66 sell_rule=fixed_60 holding_days=60
300812 activity_breakout score_delta=4.83 validation_delta=1.98 sell_rule=fixed_60 holding_days=60
300805 gs_pullback_confirm score_delta=4.12 validation_delta=4.28 sell_rule=fixed_15 holding_days=15
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1409
missing_baseline_result / missing_optuna_result = 454
ok / missing_optuna_result = 42
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=8
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=6
new_batch_missing_optuna_no_entry_signal=1
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=11300 candidate_count=528 rejected_count=10772
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1863 missing_optuna_result=496
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2359
/api/parameter-search local_optuna.batch.merge_plan source_row_count=11300 replacement_count=528 replacement_schema_rows=528 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2260 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: One-Hundred-Fourteenth Real Local Optuna Batch

Scope:

- Offset 2260, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2280 stocks and 11400 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2260 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
```

Batch run:

```text
formula_local_optuna_batch:done rows=11400 new_rows=100 elapsed=21.3s
formula_local_optuna_batch:done rows=11400 new_rows=0 elapsed=0.2s
```

Cumulative adoption result:

```text
formula_local_optuna_adoption: rows=11400 candidates=536 rejected=10864
formula_local_optuna_merge_plan: rows=11400 replacements=536
```

Cumulative candidate distribution:

```text
activity_breakout: 310
gs_raw_buy: 103
gs_pullback_confirm: 71
volume_base_breakout: 52
```

New replacement examples from the one-hundred-fourteenth batch:

```text
300827 activity_breakout score_delta=38.11 validation_delta=36.48 sell_rule=formula_exit_or_5 holding_days=5
300832 volume_base_breakout score_delta=20.37 validation_delta=21.58 sell_rule=fixed_20 holding_days=20
300841 activity_breakout score_delta=19.63 validation_delta=8.28 sell_rule=formula_exit_or_5 holding_days=5
300837 activity_breakout score_delta=19.29 validation_delta=2.04 sell_rule=fixed_30 holding_days=30
300832 activity_breakout score_delta=16.34 validation_delta=7.41 sell_rule=formula_exit_or_30 holding_days=30
300837 gs_pullback_confirm score_delta=12.68 validation_delta=24.66 sell_rule=fixed_60 holding_days=60
300834 gs_raw_buy score_delta=7.23 validation_delta=5.91 sell_rule=formula_exit_or_5 holding_days=5
300835 gs_raw_buy score_delta=3.44 validation_delta=2.89 sell_rule=formula_exit_or_30 holding_days=30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1422
missing_baseline_result / missing_optuna_result = 458
ok / missing_optuna_result = 42
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing-result investigation notes:

```text
baseline_investigation: stock_formula_best.csv has no row for this stock/formula
optuna_investigation: formula produced no entry signals
new_batch_missing_baseline_no_row=13
new_batch_missing_baseline_no_row_and_optuna_no_entry_signal=4
new_batch_missing_optuna_no_entry_signal=0
```

API/UI verification:

```text
/api/parameter-search ready=True formula_count=5 metric_count=228 metric_count_source=formula_variant_metrics.csv
/api/parameter-search local_optuna.batch row_count=11400 candidate_count=536 rejected_count=10864
/api/parameter-search local_optuna.batch.status_counts missing_baseline_result=1880 missing_optuna_result=500
/api/parameter-search local_optuna.batch.missing_investigation_counts total=2380
/api/parameter-search local_optuna.batch.merge_plan source_row_count=11400 replacement_count=536 replacement_schema_rows=536 dry_run=True
replacement_fields_ok=True
```

Regression verification:

```text
python -m py_compile compute.py execution_model.py formula_engine.py main.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- The batch path has now covered 2280 stocks. Continue offset-based batches; production merge remains blocked until full intended coverage and aggregate review are complete.
- Missing rows remain investigation leads. They are not default-filled, are not eligible for replacement candidates, and remain exposed by the API through batch-level `status_counts` and `missing_investigation_counts`.

## 2026-05-20 Follow-up: Research Cache and Optuna Console Management State

Scope:

- Added a first version of the versioned research cache builder.
- Extended `/api/parameter-search` with management state, artifact fingerprints, source file paths, and batch top candidates.
- Added an Optuna management console card to the existing parameter research section.
- Tightened the frontend rule: missing management fields are shown as blocking API/data gaps, not silently inferred from local UI state.

Command:

```text
python scripts/research_cache_build.py
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
node inline-script syntax check for index.html
git diff --check
```

Observed output:

```text
research_cache_build: rows=32202 stocks=5143 local_optuna=10900 production=21302 candidates=536 data_latest_date=2026-05-19
index inline scripts: syntax ok
```

API verification:

```text
/api/parameter-search ready=True
research_cache.ready=True
research_cache.row_count=32202
research_cache.stock_count=5143
research_cache.local_optuna_rows=10900
research_cache.production_rows=21302
research_cache.candidate_rows=536
research_cache.data_latest_date=2026-05-19
management.full_initialization covered_stock_count=2280 total_stock_count=5201 next_offset=2280 dry_run=True
```

Implementation notes:

- `scripts/research_cache_build.py` defaults to rebuilding the current cache contents, avoiding duplicate old artifact rows when data date or version keys change. Use `--append` only when intentionally preserving older cache-key versions.
- The builder now fails if it cannot determine a valid broad-coverage market data date. It first uses `compute.get_latest_data_date()`, then falls back to querying `market.duckdb`.
- The Optuna console now reads management fields from the API. If fields are missing, the UI displays the missing API/data state instead of calculating a normal-looking progress value locally.

Updated residual risk:

- Incremental Evaluator and Drift Trigger are still pending. They are intentionally displayed as `待建` until their DuckDB stores and API statuses exist.
- Research Cache is now queryable, but production merge remains blocked until full-market coverage and aggregate review are complete.

## 2026-05-20 Follow-up: Incremental Evaluator State and Top-Level Architecture

Scope:

- Added `scripts/incremental_eval_build.py`.
- Built `analysis/incremental_eval.duckdb` from BestChoice-owned `analysis/research_cache.duckdb`.
- Extended `/api/parameter-search` to read real incremental clean/dirty/pending counts.
- Updated the Optuna management console to display incremental target date and clean/dirty counts.
- Added `analysis/top_level_architecture_plan.md` to define minimal modules, data ownership, and the CodeGraph + complexity optimizer workflow.

Command:

```text
python scripts/incremental_eval_build.py
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
node inline-script syntax check for index.html
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
incremental_eval_build: rows=32202 stocks=5143 clean=32202 dirty=0 source_cache=32202 target_data_date=2026-05-19 dirty_reasons={}
index inline scripts: syntax ok
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

API verification:

```text
/api/parameter-search ready=True
research_cache.ready=True row_count=32202 stock_count=5143 candidate_rows=536 data_latest_date=2026-05-19
incremental_eval.ready=True row_count=32202 stock_count=5143 clean_count=32202 dirty_count=0 pending_count=0 target_data_date=2026-05-19
drift.ready=False stale_reason=drift_trigger.duckdb not created yet
```

Architecture decision:

- BestChoice owns research state in `analysis/*.duckdb`.
- `chunkymonkey` remains read-only upstream data.
- Minimal module boundaries are Source Adapters, Signal and Execution Core, Research Pipeline, Research State Store, API Aggregation, and UI Console.
- CodeGraph is already present via `.codegraph/codegraph.db`; it should be used to pick optimization targets and dependency radius before asking `codex-complexity-optimizer` for a report-only complexity/performance plan.

Updated residual risk:

- `drift_trigger.duckdb` is still pending.
- `codex-complexity-optimizer` is not detected as installed in the current Codex environment, so the workflow is designed but not yet executable through the skill.
- Production merge remains blocked until full-market coverage and aggregate review are complete.

## 2026-05-20 Follow-up: Drift Trigger State

Scope:

- Added `scripts/drift_trigger_build.py`.
- Built `analysis/drift_trigger.duckdb` from BestChoice-owned `analysis/research_cache.duckdb` and `analysis/incremental_eval.duckdb`.
- Extended `/api/parameter-search` to read real drift trigger counts.
- Updated the Optuna management console to display watch/reevaluate/reoptimize counts.

Command:

```text
python scripts/drift_trigger_build.py
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
node inline-script syntax check for index.html
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
drift_trigger_build: rows=32202 stocks=5143 none=27052 watch=5150 reevaluate=0 reoptimize=0 disable_candidate=0 latest_data_date=2026-05-19 actions={"none": 27052, "watch_candidate": 7, "watch_low_signal": 5143}
index inline scripts: syntax ok
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

API verification:

```text
/api/parameter-search ready=True
drift.ready=True
drift.row_count=32202
drift.stock_count=5143
drift.none_count=27052
drift.watch_count=5150
drift.reevaluate_count=0
drift.reoptimize_count=0
drift.disable_candidate_count=0
```

Implementation notes:

- The drift state is currently a conservative queue and visibility layer. It does not run Optuna automatically and does not write production parameters.
- Dirty incremental rows become `reevaluate`; low-signal rows or weak candidate validation become `watch`.
- `reoptimize` remains reserved for a later rule that converts repeated or severe drift into a local Optuna rerun queue.

Updated residual risk:

- Drift Trigger is now queryable, but it is not yet a live evaluator or automatic retraining controller.
- Run Registry is still needed before adding UI task-start buttons.
- Production merge remains blocked until full-market coverage and aggregate review are complete.

## 2026-05-20 Follow-up: One-Hundred-Fifteenth Batch and Crash Recovery Checkpoint

Scope:

- Offset 2280, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2300 stocks and 11500 `(stock_code, formula_id)` rows.
- Installed `codex-complexity-optimizer` globally and verified the skill path under `/Users/dp/.codex/skills/complexity-optimizer`.
- Added `scripts/workflow_checkpoint.py` for crash/reboot recovery.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2280 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
python scripts/research_cache_build.py
python scripts/incremental_eval_build.py
python scripts/drift_trigger_build.py
python scripts/workflow_checkpoint.py
npm install -g codex-complexity-optimizer
```

Batch run:

```text
formula_local_optuna_batch:done rows=11500 new_rows=100 elapsed=21.9s
formula_local_optuna_batch:done rows=11500 new_rows=0 elapsed=0.3s
```

Cumulative adoption and state result:

```text
formula_local_optuna_adoption: rows=11500 candidates=538 rejected=10962
formula_local_optuna_merge_plan: rows=11500 replacements=538
research_cache_build: rows=32298 stocks=5143 local_optuna=10996 production=21302 candidates=538 data_latest_date=2026-05-19
incremental_eval_build: rows=32298 stocks=5143 clean=32298 dirty=0 source_cache=32298 target_data_date=2026-05-19 dirty_reasons={}
drift_trigger_build: rows=32298 stocks=5143 none=27129 watch=5169 reevaluate=0 reoptimize=0 disable_candidate=0 latest_data_date=2026-05-19 actions={"none": 27129, "watch_candidate": 7, "watch_low_signal": 5162}
workflow_checkpoint: covered=2300 next_offset=2300 rows=11500 candidates=538 replacements=538 missing_without_reason=0
```

Cumulative candidate distribution:

```text
activity_breakout: 311
gs_raw_buy: 103
gs_pullback_confirm: 71
volume_base_breakout: 53
```

New replacement examples from the one-hundred-fifteenth batch:

```text
300863 activity_breakout score_delta=25.04 validation_delta=23.32 sell_rule=fixed_5 holding_days=5
300854 volume_base_breakout score_delta=16.96 validation_delta=4.18 sell_rule=fixed_5 holding_days=5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1432
missing_baseline_result / missing_optuna_result = 462
ok / missing_optuna_result = 42
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Recovery checkpoint:

```text
analysis/workflow_checkpoint.json
analysis/workflow_checkpoint.md
next_offset=2300
codegraph_ready=True
complexity_optimizer_skill_ready=True
```

API/UI verification:

```text
/api/parameter-search ready=True
/api/parameter-search local_optuna.batch row_count=11500 candidate_count=538 rejected_count=10962
/api/parameter-search local_optuna.batch.merge_plan replacement_count=538 dry_run=True
/api/parameter-search management.full_initialization covered_stock_count=2300 total_stock_count=5201 next_offset=2300
/api/parameter-search management.research_cache row_count=32298 local_optuna_rows=10996 candidate_rows=538
/api/parameter-search management.incremental_eval clean_count=32298 dirty_count=0
/api/parameter-search management.drift none_count=27129 watch_count=5169 reevaluate_count=0 reoptimize_count=0
```

Regression verification:

```text
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
node inline-script syntax check for index.html
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
index inline scripts: syntax ok
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- Checkpoint recovery is now available, but it is a generated file workflow, not a daemon. It must be regenerated after each batch/state change.
- CodeGraph and complexity optimizer are available, but the first real complexity report has not yet been run.
- Production merge remains blocked until full-market coverage and aggregate review are complete.

## 2026-05-20 Follow-up: One-Hundred-Sixteenth Batch

Scope:

- Offset 2300, next 20 active market stocks.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2320 stocks and 11600 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2300 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
python scripts/research_cache_build.py
python scripts/incremental_eval_build.py
python scripts/drift_trigger_build.py
python scripts/workflow_checkpoint.py
```

Batch run:

```text
formula_local_optuna_batch:done rows=11600 new_rows=100
formula_local_optuna_batch:done rows=11600 new_rows=0
```

Cumulative adoption and state result:

```text
formula_local_optuna_adoption: rows=11600 candidates=539 rejected=11061
formula_local_optuna_merge_plan: rows=11600 replacements=539
research_cache_build: rows=32394 stocks=5143 local_optuna=11092 production=21302 candidates=539 data_latest_date=2026-05-19
incremental_eval_build: rows=32394 stocks=5143 clean=32394 dirty=0 source_cache=32394 target_data_date=2026-05-19 dirty_reasons={}
drift_trigger_build: rows=32394 stocks=5143 none=27203 watch=5191 reevaluate=0 reoptimize=0 disable_candidate=0 latest_data_date=2026-05-19 actions={"none": 27203, "watch_candidate": 7, "watch_low_signal": 5184}
workflow_checkpoint: covered=2320 next_offset=2320 rows=11600 candidates=539 replacements=539 missing_without_reason=0
```

Cumulative candidate distribution:

```text
activity_breakout: 311
gs_raw_buy: 104
gs_pullback_confirm: 71
volume_base_breakout: 53
```

New replacement example from the one-hundred-sixteenth batch:

```text
300883 gs_raw_buy score_delta=7.018143 validation_delta=13.157025 sell_rule=fixed_20 holding_days=20
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1443
missing_baseline_result / missing_optuna_result = 465
ok / missing_optuna_result = 43
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing investigation added by this batch:

```text
baseline-only no row in stock_formula_best.csv: 11
baseline no row plus Optuna no entry signal: 3
Optuna-only no entry signal: 1
```

Recovery checkpoint:

```text
analysis/workflow_checkpoint.json
analysis/workflow_checkpoint.md
next_offset=2320
codegraph_ready=True
complexity_optimizer_skill_ready=True
```

API/UI verification:

```text
/api/parameter-search ready=True
/api/parameter-search local_optuna.batch row_count=11600 candidate_count=539 rejected_count=11061
/api/parameter-search local_optuna.batch.merge_plan replacement_count=539 dry_run=True
/api/parameter-search management.full_initialization covered_stock_count=2320 total_stock_count=5201 next_offset=2320
/api/parameter-search management.research_cache row_count=32394 local_optuna_rows=11092 candidate_rows=539
/api/parameter-search management.incremental_eval clean_count=32394 dirty_count=0
/api/parameter-search management.drift none_count=27203 watch_count=5191 reevaluate_count=0 reoptimize_count=0
```

Regression verification:

```text
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
node inline-script syntax check for index.html
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
index inline scripts: syntax ok
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- Checkpoint recovery is available and current at offset 2320, but it remains a generated file workflow rather than a daemon.
- CodeGraph and complexity optimizer are available; the first report-only collaboration audit has been written to `analysis/complexity_codegraph_audit.md`.
- Production merge remains blocked until full-market coverage and aggregate review are complete.

## 2026-05-20 Follow-up: Complexity Optimizer and CodeGraph Governance Audit

Scope:

- Report-only complexity and dependency-governance baseline.
- No strategy semantics, API behavior, UI behavior, Optuna artifacts, or production merge files were changed by this audit.

Commands:

```text
python3 /Users/dp/.codex/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/bestchoice --format markdown
sqlite3 .codegraph/codegraph.db '.tables'
sqlite3 .codegraph/codegraph.db '.schema'
sqlite3 .codegraph/codegraph.db "select language, count(*) as files, sum(size) as bytes, sum(node_count) as nodes from files group by language order by bytes desc;"
sqlite3 .codegraph/codegraph.db "select kind, count(*) from nodes group by kind order by count(*) desc;"
sqlite3 .codegraph/codegraph.db "select count(*) from edges; select kind, count(*) from edges group by kind order by count(*) desc;"
```

CodeGraph baseline:

```text
indexed languages: python
indexed files: 6
indexed nodes: 192
edges: 375 total, contains=186, calls=164, imports=25
```

Key findings:

- CodeGraph is usable for first-pass dependency checks on indexed Python core files, but it is incomplete for the current project because `scripts/formula_local_optuna_batch.py` and `index.html` are not covered.
- Complexity scanner flagged repeated scans/sorts in `compute.py`, formula-level scan hotspots in `formula_engine.py`, repeated sell-rule evaluation in `scripts/formula_local_optuna.py`, and repeated API aggregation passes in `main.py`.
- The safest first implementation target is `main.py` API aggregation cleanup because it should preserve strategy semantics and mainly reduces repeated summary work.

Governance decision:

- Future complexity work must start with a CodeGraph coverage check.
- Any accepted optimization must include a complexity audit note, explicit strategy-semantics statement, before/after verification commands, and no production merge side effects.
- The detailed baseline is stored in `analysis/complexity_codegraph_audit.md`.

## 2026-05-20 Follow-up: One-Hundred-Seventeenth Batch

Scope:

- Offset 2320, next 20 active market stocks: `300885` through `300904`.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2340 stocks and 11700 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2320 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_batch.py --offset 2320 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
python scripts/research_cache_build.py
python scripts/incremental_eval_build.py
python scripts/drift_trigger_build.py
python scripts/workflow_checkpoint.py
```

Batch run:

```text
formula_local_optuna_batch:done rows=11700 new_rows=100 elapsed=21.3s
formula_local_optuna_batch:done rows=11700 new_rows=0 elapsed=0.2s
```

Cumulative adoption and state result:

```text
formula_local_optuna_adoption: rows=11700 candidates=542 rejected=11158
formula_local_optuna_merge_plan: rows=11700 replacements=542
research_cache_build: rows=32487 stocks=5143 local_optuna=11185 production=21302 candidates=542 data_latest_date=2026-05-19
incremental_eval_build: rows=32487 stocks=5143 clean=32487 dirty=0 source_cache=32487 target_data_date=2026-05-19 dirty_reasons={}
drift_trigger_build: rows=32487 stocks=5143 none=27279 watch=5208 reevaluate=0 reoptimize=0 disable_candidate=0 latest_data_date=2026-05-19 actions={"none": 27279, "watch_candidate": 7, "watch_low_signal": 5201}
workflow_checkpoint: covered=2340 next_offset=2340 rows=11700 candidates=542 replacements=542 missing_without_reason=0
```

Cumulative candidate distribution:

```text
activity_breakout: 313
gs_raw_buy: 105
gs_pullback_confirm: 71
volume_base_breakout: 53
```

New replacement examples from the one-hundred-seventeenth batch:

```text
300903 activity_breakout score_delta=12.164978 validation_delta=7.182812 sell_rule=fixed_60 holding_days=60
300887 gs_raw_buy score_delta=8.551841 validation_delta=6.421164 sell_rule=fixed_30 holding_days=30
300887 activity_breakout score_delta=3.639778 validation_delta=3.002570 sell_rule=formula_exit_or_5 holding_days=5
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1454
missing_baseline_result / missing_optuna_result = 471
ok / missing_optuna_result = 44
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing investigation added by this batch:

```text
baseline-only no row in stock_formula_best.csv: 11
baseline no row plus Optuna no entry signal: 6
Optuna-only no entry signal: 1
```

Recovery checkpoint:

```text
analysis/workflow_checkpoint.json
analysis/workflow_checkpoint.md
next_offset=2340
codegraph_ready=True
complexity_optimizer_skill_ready=True
```

API/UI verification:

```text
/api/parameter-search ready=True
/api/parameter-search local_optuna.batch row_count=11700 candidate_count=542 rejected_count=11158
/api/parameter-search local_optuna.batch.merge_plan replacement_count=542 dry_run=True
/api/parameter-search management.full_initialization covered_stock_count=2340 total_stock_count=5201 next_offset=2340
/api/parameter-search management.research_cache row_count=32487 local_optuna_rows=11185 candidate_rows=542
/api/parameter-search management.incremental_eval clean_count=32487 dirty_count=0
/api/parameter-search management.drift none_count=27279 watch_count=5208 reevaluate_count=0 reoptimize_count=0
```

Regression verification:

```text
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
node inline-script syntax check for index.html
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
index inline scripts: syntax ok
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- Checkpoint recovery is current at offset 2340, but it remains a generated file workflow rather than a daemon.
- CodeGraph is still incomplete for `scripts/formula_local_optuna_batch.py` and `index.html`; dependency-sensitive optimization still requires graph refresh or manual dependency tracing.
- Production merge remains blocked until full-market coverage and aggregate review are complete.

## 2026-05-20 Follow-up: One-Hundred-Eighteenth Batch

Scope:

- Offset 2340, next 20 active market stocks: `300905`, `300906`, `300907`, `300908`, `300909`, `300910`, `300911`, `300912`, `300913`, `300915`, `300916`, `300917`, `300918`, `300919`, `300920`, `300921`, `300922`, `300923`, `300925`, `300926`.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2360 stocks and 11800 `(stock_code, formula_id)` rows.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2340 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_batch.py --offset 2340 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
python scripts/research_cache_build.py
python scripts/incremental_eval_build.py
python scripts/drift_trigger_build.py
python scripts/workflow_checkpoint.py
```

Batch run:

```text
formula_local_optuna_batch:done rows=11800 new_rows=100 elapsed=21.4s
formula_local_optuna_batch:done rows=11800 new_rows=0 elapsed=0.2s
```

Cumulative adoption and state result:

```text
formula_local_optuna_adoption: rows=11800 candidates=544 rejected=11256
formula_local_optuna_merge_plan: rows=11800 replacements=544
research_cache_build: rows=32584 stocks=5143 local_optuna=11282 production=21302 candidates=544 data_latest_date=2026-05-19
incremental_eval_build: rows=32584 stocks=5143 clean=32584 dirty=0 source_cache=32584 target_data_date=2026-05-19 dirty_reasons={}
drift_trigger_build: rows=32584 stocks=5143 none=27355 watch=5229 reevaluate=0 reoptimize=0 disable_candidate=0 latest_data_date=2026-05-19 actions={"none": 27355, "watch_candidate": 7, "watch_low_signal": 5222}
workflow_checkpoint: covered=2360 next_offset=2360 rows=11800 candidates=544 replacements=544 missing_without_reason=0
```

Cumulative candidate distribution:

```text
activity_breakout: 315
gs_raw_buy: 105
gs_pullback_confirm: 71
volume_base_breakout: 53
```

New replacement examples from the one-hundred-eighteenth batch:

```text
300911 activity_breakout score_delta=24.471108 validation_delta=34.527900 sell_rule=fixed_30 holding_days=30
300910 activity_breakout score_delta=8.260848 validation_delta=10.246352 sell_rule=formula_exit_or_30 holding_days=30
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1463
missing_baseline_result / missing_optuna_result = 474
ok / missing_optuna_result = 44
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing investigation added by this batch:

```text
baseline-only no row in stock_formula_best.csv: 9
baseline no row plus Optuna no entry signal: 3
Optuna-only no entry signal: 0
```

Recovery checkpoint:

```text
analysis/workflow_checkpoint.json
analysis/workflow_checkpoint.md
next_offset=2360
codegraph_ready=True
complexity_optimizer_skill_ready=True
```

API/UI verification:

```text
/api/parameter-search ready=True
/api/parameter-search local_optuna.batch row_count=11800 candidate_count=544 rejected_count=11256
/api/parameter-search local_optuna.batch.merge_plan replacement_count=544 dry_run=True
/api/parameter-search management.full_initialization covered_stock_count=2360 total_stock_count=5201 next_offset=2360
/api/parameter-search management.research_cache row_count=32584 local_optuna_rows=11282 candidate_rows=544
/api/parameter-search management.incremental_eval clean_count=32584 dirty_count=0
/api/parameter-search management.drift none_count=27355 watch_count=5229 reevaluate_count=0 reoptimize_count=0
```

Regression verification:

```text
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
node inline-script syntax check for index.html
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
index inline scripts: syntax ok
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Updated residual risk:

- Checkpoint recovery is current at offset 2360, but it remains a generated file workflow rather than a daemon.
- CodeGraph is still incomplete for `scripts/formula_local_optuna_batch.py` and `index.html`; dependency-sensitive optimization still requires graph refresh or manual dependency tracing.
- Production merge remains blocked until full-market coverage and aggregate review are complete.

## 2026-05-20 Follow-up: One-Hundred-Nineteenth Batch and Latest Snapshot Recovery

Scope:

- Offset 2360, next 20 active market stocks: `300927`, `300928`, `300929`, `300930`, `300931`, `300932`, `300933`, `300935`, `300936`, `300937`, `300938`, `300939`, `300940`, `300941`, `300942`, `300943`, `300945`, `300946`, `300947`, `300948`.
- 5 formula strategies.
- 24 Optuna trials per stock/formula.
- The cumulative batch artifact now covers 2380 stocks and 11900 `(stock_code, formula_id)` rows.
- Recovery workflow upgraded from checkpoint-only to checkpoint plus latest-only snapshot.

Command:

```text
python scripts/formula_local_optuna_batch.py --offset 2360 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_batch.py --offset 2360 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
python scripts/research_cache_build.py
python scripts/incremental_eval_build.py
python scripts/drift_trigger_build.py
python scripts/workflow_checkpoint.py
```

Batch run:

```text
formula_local_optuna_batch:done rows=11900 new_rows=100 elapsed=22.5s
formula_local_optuna_batch:done rows=11900 new_rows=0 elapsed=0.2s
```

Cumulative adoption and state result:

```text
formula_local_optuna_adoption: rows=11900 candidates=551 rejected=11349
formula_local_optuna_merge_plan: rows=11900 replacements=551
research_cache_build: rows=32679 stocks=5143 local_optuna=11377 production=21302 candidates=551 data_latest_date=2026-05-19
incremental_eval_build: rows=32679 stocks=5143 clean=32679 dirty=0 source_cache=32679 target_data_date=2026-05-19 dirty_reasons={}
drift_trigger_build: rows=32679 stocks=5143 none=27425 watch=5254 reevaluate=0 reoptimize=0 disable_candidate=0 latest_data_date=2026-05-19 actions={"none": 27425, "watch_candidate": 8, "watch_low_signal": 5246}
workflow_checkpoint: covered=2380 next_offset=2380 rows=11900 candidates=551 replacements=551 missing_without_reason=0
```

Cumulative candidate distribution:

```text
activity_breakout: 319
gs_raw_buy: 105
gs_pullback_confirm: 72
volume_base_breakout: 55
```

New replacement examples from the one-hundred-nineteenth batch:

```text
300928 activity_breakout score_delta=23.307285 validation_delta=8.176972 sell_rule=formula_exit_or_20 holding_days=20
300932 volume_base_breakout score_delta=21.634701 validation_delta=14.180375 sell_rule=fixed_20 holding_days=20
300939 volume_base_breakout score_delta=16.309147 validation_delta=10.318688 sell_rule=fixed_10 holding_days=10
300938 activity_breakout score_delta=13.713238 validation_delta=8.713808 sell_rule=formula_exit_or_5 holding_days=5
300942 activity_breakout score_delta=13.009534 validation_delta=29.995857 sell_rule=formula_exit_or_5 holding_days=5
300947 activity_breakout score_delta=11.689598 validation_delta=17.819127 sell_rule=fixed_60 holding_days=60
300936 gs_pullback_confirm score_delta=3.658992 validation_delta=0.000000 sell_rule=fixed_60 holding_days=60
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1472
missing_baseline_result / missing_optuna_result = 478
ok / missing_optuna_result = 45
missing_without_reason = 0
cumulative_missing_without_reason = 0
```

Missing investigation added by this batch:

```text
baseline-only no row in stock_formula_best.csv: 9
baseline no row plus Optuna no entry signal: 4
Optuna-only no entry signal: 1
```

Recovery checkpoint and latest snapshot:

```text
analysis/workflow_checkpoint.json
analysis/workflow_checkpoint.md
analysis/recovery_snapshot/latest
next_offset=2380
consistency.ready=True
codegraph_ready=True
complexity_optimizer_skill_ready=True
```

Snapshot policy:

```text
Only analysis/recovery_snapshot/latest is kept.
Old snapshots are deleted before writing the new one.
Large CSV and DuckDB artifacts are not copied; artifact_manifest.json records their size and timestamp.
resume.sh contains the next recovery command.
verify.sh contains the verification commands.
```

API/UI verification:

```text
/api/parameter-search ready=True
/api/parameter-search local_optuna.batch row_count=11900 candidate_count=551 rejected_count=11349
/api/parameter-search local_optuna.batch.merge_plan replacement_count=551 dry_run=True
/api/parameter-search management.full_initialization covered_stock_count=2380 total_stock_count=5201 next_offset=2380
/api/parameter-search management.research_cache row_count=32679 local_optuna_rows=11377 candidate_rows=551
/api/parameter-search management.incremental_eval clean_count=32679 dirty_count=0
/api/parameter-search management.drift none_count=27425 watch_count=5254 reevaluate_count=0 reoptimize_count=0
```

Regression verification:

```text
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
node inline-script syntax check for index.html
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

Observed output:

```text
index inline scripts: syntax ok
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit exited 0 but reported ready_formula_caches=0
```

Updated residual risk:

- `strategy_rebuild_audit.py` now exits 0 while reporting `ready_formula_caches=0`; this must be fixed before treating the strategy rebuild audit as a strong gate.
- Checkpoint recovery is current at offset 2380 and latest snapshot recovery is available.
- CodeGraph is still incomplete for `scripts/formula_local_optuna_batch.py` and `index.html`; dependency-sensitive optimization still requires graph refresh or manual dependency tracing.
- Production merge remains blocked until full-market coverage and aggregate review are complete.

## 2026-05-20 Follow-up: One-Hundred-Twentieth Batch And Recovery Fix

Batch 120 command:

```text
python scripts/formula_local_optuna_batch.py --offset 2380 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
```

Observed batch outcome:

```text
offset=2380
covered_stocks=2400
batch_rows=12000
candidate_count=556
rejected_count=11444
dry_run_replacements=556
resume_new_rows=0
```

New accepted examples:

```text
300962 volume_base_breakout score_delta=19.310502 validation_delta=3.378762 sell_rule=formula_exit_or_5 holding_days=5
300967 volume_base_breakout score_delta=10.143636 validation_delta=4.290391 sell_rule=fixed_15 holding_days=15
300959 activity_breakout score_delta=5.604723 validation_delta=22.791669 sell_rule=fixed_60 holding_days=60
300969 volume_base_breakout score_delta=3.260598 validation_delta=3.277577 sell_rule=fixed_30 holding_days=30
300964 activity_breakout score_delta=3.220608 validation_delta=0.928190 sell_rule=fixed_60 holding_days=60
```

Cumulative accepted distribution:

```text
activity_breakout=321
gs_raw_buy=105
gs_pullback_confirm=72
volume_base_breakout=58
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1488
missing_baseline_result / missing_optuna_result = 480
ok / missing_optuna_result = 45
missing_without_reason = 0
```

State-store refresh after batch 120:

```text
Research Cache: rows=32777 stocks=5143 local_optuna=11475 production=21302 candidates=556 data_latest_date=2026-05-19
Incremental Evaluator: rows=32777 stocks=5143 clean=32777 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=32777 stocks=5143 none=27502 watch=5275 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2400 next_offset=2400 rows=12000 candidates=556 replacements=556 missing_without_reason=0
```

Restart recovery and cache-gate fix:

```text
python scripts/strategy_rebuild_audit.py
strategy_rebuild_audit:failed missing_formula_caches=formula_activity_breakout,formula_volume_base_breakout
strategy_rebuild_audit: ready_formula_caches=3

python scripts/compute_formula_caches.py --only formula_activity_breakout formula_volume_base_breakout
compute_formula_cache:done formula_activity_breakout stocks=5201 with_signal=5131
compute_formula_cache:done formula_volume_base_breakout stocks=5201 with_signal=5131

python scripts/strategy_rebuild_audit.py
strategy_rebuild_audit: ready_formula_caches=5

python scripts/workflow_checkpoint.py --brief
consistency.ready=True
formula_caches=5/5 ready
next_action=run_next_batch
next_offset=2400
```

Updated residual risk:

- The stale formula-cache blocker is resolved and `strategy_rebuild_audit.py` is now a useful nonzero gate for missing/stale formula caches.
- Disk space remains low, observed around 5.1 GiB after cache recovery; continue only in small batches and avoid full rebuilds until more space is available.
- CodeGraph is still incomplete for `scripts/formula_local_optuna_batch.py` and `index.html`; dependency-sensitive optimization still requires graph refresh or manual dependency tracing.
- Production merge remains blocked until full-market coverage and aggregate review are complete.

## 2026-05-20 Follow-up: One-Hundred-Twenty-First Batch

Batch 121 command:

```text
python scripts/formula_local_optuna_batch.py --offset 2400 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
```

Observed batch outcome:

```text
offset=2400
covered_stocks=2420
batch_rows=12100
new_rows=100
candidate_count=565
rejected_count=11535
dry_run_replacements=565
resume_new_rows=0
```

New accepted examples:

```text
300983 gs_pullback_confirm score_delta=35.123191 validation_delta=2.254508 sell_rule=fixed_60 holding_days=60
300970 activity_breakout score_delta=20.254405 validation_delta=0.094126 sell_rule=fixed_15 holding_days=15
300980 activity_breakout score_delta=15.536330 validation_delta=20.031249 sell_rule=fixed_30 holding_days=30
300976 activity_breakout score_delta=14.860870 validation_delta=6.826665 sell_rule=formula_exit_or_20 holding_days=20
300977 activity_breakout score_delta=11.442021 validation_delta=5.896447 sell_rule=formula_exit_or_5 holding_days=5
300971 activity_breakout score_delta=10.294078 validation_delta=14.479917 sell_rule=formula_exit_or_10 holding_days=10
300976 gs_raw_buy score_delta=9.726234 validation_delta=14.100713 sell_rule=fixed_20 holding_days=20
300981 volume_base_breakout score_delta=8.345323 validation_delta=3.662653 sell_rule=fixed_5 holding_days=5
300975 gs_pullback_confirm score_delta=4.438066 validation_delta=0.151967 sell_rule=fixed_60 holding_days=60
```

Cumulative accepted distribution:

```text
activity_breakout=326
gs_raw_buy=106
gs_pullback_confirm=74
volume_base_breakout=59
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1500
missing_baseline_result / missing_optuna_result = 483
ok / missing_optuna_result = 45
missing_without_reason = 0
```

State-store refresh after batch 121:

```text
Research Cache: rows=32874 stocks=5143 local_optuna=11572 production=21302 candidates=565 data_latest_date=2026-05-19
Incremental Evaluator: rows=32874 stocks=5143 clean=32874 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=32874 stocks=5143 none=27579 watch=5295 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2420 next_offset=2420 rows=12100 candidates=565 replacements=565 missing_without_reason=0
```

Implementation fix during batch 121:

```text
scripts/research_cache_build.py
```

The Research Cache builder now resolves relative CLI paths against the project root before calling `relative_to(ROOT)`. This keeps `source_artifact` stable as a project-relative path and prevents refresh failure when commands pass `analysis/...csv`.

Verification:

```text
python -m py_compile scripts/research_cache_build.py scripts/workflow_checkpoint.py scripts/strategy_rebuild_audit.py
python scripts/strategy_rebuild_audit.py
python scripts/workflow_checkpoint.py --brief
```

Observed output:

```text
strategy_rebuild_audit: ready_formula_caches=5
consistency.ready=True
formula_caches=5/5 ready
next_action=run_next_batch
next_offset=2420
```

## 2026-05-20 Follow-up: One-Hundred-Twenty-Second Batch

Batch 122 command:

```text
python scripts/formula_local_optuna_batch.py --offset 2420 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
```

Observed batch outcome:

```text
offset=2420
covered_stocks=2440
batch_rows=12200
new_rows=100
candidate_count=570
rejected_count=11630
dry_run_replacements=570
resume_new_rows=0
```

New accepted examples:

```text
300998 gs_pullback_confirm score_delta=28.755830 validation_delta=0.000000 sell_rule=formula_exit_or_60 holding_days=60
300992 activity_breakout score_delta=27.378002 validation_delta=34.392084 sell_rule=fixed_60 holding_days=60
301003 gs_pullback_confirm score_delta=19.382303 validation_delta=13.743872 sell_rule=fixed_30 holding_days=30
301010 activity_breakout score_delta=15.431924 validation_delta=8.360855 sell_rule=fixed_5 holding_days=5
301007 activity_breakout score_delta=5.647049 validation_delta=11.062806 sell_rule=fixed_30 holding_days=30
```

Cumulative accepted distribution:

```text
activity_breakout=329
gs_raw_buy=106
gs_pullback_confirm=76
volume_base_breakout=59
```

Missing-result status combinations:

```text
missing_baseline_result / ok = 1513
missing_baseline_result / missing_optuna_result = 488
ok / missing_optuna_result = 46
missing_without_reason = 0
```

State-store refresh after batch 122:

```text
Research Cache: rows=32968 stocks=5143 local_optuna=11666 production=21302 candidates=570 data_latest_date=2026-05-19
Incremental Evaluator: rows=32968 stocks=5143 clean=32968 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=32968 stocks=5143 none=27655 watch=5313 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2440 next_offset=2440 rows=12200 candidates=570 replacements=570 missing_without_reason=0
```

## 2026-05-20 Follow-up: One-Hundred-Twenty-Third Batch Partial

Batch 123 command:

```text
python scripts/formula_local_optuna_batch.py --offset 2440 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
```

Observed main-run outcome:

```text
offset=2440
batch_rows=12300
new_rows=100
elapsed=32.4s
```

Adoption and dry-run merge plan were refreshed:

```text
formula_local_optuna_adoption: rows=12300 candidates=572 rejected=11728
formula_local_optuna_merge_plan: rows=12300 replacements=572
```

New accepted examples:

```text
301026 activity_breakout score_delta=13.765389 validation_delta=10.661284 sell_rule=fixed_30 holding_days=30
301012 gs_raw_buy score_delta=4.204524 validation_delta=1.561855 sell_rule=fixed_10 holding_days=10
```

Cumulative accepted distribution:

```text
activity_breakout=330
gs_raw_buy=107
gs_pullback_confirm=76
volume_base_breakout=59
```

Missing-result status combinations after the batch artifact refresh:

```text
missing_baseline_result / ok = 1526
missing_baseline_result / missing_optuna_result = 492
ok / missing_optuna_result = 46
```

Blocked finalization:

```text
_duckdb.IOException: Could not set lock on file "/Users/dp/Documents/M/stock/chunkymonkey/data/market.duckdb"
lock holder: backend/scripts/run_paper_sim_v2.py --variant champion_baseline_20260520T102611 --config-path backend/config/paper_sim_ml_score_champion_baseline.yaml --start 2025-01-02 --end 2026-04-13
PID observed: 571
```

Required recovery before continuing to batch 124:

```text
python scripts/formula_local_optuna_batch.py --offset 2440 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/research_cache_build.py --adoption analysis/formula_local_optuna_batch_adoption.csv --merge-plan analysis/formula_local_optuna_batch_merge_plan.csv --production analysis/stock_formula_best.csv
python scripts/incremental_eval_build.py --research-cache analysis/research_cache.duckdb
python scripts/drift_trigger_build.py --research-cache analysis/research_cache.duckdb --incremental-eval analysis/incremental_eval.duckdb
python scripts/workflow_checkpoint.py --brief
```

Do not run offset `2460` until these recovery commands succeed and checkpoint reports `covered=2460`, `next_offset=2460`, and `consistency.ready=True`.

Checkpoint improvement after observing the lock:

```text
python scripts/workflow_checkpoint.py --brief
market_db_lock_holders: 1
market_db_lock: pid=571 process=... backend/scripts/run_paper_sim_v2.py --variant champion_baseline_20260520T102611 ...
next_action: wait_external_duckdb_lock
```

`scripts/workflow_checkpoint.py` now detects external `market.duckdb` lock holders with `lsof` and surfaces `wait_external_duckdb_lock` before cache rebuild or next-batch commands. This prevents a recovery session from repeatedly running commands that are expected to fail while another process owns the DuckDB lock.

## 2026-05-20 Follow-up: One-Hundred-Twenty-Fourth And One-Hundred-Twenty-Fifth Batches

Batch 124 outcome:

```text
offset=2460
covered_stocks=2480
batch_rows=12400
new_rows=100
candidate_count=579
rejected_count=11821
dry_run_replacements=579
resume_new_rows=0
```

Batch 124 new accepted examples:

```text
301047 activity_breakout score_delta=18.688351 validation_delta=11.823005 sell_rule=formula_exit_or_5 holding_days=5
301046 activity_breakout score_delta=12.502891 validation_delta=5.184142 sell_rule=fixed_10 holding_days=10
301047 gs_raw_buy score_delta=12.410255 validation_delta=5.991268 sell_rule=fixed_10 holding_days=10
301037 activity_breakout score_delta=10.945509 validation_delta=5.349570 sell_rule=formula_exit_or_10 holding_days=10
301039 gs_raw_buy score_delta=9.277158 validation_delta=5.714517 sell_rule=fixed_15 holding_days=15
301050 gs_raw_buy score_delta=8.581407 validation_delta=3.856808 sell_rule=formula_exit_or_10 holding_days=10
301049 activity_breakout score_delta=5.715888 validation_delta=9.242868 sell_rule=fixed_20 holding_days=20
```

Batch 125 outcome:

```text
offset=2480
covered_stocks=2500
batch_rows=12500
new_rows=100
candidate_count=584
rejected_count=11916
dry_run_replacements=584
resume_new_rows=0
```

Batch 125 new accepted examples:

```text
301059 activity_breakout score_delta=19.257569 validation_delta=8.609009 sell_rule=fixed_10 holding_days=10
301061 activity_breakout score_delta=11.541969 validation_delta=1.960609 sell_rule=fixed_60 holding_days=60
301065 activity_breakout score_delta=9.130005 validation_delta=6.383111 sell_rule=fixed_5 holding_days=5
301069 activity_breakout score_delta=9.036333 validation_delta=13.536232 sell_rule=fixed_5 holding_days=5
301069 gs_raw_buy score_delta=7.147341 validation_delta=5.571355 sell_rule=formula_exit_or_5 holding_days=5
```

Cumulative accepted distribution after batch 125:

```text
activity_breakout=338
gs_raw_buy=111
gs_pullback_confirm=76
volume_base_breakout=59
```

Missing-result status combinations after batch 125:

```text
missing_baseline_result / ok = 1550
missing_baseline_result / missing_optuna_result = 500
ok / missing_optuna_result = 47
missing_without_reason = 0
```

State-store refresh after batch 125:

```text
Research Cache: rows=33255 stocks=5143 local_optuna=11953 production=21302 candidates=584 data_latest_date=2026-05-19
Incremental Evaluator: rows=33255 stocks=5143 clean=33255 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=33255 stocks=5143 none=27882 watch=5373 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2500 next_offset=2500 rows=12500 candidates=584 replacements=584 missing_without_reason=0
```

## 2026-05-20 Follow-up: One-Hundred-Twenty-Sixth Batch

Batch 126 outcome:

```text
offset=2500
covered_stocks=2520
batch_rows=12600
new_rows=100
candidate_count=589
rejected_count=12011
dry_run_replacements=589
resume_new_rows=0
```

Batch 126 new accepted examples:

```text
301087 gs_pullback_confirm score_delta=64.659553 validation_delta=84.562783 sell_rule=fixed_15 holding_days=15
301095 activity_breakout score_delta=19.254996 validation_delta=24.959523 sell_rule=fixed_5 holding_days=5
301078 activity_breakout score_delta=18.220683 validation_delta=17.262853 sell_rule=formula_exit_or_60 holding_days=60
301083 activity_breakout score_delta=10.440717 validation_delta=9.179966 sell_rule=formula_exit_or_5 holding_days=5
301092 activity_breakout score_delta=6.224279 validation_delta=1.926954 sell_rule=fixed_10 holding_days=10
```

Cumulative accepted distribution after batch 126:

```text
activity_breakout=342
gs_raw_buy=111
gs_pullback_confirm=77
volume_base_breakout=59
```

Missing-result status combinations after batch 126:

```text
missing_baseline_result / ok = 1561
missing_baseline_result / missing_optuna_result = 506
ok / missing_optuna_result = 48
missing_without_reason = 0
```

State-store refresh after batch 126:

```text
Research Cache: rows=33348 stocks=5143 local_optuna=12046 production=21302 candidates=589 data_latest_date=2026-05-19
Incremental Evaluator: rows=33348 stocks=5143 clean=33348 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=33348 stocks=5143 none=27955 watch=5393 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2520 next_offset=2520 rows=12600 candidates=589 replacements=589 missing_without_reason=0
```

Post-batch-126 external lock:

```text
market_db_lock_holders=1
PID=73446
backend/scripts/run_paper_sim_v2.py --variant champion_minhold5_20260520_105535 --start 2025-01-02 --config-path backend/config/paper_sim_ml_score_champion_minhold5.yaml
next_action=wait_external_duckdb_lock
```

Do not run offset `2520` while this lock is active. After the paper simulation exits, run `python scripts/workflow_checkpoint.py --brief`; if formula caches still report stale, rebuild them before continuing.

## 2026-05-20 Follow-up: One-Hundred-Twenty-Seventh Batch

The external paper simulation lock from PID `73446` was released before this batch resumed. `python scripts/workflow_checkpoint.py --brief` reported `consistency.ready=True`, `formula_caches=5/5 ready`, `market_db_lock_holders=0`, and `next_action=run_next_batch`.

Batch 127 outcome:

```text
offset=2520
covered_stocks=2540
batch_rows=12700
new_rows=100
candidate_count=601
rejected_count=12099
dry_run_replacements=601
resume_new_rows=0
```

Batch 127 new accepted examples:

```text
301102 activity_breakout score_delta=29.447994 validation_delta=11.725974
301108 activity_breakout score_delta=26.806008 validation_delta=24.835242
301100 activity_breakout score_delta=20.486232 validation_delta=0.567293
301117 activity_breakout score_delta=17.782209 validation_delta=25.666514
301118 activity_breakout score_delta=11.544408 validation_delta=12.813297
301106 activity_breakout score_delta=11.091352 validation_delta=0.459277
301115 activity_breakout score_delta=10.189109 validation_delta=8.055805
301112 activity_breakout score_delta=10.151285 validation_delta=7.145613
301110 activity_breakout score_delta=10.130784 validation_delta=6.272590
301119 gs_pullback_confirm score_delta=6.274784 validation_delta=1.407649
301105 volume_base_breakout score_delta=5.870710 validation_delta=1.002201
301119 gs_raw_buy score_delta=4.065097 validation_delta=2.963672
```

Cumulative accepted distribution after batch 127:

```text
activity_breakout=351
gs_raw_buy=112
gs_pullback_confirm=78
volume_base_breakout=60
```

Missing-result status combinations after batch 127:

```text
missing_baseline_result / ok = 1573
missing_baseline_result / missing_optuna_result = 512
ok / missing_optuna_result = 48
missing_without_reason = 0
```

State-store refresh after batch 127:

```text
Research Cache: rows=33442 stocks=5143 local_optuna=12140 production=21302 candidates=601 data_latest_date=2026-05-19
Incremental Evaluator: rows=33442 stocks=5143 clean=33442 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=33442 stocks=5143 none=28027 watch=5415 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2540 next_offset=2540 rows=12700 candidates=601 replacements=601 missing_without_reason=0 consistency.ready=True
```

CodeGraph and complexity optimizer follow-up:

```text
codegraph sync /Users/dp/Documents/M/stock/bestchoice
Files: 23
Nodes: 691
Edges: 1553
Language coverage: python only
```

`/opt/homebrew/bin/codegraph` and `/opt/homebrew/bin/codex-complexity-optimizer` are installed. The collaboration audit is updated in `analysis/complexity_codegraph_audit.md`. CodeGraph now covers the Python research pipeline but does not cover `index.html`, so frontend hotspot work still requires direct source inspection.

## 2026-05-20 Follow-up: One-Hundred-Twenty-Eighth Batch Partial Closure

Batch 128 main run and resume check completed:

```text
offset=2540
covered_stocks=2560
batch_rows=12800
new_rows=100
resume_new_rows=0
candidate_count=603
rejected_count=12197
dry_run_replacements=603
```

Batch 128 new accepted examples:

```text
301138 gs_pullback_confirm score_delta=34.656785 validation_delta=0.000000
301121 gs_raw_buy score_delta=23.826483 validation_delta=20.854415
```

Cumulative accepted distribution after batch 128:

```text
activity_breakout=351
gs_raw_buy=113
gs_pullback_confirm=79
volume_base_breakout=60
```

Missing-result status combinations after batch 128:

```text
missing_baseline_result / ok = 1584
missing_baseline_result / missing_optuna_result = 515
ok / missing_optuna_result = 48
missing_without_reason = 0
```

Completed state refresh:

```text
Research Cache: rows=33539 stocks=5143 local_optuna=12237 production=21302 candidates=603 data_latest_date=2026-05-19
```

Blocked state refresh:

```text
python scripts/incremental_eval_build.py
RuntimeError: Unable to determine latest market data date for research cache
```

The failure is attributable to a new external paper simulation holding `market.duckdb`:

```text
PID=24961
backend/scripts/run_paper_sim_v2.py --variant champion_minhold15_20260520_111606 --start 2025-01-02 --config-path backend/config/paper_sim_ml_score_champion_minhold15.yaml
market_db_lock_holders=1
next_action=wait_external_duckdb_lock
```

Do not run offset `2560` while this lock is active. After the paper simulation exits, run:

```text
python scripts/workflow_checkpoint.py --brief
python scripts/incremental_eval_build.py
python scripts/drift_trigger_build.py
python scripts/strategy_rebuild_audit.py
python scripts/workflow_checkpoint.py --brief
```

Only continue offset `2560` after checkpoint reports `consistency.ready=True`.

Checkpoint output refinement while this lock is active:

```text
formula_caches: unknown (market.duckdb locked; freshness check skipped)
formula_caches_status=unknown_due_to_market_db_lock
consistency.warnings=formula_caches_unknown_due_to_market_db_lock
```

This is intentional. When another process owns `market.duckdb`, formula cache freshness cannot be checked reliably. The checkpoint now reports unknown instead of the misleading `0/5 ready`; unknown means "not checked because of external lock", not "all formula caches are missing".

After the external paper simulation released `market.duckdb`, batch 128 final state-store closure was completed:

```text
Incremental Evaluator: rows=33539 stocks=5143 clean=33539 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=33539 stocks=5143 none=28100 watch=5439 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2560 next_offset=2560 rows=12800 candidates=603 replacements=603 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Twenty-Ninth Batch

Batch 129 outcome:

```text
offset=2560
covered_stocks=2580
batch_rows=12900
new_rows=100
candidate_count=608
rejected_count=12292
dry_run_replacements=608
resume_new_rows=0
```

Batch 129 new accepted examples:

```text
301156 volume_base_breakout score_delta=28.852069 validation_delta=8.918779
301168 gs_pullback_confirm score_delta=14.591155 validation_delta=20.108464
301162 gs_raw_buy score_delta=8.520942 validation_delta=12.418351
301159 gs_raw_buy score_delta=6.741706 validation_delta=3.615628
301162 volume_base_breakout score_delta=6.582208 validation_delta=21.343746
```

Cumulative accepted distribution after batch 129:

```text
activity_breakout=351
gs_raw_buy=115
gs_pullback_confirm=80
volume_base_breakout=62
```

Missing-result status combinations after batch 129:

```text
missing_baseline_result / ok = 1594
missing_baseline_result / missing_optuna_result = 518
ok / missing_optuna_result = 49
missing_without_reason = 0
```

State-store refresh after batch 129:

```text
Research Cache: rows=33635 stocks=5143 local_optuna=12333 production=21302 candidates=608 data_latest_date=2026-05-19
Incremental Evaluator: rows=33635 stocks=5143 clean=33635 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=33635 stocks=5143 none=28178 watch=5457 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2580 next_offset=2580 rows=12900 candidates=608 replacements=608 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Thirtieth Batch

Batch 130 outcome:

```text
offset=2580
covered_stocks=2600
batch_rows=13000
new_rows=100
candidate_count=613
rejected_count=12387
dry_run_replacements=613
resume_new_rows=0
```

Batch 130 new accepted examples:

```text
301186 gs_pullback_confirm score_delta=55.519573 validation_delta=75.476541
301188 activity_breakout score_delta=21.535866 validation_delta=13.470165
301191 activity_breakout score_delta=14.212460 validation_delta=3.578690
301180 activity_breakout score_delta=6.102341 validation_delta=3.996015
301179 gs_raw_buy score_delta=5.721590 validation_delta=5.284643
```

Cumulative accepted distribution after batch 130:

```text
activity_breakout=354
gs_raw_buy=116
gs_pullback_confirm=81
volume_base_breakout=62
```

Missing-result status combinations after batch 130:

```text
missing_baseline_result / ok = 1606
missing_baseline_result / missing_optuna_result = 521
ok / missing_optuna_result = 52
missing_without_reason = 0
```

State-store refresh after batch 130:

```text
Research Cache: rows=33729 stocks=5143 local_optuna=12427 production=21302 candidates=613 data_latest_date=2026-05-19
Incremental Evaluator: rows=33729 stocks=5143 clean=33729 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=33729 stocks=5143 none=28250 watch=5479 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2600 next_offset=2600 rows=13000 candidates=613 replacements=613 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Thirty-First Batch

Batch 131 outcome:

```text
offset=2600
covered_stocks=2620
batch_rows=13100
new_rows=100
candidate_count=622
rejected_count=12478
dry_run_replacements=622
resume_new_rows=0
```

Batch 131 new accepted examples:

```text
301195 activity_breakout score_delta=32.787708 validation_delta=18.013221
301199 activity_breakout score_delta=15.863488 validation_delta=40.146943
301203 gs_raw_buy score_delta=12.660383 validation_delta=20.191613
301197 gs_raw_buy score_delta=9.702733 validation_delta=1.312728
301207 gs_raw_buy score_delta=9.606484 validation_delta=5.898498
301205 activity_breakout score_delta=6.488714 validation_delta=0.623069
301201 gs_raw_buy score_delta=6.325332 validation_delta=2.117619
301197 activity_breakout score_delta=5.174046 validation_delta=1.377880
301196 gs_pullback_confirm score_delta=4.300730 validation_delta=42.466351
```

Cumulative accepted distribution after batch 131:

```text
activity_breakout=358
gs_raw_buy=120
gs_pullback_confirm=82
volume_base_breakout=62
```

Missing-result status combinations after batch 131:

```text
missing_baseline_result / ok = 1617
missing_baseline_result / missing_optuna_result = 527
ok / missing_optuna_result = 52
missing_without_reason = 0
```

State-store refresh after batch 131:

```text
Research Cache: rows=33823 stocks=5143 local_optuna=12521 production=21302 candidates=622 data_latest_date=2026-05-19
Incremental Evaluator: rows=33823 stocks=5143 clean=33823 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=33823 stocks=5143 none=28325 watch=5498 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2620 next_offset=2620 rows=13100 candidates=622 replacements=622 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Thirty-Second Batch

Batch 132 outcome:

```text
offset=2620
covered_stocks=2640
batch_rows=13200
new_rows=100
candidate_count=624
rejected_count=12576
dry_run_replacements=624
resume_new_rows=0
```

Batch 132 new accepted examples:

```text
301219 activity_breakout score_delta=15.742723 validation_delta=2.084189
301230 activity_breakout score_delta=13.535931 validation_delta=3.254643
```

Cumulative accepted distribution after batch 132:

```text
activity_breakout=360
gs_raw_buy=120
gs_pullback_confirm=82
volume_base_breakout=62
```

Missing-result status combinations after batch 132:

```text
missing_baseline_result / ok = 1631
missing_baseline_result / missing_optuna_result = 531
ok / missing_optuna_result = 52
missing_without_reason = 0
```

State-store refresh after batch 132:

```text
Research Cache: rows=33919 stocks=5143 local_optuna=12617 production=21302 candidates=624 data_latest_date=2026-05-19
Incremental Evaluator: rows=33919 stocks=5143 clean=33919 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=33919 stocks=5143 none=28402 watch=5517 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2640 next_offset=2640 rows=13200 candidates=624 replacements=624 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Thirty-Third Batch

Batch 133 outcome:

```text
offset=2640
covered_stocks=2660
batch_rows=13300
new_rows=100
candidate_count=628
rejected_count=12672
dry_run_replacements=628
resume_new_rows=0
```

Batch 133 new accepted examples:

```text
301255 activity_breakout score_delta=33.364457 validation_delta=27.177202
301266 activity_breakout score_delta=25.652967 validation_delta=20.680824
301267 gs_raw_buy score_delta=15.149213 validation_delta=5.667054
301255 gs_raw_buy score_delta=3.374597 validation_delta=3.508434
```

Cumulative accepted distribution after batch 133:

```text
activity_breakout=362
gs_raw_buy=122
gs_pullback_confirm=82
volume_base_breakout=62
```

Missing-result status combinations after batch 133:

```text
missing_baseline_result / ok = 1643
missing_baseline_result / missing_optuna_result = 536
ok / missing_optuna_result = 52
missing_without_reason = 0
```

State-store refresh after batch 133:

```text
Research Cache: rows=34014 stocks=5143 local_optuna=12712 production=21302 candidates=628 data_latest_date=2026-05-19
Incremental Evaluator: rows=34014 stocks=5143 clean=34014 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34014 stocks=5143 none=28474 watch=5540 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2660 next_offset=2660 rows=13300 candidates=628 replacements=628 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Thirty-Fourth Batch

Batch 134 outcome:

```text
offset=2660
covered_stocks=2680
batch_rows=13400
new_rows=100
candidate_count=632
rejected_count=12768
dry_run_replacements=632
resume_new_rows=0
```

Batch 134 new accepted examples:

```text
301270 gs_pullback_confirm score_delta=21.527385 validation_delta=65.652854
301288 activity_breakout score_delta=18.959873 validation_delta=31.075290
301283 gs_pullback_confirm score_delta=14.896818 validation_delta=5.826970
301282 gs_raw_buy score_delta=3.328971 validation_delta=4.593154
```

Cumulative accepted distribution after batch 134:

```text
activity_breakout=363
gs_raw_buy=123
gs_pullback_confirm=84
volume_base_breakout=62
```

Missing-result status combinations after batch 134:

```text
missing_baseline_result / ok = 1659
missing_baseline_result / missing_optuna_result = 541
ok / missing_optuna_result = 53
missing_without_reason = 0
```

State-store refresh after batch 134:

```text
Research Cache: rows=34108 stocks=5143 local_optuna=12806 production=21302 candidates=632 data_latest_date=2026-05-19
Incremental Evaluator: rows=34108 stocks=5143 clean=34108 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34108 stocks=5143 none=28549 watch=5559 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2680 next_offset=2680 rows=13400 candidates=632 replacements=632 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Thirty-Fifth Batch

Batch 135 outcome:

```text
offset=2680
covered_stocks=2700
batch_rows=13500
new_rows=100
candidate_count=637
rejected_count=12863
dry_run_replacements=637
resume_new_rows=0
```

Batch 135 new accepted examples:

```text
301313 activity_breakout score_delta=18.179202 validation_delta=1.830116
301295 activity_breakout score_delta=15.477966 validation_delta=4.287723
301298 gs_pullback_confirm score_delta=11.891007 validation_delta=5.322170
301314 gs_raw_buy score_delta=10.184981 validation_delta=8.998546
301297 activity_breakout score_delta=9.404643 validation_delta=38.298132
```

Cumulative accepted distribution after batch 135:

```text
activity_breakout=366
gs_raw_buy=124
gs_pullback_confirm=85
volume_base_breakout=62
```

Missing-result status combinations after batch 135:

```text
missing_baseline_result / ok = 1675
missing_baseline_result / missing_optuna_result = 546
ok / missing_optuna_result = 54
missing_without_reason = 0
```

State-store refresh after batch 135:

```text
Research Cache: rows=34202 stocks=5143 local_optuna=12900 production=21302 candidates=637 data_latest_date=2026-05-19
Incremental Evaluator: rows=34202 stocks=5143 clean=34202 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34202 stocks=5143 none=28618 watch=5584 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2700 next_offset=2700 rows=13500 candidates=637 replacements=637 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Thirty-Sixth Batch

Batch 136 outcome:

```text
offset=2700
covered_stocks=2720
batch_rows=13600
new_rows=100
candidate_count=641
rejected_count=12959
dry_run_replacements=641
resume_new_rows=0
```

Batch 136 new accepted examples:

```text
301332 activity_breakout score_delta=18.007275 validation_delta=19.471810
301315 gs_raw_buy score_delta=11.804109 validation_delta=23.033701
301328 gs_raw_buy score_delta=5.849605 validation_delta=3.428300
301323 activity_breakout score_delta=3.696574 validation_delta=3.461235
```

Cumulative accepted distribution after batch 136:

```text
activity_breakout=368
gs_raw_buy=126
gs_pullback_confirm=85
volume_base_breakout=62
```

Missing-result status combinations after batch 136:

```text
missing_baseline_result / ok = 1690
missing_baseline_result / missing_optuna_result = 551
ok / missing_optuna_result = 54
missing_without_reason = 0
```

State-store refresh after batch 136:

```text
Research Cache: rows=34297 stocks=5143 local_optuna=12995 production=21302 candidates=641 data_latest_date=2026-05-19
Incremental Evaluator: rows=34297 stocks=5143 clean=34297 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34297 stocks=5143 none=28692 watch=5605 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2720 next_offset=2720 rows=13600 candidates=641 replacements=641 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Thirty-Seventh Batch

Batch 137 outcome:

```text
offset=2720
covered_stocks=2740
batch_rows=13700
new_rows=100
candidate_count=645
rejected_count=13055
dry_run_replacements=645
resume_new_rows=0
```

Batch 137 new accepted examples:

```text
301363 activity_breakout score_delta=25.548530 validation_delta=11.029083
301359 activity_breakout score_delta=18.569243 validation_delta=10.560491
301362 activity_breakout score_delta=9.163700 validation_delta=4.285714
301361 activity_breakout score_delta=4.329039 validation_delta=3.193866
```

Cumulative accepted distribution after batch 137:

```text
activity_breakout=372
gs_raw_buy=126
gs_pullback_confirm=85
volume_base_breakout=62
```

Missing-result status combinations after batch 137:

```text
missing_baseline_result / ok = 1704
missing_baseline_result / missing_optuna_result = 557
ok / missing_optuna_result = 57
missing_without_reason = 0
```

State-store refresh after batch 137:

```text
Research Cache: rows=34388 stocks=5143 local_optuna=13086 production=21302 candidates=645 data_latest_date=2026-05-19
Incremental Evaluator: rows=34388 stocks=5143 clean=34388 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34388 stocks=5143 none=28764 watch=5624 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2740 next_offset=2740 rows=13700 candidates=645 replacements=645 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Thirty-Eighth Batch

Batch 138 outcome:

```text
offset=2740
covered_stocks=2760
batch_rows=13800
new_rows=100
candidate_count=650
rejected_count=13150
dry_run_replacements=650
resume_new_rows=0
```

Batch 138 new accepted examples:

```text
301378 activity_breakout score_delta=18.931379 validation_delta=12.951326
301379 activity_breakout score_delta=18.900551 validation_delta=12.664400
301386 activity_breakout score_delta=12.336587 validation_delta=7.962926
301372 activity_breakout score_delta=11.352147 validation_delta=0.384034
301389 gs_raw_buy score_delta=7.943712 validation_delta=5.638364
```

Cumulative accepted distribution after batch 138:

```text
activity_breakout=376
gs_raw_buy=127
gs_pullback_confirm=85
volume_base_breakout=62
```

Missing-result status combinations after batch 138:

```text
missing_baseline_result / ok = 1715
missing_baseline_result / missing_optuna_result = 565
ok / missing_optuna_result = 57
missing_without_reason = 0
```

State-store refresh after batch 138:

```text
Research Cache: rows=34480 stocks=5143 local_optuna=13178 production=21302 candidates=650 data_latest_date=2026-05-19
Incremental Evaluator: rows=34480 stocks=5143 clean=34480 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34480 stocks=5143 none=28838 watch=5642 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2760 next_offset=2760 rows=13800 candidates=650 replacements=650 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Thirty-Ninth Batch

Batch 139 outcome:

```text
offset=2760
covered_stocks=2780
batch_rows=13900
new_rows=100
candidate_count=653
rejected_count=13247
dry_run_replacements=653
resume_new_rows=0
```

Batch 139 new accepted examples:

```text
301399 gs_raw_buy score_delta=22.445645 validation_delta=4.979260
301446 activity_breakout score_delta=18.299027 validation_delta=1.233146
301413 activity_breakout score_delta=6.518991 validation_delta=6.088395
```

Cumulative accepted distribution after batch 139:

```text
activity_breakout=378
gs_raw_buy=128
gs_pullback_confirm=85
volume_base_breakout=62
```

Missing-result status combinations after batch 139:

```text
missing_baseline_result / ok = 1733
missing_baseline_result / missing_optuna_result = 573
ok / missing_optuna_result = 57
missing_without_reason = 0
```

State-store refresh after batch 139:

```text
Research Cache: rows=34572 stocks=5144 local_optuna=13270 production=21302 candidates=653 data_latest_date=2026-05-19
Incremental Evaluator: rows=34572 stocks=5144 clean=34572 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34572 stocks=5144 none=28900 watch=5672 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2780 next_offset=2780 rows=13900 candidates=653 replacements=653 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Fortieth Batch

Batch 140 outcome:

```text
offset=2780
covered_stocks=2800
batch_rows=14000
new_rows=100
candidate_count=656
rejected_count=13344
dry_run_replacements=656
resume_new_rows=0
```

Batch 140 new accepted examples:

```text
301489 activity_breakout score_delta=10.754128 validation_delta=15.177900
301507 activity_breakout score_delta=7.873936 validation_delta=0.000000
301503 activity_breakout score_delta=7.337497 validation_delta=0.000000
```

Cumulative accepted distribution after batch 140:

```text
activity_breakout=381
gs_raw_buy=128
gs_pullback_confirm=85
volume_base_breakout=62
```

Missing-result status combinations after batch 140:

```text
missing_baseline_result / ok = 1746
missing_baseline_result / missing_optuna_result = 590
ok / missing_optuna_result = 57
missing_without_reason = 0
```

State-store refresh after batch 140:

```text
Research Cache: rows=34655 stocks=5145 local_optuna=13353 production=21302 candidates=656 data_latest_date=2026-05-19
Incremental Evaluator: rows=34655 stocks=5145 clean=34655 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34655 stocks=5145 none=28960 watch=5695 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2800 next_offset=2800 rows=14000 candidates=656 replacements=656 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Forty-First Batch

Batch 141 outcome:

```text
offset=2800
covered_stocks=2820
batch_rows=14100
new_rows=100
candidate_count=658
rejected_count=13442
dry_run_replacements=658
resume_new_rows=0
```

Batch 141 new accepted examples:

```text
301520 gs_raw_buy score_delta=7.281703 validation_delta=2.600993
301533 gs_pullback_confirm score_delta=3.094735 validation_delta=12.506671
```

Cumulative accepted distribution after batch 141:

```text
activity_breakout=381
gs_raw_buy=129
gs_pullback_confirm=86
volume_base_breakout=62
```

Missing-result status combinations after batch 141:

```text
missing_baseline_result / ok = 1760
missing_baseline_result / missing_optuna_result = 601
ok / missing_optuna_result = 59
missing_without_reason = 0
```

State-store refresh after batch 141:

```text
Research Cache: rows=34742 stocks=5146 local_optuna=13440 production=21302 candidates=658 data_latest_date=2026-05-19
Incremental Evaluator: rows=34742 stocks=5146 clean=34742 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34742 stocks=5146 none=29019 watch=5723 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2820 next_offset=2820 rows=14100 candidates=658 replacements=658 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Forty-Second Batch

Batch 142 outcome:

```text
offset=2820
covered_stocks=2840
batch_rows=14200
new_rows=100
candidate_count=660
rejected_count=13540
dry_run_replacements=660
resume_new_rows=0
```

Batch 142 new accepted examples:

```text
301559 activity_breakout score_delta=8.613720 validation_delta=35.100852
301567 activity_breakout score_delta=5.572771 validation_delta=5.383210
```

Cumulative accepted distribution after batch 142:

```text
activity_breakout=383
gs_raw_buy=129
gs_pullback_confirm=86
volume_base_breakout=62
```

Missing-result status combinations after batch 142:

```text
missing_baseline_result / ok = 1778
missing_baseline_result / missing_optuna_result = 619
ok / missing_optuna_result = 60
missing_without_reason = 0
```

State-store refresh after batch 142:

```text
Research Cache: rows=34823 stocks=5149 local_optuna=13521 production=21302 candidates=660 data_latest_date=2026-05-19
Incremental Evaluator: rows=34823 stocks=5149 clean=34823 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34823 stocks=5149 none=29077 watch=5746 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2840 next_offset=2840 rows=14200 candidates=660 replacements=660 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Forty-Third Batch

Batch 143 outcome:

```text
offset=2840
covered_stocks=2860
batch_rows=14300
new_rows=100
candidate_count=663
rejected_count=13637
dry_run_replacements=663
resume_new_rows=0
```

Batch 143 new accepted examples:

```text
301591 activity_breakout score_delta=20.092143 validation_delta=25.180148
301607 activity_breakout score_delta=19.181481 validation_delta=28.681222
301600 activity_breakout score_delta=12.183853 validation_delta=34.348226
```

Cumulative accepted distribution after batch 143:

```text
activity_breakout=386
gs_raw_buy=129
gs_pullback_confirm=86
volume_base_breakout=62
```

Missing-result status combinations after batch 143:

```text
missing_baseline_result / ok = 1790
missing_baseline_result / missing_optuna_result = 639
ok / missing_optuna_result = 62
missing_without_reason = 0
```

State-store refresh after batch 143:

```text
Research Cache: rows=34901 stocks=5151 local_optuna=13599 production=21302 candidates=663 data_latest_date=2026-05-19
Incremental Evaluator: rows=34901 stocks=5151 clean=34901 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34901 stocks=5151 none=29131 watch=5770 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2860 next_offset=2860 rows=14300 candidates=663 replacements=663 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Forty-Fourth Batch

Batch 144 outcome:

```text
offset=2860
covered_stocks=2880
batch_rows=14400
new_rows=100
candidate_count=664
rejected_count=13736
dry_run_replacements=664
resume_new_rows=0
```

Batch 144 new accepted examples:

```text
301616 gs_raw_buy score_delta=5.495566 validation_delta=31.136005
```

Cumulative accepted distribution after batch 144:

```text
activity_breakout=386
gs_raw_buy=130
gs_pullback_confirm=86
volume_base_breakout=62
```

Missing-result status combinations after batch 144:

```text
missing_baseline_result / ok = 1808
missing_baseline_result / missing_optuna_result = 666
ok / missing_optuna_result = 65
missing_without_reason = 0
```

State-store refresh after batch 144:

```text
Research Cache: rows=34971 stocks=5156 local_optuna=13669 production=21302 candidates=664 data_latest_date=2026-05-19
Incremental Evaluator: rows=34971 stocks=5156 clean=34971 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=34971 stocks=5156 none=29176 watch=5795 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2880 next_offset=2880 rows=14400 candidates=664 replacements=664 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Forty-Fifth Batch

Batch 145 outcome:

```text
offset=2880
covered_stocks=2900
batch_rows=14500
new_rows=100
candidate_count=664
rejected_count=13836
dry_run_replacements=664
resume_new_rows=0
```

Batch 145 new accepted examples:

```text
none
```

Cumulative accepted distribution after batch 145:

```text
activity_breakout=386
gs_raw_buy=130
gs_pullback_confirm=86
volume_base_breakout=62
```

Missing-result status combinations after batch 145:

```text
missing_baseline_result / ok = 1830
missing_baseline_result / missing_optuna_result = 691
ok / missing_optuna_result = 66
missing_without_reason = 0
```

State-store refresh after batch 145:

```text
Research Cache: rows=35045 stocks=5163 local_optuna=13743 production=21302 candidates=664 data_latest_date=2026-05-19
Incremental Evaluator: rows=35045 stocks=5163 clean=35045 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=35045 stocks=5163 none=29232 watch=5813 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2900 next_offset=2900 rows=14500 candidates=664 replacements=664 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Forty-Sixth Batch

Batch 146 outcome:

```text
offset=2900
covered_stocks=2920
batch_rows=14600
new_rows=100
candidate_count=667
rejected_count=13933
dry_run_replacements=667
resume_new_rows=0
```

Batch 146 new accepted examples:

```text
600027 gs_pullback_confirm score_delta=16.462181 validation_delta=8.242570
600017 activity_breakout score_delta=13.480441 validation_delta=35.565906
600029 gs_raw_buy score_delta=3.407215 validation_delta=6.832528
```

Cumulative accepted distribution after batch 146:

```text
activity_breakout=387
gs_raw_buy=131
gs_pullback_confirm=87
volume_base_breakout=62
```

Missing-result status combinations after batch 146:

```text
missing_baseline_result / ok = 1844
missing_baseline_result / missing_optuna_result = 695
ok / missing_optuna_result = 67
missing_without_reason = 0
```

State-store refresh after batch 146:

```text
Research Cache: rows=35140 stocks=5163 local_optuna=13838 production=21302 candidates=667 data_latest_date=2026-05-19
Incremental Evaluator: rows=35140 stocks=5163 clean=35140 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=35140 stocks=5163 none=29309 watch=5831 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2920 next_offset=2920 rows=14600 candidates=667 replacements=667 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Forty-Seventh Batch

Batch 147 outcome:

```text
offset=2920
covered_stocks=2940
batch_rows=14700
new_rows=100
candidate_count=672
rejected_count=14028
dry_run_replacements=672
resume_new_rows=0
```

Batch 147 new accepted examples:

```text
600055 gs_raw_buy score_delta=13.681894 validation_delta=15.233419
600071 activity_breakout score_delta=11.837071 validation_delta=8.671560
600048 activity_breakout score_delta=11.444519 validation_delta=1.911772
600064 gs_raw_buy score_delta=8.133748 validation_delta=7.377143
600062 gs_raw_buy score_delta=4.955389 validation_delta=25.039638
```

Cumulative accepted distribution after batch 147:

```text
activity_breakout=389
gs_raw_buy=134
gs_pullback_confirm=87
volume_base_breakout=62
```

Missing-result status combinations after batch 147:

```text
missing_baseline_result / ok = 1854
missing_baseline_result / missing_optuna_result = 702
ok / missing_optuna_result = 67
missing_without_reason = 0
```

State-store refresh after batch 147:

```text
Research Cache: rows=35233 stocks=5163 local_optuna=13931 production=21302 candidates=672 data_latest_date=2026-05-19
Incremental Evaluator: rows=35233 stocks=5163 clean=35233 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=35233 stocks=5163 none=29387 watch=5846 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2940 next_offset=2940 rows=14700 candidates=672 replacements=672 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Forty-Eighth Batch

Batch 148 outcome:

```text
offset=2940
covered_stocks=2960
batch_rows=14800
new_rows=100
candidate_count=677
rejected_count=14123
dry_run_replacements=677
resume_new_rows=0
```

Batch 148 new accepted examples:

```text
600097 activity_breakout score_delta=39.117476 validation_delta=26.938059
600095 activity_breakout score_delta=12.459874 validation_delta=22.395279
600094 activity_breakout score_delta=12.269836 validation_delta=11.805834
600094 gs_raw_buy score_delta=5.615250 validation_delta=9.150227
600099 gs_raw_buy score_delta=4.240929 validation_delta=8.767935
```

Cumulative accepted distribution after batch 148:

```text
activity_breakout=392
gs_raw_buy=136
gs_pullback_confirm=87
volume_base_breakout=62
```

Missing-result status combinations after batch 148:

```text
missing_baseline_result / ok = 1871
missing_baseline_result / missing_optuna_result = 704
ok / missing_optuna_result = 67
missing_without_reason = 0
```

State-store refresh after batch 148:

```text
Research Cache: rows=35331 stocks=5163 local_optuna=14029 production=21302 candidates=677 data_latest_date=2026-05-19
Incremental Evaluator: rows=35331 stocks=5163 clean=35331 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=35331 stocks=5163 none=29470 watch=5861 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2960 next_offset=2960 rows=14800 candidates=677 replacements=677 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Forty-Ninth Batch

Batch 149 outcome:

```text
offset=2960
covered_stocks=2980
batch_rows=14900
new_rows=100
candidate_count=682
rejected_count=14218
dry_run_replacements=682
resume_new_rows=0
```

Batch 149 new accepted examples:

```text
600116 volume_base_breakout score_delta=37.254278 validation_delta=8.147537
600120 volume_base_breakout score_delta=32.859223 validation_delta=40.820263
600110 activity_breakout score_delta=14.015322 validation_delta=7.088663
600101 activity_breakout score_delta=11.062534 validation_delta=36.467148
600103 activity_breakout score_delta=3.892470 validation_delta=9.912291
```

Cumulative accepted distribution after batch 149:

```text
activity_breakout=395
gs_raw_buy=136
gs_pullback_confirm=87
volume_base_breakout=64
```

Missing-result status combinations after batch 149:

```text
missing_baseline_result / ok = 1882
missing_baseline_result / missing_optuna_result = 709
ok / missing_optuna_result = 67
missing_without_reason = 0
```

State-store refresh after batch 149:

```text
Research Cache: rows=35426 stocks=5163 local_optuna=14124 production=21302 candidates=682 data_latest_date=2026-05-19
Incremental Evaluator: rows=35426 stocks=5163 clean=35426 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=35426 stocks=5163 none=29550 watch=5876 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=2980 next_offset=2980 rows=14900 candidates=682 replacements=682 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Fiftieth Batch

Batch 150 outcome:

```text
offset=2980
covered_stocks=3000
batch_rows=15000
new_rows=100
candidate_count=686
rejected_count=14314
dry_run_replacements=686
resume_new_rows=0
```

Batch 150 new accepted examples:

```text
600133 activity_breakout score_delta=34.384094 validation_delta=26.480588
600125 volume_base_breakout score_delta=10.334218 validation_delta=23.111577
600128 gs_raw_buy score_delta=7.623086 validation_delta=4.710861
600138 gs_raw_buy score_delta=3.170754 validation_delta=4.871257
```

Cumulative accepted distribution after batch 150:

```text
activity_breakout=396
gs_raw_buy=138
gs_pullback_confirm=87
volume_base_breakout=65
```

Missing-result status combinations after batch 150:

```text
missing_baseline_result / ok = 1892
missing_baseline_result / missing_optuna_result = 712
ok / missing_optuna_result = 67
missing_without_reason = 0
```

State-store refresh after batch 150:

```text
Research Cache: rows=35523 stocks=5163 local_optuna=14221 production=21302 candidates=686 data_latest_date=2026-05-19
Incremental Evaluator: rows=35523 stocks=5163 clean=35523 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=35523 stocks=5163 none=29628 watch=5895 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3000 next_offset=3000 rows=15000 candidates=686 replacements=686 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Fifty-First Batch

Batch 151 outcome:

```text
offset=3000
covered_stocks=3020
batch_rows=15100
new_rows=100
candidate_count=687
rejected_count=14413
dry_run_replacements=687
resume_new_rows=0
```

Batch 151 new accepted examples:

```text
600153 activity_breakout score_delta=20.311777 validation_delta=49.321855
```

Cumulative accepted distribution after batch 151:

```text
activity_breakout=397
gs_raw_buy=138
gs_pullback_confirm=87
volume_base_breakout=65
```

Missing-result status combinations after batch 151:

```text
missing_baseline_result / ok = 1901
missing_baseline_result / missing_optuna_result = 715
ok / missing_optuna_result = 67
missing_without_reason = 0
```

State-store refresh after batch 151:

```text
Research Cache: rows=35620 stocks=5163 local_optuna=14318 production=21302 candidates=687 data_latest_date=2026-05-19
Incremental Evaluator: rows=35620 stocks=5163 clean=35620 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=35620 stocks=5163 none=29702 watch=5918 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3020 next_offset=3020 rows=15100 candidates=687 replacements=687 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Fifty-Second Batch

Batch 152 outcome:

```text
offset=3020
covered_stocks=3040
batch_rows=15200
new_rows=100
candidate_count=690
rejected_count=14510
dry_run_replacements=690
resume_new_rows=0
```

Batch 152 new accepted examples:

```text
600183 activity_breakout score_delta=12.021894 validation_delta=4.760537
600195 gs_pullback_confirm score_delta=6.256712 validation_delta=3.505223
600193 activity_breakout score_delta=5.271272 validation_delta=8.896522
```

Cumulative accepted distribution after batch 152:

```text
activity_breakout=399
gs_raw_buy=138
gs_pullback_confirm=88
volume_base_breakout=65
```

Missing-result status combinations after batch 152:

```text
missing_baseline_result / ok = 1918
missing_baseline_result / missing_optuna_result = 715
ok / missing_optuna_result = 68
missing_without_reason = 0
```

State-store refresh after batch 152:

```text
Research Cache: rows=35719 stocks=5163 local_optuna=14417 production=21302 candidates=690 data_latest_date=2026-05-19
Incremental Evaluator: rows=35719 stocks=5163 clean=35719 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=35719 stocks=5163 none=29781 watch=5938 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3040 next_offset=3040 rows=15200 candidates=690 replacements=690 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Fifty-Third Batch

Batch 153 outcome:

```text
offset=3040
covered_stocks=3060
batch_rows=15300
new_rows=100
candidate_count=696
rejected_count=14604
dry_run_replacements=696
resume_new_rows=0
```

Batch 153 new accepted examples:

```text
600222 activity_breakout score_delta=36.183188 validation_delta=16.171652
600221 activity_breakout score_delta=31.829927 validation_delta=8.829764
600206 gs_pullback_confirm score_delta=18.847796 validation_delta=3.587134
600201 gs_pullback_confirm score_delta=15.133093 validation_delta=106.478216
600218 gs_raw_buy score_delta=10.450115 validation_delta=0.674405
600228 volume_base_breakout score_delta=8.659791 validation_delta=4.998235
```

Cumulative accepted distribution after batch 153:

```text
activity_breakout=401
gs_raw_buy=139
gs_pullback_confirm=90
volume_base_breakout=66
```

Missing-result status combinations after batch 153:

```text
missing_baseline_result / ok = 1931
missing_baseline_result / missing_optuna_result = 718
ok / missing_optuna_result = 68
missing_without_reason = 0
```

State-store refresh after batch 153:

```text
Research Cache: rows=35816 stocks=5163 local_optuna=14514 production=21302 candidates=696 data_latest_date=2026-05-19
Incremental Evaluator: rows=35816 stocks=5163 clean=35816 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=35816 stocks=5163 none=29863 watch=5953 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3060 next_offset=3060 rows=15300 candidates=696 replacements=696 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Fifty-Fourth Batch

Batch 154 outcome:

```text
offset=3060
covered_stocks=3080
batch_rows=15400
new_rows=100
candidate_count=701
rejected_count=14699
dry_run_replacements=701
resume_new_rows=0
```

Batch 154 new accepted examples:

```text
600250 activity_breakout score_delta=22.069626 validation_delta=12.231836
600246 gs_pullback_confirm score_delta=20.952178 validation_delta=20.263385
600238 activity_breakout score_delta=17.473026 validation_delta=4.846967
600230 activity_breakout score_delta=14.204674 validation_delta=13.881509
600229 gs_raw_buy score_delta=5.228869 validation_delta=6.187098
```

Cumulative accepted distribution after batch 154:

```text
activity_breakout=404
gs_raw_buy=140
gs_pullback_confirm=91
volume_base_breakout=66
```

Missing-result status combinations after batch 154:

```text
missing_baseline_result / ok = 1943
missing_baseline_result / missing_optuna_result = 721
ok / missing_optuna_result = 68
missing_without_reason = 0
```

State-store refresh after batch 154:

```text
Research Cache: rows=35913 stocks=5163 local_optuna=14611 production=21302 candidates=701 data_latest_date=2026-05-19
Incremental Evaluator: rows=35913 stocks=5163 clean=35913 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=35913 stocks=5163 none=29939 watch=5974 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3080 next_offset=3080 rows=15400 candidates=701 replacements=701 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Fifty-Fifth Batch

Batch 155 outcome:

```text
offset=3080
covered_stocks=3100
batch_rows=15500
new_rows=100
candidate_count=703
rejected_count=14797
dry_run_replacements=703
resume_new_rows=0
```

Batch 155 new accepted examples:

```text
600267 activity_breakout score_delta=23.423867 validation_delta=30.205734
600272 gs_raw_buy score_delta=5.026267 validation_delta=6.395476
```

Cumulative accepted distribution after batch 155:

```text
activity_breakout=405
gs_raw_buy=141
gs_pullback_confirm=91
volume_base_breakout=66
```

Missing-result status combinations after batch 155:

```text
missing_baseline_result / ok = 1956
missing_baseline_result / missing_optuna_result = 723
ok / missing_optuna_result = 69
missing_without_reason = 0
```

State-store refresh after batch 155:

```text
Research Cache: rows=36010 stocks=5163 local_optuna=14708 production=21302 candidates=703 data_latest_date=2026-05-19
Incremental Evaluator: rows=36010 stocks=5163 clean=36010 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=36010 stocks=5163 none=30013 watch=5997 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3100 next_offset=3100 rows=15500 candidates=703 replacements=703 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Fifty-Sixth Batch

Batch 156 outcome:

```text
offset=3100
covered_stocks=3120
batch_rows=15600
new_rows=100
candidate_count=708
rejected_count=14892
dry_run_replacements=708
resume_new_rows=0
```

Batch 156 new accepted examples:

```text
600305 volume_base_breakout score_delta=38.565848 validation_delta=2.477066
600302 gs_pullback_confirm score_delta=21.014968 validation_delta=49.436675
600309 activity_breakout score_delta=18.514746 validation_delta=12.128712
600310 activity_breakout score_delta=13.691446 validation_delta=7.595992
600292 activity_breakout score_delta=9.734760 validation_delta=1.850946
```

Cumulative accepted distribution after batch 156:

```text
activity_breakout=408
gs_raw_buy=141
gs_pullback_confirm=92
volume_base_breakout=67
```

Missing-result status combinations after batch 156:

```text
missing_baseline_result / ok = 1974
missing_baseline_result / missing_optuna_result = 725
ok / missing_optuna_result = 69
missing_without_reason = 0
```

State-store refresh after batch 156:

```text
Research Cache: rows=36108 stocks=5163 local_optuna=14806 production=21302 candidates=708 data_latest_date=2026-05-19
Incremental Evaluator: rows=36108 stocks=5163 clean=36108 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=36108 stocks=5163 none=30090 watch=6018 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3120 next_offset=3120 rows=15600 candidates=708 replacements=708 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Fifty-Seventh Batch

Batch 157 outcome:

```text
offset=3120
covered_stocks=3140
batch_rows=15700
new_rows=100
candidate_count=711
rejected_count=14989
dry_run_replacements=711
resume_new_rows=0
```

Batch 157 new accepted examples:

```text
600336 volume_base_breakout score_delta=17.644735 validation_delta=33.868045
600329 activity_breakout score_delta=14.590444 validation_delta=17.011783
600330 activity_breakout score_delta=7.802522 validation_delta=0.246622
```

Cumulative accepted distribution after batch 157:

```text
activity_breakout=410
gs_raw_buy=141
gs_pullback_confirm=92
volume_base_breakout=68
```

Missing-result status combinations after batch 157:

```text
missing_baseline_result / ok = 1986
missing_baseline_result / missing_optuna_result = 729
ok / missing_optuna_result = 69
missing_without_reason = 0
```

State-store refresh after batch 157:

```text
Research Cache: rows=36204 stocks=5163 local_optuna=14902 production=21302 candidates=711 data_latest_date=2026-05-19
Incremental Evaluator: rows=36204 stocks=5163 clean=36204 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=36204 stocks=5163 none=30164 watch=6040 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3140 next_offset=3140 rows=15700 candidates=711 replacements=711 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Fifty-Eighth Batch

Batch 158 outcome:

```text
offset=3140
covered_stocks=3160
batch_rows=15800
new_rows=100
candidate_count=713
rejected_count=15087
dry_run_replacements=713
resume_new_rows=0
```

Batch 158 new accepted examples:

```text
600350 activity_breakout score_delta=19.406605 validation_delta=12.866753
600362 volume_base_breakout score_delta=8.637844 validation_delta=0.987235
```

Cumulative accepted distribution after batch 158:

```text
activity_breakout=411
gs_raw_buy=141
gs_pullback_confirm=92
volume_base_breakout=69
```

Missing-result status combinations after batch 158:

```text
missing_baseline_result / ok = 1997
missing_baseline_result / missing_optuna_result = 735
ok / missing_optuna_result = 69
missing_without_reason = 0
```

State-store refresh after batch 158:

```text
Research Cache: rows=36298 stocks=5163 local_optuna=14996 production=21302 candidates=713 data_latest_date=2026-05-19
Incremental Evaluator: rows=36298 stocks=5163 clean=36298 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=36298 stocks=5163 none=30246 watch=6052 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3160 next_offset=3160 rows=15800 candidates=713 replacements=713 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Fifty-Ninth Batch

Batch 159 outcome:

```text
offset=3160
covered_stocks=3180
batch_rows=15900
new_rows=100
candidate_count=714
rejected_count=15186
dry_run_replacements=714
resume_new_rows=0
```

Batch 159 new accepted examples:

```text
600375 activity_breakout score_delta=25.966066 validation_delta=24.239333
```

Cumulative accepted distribution after batch 159:

```text
activity_breakout=412
gs_raw_buy=141
gs_pullback_confirm=92
volume_base_breakout=69
```

Missing-result status combinations after batch 159:

```text
missing_baseline_result / ok = 2010
missing_baseline_result / missing_optuna_result = 738
ok / missing_optuna_result = 69
missing_without_reason = 0
```

State-store refresh after batch 159:

```text
Research Cache: rows=36395 stocks=5163 local_optuna=15093 production=21302 candidates=714 data_latest_date=2026-05-19
Incremental Evaluator: rows=36395 stocks=5163 clean=36395 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=36395 stocks=5163 none=30319 watch=6076 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3180 next_offset=3180 rows=15900 candidates=714 replacements=714 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Sixtieth Batch

Batch 160 outcome:

```text
offset=3180
covered_stocks=3200
batch_rows=16000
new_rows=100
candidate_count=717
rejected_count=15283
dry_run_replacements=717
resume_new_rows=0
```

Batch 160 new accepted examples:

```text
600397 gs_pullback_confirm score_delta=17.311910 validation_delta=5.235416
600405 activity_breakout score_delta=16.881173 validation_delta=20.936163
600397 activity_breakout score_delta=5.164436 validation_delta=1.059623
```

Cumulative accepted distribution after batch 160:

```text
activity_breakout=414
gs_raw_buy=141
gs_pullback_confirm=93
volume_base_breakout=69
```

Missing-result status combinations after batch 160:

```text
missing_baseline_result / ok = 2028
missing_baseline_result / missing_optuna_result = 741
ok / missing_optuna_result = 69
missing_without_reason = 0
```

State-store refresh after batch 160:

```text
Research Cache: rows=36492 stocks=5163 local_optuna=15190 production=21302 candidates=717 data_latest_date=2026-05-19
Incremental Evaluator: rows=36492 stocks=5163 clean=36492 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=36492 stocks=5163 none=30397 watch=6095 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3200 next_offset=3200 rows=16000 candidates=717 replacements=717 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Sixty-First Batch

Batch 161 outcome:

```text
offset=3200
covered_stocks=3220
batch_rows=16100
new_rows=100
candidate_count=720
rejected_count=15380
dry_run_replacements=720
resume_new_rows=0
```

Batch 161 new accepted examples:

```text
600452 volume_base_breakout score_delta=27.240537 validation_delta=14.554676
600433 volume_base_breakout score_delta=22.570322 validation_delta=6.794757
600428 volume_base_breakout score_delta=14.579544 validation_delta=10.647736
```

Cumulative accepted distribution after batch 161:

```text
activity_breakout=414
gs_raw_buy=141
gs_pullback_confirm=93
volume_base_breakout=72
```

Missing-result status combinations after batch 161:

```text
missing_baseline_result / ok = 2040
missing_baseline_result / missing_optuna_result = 746
ok / missing_optuna_result = 70
missing_without_reason = 0
```

State-store refresh after batch 161:

```text
Research Cache: rows=36586 stocks=5163 local_optuna=15284 production=21302 candidates=720 data_latest_date=2026-05-19
Incremental Evaluator: rows=36586 stocks=5163 clean=36586 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=36586 stocks=5163 none=30476 watch=6110 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3220 next_offset=3220 rows=16100 candidates=720 replacements=720 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Sixty-Second Batch

Batch 162 outcome:

```text
offset=3220
covered_stocks=3240
batch_rows=16200
new_rows=100
candidate_count=723
rejected_count=15477
dry_run_replacements=723
resume_new_rows=0
```

Batch 162 new accepted examples:

```text
600468 volume_base_breakout score_delta=14.633426 validation_delta=0.551490
600461 volume_base_breakout score_delta=9.912945 validation_delta=22.868936
600487 activity_breakout score_delta=8.569285 validation_delta=1.394846
```

Cumulative accepted distribution after batch 162:

```text
activity_breakout=415
gs_raw_buy=141
gs_pullback_confirm=93
volume_base_breakout=74
```

Missing-result status combinations after batch 162:

```text
missing_baseline_result / ok = 2051
missing_baseline_result / missing_optuna_result = 751
ok / missing_optuna_result = 70
missing_without_reason = 0
```

State-store refresh after batch 162:

```text
Research Cache: rows=36681 stocks=5163 local_optuna=15379 production=21302 candidates=723 data_latest_date=2026-05-19
Incremental Evaluator: rows=36681 stocks=5163 clean=36681 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=36681 stocks=5163 none=30554 watch=6127 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3240 next_offset=3240 rows=16200 candidates=723 replacements=723 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Sixty-Third Batch

Batch 163 outcome:

```text
offset=3240
covered_stocks=3260
batch_rows=16300
new_rows=100
candidate_count=727
rejected_count=15573
dry_run_replacements=727
resume_new_rows=0
```

Batch 163 new accepted examples:

```text
600491 activity_breakout score_delta=23.162470 validation_delta=11.284371
600506 gs_pullback_confirm score_delta=10.594612 validation_delta=6.407121
600510 gs_raw_buy score_delta=9.960283 validation_delta=12.664199
600493 volume_base_breakout score_delta=7.535516 validation_delta=3.897111
```

Cumulative accepted distribution after batch 163:

```text
activity_breakout=416
gs_raw_buy=142
gs_pullback_confirm=94
volume_base_breakout=75
```

Missing-result status combinations after batch 163:

```text
missing_baseline_result / ok = 2064
missing_baseline_result / missing_optuna_result = 755
ok / missing_optuna_result = 70
missing_without_reason = 0
```

State-store refresh after batch 163:

```text
Research Cache: rows=36777 stocks=5163 local_optuna=15475 production=21302 candidates=727 data_latest_date=2026-05-19
Incremental Evaluator: rows=36777 stocks=5163 clean=36777 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=36777 stocks=5163 none=30629 watch=6148 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3260 next_offset=3260 rows=16300 candidates=727 replacements=727 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Sixty-Fourth Batch

Pre-batch lock handling:

```text
market_db_lock_holder=backend/scripts/build_market_perception_daily.py --start 2024-11-01 --end 2026-05-19
checkpoint_next_action=wait_external_duckdb_lock
action_taken=waited_until_lsof_released_then_rechecked_checkpoint
post_wait_consistency.ready=True
```

Batch 164 outcome:

```text
offset=3260
covered_stocks=3280
batch_rows=16400
new_rows=100
candidate_count=732
rejected_count=15668
dry_run_replacements=732
resume_new_rows=0
```

Batch 164 new accepted examples:

```text
600511 activity_breakout score_delta=23.282576 validation_delta=1.380694
600529 activity_breakout score_delta=23.189100 validation_delta=27.285416
600526 activity_breakout score_delta=15.223316 validation_delta=2.537984
600516 gs_raw_buy score_delta=8.295116 validation_delta=7.099585
600526 gs_pullback_confirm score_delta=6.137936 validation_delta=0.933955
```

Cumulative accepted distribution after batch 164:

```text
activity_breakout=419
gs_raw_buy=143
gs_pullback_confirm=95
volume_base_breakout=75
```

Missing-result status combinations after batch 164:

```text
missing_baseline_result / ok = 2075
missing_baseline_result / missing_optuna_result = 757
ok / missing_optuna_result = 70
missing_without_reason = 0
```

State-store refresh after batch 164:

```text
Research Cache: rows=36875 stocks=5163 local_optuna=15573 production=21302 candidates=732 data_latest_date=2026-05-19
Incremental Evaluator: rows=36875 stocks=5163 clean=36875 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=36875 stocks=5163 none=30708 watch=6167 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3280 next_offset=3280 rows=16400 candidates=732 replacements=732 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Sixty-Fifth Batch

Batch 165 outcome:

```text
offset=3280
covered_stocks=3300
batch_rows=16500
new_rows=100
candidate_count=736
rejected_count=15764
dry_run_replacements=736
resume_new_rows=0
```

Batch 165 new accepted examples:

```text
600560 volume_base_breakout score_delta=30.767216 validation_delta=2.777696
600543 activity_breakout score_delta=17.847878 validation_delta=16.301450
600545 gs_pullback_confirm score_delta=8.913233 validation_delta=60.741012
600543 gs_pullback_confirm score_delta=5.128595 validation_delta=0.000000
```

Cumulative accepted distribution after batch 165:

```text
activity_breakout=420
gs_raw_buy=143
gs_pullback_confirm=97
volume_base_breakout=76
```

Missing-result status combinations after batch 165:

```text
missing_baseline_result / ok = 2089
missing_baseline_result / missing_optuna_result = 761
ok / missing_optuna_result = 70
missing_without_reason = 0
```

State-store refresh after batch 165:

```text
Research Cache: rows=36971 stocks=5163 local_optuna=15669 production=21302 candidates=736 data_latest_date=2026-05-19
Incremental Evaluator: rows=36971 stocks=5163 clean=36971 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=36971 stocks=5163 none=30783 watch=6188 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3300 next_offset=3300 rows=16500 candidates=736 replacements=736 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Sixty-Sixth Batch

Batch 166 outcome:

```text
offset=3300
covered_stocks=3320
batch_rows=16600
new_rows=100
candidate_count=738
rejected_count=15862
dry_run_replacements=738
resume_new_rows=0
```

Batch 166 new accepted examples:

```text
600578 volume_base_breakout score_delta=15.756219 validation_delta=25.402999
600570 activity_breakout score_delta=14.944307 validation_delta=15.593357
```

Cumulative accepted distribution after batch 166:

```text
activity_breakout=421
gs_raw_buy=143
gs_pullback_confirm=97
volume_base_breakout=77
```

Missing-result status combinations after batch 166:

```text
missing_baseline_result / ok = 2103
missing_baseline_result / missing_optuna_result = 764
ok / missing_optuna_result = 71
missing_without_reason = 0
```

State-store refresh after batch 166:

```text
Research Cache: rows=37067 stocks=5163 local_optuna=15765 production=21302 candidates=738 data_latest_date=2026-05-19
Incremental Evaluator: rows=37067 stocks=5163 clean=37067 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=37067 stocks=5163 none=30856 watch=6211 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3320 next_offset=3320 rows=16600 candidates=738 replacements=738 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: Offset 3320 Short Segment

Short segment outcome:

```text
offset=3320
covered_stocks=3336
segment_rows=16680
new_rows=80
candidate_count=740
rejected_count=15940
dry_run_replacements=740
resume_new_rows=0
checkpoint_next_offset=3336
```

Short segment new accepted examples:

```text
600586 activity_breakout score_delta=18.575588 validation_delta=23.585311
600593 activity_breakout score_delta=8.127495 validation_delta=23.958387
```

Cumulative accepted distribution after offset 3320 short segment:

```text
activity_breakout=423
gs_raw_buy=143
gs_pullback_confirm=97
volume_base_breakout=77
```

Missing-result status combinations after offset 3320 short segment:

```text
missing_baseline_result / ok = 2112
missing_baseline_result / missing_optuna_result = 766
ok / missing_optuna_result = 71
missing_without_reason = 0
```

State-store refresh after offset 3320 short segment:

```text
Research Cache: rows=37145 stocks=5163 local_optuna=15843 production=21302 candidates=740 data_latest_date=2026-05-19
Incremental Evaluator: rows=37145 stocks=5163 clean=37145 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=37145 stocks=5163 none=30915 watch=6230 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3336 next_offset=3336 rows=16680 candidates=740 replacements=740 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Sixty-Seventh Batch

Batch 167 outcome:

```text
offset=3336
covered_stocks=3356
batch_rows=16780
new_rows=100
candidate_count=743
rejected_count=16037
dry_run_replacements=743
resume_new_rows=0
```

Batch 167 new accepted examples:

```text
600613 activity_breakout score_delta=22.447719 validation_delta=5.167074
600601 gs_pullback_confirm score_delta=20.495684 validation_delta=53.801934
600620 gs_pullback_confirm score_delta=18.964453 validation_delta=16.542856
```

Cumulative accepted distribution after batch 167:

```text
activity_breakout=424
gs_raw_buy=143
gs_pullback_confirm=99
volume_base_breakout=77
```

Missing-result status combinations after batch 167:

```text
missing_baseline_result / ok = 2124
missing_baseline_result / missing_optuna_result = 768
ok / missing_optuna_result = 71
missing_without_reason = 0
```

State-store refresh after batch 167:

```text
Research Cache: rows=37243 stocks=5163 local_optuna=15941 production=21302 candidates=743 data_latest_date=2026-05-19
Incremental Evaluator: rows=37243 stocks=5163 clean=37243 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=37243 stocks=5163 none=30994 watch=6249 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3356 next_offset=3356 rows=16780 candidates=743 replacements=743 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Sixty-Eighth Batch

Batch 168 outcome:

```text
offset=3356
covered_stocks=3376
batch_rows=16880
new_rows=100
candidate_count=747
rejected_count=16133
dry_run_replacements=747
resume_new_rows=0
```

Batch 168 new accepted examples:

```text
600636 activity_breakout score_delta=14.894458 validation_delta=4.326130
600639 activity_breakout score_delta=7.423171 validation_delta=1.451748
600645 gs_raw_buy score_delta=5.134945 validation_delta=11.921661
600642 gs_raw_buy score_delta=4.012638 validation_delta=0.780809
```

Cumulative accepted distribution after batch 168:

```text
activity_breakout=426
gs_raw_buy=145
gs_pullback_confirm=99
volume_base_breakout=77
```

Missing-result status combinations after batch 168:

```text
missing_baseline_result / ok = 2138
missing_baseline_result / missing_optuna_result = 772
ok / missing_optuna_result = 71
missing_without_reason = 0
```

State-store refresh after batch 168:

```text
Research Cache: rows=37339 stocks=5163 local_optuna=16037 production=21302 candidates=747 data_latest_date=2026-05-19
Incremental Evaluator: rows=37339 stocks=5163 clean=37339 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=37339 stocks=5163 none=31072 watch=6267 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3376 next_offset=3376 rows=16880 candidates=747 replacements=747 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Sixty-Ninth Batch

Batch 169 outcome:

```text
offset=3376
covered_stocks=3396
batch_rows=16980
new_rows=100
candidate_count=750
rejected_count=16230
dry_run_replacements=750
resume_new_rows=0
```

Batch 169 new accepted examples:

```text
600667 activity_breakout score_delta=24.054010 validation_delta=16.083053
600666 activity_breakout score_delta=9.153473 validation_delta=7.699283
600660 gs_raw_buy score_delta=5.676654 validation_delta=12.365895
```

Cumulative accepted distribution after batch 169:

```text
activity_breakout=428
gs_raw_buy=146
gs_pullback_confirm=99
volume_base_breakout=77
```

Missing-result status combinations after batch 169:

```text
missing_baseline_result / ok = 2150
missing_baseline_result / missing_optuna_result = 774
ok / missing_optuna_result = 71
missing_without_reason = 0
```

State-store refresh after batch 169:

```text
Research Cache: rows=37437 stocks=5163 local_optuna=16135 production=21302 candidates=750 data_latest_date=2026-05-19
Incremental Evaluator: rows=37437 stocks=5163 clean=37437 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=37437 stocks=5163 none=31148 watch=6289 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3396 next_offset=3396 rows=16980 candidates=750 replacements=750 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Seventieth Batch

Batch 170 outcome:

```text
offset=3396
covered_stocks=3416
batch_rows=17080
new_rows=100
candidate_count=753
rejected_count=16327
dry_run_replacements=753
resume_new_rows=0
```

Batch 170 new accepted examples:

```text
600692 activity_breakout score_delta=33.398338 validation_delta=19.998463
600697 gs_raw_buy score_delta=8.101829 validation_delta=4.381656
600684 gs_pullback_confirm score_delta=7.046291 validation_delta=8.842282
```

Cumulative accepted distribution after batch 170:

```text
activity_breakout=429
gs_raw_buy=147
gs_pullback_confirm=100
volume_base_breakout=77
```

Missing-result status combinations after batch 170:

```text
missing_baseline_result / ok = 2163
missing_baseline_result / missing_optuna_result = 778
ok / missing_optuna_result = 71
missing_without_reason = 0
```

State-store refresh after batch 170:

```text
Research Cache: rows=37533 stocks=5163 local_optuna=16231 production=21302 candidates=753 data_latest_date=2026-05-19
Incremental Evaluator: rows=37533 stocks=5163 clean=37533 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=37533 stocks=5163 none=31221 watch=6312 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3416 next_offset=3416 rows=17080 candidates=753 replacements=753 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Seventy-First Batch

Batch 171 outcome:

```text
offset=3416
covered_stocks=3436
batch_rows=17180
new_rows=100
candidate_count=760
rejected_count=16420
dry_run_replacements=760
resume_new_rows=0
```

Batch 171 new accepted examples:

```text
600711 activity_breakout score_delta=25.110293 validation_delta=0.578775
600710 volume_base_breakout score_delta=22.856684 validation_delta=59.892766
600713 gs_pullback_confirm score_delta=9.242459 validation_delta=5.815401
600721 gs_raw_buy score_delta=8.088087 validation_delta=0.328129
600710 gs_raw_buy score_delta=7.548282 validation_delta=1.006182
600716 gs_pullback_confirm score_delta=5.690320 validation_delta=7.883680
600703 activity_breakout score_delta=5.518815 validation_delta=1.730314
```

Cumulative accepted distribution after batch 171:

```text
activity_breakout=431
gs_raw_buy=149
gs_pullback_confirm=102
volume_base_breakout=78
```

Missing-result status combinations after batch 171:

```text
missing_baseline_result / ok = 2175
missing_baseline_result / missing_optuna_result = 780
ok / missing_optuna_result = 71
missing_without_reason = 0
```

State-store refresh after batch 171:

```text
Research Cache: rows=37631 stocks=5163 local_optuna=16329 production=21302 candidates=760 data_latest_date=2026-05-19
Incremental Evaluator: rows=37631 stocks=5163 clean=37631 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=37631 stocks=5163 none=31296 watch=6335 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3436 next_offset=3436 rows=17180 candidates=760 replacements=760 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Seventy-Second Batch

Batch 172 outcome:

```text
offset=3436
covered_stocks=3456
batch_rows=17280
new_rows=100
candidate_count=766
rejected_count=16514
dry_run_replacements=766
resume_new_rows=0
```

Batch 172 new accepted examples:

```text
600732 gs_raw_buy score_delta=17.122932 validation_delta=1.990648
600742 activity_breakout score_delta=12.536496 validation_delta=25.672520
600729 gs_raw_buy score_delta=8.986535 validation_delta=12.402225
600744 gs_pullback_confirm score_delta=8.842282 validation_delta=2.634344
600734 activity_breakout score_delta=7.585015 validation_delta=17.205457
600739 gs_raw_buy score_delta=4.766280 validation_delta=2.942459
```

Cumulative accepted distribution after batch 172:

```text
activity_breakout=433
gs_raw_buy=152
gs_pullback_confirm=103
volume_base_breakout=78
```

Missing-result status combinations after batch 172:

```text
missing_baseline_result / ok = 2185
missing_baseline_result / missing_optuna_result = 784
ok / missing_optuna_result = 72
missing_without_reason = 0
```

State-store refresh after batch 172:

```text
Research Cache: rows=37726 stocks=5163 local_optuna=16424 production=21302 candidates=766 data_latest_date=2026-05-19
Incremental Evaluator: rows=37726 stocks=5163 clean=37726 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=37726 stocks=5163 none=31370 watch=6356 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3456 next_offset=3456 rows=17280 candidates=766 replacements=766 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Seventy-Third Batch

Batch 173 outcome:

```text
offset=3456
covered_stocks=3476
batch_rows=17380
new_rows=100
candidate_count=770
rejected_count=16610
dry_run_replacements=770
resume_new_rows=0
```

Batch 173 new accepted examples:

```text
600757 activity_breakout score_delta=19.029994 validation_delta=8.683559
600753 gs_pullback_confirm score_delta=9.104039 validation_delta=11.160254
600761 volume_base_breakout score_delta=9.041005 validation_delta=21.598008
600763 activity_breakout score_delta=3.862897 validation_delta=12.311999
```

Cumulative accepted distribution after batch 173:

```text
activity_breakout=435
gs_raw_buy=152
gs_pullback_confirm=104
volume_base_breakout=79
```

Missing-result status combinations after batch 173:

```text
missing_baseline_result / ok = 2199
missing_baseline_result / missing_optuna_result = 788
ok / missing_optuna_result = 73
missing_without_reason = 0
```

State-store refresh after batch 173:

```text
Research Cache: rows=37821 stocks=5163 local_optuna=16519 production=21302 candidates=770 data_latest_date=2026-05-19
Incremental Evaluator: rows=37821 stocks=5163 clean=37821 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=37821 stocks=5163 none=31454 watch=6367 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3476 next_offset=3476 rows=17380 candidates=770 replacements=770 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Seventy-Fourth Batch

Batch 174 outcome:

```text
offset=3476
covered_stocks=3496
batch_rows=17480
new_rows=100
candidate_count=775
rejected_count=16705
dry_run_replacements=775
resume_new_rows=0
```

Batch 174 new accepted examples:

```text
600789 activity_breakout score_delta=27.758781 validation_delta=26.692463
600785 activity_breakout score_delta=23.026285 validation_delta=10.247290
600770 activity_breakout score_delta=11.576400 validation_delta=40.299427
600771 gs_raw_buy score_delta=7.129999 validation_delta=13.505237
600790 gs_raw_buy score_delta=5.561272 validation_delta=10.761086
```

Cumulative accepted distribution after batch 174:

```text
activity_breakout=438
gs_raw_buy=154
gs_pullback_confirm=104
volume_base_breakout=79
```

Missing-result status combinations after batch 174:

```text
missing_baseline_result / ok = 2212
missing_baseline_result / missing_optuna_result = 793
ok / missing_optuna_result = 73
missing_without_reason = 0
```

State-store refresh after batch 174:

```text
Research Cache: rows=37916 stocks=5163 local_optuna=16614 production=21302 candidates=775 data_latest_date=2026-05-19
Incremental Evaluator: rows=37916 stocks=5163 clean=37916 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=37916 stocks=5163 none=31533 watch=6383 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3496 next_offset=3496 rows=17480 candidates=775 replacements=775 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Seventy-Fifth Batch

Batch 175 outcome:

```text
offset=3496
covered_stocks=3516
batch_rows=17580
new_rows=100
candidate_count=777
rejected_count=16803
dry_run_replacements=777
resume_new_rows=0
```

Batch 175 new accepted examples:

```text
600807 activity_breakout score_delta=8.019649 validation_delta=8.602555
600795 gs_raw_buy score_delta=3.771136 validation_delta=2.530158
```

Cumulative accepted distribution after batch 175:

```text
activity_breakout=439
gs_raw_buy=155
gs_pullback_confirm=104
volume_base_breakout=79
```

Missing-result status combinations after batch 175:

```text
missing_baseline_result / ok = 2226
missing_baseline_result / missing_optuna_result = 794
ok / missing_optuna_result = 73
missing_without_reason = 0
```

State-store refresh after batch 175:

```text
Research Cache: rows=38015 stocks=5163 local_optuna=16713 production=21302 candidates=777 data_latest_date=2026-05-19
Incremental Evaluator: rows=38015 stocks=5163 clean=38015 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=38015 stocks=5163 none=31616 watch=6399 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3516 next_offset=3516 rows=17580 candidates=777 replacements=777 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Seventy-Sixth Batch

Batch 176 outcome:

```text
offset=3516
covered_stocks=3536
batch_rows=17680
new_rows=100
candidate_count=784
rejected_count=16896
dry_run_replacements=784
resume_new_rows=0
```

Batch 176 new accepted examples:

```text
600838 activity_breakout score_delta=22.211962 validation_delta=18.328829
600839 activity_breakout score_delta=10.488868 validation_delta=12.085260
600831 activity_breakout score_delta=8.303416 validation_delta=14.000347
600841 activity_breakout score_delta=7.932655 validation_delta=5.344126
600838 volume_base_breakout score_delta=6.648757 validation_delta=25.599368
600844 activity_breakout score_delta=5.785147 validation_delta=2.853110
600835 gs_raw_buy score_delta=4.541529 validation_delta=12.455445
```

Cumulative accepted distribution after batch 176:

```text
activity_breakout=444
gs_raw_buy=156
gs_pullback_confirm=104
volume_base_breakout=80
```

Missing-result status combinations after batch 176:

```text
missing_baseline_result / ok = 2236
missing_baseline_result / missing_optuna_result = 799
ok / missing_optuna_result = 74
missing_without_reason = 0
```

State-store refresh after batch 176:

```text
Research Cache: rows=38109 stocks=5163 local_optuna=16807 production=21302 candidates=784 data_latest_date=2026-05-19
Incremental Evaluator: rows=38109 stocks=5163 clean=38109 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=38109 stocks=5163 none=31695 watch=6414 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3536 next_offset=3536 rows=17680 candidates=784 replacements=784 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Seventy-Seventh Batch

Batch 177 outcome:

```text
offset=3536
covered_stocks=3556
batch_rows=17780
new_rows=100
candidate_count=789
rejected_count=16991
dry_run_replacements=789
resume_new_rows=0
```

Batch 177 new accepted examples:

```text
600850 activity_breakout score_delta=12.191319 validation_delta=2.846159
600854 gs_raw_buy score_delta=5.362741 validation_delta=3.663094
600858 activity_breakout score_delta=4.886338 validation_delta=10.182488
600857 activity_breakout score_delta=3.838099 validation_delta=4.202714
600848 gs_raw_buy score_delta=3.239982 validation_delta=12.118762
```

Cumulative accepted distribution after batch 177:

```text
activity_breakout=447
gs_raw_buy=158
gs_pullback_confirm=104
volume_base_breakout=80
```

Missing-result status combinations after batch 177:

```text
missing_baseline_result / ok = 2245
missing_baseline_result / missing_optuna_result = 803
ok / missing_optuna_result = 74
missing_without_reason = 0
```

State-store refresh after batch 177:

```text
Research Cache: rows=38205 stocks=5163 local_optuna=16903 production=21302 candidates=789 data_latest_date=2026-05-19
Incremental Evaluator: rows=38205 stocks=5163 clean=38205 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=38205 stocks=5163 none=31771 watch=6434 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3556 next_offset=3556 rows=17780 candidates=789 replacements=789 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Seventy-Eighth Batch

Batch 178 outcome:

```text
offset=3556
covered_stocks=3576
batch_rows=17880
new_rows=100
candidate_count=795
rejected_count=17085
dry_run_replacements=795
resume_new_rows=0
```

Batch 178 new accepted examples:

```text
600888 volume_base_breakout score_delta=22.460231 validation_delta=62.291674
600876 activity_breakout score_delta=13.894706 validation_delta=10.218774
600873 volume_base_breakout score_delta=13.269451 validation_delta=19.333482
600888 gs_raw_buy score_delta=11.011385 validation_delta=2.663858
600876 gs_raw_buy score_delta=10.732225 validation_delta=1.857833
600881 activity_breakout score_delta=9.865980 validation_delta=25.150522
```

Cumulative accepted distribution after batch 178:

```text
activity_breakout=449
gs_raw_buy=160
gs_pullback_confirm=104
volume_base_breakout=82
```

Missing-result status combinations after batch 178:

```text
missing_baseline_result / ok = 2265
missing_baseline_result / missing_optuna_result = 805
ok / missing_optuna_result = 74
missing_without_reason = 0
```

State-store refresh after batch 178:

```text
Research Cache: rows=38303 stocks=5163 local_optuna=17001 production=21302 candidates=795 data_latest_date=2026-05-19
Incremental Evaluator: rows=38303 stocks=5163 clean=38303 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=38303 stocks=5163 none=31850 watch=6453 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3576 next_offset=3576 rows=17880 candidates=795 replacements=795 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Seventy-Ninth Batch

Batch 179 outcome:

```text
offset=3576
covered_stocks=3596
batch_rows=17980
new_rows=100
candidate_count=797
rejected_count=17183
dry_run_replacements=797
resume_new_rows=0
```

Batch 179 new accepted examples:

```text
600909 volume_base_breakout score_delta=17.923353 validation_delta=40.513327
600895 activity_breakout score_delta=5.588824 validation_delta=4.566674
```

Cumulative accepted distribution after batch 179:

```text
activity_breakout=450
gs_raw_buy=160
gs_pullback_confirm=104
volume_base_breakout=83
```

Missing-result status combinations after batch 179:

```text
missing_baseline_result / ok = 2279
missing_baseline_result / missing_optuna_result = 809
ok / missing_optuna_result = 74
missing_without_reason = 0
```

State-store refresh after batch 179:

```text
Research Cache: rows=38399 stocks=5163 local_optuna=17097 production=21302 candidates=797 data_latest_date=2026-05-19
Incremental Evaluator: rows=38399 stocks=5163 clean=38399 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=38399 stocks=5163 none=31924 watch=6475 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3596 next_offset=3596 rows=17980 candidates=797 replacements=797 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Eightieth Batch

Batch 180 outcome:

```text
offset=3596
covered_stocks=3616
batch_rows=18080
new_rows=100
candidate_count=805
rejected_count=17275
dry_run_replacements=805
resume_new_rows=0
```

Batch 180 new accepted examples:

```text
600959 activity_breakout score_delta=21.895185 validation_delta=3.514312
600929 activity_breakout score_delta=18.511607 validation_delta=21.065239
600938 activity_breakout score_delta=16.299693 validation_delta=1.497350
600933 gs_raw_buy score_delta=12.737131 validation_delta=4.070443
600966 activity_breakout score_delta=12.485009 validation_delta=1.906396
600955 gs_raw_buy score_delta=4.237866 validation_delta=1.598437
600968 gs_raw_buy score_delta=3.873072 validation_delta=8.399958
600962 gs_pullback_confirm score_delta=3.339689 validation_delta=0.000000
```

Cumulative accepted distribution after batch 180:

```text
activity_breakout=454
gs_raw_buy=163
gs_pullback_confirm=105
volume_base_breakout=83
```

Missing-result status combinations after batch 180:

```text
missing_baseline_result / ok = 2290
missing_baseline_result / missing_optuna_result = 815
ok / missing_optuna_result = 74
missing_without_reason = 0
```

State-store refresh after batch 180:

```text
Research Cache: rows=38493 stocks=5164 local_optuna=17191 production=21302 candidates=805 data_latest_date=2026-05-19
Incremental Evaluator: rows=38493 stocks=5164 clean=38493 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=38493 stocks=5164 none=32003 watch=6490 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3616 next_offset=3616 rows=18080 candidates=805 replacements=805 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Eighty-First Batch

Batch 181 outcome:

```text
offset=3616
covered_stocks=3636
batch_rows=18180
new_rows=100
candidate_count=811
rejected_count=17369
dry_run_replacements=811
resume_new_rows=0
```

Batch 181 new accepted examples:

```text
600970 activity_breakout score_delta=34.109488 validation_delta=10.030763
600969 activity_breakout score_delta=32.483100 validation_delta=52.376741
600981 activity_breakout score_delta=17.285100 validation_delta=8.264443
600985 activity_breakout score_delta=15.017509 validation_delta=8.096344
600992 activity_breakout score_delta=13.271102 validation_delta=20.995700
600971 gs_pullback_confirm score_delta=3.360674 validation_delta=2.213112
```

Cumulative accepted distribution after batch 181:

```text
activity_breakout=459
gs_raw_buy=163
gs_pullback_confirm=106
volume_base_breakout=83
```

Missing-result status combinations after batch 181:

```text
missing_baseline_result / ok = 2301
missing_baseline_result / missing_optuna_result = 820
ok / missing_optuna_result = 74
missing_without_reason = 0
```

State-store refresh after batch 181:

```text
Research Cache: rows=38588 stocks=5164 local_optuna=17286 production=21302 candidates=811 data_latest_date=2026-05-19
Incremental Evaluator: rows=38588 stocks=5164 clean=38588 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=38588 stocks=5164 none=32080 watch=6508 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3636 next_offset=3636 rows=18180 candidates=811 replacements=811 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Eighty-Second Batch

Batch 182 outcome:

```text
offset=3636
covered_stocks=3656
batch_rows=18280
new_rows=100
candidate_count=815
rejected_count=17465
dry_run_replacements=815
resume_new_rows=0
```

Batch 182 new accepted examples:

```text
600993 gs_pullback_confirm score_delta=48.606662 validation_delta=38.654822
601011 gs_pullback_confirm score_delta=13.588890 validation_delta=5.422394
601015 gs_raw_buy score_delta=9.167544 validation_delta=0.828033
601001 activity_breakout score_delta=3.981771 validation_delta=2.391719
```

Cumulative accepted distribution after batch 182:

```text
activity_breakout=460
gs_raw_buy=164
gs_pullback_confirm=108
volume_base_breakout=83
```

Missing-result status combinations after batch 182:

```text
missing_baseline_result / ok = 2316
missing_baseline_result / missing_optuna_result = 821
ok / missing_optuna_result = 74
missing_without_reason = 0
```

State-store refresh after batch 182:

```text
Research Cache: rows=38687 stocks=5164 local_optuna=17385 production=21302 candidates=815 data_latest_date=2026-05-19
Incremental Evaluator: rows=38687 stocks=5164 clean=38687 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=38687 stocks=5164 none=32159 watch=6528 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3656 next_offset=3656 rows=18280 candidates=815 replacements=815 missing_without_reason=0 consistency.ready=True
```

## 2026-05-20 Follow-up: One-Hundred-Eighty-Third Batch

Batch 183 outcome:

```text
offset=3656
covered_stocks=3676
batch_rows=18380
new_rows=100
candidate_count=825
rejected_count=17555
dry_run_replacements=825
resume_new_rows=0
```

Batch 183 new accepted examples:

```text
601069 activity_breakout score_delta=21.049968 validation_delta=3.610472
601059 activity_breakout score_delta=18.130402 validation_delta=19.149999
601018 activity_breakout score_delta=16.633721 validation_delta=12.458391
601021 gs_raw_buy score_delta=14.260895 validation_delta=6.992419
601018 volume_base_breakout score_delta=13.820762 validation_delta=1.073547
601077 volume_base_breakout score_delta=13.078859 validation_delta=0.105769
601068 volume_base_breakout score_delta=10.750529 validation_delta=10.860990
601059 gs_raw_buy score_delta=9.228839 validation_delta=2.898996
601061 gs_pullback_confirm score_delta=6.799681 validation_delta=0.960321
601088 gs_raw_buy score_delta=4.756460 validation_delta=5.570694
```

Cumulative accepted distribution after batch 183:

```text
activity_breakout=463
gs_raw_buy=167
gs_pullback_confirm=109
volume_base_breakout=86
```

Missing-result status combinations after batch 183:

```text
missing_baseline_result / ok = 2332
missing_baseline_result / missing_optuna_result = 826
ok / missing_optuna_result = 74
missing_without_reason = 0
```

State-store refresh after batch 183:

```text
Research Cache: rows=38782 stocks=5165 local_optuna=17480 production=21302 candidates=825 data_latest_date=2026-05-19
Incremental Evaluator: rows=38782 stocks=5165 clean=38782 dirty=0 target_data_date=2026-05-19
Drift Trigger: rows=38782 stocks=5165 none=32234 watch=6548 reevaluate=0 reoptimize=0
Workflow Checkpoint: covered=3676 next_offset=3676 rows=18380 candidates=825 replacements=825 missing_without_reason=0 consistency.ready=True
```
