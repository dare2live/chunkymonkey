# 主升浪猎手 执行方案 (post-reset 干净地基, 2026-06-17)

> 状态: live (north-star 执行计划)。**2026-06-20 大重优化** (买点detour全删+方法论锁定+条件化假设验证): §3.5 方法论锁定 / §4 优化后优先级 / §7.5 不可逆闸门 为最新前向纲领, §0-2 范式与地基不变。
> owner: 本文件 + goal.md Active Priority Board。
> 输入: 架构师审计 wf_4d9f4bbf + 用户阶段框架 + MASTER §5 监督式 + **本session用户连环纠偏定稿的完整思路**(形态分层选特征非选股/逐日回放无泄漏/含成本判据/先验假设再投Modal)。
> KPI (goal.md): 年化≥30% / max_dd≥-20% / 超额 HS300>0 / 月胜率≥55%, **含成本 OOS**。

## 0. 核心范式 (用户定, 不可偏离)

主升浪不是一个点, 是**有阶段的过程**。监督式结果倒推:

```
主升浪 episode (底→顶, 已识别 9070)  切三阶段:
  起涨段(鱼头) → 主升段(鱼身) → 顶部段(鱼尾)
逐阶段问: 哪些因子能识别"当前在这阶段 / 即将切到下阶段"?
  起涨: 进场因子   主升: 持仓/继续因子   顶部: 出场因子
```

**诚实先验 (写明, 决定打法)**: 个股单点"买点"二分类判别力 AUC≈0.5 (SOTA 三方印证: Qlib IC 0.03-0.05 / Avramov 成本+衰减三杀)。所以**主攻"鱼身延续+出场择时+仓位管理"(不依赖买点判别成功), 买点 secondary 作边际改善**。若含成本无正期望→早死早超生, 方向题交用户。

## 1. 干净地基 (已就位, 审计核实 PASS)

| 件 | 状态 |
|---|---|
| D1 GT | fact_rally_ground_truth 9070/4347股 + fact_macd_episode 311291/5197股, universe 硬门 0 排除股 |
| universe 真相源 | services/universe.py 单一计算点 + assert_universe_clean 运行时硬验 |
| K线 | price_kline_qfq_tushare 8.56M/2019+ (与 GT 对齐, 回测直读此源不走 tdxhub 视图) |
| D4 引擎 | portfolio_execbacktest (R2: T+1 open/一字板剔/非对称成本/容量/停牌) + oos_ic walk-forward + optuna 治理 |
| 法典闸 | C-R1(含成本绝对收益)/C-R2/C-WinReturn/C-LEAK, moth 30 PASS |
| 探索隔离 | sandbox/ (gitignored 用完删) + experiment_store (裁决留档) |

## 2. Phase 0 — 地基补全 (审计 BLOCKER, 动因子验证前必做)

| # | 交付 | 为什么 (审计 finding) |
|---|---|---|
| 0.1 | **build_feature_panel 重建** (用户定): 4 因子函数从 git history 恢复 → `backend/services/formula_engine/factors/` (PIT + 单测), 不复活 experiment 脚本 (消除 builder→experiment 倒挂); 因子列扩到覆盖 明暗筹价/申万L2残差动量/Qlib算子 (非只旧5因子); manifest active 转正 | BLOCKER: 当前 BROKEN, D2 因子载体死锚 |
| 0.2 | **GT 标签时点拆分**: rally GT 加 `entry_signal_date`(=bottom_date, PIT 入场锚) + `fwd_complete`(bottom ≤ 数据末-250日才入 OOS); 明确 gain/peak/dd/bull = 后验 outcome 字段, **训练禁作 X** | BLOCKER: GT 全后验定义, 误用 outcome 当特征=严重泄漏 |
| 0.3 | **PIT 负样本生成器**: universe 内同期非-GT 突破/横盘点 + purge+embargo≥250日 + average-uniqueness 权重 | BLOCKER: rally GT 现全正零负, 监督训练无正负边界 |
| 0.4 | **死/虚闸落地**: audit_panel_leakage 升强制 gate (moth + experiment_jobs); feature-layer-l2-bypass 做成真 moth 断言 (实验禁直读 L0 算因子) | SHOULD_FIX: 现"校验了不执行"死闸 + manifest 散文虚引用 |

