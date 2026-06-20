# 数据验证 + 回测方案 Refined Plan (主升浪猎手 · 可执行, 2026-06-19)

> 状态: live (接下来执行的数据验证+回测路线)。owner: 本文件。
> 来源: workflow wf_5034f0b3 (5 agents / 482k tokens: tushare字段gap / A股alpha经验 / 回测best-practice / 现状对账 + 综合), 主会话落档。
> 锚: 真金白银 / R1墙 (IC≠赚钱) / 口径铁律 (申万行业·东财概念·禁同花顺) / execution-aware / 买点 secondary。
> **实查校验 (写入前已验, 防陈旧)**: `backend/services/formula_engine/factors/` 不存在 (feature_panel BROKEN [confirmed]) · `block_bootstrap_return_null`(experiment_harness.py:147) 仅 test 引用未接裁决 (G1 [confirmed]) · CPCV/PBO 零实现 [confirmed] · `fact_optuna_cumulative_trials` 无 DDL 无 writer [confirmed] · `engine_execution_aware=PASS` live (合约 FAIL note 已陈旧 [confirmed])。
> **不重复** 已覆盖 (法典 PASS / execution-aware 引擎 / rally GT 9070 / universe 硬门 / tushare 口径决策); 本 plan 只补缺口。

## 第 0 部分 · 总纲 (一句话决策)

真 next ≠ 拉数据 ≠ 挖因子。**真 next = Phase 0 地基补全** (feature_panel 实证 BROKEN + GT 后验标签泄漏) — 不修这两条, 后续因子验证全建在泄漏地基 / 无特征载体上。数据增量 (P0/P1) 与回测阶梯收尾 (G1/G5/G6) 可与 Phase 0 **并行** (不同写锁域), 但因子矩阵执行必须等 Phase 0→1 完成。

执行总序: **[A0 地基止血] → [B 第一 go/no-go: 可买入率] → [并行: 数据 P0拉取 ∥ 回测阶梯收尾] → [C 分层+切阶段] → [D 按阶段因子矩阵] → [E CPCV/PBO 转正门]**。

## 第 1 部分 · 增量数据拉取优先级 (gap lens 收口)

> 纠偏: task spec 已拉清单=权威基线 (比 inventory doc 新)。`stk_factor_pro/sw_daily/share_float/stk_holdernumber` 均已注册 (stk_factor_pro 此刻 backfill)。THS 全系=SKIP (口径铁律)。估值分位/一致预期 ≠ 新拉 (见 KEEP_MIGRATE)。

### P0 — 立即拉 (已拉之外, 性价比+判别力最高)

| 接口 | 积分 | PIT锚 | 服务消费者 (哪阶段哪步) | 口径 | 超越已拉的边际价值 |
|---|---|---|---|---|---|
| **margin_detail** | 2000 | t-1 | 主升延续确认 + 顶部预警 (D2: Δ融资余额) | 中性 | 个股两融天天有 (vs 龙虎榜仅上榜日); Δ融资=杠杆确认/见顶, 正交价量 |
| **kpl_list** | 5000 | t-1 | 起涨(连板溢价) + 顶部(梯队瓦解=出场, D3) | 独立榜单 | 连板数/封单/竞价/炸板/涨停原因; limit_list_d 只有标记无梯队语义 |
| **stk_holdertrade** | 2000 | ann_date | 顶部出场 (D3: 高位减持负向 gate) | ann_date 干净 | 内部人行为维度, 已拉数据 0 覆盖 |
| **disclosure_date** | 500 | pre_date前瞻 | PIT 基础设施 (校准所有财报 ann_date 对齐) | — | 极低积分; 修补财报 JOIN 时点风险, PIT 工程依赖项优先 |

### P1 — 拉 (episode-first 验证排序)
hm_detail(+hm_list, 游资席位语义) · top10_floatholders(可抛压筹码, 分层慢变量) · **ccass_hold**(北向个股 2025-07 停披露, ccass 是仅存北向持股代理) · daily_info(regime) · pledge_stat(爆仓风控)。

### P2 / SKIP
P2: idx_factor_pro / stk_nineturn / stk_auction(R2 T+1 open校准需单独权限) / repurchase / 异动系。**ci_*(中信行业)=口径污染倾向 SKIP**。
SKIP: THS 全系(口径铁律) · weekly/monthly(daily可聚合) · 治理慢变量。

### KEEP_MIGRATE (aif10) → tushare 等价 **具体接口**
| aif10 表 | 等价 | 实证 |
|---|---|---|
| 估值分位 | **不新拉 — daily_basic 自算** | 已拉, pe/pb/ps/dv 滚动 rank=PIT 分位 (奥卡姆) |
| 一致预期 | **report_rc 聚合** | 已拉 23 字段逐分析师→ts_code×quarter 聚合=均值/分歧度/上调家数 |
| 同行估值 | **唯一真 gap** | 自建: 申万 index_member_all 取同业→daily_basic 组内分位; v3_picture serving live 消费→先迁后删 |
| 财务历史/holder_count | income/fina_indicator / stk_holdernumber (已拉; holder_count 已 RETIRED) | — |

