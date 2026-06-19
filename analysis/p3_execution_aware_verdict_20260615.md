## P3 execution-aware 引擎裁决纪律 (2026-06-15; 2026-06-17 清污染期数字)

> 状态: live (机制)。owner=本文件; R1/R2 根因权威 = `design_deficiencies_extension2_20260615.md`。
> 2026-06-17 清验证墓地: 本文件原含裸 K 线 reversal long-only 的具体含成本回测数字 (年化/max_dd/IC) —
> 建于污染 universe + 污染期买点信号正推, 已清除 (含已删 tier2_*.json + 已 wipe experiment_store run_id)。
> 保留的是 execution-aware 引擎的 R1/R2 裁决机制 (与具体策略无关的纪律)。任何具体策略裁决须在
> 结构型主升浪 GT + universe 硬门下用真引擎重跑, 数字 = unknown 待重测。

### R1 — IC 高 ≠ 能赚钱 (验证空间 ⟂ 盈利空间)

每日截面 RankIC 测的是 cohort 内相对排序, 数学上减掉了 cohort 绝对漂移; long-only 赚的恰是被减掉的
绝对水平。后果: 一个 IC 高 / 统计显著 (高 σ) 的 cohort 可能恰是崩盘 cohort, 排序技能在下跌里一文不值。
**裁决必看含成本绝对收益 (`tradability_verdict`), 不看 IC** — `IC_POSITIVE_BUT_UNTRADABLE` 是单边盲点的对称门。

### R2 — 信号 ≠ 可交易头寸 (execution-aware 四类摩擦)

旧 return-based 引擎假设"信号即头寸 / close 全额成交"= 无摩擦市场, 系统性偏乐观。真引擎须含:

| R2 摩擦 | 机制 |
|---|---|
| T+1 open 入场 (N14) | 决策日 close 假成交偷了隔夜跳空收益 |
| 涨停一字板剔篮 (N8/N12) | 反弹最猛的票 T+1 一字板买不进, IC 把买不到的赢家也算进排序 |
| 非对称成本 + 容量 (N13/N10) | 卖方印花 + 小盘大单溢价 (不编造冲击系数, measured) |
| 诚实路径 (N11) | 停牌冻结 / 无欠仓掩盖 → max_dd 暴露真实回撤 |

### Phase D 方向 (R1/R2 兼容)

裸 K 线短衰减相对排序信号 (reversal 类) 不在裸短信号上再投 Optuna/Modal 精调 (只会在 rank 空间多生产
R1 盲点产物, 见 design_deficiencies_extension2 §4)。优先 **慢衰减 + 绝对预测** 源 (财务质量 / 资金流
trend / 景气 / 筹码结构, 已在库): 绝对方向 + 慢衰减 → 低换手 → 成本可 survive (R2); 驱动 cohort 整体涨
= long-only 真 alpha (R1)。验收一律 `tradability_verdict` + `kpi_verdict` (含成本 execution-aware
backtest 绝对收益), 不按 IC。

引擎 = `portfolio_execbacktest` (T+1 open / 涨跌停 / 非对称成本 / 停牌冻结 / 容量 / 仓位 policy); 微结构
真相源 = `backtest_execution.yaml`。
