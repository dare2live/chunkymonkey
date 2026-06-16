# 鱼身框架设计 — 主升浪猎手 (确认主升→阶段性买入→鱼身持有→破位出场)

> 日期: 2026-06-16 · owner: 主会话 (controller) · 状态: 设计 spec (用户多轮 refine 累积固化, 待建+含成本验)
> 真相源: 用户口述 (本 session) + docs/zhushenglang_hunter_research_log_20260528.md (二次突破入场实证) + MASTER §5
> 前提裁定 (已 measured): 预测哪个突破/金叉→主升浪(>60%) 在买点**不可预测** (干净 purged CV OOS AUC 0.51≈null);
>   裸 reversal 抢最深超跌(抢鱼头)含成本 -29%~-35% 结构性不可交易。→ 不抓拐点, 改吃鱼身。

## 0. 核心 pivot (用户 2026-06-16)
**不抓精确起涨点(鱼头, 不可预测), 不要鱼尾, 只吃鱼身**: 等趋势确认后跟进, 阶段性买入, 趋势破位出场。
顺势/确认入场 = tractable edge; 拐点预测 = 不可预测 (已证)。

## 1. 时间周期 (用户: 主升浪看长线, 周线探索 / 日线周线结合)
- **周线** = 主升浪趋势确认 + 持仓周期主轴 (长线现象, 噪声少; Weinstein 30周线本来就是周线口径)。从 daily qfq 重采样 (open=首/high=max/low=min/close=末/vol=sum), 或 tushare weekly。
- **日线** = 入场/出场精确时机 (在周线确认的主升内, 日线择时买卖点)。
- **日线周线结合**: 周线定"在不在主升+该不该持有", 日线定"今天买/卖点"。

## 2. 确认主升 (segment 结构层, 圈鱼身候选池, 非排序信号)
- **复用** fact_segment_panel (已落库 7.15M 行): range_pos + MACD零轴 + dwell (有信息)。
- **不直接用** (F0 measured 否): Weinstein 5态单标签当先兆 (stage1.5突破中位 fwd10 -0.97% = 接刀)。
- **新建**: "确认主升" = event-in-context (**周线**多头排列/站稳关键均线 + 趋势纯度 ADX/ER + 底盘整理后放量突破确认), 补 Weinstein 缺的 MA斜率/量比/RS 三维。
- 诚实: 形态/位置作**条件桶(在哪个cell)成立**, 作直接择股 IC≈0 → 只圈池不排序。

## 3. 阶段性买入点 (鱼身入场)
- **复用最强实证捷径**: **二次突破入场** (研究日志: 突破日 3.5% → 二次突破 42.3%, 全研究最大单步增益; 原型灭失需重建) + V3 极严 AND filter (C3+前安静+后强势+下影 → 58%)。
- **回调确认**: 浅回调不破 + 缩量回踩 + 再放量加速 (先半仓→确认上破加满)。入场日**温和涨+缩量**反而好 (放量暴量胜率低), 且 T+1 可成交性最好 (避一字板)。
- **新建/未测**: 延迟入场 (突破后 5/10/20 天再确认) — 数据齐可直接做。

## 4. 鱼身持有 + 破位出场 (不要鱼尾)
- **复用** V4 分级 trailing (<20% 用 -8%/50-100% 允回入场/100-200% 锁5%) + "主升活着"判定 (ma60斜率/close<ma20连续/distribution累计); 实证捕获率 90%。
- **出场用波峰口径探索** (用户): 死叉=最差出场 (实测中位 -2.25%); 周线破位/MA破/移动止盈更抓鱼身。出场立为**独立研究对象** (对比固定持有/ATR/状态机, 同一 walk-forward)。
- **新建**: CYQ 出货预警 (筹码 distribution 累计, spec 已写 0 行代码; 全项目期望值最高单项)。

## 5. "之前发生了啥" context 因子 (multi-horizon 1周/1月/3月/6月回看, 全 PIT <=t)
量价轨迹 (放量/缩量/波动收缩/回调/新高距离) + 资金 (moneyflow 2010+/moneyflow_dc) + 筹码 (cyq **2018+** 已解冻回填) + **券商盈利预测 report_rc (2010+, 之前预期上调?)** + **机构调研 stk_surv (之前机构密集调研?)** + 业绩预告/快报。
> 单因子全弱 (~1.1x); 价值在 cell 内组合 + 作为"确认主升"的 context, 非单独排序。

## 6. 验收 (转正铁律, 不可省)
execution-aware 含成本 backtest (T+1 open/涨跌停剔/非对称成本/容量/停牌冻结) + **期望值账单** (月入场×盈亏比×仓位→年化, 胜率是诊断量非放行) + **random-entry 同退出对照** (V9 露馅: Optuna入场58% vs 随机55% = 大头在universe+退出+beta不在入场) + **组合层 NAV** (per-trade→5仓重叠真NAV, 年化压缩60-70%) + DSR/PBO 防搜出过拟合。**不按 IC 选 cell** (C-R1)。

