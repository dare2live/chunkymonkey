# 条件化(形态×公式×因子)策略设计思路 (2026-06-14, 草案/未实现)

> **状态 (2026-06-15 P0-P3 后部分 superseded)**: 核心条件化洞察仍对; 操作化三处偏离 — ① 立方体 3 轴→**5 轴**(+Regime/Timing+Execution); ② 选 cell/解锁改按**含成本 execution-aware 绝对收益**, IC 降级 necessary 快筛(§3/4 ORDER BY IC stale); ③ 裸 K 线 reversal base 经 P3 实弹判**结构性不可交易**, 转慢衰减绝对源。owner 冲突优先: 判断法典=`docs/strategy_validation_contract.md` · 缺陷体系/根因 R1/R2=`analysis/design_deficiencies_extension2_20260615.md` · P3 实弹裁决+Phase D=`analysis/p3_execution_aware_verdict_20260615.md`。本文骨架保留作演进记录。

> 用户核心洞察: 不找"通用全部股票全部阶段的万能公式", 而是 先给股票形态分类 → 看哪个公式适用哪个
> 形态 → 再叠因子(换手/筹码/资金流)增 alpha → 多维多因子寻优 + 数据驱动探索模式 + 用现有排除列表。
> owner=本文件(具体思路); 立法上位=`analysis/multidim_strategy_architecture_20260613.md`(策略立方体);
> 实现前须 grill + 对抗复审。本文件仅思路。

## 0. 为何这是对的 — L0 已实证条件化假设
L0 市场级 reversal 仅 OOS RankIC **+0.064**, 动量/突破**负**相关。**这不是 K线无 alpha, 是把所有形态平均了**:
低位/横盘里 reversal(超卖反弹)有效、上升通道里动量/突破有效, 市场级一平均互相抵消 → 稀释成 +0.064。
**正解 = 条件化**: 先分形态, 再在形态内用形态适配的公式 → 形态内 edge 远高于市场级平均。这正是立方体
"segment 是全市场一组(太粗)与每股一组(过拟合)之间**可治理中间粒度**"的论点。

## 1. 这就是策略立方体 (已立法, 你的想法=其 Segment 维)
cell = **(Segment 形态 × Feature-set 因子族 × Policy 公式/模型)**。三维映射你的描述:

| 你的话 | 立方体维 | 真相源 (复用, 不新建) |
|---|---|---|
| 横盘/低位/上升通道/下跌通道/高位 | **Segment 形态** | `technical_stage.py`(Stan Weinstein 5 阶段, 幸存)+ config |
| MACD 零轴上/下、当前阶段历史区间分位 | Segment 细分轴 | 新增 stage 描述子(PIT 分位 + MACD 符号), 进 technical_stage.yaml |
| 哪个公式适用哪个阶段 | **Policy 公式** | 20 公式 + 主升浪猎手(formula_candidates.yaml) |
| 换手率/筹码/资金流向 增 alpha | **Feature-set 因子族** | Alpha158(旧panel已删, 验证时干净重算)+ tushare menu(资金流/筹码)+ daily_basic 换手 |
| 排除 ST/退市/三板/北交所 | **Universe** | `universe.py`(前缀非60/00/30/68排除=北交所/三板;ST名;退市no-trade)**已覆盖你列的全部** |
| 数据驱动探索其他模式 | Segment 发现 | 无监督 regime 聚类(K线特征)作 Weinstein 之外的第二 segment 源, OOS 闸 |

## 2. 形态分类 (Segment) — PIT + measured, 不拍脑袋
Stan Weinstein 5 阶段(已有)对应:Stage1 底部(低位/横盘)/ 1.5 突破 / 2 上升通道 / 3 顶部(高位)/ 4 下跌通道。
**你的两个补充轴**(与 Weinstein 正交, 可交叉成更细 segment):
- **MACD 零轴位置**: DIF 在零轴上/下(动能方向)—— PIT 干净(EMA 只用过去, 复用 features.py)。
- **历史区间分位**: close 在自身历史的分位(低位/中位/高位)—— **必须 expanding/as-of 分位(只用 ≤t)**,
  禁全历史分位(=lookahead 泄漏)。Stage1 的"60周低位±15%"已是此概念, 推广到连续分位。

**红线(立方体 C1 + §4.5 regime 拍脑袋反例)**: 阶段边界(什么斜率算上升通道、什么分位算低位、量比阈)
**不许 hardcode 拍脑袋**, 走 technical_stage.yaml + **历史 regime sensitivity sweep 标定**(measured)。
阶段标签 PIT: stage[t] 只用 bars[:t](Weinstein 用周线 MA/分位都是过去窗)→ 复用 pit_guard 行为门核证。

