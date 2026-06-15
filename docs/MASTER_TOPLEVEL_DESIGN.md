# ChunkyMonkey 综合顶层设计 (Master Top-Level Design)

> 2026-06-14 立。本会话全部设计 + 用户对公式/数据的核心想法 + 重构方案的**系统性综合**。
> 定位: 项目最高层设计真相源, 串起 数据→因子→策略→验证→KPI 全链 + 纪律/工具 + 路线。
> 各层细节 owner 见文末文档体系; 本文件只给**骨架 + 决策 + 为何**, 不复制细节 (防双真相源)。
> 固化: 本设计的操作知识 (坑/工具调度/纪律) 沉淀进项目专用 skill `chunkymonkey-ops` (后续开发持续用)。
>
> **状态 (2026-06-15 P0-P3 固化后部分 superseded)**: 本文 2026-06-14 骨架在三处已演进, 冲突以下列 owner 为准 (本文旧措辞保留作演进记录):
> ① **验证范式** (§6): IC 降级为 necessary 快筛, **含成本 execution-aware backtest 绝对收益 = sufficient gate**; 选 cell/因子按含成本绝对收益**不按 IC** (根因 R1)。
> ② **base 性质** (§4/§5): 裸 K 线 reversal long-only 经 P3 实弹判为 A 股**结构性不可交易** → base 转**慢衰减绝对源**。
> ③ **立方体** (§5): 3 轴 → **5 轴** (+ Regime/Timing 绝对方向门 + Execution sizing/exit/容量)。
> owner: 判断法典=`docs/strategy_validation_contract.md` · 缺陷体系 N1-N30+根因 R1/R2=`analysis/design_deficiencies_extension2_20260615.md` · Phase B 实弹裁决+Phase D 方向=`analysis/p3_execution_aware_verdict_20260615.md`。

---

## 0. 北极星 (一句话)
把 A 股公开数据 (K线/财报/筹码/资金) 转成**真金白银的 KPI 级回报** —— 不是论文不是数字游戏。
KPI (owner=goal.md): 年化≥30% / max_dd≥-20% / 超额HS300>0 / 月胜率≥55% (含成本 OOS paper_sim, 2023起)。当前=unknown。

## 1. 创世法 (不可变, owner=goal.md)
**死亡条款** (陌生人可判): ① 感知死=forward不回填对账/异常高回测(RankIC>0.3/sharpe>5/年化>100%)不查leakage上线;
② 判断死=阈值/权重/策略组合 hardcode 进代码非 config; ③ 谄媚死=报喜不报忧/只调舒服方向。
**判断法典** (人话→机器话): 每表声明layer→`moth data-layer-integrity`; 不留god-file→`minimal-module-*`;
删层不被重建→`schema_layer_filter`+`legacy-flow-no-pollution`; 数字measured可复现→`check_rule_compliance`+leakage gate。

## 2. 总架构 (8层数据 × 4流程域, **data_layers.yaml 唯一驱动**)
```
真相源: data_layers.yaml (表→layer, 85活/56wiped) ── 流程/config 读它, 不自带表清单 (反转, 防重建循环)
─ 数据层 ───────────────────────────────────────────────────────
 L0_source     tushare_raw + market K线          ← sync_runner (registry 驱动)
 L1_foundation dim/财报PIT/十大股东/LHB/机构      ← from L0
 L1k_kline_int technical_stage/macd (纯OHLCV)     ← from v_price_kline_qfq
 display       档案展示 (UI serving)              ← from L1
 infra         watermark/audit/gate/deletion
─ 实验层 (不进 daily_update, 走 alpha 验证程序) ──────────────────
 L2_feature / L3_model / L4_experiment           ← experiment_store, S0-S4, 参数寻优重做
─ 4 流程域 (边界清晰, 各碰各 layer) ──────────────────────────────
 daily-update(L0/L1/L1k/snapshot+retention) · alpha-validation(L2-L4) · serving(display) · infra(治理)
```
核心反转 (重构): daily_update 不再硬编步骤 (自当真相源致删层留死调用); 读 data_layers active 表自动跳 wiped。

