## Segment 分层 Taxonomy 设计 — 架构师版 (2026-06-15)

> **状态 (2026-06-15 P0-P3 后部分 superseded)**: 逐层解锁防组合爆炸 + DSR/PBO 去偏骨架仍有效; 解锁判据偏离 — "OOS IC 增益 > 基线"已被 R1 推翻, 改**含成本 execution-aware 绝对收益 sufficient gate + DSR 去偏**(IC 仅 necessary 快筛)。另: 立方体 3 轴→**5 轴**; S1 裸 K 线 reversal base 经 P3 判**结构性不可交易**; 数据优先级反转(慢衰减绝对>快衰减相对)。owner 冲突优先: 判断法典=`docs/strategy_validation_contract.md` · 缺陷体系/根因=`analysis/design_deficiencies_extension2_20260615.md`(N16/17/18 直接点名本文逐层解锁) · P3 裁决+Phase D=`analysis/p3_execution_aware_verdict_20260615.md`。

> 状态: live。owner=本文件 (Segment 维细化), 上承 `conditional_stage_strategy_design_20260614.md` +
> `multidim_strategy_architecture_20260613.md` + MASTER §5。**在既有立方体框架内逐层推进, 非另起炉灶。**
> 缘起: 用户 direction (形态分更细 / 流通市值+换手率+成交量 / tushare+iFinD 充分挖掘 / 顶层设计逐层推进)。

### 立方体回顾 (不变)
cell = **Segment(形态/规模/流动性/资金/产业链) × Feature(打分因子) × Policy(出仓公式/模型)**。
本文件细化 **Segment 维** = 把"哪类股票在哪种状态下"分得更细更专业。

### 架构师铁律: 逐层解锁, 禁组合爆炸 (防过拟合命门)
天真叉乘 = 5形态 × 2零轴 × 5子型 × 4市值 × 4换手 × ... = 数千 cell, 单看高 cell = selection bias 必假。
**纪律**: 一次只解锁**一个 Segment 轴**, 证 OOS IC 增益 > 当前最佳基线 (DSR 按"试过的 segment 定义数"多重比较
校正, Bailey-LdP) + 独立 holdout + PBO, 才把该轴并入立方体, 再解下一轴。每轴跑前 pre-reg 冻结搜索空间。
**每次验证前先跑防泄露工具** (用户提醒): `pit_guard.assert_pit_clean` (特征追加未来 bar 不变) + `leakage_detect`
(单特征 AUC 上限 / 时间切 / embargo) — 不过门不验。

### Segment 轴分层 (按解锁顺序, 数据成本从低到高)

**S1 技术形态 (纯 K线, 0 数据成本 — 先解锁, 进行中)**
- 已验证 (实验1/2): Weinstein 5 阶段 + MACD 零轴 (DIF=ema12-ema26 符号)。突破中(1.5)是关键 regime:
  reversal +0.156 / macd·ma -0.116/-0.117 (待 DSR+ablation)。
- **细分子型 (我的专业补充, 用户"低位多种"具象化)** — 全由 K线派生, config 化进 `technical_stage.yaml`:
  - 低位横盘 (range_pos<0.3 + |MA30 slope|<ε + vol_ratio<0.8): 缩量筑底
  - 冲高回落后相对低位横盘 (近 N 日内有高点 + 当前 drawdown_from_recent_high<-X% + 当前 range_pos 中低 + 走平): 套牢盘消化
  - 低位放量 (range_pos 低 + vol_ratio>1.5): 可能启动
  - 突破回踩 (近期上穿 MA30 + 当前回落到 MA30±Y%): 1.5 的精细化
  - 趋势中继 (Stage2 内 + 回撤 5-15% + 缩量): 上升途中的买点
  - **历史区间分位 (expanding PIT, 只用 ≤t)**: close 在自身历史的分位 (低/中/高), 禁全历史分位 (=lookahead)。
- Optuna 搜这些子型的**定义参数** (range_lookback / flatness_eps / pullback_depth / vol_ratio_thresh /
  zero_axis × stage 交叉), DSR 治理。Modal 并行 (子型 × 公式 × 全史 = embarrassingly parallel)。

