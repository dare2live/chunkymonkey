# ChunkyMonkey Goal

> 当前阶段契约 only。完成项 → `analysis/project_state_ledger.md`;运行态 →
> `SESSION_HANDOFF.md`。保持 < 165 行;item 完成就移 ledger 只留当前决策/blocker。

## Document Contract

| Document | Owns | Startup use |
|---|---|---|
| `docs/MASTER_TOPLEVEL_DESIGN.md` | 综合顶层设计 (数据→因子→策略→验证→KPI 全链骨架 + 纪律/工具 + 路线) | 先读它有全局 |
| `goal.md` | 当前阶段目标 / 优先级板 / genesis 法 / 路线 | Read first |
| `analysis/project_state_ledger.md` | 完成项 / 历史状态 / 证据 | `rg`/`tail` 查, 不全读 |
| `SESSION_HANDOFF.md` | 运行态恢复快照 | Context-only, 以 live gate 为准 |
| `docs/data_management_framework.md` | 数据层级框架 + 三原则 + 自动执法 (2026-06-14 立) | 数据管理权威 |
| `docs/chunkyctl_session_quickstart.md` | 启动流程 | 启动契约 |
| `docs/README.md` | docs 地图 + 权属 | 文档权威图 |

## 创世层 (项目法, 2026-06-14 地基-reset 后立; 重建的验收标尺)

**为何存在**: 把 A 股公开数据 (K线/财报/筹码/资金) 转成真金白银的 KPI 级回报 —— 不是论文, 不是数字游戏 (用户原话: "真金白银投入的")。

**死亡条款** (≤3, 陌生人可判):
1. **感知死** — forward 预测从不回填对账 / 异常高回测 (RankIC>0.3, sharpe>5, 年化>100%) 不查 leakage 就上线。
2. **判断死** — 阈值/权重/策略组合 hardcode 进代码而非 config (立方体退化成死代码)。
3. **谄媚死** — 报喜不报忧 (0 STRONG_BUY / Gate FAIL 不先讲) / 只调对你舒服的方向。

**判断法典种子** (人话 → 机器话, 自动执法):
| 人话 | 机器话 (moth/gate) |
|---|---|
| 每张表必声明所属 layer | `moth data-layer-integrity` [OK] |
| 不留 god-file / 万物互引 | `moth minimal-module-main-routers` + `no-new-godfile` [OK] |
| 删的层不被启动重建 | `services/schema_layer_filter` [OK] |
| 文档不准漂移 | `moth doc-drift` (本轮立) |
| 数字 measured 可复现 | `check_rule_compliance` + leakage gate |

## North-Star KPI (唯一 owner: 本文件)

| 指标 | 角色 | 目标 | 口径 |
|---|---|---|---|
| 年化收益 | **目标量** | >= +30% | 含成本 OOS paper_sim, 2023-01-03 起 100 万初始 |
| 最大回撤 | **特征化输出** (2026-06-16 用户改: "回撤已经没有限制了, 方案是看要想拿到最高胜率最大收益应该承受多大回撤") | 不设硬上限 | 报为"拿到最高胜率/收益所需承受的回撤"; 与年化/胜率成对呈现(return/win↔dd 前沿), 可接受性单独裁决, 非 pass/fail 门 |
| 超额 vs HS300 | 目标量 | > 0 | 真实基准 (小盘 cohort 须对标中证1000/2000, 非 HS300; 见缺陷 N21) |
| 月胜率 | **诊断量** | >= 55% | walk-forward OOS 月度分布; **单独不构成放行** |
| 胜率×盈亏比期望 | **诊断量** | > 0 | 单笔期望=胜率×平均盈−败率×平均亏 (positive_expectancy) |

**判断法典 C-WinReturn (2026-06-15 用户: "除了考核胜率还要考核收益率, 最终目的不是证明策略有效而是真能赚钱")**:
胜率是诊断量, 收益率+max_dd 是目标量 —— 胜率脱离盈亏比无意义 (40%×3:1 完胜 60%×0.5:1)。验收全 AND, 单项不放行。
**仓位管理 (sizing/exit/exposure) 是一等设计轴** (把 {edge,胜率,盈亏分布} 转成 {实现收益,回撤} 的传递函数), 非事后系数。
机器执法: `experiment_harness.kpi_verdict` + `tradability_verdict`; owner 全文 = `docs/strategy_validation_contract.md` 判断法典节。

