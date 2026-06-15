## 扩展设计批判 II — 条件化分层框架深层缺陷体系 (8-lens 对抗复审版, 2026-06-15)

> 状态: live。owner=本文件。上承 `design_deficiencies_and_extension_20260615.md` (D1-D7 基线), 本文在其上**扩展深挖**, 不复述。
> 缘起: 用户"顺着我的思路扩展和深挖, 看之前的设计有哪些不足"(第二次, 要求更深)。
> 方法: 8 个正交 lens (objective-metric / missing-dimension / validation-survival / ashare-microstructure /
>   signal-tradability / long-only-constraint / data-priority-base / governance-process) 批判旧设计 → 对抗式 skeptic
>   逐条试图反驳 (读源码核实是否已覆盖) → 综合。批判 44 条 → 对抗验证确认 34 条 (N1-N30 去重后), 反驳/降级 10 条。
>   severity 经对抗复审用"是否造成/将造成真金白银亏损"重定标 (多条从 CRITICAL/HIGH 下调, 理由: 当前 cell 已 KPI_FAIL,
>   缺陷是 forward-looking design gap 而非 live 假阳)。
> Controller 亲核 (不只信 agent 报告, CLAUDE §10): N1 (gate2 置换只打乱日内 label 保留当日收益分布,
>   `experiment_ablation_gate2.py:79`)、N9 (`load_kline` 只 SELECT close/high/low, `experiment_l0_baseline.py:63`)、
>   N2 (`anomaly_verdict` 仅 |IC|>0.3 / 相对>+50% 两单边分支, 无"IC正但收益负"分支, `experiment_harness.py:49-66`)、
>   N8 (合约 `strategy_validation_contract.md:13/66/83` 白纸黑字要求 limit-up buyability/one-line boards/capacity,
>   引擎 `portfolio_returnbacktest.py` 一行未实现) —— 4 个最承重锚点全部对照源码属实。

## 0. 一句话结论

> 这套验证流水线在数学上**不可能**看见"long-only 绝对收益", 却用它给不赚钱策略发"统计显著"的合格证。Phase B 已用 33σ REAL_EDGE + gross -34.6% 把这件事坐实。**根因不是阶梯顺序错, 是阶梯每一道闸的 null 构造本身对绝对收益盲。**

## 1. 整合后的缺陷体系 (去重 + 按真金白银 severity 排序)

severity 经对抗复审重定标后的**最终档位** (sev 列)。NEW = D1-D7 未覆盖; DEEPENS:Dx = 已有条目的机制级深化。

### Tier-S — 数学根因级 (验证范式本身对绝对收益盲)

| # | 缺陷 (lens) | 最终 sev | 关系 | 一句话机制 | 代码锚点 |
|---|---|---|---|---|---|
| **N1** | 整套随机性军火库 (Gate2/3/4/5) 的 null 全是排序型 (objective-metric) | **CRITICAL** | DEEPENS:D1 | 置换只打乱 feature↔return 配对, 保留每日收益分布 → 数学上对 cohort 绝对涨跌恒不变; 崩盘 cohort 可全数过闸 | `experiment_ablation_gate2.py:79` `ll = l[rng.permutation(l.size)]` |
| **N2** | 异常核查门 `anomaly_verdict` 只抓 "IC 太高", 数学上抓不到 "IC 真但绝对收益负" (governance) | **HIGH** | DEEPENS:D1 | 单边 leakage 检测器; long-only 真正亏钱模式 (IC>0 + 含成本收益<0) 100% 静默 CLEAN | `experiment_harness.py:49-66` |
| **N3** | REAL_EDGE / confirmed_by_owner "转正章"纯靠 IC 置换 p_adj<0.05 盖, 不要求任何含成本绝对收益 (governance) | **MEDIUM** (复审降) | NEW | 系统最强章盖时完全没看过钱; Tier-2 是解耦手动脚本不是前置 gate | `experiment_ablation_gate2.py:122,150` |

> **复审降级依据 (N3)**: 已核实 `confirmed_by_owner` 全库**write-only, 零读取消费者** (6 个脚本写, 无 promotion/champion/算力分配逻辑读它)。故是"潜伏 footgun"而非"已激活的亏损机制" — 但任何代码开始消费此 flag 前必须先拆章。

