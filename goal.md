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
| 每张表必声明所属 layer | `moth data-layer-integrity` ✓ |
| 不留 god-file / 万物互引 | `moth minimal-module-main-routers` + `no-new-godfile` ✓ |
| 删的层不被启动重建 | `services/schema_layer_filter` ✓ |
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

## Current Phase: 干净地基上重建 alpha

**地基现状 (2026-06-14 reset 后, 2026-06-19 数据底座硬化)**: 数据管理框架已立 (8层声明式 `data_layers.yaml` + `data_layer_audit` PASS + `schema_layer_filter` 防重建 + moth 全pass); S1 基本面四件套 (forecast/express/income/fina_indicator) 回填完成; CI绿。**2026-06-19 硬化**: universe 身份真相源 = tushare `stock_basic` (退役 akshare dim_active 前缀猜); 非tushare孤儿源 6/6 SAFE_TO_DROP 退役 (~838k行, smartmoney 当前 ~87表); 排除股(北交所/三板)全库清+sync写入门防回潮。owner=`docs/data_management_framework.md` + `analysis/non_tushare_source_inventory_20260619.md`。

**Controller rule**: 主会话 owns 方向/真相源/共享文档/gate/staging/commit/风险写窗口。side-agent 只给有界证据, 非裁决。重大改动 (数据语义/策略/资金路径) 走对抗复审。

## Active Priority Board

| P | Workstream | State | Next action |
|---|---|---|---|
| P0 | 文档同步 | goal/INDEX reset-rewrite | 立 `moth doc-drift` 固化 (机器对账, 防再漂; mythos §16) |
| P0 | 彻底清除污染期产物 | **完成** (2轮穷尽 sweep: DELETE 64 docs/脚本/config/验证结果 + scrub/作废头注~14 + DB 0残留; build_feature_panel BROKEN flag; fact_feature_panel 三处虚假active改诚实) + **沙盒机制根治** (sandbox/ gitignored 用完删 + moth exploration-isolated, 探索不再散进主代码) | — |
| **P1** | **主升浪猎手执行方案** | **owner=`analysis/zhushenglang_hunter_plan_20260617.md`** (架构审计 REVISE + 用户阶段框架: 识别→分层+切阶段→逐阶段验因子; 短路径鱼身/出场/仓位优先) | Phase 0 地基补全 (build_feature_panel 重建 + GT 标签拆 + 负样本生成器) |
| **P1** | **数据底座: universe真相源 + 非tushare退役** | **2026-06-19**: universe 身份真相源切 tushare stock_basic (双向bug根治: K线∩前缀漏入指数000300 + stale akshare漏真股 → 加身份交集); 非tushare源全盘点(akshare22/tdxhub18/aif10 13, owner=`analysis/non_tushare_source_inventory_20260619.md`); **6/6 SAFE_TO_DROP 退役 ~838k行**(逐表对抗验证0消费者+shared-writer删X留Y); 排除股全库清+写入门防回潮; ensemble污染孤儿退役; P0拉取(stock_basic/share_float/stk_holdernumber done, stk_factor_pro运行中) | 链完验P0落库 universe-clean; **KEEP_MIGRATE 5表**(aif10 valuation/peer/forecast→v3_picture + dividend_summary→scoring + price_kline→regime)走 M2/M3/M4 双轨先迁后删 |
| P1 | 数据底座研究 (chips/分位/资金) | cyq 实测与 tushare qfq 同复权坐标可用 (C0 FAIL=审计比错基准非数据错); 高积分高价值因子已排序 | cyq 解冻 2018 + 待评估高价值未拉项 |
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
