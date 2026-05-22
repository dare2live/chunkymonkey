# Strategy Detail Cards Audit

- date: `2026-05-19`
- scope: unified stock detail strategy cards

## Issue

The unified detail page already rendered a strategy-signal matrix, but each card did not expose enough execution and historical context to satisfy the stock-level aggregation goal. Missing or under-displayed fields included sell/evaluation price, max drawdown, Calmar, signal count, parameter summary, and explicit buy-window state.

## Changes

- Extended each `strategy_signal` payload with:
  - `buy_price_method`
  - `target_sell_price`
  - `eval_date`
  - `eval_price`
  - `ref_ret`
  - `ref_max_dd`
  - `reached_target`
  - `pending_buy`
  - `pending_reason`
- Expanded strategy cards in the detail drawer to show:
  - current status and signal date;
  - buy date / price;
  - sell or evaluation date / price;
  - best holding period;
  - recent return;
  - win rate;
  - average return;
  - average drawdown;
  - Calmar;
  - effectiveness score and label;
  - signal count;
  - optimized variant and score;
  - optimized parameter summary;
  - whether the signal is in the buy window, current-but-not-buy-window, or historical-only.
- Raised unified snapshot schema to `8` so old cached unified payloads do not hide the new fields.

## Verification

Scripts:

```text
python -m py_compile compute.py main.py execution_model.py formula_engine.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
node --check /tmp/bestchoice_index_scripts.js
```

Results:

```text
execution_model_smoke: ok
unified_data_smoke: ok
strategy_rebuild_audit: ready_formula_caches=5
```

Unified pool:

```text
summary {'total': 5201, 'today_recommended': 97, 'buy_window': 2425, 'current_signal': 5190, 'multi_signal': 56, 'multi_family': 56, 'current_multi_family': 4455, 'profiles': 10}
```

Sample payload checks confirmed the new card fields exist for:

| code | signals | best strategy | example added fields |
|---|---:|---|---|
| `301511` | 8 | 活跃度大牛突破 | `buy_price`, `eval_price`, `ref_ret`, `avg_dd`, `calmar`, `optimized_params` |
| `301658` | 8 | 通达信参数 · EMA(12,26,9) | `buy_price`, `eval_price`, `ref_ret`, `avg_dd`, `calmar` |
| `688700` | 9 | 通达信参数 · EMA(12,26,9) | `buy_price`, `eval_price`, `ref_ret`, `avg_dd`, `calmar` |
| `002718` | 9 | GS回调确认 | `buy_price`, `eval_price`, `ref_ret`, `avg_dd`, `calmar`, `optimized_params` |

Local HTTP checks against `http://127.0.0.1:8766`:

```text
/ 200
/api/status 200
/api/unified 200
/api/chart/301511 200
```

## Residual Risk

- Strategy cards now expose the required stock-level strategy summary, but formula-specific indicator charts are still not implemented. The current chart panel still switches by selected primary strategy and does not yet render GS X3/X36, MA5/MA90/MA145, activity X15, or volume-platform overlays as separate indicator modules.

## 2026-05-19 Follow-up: Execution Quality Fields

Changes:

- Strategy cards now expose execution feasibility metrics:
  - `成交率`
  - `不可成交`
- Card notes flag strategies whose `untradable_rate` is above `0.20`.
- Row normalization carries `completion_rate`, `skipped_buy_rate`, and `untradable_rate` from the primary signal.
- This aligns the detail view with the recommendation gate, which now excludes high-untradable-rate buy-window signals.

Verification:

```text
python -m py_compile compute.py main.py execution_model.py formula_engine.py scripts/*.py
node --check /tmp/bestchoice_index_scripts.js
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
```

Sample payload checks:

```text
301511 活跃度大牛突破 untradable_rate=0.0101 completion_rate=1.0
301658 活跃度大牛突破 untradable_rate=0.037 completion_rate=0.963
688700 活跃度大牛突破 untradable_rate=0.0117 completion_rate=0.9942
002718 活跃度大牛突破 untradable_rate=0.0562 completion_rate=0.9888
```

Updated residual risk:

- Execution feasibility is now visible on cards. UI can later add a list-level filter for `不可成交率`, but the recommendation gate already uses it.

## 2026-05-19 Follow-up: Optimized Sell Rule Field

Changes:

- Strategy detail cards now include `卖出规则`.
- Formula strategy signals display the audited best sell rule and score from unified data fields:
  - `optimized_sell_rule`
  - `optimized_sell_rule_score`

Sample payload checks:

```text
301511 GS原始买点 fixed_20
301658 活跃度大牛突破 fixed_30
688700 GS回调确认 fixed_30
002718 巨量蓄势启动 fixed_60
```

Validation:

```text
node inline script syntax check
/api/unified 200
```

## 2026-05-20 Follow-up: Per-Stock Sell Rule Display

Changes:

- Strategy cards now receive per-stock sell rules from production signals, not the full-market audit winner.
- This fixes the earlier mismatch where cards could show a per-stock best holding period such as `20` but sell rule `fixed_60`.

Sample payload checks:

```text
301511 巨量蓄势启动 fixed_20
301658 巨量蓄势启动 fixed_30
688700 巨量蓄势启动 fixed_5
002718 巨量蓄势启动 fixed_60
```