## 3. Phase 1 — 分层 + 切阶段 = DONE (2026-06-20)

`fact_rally_episode_strata`(正9070: cap/base/申万 as-of) + `fact_rally_negative_strata`(负35198, 同口径) +
`fact_rally_stage`(鱼头/鱼身/鱼尾) + `fact_stock_technical_stage`(Weinstein) 全就位。

## 3.5 方法论锁定 (2026-06-20 大转折, 用户连环纠偏后定稿; owner=[[feedback-alpha-methodology-adherence]] + CLAUDE §1)

> 本 session 删了一整条"买点 ML detour"(偏离), 把用户完整思路对齐定稿。后续一切遵此:
1. **监督式结果倒推**: 已知赢家(9070 episode)反推 PIT 前兆; **禁信号正推**(造因子看 IC)。
2. **形态分层内选特征, 非选股, 非全市场通用因子**: 已验证 cap-条件化成立(净化后多因子 per-cap GBDT OOS, 跨cap重要性 Spearman 0.39; **小盘=筹码驱动 cyq_spread/winner_rate, 大盘=资金质量 turnover/mf/roe 无筹码**, 大盘vs微盘 ρ≈-0.05; experiment_store d4_stratum SUPPORT_LIGHT_CONDITIONAL)。
3. **判据 = 含成本逐日回放 OOS 绝对收益**; IC/AUC 仅必要快筛, **不是 AUC**。
4. **泄漏 = 输入特征用了决策点 t 之后数据**(非相对2025-06; label用未来合法, 时间隔离防的是另一回事)。逐日回放(每日只见≤t, T+1成交)结构上杜绝; 残余审查两条: 信号函数内部 PIT(无全样本统计/PIT复权/as-of行业)+ 参数冻结在回放起点前。
5. **算力**: 特征面板物化一次, 搜索时不重算(砍100x); 分层=算力+过拟合双控; 先廉价验假设→grill→本地搜→才 Modal(29/34反例)。
6. **不可逆闸**(见§7.5): 烧OOS/上实盘门要高(含成本KPI+DSR/PBO+按试错打折)+渐进上线+前向对账熔断。

## 4. 优化后优先级 (后续验证, 按"最快回答30%行不行"排序; 2026-06-20 重定)

> 决胜逻辑: 一切的真裁判 = 含成本逐日回放 OOS 是否达 KPI。P1 之前都是地基, P1 之后(Modal/扩维/上线)都条件化于 P1 过。

