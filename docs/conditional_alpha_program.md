# 条件化 Alpha 工程纲领 (Conditional-Alpha Program) — 权威方案

> **owner = 本文件** (alpha 工程执行真相源)。supersede `analysis/alpha_validation_program_spec_20260614.md`
> (IC-first 范式已反转, 旧 S0 重型 family 框架已弃用)。goal.md「alpha 工程」节为薄指针。
> authority 顺序: 判断法典 `docs/strategy_validation_contract.md` (R1/R2/PIT 红线) > 本文件 (执行纲领) > 各实验 prereg。
> 立法日 2026-06-16 (用户三次纠偏后固化: ① 截面月度排名是错方法 → 条件化; ② 不要字面实现 → 发散补全;
> ③ 不要临时脚本 → 模块化+分层 DB+实验记录, 且要查清为何旧方案被架空)。

---

## 0. 创世层 (genesis, 不可变, 陌生人可判)

- **为何存在**: A 股 alpha 是**条件性**的 — 同一公式只在特定形态×阶段的票上有效, 套在错误状态上是噪音。
  无条件全市场截面排名把真 edge 平均成个位数 (实证 RankIC 摊薄到 +0.064)。本纲领的存在意义 =
  **先把股票分到正确的 (形态×阶段×分层) 状态, 在状态内按公式的买入→卖出 episode 度量真实含成本收益,
  再逐层叠加因子增强** — 让 edge 不被稀释。
- **死亡线 (≤3, 违者该项作废)**:
  1. **泄漏死**: 任一实验用到 t 之后信息 (built_at>t / latest-snapshot / label embargo 不足 / 结局字段当特征) → 作废。
  2. **散落死**: 任一 alpha 实验不走唯一 harness (`services/phaseD_signal_eval`) 留档进 `experiment_store` →
     不算交付 (它没发生过, 因为下个 session 看不到)。**这是本纲领针对"旧方案被架空"根因 (H1/H2) 的死亡条款。**
  3. **谄媚死**: 任一判据在看到结果后被改 / 只报赢的 cell 不计全 cell 多重比较 → 整实验作废 (prereg_hash 机器锁)。

## 1. 根因备忘 (为何旧方案被架空 — 不可重蹈)

机器审计裁决 (agent a3b2fbfa, 2026-06-16, 证据见 commit 关联):
- **H1**: 旧 S0 重型 family (`chunkyctl jobs --family consumer_alpha_validation` + executor) 被判"想象的复杂度"
  主动绕过 → 沦为孤儿 (只跑 2 行 dry)。
- **H2**: **无任何 gate 拦"实验不走 harness / 不写库"** → 纪律靠人记, 必漂移。**本纲领第 4 节的 3 道门即补此。**
- **H3**: Alpha158 从未动工 (0 表 0 模块) 却长期标 in_progress = 完成言过其实。
- 漂移量化: 15/15 脚本绕过契约层 · 47 个 `analysis/*.json` vs 24 库 verdict (留档双轨) · 11 脚本/1 天。

教训: **重型框架会被弃用; 唯一活得下来的是轻 harness (`phaseD_signal_eval`, 已被 5+ 脚本复用)。
所以扶正它为唯一入口, 而不是再造重框架 (奥卡姆)。**

## 2. 方法论 (5 层, 用户口径 + 发散补全)

