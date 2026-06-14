# 模型验证「可靠性检测仪」+ 因子来源 设计思路 (2026-06-14, 草案/未实现)

> 用户: "把模型规则策略的思路先想好" (像数据底座那样先立法再实现, 不着急验证)。
> 缘起: 用户提蒙特卡洛回测可靠性检测 + qlib Alpha158 因子库。
> owner=本文件 (设计真相源); 实现前须 grill + 对抗复审方法论。上位=alpha_validation_program_spec / l0_bare_kline_baseline_spec。

## 0. 立法层: 什么是「可靠的」回测 (death-clause)
> **一个无法通过随机性检验的策略, 不投一分钱** (用户引文)。漂亮的年化/夏普可能是"历史掷骰子的巧合"。
死亡条款 (本阶段): 任一未过随机性/过拟合检验阶梯的因子/策略 **禁入实盘候选** —— 不是因为历史表现差,
而是无法区分"真本事 vs 真运气"。验证 = 一道**阶梯**(多个独立 null 逐层证伪), 非单一 p 值。

## 1. 可靠性检测军火库 (每个测**不同的 null 假设**, 互补非冗余)
| 检测 | null 假设 (H0) | 抓什么 | 适用层 | 状态 |
|---|---|---|---|---|
| walk-forward OOS | (实测 OOS 表现本身) | 时间外泛化 | Tier-1/2 | [OK] 有 (`oos_ic`) |
| **DSR** (Bailey-LdP) | N 次试验中"最好的"在无技能下的期望 | 多重比较/选参 selection bias | 寻参 | [OK] 已重建 (`deflated_sharpe`) |
| **PBO** (Lopez de Prado CSCV) | IS-best 在 OOS 仍 best 的秩一致性 | 过拟合**概率** Pr(λ<0) | Tier-2 | [无] 删, **恢复** (`backtest_validation/pbo.py`) |
| **MC 截面置换** (feature↔label shuffle) | feature 与 forward-return 无截面关系 | 因子 IC 显著性 (横截面技能真假) | Tier-1 | [无] **新建** |
| **MC 块自助** (block bootstrap 重生信号) | 策略在"另一种历史"下的表现 | 时序择时技能稳健性 + 收益分布 | Tier-2 | [无] **新建** |
| **MC 回撤压力** (有放回抽样净值) | 同机制下的尾部回撤 | max_dd 可能远超历史 (心脏测试) | Tier-2 | [无] **新建** |
| **MC 成本不确定** (滑点分布抽样) | 执行成本波动下的稳健性 | 成本敏感 (稍大滑点就亏=脆弱) | Tier-2 | [无] 新建 (接 Tier-2 成本模型) |
| **Bayesian 收缩** (prior→posterior) | 小样本 IC 向先验收缩 | 小样本 IC 高估去偏 | Tier-1 | [无] 删, 可选 (`sef/bayesian_updater`) |

## 2. [!] 纠正文章的技术错误 (measured-not-estimated)
文章"记录交易收益率→随机打乱→重算夏普"**对夏普是无效的**:
- 夏普 = mean/std, **置换不变** (打乱不改均值/标准差) → 重算夏普恒等于原值, 该检验是 no-op。
- 复利总收益 = ∏(1+r) 也**置换不变** (乘法交换律)。
- **只有路径依赖量 (max_dd / 最长回撤期) 置换才变** → 置换检验**只对回撤类有效**。
**正确的 MC 显著性检验** (本项目采用):
1. **截面置换** (因子层): 固定日内, 打乱"哪只股的 feature 配哪只股的 forward-return" → 重算 RankIC →
   真实 RankIC 须 > shuffle null 95 分位 (mythos §9 同组 shuffle baseline 同源)。**这才测因子真有横截面技能**。
2. **信号重生** (策略层): block bootstrap 价格路径 → **在新路径上重跑策略生成信号** → 算 Sharpe 分布
   (文章 §5.1 合成路径的正确版; 关键是"重跑策略"非"打乱已实现收益")。
3. **块置换保自相关** (文章 §4 对): 5日块打乱破长程依赖留短期结构 → 但仍走信号重生不走收益打乱。

## 3. 因子来源 (consumer_alpha 矩阵的"列"; 与可靠性阶梯正交)
| 来源 | 内容 | 数据依赖 | 状态 |
|---|---|---|---|
| L0 hand-built | reversal/macd/ma/turtle (4) | 纯 OHLCV | [OK] 标尺 reversal +0.064 |
| **qlib Alpha158** | KBAR/rolling 技术因子 (`fact_alpha158_panel` 418万行×67列) | **纯 OHLCV** (+少量量) | [OK] **已建** (3.8G, 但 PIT 待重核) |
| qlib Alpha360 | 60日归一价量原始序列 | 纯 OHLCV | [无] 可选扩 |
| tushare 新数据 | flow/chip/fundamental (menu P0/P1) | 资金流/筹码/财务 | [无] 验证后抓 |