### Tier-A — 缺失维度 (立方体三轴之外的真金白银轴)

| # | 缺陷 (lens) | 最终 sev | 关系 | 一句话机制 | 锚点 |
|---|---|---|---|---|---|
| **N4** | 仓位 sizing 不在决策面, 等权 basket 把 IC 排序信息在出仓时丢弃 (missing-dim) | **HIGH** (复审降) | DEEPENS:D2 | 花力气验 IC 又不用 IC; 但负 cohort 内 rank-weight 也救不回 → 位于 base-edge 下游 | `portfolio_returnbacktest.py` 无 weight 参数 |
| **N5** | 退出/止损无一等地位, 唯一实现建在已删 `_latest` 表上; 持仓期内无 stop (missing-dim) | **HIGH** (复审降) | DEEPENS:D3 | cohort 下行只能扛到下个 rebalance; 直接决定能否守住 max_dd≥-20% 红线 | `profiles.py:35-37`; 引擎无 intra-holding stop |
| **N6** | 现金/空仓不是一等持仓状态, 缺连续 gross-exposure 维 (missing-dim) | **MEDIUM** | OVERLAP:D2 | D2 的 regime 是离散 long/flat; 真正缺的是 0%-100% 连续降仓 | 引擎 nav=1.0 锁满仓 |
| **N7** | 行业/概念**集中度**上限默认关闭, 而设计正主动按 segment 切片 → basket 天然单题材 (missing-dim) | **HIGH** | NEW | 真正该防的是更广的 **cohort-beta** (同形态×同规模×同换手共享题材 beta), 非仅 GICS 行业 | `portfolio_backtest.py:189` `industry_fn=None` |

### Tier-B — A股微观结构 / 可交易性 (回测 fill 假设系统性乐观)

| # | 缺陷 (lens) | 最终 sev | 关系 | 一句话机制 | 锚点 |
|---|---|---|---|---|---|
| **N8** | Tier-2 "含成本裁决"引擎完全无涨跌停板逻辑, close 无条件成交; 合约白纸黑字要求 limit-aware 一行未实现 (ashare-micro) | **CRITICAL** | NEW | reversal/突破 cohort T+1 极易一字板买不进; 能赚的连板买不到, 该亏的开板按 close 平滑 | `portfolio_returnbacktest.py:77/89` vs 合约 `strategy_validation_contract.md:13/66/83` |
| **N9** | `load_kline` 只取 close/high/low, 丢 open/volume/amount → 涨停判定/容量/冲击成本在**数据层**就不可计算 (ashare-micro) | **HIGH** | DEEPENS:D7 | `v_price_kline_qfq` 有 open/volume/amount, SELECT 阉割掉 | `experiment_l0_baseline.py:63` (已核实) |
| **N10** | 容量/冲击成本盲 + 固定 10bps 平 bps; 成本模型对自己最爱选的小盘×高换手格最不准 (ashare-micro / governance) | **HIGH** | DEEPENS:D7 | 100万本金小盘 top20 单边冲击可达数百 bps; -34.6% 仍是乐观值 | `portfolio_returnbacktest.py:79-84` flat `cost_bps` |
| **N11** | 停牌票"缺价=剔出等权篮"静默当干净退出, 复牌补跌/退市归零损失被抹掉 (ashare-micro) | **HIGH** | NEW | docstring 声称"含已退市防生存者偏差", 缺价剔出反而重新引入它 | `portfolio_returnbacktest.py:77/93-94` |
| **N12** | reversal 选股=跌幅最大=最可能正触跌停板, 选股+成交全链路无可交易过滤 (ashare-micro) | **HIGH** | DEEPENS:D5 | 想抄底的最深超跌票恰是流动性最差最可能一字闷杀的 | `experiment_tier2:135`; `features.py:84/91` |
| **N13** | 成本用对称 10bps, 缺 A股印花税卖方不对称; net -2.8% 实际可能 -6.4%~-21% (long-only) | **HIGH** | DEEPENS:D3 | 项目自有 `paper_sim_momentum.yaml` 真实栈 round-trip 27.3bps=1.36x, 大单 57bps=2.86x | `portfolio_returnbacktest.py:48` vs `paper_sim_momentum.yaml:59-66` |
| **N14** | T+1 用 t+1 **close** 成交而非 open/VWAP → 含当日全天走势 + 收盘集合竞价成交不到 (ashare-micro) | **MEDIUM** | DEEPENS:D3 | 对 5天半衰快衰减信号, 入场日占持有期比例大, forward-info 泄露被放大 | `portfolio_returnbacktest.py:59/89` |