**拉取纪律**: sync_registry 范式注册; 落库前单日实弹核证字段/grain/单页上限 (top_inst 1000整反例); 财报/事件类 `allow_empty_batch=false`+`min_rows_per_batch` (预收 37→7 散落反例); 验收查 period/day 覆盖完整性。拉完进 sandbox episode-first 验证。

## 第 2 部分 · 按阶段因子验证矩阵

> 铁律: Alpha158/360/WQ101 全高换手量价为主, **直接截面 long-only 必撞 R1**。正确用法=stage 窗内特征池, 靠 stage 框架天然降频 + 条件化持有 (持到信号反转非固定调仓)。

### 2.1 起涨段 (鱼头/进场) — buy-point AUC≈0.52, 做 meta-labeling secondary 过滤器, 不当独立 signal
缩量回踩+放量突破确认 (daily/stk_factor_pro/cyq_perf) · 二次突破洗盘后 · 主力/游资净入 (moneyflow_dc/top_inst/hm_detail待拉) · 板块L2热度 (moneyflow_ind_dc/sw_daily)。**最致命雷=一字板买不进** (突破票次日一字封死, 回测能买实盘买不到→R2剔篮+B可买入率体检)。

### 2.2 主升段 (鱼身/持仓延续) ← 主攻轴 #1 (R1墙下最可能赚钱)
时序动量/多头排列持续 (stk_factor_pro; **横截面动量≠时序动量, A股横截面常反转勿混**) · 量价配合(价升量稳) · 资金持续净入 (moneyflow_dc/hsgt/margin_detail待拉Δ融资) · **板块L2残差动量 sector-relative** (sw_daily; taxonomy切源桶变跨期不可比须戳version; sector fallback 99.978% leakage反例) · 筹码集中度(cyq_perf 低位单峰→上移)。

### 2.3 顶部段 (鱼尾/出场) ← 主攻轴 #2 (对收益/回撤杠杆 ≥ 买点)
高位放量滞涨/巨量阴线 · 量价背离(价涨量缩, 更早→出场分档) · **筹码松动(cyq_perf 单峰→多峰, 0代码高价值优先建)** · 资金/北向净流出(确认类滞后) · **股东减持/解禁/大宗折价**(block_trade/share_float/stk_holdernumber/stk_holdertrade待拉, PIT公告日) · 连板梯队瓦解(kpl_list待拉)。

### 2.4 分层轴 (横截面)
形态(rally episode link) × 申万L2(PIT 131桶) × 市值分位 × base长度 → 物化 `fact_rally_episode_strata`(未建)。**KPI基准错配: 小盘 cohort 对标中证1000/2000 非 HS300**。

### 2.5 Regime 轴 (一等验证矩阵轴)
daily_info(市场放量)+指数动量→bull/bear/range 分层报 OOS; gross_exposure 连续 0-100% 由 cohort 健康度驱动 (现仅离散 long/flat)。

### 2.6 R1墙陷阱清单 (IC好但不赚钱 — 必过含成本门才扣)
短期反转(1-5日) · 换手率波动(2024 IR2.64类) · 横截面动量排名 · 小盘流动性溢价。实证: Stage1.5×小盘×高换手 IC +0.195 但含成本 gross **-34.6%**。任何此类: C-R1(含成本绝对收益 sufficient/IC necessary-only)+C-R2 先过。

### 2.7 微结构排雷 (回测必建模)
一字板买不进 · T+1(出场次日open执行) · 停牌缺价(suspend_d) · 印花税单边+非对称成本 · 小盘流动性/冲击(daily_basic判容量)。

## 第 3 部分 · 回测方法完善清单 (best-practice gap, 排序=真金白银降序)

> 已达标 (别重做): walk-forward expanding_monthly+purge/embargo · DSR(Bailey-LdP) · 含成本绝对收益 sufficient gate(C-R1) · execution-aware 引擎 · KPI联合门(C-WinReturn) · trailing 多窗。
> 诚实: 文档(N1-N30)远超代码; CPCV/PBO 前端 mock(PBO=0.626)非真算; block-bootstrap null 写了测了**没接线**。

