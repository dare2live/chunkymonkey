# Unified Pool Performance Audit

- date: `2026-05-19`
- scope: unified stock pool startup and aggregation
- profiles: `10`
- total_stocks: `5201`

## Issue

Cold-start aggregation previously had to load or compute all unified-pool strategy sources before `/api/unified` could return ready data. A full cached build was measured at about `134s`, which is too slow for the main entry after a process restart.

## Fix

- Added a lightweight cache loader path that can skip `trade_series_json` when only list-level metrics are needed.
- Unified list aggregation now reads historical metrics without parsing full trade series.
- Chart/detail endpoints still load full trade series on demand, preserving holding intervals and strategy-effectiveness charts.
- Added a persistent unified-pool snapshot: `cache_unified.json.gz`.
- The snapshot signature includes the unified profile set, underlying profile cache signatures, data freshness, and schema version.
- If any strategy cache, formula logic, execution model, or data latest date changes, the snapshot signature changes and the unified pool rebuilds.

## Verification

First build after removing the snapshot:

```text
build_elapsed 134.3
summary {'total': 5201, 'today_recommended': 97, 'buy_window': 2425, 'current_signal': 5190, 'multi_signal': 56, 'multi_family': 56, 'current_multi_family': 4455, 'profiles': 10}
cache_unified.json.gz size: 3.0M
```

Second process load from the snapshot:

```text
load_elapsed 0.638
ready True
summary {'total': 5201, 'today_recommended': 97, 'buy_window': 2425, 'current_signal': 5190, 'multi_signal': 56, 'multi_family': 56, 'current_multi_family': 4455, 'profiles': 10}
```

Interface checks:

```text
/api/status 200
/api/unified 200
/api/chart/301511 200
/api/chart/688700 200
```

Chart checks confirmed `/api/chart` still returns recent-year dates and full `trade_series` data on demand.

## Residual Risk

- The first build after a cache/data/signature change still takes about `134s`; the optimization primarily improves process restart and repeat page-load behavior.
- Further first-build optimization should target current-signal computation for dense formula strategies, especially `formula_volume_base_breakout`.

## 2026-05-19 Follow-up: First-Build Optimization

Additional profiling showed per-strategy current-state cost:

```text
tdx_12_26_9                  ~4.0s
macd_10_22_8                 ~3.4s
macd_12_26_9                 ~4.3s
macd_14_30_11                ~3.5s
optuna_best                  ~3.9s
formula_gs_pullback_confirm  ~15.1s before optimization
formula_volume_base_breakout ~39.9s before optimization
```

Changes:

- Vectorized formula-engine rolling sum/max/min helpers.
- Precomputed `volume_base_breakout` MA10/MA20 arrays outside the inner platform scan.
- Limited current-state K-line window only for formulas where the recent trigger can be computed safely:
  - `volume_base_breakout`: `150` bars.
  - `activity_breakout`: `90` bars.
  - `GS回调确认` remains at `220` bars because reducing it changed current holding rows.
- Capped unified-pool build concurrency to `2`; local testing showed concurrency `4` caused DuckDB read contention and slower total builds.

Post-change per-strategy timings:

```text
formula_gs_pullback_confirm  ~7.5s, rows unchanged at 306
formula_volume_base_breakout ~18.8s, rows unchanged at 2361
```

Full first build with default unified concurrency `2`:

```text
build_elapsed 83.1
summary {'total': 5201, 'today_recommended': 97, 'buy_window': 2425, 'current_signal': 5190, 'multi_signal': 56, 'multi_family': 56, 'current_multi_family': 4455, 'profiles': 10}
```

Snapshot load after rebuild:

```text
load_elapsed 1.303
ready True
```

Interface checks after the optimization:

```text
/api/status 200
/api/unified 200
/api/chart/301511 200
/api/chart/688700 200
```

Updated residual risk:

- First build is improved from roughly `134s` to `83s`, but still not instant.
- Further optimization should avoid recomputing current-state K-line scans separately for MACD variants that share the same latest market data.

## 2026-05-19 Follow-up: Shared Current K-Line Cache

Additional change:

- Added an in-process latest K-line cache keyed by current-state query window.
- All unified-pool strategy builds in the same process can reuse the same DuckDB result arrays.
- This directly reduces repeated reads for the five MACD/Optuna strategies and reduces read contention during formula current-state builds.
- Raised unified snapshot schema to `3` so old snapshots do not mask the new current-state path.

Per-strategy timing after this change:

```text
tdx_12_26_9                  3.95s
macd_10_22_8                 1.73s
macd_12_26_9                 1.86s
macd_14_30_11                1.77s
optuna_best                  1.84s
formula_gs_pullback_confirm  5.14s
formula_gs_raw_buy           2.96s
formula_ma_base_breakout     2.71s
formula_activity_breakout    3.00s
formula_volume_base_breakout 19.41s
```

