# BestChoice × ChunkyMonkey Phase 0 — Freeze + Lineage + Schema Mapping

> 2026-05-21 freeze. Source: `/Users/dp/Documents/M/stock/bestchoice/analysis/bestchoice_chunkymonkey_validation_plan.md` §5 Phase 0.
> 主项目 stability retrain `lgbm_phase5_stability_20260521T055800Z` 跑中 (~47.5% trial complete), POC 启动 (Phase 1+) 等其出 best checkpoint + summary 后再决定.

## Run ID

- **run_id**: `bestchoice_formula_optuna_20260521_v1`
- 此 run_id 用于主项目侧 challenger 表 `mart_stock_formula_optuna_bestchoice_v1` 的 `run_id` 主键, 防多版 BestChoice import 串行覆盖.

## Phase 0 边界 (来自 plan §5.0)

- [DONE] 记录 source artifact 路径 + sha256 + row count
- [DONE] 分配 run_id
- [NO] **不 overwrite** `bestchoice/analysis/stock_formula_best.csv` (production CSV 保留原状)
- [NO] **不 promote** 任何 candidate 进主项目 champion/challenger 生产表
- [NO] **不动主项目库** (read-only)
- [NO] **不耗 GCP** (本 freeze 全本地)

## Source Artifacts (5 主 + 3 secondary)

### 主 artifact

| File | Path | Size | SHA-256 |
|---|---|---:|---|
| research_cache.duckdb | `bestchoice/analysis/research_cache.duckdb` | 973 MB | `cb3cc58090d97f6339fd0245030affb8a09f3f432cb7466a76ef1e50f310d6e7` |
| formula_local_optuna_batch_adoption.csv | `bestchoice/analysis/formula_local_optuna_batch_adoption.csv` | 21 MB | `c8ca8b53d47672c20884137325e5a052cf51ba9babe96f0024417e3e48fc6f54` |
| formula_local_optuna_batch_stock_best_replacements.csv | `bestchoice/analysis/formula_local_optuna_batch_stock_best_replacements.csv` | 354 KB | `86dc484b910921fdebaeb969eee5fbe1dd95af4f5be2986482b9520205055915` |
| formula_local_optuna_aggregate_audit.md | `bestchoice/analysis/formula_local_optuna_aggregate_audit.md` | 1.0 KB | `9ea028e701d8b2ef4e90e1cbfd171580e75f7a110d5b4572cd04274818ebe0e7` |
| operational_delivery_readiness.md | `bestchoice/analysis/operational_delivery_readiness.md` | 1.3 KB | `ae8335e6079f2f6e07007d8475cbb697c57fe3081a103d93e876050b4c3737c8` |

### Secondary artifact

| File | SHA-256 |
|---|---|
| formula_local_optuna_batch_merge_plan.csv | `fd45b49c9160d48f159fab6767ef0c0dad3bb4d4059a32c9baf7a9087ad46893` |
| formula_local_optuna_batch_smoke.csv | `2ba99bf1bc2ddded14f8237d41736ef91a0e24c830189eebc9e043e40f0287fe` |
| formula_local_optuna_batch_smoke_adoption.csv | `30b2a65886b229e2466a170bf98c4a87b713d5ba700c23ab3d99bcc16727faa3` |

## Row Count + 数据覆盖 (实测)

### research_cache.duckdb

| 项 | 值 |
|---|---:|
| Table `research_cache` rows | **45,908** |
| Table `cache_manifest` rows | 3 |
| Unique `stock_code` | **5,201** |
| Unique `formula_id` | **5** (`activity_breakout` / `gs_raw_buy` / `gs_pullback_confirm` / `volume_base_breakout` / `ma_base_breakout`) |
| Latest `data_latest_date` | **2026-05-19** |

### Adoption / Merge 决策分布 (来自 `research_cache.adoption_decision` / `merge_decision`)

| Decision | Count | 含义 |
|---|---:|---|
| candidate / replace | **1,146** | passes_guardrails → 可作 challenger 评估 |
| production_baseline | 21,302 | 现有 production 用 params, BestChoice 没找到更好 → 保留原状 |
| reject | 23,460 | 不通过 guardrail → 不入候选 |

跟 plan §2.1 列的 "Dry-run replacement candidates: 1146" 完全对得上, 数据完整.

### Formula 每公式 row 数

| formula_id | rows |
|---|---:|
| gs_raw_buy | 10,332 |
| activity_breakout | 10,332 |
| volume_base_breakout | 10,291 |
| gs_pullback_confirm | 9,141 |
| ma_base_breakout | 5,812 |

## research_cache 表完整 schema (39 columns)

```text
cache_key VARCHAR
source_type VARCHAR
stock_code VARCHAR
formula_id VARCHAR
variant_id VARCHAR
sell_rule VARCHAR
holding_days INTEGER
params_json VARCHAR
params_hash VARCHAR
version_key VARCHAR
data_latest_date VARCHAR
execution_model VARCHAR  -- 实测 vwap_tradable_v1
trials INTEGER
validation_ratio DOUBLE
baseline_status VARCHAR
optuna_status VARCHAR
adoption_decision VARCHAR  -- candidate / production_baseline / reject
adoption_reason VARCHAR
merge_decision VARCHAR     -- replace / '' / reject
merge_reason VARCHAR
signal_count INTEGER
win_rate DOUBLE
avg_ret DOUBLE
avg_dd DOUBLE
calmar DOUBLE
score DOUBLE
validation_signal_count INTEGER
validation_win_rate DOUBLE
validation_avg_ret DOUBLE
validation_score DOUBLE
baseline_score DOUBLE
baseline_validation_score DOUBLE
score_delta DOUBLE
validation_score_delta DOUBLE
baseline_investigation VARCHAR
optuna_investigation VARCHAR
source_artifact VARCHAR
source_mtime_ns BIGINT
created_at VARCHAR
```

