# TDX Keep Challenger Productionization Report

执行日期: 2026-05-04 Asia/Shanghai
目标: 将上一阶段验证出的 5 个 TDX keep 特征接入 production-grade challenger、shadow topK、API/前端展示和 promotion gate。生产 champion 不被替换。

## 安全边界

- 默认推荐模型选择已改为 lifecycle `status='champion'`，不再按 `mart_multidim_model.created_at DESC` 自动选择最新模型。
- 当前 champion: `multidim_v2_base_dense_v2_20260425_144552`。
- 当前 TDX keep challenger: `tdx_keep_challenger_20260504_110529`，lifecycle status = `challenger`。
- `/api/rec/daily-topk` 默认返回 champion；显式传 `model_id=tdx_keep_challenger_20260504_110529&run_mode=shadow` 才返回 challenger shadow 推荐。
- `mart_daily_recommendation` 已增加 `run_mode`；champion rows 为 `run_mode='champion' / is_primary=true`，challenger rows 为 `run_mode='shadow' / is_primary=false`。
- `run_daily_topk.py` 会拒绝 `shadow` 写 lifecycle champion，也会拒绝 `champion` 模式写非 champion 模型，避免操作层误覆盖正式推荐。

API 直接验证:

- default topK: model `multidim_v2_base_dense_v2_20260425_144552`，role `champion`，`selection_fallback=false`，count 3。
- explicit shadow topK: model `tdx_keep_challenger_20260504_110529`，role `challenger`，`run_mode='shadow'`，count 3。
- model comparison: champion vs challenger 可取，promotion gate = `FAIL`。
- TDX feature validation: manual keep = 5，PIT violations = 0。

## Schema 和特征契约

Schema: `m8_tdx_keep_challenger_v1`

第一版 production challenger 只纳入 5 个手工 keep overlay，不纳入自动 gpcw watch pool:

- `forecast_profit_yoy_mid`
- `avg_float_shares_change_pct_tdx`
- `ocf_to_profit_tdx`
- `fund_shares_qoq`
- `forecast_range_width`

训练特征实际为生产 baseline 54 列 + 上述 5 个 TDX keep overlay，共 59 列。自动 gpcw keep 特征保留为 optional extension / watch pool，未进入本版默认训练 schema。

## Challenger Panel

表: `fact_feature_panel_tdx_keep_challenger`

构建命令:

```bash
python3 backend/scripts/build_tdx_keep_challenger_panel.py \
  --feature-set-id tdx_keep_challenger_v1 \
  --source-feature-set-id tdx_f10_gpcw_v1 \
  --start 2023-01-01
```

结果:

- rows: 4,022,758
- stocks: 5,200
- dates: 799
- min_date: 2023-01-03
- max_date: 2026-04-23
- baseline features: 54
- keep features: 5

Keep feature coverage:

| feature | coverage |
|---|---:|
| `forecast_profit_yoy_mid` | 93.059% |
| `avg_float_shares_change_pct_tdx` | 65.066% |
| `ocf_to_profit_tdx` | 92.991% |
| `fund_shares_qoq` | 69.326% |
| `forecast_range_width` | 93.059% |

生产 `fact_feature_panel` 未被覆盖。

## Training

命令:

```bash
python3 backend/scripts/train_tdx_keep_challenger_model.py \
  --feature-table fact_feature_panel_tdx_keep_challenger \
  --feature-set-id tdx_keep_challenger_v1 \
  --schema-version m8_tdx_keep_challenger_v1 \
  --model-prefix tdx_keep_challenger \
  --trials 80
```

结果:

- model_id: `tdx_keep_challenger_20260504_110529`
- lifecycle: `challenger`
- n_features: 59
- keep features present: all 5
- holdout IC: 0.017031
- holdout RankIC: 0.039299
- holdout long-short spread: 0.009233
- holdout top winrate: 0.482375
- promote_to_champion: false

模型 pkl 写入 `data/multidim_models/tdx_keep_challenger_20260504_110529.pkl`，不提交。

## Walk-forward

命令:

```bash
python3 backend/scripts/run_multidim_walkforward.py \
  --model-id tdx_keep_challenger_20260504_110529 \
  --feature-table fact_feature_panel_tdx_keep_challenger \
  --feature-set-id tdx_keep_challenger_v1 \
  --feature-group tdx_keep_v1 \
  --save-predictions
```

结果:

- run_id: `walkforward_20260504_110546`
- folds: 5
- ok folds: 5
- average RankIC: 0.078537
- RankIC std: 0.083679
- average long-short spread: 0.025764

Fold RankIC:

- fold 1: 0.0780
- fold 2: 0.2092
- fold 3: 0.0803
- fold 4: -0.0210
- fold 5: 0.0462

## Shadow TopK

Challenger shadow command:

```bash
python3 backend/scripts/run_daily_topk.py \
  --model-id tdx_keep_challenger_20260504_110529 \
  --mode shadow \
  --feature-table fact_feature_panel_tdx_keep_challenger \
  --feature-set-id tdx_keep_challenger_v1 \
  --limit 100
```

Champion refresh command:

```bash
python3 backend/scripts/run_daily_topk.py --mode champion --limit 100
```

Rows:

| model | run_mode | track_id | rows | primary rows | date |
|---|---|---|---:|---:|---|
| `multidim_v2_base_dense_v2_20260425_144552` | champion | primary | 100 | 100 | 2026-04-23 |
| `tdx_keep_challenger_20260504_110529` | shadow | `shadow_tdx_keep_challenger_20260504_110529` | 100 | 0 | 2026-04-23 |

## Portfolio Backtest

Challenger command:

```bash
python3 backend/scripts/backtest_model_portfolio.py --model-id tdx_keep_challenger_20260504_110529
```

