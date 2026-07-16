# BestChoice frozen challenger contract

> 状态：`FROZEN_CHALLENGER`
> 原冻结日：2026-05-24
> 边界复核：2026-07-16

本文件是 `bestchoice/` 的唯一边界与证据清单。项目级权威仍是
`../AGENTS.md`、`../goal.md`、`../docs/MASTER_TOPLEVEL_DESIGN.md` 和
`../docs/strategy_validation_contract.md`；本子树没有独立 goal、agent、handoff、
恢复流程、自动跑批或发布权限。

`evidence_manifest.json` 是本清单的机器可校验镜像，不是第二份业务规则；
`scripts/verify_frozen_evidence.py` 对 hash、shape、唯一键和候选/拒绝分布 fail closed。

## 保留的 challenger

`formula_engine.py` 固定保留五个公式：

| formula_id | 名称 |
|---|---|
| `gs_pullback_confirm` | GS回调确认 |
| `gs_raw_buy` | GS原始买点 |
| `ma_base_breakout` | 均线筑底突破 |
| `activity_breakout` | 活跃度大牛突破 |
| `volume_base_breakout` | 巨量蓄势启动 |

`execution_model.py` 的 `vwap_tradable_v1` 只用于解释历史结果，不是当前名义价格、
PIT、T+1、停牌/涨跌停、成本或容量契约的合格实现。

## 冻结代码指纹

| 文件 | SHA-256 |
|---|---|
| `formula_engine.py` | `5096d4778a2b8f34afd1c1f5dfcf7b1033294fa5e421b76ec46d340202a82379` |
| `execution_model.py` | `22f58cde4d981c9ca01a038a9db031aee5efb5c9441813c49b6073618f075216` |

改动任一实现即产生新的 challenger 版本，不能继续沿用这里的历史结果。

## 最小历史证据

| 文件 | 角色 | 数据行/列 | SHA-256 |
|---|---|---:|---|
| `analysis/formula_local_optuna_batch_adoption.csv` | 完整 stock×formula 结果；包含 candidate 与 reject 原因 | 26,005 / 70 | `c8ca8b53d47672c20884137325e5a052cf51ba9babe96f0024417e3e48fc6f54` |
| `analysis/stock_formula_best.csv` | 解释历史 delta 所需的 baseline | 21,302 / 14 | `333531c602d93d3ca1498425c7df17258ff9bcf61ddfbb21eeba5171bf02d3e8` |

其中 adoption evidence 包含 `1,146 candidate + 24,859 reject`；失败和无增益结果必须
保留，不能只保留赢家。候选分布为 `activity_breakout=652`、`gs_raw_buy=233`、
`gs_pullback_confirm=144`、`volume_base_breakout=117`、`ma_base_breakout=0`。因此这里
冻结的是五个公式定义，不是“五个均已通过”的 challenger。

原聚合记录的数据截止日为 `2026-05-19`，但其 `passed`/readiness 还依赖现已不存在的
research/incremental/drift DuckDB，已从活动证据中删除。上述 candidate 只是旧门槛下的
历史标签；不代表当前 accept。缺少 as-of 的旧字段一律按 `unknown` 处理。候选参数行可
从 adoption evidence 的 `optuna_*` 与 `optuna_params` 重建，不再保留重复 replacement 表。

## 明确不具备的能力

- 没有可运行的 BestChoice Web 应用、UI tab、paper simulator 或活跃数据入口；
- 没有 `backend/services/bc_absorbed/`、Track B 副本或自动吸收路径；
- 没有 `StrategyRelease`、`DecisionBatch`、当前 KPI 或生产候选资格；
- 旧 qfq、单股最优参数、Optuna、胜率和 `operational_ready` 声称均不是组合级
  可交易 edge；
- 禁止自动跑批、GCP/付费计算、后台恢复、旧脚本复活或覆盖主项目数据。

## 重新接入门

首次接入只能把本证据包作为只读、namespaced challenger。至少需要：

1. 校验上述 code/data hash，并登记 lineage、数据截止日和 `DatasetSnapshot`；
2. 在主项目当前 eligible universe 与 availability/PIT 契约下重放 daily trigger；
3. 使用名义可成交价格，执行 T+1、停牌/涨跌停、成本、容量和仓位规则；
4. 固定同一 snapshot、fold、成本和执行，按 B0→B5 做单 block 增量消融；
5. 使用 purged walk-forward、embargo 与 single-touch holdout；
6. 产出可复现 artifact manifest 和 `ExperimentVerdict(accept/reject/inconclusive)`；
7. 只有 `accept` verdict 才能讨论在新 namespace 吸收公式代码，且不得覆盖本证据包。

Tier 0 与分类契约未闭合前，不启动大规模公式搜索。
