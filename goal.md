# ChunkyMonkey Goal

> 当前阶段契约 only。完成项 → `analysis/project_state_ledger.md`;运行态 →
> `SESSION_HANDOFF.md`。保持 < 165 行;item 完成就移 ledger 只留当前决策/blocker。

## Document Contract

| Document | Owns | Startup use |
|---|---|---|
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

| 指标 | 目标 | 口径 |
|---|---|---|
| 年化收益 | >= +30% | 含成本 OOS paper_sim, 2023-01-03 起 100 万初始 |
| 最大回撤 | >= -20% | 同上 |
| 超额 vs HS300 | > 0 | 真实 HS300 基准 (基准=0 无效) |
| 月胜率 | >= 55% | walk-forward OOS 月度分布, 不是均值 |

**当前: `unknown`** —— 2026-06-14 模型/特征/寻优层全 reset, 参数寻优从零重做。不许引用 reset 前的旧数字。

## Current Phase: 干净地基上重建 alpha

**地基现状 (2026-06-14 reset 后)**: smartmoney **85 表 / 2.5G** (raw源/dim/财报PIT/十大股东/K线中间 + 档案展示 + 治理infra); 数据管理框架已立 (8层声明式 `data_layers.yaml` + `data_layer_audit` + `schema_layer_filter` 防重建 + moth 13断言全pass); S1 基本面四件套 (forecast/express/income/fina_indicator) 回填完成; CI绿。owner=`docs/data_management_framework.md`。

**Controller rule**: 主会话 owns 方向/真相源/共享文档/gate/staging/commit/风险写窗口。side-agent 只给有界证据, 非裁决。重大改动 (数据语义/策略/资金路径) 走对抗复审。

## Active Priority Board

| P | Workstream | State | Next action |
|---|---|---|---|
| P0 | 文档同步 | goal/INDEX reset-rewrite | 立 `moth doc-drift` 固化 (机器对账, 防再漂; mythos §16) |
| P0 | Alpha 验证 S0 | 实验台 (`experiment_jobs.py`) 被 reset 删, yaml 契约在 | 在干净地基重写 consumer_alpha family + executor + 留档表 (fact_experiment_verdict/ic_scan/lineage/pit_audit → `experiment_store.duckdb`) |
| P1 | 裸K线基准 L0 | 待建 (北极星: 先建标尺再加 alpha) | S0 后第一个 consumer: 仅 OHLCV 派生的基准策略, PIT-clean, 作每个 alpha 的标尺 |
| P2 | 深层解耦 backlog | kept routers 懒加载已删 services / 8 散落服务自建表 / 23 god-file (framework doc §6) | rebuild 时按 layer 顺手解耦, moth 守不回潮; **不 big-bang** (反复破 CI 教训) |

## 重建路线 (全 config 驱动, owner=`analysis/alpha_validation_program_spec_20260614.md`)

1. **S0** 实验台 + 留档表 (experiment_store, 与 live 隔离防污染)。
2. **L0 裸K线基准** (只 OHLCV, PIT-clean) — 标尺, 每个 alpha 要超越它。
3. **逐步加 alpha 因子** (S1 数据已备), 每个过 leakage 审计 + walk-forward OOS, 结论入 `retired_experiments.yaml` (challenger 只留摘要不留全表)。
4. **多维策略立方体** (cell = Segment × Feature-set × Policy, 全 config 组合; owner=`analysis/multidim_strategy_architecture_20260613.md`), edge 为正再逐维解锁。

## Operating Reminders

- 主动用全套工具/skill (不等点名): `architect-controller` (架构/总指挥) · `mythos` (神话/创世) · `chunkymonkey-governance` (跑批前 grill) · moth (断言对账) · codegraph (耦合/依赖) · workflow (并发)。
- 第一性原理真相源: K线=可交易性 / 日历=日期 / config-table-service owner=业务规则。
- 删确定死的路径直接删, 不留注释/隐藏flag/兼容垫片; 但**不 big-bang 硬删紧耦合层** (按 layer 增量, moth 守)。
- commit 走 `bash scripts/safe_commit.sh`; 大改动数据语义/策略/资金路径走对抗复审。
- 历史详情 (reset 前 Strategy Portfolio / 数据底座 blocker / DB分区 / 旧 board / live gate) 见各 owner 文档 + ledger; 不在本文件保留。