| 层 | 人话 | 机器话 (实现 owner) | 数据/PIT |
|---|---|---|---|
| **L1 形态** | 高位/低位/横盘/底部盘整 | `technical_stage.py` 60周区间分位 range_pos + Weinstein stage; 落 `feature_store` segment 表 | K线, PIT 干净, 2020+ 够 |
| **L2 阶段** | 上涨/下跌趋势 | 同上 stage 2(上升)/4(下跌)/1(底部)/1.5(突破)/3(顶部) | K线, 内联重算 (不依赖只到 2023 的旧落库表) |
| **L3 分层** | 如 MACD 零轴上下 | `macd_golden_cross._variant_for_dif` 零轴轴 + 位置桶, 作正交 segment 描述子 | K线 |
| **L4 公式 episode** | 找每个公式**买入信号→卖出信号**的持仓段, 刻画其特征 | **新建 episode 评估能力 (加进 `phaseD_signal_eval`, 不另起脚本)**: 入场 T+1 open(一字板剔)/**出场与持仓周期条件在 (形态×分层×公式) cell 上度量, 无全市场统一卖出信号** (见发散补全)/含成本; 每 episode 打 segment 标 → 公式×segment cell 的 win_rate/payoff/expectancy/hold_days/含成本年化 | K线+成本栈 |
| **L5 条件化增强** | 在赚钱的 cell 内, 再用其他信号提高 alpha | 逐维解锁: base价量 → +Alpha158 → +**板块概念热度/成交量结构/资金流向** → +筹码(2023+); DSR/PBO 跨全 cell 去 selection bias | feature_store |

**出场/持仓周期 — 条件化, 无全市场统一卖出信号 (用户 2026-06-16 纠偏铁律)**:
- 用户原话: "不可能有通用于全市场的卖出信号, 而是要根据不同形态和分层还有公式本身的卖出信号来给出持仓周期"。
- 实现: 持仓周期 = f(形态 × 分层 × 公式自身卖出信号), **按 cell 从 episode 数据度量/优化, 绝不施加全局出场规则**。
  公式有自身卖出信号 (MACD死叉 / 海龟 exit_level / 活跃度 strong_line) 的优先用其信号; 无则按该 cell 实测最优
  持仓窗 + 保护性止损。出场口径与入场口径同样 segment-conditional。

**发散补全 (本纲领新增, 非用户字面)**:
- **辅助增强源 (用户 2026-06-16 点名 "一定有辅助作用")**: 板块概念热度 (dc concept heat, 2024+) / 成交量结构 (量比/换手分位/缩放量) / 资金流向 (mf_trend + 板块资金流 moneyflow_ind_dc) — 进 L5 增强层, 在赚钱 cell 内逐个验增量。
- **segment-因子正交性排查** (宪法级防泄露, 非可选): Stage1.5 定义(从MA30下穿)与 reversal(近20日跌幅)机械重叠 → IC 虚高 (Berkson/collider)。每个 segment×因子 cell 过 Gate0 时强制用正交定义 holdout 复测。
- **无监督 regime 发现** (用户选定的发散项): 不只用人工形态标签, 用聚类/HMM 让数据自分隐含 regime, 与人工形态轴互验。**优先级靠后** (人工 L1-L4 跑通后做)。

## 3. 因子层 (feature_store, 逐层加, 全 PIT 干净)

| 阶段 | 因子族 | 落点 | 状态 |
|---|---|---|---|
| F0 | 现 5 因子 (mom/reversal/vol/mf_trend/roe) | `feature_store.fact_feature_panel` | 已有 |
| **F1** | **Alpha158 (qlib)**: 64+ OHLCV 衍生 (KMID/KLEN/ROC/MA/STD/BETA/RSQR/MAX/MIN/QTLU/CORR...) 干净重算 | `feature_store` 新表 | **待建 (task #26 真正动工; 建表前不许标 done)** |
| F2 | 形态×阶段 segment panel | `feature_store` segment 表 | 复用分类器, 待物化 |
| **F3 辅助增强** | **板块概念热度 / 成交量结构 / 资金流向** (用户点名) + cyq winner_rate / forecast 预告 | `feature_store` | 逐层最后加, cell 内验增量 |
| F1b | Alpha360 原始价量 6×60 (ML 料) | — | 推迟 (用户未选) |

## 4. 治理 — 防再漂移的 3 道强制门 (G3, 牙齿)

> 这是本纲领与旧 spec 的本质区别: 旧 spec 写了方案没有门 (H2)。以下门入 `.moth/assertions/claims.yaml` + check 脚本, commit 即扫。

- **门1 散落死**: `backend/scripts/experiment_*.py` 必须 import `phaseD_signal_eval` 或 `experiment_store` (留档),
  否则 FAIL。禁裸跑不写库。
- **门2 完成言过其实**: 若 task/doc 声明某因子族 (Alpha158 等) 已建/in_progress, 对应 `feature_store` 表必须存在
  (claims-vs-reality), 否则 FAIL。
- **门3 留档收敛**: `experiment_store` 为唯一 verdict 真相源; `analysis/*verdict*.json` 须有对应 verdict 行
  (双轨收敛), 否则 WARN→FAIL。

## 5. 验收 (含成本 R1 + KPI, 每 cell)

Gate0 PIT 干净 (pit_guard + shuffle-label null + buyable-only IC + segment-因子正交 holdout) →
Gate1 OOS IC>baseline (necessary 快筛, IC<0 直接砍) →
Gate2 MC 截面置换 (Bonferroni×全 cell) →
**Gate3 money 硬门 (R1, 不可绕)**: `phaseD_signal_eval` 含成本 episode 回测 tradability=TRADABLE 且 KPI 四项真触发 →
Gate4 DSR + block-bootstrap (按事件聚簇分块) + PBO →
Gate5 子周期稳定性 (含 2024 微盘崩盘)。

KPI (owner=goal.md): 年化≥30% / max_dd≥-20% / 超额 HS300>0 / 月胜率≥55% (含成本 OOS 2020起 100万)。

## 6. 执行顺序 (先修法再建, 用户 2026-06-16 拍板)

**G 阶段 (修法, 先做)**: G1 扶正 `phaseD_signal_eval` 为唯一 harness + 加 episode 评估能力 · G2 退役/降级
consumer_alpha family 孤儿 · G3 落 3 道门 · G4 DB 分层钉死 (因子→feature_store / 记录→experiment_store /
raw→tushare_raw / K线→market)。

**B 阶段 (建, G 后)**: B1 segment panel 物化 (L1-L3) · B2 episode 引擎跑现有公式 × segment → 找赚钱 cell ·
B3 Alpha158 建表 (F1) · B4 逐维解锁增强 (L5) · B5 无监督 regime (发散项) · B6 含成本 KPI 验收。

数据现状 (agent a70d, 实测): 价量+segment+Alpha158 全 2020+ 够、无生存者偏差 (含 232 退市股);
cyq/forecast 仅 2023+ (上游限制补不了); 预收/研报没拉全**可补** (tushare 代理限流=每分钟级: 单接口 120/多接口 200,
撞了退避几分钟重试即可, **非当日封顶** — 我此前误判为"配额墙"是错的; advrecv 正后台 backfill 重拉连续季报);
HS300/500 成分历史缺表 (超额暂用指数点位)。