### Tier-C — 统计有效性 / selection bias (多重比较去偏失效)

| # | 缺陷 (lens) | 最终 sev | 关系 | 一句话机制 | 锚点 |
|---|---|---|---|---|---|
| **N15** | 重叠 5天 label 使日度 IC 自相关被当独立观测 → DSR 的 n_obs 虚高约 5×, Gate3 显著门系统性偏松 (validation-survival) | **HIGH** (复审降) | NEW | 有效 N≈n_days/horizon; t 统计虚高√5≈2.24x; 但 DSR 坐 Tier-1 研究筛, 下游含成本 backtest 是终端 backstop | `oos_ic.py:159-161`; `deflated_sharpe.py:74` |
| **N16** | DSR 的 n_trials 只数单公式内参数组合, 跨公式/跨轴/跨实验试错累积完全不进 DSR (validation-survival) | **HIGH** | NEW | "逐层解锁"对 5形态×N子型×多轴反复试, DSR 漏掉 99% 试错; `fact_optuna_cumulative_trials` 仅 yaml 字符串无 DDL | `formula_param_search.py:94`; `optuna_config.yaml:102` enabled:false |
| **N17** | 立方体 cell 跨格 selection bias 治理只停文档; 9 子格 scan 实际只跑相对 IC 红线, 从不调 DSR (governance) | **HIGH** | DEEPENS:D7 | 9 选 1 best (+0.195) vs baseline +0.156 = +25%<+50% 红线 → 连相对红线都不触发 | `experiment_layered_segment_ic.py:124-128`; moth `claims.yaml:126-128` |
| **N18** | 逐层解锁"每轴选 OOS 增益最大者"可逆向为"全试报赢家": 同一 holdout 被每轮重复 query, 无 holdout 消耗预算 (validation-survival) | **MEDIUM** (复审降) | DEEPENS:D1 | 5 轮 (S1-S5) = 对同一 holdout 5 次 selection; 二阶放大器, 一阶仍是 cost/cohort 错配 | `segment_taxonomy_design:13-14,67` |
| **N19** | Bonferroni 分母 `N_CELLS_TRIED=30` hardcode 且低估真实搜索空间 (objective-metric) | **MEDIUM** (复审降) | NEW | 治理/正确性缺陷真实; 但本案 p_raw=0 → p_adj 对任意 N 恒=0, 裁决对分母不敏感; 真约束是 n_perm=500 给的 1/500 分辨率 | `experiment_ablation_gate2.py:35` (已核实) |

### Tier-D — 目标/度量错配 (用错指标/错窗口/错人群/错基准)