| # | Gap | 补法 (挂哪) | 成本 |
|---|---|---|---|
| **G1** | block-bootstrap 绝对收益 null 未接 gate (harness:147 仅 test 调) | phaseD_signal_eval 喂 seg_returns→block_bootstrap, p_le_zero<0.05 作转正必要条件; moth 升级 | 1-2h |
| **G5** | DSR n_obs 用 n_days 非 n_eff (5天重叠 label→t虚高√5) | n_observations 改 n_days/horizon (或 Newey-West HAC)+单测证伪 | 2-3h |
| **G6** | anomaly 门单边 (抓不到 IC正崩盘cohort); tradability_verdict 只print不block | IC_POSITIVE_BUT_UNTRADABLE 升硬 BLOCK + integrity check | 1-2h |
| **G9** | IC衰减/半衰期从未 measured (horizon=5 写死) | 零成本: oos_rank_ic over horizon grid→τ→rebalance=f(τ) | 2-3h |
| **G8** | 起涨可买入率体检缺位 (=第一 go/no-go) | execbacktest 输出 entry_fill_rate; 前置门 fill_rate<阈值=不可交易 | 0.5d |
| **G4** | 多重比较跨实验累积失效 (cumulative_trials 无DDL无writer); multi-cell不调DSR | 建DDL+writer记全局试错+DSR读累积+moth max-over-cells无DSR=BLOCK+BH-FDR | 1d |
| **G2** | CPCV 完全缺位 (唯一时序验证=单条walk-forward) | 新建 services/optimization/cpcv.py: C(N,k)组合复用 oos_ic purge+embargo→多条纯OOS路径 | 1-2d |
| **G3** | PBO 完全缺位 (前端mock) | CPCV 之上 CSCV: IS-best在OOS rank→PBO<0.5 转正 | 0.5d |
| **G11** | selection 选格期=评估期 (无真holdout) | cell选(2023-24)与评估(2025 holdout)不相交; nested-CV | 0.5d |
| **G7** | episode 条件化出场评估缺位 (固定N日调仓与北极星错配) | execbacktest 加 exit_fn 钩子(time-stop=半衰期/trailing/regime)+episode_eval 持仓期分布 | 2-3d |
| **G10** | regime 仅离散 long/flat | regime 升验证矩阵一等轴+gross_exposure 连续化 | 1d |
| **G12** | 感知死/forward reconciliation 空头支票 (verdict INSERT-only零reader) | forward_reconciliation job 读 confirmed cell 周期比对→连续负兑现冻结 | 1d |

## 第 4 部分 · 执行顺序 (锚 hunter plan Phase 0-4 + 诚实先验 + KPI 穿透)

**A0 地基止血 (BLOCKER, 一切之前; Phase 0)**:
1. build_feature_panel 重建 (formula_engine/factors/ 不存在): 4 因子函数从 git history 恢复 (PIT+单测), 不复活 experiment 脚本 (消除 builder→experiment 倒挂); 列扩 明暗筹价/申万L2残差/Qlib算子。
2. GT 标签时点拆分: rally GT 加 entry_signal_date(=bottom_date PIT)+fwd_complete; gain/peak/dd/bull 明确后验 outcome, 训练禁作 X (现全后验=泄漏)。
3. PIT 负样本生成器 (现全正零负): universe 内同期非-GT 点 + purge+embargo≥250日 + average-uniqueness 权重。
4. 死/虚闸落地: audit_panel_leakage 升强制; feature-layer-l2-bypass 真 moth 断言。

**B 第一 go/no-go (Phase 1.3, A0 后)**: 起涨点 T+1 可买入率体检 (9070 episode bottom+1 一字率/可买入率/扣成本分布)。判据: 若大面积买不进 → 主攻轴权重进一步压向鱼身延续+出场, 买点彻底降级。

**并行轨 (不同写锁域)**: 轨1 数据 P0 拉取→sandbox 验证; 轨2 回测阶梯收尾 G1+G5+G6 (各1-3h纯接线堵已知漏洞)+G9+G8 (廉价)。

**C 分层+切阶段 (Phase 1)**: 物化 fact_rally_episode_strata + fact_rally_stage。
**D 按阶段因子矩阵 (Phase 2-3)**: 优先级 = 鱼尾出场 > 鱼身延续 > 鱼头买点; 每因子 stage 窗内条件化持有→C-R1/C-R2 含成本裁决, IC 仅快筛。先 CYQ 出货预警(0代码高价值)+一致预期上修(report_rc已拉)。
**E 转正门 (Phase 4)**: G4→G2→G3→G11 (CPCV/PBO/nested-CV 一次落)→G7(episode-eval)→G10/G12。

**KPI 穿透铁律**: 任何 improve 必答 含 selection bias/leakage 吗? 真实 forward 期望? 含成本绝对收益(非IC非裸年化)? 小盘对标对基准(中证1000/2000)? 不穿透=噪音。联合门 AND: 年化≥30%/max_dd≥-20%/超额>0/月胜率≥55% (含成本 OOS)。

## 第 5 部分 · 文档悬空清理 (顺手)
- C1: strategy_validation_contract + design_ext2 的 "engine_execution_aware FAIL/Tier-2假裁决" note 已陈旧 (实跑 PASS); N8-N14 锚旧引擎 portfolio_returnbacktest.py (仍在 repo 未删)→post-fix audit 物删。
- C3: alpha_validation_program_spec + S0-S4 矩阵 = reset 遗留 (文件不存在但 ops skill §5 + task#9 仍引用)→悬空待清 (已被 zhushenglang D1-D4 取代)。