## Schema Mapping → `mart_stock_formula_optuna_bestchoice_v1` (Phase 1 候选表)

Plan §5.1 列的最小字段映射:

| 主项目字段 | BestChoice 来源 | 类型 | 说明 |
|---|---|---|---|
| `run_id` | (常量) `bestchoice_formula_optuna_20260521_v1` | TEXT | freeze run id, 防多版覆盖 |
| `stock_code` | `research_cache.stock_code` | TEXT | 5201 unique |
| `formula_id` | `research_cache.formula_id` | TEXT | 5 unique |
| `variant_id` | `research_cache.variant_id` | TEXT | e.g. `local_optuna_t24_vsplit` / `default` / `loose` |
| `params_json` | `research_cache.params_json` | TEXT | yaml-style optuna params |
| `params_hash` | `research_cache.params_hash` | TEXT | 防重 / lineage 主键 |
| `sell_rule` | `research_cache.sell_rule` | TEXT | `fixed_5/10/15/20/30/60` / `formula_exit_or_5` 等 |
| `holding_days` | `research_cache.holding_days` | INTEGER | 实测 5/10/15/20/30/60 |
| `signal_count` | `research_cache.signal_count` | INTEGER | train+test 总 trade 数 |
| `win_rate` | `research_cache.win_rate` | DOUBLE | bestchoice 自己测的, **PIT 不严格** (无主项目 walk-forward) |
| `avg_ret` | `research_cache.avg_ret` | DOUBLE | 同上 |
| `avg_dd` | `research_cache.avg_dd` | DOUBLE | 同上 |
| `score` | `research_cache.score` | DOUBLE | bestchoice 综合分 |
| `validation_signal_count` | `research_cache.validation_signal_count` | INTEGER | 30% split |
| `validation_win_rate` | `research_cache.validation_win_rate` | DOUBLE | 30% split |
| `validation_avg_ret` | `research_cache.validation_avg_ret` | DOUBLE | 30% split |
| `validation_score` | `research_cache.validation_score` | DOUBLE | 30% split |
| `score_delta` | `research_cache.score_delta` | DOUBLE | optuna - baseline |
| `validation_score_delta` | `research_cache.validation_score_delta` | DOUBLE | 同上 OOS proxy |
| `adoption_decision` | `research_cache.adoption_decision` | TEXT | candidate / production_baseline / reject |
| `merge_decision` | `research_cache.merge_decision` | TEXT | replace / '' / reject |
| `source_artifact` | (常量) `bestchoice/analysis/research_cache.duckdb#cb3cc580...` | TEXT | lineage 回溯 |
| `source_data_latest_date` | `research_cache.data_latest_date` | TEXT | 最新 = 2026-05-19 |
| `as_of_date` | `source_data_latest_date` | DATE | PIT key: 参数证据可用的数据截止日 |
| `execution_model` | `research_cache.execution_model` | TEXT | `vwap_tradable_v1` |
| `created_at` | (now) | TEXT | import 时点 |
| `built_at` | (now) | TIMESTAMP | 主项目导入时点, 用于审计/lineage |

## 主项目验证缺口 (来自 plan §3)

BestChoice 已有 5201 × 5 公式 × params × sell_rule 级别候选, 但 indicators 只到 stock-formula level. 主项目验证必须补:

- T+1 入场
- 涨跌停 / 停牌约束
- 滑点 + 交易成本
- 每日 top-K 组合
- 单票去重 / 最大持仓数
- 换手率
- 组合净值 / max_dd / Sharpe / Calmar / 月胜率
- **跟现有 champion 的重合度和互补性**

这些是主项目 `paper_sim_v2 + Phase4 gate + mart_strategy_result_registry` 已有能力, Phase 1+ 直接复用.

## Phase 1 触发条件 (来自 plan §5 + goal.md line 16)

- [DONE] Phase 0 freeze 完成 (本 doc)
- [WAIT] 主项目 stability retrain 出 **可用 COMPLETE checkpoint + summary JSON** (当前 22 COMPLETE / 80 trial, ~47.5%)
- [WAIT] 或 retrain 被明确停掉并复盘

满足上述任一即进入 Phase 1: import 1146 candidates 到 `mart_stock_formula_optuna_bestchoice_v1` (read-only challenger, 不动 champion).

## 升 GCP 综合寻优触发 (来自 plan §5 + goal.md)

本地 paper_sim 跑出组合级:

- **Sharpe ≥ 1.3** 或
- **ann_ret ≥ 50% 且 max_dd 不差于 -25%** 或
- **显著改善 champion drawdown/return/相关性**

任一满足才升 GCP 全股票 / 全公式 / 多 context / multi cutoff 综合寻优. 否则只保留 evidence + 失败原因, 不上 GCP.

## 验证复现命令 (sha256 二次确认)

```bash
cd /Users/dp/Documents/M/stock/bestchoice/analysis
shasum -a 256 research_cache.duckdb \
  formula_local_optuna_batch_adoption.csv \
  formula_local_optuna_batch_stock_best_replacements.csv \
  formula_local_optuna_aggregate_audit.md \
  operational_delivery_readiness.md \
  formula_local_optuna_batch_merge_plan.csv

# 期望: 5 主 + 1 secondary hash 跟本 doc 表格完全一致
# 任一不一致 = source artifact 被改动 → run_id 失效, Phase 1 import 拒绝
```

## 引用

- `bestchoice/analysis/bestchoice_chunkymonkey_validation_plan.md` §5 Phase 0
- `goal.md` BestChoice 条件化持有/退出策略计划 (2026-05-21 14:58)
- `CLAUDE.md` §4 PIT-strict + §9 GCP controlled use
