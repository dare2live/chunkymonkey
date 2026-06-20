# 主升浪猎手 执行方案 (post-reset 干净地基, 2026-06-17)

> 状态: live (north-star 执行计划)。owner: 本文件 + goal.md Active Priority Board。
> 输入: 架构师审计 wf_4d9f4bbf (REVISE) + 用户阶段框架 (起涨/主升/顶部) + MASTER §5 监督式 episode-first。
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

## 3. Phase 1 — 分层 + 切阶段 (用户核心缺口: 识别了但没分层)

| # | 交付 |
|---|---|
| 1.1 | **横截面分层**: 9070 episode 打标 — link segment_panel(stage/range_pos/MACD@bottom) + 申万L2板块(PIT) + 市值分位 + base长度。物化 `fact_rally_episode_strata` |
| 1.2 | **内部阶段切分**: 每 episode(底→顶)切 起涨(0-20% of move)/主升(20-80%)/顶部(80-100%) 或按 form 转换点。物化 `fact_rally_stage` (episode×stage×date区间, PIT) |
| 1.3 | **起涨点 T+1 可买入率体检** (廉价 go/no-go): 9070 bottom+1 一字涨停率/可买入率/扣成本收益分布 + 月度可建仓覆盖率 → 判 episode 结构是否适配月胜率 KPI (审计 skeptic c) |

## 4. Phase 2 — 逐阶段因子验证 (D2, 探索全在 sandbox/)

每阶段在分层 GT 上验"哪些因子有判别力" (vs PIT 负样本):

| 阶段 | 验什么因子 | 判据 |
|---|---|---|
| 起涨(鱼头) | 缩量回踩/明暗共识入/winner低位/板块L2热度/二次突破 | 判别"成主升浪 vs 假突破" |
| 主升(鱼身) | 多头排列持续/资金持续净入/量价配合/CNIR残差动量 | 判别"主升继续 vs 转顶" |
| 顶部(鱼尾) | 放量滞涨/winner高位/明买暗卖/CYQ出货预警(px_pctile) | 判别"接近顶部出场" |

铁律: 特征严格 ≤ decision date (PIT); IC=necessary 快筛 / **含成本绝对收益=sufficient (C-R1)**; CPCV+PBO+nested-CV 防过拟合 (审计: 当前 0 实现, Phase 2 前补); record_verdict 留 experiment_store。

## 5. Phase 3 — 短路径 honest baseline 优先 (奥卡姆, 审计强推, 不等买点完美)

| # | 交付 |
|---|---|
| 3.1 | primary 规则(周线多头+二次突破回调确认, PIT-clean)定方向 → 确认上涨候选池 → 池内多因子排名(Phase 2 验出的有效因子) → 出场择时(鱼尾因子) → 仓位管理 → **含成本组合 NAV** |
| 3.2 | 对四基准归因: HS300 / 等权 / 不换股 / **random-entry-same-exit**(入场 alpha 真对照, 审计 skeptic) |

→ **第一个能回答"30% 行不行"的 honest baseline**。

## 6. Phase 4 — meta-labeling + 寻优 + KPI (D3/D4)

| # | 交付 |
|---|---|
| 4.1 | meta-labeling: primary 定方向 + secondary ML(Phase 2 因子)判 true/false breakout, 指标=precision/含成本Sharpe 非 AUC |
| 4.2 | Optuna 调参 (walk-forward OOS, search space 非空 plan_validator, DSR/PBO/CPCV) |
| 4.3 | 含成本 paper_sim (T+1/涨停/容量) → KPI 验收 (年化/max_dd/超额/月胜率) |

## 7. 执行纪律 (贯穿)

- **探索全在 sandbox/** (scripts/sandbox.sh new), 用完删; 裁决→experiment_store; 真 edge→promote backend/services+单测。绝不散进主代码/文档。
- 每步过法典闸: universe 硬门 / leakage / C-R1(含成本绝对收益) / C-R2(execution-aware) / C-WinReturn / DSR-PBO-CPCV。
- 异常高数字=leakage 警报 (§4.2), 先查不兴奋。
- 阶段切分口径 (% of move vs form 转换) + 负样本定义 跑前冻结 prereg (防挪门柱)。

## 8. 顺序与依赖

```
Phase 0 (地基补全 BLOCKER)  →  Phase 1 (分层+切阶段)  →  Phase 2 (逐阶段验因子, sandbox)
                                                              ↓
        Phase 3 (短路径 honest baseline, 奥卡姆优先) ←────────┘
                                ↓
        Phase 4 (meta-labeling + Optuna + 含成本 paper_sim → KPI)
```

第一个 go/no-go: Phase 1.3 (可买入率体检) + Phase 3.2 (短路径含成本 NAV vs 基准)。短路径含成本无正期望 → 不投 Phase 4 长 pipeline, 方向题交用户。
