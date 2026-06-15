## Phase B 实验2: (公式 x 形态) IC 矩阵 + MACD 零轴 — 方向性 V0 (2026-06-15)

> 状态: live。实验=`backend/scripts/experiment_formula_stage_matrix.py`; 结果=`formula_stage_matrix_20260615.json`。
> 用户 direction (2026-06-15): macd 金叉零轴上下不同 + (公式x形态)矩阵 + 低位多种细分 → 本地 V0 验方向再上 Optuna+Modal。

### (公式 x 形态) OOS RankIC (horizon=5, embargo=5, 全宇宙, 513 OOS 日)
| 公式 | ALL | 底部 | 突破中 | 上升 | 顶部 | 下跌 |
|---|---|---|---|---|---|---|
| macd_golden_cross | -0.049 | -0.001 | **-0.116** | -0.036 | -0.028 | -0.037 |
| ma_base_breakout | -0.073 | -0.012 | **-0.117** | -0.078 | -0.079 | -0.025 |
| turtle_breakout | -0.037 | +0.005 | -0.024 | -0.038 | -0.018 | -0.015 |
| reversal_short_term | +0.064 | +0.004 | **+0.156** | +0.066 | +0.053 | +0.031 |

ALL 级 4 公式全复现 L0 标尺 (-0.049/-0.073/-0.037/+0.064) = 管线无 drift。

### 核心发现
1. **"突破中"(Stage1.5)是关键 regime, 方向相反**: reversal +0.156 (超卖反弹强正) vs macd/ma -0.116/-0.117
   (动量/趋势强负)。解读: 刚突破 MA30 的股, 近期**跌**的继续涨、近期**涨**的反转跌 = 回踩突破 vs 冲高乏力。
   强条件化结构, 经济自洽。**这是目前最强的 (公式x形态) edge cell。**
2. **底部(Stage1)所有公式 ≈ 0** (-0.012 ~ +0.005): 用户"低位有效"在全 4 公式被数据否。低位需更细分割
   (低位横盘 vs 冲高回落后低位横盘, 见下 Optuna 计划) 才可能有 edge。
3. **MACD 零轴上下不同 (用户点, 成立)**: DIF+ (零轴上, ema12>ema26) IC -0.059 vs DIF- (零轴下) -0.026,
   零轴上方显著更负。DIF±x形态 cross 有更细结构 (DIF+x1.5=-0.109)。

### 诚实边界 (多重比较)
20+ cell 地图 = 方向线索非结论。flags: 仅 reversal|1.5 (+0.156) 触 relative 红线 (pending ablation)。
**任何高 cell 转正前须**: (a) DSR-deflate (n_cells 多重比较校正, Bailey-LdP); (b) ablation (MC截面置换/子周期/
机械重叠); (c) pre-reg 冻结 cell 再独立窗验证。单看高 cell = selection bias (§4.2)。

### 下一步: Optuna + Modal (V0 方向已成立, 投算力 justified)
- **更细 Segment 搜索** (用户 direction): 低位细分 (低位横盘 / 冲高回落后相对低位横盘 / 低位放量 ...) +
  MACD 零轴 x 形态 + 历史分位 (expanding PIT) — Optuna 搜 segment 定义参数 (range_lookback/flatness/
  pullback_depth/zero_axis), DSR 治理多重比较, OOS-only, pre-reg 冻结搜索空间。
- **Modal 算力**: segment x 公式 x 全史 的 IC 搜索是 embarrassingly parallel — reviewed adapter + artifact
  manifest 契约后上 Modal 并行 (跑前 grill: 搜索空间非空? 输出可决策? 成本 vs 产出?)。
- 转正路径: Optuna 选出的 top cell → 独立 holdout 窗 + DSR/PBO → 进策略立方体作正 edge cell。