## 3. 数据层 (tushare 主源; 验证优先于抓取)
- **主源 tushare** (171/239 可用, 已抓 29); tdxhub/miaoxiang 备援; akshare 淘汰中。
- **数据菜单** (owner=tushare_alpha_potential_menu): 137 A股未抓接口评估, **无 high edge** (诚实)。真缺口因子族:
  现金流质量(cashflow)/机构大宗折价(block_trade)/北向per-stock(ccass/hsgt_top10)/事件日历/杠杆(margin)。
- **用户 focus (技术类: 资金流/筹码)**: 个股资金流 moneyflow_dc(低PIT) / 筹码 cyq_chips(重建winner_rate) /
  北向 hsgt_top10+ccass / 大宗 block_trade / 龙虎 hm_detail(10000档, 高PIT须t-1)。
- **口径铁律**: 行业/概念资金流必须 flow vendor = membership vendor (东财链自洽 dc_member+moneyflow_ind_dc;
  申万只做中性化; **禁同花顺第三套**)。混用=§4.5 sector leakage 同源。
- **原则**: 不为没证明的数据建保鲜管道 (architect rule6); 验出 alpha 才抓 (超 L0 标尺 +0.064 才转正)。

## 4. 因子层 (因子来源 = 验证矩阵的列)
> **状态: 已偏离 (2026-06-15 P3 实弹)**。裸 K 线 reversal long-only 含成本 execution-aware 实测年化 -14.06%~-34.69% / max_dd -57%~-81% (IC_POSITIVE_BUT_UNTRADABLE) = A 股结构性不可交易; +0.064 IC 标尺仅作 necessary 快筛, **不再是选层/增量判据**。base 转**慢衰减绝对源** (见 §5/§10, owner=p3_execution_aware_verdict)。
- **裸K线 L0** (已建, 降级为 base 候选): reversal/macd/ma/turtle, 标尺 reversal OOS RankIC +0.064 (= R1 的 IC⟂盈利活样本)。
- **Alpha158** (旧panel已删PIT不可信, 验证时干净重算): 64 OHLCV 因子; 入选判据改"含成本 execution-aware backtest 绝对收益", 不再"超+0.064 IC"。
- **慢衰减绝对源 (Phase D P0/P1)**: 财务质量/资金流 trend/景气/筹码结构 (已在库 daily_basic/moneyflow/cyq_perf); 绝对方向+慢衰减→低换手→成本可 survive。

## 5. 策略层 (用户核心想法: **条件化, 非万能公式**)
> L0 市场级 reversal 仅 +0.064, **因把所有形态平均了** (低位用reversal/上升用动量互相抵消)。正解=条件化。
**策略立方体** cell = (Segment 形态 × Feature 因子 × Policy 公式 × **Regime/Timing** × **Execution**), 5 轴 (2026-06-15 扩展, owner=design_deficiencies_extension2 §3.1 N4/N5/N6 + 法典 C-WinReturn; 旧 owner conditional_stage_strategy_design/multidim 已加偏离头注):
- **Segment 形态** (用户): 横盘/低位/上升通道/下跌通道/高位 = `technical_stage` (Weinstein 5阶段) + MACD零轴/历史分位(PIT expanding)细分轴。
- **Policy 公式**: 20公式+主升浪猎手, 找"哪个公式适配哪个形态"。
- **Feature 因子**: 换手/筹码/资金流/Alpha158 在 cell 内增 alpha (主辅: 主公式出仓, 因子只调制)。
- **Regime/Timing 轴 (第四, 绝对方向门)**: long/flat/defensive 离散 + 连续 gross-exposure 0-100% (cohort 健康度/regime 驱动)。long-only 的钱主要来自"在对的时候在场" (R1: cohort 绝对漂移 = 被 IC 减掉的水平)。
- **Execution 轴 (第五, 一等非事后系数)**: sizing(equal/rank/inverse_vol) + exit(time-stop=半衰期/trailing) + 容量/集中度约束 (C-WinReturn: 仓位管理是把 edge 转实现收益+回撤的传递函数)。
- **第一实验** (已跑, 部分推翻): per-stage L0 IC — 用户低位假设被数据否(Stage1≈0), 赢家 Stage1.5 突破中 +0.156, **但含成本 execution-aware 仍年化 -14% (IC_POSITIVE_BUT_UNTRADABLE)** → 解锁/选 cell 按含成本绝对收益不按 IC (owner=p3 裁决)。
- **治理**: 维度爆炸用 DSR/PBO 压 (n_trials 如实计全 cell, 非 hardcode); **逐维解锁** (先证含成本绝对收益>0 再加维); 数据驱动 regime 聚类作补充。
- **宇宙**: `universe.py` 排除 ST/退市/三板/北交所 (前缀非60/00/30/68 + ST名 + 退市no-trade)。

