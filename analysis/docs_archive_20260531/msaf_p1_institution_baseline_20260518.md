# MSAF P1: Institution Baseline Test (2026-05-18)

## 目标

接 institution_score 真 source 替换 placeholder None, 看 ensemble (lambdamart + institution) vs lambdamart-only KPI.

## Implementation

`load_institution_scores()` 用 mart_p0a_feature_label_panel_v4.lhb_inst_buy_30d 作 institution score:
- lhb_inst_buy_30d = 30 天内龙虎榜机构净买入次数 (整数 ≥ 0)
- 简化版 institution signal (不是 Codex 2.3 完整 4-class composite)
- 在 ensemble 接 institution_scores 参数 (regime 30% weight)

## 实测对比 (22 monthly obs, 2024-07-01~2026-04-13, neutral_cash=0%)

| variant | mean ann | median ann | CAGR | max_dd | sharpe | hit_rate | NAV_end |
|---|---:|---:|---:|---:|---:|---:|---:|
| lambdamart-only (default) | +63.21% | +34.88% | +69.15% | -21.38% | 1.347 | 63.64% | 2.50 |
| **with-institution simple (lhb_inst_buy_30d)** | +2.47% | +7.54% | **-2.71%** | **-30.91%** | 0.076 | 50.00% | 0.95 |

## Finding

**institution raw signal weak → ensemble 反而 underperform lambdamart-only**:
- ann CAGR -71.86pp (从 +69% 跌到 -2.7%)
- max_dd -9.53pp 恶化 (从 -21% 到 -30%)
- sharpe 几乎归零

## 根因

ensemble 设计假设 3 source 都是 strong alpha (lambdamart RankIC 0.01 / sniper confluence 多 rule / institution composite). 当 institution 只是 raw count (lhb_inst_buy_30d 1-2 之间常见), 在 regime neutral 30% weight 下严重 dilute lambdamart 选股:
- lambdamart top-5 是 score rank 0-100%
- institution top-5 是 lhb buy 数最多, 跟未来 ret 弱相关

ensemble 30% weight institution → 强制选 lhb 信号 stocks → 错过 lambdamart strong score stocks

## 决策

**不开 --with-institution default**. lambdamart-only 仍是当前最佳 ensemble (Phase 3.4 真接需 Codex sniper builder a432eadffa 完成完整 4-class composite, 不是 raw count).

Phase 5 retrain 时 Codex 设计:
- institution score 应该是 4-class composite (LHB + CapitalFlow + Survey + Northbound) normalize sum
- 用真 score (e.g. inst_money_flow / total_volume 比例), 不是 raw count
- ensemble regime weight 应 Optuna 调优 (当前 lambdamart 30% / sniper 40% / institution 30% 是 doc default 拍脑袋)

## 引用

- 实测 ensemble 22 monthly obs (commit 1bae489a 后 ensemble runner + institution wire)
- baseline c13086cc (lambdamart-only +34.88% median)
- Codex sniper builder agent a432eadffa background 跑中
