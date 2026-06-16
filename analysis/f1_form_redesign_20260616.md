# F1 形态识别+分层 重设计 (web 调研驱动, 带出处)

> 日期: 2026-06-16 · owner: 主会话 (controller) · 输入: F0 普查 (form_survey_20260616.md) + 4 面 web 调研 (wf_b02fcf99)
> 一句话: F0 实测 Weinstein 5 态 RankIC≈0 不是"形态没用", 是方法缺了 4 环; 业界顶刊证明"位置⊥趋势"该正交。

## 1. 为什么现在分不开 (RankIC≈0 的 4 个根因, 文献诊断)

| 根因 | 机制 | 出处 (证据等级) |
|---|---|---|
| **缺维度** | Weinstein 真正判据 = MA斜率 + 量能 + 相对强弱(RS), 不是裸价位; 我们只用了 range_pos+MACD, 丢了这 3 维 | stageanalysis.net (民间经验) |
| **没条件化就平均掉** | 同一因子在 UP/DOWN 市场态符号相反 (+0.93% vs −0.37%/月), 全样本平均 → 抵消成 0 | Cooper-Gutierrez-Hameed 2004 **J.Finance (强)** |
| **A股趋势动量先天弱/反向** | A股 past-return 动量弱, reversal + residual momentum 强 (T+1+散户主导) → "趋势方向"本身前瞻 IC≈0 | Jansen-Swinkels-Zhou 32-anomaly + T+1 文献 **(强)** |
| **市值/行业 beta 淹没** | 不中性化市值/行业, 形态信号被 beta 吃掉 | Fama-French 依存排序范式 **(强)** |

**反推**: 把这 4 环补上 (加斜率/量能/RS维 + cell内条件化估计 + 市值中性 + 趋势降级为条件桶) 再测, 形态很可能就有信号了。

## 2. 业界顶刊验证了"位置⊥趋势"该正交 (你的直觉对了)

- George & Hwang 2004 **J.Finance**: 52周高点贴近度 (PTH = price/52wk-high) 的预测力**压过**过去收益动量, 且位置预测的收益**长期不反转**、趋势动量长期反转 → 原文判定二者是 "separate phenomena"。**这是"位置讲高低、趋势讲方向, 应正交"的最强单一证据**。我们的 range_pos 就是这个家族, 方向对了。
- state cube 业界在用: Macrosynergy "3方向×2波动=6态", LuxAlgo HMM 4态 → 多轴 regime 立方体非独创。

## 3. F1 重设计 (轴 + 切法 + 突破 + 防过拟合, 全带出处)

### 3.1 轴 (按学术证据强度排, 全部用现有日K+MACD可算)
| 轴 | 实现 | = 用户形态库 | 证据 |
|---|---|---|---|
| **位置** | PTH(52周高点贴近) + range_pos | 高位/低位 | George-Hwang JF (强) |
| **波动率 regime** ★ | 已实现波动率 / ATR percentile → 低波/高波 | (新轴) | 低波异象/BAB 条件化 **(强, 最硬)** |
| **趋势纯度** | Kaufman ER 或 回归R² (趋势 vs 横盘) | 横盘/趋势 | Kaufman/工程标准 (中) |
| **趋势方向** | MA斜率符号 / +DI vs −DI | 上涨/下跌, **但A股降级为条件桶非预测器** | ADX/Wilder (中) |
| **横盘拆两子轴** | 方向态(ADX) ⊥ 波动态(ATR/带宽) | 横盘/底部盘整 | 实务共识 (中) |

补 Weinstein 缺的 3 维: MA斜率分桶 + 突破日量比 + RS(个股收益−大盘)。

### 3.2 切法 (分层)
- **依存排序 (conditional bivariate sort)** 而非独立交叉 — 抗稀疏, 每 cell 股数均衡 (Fama-French 同款)。
- **市值放第一层** (必用, A股市值效应极强, RankIC≈0 头号嫌疑) → **行业** (申万官方, 不用统计簇=不稳+taxonomy坑) → **波动率regime**。
- cell 最小样本门槛, 不足合并相邻桶; 先粗 (市值3×申万一级×波动2) 跑通再细 (3维以上必稀疏)。