Top20, 15 bps comparison:

| model | total return | annualized | max drawdown | Sharpe | avg turnover |
|---|---:|---:|---:|---:|---:|
| champion | 19.668% | 47.215% | -13.860% | 1.724 | 1.600 |
| TDX keep challenger | 26.875% | 66.978% | -15.726% | 2.114 | 1.283 |

The challenger has stronger portfolio return in this window, but drawdown is worse and other gates still fail.

## Drift

命令:

```bash
python3 backend/scripts/compute_feature_drift.py \
  --model-id tdx_keep_challenger_20260504_110529 \
  --feature-table fact_feature_panel_tdx_keep_challenger \
  --recent-days 20 \
  --train-days 365
```

Latest drift snapshot:

- ok: 16
- warn: 6
- critical: 2
- unknown: 6
- lifecycle `drift_score`: 0.093570

Because critical drift exists, the challenger must remain rejected/shadow.

## Promotion Gate

命令:

```bash
python3 backend/scripts/evaluate_tdx_keep_promotion_gate.py \
  --model-id tdx_keep_challenger_20260504_110529 \
  --feature-set-id tdx_keep_challenger_v1
```

Latest gate:

- gate_run_id: `tdx_keep_gate_20260504_111301`
- promotion_status: `FAIL`
- decision: `reject`
- champion_model_id: `multidim_v2_base_dense_v2_20260425_144552`
- challenger_model_id: `tdx_keep_challenger_20260504_110529`

Gate details:

| gate | status | evidence |
|---|---|---|
| model | PASS | challenger model exists |
| PIT | PASS | violations = 0 |
| coverage | PASS | 5/5 keep features coverage >= 60% |
| rank_ic | FAIL | challenger 0.039299 < required 0.042415 |
| long_short | PASS | challenger 0.009233 >= required 0.009197 |
| max_drawdown | PASS | challenger -0.157264 within 20% tolerance of champion -0.138596 |
| drift | FAIL | 2 critical drift features |
| shadow_topk | PASS | 100 shadow rows generated |
| api_safety | PASS | default remains lifecycle champion, no fallback |

Blockers:

- RankIC uplift is insufficient under the gate rule.
- Critical drift exists.

No champion replacement was performed.

## Frontend/API

New/updated APIs:

- `GET /api/rec/daily-topk`: default champion-only; explicit `model_id` supports challenger/shadow.
- `GET /api/rec/model-performance`: default champion-only; explicit challenger supported.
- `GET /api/rec/stock-prediction`: default champion-only.
- `GET /api/rec/tdx-feature-validation`: keep/watch/drop + IC/coverage/PIT/source.
- `GET /api/rec/model-comparison`: champion vs challenger metrics, shadow rows and promotion gate.
- `GET /api/data_health/sources`: includes `source_priorities` for tdxhub/miaoxiang/akshare business split.

Frontend changes:

- Model monitor page now displays Champion, TDX Keep Challenger and Promotion Gate.
- TDX feature validation section shows 5 keep features, watch/drop reasons and PIT violations.
- Data source pages show tdxhub main supply, miaoxiang retained domains and akshare fallback.
- Challenger is labeled Shadow / Not promoted and is not mixed into formal recommendation by default.

## Verification Notes

Implementation has targeted unit coverage for:

- lifecycle champion default selection over latest challenger;
- explicit challenger model request;
- TDX keep schema excluding auto watch features;
- challenger panel generation without touching champion panel;
- promotion gate WAIT/FAIL behavior for missing/failed evidence.

Final verification:

- `python3 -m py_compile ...`: passed for edited backend routers, scripts, services and tests.
- `node --check assets/js/app.js assets/js/data-view.js assets/js/data-health-view.js`: passed.
- `python3 -m pytest backend/tests/test_tdx_source.py backend/tests/test_tdx_f10_extra_client.py backend/tests/test_candidate_feature_pipeline.py backend/tests/test_phase0_daily_closure.py backend/tests/test_updater_daily_sync_metrics.py -q`: 31 passed.
- `python3 -m pytest backend/tests/test_tdx_keep_productionization.py backend/tests/test_system_routes.py backend/tests/test_openapi_contract.py -q`: 7 passed.
- `python3 backend/scripts/audit_stale_references.py`: no stale references detected.
- `python3 backend/scripts/data_health_snapshot.py --dry-run`: scanned 145 assets, green 64 / yellow 18 / red 63; red/yellow items are pre-existing freshness/orphan health debt, not a TDX keep champion promotion.
- API direct check: default `/api/rec/daily-topk` returns champion `multidim_v2_base_dense_v2_20260425_144552`; explicit challenger shadow returns `tdx_keep_challenger_20260504_110529`; `/api/rec/model-comparison` reports gate `FAIL / reject`; `/api/rec/tdx-feature-validation` reports manual keep/watch/drop = 5/9/6 and PIT violations = 0.
- Local FastAPI smoke check: `python3 -m uvicorn main:app --host 127.0.0.1 --port 8765`; `GET /` returned `index.html`, `GET /api/rec/model-comparison` returned `FAIL / reject`, and `GET /api/rec/daily-topk?limit=1` returned the champion with `run_mode='champion'`.

## Risk

- Gate result is `FAIL`, so the correct operational decision is `reject`, not `promote_ready`.
- Drift has 2 critical features and 6 unknown features; this needs deeper feature-level investigation before any promotion.
- Challenger holdout RankIC is slightly above champion, but does not clear the configured uplift threshold.
- Current backtest comparison windows differ between champion historical run and challenger run. Gate still uses this as available evidence but does not pass the model.
- The local pkl and DuckDB rows are generated artifacts and are intentionally not committed.