| # | 缺陷 (lens) | 最终 sev | 关系 | 一句话机制 | 锚点 |
|---|---|---|---|---|---|
| **N20** | IC 选格期 = Tier-2 评估期 (同 2023+ 窗), 绝对收益无真正样本外 (objective-metric) | **MEDIUM** (复审降) | DEEPENS:D4 | cell-which 自由度吃了整段 2023+ IC 又在同段评收益 = §4.5 in-sample fit 的 segment 级变体; cell 候选空间小, bias 有界 | `experiment_per_stage_ic.py:64` → `tier2 json` |
| **N21** | KPI 第3条对标 HS300, 但小盘 cohort 应对标中证1000/2000; 且 Tier-2 excess 项空缺 (long-only) | **MEDIUM** (复审降) | NEW | 用大盘基准评中小盘 = 把市值风格 beta 误记成 alpha; 当前 tier2 是全市场 run 且 KPI 诚实标 unknown | `goal.md` KPI 表; `tier2 json` note |
| **N22** | 可交易性从未被算成数: 全设计只测单 horizon=5 IC, 缺 IC-衰减曲线, 无法算半衰期 (signal-trad) | **HIGH** (复审降) | DEEPENS:D7 | "~5天半衰"是 backtest 副产品反推, 非 measured; IC(h) for h∈{1,2,3,5,10,20} 是几分钟零成本实验却从未跑 | 三脚本 `HORIZON=5` 写死 |
| **N23** | Tier-1 RankIC 快筛作"共享地板"本身是可交易性盲筛, 把成本预算挡在 Tier-2 之外是顺序错误 (signal-trad) | **MEDIUM** | DEEPENS:D4 | 第一道闸应携带可交易性语义 (半衰期/换手在成本可生存区间), 而非纯 RankIC | `l0_spec §0.1` |
| **N24** | IC_IR 被当 edge 可信度证据, 但它度量 rank 稳定性, 与 long-only 实现 sharpe 无数学桥 (objective-metric) | **MEDIUM** | DEEPENS:D1/OVERLAP:D5 | Stage1.5 IC_IR=0.895 同时对应 sharpe=-0.009; 高一致性可伴负实现收益 | `oos_ic.py:159-161` |
| **N25** | L0 标尺 +0.064 是全名次 spearman 地板, 组合只持 top-K 尾部 — 测错了人群 (objective-metric) | **MEDIUM** | DEEPENS:D7 | false-negative 缺陷: 全名次平/top-K 强的会被 Gate1 误杀永不获验证 (false-positive 方向已被 Tier-2 兜住) | `oos_ic.py:76-83` |
| **N26** | horizon 固定 5 天与 reversal 半衰期/T+1/换手未联立; 度量窗 ≠ 可交易窗 (objective-metric) | **MEDIUM** | DEEPENS:D7 | 度量 t 起算 5日 forward, 实际 T+1 起算周度持有; 二阶失真 | 三脚本 `HORIZON=5` |

### Tier-E — 数据优先级 (base 源选错)

| # | 缺陷 (lens) | 最终 sev | 关系 | 一句话机制 | 锚点 |
|---|---|---|---|---|---|
| **N27** | 数据菜单的 edge 排序是纯 a-priori "alpha假设", 从未 measured — 违 §1.2 红线 (data-priority) | **MEDIUM** (复审降) | NEW | flow 族实测兄弟 (moneyflow net_mf 0.0267≈0) 已证零增量, 菜单仍照脑补给 medium | `tushare_alpha_potential_menu.md` edge 列 |
| **N28** | 已验证为 REAL alpha 的慢衰减绝对源 (industry_beta/mcap decile/sector momentum) 已在库, L0/菜单当它不存在 (data-priority) | **MEDIUM** (复审降) | DEEPENS:D6 | 实测结论是 regime/pool-gate 有效 (DSR PASS) 但 paper_sim 绝对收益 FAIL (-16.1%) → 应作 **regime-gate 候选**纳入, 非当 selector base; 重用前须修 Pattern-10 NULL-gradient leakage | `project_index_changelog_archive #36` |
| **N29** | L0 "best-OOS-params 调到最优的纯价量地板"方法论错配 long-only: 把相对排序上限当所有 alpha 判负线, 系统性偏向短衰减 (data-priority) | **MEDIUM** (复审降) | DEEPENS:D6 | best-OOS-params 在 RankIC 维调最优 = 偏向短衰减反转; 慢衰减绝对源 5d RankIC<0.064 会被判"假增量"淘汰 | `l0_spec §1/§3.2` |

### Tier-F — 治理自动化 (死亡条款是空头支票)

| # | 缺陷 (lens) | 最终 sev | 关系 | 一句话机制 | 锚点 |
|---|---|---|---|---|---|
| **N30** | 立方体"感知死"(cell forward 连续 N 窗不兑现即冻结) + cohort 健康度降仓只在文档, 无 wired 检测器/cron (governance) | **MEDIUM** | DEEPENS:D2 | `mart_model_lifecycle` 只被展示读, `fact_experiment_verdict` 全 INSERT-only 无 reader; 死条款无法触发 | `multidim:51`; `daily_update.sh` 不读 verdict 表 |

## 2. 根因链 (第一性原理: 30 条缺陷 → 2 个根本设计错误)

> 把 N1-N30 + D1-D7 全部往回推, 它们**不是 30 个独立 bug**, 是两个根本设计错误的全息投影。

