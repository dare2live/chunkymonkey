# Codex Round 27 — 量化分析工具评估

Source: agent ab659037531216190, 2026-05-17.

## 推荐 (P0 ~ P1)

| 工具 | License | 集成 | RankIC 预期 | max_dd 预期 | 工作量 |
|---|---|---|---|---|---|
| **AlphaLens-reloaded** | Apache 2.0 | services/factor_eval/ | +0.001-0.005 | 0-2pp | 16-32h |
| **Riskfolio-Lib** | BSD-3 | services/riskfolio/ | 0 | 2-6pp | 24-48h |
| **TA-Lib** (P1) | BSD | services/talib_features/ | +0-0.003 | 0 | 12-24h |
| **empyrical** (P1) | Apache 2.0 | KPI 标准化 | 0 | 0 | 8-16h |

## DROP

| 工具 | DROP 理由 |
|---|---|
| Backtrader | GPLv3+, 维护停滞 2023, A 股 T+1 大量定制 |
| MlFinLab 包本身 | 商业 closed, 但思想可自研 |
| 原版 pyfolio | Quantopian 已停, 用 reloaded fork |
| Featuretools | DFS 容易爆 + cutoff_time 错配 leakage 高 |
| Qlib (full) | 高 overlap (Alpha158 / LGBM / 回测), 双栈成本高, 仅 P2 benchmark |
| VectorBT | Commons Clause 限商业; P2 仅参数 sweep sidecar |

## 集成 plan 见 task #75-#78

新模块:
- `services/factor_eval/` — AlphaLens adapter, FastAPI /api/research/factors/
- `services/riskfolio/` — 组合优化, /api/portfolio/optimize
- `services/talib_features/` — 技术指标生成, /api/features/technical/
