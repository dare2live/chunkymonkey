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
| 最大回撤 | **目标量** | >= -20% | 同上 (生存约束: 活到 edge 兑现) |
| 超额 vs HS300 | 目标量 | > 0 | 真实基准 (小盘 cohort 须对标中证1000/2000, 非 HS300; 见缺陷 N21) |
| 月胜率 | **诊断量** | >= 55% | walk-forward OOS 月度分布; **单独不构成放行** |
| 胜率×盈亏比期望 | **诊断量** | > 0 | 单笔期望=胜率×平均盈−败率×平均亏 (positive_expectancy) |

**判断法典 C-WinReturn (2026-06-15 用户: "除了考核胜率还要考核收益率, 最终目的不是证明策略有效而是真能赚钱")**:
胜率是诊断量, 收益率+max_dd 是目标量 —— 胜率脱离盈亏比无意义 (40%×3:1 完胜 60%×0.5:1)。验收全 AND, 单项不放行。
**仓位管理 (sizing/exit/exposure) 是一等设计轴** (把 {edge,胜率,盈亏分布} 转成 {实现收益,回撤} 的传递函数), 非事后系数。
机器执法: `experiment_harness.kpi_verdict` + `tradability_verdict`; owner 全文 = `docs/strategy_validation_contract.md` 判断法典节。

**当前: `unknown`** —— 2026-06-14 模型/特征/寻优层全 reset, 参数寻优从零重做。不许引用 reset 前的旧数字。

## Current Phase: 干净地基上重建 alpha

**地基现状 (2026-06-14 reset 后)**: smartmoney **85 表 / 2.5G** (raw源/dim/财报PIT/十大股东/K线中间 + 档案展示 + 治理infra); 数据管理框架已立 (8层声明式 `data_layers.yaml` + `data_layer_audit` + `schema_layer_filter` 防重建 + moth 13断言全pass); S1 基本面四件套 (forecast/express/income/fina_indicator) 回填完成; CI绿。owner=`docs/data_management_framework.md`。

**Controller rule**: 主会话 owns 方向/真相源/共享文档/gate/staging/commit/风险写窗口。side-agent 只给有界证据, 非裁决。重大改动 (数据语义/策略/资金路径) 走对抗复审。

## Active Priority Board