### 根因 R1 — 验证空间与盈利空间数学上正交 (用 A 空间的尺子量 B 空间的钱)

```
        验证空间 (流水线度量的)              盈利空间 (long-only 实际赚的)
        ─────────────────────              ──────────────────────────
        每日截面 spearman rank-IC    ⟂      top-K basket 含成本绝对 NAV
        (减掉了 cohort 绝对漂移)             (= cohort 绝对漂移 × sizing × 可成交)
              │                                      │
   N1 置换null保留每日收益分布           N8/N12 涨停买不进 → 能赚的买不到
   N15 DSR n_obs 虚高 5×               N10/N13 容量/印花税成本被低估
   N16/N17/N19 selection 去偏失效       N4 sizing 抹平 / N5 无 stop / N7 cohort-beta
   N24 IC_IR / N25 全名次 / N29 标尺      N11 停牌票静默剔出抹掉尾部损失
   N20 选格期=评估期 / N18 holdout snoop  N21 基准错配
              ↓                                      ↓
        Gate0-5 全绿                         gross -34.6% / net -2.8% KPI_FAIL
        N2/N3 发 REAL_EDGE 章                       (33σ 也救不了)
```

**第一性陈述**: 每日横截面 spearman 在数学上**减掉了 cohort 的水平 (绝对漂移)**。long-only 赚的恰恰是这个被减掉的水平。所以**只要验证闸的 null/度量建在 rank 上, 它在数学上就永远看不见 long-only 的钱** — 加再多统计闸 (Gate2/3/4/5)、再高的 σ (33σ) 都补不上这个维度缺失。这是**测度论级别的盲点, 不是参数没调好**。Phase B 的 "IC 真但不赚钱" 不是意外, 是这个正交性的必然产物。

> N1/N2/N24/N25/N29 全是 R1 在不同闸上的同一个病; N4/N5/N7/N8/N10/N11/N13 是盈利空间侧的各个泄漏口。修 R1 = 在每一道闸上把 rank-null 换成 **绝对收益 null** (block bootstrap 重生价格路径后看 long-only 含成本 NAV 符号), 而非 sharpe/rank。

### 根因 R2 — 把"信号"和"可交易的头寸"当成同一个东西 (引擎假设信号即头寸)

`run_return_backtest` 的假设链: 信号触发 → top-K 等权 → T+1 close 全额成交 → 持有到 rebalance → 缺价就剔出。**每一环都假设"算出来的信号能无摩擦变成实盘头寸"**:

| 假设链环节 | 现实 (A股 long-only) | 受影响缺陷 |
|---|---|---|
| top-K 等权 | sizing 是一等决策维 (N4) | N4 |
| T+1 close 全额成交 | 涨停买不进 (N8/N12)、用 open/VWAP 才真实 (N14)、容量限制 (N9/N10) | N8,N9,N10,N12,N14 |
| 持有到 rebalance | 需要主动 stop (N5)、连续降仓 (N6) | N5,N6 |
| 缺价剔出 | 停牌钱卡死、退市归零 (N11) | N11 |
| 平 10bps 对称成本 | 印花税卖方不对称 + 大单冲击 (N13) | N13 |

**第一性陈述**: 信号是一个数学对象 (排序), 头寸是一个受 A股微观结构 (涨跌停/T+1/停牌/流动性/印花税) 约束的物理对象。**回测把二者等同, 等于假设了一个不存在的无摩擦市场**, 所有"绝对收益"数字 (即便 R1 修好) 仍系统性乐观。

> **R1 是"度量空间错"(看不见钱), R2 是"成交假设错"(看见的钱是假的)**。两者叠加: R1 让流水线给不赚钱策略发证, R2 让即便修了 R1 算出的绝对收益也比实盘乐观。Phase B 的 -34.6% 同时被两者驱动 — 一阶是 R1 (cohort 绝对崩盘, IC 数学上看不见), 二阶是 R2 (容量/涨停/成本进一步放大)。

### R3 (派生) — 治理失败是 R1+R2 的元层投影