Full first build with default unified concurrency `2`:

```text
build_elapsed 50.3
summary {'total': 5201, 'today_recommended': 97, 'buy_window': 2425, 'current_signal': 5190, 'multi_signal': 56, 'multi_family': 56, 'current_multi_family': 4455, 'profiles': 10}
```

Snapshot load:

```text
load_elapsed 0.62
ready True
```

Interface checks:

```text
/api/status 200
/api/unified 200
/api/chart/301511 200
/api/chart/688700 200
```

Updated residual risk:

- First build is now about `50s`, down from roughly `134s`.
- The remaining bottleneck is `formula_volume_base_breakout` current-state platform scan, about `19s`.
- A deeper optimization should isolate only stocks with recent volume-base candidates before running the full platform scan.

## 2026-05-19 Follow-up: Volume Formula Hotspot Reduction

Profiling `formula_volume_base_breakout` showed the dominant cost was not trade generation but repeated short-slice `np.nanmean` / `np.nanmax` / `np.nanmin` calls inside the platform-condition scan:

```text
formula_engine.py:evaluate_condition 35.4s cumulative
numpy nanmean                     22.1s cumulative
numpy nanmax                       4.6s cumulative
numpy nanmin                       2.8s cumulative
```

Changes:

- Current-state `volume_base_breakout` uses an internal `__latest_only` path because the unified list only needs the latest actionable signal.
- The historical formula path still computes the full daily signal series.
- Current-state `volume_base_breakout` evaluates ordinary OHLCV arrays with `mean` / `max` / `min` rather than NaN-aware reductions, matching the non-NaN market data path and avoiding repeated nan-handling overhead.
- Raised unified snapshot schema to `7`.

Post-change verification:

```text
formula_volume_base_breakout 11.14s rows 2361
summary {'total': 2361, 'just_cross': 744, 'holding': 1617, 'with_history': 2332, 'today_candidates': 32, 'strong_picks': 10}
```

Full first build:

```text
build_elapsed 43.9
summary {'total': 5201, 'today_recommended': 97, 'buy_window': 2425, 'current_signal': 5190, 'multi_signal': 56, 'multi_family': 56, 'current_multi_family': 4455, 'profiles': 10}
```

Snapshot load:

```text
load_elapsed 0.584
ready True
```

Interface checks:

```text
/api/status 200
/api/unified 200
/api/chart/301511 200
/api/chart/688700 200
```

Updated residual risk:

- First build is now about `44s`, down from roughly `134s`.
- `formula_volume_base_breakout` is still the slowest single strategy at about `11s`; further reduction likely requires a vectorized prefilter for recent spike/platform candidates.

## 2026-05-19 Follow-up: Volume Formula Range Query Optimization

The previous hotspot still came from repeatedly scanning recent spike windows and short OHLCV slices in `volume_base_breakout`.

Changes:

- Precomputed spike indices and used `searchsorted` to select only eligible spike candidates.
- Replaced repeated slice means with prefix-sum range means.
- Replaced repeated platform high/low and breakout reference scans with sparse range max/min queries.
- Kept the formula semantics unchanged; this is a current-path and chart-path computation optimization only.

Equivalence checks:

```text
301511 signals {'entry': 13, 'exit': 76} platform points [('平台低', 170), ('平台高', 170)]
301658 signals {'entry': 2, 'exit': 120} platform points [('平台低', 40), ('平台高', 40)]
688700 signals {'entry': 4, 'exit': 95} platform points [('平台低', 60), ('平台高', 60)]
002718 signals {'entry': 17, 'exit': 58} platform points [('平台低', 160), ('平台高', 160)]
```

Single-strategy current build:

```text
formula_volume_base_breakout 5.9s
summary {'total': 2361, 'just_cross': 744, 'holding': 1617, 'with_history': 2332, 'today_candidates': 32, 'strong_picks': 10}
```

Full unified first build after removing the unified snapshot:

```text
build_elapsed 34.34
summary {'total': 5201, 'today_recommended': 97, 'buy_window': 2425, 'current_signal': 5190, 'multi_signal': 56, 'multi_family': 56, 'current_multi_family': 4455, 'profiles': 10}
```

Snapshot load:

```text
load_elapsed 0.955
ready True
```

Verification:

```text
python -m py_compile formula_engine.py compute.py main.py execution_model.py scripts/*.py
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
/api/chart/301511?strategy=formula_volume_base_breakout 200
/api/chart/688700 200
```

Updated residual risk:

- First unified build is now about `34s`, down from roughly `134s`.
- Further performance work should be based on fresh profiling; the previous volume-platform slice-scan hotspot has been materially reduced.