| P | Workstream | State | Next action |
|---|---|---|---|
| P0 | 文档同步 | goal/INDEX reset-rewrite | 立 `moth doc-drift` 固化 (机器对账, 防再漂; mythos §16) |
| P0 | Alpha 验证 S0 | **完成** (留档4表 + `experiment_jobs.py` loader 恢复修4悬空import + `consumer_alpha_validation` family + `consumer_alpha_matrix.yaml` 6候选→7cell + `experiment_consumer_alpha_validation.py` config驱动执行器: gate-before-run/prereg_hash/PIT每步落档/dry空矩阵ic_scan不造假; 10单测+moth断言+CI全pass) | 进 L0 |
| P1 | 裸K线基准 L0 | **Tier-1 标尺定** (owner=`l0_bare_kline_baseline_spec`): 寻参 RUN 完成 (pre-reg d80e8ce 冻结+grill+plan_validator闸+3门, J1全过/J3无anomaly) → **标尺=reversal_short_term OOS RankIC +0.064 (lookback=20)**, 寻参佐证默认近最优(非过拟合敏感)。全链审计过 (对抗7skeptic抓embargo死闸已修, moth 21断言, 防泄露3门固化, DSR诚实框定为辅助)。每个 alpha 须超 +0.064 | **Tier-2 backtest 终验引擎** (信号→T+1含成本→OOS sharpe, 对齐KPI; reset删须重建) → S3 逐数据 alpha 验证 (S1数据已备) |
| P1 | 条件化 Phase B 实验1 | **per-stage L0 IC 跑完** (owner=`analysis/per_stage_l0_ic_result_20260615`): 条件化成立 (reversal IC 0.004~0.156 跨形态, 市场级 +0.064=平均稀释), **用户"低位有效"假设被数据推翻** (Stage1 底部≈0); 赢家=**Stage1.5 突破中 +0.156 (2.44x 基线/IC_IR 0.895)**。验收 `CONFIRM_PENDING_ABLATION` (+144% 触 §4.2 相对红线; PIT链已核净 stage/feat/label 全 as-of 须 ablation 转正)。**实验2 (公式×形态矩阵 + MACD零轴, owner=`formula_stage_matrix_20260615`)**: 4公式 ALL 全复现标尺; **突破中是关键 regime 且方向反** (reversal +0.156 vs macd/ma -0.116/-0.117); 底部全公式≈0 (用户低位假设全否); MACD 零轴上下不同 (DIF+ -0.059 vs DIF- -0.026, 用户点成立)。**实验3 子型 (owner=`subpattern_ic_20260615`)**: 低位5子型全<+0.064 (pit_selfcheck PIT_CLEAN), 用户"低位多试"方向被数据否, edge 在突破非低位。**ablation Gate2 (owner=`ablation_gate2_stage1.5`)**: MC 截面置换 real +0.156 vs null mean +0.0003/std 0.0047, p_raw=0 (~33σ), Bonferroni×30 p_adj≈0 → **REAL_EDGE 验收 CONFIRMED**; Gate0(PIT)+Gate1(OOS)+Gate2(MC) 三门过, 条件化思路统计验证 | **Tier-2 含成本 backtest 跑完 (owner=`tier2_conditional_backtest_20260615`, 引擎=干净重建 `portfolio_returnbacktest.py`+4单测; 旧 portfolio_backtest.py[5-07]退役标P2)**: 用户 push back "旧引擎可能有问题" 证实 — 旧引擎 cash*0.95欠仓+5%容差致 -42% 假象, **clean 引擎实测**: 周度 net **-2.8%** / **gross +7.1% (Sharpe +0.42)**, dd -44%, 月胜率 45% → **KPI_FAIL 但 edge 真实**。结构性裁决: reversal edge **~5天快衰减** (周度 gross+7.1% 有 edge, 但 turnover1.93 成本拖累 31% 吃掉→net-2.8%; 月度低换手则 edge 消失 gross-4.8%) = **快换手捕edge vs 低成本不可兼得=结构性不可交易**。**重定向**: 裸K线短衰减信号被成本结构杀死 → **Phase D 慢衰减信号 (财务质量/资金流trend/筹码结构, edge持续数周→低换手→成本可survive)**; 不在裸K线短信号上投精调 |
| P2 | 深层解耦 backlog | kept routers 懒加载已删 services / 8 散落服务自建表 / 23 god-file (framework doc §6) | rebuild 时按 layer 顺手解耦, moth 守不回潮; **不 big-bang** (反复破 CI 教训) |

## 重建路线 (全 config 驱动, owner=`analysis/alpha_validation_program_spec_20260614.md`)

1. **S0** 实验台 + 留档表 (experiment_store, 与 live 隔离防污染)。
2. **L0 裸K线基准** = 裸K线公式 walk-forward OOS 寻优的**最佳OOS参数** (防过拟合第一约束: OOS选参/DSR/pre-reg/限维度) — 标尺, 每个 alpha 要超越它。owner=`analysis/l0_bare_kline_baseline_spec_20260614.md`。
3. **逐步加 alpha 因子** (S1 数据已备), 每个过 leakage 审计 + walk-forward OOS, 结论入 `retired_experiments.yaml` (challenger 只留摘要不留全表)。
4. **多维策略立方体** (cell = Segment × Feature-set × Policy, 全 config 组合; owner=`analysis/multidim_strategy_architecture_20260613.md`), edge 为正再逐维解锁。

## Operating Reminders

- 主动用全套工具/skill (不等点名): `architect-controller` (架构/总指挥) · `mythos` (神话/创世) · `chunkymonkey-governance` (跑批前 grill) · moth (断言对账) · codegraph (耦合/依赖) · workflow (并发)。
- 第一性原理真相源: K线=可交易性 / 日历=日期 / config-table-service owner=业务规则。
- 删确定死的路径直接删, 不留注释/隐藏flag/兼容垫片; 但**不 big-bang 硬删紧耦合层** (按 layer 增量, moth 守)。
- commit 走 `bash scripts/safe_commit.sh`; 大改动数据语义/策略/资金路径走对抗复审。
- 历史详情 (reset 前 Strategy Portfolio / 数据底座 blocker / DB分区 / 旧 board / live gate) 见各 owner 文档 + ledger; 不在本文件保留。
