## P3 实弹重裁决 — execution-aware 引擎 vs 旧 return-based 引擎 (2026-06-15)

> 状态: live。owner=本文件。上承 design_deficiencies_extension2 (根因 R1/R2) + P1 引擎重建。
> 目的: 用 P1 的 execution-aware 真引擎重跑 Phase B 两个 cell, 量化 R2 摩擦把"含成本裁决"修正多少, 定 Phase D 方向。
> 法典工具全链生效: 跑前 leakage_gate / 裁决 tradability_verdict (R1) + kpi_verdict (C-WinReturn)。

### 1. 新旧引擎对比 (Stage1.5 突破中 reversal, top20, T+1, 周度, 2023+)

| cell | 引擎 | 年化 | max_dd | 月胜率 | 段胜率 | 盈亏比 | 期望 | 末NAV | R1 裁决 |
|---|---|---|---|---|---|---|---|---|---|
| 全市场 Stage1.5 | 旧 return-based | net -2.8% / gross +7.1% | -44% | 45% | — | — | — | — | (旧引擎无此门) |
| 全市场 Stage1.5 | **execution-aware** | **-14.06%** | **-57.3%** | 42.5% | 46.9% | 0.93 | -0.095 | 0.608 | **IC_POSITIVE_BUT_UNTRADABLE** |
| 小盘×高换手 (IC最高 +0.195) | 旧 (gross) | gross -34.6% | — | — | — | — | — | — | — |
| 小盘×高换手 | **execution-aware** | **-34.69%** | **-80.6%** | 32.5% | 43.8% | 0.74 | -0.24 | 0.251 | **IC_POSITIVE_BUT_UNTRADABLE** |

> 两 cell 均: IC +0.156 (Stage1.5 cohort, Gate2 33σ STAT_EDGE_CONFIRMED) **> 0**, 但含成本年化 **< 0** → `tradability_verdict` = IC_POSITIVE_BUT_UNTRADABLE; `kpi_verdict` = KPI_FAIL (盈亏比<1 + 期望<0 + 段胜率<50%, C-WinReturn 三诊断量同向印证)。

### 2. 为什么 execution-aware 比旧引擎更惨 (R2 摩擦实测幅度)

全市场 net -2.8% → **-14.06%** (恶化 ~11pp); 这 11pp 就是旧引擎"无摩擦假设"隐藏的亏损:

| R2 摩擦 (缺陷) | 机制 | 实测信号 |
|---|---|---|
| **T+1 open 入场 (N14)** | reversal 信号的"赢家"隔夜跳空高开, 在更高 open 才买到 (旧引擎按决策日 close 假成交, 偷了隔夜收益) | 旧 gross +7.1% → 新 -14% 主因 |
| **涨停一字板剔篮 (N8/N12)** | 真正反弹最猛的超跌票 T+1 一字涨停 **买不进**, 系统性只留没反弹的 → IC 把买不到的赢家也算进排序 | reversal 的 IC 大半来自买不到的票 |
| **非对称成本+容量 (N13/N10)** | 卖方印花 + 小盘大单溢价 (小盘格 max 参与度 1.63, 超阈率 12.1%) | cost_drag 全市场 34.5% |
| **诚实路径 (N11)** | 停牌冻结/无欠仓掩盖 → max_dd 暴露真实 -57%/-80% | 旧 -44% → 新 -57%/-80% |

### 3. 裁决: 裸 K 线 reversal long-only 在 A 股**结构性不可交易** (真金白银定论)

- 同一个"33σ REAL_EDGE / IC 最高"的 cohort, 用诚实引擎交易: 年化 -14% (全市场) 到 -35% (IC 最高格), max_dd -57% 到 -81%。
- **IC 越高的子格越惨** (小盘×高换手 IC +0.195 → -34.69%): 印证 R1 —— IC 测的是 cohort 内相对排序, 高 IC cohort 恰是崩盘投机股 (微盘 2024), 排序技能在崩盘里一文不值; 且小盘容量约束 + 涨停买不进把它推到 -81% max_dd。
- **R1+R2 双重定论**: R1 让 IC 看不见这是崩盘 (33σ 显著); R2 让旧引擎的 -2.8% 偏乐观 11pp。两者叠加 = 一个"统计完美"的策略实盘亏 14-35%。

### 4. Phase D 方向 (已被真金白银证据锁定)

裸 K 线 reversal (短衰减相对排序) **退役为 base 候选**; Phase D 转**慢衰减 + 绝对预测**源:

| 优先级 | 源 (已在库) | 为什么 (R1/R2 兼容) |
|---|---|---|
| P0 (激活已在库) | industry_beta / mcap decile / sector momentum | 已实测 regime/pool-gate REAL (DSR PASS); 作**第四轴 regime-gate 候选**, 非 selector base; 重用前修 Pattern-10 NULL-gradient leakage |
| P1 (慢衰减绝对) | 财务质量 / 资金流 trend / 景气 / 筹码结构 (daily_basic 762万 / moneyflow 538万 / cyq_perf 457万行已在库) | 绝对方向 + 慢衰减 → 低换手 → 成本可 survive (R2); 驱动 cohort 整体涨 = long-only 真 alpha (R1) |
| 验收 | 一律 tradability_verdict + kpi_verdict (含成本 execution-aware backtest 绝对收益), **不按 IC** | 法典 C-R1/C-WinReturn 已固化 |

**不在裸 K 线短信号上再投 Optuna/Modal 精调** —— 那只会在 rank 空间多生产 R1 盲点产物 (design_deficiencies_extension2 §4)。

### 附: 留档

- 全市场: `analysis/tier2_conditional_backtest_20260615.json` (canonical) + experiment_store run_id=phaseb_tier2_fullmarket_equal_20260615。
- 小盘子格: `analysis/tier2_conditional_backtest_caplow_tohigh_equal_20260615.json` + run_id=phaseb_tier2_caplow_tohigh_equal_20260615。
- 引擎=portfolio_execbacktest (T+1 open/涨跌停/非对称成本/停牌冻结/容量/仓位 policy); 微结构真相源=backtest_execution.yaml。