**S2 规模 (流通市值, 用户点 — 需 tushare daily_basic.circ_mv, 小 ingest 高价值)**
- circ_mv tier: 大/中/小/微 (分位切, PIT as-of t)。假设: 小盘更反转 (散户主导)、大盘更动量 (机构主导) →
  reversal edge 可能在小盘×突破中更强。S1 证完加此轴, 验证 size 是否调制 edge。

**S3 流动性/活跃度 (换手率+成交量, 用户点 — daily_basic.turnover_rate + K线 amount)**
- turnover_rate tier + 量能 (amount 相对自身均值)。假设: 高换手=情绪驱动(更反转)、低换手=被冷落。
  与 S1 低位放量/缩量正交但互补。S2 后解锁。

**S4 资金/筹码 (tushare moneyflow_dc / cyq_chips — validation-gated, 既是 Segment regime 也是 Feature)**
- regime 轴: 个股近期净流入/流出 (moneyflow); 筹码集中度/获利盘 (cyq 自算, winner_rate 口径冻结须本地复权重算)。
- 口径铁律: 行业/概念资金流 flow vendor = membership vendor (东财链自洽, 禁同花顺第三套, §4.5 sector leakage)。

**S5 产业链/行业 (iFinD 产业链 + 专有数据 + tushare 申万行业 — 最后层, 数据成本最高)**
- iFinD 产业链挖掘 (用户点): 个股在产业链位置 (上游/中游/下游) + 链景气传导 → segment by 链 regime。
- iFinD 专有数据 (风险因子/ESG/事件) + 申万行业中性化。交互式调研定位 → 自算 rolling → 入库做 regime_gate
  (见 data_access_surface_menu iFinD 定位: 定向核证非批量, 批量结构化仍走 tushare)。

### Feature 维 (Segment 解锁后, 在 proven cell 内加打分因子) — 主辅契约 (C3)
公式 (reversal/macd/ma/turtle) 主出仓 → + tushare 因子 (资金流/筹码/财务/daily_basic 风格 pe/pb) 调制 →
+ iFinD 专有。每因子族 = 一个 Optuna 开关 (feature_registry production_ready groups), 全 OOS 闸。

### 数据获取路线 (Segment/Feature 解锁驱动, 不为没证明的数据建管道 — architect rule6)
| 优先级 | 数据 | 解锁的轴 | 成本 | 触发 |
|---|---|---|---|---|
| P0 | tushare daily_basic (circ_mv/turnover_rate/pe/pb) | S2 规模 + S3 流动性 + 风格因子 | 小 (1接口) | S1 证完即接 (高杠杆: 一接口开两轴) |
| P1 | tushare moneyflow_dc / cyq_chips | S4 资金/筹码 | 中 | S2/S3 后, 超 +0.064 才保鲜 |
| P2 | iFinD 产业链 + 专有 | S5 产业链 | 高 (交互式) | S4 后, 定向核证 |

### 执行序列 (系统化, 每步 pit_guard → 验证 → DSR/PBO → 解锁)
1. **[now] S1 技术形态细分**: Optuna 搜子型定义 (低位横盘/冲高回落低位横盘/突破回踩/历史分位 × MACD零轴 × stage),
   pre-reg 冻结搜索空间, pit_guard 先行, DSR 多重比较校正, Modal 并行。产出: (公式×细分形态) edge 矩阵, top cell 独立 holdout 验。
2. **+S2 规模**: 接 daily_basic.circ_mv, 在 S1 proven cell 内加 size tier, 验 lift。
3. **+S3 流动性**: 加 turnover/volume tier。
4. **+S4 资金筹码 / +S5 产业链**: validation-gated 逐个。
每步: 跑前 grill (搜索空间非空? 输出可决策? 成本 vs 产出?) + pit_guard/leakage_detect; 跑后 DSR/PBO + 独立 holdout; 只在 OOS 增益证实才解锁下一轴。