## 6. 验证层 (可靠性阶梯, 防过拟合第一约束; owner=model_validation_reliability_design)
> **状态: 验证范式 R1 修正 (2026-06-15, owner=strategy_validation_contract 判断法典)**。Gate1-5 的 null 全建在 rank/sharpe 空间, **数学上对 long-only 绝对收益盲** (N1: 截面 spearman/置换保留每日收益分布, 崩盘 cohort 可全数过闸; 33σ 仍亏)。修正: **IC=necessary 快筛(降级)**, **含成本 execution-aware backtest 绝对收益=sufficient gate(升级)**; 选 cell 按绝对收益不按 IC。
> 阶梯加**绝对收益门 (R1)**: `tradability_verdict`(IC>0 且含成本净≤0→IC_POSITIVE_BUT_UNTRADABLE) + `kpi_verdict`(年化 AND max_dd AND 胜率×盈亏比期望, C-WinReturn) + `block_bootstrap_return_null`(NAV 符号 null, 与 rank 置换正交)。两级转正 (N3): Gate2 排序显著=STAT_EDGE_CONFIRMED 非 money; confirmed_by_owner 须含成本证据。执法 gate=`check_strategy_validation_integrity` 4 维 + moth `validation-*`。
```
Gate0 PIT-clean (3门固化: PIT行为门/embargo切分/异常红线; pit_guard)
Gate1 walk-forward OOS RankIC > +0.064  ← necessary 快筛 (降级, 不再是升级判据)
Gate2 MC 截面置换 → STAT_EDGE_CONFIRMED (排序显著, 非 money; + 报 cohort/top-K 绝对 forward, N1)
Gate3 DSR (Bailey-LdP): best 在 N trials 下显著 (n_trials 实计, n_eff=n_days/horizon 重叠校正)
Gate4 PBO (CSCV): IS-best 在 OOS 仍 best
Gate5 含成本 execution-aware backtest 绝对收益 (sufficient gate, portfolio_execbacktest):
      tradability_verdict + kpi_verdict + block_bootstrap_return_null → 含成本年化/max_dd/胜率×盈亏比联合
   → 全过 + 含成本绝对收益>0 才进实盘候选
```
纠错 (N1): feature↔label 截面置换 null 保留每日收益分布, **对 cohort 绝对涨跌恒不变, 不能单独作终验**; 终验=含成本 execution-aware backtest 绝对收益 (NAV 符号 block bootstrap), 不是策略 sharpe (sharpe 仍 rank/风险调整量)。

## 7. 实验台 (alpha 验证程序 S0-S4, owner=alpha_validation_program_spec)
S0 实验台✓(experiment_store 4留档表 + consumer_alpha family + 执行器) → S1 数据✓ → S2 harness →
S3 逐数据验证 → S4 判决。留档链: pre-reg(冻结判据 prereg_hash) → verdict JSON → DB → ledger。隔离 live 防污染。

## 8. 纪律与工具调度 (固化进 skill `chunkymonkey-ops`)
| 工具 | 何时用 | 守什么 |
|---|---|---|
| **moth** `moth assert --repo .` | commit前/接手/重大改动 | claims-vs-reality 弹仓 (layer/godfile/leakage gate/legacy-flow/防过拟合) |
| **codegraph** sync/query | substantial change (新service/LOC>50/拆模块/改JOIN) | 耦合/依赖/影响面 |
| **gate 脚本** | commit + 重构验收 | data_layer_audit / legacy-flow-no-pollution / check_rule_compliance |
| **grill** (chunkymonkey-governance skill + plan_grill_gate hook) | 跑批/Optuna寻优/新模块前 | 跑了有没有用 (search space非空/前提验/成本) |
| **Workflow** (ultracode) | 全面 audit/对抗验证/fan-out | 多视角证伪 (本会话: 对抗泄漏审计抓 embargo 死闸) |
| **safe_commit.sh** | 每次 commit | hook 矩阵 (INDEX同步/rule/no-emoji/self-check/post-fix-audit) |
| **subagent** (Explore/general) | 跨文件 audit/research | 主对话只带结论 |
高频 commit+push; 高风险动作(删/force)先问; codex 复审非阻塞但 CRITICAL leakage 完全接受。