N2/N3/N17/N19/N30: 治理门 (`anomaly_verdict`/REAL_EDGE/moth/感知死) 全部**继承了 R1 的盲点** — 它们只会问 "IC 是真的吗", 从不问 "这能 long-only 赚到钱吗"。治理门不是独立缺陷, 是 R1 在自动化层的复制。修 R1 必须同步修治理门, 否则人工修好的判据会被自动门重新放行。

## 3. 扩展后的设计 (仍在条件化分层框架内, 修正 R1+R2)

> **不推翻用户核心思路**: "不同股票/状态行为不同 → 条件化分层" 是对的 (Phase B 也证实形态/换手有结构)。错的是操作化时把验证空间当盈利空间、把信号当头寸。下面修这两点。

### 3.1 立方体: 从 3 轴 → 5 轴, 且 sizing/exit 从 auxiliary 升一等

```
旧: Segment × Feature × Policy(选哪只)
新: Segment × Feature × Policy × Regime/Timing(该不该在场, D2) × Execution(怎么变成头寸)
                                  ─────────────────────         ──────────────────
                                  绝对方向门 (long/flat/         sizing(rank/IC/vol-wt) +
                                  defensive + 连续 gross         exit(time-stop=半衰期对齐/
                                  exposure 0-100%, N6)           trailing/cohort-regime, N5) +
                                                                 容量/集中度约束 (N7/N10)
```

- **Regime/Timing 轴 (第四轴, D2/N6)**: 一等决策维, 不是 Segment 子轴。long/flat/defensive **离散** + gross-exposure **连续** (0-100%, 由 cohort 健康度/波动率目标驱动)。"long-only 的钱主要来自在对的时候在场"。
- **Execution 轴 (第五轴, N4/N5/N7/N10)**: sizing (等权是 baseline **不是** default) + exit (time-stop 对齐半衰期 τ) + 容量闸 + cohort-beta 集中度上限。每个 cell 必须在多种 sizing/有无 stop 下跑, 报 sizing 敏感度 + 有/无 stop 两组 max_dd。
- **克制原则 (与 multidim §5 一致)**: Execution 轴的 sizing 子轴**在 base-edge 被证为正 (含成本绝对收益>0) 之后再激活** — 在负 cohort 内 rank-weight 救不回 (N4 复审洞察)。

### 3.2 验证范式反转: 给每道闸装"绝对收益 null", 拆"统计章 vs 转正章"

| 旧闸 (rank 空间) | 新增/改造 (绝对收益空间) | 修哪条 |
|---|---|---|
| Gate2 MC 截面置换 (只测 rank) | **+ 报告 cohort 当期实际平均 forward 收益**; 排序 p 值 **AND** cohort 绝对收益符号一起放行 | N1 |
| Gate3 DSR (sharpe, n_obs=n_days) | n_obs 改 **n_eff = n_days/horizon** (或 Newey-West); n_trials 读 **全局累积** (`fact_optuna_cumulative_trials` 须落地 DDL+writer) | N15,N16,N19 |
| Gate5 信号重生 (产出 Sharpe 分布) | **改/补为 long-only 含成本 NAV 符号判据**: top-K basket block bootstrap 重生**价格路径**后看含成本年化>0 的概率 | N1 |
| `anomaly_verdict` (单边 IC 太高) | **+ 对称低绝对收益门**: IC>0 但 basket 含成本收益≤0 → 标 `IC_POSITIVE_BUT_UNTRADABLE` (而非 CLEAN) | N2 |
| REAL_EDGE = p_adj<0.05 即 confirmed | **拆两级**: IC 置换显著 → `STAT_EDGE_CONFIRMED` (不置 confirmed_by_owner); confirmed_by_owner=1 **必须**由含成本 KPI 联立, `record_verdict` 加断言 | N3 |
| multi-cell scan (只跑相对 IC 红线) | **强制 DSR 收尾**: `deflated_sharpe_ratio(best, n_trials=实际cell数)`, p<0.95 标 `SELECTION_BIAS_SUSPECT`; moth 加断言 "max-over-cells 脚本无 DSR 调用 = BLOCK" | N17 |
| Tier-1 RankIC 共享地板 | **L0 双地板**: (1) RankIC necessary 快筛但**不作唯一淘汰**; (2) 新增**含成本 long-only top-K 绝对收益**地板 (sufficient)。入选 = 超 (1) OR 超 (2) | N23,N25,N29 |
| cell 选择期 = 评估期 | cell 选择与绝对收益评估**时间不相交** (2023-24 选 cell / 2025 holdout 评收益); holdout query 预算化 | N18,N20 |

