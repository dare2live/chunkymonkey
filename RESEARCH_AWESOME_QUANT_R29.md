# Codex Round 29 — awesome-quant 仓库工具评估

Source: agent a660f33e1a4f83340, 2026-05-17. 排除上轮 Round 27 已评估的工具.

## 5 个推荐 (按 ROI 排序)

| # | 工具 | license | task 映射 | 数据 | 工作量 | ROI | 风险 | verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | **backtester-mcp** | Apache-2.0 | 新 task: PBO/DSR/robustness gate | OHLC/信号/NAV 可从 DuckDB 导出 | 16-32h | read-only no alpha, 但能阻过拟合策略晋级 | 项目新 (2026-04), A 股 T+1/涨跌停需适配 | AAA-PASS |
| 2 | **skfolio** | BSD-3 | #76 (替代 Riskfolio?) | 日收益矩阵 + 行业/风格约束 + 成本 | 24-40h | max_dd 改 3-7pp / ann +0-4pp | 5000 股协方差不可全量, 需 top-K + walk-forward | AAA-PASS |
| 3 | tsfresh | MIT | #77 PIT-safe 时序特征挖掘 | OHLCV + capital_flow + valuation + LHB | >40h | RankIC +0.002-0.006 / ann +3-8pp (不确定) | PIT 泄漏 + 多重检验 + 特征爆炸 | B-WATCH |
| 4 | Hikyuu | Apache-2.0 | 新 task: A 股 oracle 回测对照 | tdxhub K-line + 交易日历 + 费用 | >40h | read-only, max_dd 估计误差 1-4pp | C++/Python 重栈, 只能 oracle | B-WATCH |
| 5 | PyPortfolioOpt | MIT | #76 轻量 baseline | price/return DataFrame | <16h | max_dd 改 1-3pp / ann +0-2pp | 跟 Riskfolio/skfolio 重叠 | B-WATCH |

## 1. backtester-mcp (AAA-PASS, 重磅推荐)

**License**: Apache-2.0. GitHub release v0.1.0 2026-04-12, PyPI 2026-04-14.

**功能**: 本地优先的回测验证层. 核心:
- PBO (Probability of Backtest Overfit) — Lopez de Prado
- DSR (Deflated Sharpe Ratio)
- Bootstrap CI
- walk-forward
- 保守成交情景 (conservative scenarios)

**对应 task**: 新 task `backtest_robustness_gate` — 不替换 paper_sim, 只作独立验证门禁.

**Gate 条件建议**:
- DSR p >= 0.95
- PBO <= 0.20
- conservative scenario ann > 0

**对应 ChunkyMonkey 痛点**: 当前最大未满足是"防过拟合"而不是"再加 alpha". 16×50 Optuna + 频繁 ablation 极易出漂亮但脆弱结果. 这个 gate 直接阻止假 alpha 上线.

**实施**: 导出 paper_sim NAV/trades 给 backtester-mcp, 跑 PBO + DSR + bootstrap, gate 阻断不通过的策略.

## 2. skfolio (AAA-PASS)

**License**: BSD-3, commit 活跃 2026-05-11.

**功能**: sklearn 风 portfolio optimizer + risk mgmt
- HRP / CVaR / CDaR
- turnover / cardinality / group constraints
- walk-forward + Combinatorial Purged CV

**对应 task**: #76 — 是 Riskfolio-Lib 的更现代替代.

**ROI**: max_dd 现 ~-30% → -23%~-27%, ann +0-4pp.

**关键约束**: 5000 股全量协方差不行, 必须 top-100/top-300 候选上 rolling 优化. `fit_end <= signal_date` 严格守门防 optimizer leakage.

**vs Riskfolio**: skfolio 更现代 (sklearn API + Combinatorial Purged CV 内置), 但 Riskfolio 已规划 task #76. 二选一或并存测.

## 3. tsfresh (B-WATCH)

**License**: MIT, commit 最新 2025-11-15.

**功能**: 自动抽取时序特征 (统计/频域/非线性) + hypothesis-test 筛选.

**对应 task**: #77 扩展 — 在 K-line/capital_flow/LHB 长表上跑.

**ROI 估计**: RankIC +0.002-0.006, ann +3-8pp (不确定性高).

**主要风险**: PIT + 多重检验. 必须 `signal_date` 前窗口 + purged CV + holdout + feature family ablation. 特征爆炸 (tsfresh 默认 1000+ feature) 易过拟合.

**Verdict B**: 进 watch list, 不优先实施. 等 Wave 1 结果 + skfolio 接入后再考虑.

## 4. Hikyuu (B-WATCH)

**License**: Apache-2.0, commit 2026-05-11. README 明确 "深度适配 A 股市场数据体系".

**功能**: C++/Python A 股专门量化框架. 含策略组件 + 资金管理 + 滑点 + 组合分析.

**对应 task**: 新 task `a_share_backtest_oracle` — 独立 oracle 对照 paper_sim T+1 / 费用 / 滑点 / 涨跌停.

**ROI**: read-only, 不产 alpha. max_dd 估计误差收敛 1-4pp.

**风险**: 工程栈重 (C++/Python). 内置数据是 latest snapshot → PIT 不能直接用, 只能作 oracle. 写数据适配层 40h+.

**Verdict B**: 不优先. 如果 paper_sim 实测 vs Hikyuu 误差 < 5%, 验证我们 paper_sim 可信; 大则用 Hikyuu 当 ground truth.

## 5. PyPortfolioOpt (B-WATCH)

**License**: MIT, commit 2026-03-10.

**功能**: 轻量 portfolio optimizer (Efficient Frontier / Black-Litterman / shrinkage cov / HRP / discrete allocator).

**对应 task**: #76 轻量 baseline, 不作主优化.

**ROI**: max_dd +1-3pp / ann +0-2pp.

**vs skfolio**: skfolio walk-forward/CPCV 框架更全; PyPortfolioOpt 是基础. 二选一选 skfolio.

**Verdict B**: 跟 Riskfolio + skfolio 重叠, 不再加.

## 总结决策

| 立刻加 task | 等结果再决 |
|---|---|
| backtester-mcp gate (AAA) — 防过拟合, 工程小 ROI 高 | tsfresh (Wave 1 完了再说) |
| skfolio (AAA) — 评估是否替代 / 并存 Riskfolio | Hikyuu (paper_sim 误差测了再说) |
| | PyPortfolioOpt (重叠) |

**重磅推荐 (Codex 原话)**: backtester-mcp 最值得先做. ChunkyMonkey 最大未满足痛点不是再加 alpha, 而是在 Optuna + ablation + 严格 PIT 之外, 加独立 PBO/DSR/保守成交验证闸门, 防止漂亮但脆弱的回测推成真钱候选.

跟 [[feedback-leakage-red-flag]] 相符: RankIC > 0.3 / 异常高数字 = leakage 警报. backtester-mcp 是这个警报的 systematic 落地.