## 3. 第一个实验 (最便宜最高价值 — 直接验条件化, 解锁形态维)
**per-stage L0 IC**: 复用 `oos_ic.py` + `technical_stage`, 把 L0 的 reversal/macd/turtle/ma 在**每个 Weinstein
阶段子集内**分别算 OOS RankIC。预期:
- reversal 在 Stage1(低位/横盘) IC 远 > 市场级 +0.064;在 Stage2(上升) 可能负 → **证明条件化**。
- 各公式现出"哪个阶段最适配"的模式 → 直接产出 (公式×阶段) 适配矩阵。
零新数据、零新引擎(oos_ic + technical_stage 都在), 几分钟。**这是立方体形态维的解锁证据**(死亡条款#2:
该维分组 OOS 严格优于全市场基线才解锁)。不优于 +0.064 则形态维不解锁, 老实回退。

## 4. 三维寻优 + 过拟合治理 (立方体 C5 + 可靠性阶梯)
> **唯一真风险 = 维度爆炸 × 过拟合 × 泄漏面**(立方体 §0)。形态(5)×公式(20)×因子族(N) = cell 爆炸。
治理(全复用):
- **实例化克制**: 先解锁形态单维(实验3证), 证优于基线再加因子维, 不一次全开(立方体脊柱)。
- **DSR/PBO/MC** (可靠性阶梯, 见 model_validation_reliability_design): n_trials **必须如实计全部 cell×公式×因子组合**,
  否则多重比较去偏失效。每 cell 过 Gate0-5(PIT→OOS>基线→MC截面置换→DSR→PBO)。
- **主辅契约 C3**: 每 cell 至多 1 个出仓源(形态选中的主公式), 因子族(换手/筹码/资金流)只调制 size/gate 不独立持仓。
- **只看 OOS C4** / **泄漏闸**: feature_set 过 leakage_consumers gate。

## 5. 数据驱动探索 (你的"探索其他模式")
Weinstein 是**先验命名**阶段; 大数据下还可**无监督发现**阶段: 对 PIT K线特征(波动/趋势/量能/分位)做
聚类(如 HMM regime / KMeans on rolling features)→ 数据自己划分 regime → 作 Weinstein 之外的第二 segment 源。
但**先验阶段先行**(可解释 + 便宜), 无监督作补充, 且同样过 OOS 闸(无监督 regime 若 OOS 不优于 Weinstein 则不用)。

## 6. reuse vs 新建
- 复用: `technical_stage.py`(阶段)/`universe.py`(排除, 覆盖全)/`oos_ic.py`(per-stage IC)/`features.py`/
  Alpha158 panel(因子, PIT 重核后)/ 立方体立法 / DSR / 可靠性设计。
- 新建: technical_stage 加 MACD轴+历史分位轴; `strategy_cube.yaml`(三轴+解锁态+cell注册, 立方体 §实现已设计);
  `services/strategy_cube/`(cell 编排层, 调中央 optimization, 不裸调 study); MC截面置换 + PBO 恢复。
- 全 config 驱动 + moth 固化(cell 数上限/解锁态/gate 真触发)。

## 7. 阶段计划 (实现前 grill)
1. **per-stage L0 IC**(实验3)— 验条件化, 出 (公式×阶段) 适配矩阵。**先做这个**, 极便宜, 定方向。
2. 形态维解锁(若实验3证优): technical_stage 加 MACD/分位轴 + sweep 标定边界。
3. 叠因子维: 在最优 (阶段×公式) cell 内, 加 Alpha158/资金流/筹码 因子, multi-factor 寻优(受 DSR/PBO)。
4. 数据驱动 regime 发现(补充)。
5. 形成 mart_strategy_cube_optimal(cell × oos_*)→ champion → Tier-2 backtest 终验 → 实盘候选。

## 8. 红线 / 坑
- 形态分位/MACD 必 PIT(expanding 分位, 禁全历史)→ pit_guard 核证。
- 阶段边界 measured(sweep)不拍脑袋(§4.5 regime 反例)。
- cell 爆炸 → n_trials 如实计 + 逐维解锁(不一次全开)。
- per-stock 极端粒度(mart_per_stock_stage)已证易过拟合 → segment 用阶段级中间粒度, 不退化成每股一组。
- Alpha158 panel PIT 重核后才作因子(见 reliability design §7)。