### 3.3 可交易性前置筛 (Tier-1.5, R2 一阶修复): 在烧 Tier-2 算力前拦不可交易信号

```
Tier-1 (RankIC necessary)
   ↓
Tier-1.5 可交易性闸 [新增, 廉价]:
   半衰期 τ (IC(h) 衰减曲线 measured, N22) → 换手预算 → 含成本可活性 → 容量 (basket名义/ADV, N9/N10)
   τ 写入 formula metadata 作一等属性; 持有期 = f(τ) 不再固定 5
   净 edge 估计为负 → 直接死, 不进 Tier-2
   ↓
Tier-2 (含成本绝对收益 sufficient, R2 二阶修复):
   load_kline 补 open/volume/amount (N9)
   涨停一字板买不进剔出当期篮 + 跌停持仓顺延 (N8/N12)
   入场价改 t+1 open/VWAP 而非 close (N14)
   成本改 A股非对称栈 (买:佣金+滑点; 卖:+印花0.05%) + ADV 平方根冲击 (N13/N10)
   停牌票冻结仓位在最后有效价不重分权重, 退市归零计损 (N11)
   接 excluded_stocks/capacity preflight gate (落实合约 L21)
```

### 3.4 数据优先级 (Phase D, R1 源头修复)

| 优先级 | 源 | 依据 (measured-driven, 禁 a-priori) |
|---|---|---|
| **P0 (激活已在库)** | industry_beta_daily / mcap decile / sector momentum (N28) | 已实测 REAL (regime/pool-gate 有效, DSR PASS); **作 regime-gate 候选纳入第四轴验证矩阵, 非当 selector base**; 重用前先修 Pattern-10 NULL-gradient leakage |
| **P1 (慢衰减绝对源)** | 财务质量 / 资金流 trend / 筹码结构 (goal.md Phase D) | 绝对方向 + 慢衰减; 能驱动 cohort 整体上涨 (long-only 真 alpha) |
| **P-降** | flow 族 (moneyflow_dc/hsgt_top10/block_trade) | 实测兄弟 moneyflow net_mf 0.0267≈0 已证近零增量; 菜单 edge 列改 measured/unknown 两段, 禁脑补 medium 定 P0 (N27) |
| **base 重定义** | 裸 K 线 reversal 降级为 P0 慢衰减 base 之上的**短衰减卫星**, 不再当 base (N29/D6) | |

### 3.5 治理自动机制 (R3, 把死亡条款从文本变 wired job)

- **forward_reconciliation 检测器** (新 cron/手动 job): 读 `fact_experiment_verdict` 的 confirmed cell, 周期比对 OOS 预测 vs 实际 forward 收益; 连续 N 窗负兑现 → 写冻结状态 (N30 感知死从空头支票变可触发)。
- **cohort 健康度信号** (接 daily flow): cohort 近 N 日 drawdown/breadth → 运行时 regime 降仓输入 (N6 连续 gross-exposure 的驱动源)。
- **moth 断言扩展**: multi-cell scan 无 DSR 调用 = BLOCK (N17); confirmed_by_owner=1 无 cost-aware net_return = BLOCK (N3)。

## 4. 最小可执行下一步 (Phase D 第一刀)

> 原则: 一刀同时验证 R1 (度量空间) + R2 (成交假设), 廉价, 用现成数据, 立刻能改变 Phase B 裁决的可信度。**不是再跑一个 IC 实验** — 那只会再生产一个 R1 盲点产物。

**第一刀 = 给 Tier-2 引擎补 R2 三件套, 重跑 Phase B 已有的两个 cell, 看裁决符号是否翻转:**