### 3.3 突破 = event-in-context (3 层, 修我上次"event-in-context"的模糊)
1. **上下文/底盘**: 整理时长(≥1月) + 波动收缩(VCP式逐次变浅 / ATR下降) + 缩量(换手<5%) — 把"好突破"和"乱突破"分开 (Weinstein 真正补丁)
2. **触发**: 收盘价(非盘中)破上沿(N日高/箱顶/平台/VCP pivot) + 放量确认 (倍数进 Optuna)
3. **可成交闸 (A股铁律, 漏=leakage变体)**: 一字板(Close==涨停价 AND Low==Close)→标不可成交 forward作废; T+1 用 signal.shift(1); 涨跌停按板块(主板10/创科20/ST5)查真相源不hardcode
- 突破类型 = pattern 维(箱体/平台/VCP/新高); context 连续分 = stratify 维。
- 证据: 52周高点接近度 (顶刊); Darvas/Minervini-VCP/A股平台突破/主升浪/换手板 (民间, 阈值须A股重拟合不照搬美股)。

### 3.4 防过拟合闸 (N cells × M 因子 = 巨量试验, 必用)
- **DSR (Deflated Sharpe ≥0.95)**: 惩罚"试了多少次", 单 cell 高 Sharpe 几乎必含 selection bias (Bailey-López de Prado)
- **PBO via CSCV/CPCV**: 量化 cell 胜出是否过拟合; 对小样本 cell 比单条 walk-forward 更稳
- 全谱呈现 (哪些cell有edge/哪些没/哪些样本不足), 不只报赚钱cell (防 selection bias)

## 4. 无监督聚类的定位 (验证手工形态, 非 alpha 源)
- 在 (滚动收益,波动,MACD,range_pos) 上跑 HMM(优先, 建模时间持续性) / GMM, 与手工形态做混淆矩阵 + 比前瞻IC:
  - 聚类簇与手工态都 IC≈0 → 形态维在A股没前瞻信息, **砍掉换波动率regime**
  - 聚类簇 IC 明显更高 → 手工切错维度, 用聚类簇替代
- 诚实预期: regime 过滤提升温和 (Sharpe 0.37→0.48 量级), "聚类本身变 alpha"证据弱, 别期待奇迹。

## 5. PIT 红线 (调研一致警告, 对照 §4.1/§4.2)
- PTH/range_pos: 用 [t-252, t-1] 严格 <t; 市场态用滞后收益不含当日
- regime 分桶阈值: rolling/expanding 分位, **禁全样本分位** (=未来信息)
- HMM: 用 filtered 概率 Pr(s_t|≤t) **不能用 smoothed**; 参数 walk-forward 重估**不能全期fit** (否则=v3.2 系统性泄露)
- 异常警报: 加了 regime/聚类后 RankIC 跳>0.3 或 baseline+50% → 先怀疑 cell 用了未来信息, 不是兴奋

## 6. 最小可落地第一步 (不一次煮沸海洋)
先做 **市值中性 + 位置(PTH/range_pos)×波动regime 2×2 cell**, 在每 cell 内测已有因子 forward IC (含成本) → 看哪个 cell 有稳定 edge。哪个 cell 有 edge 再细化 + 加趋势/突破维。HMM/聚类放第二轮。
> 转正铁律不变: cell/因子最终须过 execution-aware 含成本 OOS backtest (C-R1/C-R2) + DSR/PBO, 不凭 IC 选 (§4.5)。

## 7. 含成本裁决 (experiment_position_reversal.py, 2023+ 4 cell) — 位置-反转 ≠ alpha

| cell | IC快筛 | 含成本年化 | max_dd | R1 | verdict |
|---|---|---|---|---|---|
| overall | +0.051(反转) | **-29.1%** | -72% | IC_POSITIVE_BUT_UNTRADABLE | KPI_FAIL |
| 中盘高波 | +0.058(最强反转) | -10.0% | -42% | IC_POSITIVE_BUT_UNTRADABLE | KPI_FAIL |
| 小盘低波 | +0.006(弱) | +3.8% | -58% | TRADABLE | KPI_FAIL |
| 大盘低波 | -0.006(动量) | -5.4% | NO_EDGE | — | KPI_FAIL |

**裁决: 位置/形态轴 IC 真(反转), 但不是可交易 alpha — 全 4 cell KPI_FAIL** (§4.5 再现: IC 最强反转 cell 恰最不可交易, 买最深超跌=接刀 -72% max_dd)。

**对方向的含义 (关键, 正向印证用户原始方法论)**: 形态/位置/分层 = **结构层 (segment, 决定在哪个 cell 条件化), 不是 alpha 本身**; alpha 来自 cell 内**叠加高价值因子** (量→换手→筹码→资金→北向/板块, 用户原话)。F0/F1 已建成+验证**结构层** (正交轴方向 + vol-regime 条件化成立)。下一相 = 结构落成 cell 框架 (F2) + 拉高价值因子 (北向 hk_hold/筹码/资金, 与 tdxhub 退役要拉的 tushare 数据同源) 在 cell 内挖真 alpha; **不再在位置/形态轴上找 alpha**。