**当前: `unknown`** —— 2026-06-14 模型/特征/寻优层全 reset, 参数寻优从零重做。不许引用 reset 前的旧数字。

## Current Phase: 双线 — 股票档案三层 (认识论地基, 当前主攻) → 主升浪猎手 (选股, 下游)

**2026-06-21 用户重定向**: 先**完成股票档案三层** (L1价格形态 done / L2每日盘面 / L3属性背景) = 认识论地基 (先看懂一只股才能选股), 三层各维度 config驱动+模块化+单测+审计 (PIT/口径/tushare源)。三层维度就绪后, 维度→主升浪猎手选股因子 (D因子矩阵, 档案 L2/L3 维度作 stage-conditional 因子; 基本面×机构跟随=典型组合)。详见 P1。

## Current Phase (下游): 干净地基上重建 alpha

**地基现状 (2026-06-14 reset 后, 2026-06-19 数据底座硬化)**: 数据管理框架已立 (8层声明式 `data_layers.yaml` + `data_layer_audit` PASS + `schema_layer_filter` 防重建 + moth 全pass); S1 基本面四件套 (forecast/express/income/fina_indicator) 回填完成; CI绿。**2026-06-19 硬化**: universe 身份真相源 = tushare `stock_basic` (退役 akshare dim_active 前缀猜); 非tushare孤儿源 6/6 SAFE_TO_DROP 退役 (~838k行, smartmoney 当前 ~87表); 排除股(北交所/三板)全库清+sync写入门防回潮。owner=`docs/data_management_framework.md` + `analysis/non_tushare_source_inventory_20260619.md`。

**Controller rule**: 主会话 owns 方向/真相源/共享文档/gate/staging/commit/风险写窗口。side-agent 只给有界证据, 非裁决。重大改动 (数据语义/策略/资金路径) 走对抗复审。

## Active Priority Board

