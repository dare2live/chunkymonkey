# D-step-1: 主升浪因子判别 (结果倒推, 2026-06-20)

> owner: 本文件 (D 因子矩阵 step1 finding)。方法论=goal.md D2 (因子判别力, 结果倒推非信号正推)。
> 数据: fact_rally_entry_pit (正9070) / fact_rally_entry_negative (hard-neg35198) / fact_rally_stage × fact_feature_panel。

## TL;DR
5 个 panel 基础因子里 **reversal_20 + vol_20 对入场点有稳定判别 (AUC 0.6-0.7, OOS-robust)**, mom/mf/roe 近噪音。
**但 AUC 是 C-R1 必要非充分** — reversal 是 Stage1.5 含成本 gross -34.6% 的同族, 必须含成本 event-driven 回测才下结论。
阶段轮廓给出**鱼尾(顶部)signature = 高 mom + 高 vol + 极负 reversal = climax** (出场信号雏形)。

## 入场判别 AUC (正=rally底 vs 负=同结构没涨的底; PIT特征/post-hoc标签)
| 因子 | AUC | 方向 | 解读 |
|---|---|---|---|
| reversal_20 | 0.642 | 正↑ | rally底**更超卖** (跌得狠→反弹大, 超卖均值回归) |
| vol_20 | 0.639 | 正↑ | rally底**波动更高** (高beta movers) |
| mf_trend_20 | 0.526 | — | 近噪音 |
| mom_60 | 0.521 | — | 近噪音 |
| roe_dt_asof | 0.501 | — | 纯噪音 (质量不区分入场) |

## Walk-forward 稳定性 (train<=2022 / OOS>=2023)
| 因子 | TRAIN | OOS | 判定 |
|---|---|---|---|
| reversal_20 | 0.612 | 0.679 | **稳定 (OOS 不衰反增)** |
| vol_20 | 0.592 | 0.699 | **稳定** |
| mom_60 | 0.482 | 0.567 | 噪音 (绕0.5) |
| mf_trend_20 | 0.497 | 0.533 | 噪音 |

cap-strata 内 reversal/vol 中位一致 (微/小/中盘 reversal 0.11-0.13, vol 0.025-0.026) = 非市值混杂代理。

## 三阶段因子轮廓 (鱼头起涨/鱼身主升/鱼尾顶部, 中位)
| stage | mom60 | rev20 | vol20 | mf20 | roe |
|---|---|---|---|---|---|
| 起涨 | -0.014 | -0.006 | 0.020 | -0.017 | 1.75 |
| 主升 | +0.090 | -0.043 | 0.025 | -0.012 | 1.95 |
| 顶部 | **+0.220** | **-0.091** | **0.032** | -0.008 | 2.05 |

mom/vol 一路升到顶部 (加速+放量=climax); reversal 越来越负 (近期涨幅越来越大)。
→ **鱼尾 signature = 高mom + 高vol + 极负reversal**; 出场信号候选。

## 解读 (真金白银, 诚实)
- reversal/vol 判别**真 PIT-clean + OOS 稳定**, 经济意义合理 (超卖反弹 + 高beta)。**事件驱动** (底事件买、持数月大波)
  = 低换手, **可能避开 R1 墙** (cross-section 高换手 reversal 才死)。
- **但 C-R1: AUC 必要非充分**。reversal 是 Stage1.5 (IC+0.195/含成本-34.6%) 同族因子。换手低不代表赚钱,
  涨跌停/滑点/T+1 open 未计。**不含成本回测前不许称 alpha**。
- mom/mf/roe 入场近噪音 → 用户想要的 **量价异常/板块概念热度/筹码** 不在当前 5 因子里; 若基础因子不足, D2 扩因子。

## 下一步 (D-step-2 = 真金白银 gate, owner=待建)
1. **含成本 event-driven 回测**: 规则 = 长底 pivot 事件 + 高 reversal/vol 入场 (T+1 open), 持有到出场,
   portfolio_execbacktest (涨跌停剔篮/T+1 open/非对称成本/容量) → 含成本 NAV vs 基准 (含 random-entry)。
   net<=0 → reversal/vol 是 AUC 幻象 (R1 墙); net>0 且超基准 → 真 edge。
2. **出场信号**: 鱼尾 climax (高mom+vol) 触发 → 回测加 vs 固定持有/移动止损。
3. grill (plan_validator 非空 search space) 后才 Optuna/Modal sweep 调参; 先证简单规则有正期望再花算力 (29/34 反例)。