## 7. 复用 vs 新建 速查
| 环节 | 状态 |
|---|---|
| fact_rally_ground_truth (主升浪标签) / fact_segment_panel (形态) | 复用 (已落库) |
| 二次突破入场 (3.5%→42%) / V4 trailing 出场 | 复用骨架 (原型灭失需重建) |
| 周线重采样 + 日线周线结合 | **新建** (用户本轮) |
| event-in-context 确认主升 (周线多头+趋势纯度+底盘突破) | **新建** |
| 出场引擎 (波峰口径, 死叉外的规则) | **新建** (task #40 episode 条件化出场) |
| report_rc/stk_surv 等 context 因子接入 | **新建** (report_rc已注册待用, stk_surv需注册) |
| 期望值账单 + random对照 + 组合NAV | **新建** (三盲点补法) |

## 8.5 验证进展 (2026-06-16 含成本实测, 主会话主导)

> 真相源: `experiment_yushen_backtest.py` (per-trade) / `experiment_yushen_portfolio.py` (组合NAV) /
> `experiment_yushen_selective_entry.py` (入场信号裁决, experiment_store family=yushen_entry_alpha)。

**裸基组合 (粗突破入场) = 硬 FAIL** (含成本年化 +3.1% / max_dd -46.8% / Sharpe 0.25; KPI 30%/-20% 差量级)。
2024 微盘崩盘整段骑下去 (周线破位出场滞后)。

**入场信号有效性裁决 (周线确认 context 内, 同移动止盈/周破位出场, 含成本13bps, random 对照隔离 beta)**:

| 入场 | n | 均值 | 胜率 | 盈亏比 | >30% | 比随机增量 | bootstrap |
|---|---|---|---|---|---|---|---|
| A 粗突破(20日新高) | 70,665 | +0.65% | 35.2% | 2.08 | 5.2% | **+0.12%** | 无 alpha |
| B 二次突破(出箱→缩量回踩→再破前峰) | 561 | +3.34% | 41.7% | 2.31 | 9.1% | **+2.81%** | 全样本 p(>0)=1.000 / 5%下界 +1.40% |
| C 随机对照 | 12,388 | +0.53% | 35.0% | 2.07 | 4.7% | (基准) | — |
| B OOS (2025-06+) | 137 | +4.79% | 45.3% | — | — | **+2.55%** | OOS p(>0)=0.899 / 5%下界 -0.64% |

**裁决**: 价格行为入场论题**活在二次突破, 死在粗突破**。粗突破 (见高就追) ≈ 随机 = 撞 R1/无条件墙;
二次突破 (回调确认后再突破) **样本内 alpha 铁** (p=1.0, 下界+1.40%), **OOS 方向同向但 n=137 欠样本** (90% 为正,
未到 95% 显著)。低胜率 (34→42%) 之前的锅在粗入场, 非趋势跟踪天花板。
**遗留缺口**: (a) 561 笔太稀, 建不起组合; (b) OOS 须扩样本锁死。
**下一步 (pre-reg)**: Optuna 调 BASE_N/RETR/HOLD_TOL/MAX_BASE/缩量阈值 — 目标函数 = **比随机的增量** (非裸收益,
防拟合 beta), walk-forward OOS; 放宽规则提频但守住增量 → 组合 NAV含成本 → 叠 context 因子 (筹码/资金/预期上调) 过滤。

## 8.6 系统化 Optuna pre-reg (冻结判据, 2026-06-16, 跑前必冻 — CLAUDE§5/plan_validator)

> 触发前置: 入场 alpha 已证稳健可调 (§8.5 鲁棒性扫描, 8组增量全+2~3.4%/OOS同向)。**但到 KPI 有三道墙, 仅清墙1**。

**三道墙 (诚实分解, 防"入场 alpha 真=快到 KPI"错觉)**:
- 墙1 入场 edge: ✓ 二次突破比随机 +2~3.4% 稳健, OOS 坐实 (n=589@#6)。
- 墙2 回撤控制: ✗ **真正硬墙** — 裸基组合 max_dd -46.8% → 需 ≤-20%; 入场 alpha 不解决回撤, 须 regime门/仓位/出场。
- 墙3 含成本组合年化 ≥30%: ✗ 入场+风控合起来的最终真金白银, 未知。

**冻结目标函数 (C-R1/C-WinReturn 铁律)**: maximize **含成本组合 OOS 年化**, subject to **max_dd ≥ -20% (硬约束/惩罚项)**;
**禁用** per-trade 增量 / IC / sharpe 当唯一目标 (per-trade 增量是诊断量, IC 减掉 cohort 漂移看不见 long-only 的钱)。

**冻结搜索空间 (非空, 三组)**:
- 入场: base_n∈[30,60], retr∈[0.04,0.10], hold_tol∈[0.05,0.10], max_base∈[60,120], vol_mult∈[1.0,2.0]。
- 出场: trail∈[0.85,0.92], weekly_break on/off, max_hold∈[60,180]。
- 风控 (攻墙2): regime门 (HS300/中证500 周线在不在多头) on/off, 仓位 (等权 vs 波动倒数), max_pos∈[10,30]。

**冻结 OOS 协议**: walk-forward expanding_monthly, train ≤2025-05, OOS 2025-06+ **完全留出**; selector 只读 oos_* 列;
random-entry 同context对照保留 (隔离 beta); DSR + PBO 防多重比较过拟合 (8+维搜索空间必做)。

**冻结成败判据 (跑前写死, 不事后挪门柱)**:
- PASS: 含成本组合 OOS 年化 ≥30% AND max_dd ≥-20% AND 超额HS300>0 AND DSR>0 AND PBO<0.5。
- FAIL: OOS 年化 < 裸基baseline OR max_dd 更差 OR DSR/PBO 报过拟合 → 诚实记录, 不调判据复跑。
- 部分: 年化或回撤单项达标但非全 KPI → 记 tradeoff, 不当 delivery。

**执行面**: 本地 Optuna (无 Modal 花钱 → assistant 权限内可跑); 走 services.optimization 中央层不裸调 study.optimize;
阈值走 optuna_config.yaml; 跑前 plan_validator.enforce_optuna_plan() 须 PASS (搜索空间非空已满足)。

## 8.7 OOS +95.9% 对抗泄漏审计裁决 (2026-06-16, 6向量Workflow wgq5k8z37)

> 用户纠: 异常高 OOS 不该红线判死, 要用防泄露工具真查 (探索阶段红线=去查不是挡箭牌)。结论印证: 信号无泄漏 bug, 但+95.9%是peek+beta非真alpha。

**裁决 PARTIAL** — 嵌套分解 (非加和):

| 来源 | 贡献 | 证据 |
|---|---|---|
| 幸存者偏差 | 0pp | 表含退市股完整死亡螺旋(19只OOS退市末25日中位-69.3%), 全量喂入 |
| 短窗年化放大 | ~0.5pp | OOS 251交易日≈满年(252/251=1.004), 月度分散11/13正 |
| 题材集中 | 0pp | 404笔/368股/13行业, AI仅6.3%P&L, HHI 0.016 |
| 信号PIT泄漏 | 0pp | 9796笔0违规, 周线shift(1)/二次突破<=t/T+1 open, 无look-ahead |
| **入场参数OOS-peek** | **+38~69pp** | risk_harness.py:36注释把#3的"OOS+3.33%"写进选参理由; 脚本train准则选的是#6; 干净#6 OOS仅+26.7% (一处peek吹高3.6x) |
| **小盘beta** | **~35~40pp** | OOS=小盘牛市(中证500+42.5%/中证1000+35.3%/HS300+24.1%); OOS>>TRAIN是regime luck非策略变强(TRAIN期指数≈0%策略+37.4%) |
| 残余真alpha | **−12~+19pp(中心≈0)** | DSR p=0.83不显著, 单一牛市窗; 干净#6(+26.7%)还跑输小盘基准(+38.9%) |

**诚实 OOS 超额 alpha ≈ −10%~+15% (中心≈0, 不可分于运气), KPI_FAIL**。唯一干净"砖"=二次突破 per-trade +2.5~2.8%(boot p=0.99), 被复利+peek+beta放大成虚高的"楼"。

**方法论修 (固化进后续所有寻优, owner=[[feedback-param-selection-peek]])**:
1. 全参数(入场+风控+出场+因子)train-only选; OOS列冻结前物理遮蔽; 多参数进同一walk-forward中央层一次联合选优(非分步各peek)。
2. 裁决换超额(对标中证500/1000同窗), 非裸年化; 别把beta当alpha。
3. 单一OOS窗不够 → regime-conditional/滚动OOS(牛/熊/震荡分段)。
4. 扩样本/降搜索维 把DSR拉过0.95再谈可投入。

## 8. 数据真相源铁律 (2026-06-16 教训)
data_start 用 catalog `history_start` (tushare文档一手), 非冻结的 registry; 字段审计别冻结数据 range; 限流 0 行≠数据无。已修: cyq 2023→2018 解冻回填。(owner=memory feedback-data-start-truth-source)