| 步骤 | 动作 | 为什么是第一刀 | 成本 |
|---|---|---|---|
| **1** | `load_kline` 补 `SELECT open, volume, amount` (N9) | 涨停/容量/VWAP 全部依赖它, 是数据层总开关; `v_price_kline_qfq` 已有列, 一行 SQL | 分钟级 |
| **2** | `run_return_backtest` 加 3 个真实约束: (a) 入场价 close→t+1 open (N14); (b) 一字涨停 (open==high==low 且涨幅≥板幅, 走 `dim_price_limit_rules`) 剔出当期篮 (N8/N12); (c) 成本 close→A股非对称栈 (卖+印花0.05%, 读 `paper_sim_momentum.yaml`) (N13) | 全是现成数据/yaml, 把"假成交"换成"真成交"; 直接量化 R2 对 -34.6%/-2.8% 的影响幅度 | 半天 |
| **3** | 用改造后引擎重跑 Phase B 两个 cell (全市场 Stage1.5 + 小盘×高换手), 对比 gross/net/max_dd 变化 | 唯一能回答"修了 R2 后 Phase B 结论变多惨"的实弹; 大概率 net -2.8% → 更负 | 分钟级跑批 |
| **4** | `anomaly_verdict` 加对称门: IC>0 但含成本收益≤0 → `IC_POSITIVE_BUT_UNTRADABLE` (N2, R1 治理修复最小版) | 一个函数级改动, 立刻堵住"崩盘 cohort 拿绿章"的元层漏洞; R1 修复成本最低的入口 | 1-2 小时 |

**第一刀验收标准** (measured, 非估计): 改造后重跑, 若小盘×高换手 cell 的 net 从 -34.6% 区间进一步恶化 (涨停买不进 + 真实成本), 且全市场 Stage1.5 的 net -2.8% 翻更负 → **实弹证明 reversal long-only 在 A股结构性不可交易**, Phase D 应放弃裸 K 线 reversal base, 转 P0 (激活已在库慢衰减绝对源作 regime-gate) + P1 (慢衰减绝对源)。

> **为什么不先做立方体第四/五轴**: 那是 R1 的完整修复 (大), 但在没有一个被 R2-真实引擎验证过的"含成本绝对收益地板"之前, 加轴只会在 rank 空间里多生产几个 N1 盲点产物。**先用第一刀把"地板"造成真的 (含成本/含涨停/含真实成本), 再在其上加轴** — 与 multidim §5 "先找 base edge 再逐维解锁" 的克制原则一致。

## 5. 被对抗复审反驳/剔除的候选 (10 条, 防 selection bias 留痕)

> 这些是 critic 提出但 skeptic 读源码后反驳的, 多数因"已被 base 版 D1-D7 覆盖"或"项目代码其实已正确实现"。留痕证明本批判经过去伪。

- horizon 固定5天 (objective-metric 版): 实测 `REBALANCE_DAYS=5` 刻意 = horizon, 不存在窗口长度错配; 且已被 D7 + extension#2 覆盖 (注: N26 是另一角度——T+1 相位错配, 保留)。
- Gate2 Bonferroni 分母 (validation-survival 版): 多重比较去偏 gate 是 Gate3=DSR 不是 Gate2; 误认 gate 职责 (注: N19 从 objective-metric 角度保留)。
- expanding 早窗 OOS 噪声: `formula_param_search.py:94` 传的 n_observations 是全窗 n_days 非早窗, 断言被代码事实推翻。
- 可交易性评分框架缺位 (signal-trad 版总论): 已被 base 版 design_deficiencies 完整覆盖 (注: N22 半衰期未 measured 的具体点保留)。
- reversal 机械选下跌股是病根: 已被 D1+D5 覆盖, 且"机械病根"因果被项目实证推翻。
- T+1+难融券硬约束被完全跳过: T+1 在 `portfolio_returnbacktest.py` 已正确建模 (`entry_i=di+1`)。
- IC 选格通向小盘容量陷阱 (long-only 版): 已被 base 版 + N10 覆盖。
- 子篮过小不可分散: 已被 base 版含成本/容量/cohort 健康度覆盖。
- L0 horizon 冻死判死慢衰减源: 已被 D6 + N29 覆盖, 夸大为 CRITICAL 死锁。
- L0/菜单建在过期库存认知 (财务表已 sync): 时间线证伪 (菜单写于 06-14, 财务表 sync 在后)。
- tushare 慢衰减源被 PIT 风险压制: 财务/预测源在菜单里恰被标 "PIT 干净" 列第一优先, 因果链不成立。
