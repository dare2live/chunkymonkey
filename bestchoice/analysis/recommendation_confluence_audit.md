# Recommendation Confluence Audit

- date: `2026-05-19`
- scope: unified stock pool recommendation gating
- profiles: `10`
- total_stocks: `5201`

## Issue

The unified pool originally treated every current MACD parameter group and formula hit as an independent confluence contributor. That inflated the recommendation count because ordinary current signals, including multiple MACD variants, could satisfy the multi-strategy condition.

## Fix

- MACD variants now share one signal family: `macd`.
- Formula strategies use their formula id as the signal family.
- `confluence_score` remains the current-signal family count for diagnostics.
- Recommendation gating now uses only qualified buy-window signals:
  - signal is in the buy window;
  - win rate is at least `0.55`;
  - average return is positive;
  - effectiveness score is at least `50`.
- Added `qualified_buy_signal_count` and `qualified_buy_family_count`.
- UI "多策略共振" now uses `qualified_buy_family_count >= 2`, not raw current confluence.

## Verification

Command:

```bash
BESTCHOICE_SKIP_WARMUP=1 BESTCHOICE_UNIFIED_MAX_COMPUTE=4 python - <<'PY'
import time
from main import engine
start=time.time()
for i in range(120):
    d=engine.unified_data()
    if d and d.get('ready'):
        print('elapsed', round(time.time()-start,1))
        print('summary', d['summary'])
        break
    time.sleep(2)
PY
```

Result:

```text
elapsed 134.2
summary {'total': 5201, 'today_recommended': 97, 'buy_window': 2425, 'current_signal': 5190, 'multi_signal': 56, 'multi_family': 56, 'current_multi_family': 4455, 'profiles': 10}
```

Sample checks:

| code | recommended | qualified families | current confluence | buy-window signals | reason |
|---|---:|---:|---:|---:|---|
| `301511` | no | 1 | 2 | 1 | 缺少胜率/收益达标的回测支持 |
| `301658` | no | 0 | 3 | 2 | 缺少胜率/收益达标的回测支持 |
| `688700` | no | 0 | 2 | 5 | 缺少胜率/收益达标的回测支持 |
| `002718` | no | 0 | 5 | 0 | 当前未处于最佳买入窗口 |

Top recommended rows now have explicit qualified confluence, for example `301377` and `688257` each show `4 个达标策略共振，且处于买入窗口`.

## Residual Risk

- Full cold unified loading took about `134s` in this verification. This is acceptable for cached batch startup but still worth optimizing if the UI needs faster first paint.
- `current_multi_family` remains high by design because it tracks broad current signal overlap, not recommendation quality.

## 2026-05-19 Follow-up: Tradability Gate

Issue:

- `goal.md` requires today's recommendations to consider execution feasibility, including whether a signal is frequently blocked by suspension, limit-up buys, or delayed exits.
- Historical caches previously stored the executed trade series, but there was no compact per-strategy execution-quality metric available to the unified recommendation gate.

Fix:

- Added cached `execution` metrics per strategy/stock:
  - `total_signals`
  - `completed_trades`
  - `skipped_buys`
  - `pending_buys`
  - `delayed_buys`
  - `delayed_sells`
  - `untradable_events`
  - `completion_rate`
  - `skipped_buy_rate`
  - `untradable_rate`
- Raised strategy cache schema and unified snapshot schema to `9`.
- Strategy-card payloads now include `completion_rate`, `skipped_buy_rate`, and `untradable_rate`.
- Recommendation qualification now excludes buy-window signals with `untradable_rate > 0.20`.

Important correction:

- During cache rebuild, `formula_volume_base_breakout` exposed a historical-path bug: the historical computation was incorrectly passing the current-path `__latest_only` flag and produced `with_signal=0`.
- Fixed by keeping `__latest_only` only for current-state evaluation, then rebuilt the volume-base historical cache.

Verification:

```text
python -m py_compile compute.py main.py execution_model.py formula_engine.py scripts/*.py
node --check /tmp/bestchoice_index_scripts.js
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
```

Formula cache audit:

```text
ready_formula_caches=5
formula_gs_pullback_confirm  stocks_with_signal=3801 avg_untradable_rate=0.012736
formula_gs_raw_buy           stocks_with_signal=5131 avg_untradable_rate=0.016698
formula_ma_base_breakout     stocks_with_signal=905  avg_untradable_rate=0.055801
formula_activity_breakout    stocks_with_signal=5131 avg_untradable_rate=0.025192
formula_volume_base_breakout stocks_with_signal=5131 avg_untradable_rate=0.015992
```

Unified result:

```text
elapsed 195.666
summary {'total': 5201, 'today_recommended': 91, 'buy_window': 2425, 'current_signal': 5190, 'multi_signal': 54, 'multi_family': 54, 'current_multi_family': 4439, 'profiles': 10}
snapshot_elapsed 0.84
```

Sample checks:

| code | recommended | reason | best untradable rate | qualified families |
|---|---:|---|---:|---:|
| `301511` | no | 缺少胜率/收益达标的回测支持 | 0.0101 | 1 |
| `301658` | no | 缺少胜率/收益达标的回测支持 | 0.0 | 0 |
| `688700` | no | 缺少胜率/收益达标的回测支持 | 0.0 | 0 |
| `002718` | no | 当前未处于最佳买入窗口 | 0.0 | 0 |

HTTP checks:

```text
/ 200
/api/status 200
/api/unified 200
/api/chart/301511?strategy=formula_volume_base_breakout 200
/api/chart/688700 200
```

Updated residual risk:

- The first schema-9 rebuild took about `196s` because all strategy caches needed the new execution metrics. Subsequent unified snapshot reads are back under one second.
- `untradable_rate > 0.20` is a first operational threshold; future tuning can make it profile-specific if real candidates show materially different liquidity behavior.

## 2026-05-19 Follow-up: Per-Stock Optimized Holding Periods

Correction:

- Formula strategy production caches now use each stock/formula row from `analysis/stock_formula_best.csv`.
- Formula recommendations are no longer evaluated against the profile-level fixed holding period.
- Missing optimized rows are marked as missing optimization instead of falling back to default formula params.

New unified result:

```text
summary {'total': 5201, 'today_recommended': 37, 'buy_window': 1857, 'current_signal': 5190, 'multi_signal': 35, 'multi_family': 35, 'current_multi_family': 4267, 'profiles': 10}
```

Interpretation:

- The drop from `91` to `37` is expected: the prior recommendation count still included formula histories evaluated with fixed profile holding periods. The new count is stricter and uses per-stock optimized formula parameters and holding periods.
- Current formula signals in unified payloads were checked for missing optimization metadata:

```text
formula_signal_missing_optimized 0
```