> **[2026-06-24 架构蓝图驱动: 数据模块顶层重构 — 用户授权架构师排程; owner=`analysis/data_module_architecture_20260624.md`]**
> 用户决: **数据底座必须做好 + 模块化功能分区, 然后再搭建其他 (档B alpha/strategy)**。架构 = M1-M8 子模块 (按对血缘图一类操作切 owner) + **字典/总指挥 (M5 血缘路由中枢: 声明先行→派生对账→不可绕过闸 = codegraph+moth 融合到数据)** + 变量加工三态 (derived/vendor_precomputed/passthrough=未加工) + 阶段独立化门控前端 (§8) + DB 按写锁域分 (§9)。
> **实施顺序 (架构师定, 结合实际优化调整)**:
> - **阶段一 数据底座扎实**: [DONE] daily真跑验证(新holder/估值/qfii/org_holding sync全执行✓) + tdx 3表迁移归档(户数→tushare 284951行/2019+ deprecated; 增减持+关联→archived 保留唯一数据) · **§9 `reference.duckdb` 拆库 (用户选结构拆option B)**: **Stage A DONE** (migrate_reference_db.py 保真建 4核心表 dim_active/all_ever/listing_status/trading_calendar, 5件套验收 PASS, smartmoney未动可逆); **Stage B-E (get_conn ATTACH+view+写方repoint+sync_runner读reference+物删) = 高风险动get_conn中央工厂(33消费), 焦点session执行非长session尾鲁莽** (计划 owner=data_module_architecture §9.5) · **[NEXT 安全项] 2张额外冻结tdx表(fund_holding/shareholder_trade_tdx_b)triage**
> - **阶段二 模块化功能分区 (用户核心)**: §8 阶段独立化 — M1/M2/M3 独立命令(`chunkyctl pipeline acquire|clean|process`)+自带验收门(完整性+准确性)+`pipeline_stage_status`状态机; daily退化为门链编排非唯一入口; + T1 变量加工登记(feature_registry三态+`services/factors/`纯函数层+透传标untransformed)
> - **阶段三 字典+总指挥 (M5 血缘路由中枢)**: **[2026-06-26 T2 DONE — 用户拍板先造]** `services/lineage/` 缝合器(acquire自sync_registry 42源→表+PIT锚 / consume自确定性git-grep fan-in / 表自information_schema 6库 / layer自data_layers)+ `chunkyctl lineage build|impact|provenance|dead|show` + `data/lineage/graph.json`(472节点/1191边)+ `check_lineage_drift.py`(确定性连跑2次一致, safe_commit Step3.96 informational WARN, 硬闸排T4)+ 10单测。**killer: `lineage impact <table>` 删/迁前自动fan-in 替代手grep(根治本session tdx迁移反复手工漏判LIVE消费方的痛)**; dead检测19张已落库未用表。owner=analysis/data_lineage_routing_hub_design_20260624.md。余 · §8前端阶段控制面 · T3 transform/display字段级血缘 · T4 domain标签+闭环+drift硬闸
> - **阶段四 转场gate**: T0 Gap1 leakage收口(build_segment/signal_panel绕SERVE→0, task#55; 建档B alpha前堵漏)
> - **→ 然后 其他 (档B, 地基硬+leak堵后才上)**: 主升浪猎手D因子矩阵(#46-50) / dossier残 / 策略cube
> **建时已定(架构师授权)**: 血缘存储=JSON起(scale再→DuckDB) · 字段级深度=SERVE/特征层字段级+其余表级 · 前端图=阶段四后 · 刷新=commit drift门+手动(非daily自动) · 户数=archived保留(物删可逆性低暂缓, 要DB-lean再删)。
> **诚实flag (真金白银)**: T0 Gap1 是 alpha 钱路解锁; 用户选先做地基/模块化(平台做对再建上层)=合理战略, 但延后 alpha。最危险=血缘门假绿→图假真(M5门须红绿单测非裸grep)。

> **[2026-06-24 降序]** 以下 alpha/dossier (P1 主升浪猎手 / 股票档案残) = 用户所说"**其他**", **架构地基阶段一~四 done 后才推进** (用户: 数据底座做好+模块化再搭建其他)。dossier 三层已 DONE 故只剩残项; 主升浪 D 因子矩阵押后到地基硬+Gap1 leak 堵。下表保留为 backlog, 非当前主攻。

| P | Workstream | State | Next action |
|---|---|---|---|
| P0 | 文档同步 | goal/INDEX reset-rewrite | 立 `moth doc-drift` 固化 (机器对账, 防再漂; mythos §16) |
| P0 | 彻底清除污染期产物 | **完成** (2轮穷尽 sweep: DELETE 64 docs/脚本/config/验证结果 + scrub/作废头注~14 + DB 0残留; build_feature_panel BROKEN flag; fact_feature_panel 三处虚假active改诚实) + **沙盒机制根治** (sandbox/ gitignored 用完删 + moth exploration-isolated, 探索不再散进主代码) | — |
| **P1** | **主升浪猎手 (episode-first 结果倒推)** | **owner=`analysis/zhushenglang_hunter_plan_20260617.md` + `data_validation_backtest_plan_20260619.md`**; 5阶段 #46-50 (A0地基→B可买入率→C分层切阶段→**D因子矩阵 Optuna+Modal**→E转正门)。**F0地基 + B tradability + C分层切阶段 = done+对抗验证 (2026-06-19~20, 8 commit, 详 ledger 2026-06-19~20 条)**: 地基全连通 (fact_feature_panel 8.17M PIT / 正9070+hard-neg35198 入场点100%可查因子 / 鱼头鱼尾 fact_rally_stage 切分 panel-join100%); tradability GREEN (可买入率99.9%, 主升浪慢平滑非连板); Modal 端到端就绪 | **→ D 按阶段因子矩阵 (#49, 2026-06-20 用户纠偏重定向; 前期买点 detour 已全删)**: 前期 D-step 起涨点买点判别 (单/多因子二分类) **偏离计划 §0 诚实先验** (买点 AUC≈0.5=secondary, **主攻=鱼身延续+鱼尾出场+仓位+在场时机**) — 已全删重来。**正确路径 (用户确认)**: 在已切 `fact_rally_stage` (起涨630k/主升750k/顶部127k) 上**逐阶段验因子**, 优先级 **鱼尾出场 > 鱼身延续 > 鱼头买点**; **判据 = stage 窗内条件化持有 (持到信号反转非固定调仓) → 含成本绝对收益裁决, IC 仅快筛, 不看 AUC**; 消费者锚定: 鱼身因子=是否继续持有, 鱼尾因子=何时卖 (捕更多主升+避顶部回撤)。先 **CYQ 出货预警 (鱼尾, 0代码高价值: cyq_perf 单峰→多峰/px_pctile)** + 多头排列/资金净入 (鱼身)。owner=`data_validation_backtest_plan_20260619.md` §2.2-2.3 + `zhushenglang_hunter_plan_20260617.md` §0/Phase2-3。**execution 铁律 (非泄漏, 措辞校准)**: 回测出场只用实时可确认点 — 顶是事后 ±窗确认, 禁在事后顶当天卖 (label 可用未来=合法; 成交点用未来=不现实)。残: (e)/GT统一/winsorize/daily_basic2019(task_022abb42) 留后 |
| **P1** | **数据底座: universe真相源 + 非tushare退役** | **2026-06-19**: universe 身份真相源切 tushare stock_basic (双向bug根治: K线∩前缀漏入指数000300 + stale akshare漏真股 → 加身份交集); 非tushare源全盘点(akshare22/tdxhub18/aif10 13, owner=`analysis/non_tushare_source_inventory_20260619.md`); **6/6 SAFE_TO_DROP 退役 ~838k行**(逐表对抗验证0消费者+shared-writer删X留Y); 排除股全库清+写入门防回潮; ensemble污染孤儿退役; P0拉取(stock_basic/share_float/stk_holdernumber done, stk_factor_pro运行中) | 链完验P0落库 universe-clean; **KEEP_MIGRATE 5表**(aif10 valuation/peer/forecast→v3_picture + dividend_summary→scoring + price_kline→regime)走 M2/M3/M4 双轨先迁后删 |
| P1 | 数据底座研究 (chips/分位/资金) | cyq 实测与 tushare qfq 同复权坐标可用 (C0 FAIL=审计比错基准非数据错); 高积分高价值因子已排序 | cyq 解冻 2018 + 待评估高价值未拉项 |
| **P1 当前主攻** | **股票档案三层完成 = 认识论地基 (先看懂一只股才能选股; owner=`docs/stock_dossier_master_design.md` + `analysis/session_research_chips_formula_residual_20260621.md`)** | 三层架构定稿+前端浅色(claude_design配色)+入口收口(根路由旧v3→dossier, commit 56e79b14). **L1价格形态=DONE** (technical_states 9态/子态/多TF/涨停/上下文/单日K线/命名形态+RS). **L2每日盘面=DONE** (资金capital[大单净/量价背离/主力意图]+筹码chips[精细化:套牢盘/成本偏度/派发预警]+成交量vol[量比/量价配合]+RS+个股vs板块相对 done, 砍暗盘伪维度→量价背离). **L3属性背景=DONE** (机构十大流通股东**已切东财妙想 aif10**[2026-06-24 §4.3例外, ann_date/披露日PIT+退出行derive; tushare top10 季中滞后故弃]; **板块/概念sector_context DONE**; **基本面/估值/分析师预期fundamentals DONE**[ann_date PIT]; **事件催化events DONE**[龙虎榜+大宗+解禁float_date前瞻]; **市场regime DONE**[大盘趋势真MA斜率+涨停情绪净涨停/炸板率→牛/震荡/熊, 横切非单股stage-conditional最外层门]). **三层架构L1+L2+L3全维度完整**. 方法论: 公式=episode标注工具(用户纠正); GS(动态均线迭代+神奇九转+明暗盘红买绿卖, 中际旭创验证). | **完成清单 (逐维 实现+单测+审计 PIT/口径/tushare源; 判据=三层前端完整+单测全绿+doctor/moth绿)**: ①L2**筹码精细化** DONE(套牢盘/成本偏度/集中度变化/派发预警, ccce0268) ②L2 成交量独立 DONE(放量/缩量/量价配合, 8106fda0) ③L3**sector_context板块概念** DONE(申万行业regime vs HS300+个股vs板块RS复用+东财概念热度dc_index; sw_daily drain回填; sector_context.py 2纯函数+dossier 2 loaders+前端sectorCard+2单测) ④L3**基本面+估值+分析师预期** (income/fina_indicator/daily_basic/report_rc 数据全有, 模块化) ⑤L3 机构切tushare(top10_floatholders) + 事件催化(龙虎榜/大宗/解禁) ⑥L3 regime门(大盘/涨停情绪, 横切非单股因子). **基本面×机构跟随=stage-conditional选股策略**(机构买入事件内基本面排名, 避无条件截面R1) → 三层完成后接主升浪D |
| P2 | 深层解耦 backlog | kept routers 懒加载已删 services / 散落服务自建表 / god-file (framework doc §6) | rebuild 时按 layer 顺手解耦, moth 守不回潮; **不 big-bang** |

## 重建路线 (owner=`analysis/zhushenglang_hunter_plan_20260617.md` + MASTER §5)

主升浪=**有阶段的过程** (起涨鱼头/主升鱼身/顶部鱼尾), 逐阶段验因子。诚实先验: 个股单点买点 AUC≈0.5
(SOTA 三方印证) → **主攻鱼身延续/出场择时/仓位 (不依赖买点判别), 买点 secondary 作边际改善**。
D1 GT 已识别 (rally 9070 + macd 311291, universe 硬门 clean); 缺口 = 分层+切阶段。

- **Phase 0 地基补全** (审计 BLOCKER): build_feature_panel 重建(因子移 services 消除倒挂) / GT 标签拆
  entry-PIT vs outcome + fwd_complete / PIT 负样本生成器(现全正零负) / 死闸落地(audit_panel_leakage 升强制 + L2-bypass 真断言)。
- **Phase 1 分层+切阶段** (用户核心缺口): 9070 episode link 形态+申万L2+市值(横截面) + 切起涨/主升/顶部(内部) + 起涨点 T+1 可买入率体检(go/no-go)。
- **Phase 2 逐阶段验因子** (探索全在 sandbox/): 起涨进场/主升持仓/顶部出场 因子判别力, PIT + 含成本绝对收益(C-R1) + CPCV/PBO。
- **Phase 3 短路径 honest baseline** (奥卡姆优先): primary规则定方向→池内排名→出场→仓位→含成本 NAV vs 四基准(含 random-entry)。
- **Phase 4 meta-labeling + Optuna + 含成本 paper_sim → KPI**。
- 第一 go/no-go: Phase 1.3 可买入率 + Phase 3.2 含成本 NAV; 短路径无正期望 → 不投 Phase 4, 方向题交用户。

## Operating Reminders

- 主动用全套工具/skill (不等点名): `architect-controller` (架构/总指挥) · `mythos` (神话/创世) · `chunkymonkey-governance` (跑批前 grill) · moth (断言对账) · codegraph (耦合/依赖) · workflow (并发)。
- 第一性原理真相源: K线=可交易性 / 日历=日期 / config-table-service owner=业务规则。
- 删确定死的路径直接删, 不留注释/隐藏flag/兼容垫片; 但**不 big-bang 硬删紧耦合层** (按 layer 增量, moth 守)。
- commit 走 `bash scripts/safe_commit.sh`; 大改动数据语义/策略/资金路径走对抗复审。
- 历史详情 (reset 前 Strategy Portfolio / 数据底座 blocker / DB分区 / 旧 board / live gate) 见各 owner 文档 + ledger; 不在本文件保留。
