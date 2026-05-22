# Formula Indicator Chart Audit

- date: `2026-05-19`
- scope: stock detail chart indicators for formula strategies

## Issue

The detail page previously hard-coded the second chart as `MACD · DIF / DEA`, even when the selected strategy was a formula strategy. This did not satisfy the goal requirement for formula-specific indicators:

- GS: `X3 / X36`
- 均线筑底: `MA5 / MA90 / MA145`
- 活跃度: `X15 / 强势线 / 大牛线`
- 巨量蓄势: platform range

## Changes

- `/api/chart/{code}` now returns a generic `indicator_chart` payload.
- MACD strategies return `DIF`, `DEA`, and `MACD`.
- `GS回调确认` and `GS原始买点` return `X3`, `X36`, and historical quick-buy rate when available.
- `均线筑底突破` returns short/mid/long MA series.
- `活跃度大牛突破` returns `X15`, `强势线`, and `大牛线`.
- `巨量蓄势启动` returns extended platform-low and platform-high series for visual range inspection.
- `/api/chart/{code}` now also returns `signal_points`; formula charts use formula entry points as price markers instead of MACD crosses.
- The front-end indicator chart title, legend, and datasets now render from `indicator_chart` instead of hard-coded MACD datasets.

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

Chart API checks:

| strategy | code | indicator kind | datasets | signal points |
|---|---|---|---|---:|
| `tdx_12_26_9` | `301511` | `macd` | DIF, DEA, MACD | 0 |
| `formula_gs_pullback_confirm` | `002718` | `gs` | X3, X36, 历史快买率 | 3 |
| `formula_ma_base_breakout` | `000004` | `ma_base` | 短均线, 中均线, 长均线 | 0 |
| `formula_activity_breakout` | `301511` | `activity` | X15, 强势线, 大牛线 | 16 |
| `formula_volume_base_breakout` | `301511` | `volume_base` | 平台低, 平台高 | 13 |

Local HTTP checks:

```text
/ 200
/api/status 200
/api/unified 200
/api/chart/301511?strategy=formula_volume_base_breakout 200
/api/chart/002718?strategy=formula_gs_pullback_confirm 200
```

Unified summary remained unchanged:

```text
{'total': 5201, 'today_recommended': 97, 'buy_window': 2425, 'current_signal': 5190, 'multi_signal': 56, 'multi_family': 56, 'current_multi_family': 4455, 'profiles': 10}
```

## Residual Risk

- Formula-specific indicator charts are now present, but the price chart still has a generic legend text for signal markers. A later UI polish pass should rename marker labels dynamically from “金叉/死叉” to “公式买点/卖点” when a formula strategy is selected.

## 2026-05-19 Follow-up: Price Chart Marker Labels

Changes:

- Added a dynamic price-chart legend.
- MACD strategies keep `金叉 / 死叉` marker labels.
- Formula strategies show `公式买点` and hide the death-cross marker label.
- Formula buy points use a diamond marker shape on the price chart.
- The empty effectiveness-chart message now says `无已完成交易` instead of `无已完成金叉交易`.

Verification:

```text
python -m py_compile compute.py main.py execution_model.py formula_engine.py scripts/*.py
node --check /tmp/bestchoice_index_scripts.js
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
```

Local HTTP checks:

```text
/ 200
/api/unified 200
/api/chart/301511?strategy=formula_volume_base_breakout 200 indicator=volume_base signals=13
/api/chart/301511?strategy=tdx_12_26_9 200 indicator=macd signals=0
```

Updated residual risk:

- The main formula indicator and price-marker semantics are now corrected. Remaining chart polish is lower priority and should focus on richer formula-specific annotations, such as labeling volume-platform breakout spans directly on the price chart.

## 2026-05-19 Follow-up: Volume Platform Band

Changes:

- Added price-chart structure bands derived from `indicator_chart` data.
- For `volume_base` charts, the price chart now fills the area between `平台低` and `平台高`.
- The price-chart legend now includes `平台区间` when such a structure band exists.
- This is a visual overlay only; it does not alter formula signals, backtests, recommendation logic, or trade generation.

Verification:

```text
python -m py_compile compute.py main.py execution_model.py formula_engine.py scripts/*.py
node --check /tmp/bestchoice_index_scripts.js
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
```

Chart data:

```text
/api/chart/301511?strategy=formula_volume_base_breakout 200
indicator volume_base
platform non-null points [170, 170]
signal_points 13
```

Local HTTP checks:

```text
/ 200
/api/unified 200
/api/chart/301511?strategy=formula_volume_base_breakout 200
```

Updated residual risk:

- Volume platform structure is now visible on the price chart. Future chart polish can add text labels for individual platform breakout dates, but the core structure annotation is present.

## 2026-05-19 Follow-up: Formula Sell Points

Changes:

- `/api/chart/{code}` now returns formula exit points in `signal_points` with `type=exit`.
- Formula price charts map `entry` to `公式买点` and `exit` to `公式卖点`.
- Formula buy markers use a red diamond, and formula sell markers use a green square.
- MACD charts keep the original golden/death cross marker behavior.

Verification:

```text
python -m py_compile compute.py main.py execution_model.py formula_engine.py scripts/*.py
node --check /tmp/bestchoice_index_scripts.js
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
```

Chart data:

```text
301511 formula_volume_base_breakout kind=volume_base signals={'entry': 13, 'exit': 76}
002718 formula_gs_pullback_confirm kind=gs signals={'entry': 3, 'exit': 7}
```

Local HTTP checks:

```text
/ 200
/api/status 200
/api/chart/301511?strategy=formula_volume_base_breakout 200 volume_base {'entry': 13, 'exit': 76}
/api/chart/002718?strategy=formula_gs_pullback_confirm 200 gs {'entry': 3, 'exit': 7}
```

Updated residual risk:

- Formula buy/sell marker semantics are now complete. Remaining chart polish is optional text annotation for individual breakout or exit dates.