**关键洞察 — Alpha158 是"K线还有没有油水"的判决**: Alpha158 因子**几乎全 OHLCV 派生** (与 L0 同源, 只是
工程更细)。所以它不是"新数据 alpha", 是"**更精细的 K线因子工程能否超越简单 reversal +0.064**"的判决:
- Alpha158 最佳因子 (过可靠性阶梯后) **显著超 +0.064** → K线仍有油水, 这是新技术标尺。
- **不超** → 裸 K线已榨干, 增量**只能来自新数据** (flow/chip/fundamental) → 验证重心转 tushare menu。
先跑 Alpha158 判这个, 再决定是否大投入抓新数据 (架构 rule6: 不为未证明的数据建管道)。

## 4. 整合: 因子来源 × 可靠性阶梯 = 验证流水线
> **因子越多, 过拟合风险越大, 可靠性闸越关键。** Alpha158 的 67 因子 × Optuna 无 MC/PBO/DSR = 数据窥探灾难。
> 两个话题 (蒙卡 + Alpha158) 本质连体: 因子库**需要**军火库护着。

**验证阶梯 (因子/策略须逐层过, 任一不过=不进实盘候选)**:
```
Gate 0  PIT-clean (无泄漏)                         [前置, 已有 3 门固化]
Gate 1  walk-forward OOS RankIC > L0 标尺 +0.064    [Tier-1, oos_ic 已有]
Gate 2  MC 截面置换: RankIC > shuffle null 95%      [Tier-1, 新建 — 测横截面技能真假]
Gate 3  DSR: 选参 best 显著 (多重比较去偏)           [寻参, 已重建]
Gate 4  PBO < 阈 (理想<0.1): IS-best 仍 OOS-best     [Tier-2, 恢复]
Gate 5  MC 块自助 Sharpe>95% + 回撤压力 + 成本MC      [Tier-2, 新建]
        → 全过才进实盘候选
```
MASTER_SYNTHESIS (reset 前) 实证 PBO 有效: v7 PBO 0.094 PASS / ensemble 0.827 FAIL → 当时正是 PBO 挡掉过拟合。

## 5. reuse vs rebuild (reset 后)
- [OK] **复用**: alpha158 panel (已建, 但 PIT 须重核 — 它 06-11 建于 reset 前, 不可信任直接用); `oos_ic` (Tier-1);
  `deflated_sharpe` (DSR 已重建); optuna_config 治理。
- [恢复] **恢复** (git 639e0dfb~1): `pbo.py` (CSCV, 数学忠实恢复同 DSR 做法); 可选 `bayesian_updater`。
- [新建] **新建**: MC 截面置换 (Tier-1, 接 oos_ic); MC 块自助+回撤+成本 (Tier-2, 接 backtest 引擎); qlib Alpha158
  消费器 (读 fact_alpha158_panel 当 consumer_alpha 矩阵的列)。
- 全 **config 驱动** (MC n_iter/block_size/分位阈、PBO sub_periods、各 gate 阈值走 yaml 不 hardcode);
  **moth 固化** (gate 真触发非死闸, 同 embargo 死闸教训); 阶梯顺序 + 阈值进 `optuna_config.yaml` 或新 `reliability_gates.yaml`。

## 6. 阶段建议 (实现前 grill, 本文件仅思路)
1. **先 Alpha158 判油水**: PIT 重核 panel → 接 consumer_alpha 矩阵 → 过 Gate1+2 (RankIC+截面置换) → 看最佳因子能否超 +0.064。判"K线还有没有油水"。
2. **同步恢复 PBO + 新建 MC 截面置换**: Tier-1 阶梯补全 (Gate2/Gate3 已有 DSR)。
3. **Tier-2 引擎建好后**: 补 Gate4 (PBO) + Gate5 (MC 自助/回撤/成本) — 策略级随机性检验。
4. Bayesian 收缩: 小样本 IC 场景才上, 非必需 (优先级低)。

## 7. 已知坑 / 红线
- 文章的收益打乱测夏普 = 无效 (§2); 必用截面置换/信号重生。
- MC/PBO 是**过滤器非圣杯** (文章 §6 + genesis): 过了仍可能因未来函数/幸存者偏差失效 → PIT 阶梯不可省。
- alpha158 panel 建于 reset 前, **PIT 不可信任直接用** → 须重核 (factor[t] 只用 ≤t; 复权口径)。
- 因子多 (158) → DSR/PBO 的 n_trials 必须如实计 (含所有试过的因子), 否则去偏失效 (同 §4.2 selection bias)。
- 块自助 block_size 是超参 (太小破自相关/太大样本少), 走 config + 敏感性, 不拍脑袋。