| 优先 | 交付 | 为什么这个序 |
|---|---|---|
| **P1 决胜 (含成本 honest baseline)** | (a) 确认/补 **leak-free 逐日回放引擎**(现 portfolio_execbacktest 是 rebalance 式, 验是否支持事件驱动逐日扫描或补之); (b) 搭 **cap-条件化 baseline 策略**: 简单 realizable 入场(买点 secondary 不求完美)→ 候选池 → **池内 cap-条件化多因子排名**(小盘筹码族/大盘资金质量族, 本session验出方向)→ 鱼尾出场 → 仓位 → **含成本 NAV**; (c) 对四基准: HS300/等权/不换股/**random-entry-same-exit**。**→ 第一个能回答"30%行不行"。可跑在当前少数因子上, 不必等全面板。** | KPI 是唯一真裁判; 先用最小料过这道闸, 别在没验证前建大基建 |
| **P2 地基 (模块+数据+config, 用户要求)** | 扩 `fact_feature_panel`: 全因子集(筹码集中度/胜率/换手/资金/板块相对/券商一致预期 report_rc)按各自 PIT 锚物化, **config 驱动**(因子清单+PIT锚进 yaml)+ **单一计算点**(import 不复制); 收 hardcode 桶阈值进 `stratification.yaml` + 修 `build_segment_panel` 双定义 | P1 用最小因子; P2 是工业化基础(给 P4 Modal 大搜索铺料 + 算力洞察落地) |
| **P3 严谨 + 扩维 (P1 有信号后)** | 多因子 ρ 置换检验(给 0.39 上显著性); 加 base/sector/**regime** 轴(若 cap 单轴不够 KPI); 鱼尾出场 winner_rate×放量组合(单用已证弱 REJECT_STANDALONE_WEAK) | 扩维成本高, 只在 P1 证明方向赚钱后投 |
| **P4 寻优 (P1 过含成本后, grill)** | Optuna 调参(walk-forward OOS, search space 非空 plan_validator, DSR/PBO/CPCV); 大规模才上 Modal | 不在无 edge 的策略上烧 Modal(29/34反例) |
| **P5 转正上线** | CPCV/PBO/nested-CV 转正门 → 渐进上线(paper→小仓→加仓)→ 前向对账熔断(感知死, 连续负兑现冻结) | 烧 OOS = 不可逆, 门高+渐进 |

## 5. 已沉淀的反例 (本session, 防重踩)
- 买点 ML detour: 把计划点名 secondary 的买点当主攻四步, 偏离"主攻鱼身延续+出场+仓位"(已全删)。
- 单因子 AUC 不能判多因子条件化命题(数学盲); 正负样本 year×base×fwd_complete 系统失配虚高 AUC 6-8pp(对抗审查抓, 净化才可信)。
- 鱼尾 winner_rate 单用弱/太早(早饱和提前79d触发); pivot/peak 当成交点=execution不现实(非泄漏, 措辞校准)。

## 7. 执行纪律 (贯穿)

- **探索全在 sandbox/** (scripts/sandbox.sh new), 用完删; 裁决→experiment_store; 真 edge→promote backend/services+单测。绝不散进主代码/文档。
- 每步过法典闸: universe 硬门 / leakage(输入≤t) / C-R1(含成本绝对收益) / C-R2(execution-aware) / C-WinReturn / DSR-PBO-CPCV。
- 异常高数字=leakage/confound 警报, 先查不兴奋(本session两次靠对抗审查抓出致命缺陷)。
- 阶段切分口径 + 负样本定义 + search space 跑前冻结 prereg(防挪门柱)。
- **重大判断(建系统/烧算力/上线)前开对抗审查 workflow**(本session 实证: 抓出 100x 单位bug + year×base confound, 都是手工漏的)。

## 7.5 不可逆闸门纪律 (新, 2026-06-20 用户确立)
- **烧 OOS**(把测试期折进训练重训): 之后再无独立历史检验, 重训模型的全程回测=in-sample 证明不了任何东西, 唯一裁判变实盘前向。门: 含成本 OOS 达标 + DSR/PBO 显著 + **按 OOS 试错次数打折**(试20个挑1个=最走运非最真)。
- **上实盘**: 渐进(paper→小仓→加仓)+ 前向对账(预测回填, 连续负兑现 kill-switch)+ 别拿同段累积实盘反复 tweak-recheck(每轮迭代跑前 pre-register)。

## 8. 顺序与依赖 (优化后)

```
Phase 0-1 (地基+分层) DONE  →  P1 含成本 honest baseline (cap-条件化, 决胜 go/no-go)
                                         │
                    含成本无正期望 ──────┴──── 含成本有 edge
                         ↓                          ↓
                  方向题交用户             P2 全因子面板(模块+config) ∥ P3 严谨+扩维
                                                    ↓
                                         P4 Optuna/Modal(grill) → P5 CPCV/PBO转正 → 渐进上线+前向对账
```
第一个 go/no-go = **P1 含成本 NAV vs 四基准**。无正期望 → 不投后续长 pipeline, 方向题交用户。