## 9. 重构 (老流程污染清除, owner=system_refactor_architecture)
教训 (DB 9.1G 根因): 删层必删caller / 删schema留caller=静默degraded / append-only无retention=膨胀 /
孤儿config引用(238处) / 散落DDL绕schema门(alpha158循环根) / daily_update自当真相源。
重构 (gate红→绿验收): 退役老daily_update → 清238孤儿引用 → 3表加retention → 散落DDL包layer-gate → bloat回收。

## 10. 路线图 (当前态 → KPI)
```
[✓] 地基reset (85表/2.5G) + 8层框架 + genesis法
[✓] S0 实验台 + L0 Tier-1 标尺 (reversal +0.064) + 寻参治理层(DSR)
[✓] 数据菜单评估 + 可靠性/条件化策略 设计 + 重构架构设计 + 教训工具化(gate)
[✓] per-stage L0 IC (条件化成立, 赢家 Stage1.5 +0.156; 用户低位假设被否)
[✓] R1/R2/C-WinReturn 法典工具化 (P0) + execution-aware 引擎删重建 (P1) + 阶梯绝对收益门 (P2)
[✓] Tier-2 实弹重裁决 (P3): 裸 K 线 reversal long-only **结构性不可交易** (含成本 -14%~-35%, IC_POSITIVE_BUT_UNTRADABLE)
[ ] **Phase D (当前)**: 转慢衰减绝对源 (财务质量/资金流trend/景气/筹码, 已在库), 选 cell 按含成本 execution-aware 绝对收益不按 IC
[ ] 策略立方体 5 轴逐维解锁 → 含成本 paper_sim → KPI 达标 → 实盘候选
```

## 11. 文档体系 (防"到处指引到处找"; 整合后)
- **本文件** = 顶层骨架 (读它先有全局)。**goal.md** = 当前阶段+genesis法+KPI。**PROJECT_INDEX.md** = 活索引(表/模块/脚本)。
- **owner 细节 doc** (本文件引用, 不复制): data_management_framework(数据层) / strategy_validation_contract(PIT/Optuna + **判断法典 R1/R2/C-WinReturn**) /
  PROJECT_CONSTITUTION(三原则) / 各 analysis 设计 doc (alpha验证/L0/可靠性/条件化策略/重构架构/数据菜单, 均已加 2026-06-15 偏离头注)。
- **2026-06-15 现行策略真相源** (P0-P3 固化, 上述旧设计 doc 冲突以此为准): `analysis/design_deficiencies_extension2_20260615.md` (缺陷体系 N1-N30 + 根因 R1/R2 + 5 轴立方体 + 验证范式反转) · `analysis/p3_execution_aware_verdict_20260615.md` (Phase B 含成本实弹裁决 + Phase D 方向) · `analysis/design_deficiencies_and_extension_20260615.md` (base 版 D1-D7)。
- **skill `chunkymonkey-ops`** = 操作知识固化 (坑/工具调度/纪律/反例), 后续开发持续 invoke。
- **退役 (2026-06-15 A6 执行)**: git rm 5 已偏离 analysis (first_principles_diagnosis/chunkymonkey_architecture_audit/multi_wave_strategy_300616/system_architecture_audit_20260521/implementation_plan_20260611) + architecture_reform_context (本设计已覆盖); 移 zhushenglang 研究日志 docs/->analysis/ (北极星证据归 evidence 目录)。docs 12->10。
- **CLAUDE.md** = 瘦身到会话红线 + 指针到本文件/skill (不重复细节)。
