# ChunkyMonkey 结构化复杂度审计 2026-05-20

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


本审计只写文档，不修改 `backend/`、`tests/`、`frontend/`。

范围：`backend/` Python 源码、`backend/tests/` 测试邻接、`analysis/modularization_refactor_plan.md` 对比。

背景引用：上次 codegraph audit `aa94bbab`、现有重构方案 `a5b70bb9`、已 deploy 的 P-2 / F1+F2 / P-1 / db.py Phase 1 / retrain in-flight 均作为既有事实，不在正文重复展开。

本轮新角度：函数级长函数、嵌套深度、控制流代理、长参数列表、跨模块调用、测试邻接缺口。

重要限制：`codegraph status` 显示索引有 pending changes；为遵守“只写 docs”的约束，本轮没有运行会写 `.codegraph/` 的 `codegraph sync`。

## A. complexity-review skill 能力描述

### A1. skill 安装状态

实测命令：

```bash
find ~/.codex/skills -maxdepth 3 -iname '*complexity*' -print
```

真实输出摘要：

```text
/Users/dp/.codex/skills/complexity-optimizer
/Users/dp/.codex/skills/complexity-optimizer/scripts/analyze_complexity.py
```

实测命令：

```bash
ls -la ~/.codex/skills
```

真实输出摘要：

```text
.system
complexity-optimizer
```

结论：用户指定的 `complexity-review` skill 未找到 / 无法调用。

降级策略：按本地可用的 `complexity-optimizer` skill 执行报告型复杂度审计，并用 grep / AST 代理指标补齐函数级量化。

### A2. 可用 fallback skill：complexity-optimizer

读取 `~/.codex/skills/complexity-optimizer/SKILL.md` 后，实测能力如下：

- 支持“analysis / scan / audit / review / report”场景自动产出复杂度报告。
- 内置 scanner：`scripts/analyze_complexity.py <repo> --format markdown|json`。
- scanner 定位常见模式：nested loop、sort in loop、io-or-query-in-loop。
- scanner 明确要求把输出当 leads，而不是证明；需要人工看上下文。
- report 模板要求包含范围、stack、test/build commands、findings、复杂度前后、风险、验证。
- 对 report-only 请求，skill 明确要求不修改代码。

### A3. fallback scanner 实测结果

实测命令：

```bash
python3 ~/.codex/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey/backend --format markdown
```

真实输出摘要：

```text
HIGH io-or-query-in-loop main.py:183
HIGH sort-in-loop routers/institution.py:540
HIGH nested-loop routers/institution.py:544
HIGH io-or-query-in-loop routers/updater.py:817
HIGH nested-loop routers/updater.py:1008
HIGH nested-loop routers/updater.py:1214
HIGH nested-loop routers/updater.py:1650
HIGH sort-in-loop routers/updater.py:2010
HIGH nested-loop routers/v3_portfolio_builder.py:312
HIGH io-or-query-in-loop scripts/audit_end_to_end.py:43
```

scanner 的主要加值：它把热点集中指向 `routers/updater.py`、`routers/institution.py`、多个 audit/sync scripts。

scanner 的主要局限：它按模式匹配，不知道业务热路径，也不计算真实 cyclomatic complexity。

本报告因此把 scanner 输出放入 C/D/G 作为线索，把 B 节的函数级指标作为主量化依据。

## B. 实测复杂度指标

### B0. 指标口径

源码范围：`backend/` 下 Python 文件，排除 `__pycache__` 与 `.optuna`。

生产源码口径：排除 `backend/tests/`。

测试邻接口径：`backend/tests/`。

函数行数：Python AST `lineno` 到 `end_lineno`。

嵌套层数：AST 中 `if / for / while / try / with / match` 的最大嵌套层数代理。

控制流代理：`if + for + while + except` 个数。

grep 代理：使用 `rg` 统计 `def / if / for / while / except`，用于全局 sanity check。

### B0.1 全局规模

实测命令输出：

```text
backend Python 文件: 846
source Python 文件（不含 tests）: 539
test Python 文件: 307
source AST functions: 4236
AST parse errors: 0
source LOC: 175911
test LOC: 53003
test functions: 1914
```

grep 代理命令输出：

```text
rg def source count: 4236
rg if/elif/for/while/except source count: 17201
rg >=12 spaces control lines: 2115
rg >=16 spaces control lines: 702
rg >=20 spaces control lines: 185
```

### B1. Top 20 高复杂度函数（按行数 + 嵌套深度 + 控制流代理排序）

| Rank | 函数名 | 文件 | 起始行 | 行数 | 嵌套层数 | if/for/while 数 | except 数 | 参数数 |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 1 | `calculate_stock_scores` | `backend/services/scoring.py` | 1648 | 1037 | 8 | 137 | 10 | 1 |
| 2 | `_step_sync_market_data` | `backend/routers/updater.py` | 2807 | 706 | 5 | 41 | 10 | 1 |
| 3 | `run_quality_audit` | `backend/services/audit.py` | 613 | 706 | 2 | 17 | 15 | 2 |
| 4 | `build_stock_horizon_profile` | `backend/scripts/build_stock_horizon_profile.py` | 238 | 687 | 3 | 15 | 0 | 17 |
| 5 | `_temporal_synergy_research` | `backend/services/workbench_read.py` | 2630 | 647 | 3 | 28 | 0 | 3 |
| 6 | `init_db` | `backend/services/schema_migrations.py` | 154 | 488 | 5 | 39 | 35 | 0 |
| 7 | `build_smart_plan` | `backend/services/audit.py` | 1321 | 429 | 5 | 57 | 9 | 4 |
| 8 | `validate_synergy_policy_candidate` | `backend/scripts/validate_synergy_policy_candidate.py` | 269 | 496 | 4 | 27 | 0 | 18 |
| 9 | `main` | `backend/scripts/build_price_kline_tdxhub.py` | 1001 | 403 | 6 | 44 | 9 | 0 |
| 10 | `run_optuna_synergy_search` | `backend/scripts/run_optuna_synergy_search.py` | 962 | 507 | 3 | 18 | 0 | 29 |
| 11 | `main` | `backend/scripts/portfolio_backtest.py` | 45 | 401 | 6 | 43 | 0 | 0 |
| 12 | `_stock_horizon_profile` | `backend/services/workbench_read.py` | 474 | 425 | 6 | 18 | 0 | 3 |
| 13 | `sync_financial_data` | `backend/services/financial_client.py` | 1136 | 416 | 5 | 25 | 3 | 9 |
| 14 | `main` | `backend/scripts/run_daily_topk.py` | 655 | 420 | 3 | 30 | 1 | 0 |
| 15 | `build_stock_stage_features` | `backend/services/stock_stage_engine.py` | 265 | 325 | 8 | 47 | 0 | 3 |
| 16 | `_step_build_profiles_sync` | `backend/routers/updater.py` | 1938 | 389 | 7 | 15 | 2 | 1 |
| 17 | `_build_panel_with_connection` | `backend/scripts/build_feature_panel_duck.py` | 1452 | 416 | 4 | 16 | 4 | 6 |
| 18 | `build_shareholder_plan_initial_feature_panel` | `backend/services/shareholder_plan_initial_feature_panel.py` | 259 | 434 | 2 | 16 | 0 | 12 |
| 19 | `_check_tdx_f10_source_availability` | `backend/services/data_quality.py` | 2666 | 426 | 3 | 12 | 0 | 5 |
| 20 | `get_industry_overview` | `backend/routers/screening.py` | 90 | 382 | 5 | 9 | 6 | 1 |

观察：

- `services/scoring.py::calculate_stock_scores` 是本轮新增的函数级头号热点：1037 行、8 层嵌套、137 个 if/for/while。
- `routers/updater.py` 虽然已被上次架构审计识别为 god router，本轮新增定位到 `_step_sync_market_data` 与 `_step_build_profiles_sync` 两个可拆函数。
- `services/audit.py` 有两个函数进入 Top 7，说明 audit 复杂度不是只有 `data_quality.py`。
- `scripts/build_stock_horizon_profile.py` 的单函数 687 行 + 17 参数，是“长函数”和“长参数列表”的交集。
- `schema_migrations.py::init_db` 在 db.py Phase 1 后仍有 488 行、35 个 except，说明 db.py 拆分后剩余 schema migration 仍需要二次清理。

### B2. 模块级分布

| Rank | 文件 | LOC | 函数数 | 类数 | 控制流代理和 | 深缩进控制行 | 最大函数行数 | 最大嵌套 | >50 行函数数 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `backend/routers/updater.py` | 5060 | 145 | 2 | 491 | 156 | 706 | 9 | 23 |
| 2 | `backend/services/scoring.py` | 2684 | 63 | 0 | 345 | 42 | 1037 | 8 | 9 |
| 3 | `backend/services/workbench_read.py` | 4516 | 65 | 0 | 231 | 73 | 647 | 6 | 21 |
| 4 | `backend/services/data_quality.py` | 4249 | 69 | 0 | 230 | 95 | 426 | 8 | 23 |
| 5 | `backend/services/audit.py` | 1749 | 25 | 0 | 155 | 8 | 706 | 5 | 4 |
| 6 | `backend/scripts/build_stock_horizon_profile.py` | 980 | 12 | 0 | 27 | 3 | 687 | 3 | 1 |
| 7 | `backend/scripts/build_feature_panel_duck.py` | 2291 | 60 | 0 | 141 | 14 | 416 | 4 | 11 |
| 8 | `backend/scripts/run_optuna_synergy_search.py` | 1541 | 40 | 0 | 74 | 27 | 507 | 4 | 6 |
| 9 | `backend/services/financial_client.py` | 1689 | 49 | 0 | 167 | 34 | 416 | 5 | 8 |
| 10 | `backend/scripts/build_price_kline_tdxhub.py` | 1407 | 42 | 0 | 157 | 32 | 403 | 6 | 6 |
| 11 | `backend/scripts/validate_synergy_policy_candidate.py` | 827 | 18 | 0 | 43 | 8 | 496 | 4 | 2 |
| 12 | `backend/scripts/train_multidim_model.py` | 1372 | 53 | 1 | 96 | 6 | 376 | 9 | 4 |
| 13 | `backend/services/schema_migrations.py` | 649 | 4 | 0 | 75 | 40 | 488 | 5 | 1 |
| 14 | `backend/scripts/run_optuna_model_stability_search.py` | 1629 | 50 | 4 | 82 | 10 | 350 | 6 | 8 |
| 15 | `backend/scripts/run_daily_topk.py` | 1184 | 23 | 0 | 81 | 5 | 420 | 3 | 5 |
| 16 | `backend/scripts/validate_synergy_policy_mark_to_market.py` | 1431 | 17 | 0 | 62 | 4 | 370 | 3 | 9 |
| 17 | `backend/scripts/run_multidim_walkforward.py` | 977 | 25 | 0 | 71 | 15 | 366 | 7 | 2 |
| 18 | `backend/services/shareholder_plan_initial_feature_panel.py` | 705 | 12 | 0 | 32 | 1 | 434 | 3 | 1 |
| 19 | `backend/services/etf_grid_engine.py` | 1038 | 22 | 0 | 106 | 5 | 333 | 3 | 7 |
| 20 | `backend/scripts/build_temporal_synergy_research.py` | 1643 | 30 | 0 | 63 | 2 | 257 | 5 | 11 |
| 21 | `backend/services/signals_v2.py` | 1999 | 57 | 4 | 133 | 15 | 176 | 6 | 10 |
| 22 | `backend/scripts/portfolio_backtest.py` | 449 | 1 | 0 | 43 | 21 | 401 | 6 | 1 |
| 23 | `backend/routers/screening.py` | 492 | 8 | 0 | 24 | 8 | 382 | 5 | 1 |
| 24 | `backend/services/shareholder_plan_family_walkforward.py` | 955 | 17 | 0 | 36 | 2 | 322 | 4 | 5 |
| 25 | `backend/services/stock_stage_engine.py` | 589 | 12 | 0 | 83 | 45 | 325 | 8 | 2 |

模块层结论：

- `routers/updater.py` 当前实测 5060 LOC，已高于重构方案中记录的 5034 LOC。
- `services/workbench_read.py` 和 `services/data_quality.py` 仍是 LOC 巨石，但函数级最高风险目前在 `services/scoring.py`。
- `schema_migrations.py` LOC 不高，但控制流密度高：649 LOC 中 `init_db` 单函数 488 行。
- 多个 scripts 不是单纯入口脚本，而是把 orchestration、SQL、评估逻辑混在同一个函数里。

### B3. 测试覆盖 vs 复杂度

没有发现可直接复用的 coverage artifact。

实测命令：

```bash
find . -maxdepth 2 \( -name '.coverage' -o -name 'coverage.xml' -o -name 'htmlcov' -o -name '.coveragerc' -o -name 'pyproject.toml' -o -name 'pytest.ini' \) -print
```

真实输出：

```text
./pytest.ini
```

因此本节使用“测试邻接代理”：直接 import 测试文件数 + 内容提及测试文件数。

| Rank | 模块 | LOC | 直接测试 import 数 | 内容提及测试文件数 | 最大函数行数 | 最大嵌套 |
|---:|---|---:|---:|---:|---:|---:|
| 1 | `routers.updater` | 5060 | 6 | 14 | 706 | 9 |
| 2 | `services.scoring` | 2684 | 5 | 11 | 1037 | 8 |
| 3 | `services.workbench_read` | 4516 | 5 | 5 | 647 | 6 |
| 4 | `services.data_quality` | 4249 | 2 | 4 | 426 | 8 |
| 5 | `services.audit` | 1749 | 2 | 31 | 706 | 5 |
| 6 | `scripts.build_stock_horizon_profile` | 980 | 0 | 1 | 687 | 3 |
| 7 | `scripts.build_feature_panel_duck` | 2291 | 0 | 6 | 416 | 4 |
| 8 | `scripts.run_optuna_synergy_search` | 1541 | 0 | 1 | 507 | 4 |
| 9 | `services.financial_client` | 1689 | 2 | 4 | 416 | 5 |
| 10 | `scripts.build_price_kline_tdxhub` | 1407 | 0 | 3 | 403 | 6 |
| 11 | `scripts.validate_synergy_policy_candidate` | 827 | 0 | 1 | 496 | 4 |
| 12 | `scripts.train_multidim_model` | 1372 | 1 | 7 | 376 | 9 |
| 13 | `services.schema_migrations` | 649 | 0 | 1 | 488 | 5 |
| 14 | `scripts.run_optuna_model_stability_search` | 1629 | 0 | 4 | 350 | 6 |
| 15 | `scripts.run_daily_topk` | 1184 | 3 | 5 | 420 | 3 |

测试邻接结论：

- 高复杂度 scripts 普遍缺少直接 import 级测试，说明主要靠端到端或内容提及覆盖。
- `services.data_quality` 的测试邻接明显低于它的复杂度规模。
- `services.audit` 的内容提及多，但直接 import 少；这类覆盖更像行为/contract 覆盖，不足以支撑大拆分。
- `codegraph affected` 对 top 文件返回“无受影响测试”，与手工测试邻接矛盾，说明当前 codegraph affected 不能作为本仓库测试选择的唯一依据。

## C. 架构 smell 清单

| Smell 类型 | 位置 | 严重度 | 证据 |
|---|---|---|---|
| Long Function | `services/scoring.py::calculate_stock_scores` | P0 | 1037 行、嵌套 8、if/for/while 137、except 10 |
| Long Function | `routers/updater.py::_step_sync_market_data` | P0 | 706 行、嵌套 5、scanner 多处命中 io/query-in-loop |
| Long Function | `services/audit.py::run_quality_audit` | P1 | 706 行、SQL/coverage/status 汇总混合 |
| Long Function | `services/workbench_read.py::_temporal_synergy_research` | P1 | 647 行、多个 mart 表读取与 response shape 混合 |
| Long Function + Long Parameter List | `scripts/build_stock_horizon_profile.py::build_stock_horizon_profile` | P1 | 687 行、17 参数 |
| Long Parameter List | `scripts/run_optuna_model_stability_search.py::run_optuna_model_stability_search` | P1 | 32 参数、350 行 |
| Long Parameter List | `scripts/run_optuna_synergy_search.py::run_optuna_synergy_search` | P1 | 29 参数、507 行 |
| Long Parameter List | `services/pipeline_manifest.py::record_pipeline_run` | P2 | 21 参数、82 行，高 fan-in 模块 |
| God Module | `routers/updater.py` | P0 | 5060 LOC、145 函数、23 个 >50 行函数 |
| God Module | `services/workbench_read.py` | P0 | 4516 LOC、21 个 >50 行函数 |
| God Module | `services/data_quality.py` | P1 | 4249 LOC、23 个 >50 行函数、深缩进控制行 95 |
| Dense Control Module | `services/scoring.py` | P0 | 控制流代理和 345，最大函数 1037 行 |
| Feature Envy | `services/scoring.py -> services.utils` | P1 | AST cross-module call 149 次 |
| Feature Envy | `services/etf_grid_engine.py -> services.utils` | P2 | AST cross-module call 47 次 |
| Feature Envy | `services/shareholder_plan_family_walkforward.py -> services.shareholder_plan_feature_family_eval` | P2 | AST cross-module call 44 次 |
| Feature Envy | `routers/institution.py -> services.db` | P1 | AST cross-module call 32 次，router 直接依赖 DB helper |
| Shotgun Surgery Candidate | `services.db` | P0 | source importers 177、test importers 2、总 179 |
| Shotgun Surgery Candidate | `services.schema_versions` | P1 | source importers 69 |
| Shotgun Surgery Candidate | `services.pipeline_manifest` | P1 | source importers 57、test importers 6 |
| Shotgun Surgery Candidate | `services.duck_adapter` | P1 | source importers 34、test importers 29 |
| Shotgun Surgery Candidate | `services.market_db` | P1 | source importers 51、test importers 6 |
| Hidden Test Risk | `scripts.*` high complexity modules | P1 | 多个 top modules 直接测试 import 数为 0 |
| Residual Schema Complexity | `services/schema_migrations.py::init_db` | P1 | db.py Phase 1 后仍 488 行、74 控制代理、35 except |
| Scanner Pattern Hotspot | `routers/institution.py` lines 540-574 | P1 | sort-in-loop、nested-loop、io-or-query-in-loop 同段命中 |
| Scanner Pattern Hotspot | `routers/updater.py` lines 817-2755 | P0 | io/query-in-loop、nested-loop、sort-in-loop 多段命中 |
| Coverage Artifact Gap | repo root | P2 | only `pytest.ini` found; no `.coverage` / `coverage.xml` / `htmlcov` |

### C1. God class 结果

阈值：class >500 行，或 public methods >20。

实测结果：无命中。

解释：本仓库主要问题不是传统 OOP god class，而是 god module、long function、script-orchestrator 过大。

### C2. Long parameter list Top 20

| Rank | 函数 | 文件 | 起始行 | 参数数 | 行数 |
|---:|---|---|---:|---:|---:|
| 1 | `run_optuna_model_stability_search` | `backend/scripts/run_optuna_model_stability_search.py` | 1201 | 32 | 350 |
| 2 | `run_drift_safe_candidate_batch` | `backend/scripts/run_drift_safe_candidate_batch.py` | 207 | 32 | 231 |
| 3 | `run_optuna_synergy_search` | `backend/scripts/run_optuna_synergy_search.py` | 962 | 29 | 507 |
| 4 | `rerank_optuna_synergy_mtm` | `backend/scripts/rerank_optuna_synergy_mtm.py` | 269 | 24 | 281 |
| 5 | `build_temporal_synergy_research` | `backend/scripts/build_temporal_synergy_research.py` | 1321 | 22 | 257 |
| 6 | `sweep_synergy_mtm_strategy` | `backend/scripts/sweep_synergy_mtm_strategy.py` | 156 | 22 | 239 |
| 7 | `validate_synergy_policy_mark_to_market` | `backend/scripts/validate_synergy_policy_mark_to_market.py` | 1003 | 21 | 370 |
| 8 | `record_pipeline_run` | `backend/services/pipeline_manifest.py` | 134 | 21 | 82 |
| 9 | `build_feature_drift_mitigation_panel` | `backend/scripts/build_feature_drift_mitigation_panel.py` | 298 | 20 | 319 |
| 10 | `_build_result_none` | `backend/services/backtest/optimize.py` | 346 | 19 | 38 |
| 11 | `validate_synergy_policy_candidate` | `backend/scripts/validate_synergy_policy_candidate.py` | 269 | 18 | 496 |
| 12 | `build_feature_rank_matrix_proxy` | `backend/scripts/build_feature_rank_matrix_duck.py` | 629 | 18 | 285 |
| 13 | `build_stock_horizon_profile` | `backend/scripts/build_stock_horizon_profile.py` | 238 | 17 | 687 |
| 14 | `build_shareholder_plan_family_walkforward` | `backend/services/shareholder_plan_family_walkforward.py` | 623 | 17 | 322 |
| 15 | `_risk_evaluate_selection` | `backend/scripts/run_optuna_synergy_search.py` | 558 | 16 | 254 |
| 16 | `write_reflection` | `backend/services/research/reflection.py` | 21 | 16 | 65 |
| 17 | `_insert_model` | `backend/scripts/train_multidim_model.py` | 939 | 16 | 52 |
| 18 | `build_feature_search_space` | `backend/scripts/build_feature_search_space.py` | 299 | 15 | 299 |
| 19 | `build_feature_association_stats` | `backend/scripts/build_feature_association_duck.py` | 701 | 15 | 256 |
| 20 | `build_drift_safe_feature_candidates` | `backend/scripts/build_drift_safe_feature_candidates.py` | 546 | 15 | 237 |

### C3. Cross-module call Top 20

| Rank | Caller | Callee module | Calls |
|---:|---|---|---:|
| 1 | `backend/services/scoring.py` | `services.utils` | 149 |
| 2 | `backend/services/etf_grid_engine.py` | `services.utils` | 47 |
| 3 | `backend/services/shareholder_plan_family_walkforward.py` | `services.shareholder_plan_feature_family_eval` | 44 |
| 4 | `backend/scripts/validate_synergy_policy_mark_to_market.py` | `scripts.validate_synergy_policy_candidate` | 39 |
| 5 | `backend/services/stock_stage_engine.py` | `services.utils` | 36 |
| 6 | `backend/scripts/build_feature_rank_matrix_duck.py` | `scripts.build_feature_association_duck` | 33 |
| 7 | `backend/routers/institution.py` | `services.db` | 32 |
| 8 | `backend/services/etf_engine.py` | `services.utils` | 32 |
| 9 | `backend/services/etf_mining_engine.py` | `services.utils` | 27 |
| 10 | `backend/routers/updater.py` | `services.gap_queue` | 26 |
| 11 | `backend/scripts/run_walkforward_feature_eval.py` | `scripts.run_feature_group_ablation` | 26 |
| 12 | `backend/scripts/train_tdx_keep_challenger_model.py` | `scripts.run_feature_ablation` | 25 |
| 13 | `backend/routers/updater.py` | `services.db` | 20 |
| 14 | `backend/scripts/run_optuna_feature_elimination.py` | `scripts.run_feature_group_ablation` | 18 |
| 15 | `backend/scripts/backtest_walkforward_portfolio.py` | `scripts.backtest_model_portfolio` | 17 |
| 16 | `backend/routers/updater.py` | `services.market_db` | 15 |
| 17 | `backend/scripts/plan_architecture_cleanup.py` | `services.pipeline_manifest` | 15 |
| 18 | `backend/services/akshare_client.py` | `services.kline_source` | 15 |
| 19 | `backend/services/data_sources/sources/tdxhub.py` | `services.tdx_source` | 15 |
| 20 | `backend/services/etf_mining_engine.py` | `services.etf_grid_engine` | 15 |

### C4. Import fan-in / shotgun surgery Top 20

| Rank | Module | Source importers | Test importers | Total |
|---:|---|---:|---:|---:|
| 1 | `services.db` | 177 | 2 | 179 |
| 2 | `services.schema_versions` | 69 | 0 | 69 |
| 3 | `services.pipeline_manifest` | 57 | 6 | 63 |
| 4 | `services.duck_adapter` | 34 | 29 | 63 |
| 5 | `services.market_db` | 51 | 6 | 57 |
| 6 | `services.utils` | 48 | 5 | 53 |
| 7 | `scripts` | 0 | 45 | 45 |
| 8 | `services.model_feature_schema` | 15 | 6 | 21 |
| 9 | `services` | 0 | 20 | 20 |
| 10 | `services.pricing_policy` | 15 | 4 | 19 |
| 11 | `services.paper_sim.config` | 13 | 6 | 19 |
| 12 | `services.industry` | 17 | 1 | 18 |
| 13 | `services.tdx_source` | 16 | 1 | 17 |
| 14 | `main` | 1 | 14 | 15 |
| 15 | `services.optimization.config` | 11 | 2 | 13 |
| 16 | `services.ml_lifecycle.registry` | 10 | 1 | 11 |
| 17 | `services.backtest.result` | 8 | 3 | 11 |
| 18 | `services.selection.ddl` | 5 | 6 | 11 |
| 19 | `routers` | 1 | 10 | 11 |
| 20 | `services.trading_config` | 9 | 1 | 10 |

## D. 优化机会（按 ROI 排序）

### D1. P0：拆 `calculate_stock_scores` 为评分 pipeline

证据：1037 行、嵌套 8、if/for/while 137、except 10。

复杂度下降方向：把 schema patch、preload、per-stock scoring、penalty、gate、persist 分开。

重构成本：中。

测试策略：先固定 `calculate_stock_scores` 的输入 DB fixture 与输出行数/关键字段，再分步抽纯函数。

新增价值：这不是 aa94bbab 的 db/fan-in finding，是本轮函数级扫描新增头号热点。

### D2. P0：拆 `routers/updater.py::_step_sync_market_data`

证据：706 行、scanner 多段命中 io/query-in-loop、模块总 LOC 5060。

复杂度下降方向：monthly sync、daily sync、gap queue reconciliation、xdxr sync、progress reporting 分离。

重构成本：中高。

测试策略：以现有 updater calendar / connectivity tests 为基础，新增每个 sync 子步骤的 fake conn 单测。

新增价值：不是重复“updater.py 很大”，而是定位到最该先拆的函数。

### D3. P0：为高复杂 scripts 引入 config object / run spec

证据：Optuna / synergy / horizon profile 函数参数数 17 到 32。

复杂度下降方向：把长参数列表收敛为 `RunConfig` / `SearchConfig` / `ProfileConfig`。

重构成本：中。

测试策略：config object 的 default/override/backward-compatible CLI 单测。

新增价值：和现有 P1-C Optuna runner 重合，但本轮给出参数级优先级。

### D4. P1：拆 `services/workbench_read.py::_temporal_synergy_research`

证据：647 行、涉及 quality/relevance/synergy/optuna/policy/redundancy 多个 mart 表。

复杂度下降方向：每个 read model 一个 loader，最终 assembler 只合并 dict。

重构成本：中。

测试策略：复用 `backend/tests/contract/test_workbench_read.py`，先做 byte-compatible payload fixture。

新增价值：现有 plan 说拆 workbench_read，本轮定位到最复杂 read model。

### D5. P1：拆 `services/audit.py::run_quality_audit`

证据：706 行、15 except、内容提及测试多但直接 import 测试少。

复杂度下降方向：audit check registry + metric collectors + report assembler。

重构成本：中。

测试策略：先把当前 payload snapshot 固化，然后逐块迁移 collectors。

新增价值：现有 plan 把 audit lib 放 P2，本轮函数级证据支持提前到 P1。

### D6. P1：`schema_migrations.py::init_db` 二次拆分

证据：db.py Phase 1 后，`db.py` 已 6 行 facade；但 `init_db` 仍 488 行、35 except。

复杂度下降方向：DDL migration steps 按 domain / version / idempotent helper 拆。

重构成本：低中。

测试策略：保留 facade import；跑 `test_db.py` 与 schema drift tests。

新增价值：区分“db.py god 已改善”和“migration god 函数仍在”。

### D7. P1：减少 router 直接 DB 依赖

证据：`routers/institution.py -> services.db` 32 calls，`routers/updater.py -> services.db` 20 calls。

复杂度下降方向：router 只做 HTTP adapter，DB orchestration 迁到 service layer。

重构成本：中。

测试策略：router contract tests 不变，新增 service 层 tests。

新增价值：和 P1-D router common layer一致，但这里用 cross-call 数据给出优先 router。

### D8. P2：拆 `services.utils` 为 domain helpers

证据：`services.scoring.py -> services.utils` 149 calls，多个 engine 对 `services.utils` 高依赖。

复杂度下降方向：calendar、numeric、date、score、format 等 helper 分包；保留兼容 re-export。

重构成本：中。

测试策略：先只移动 pure helper；禁止业务 helper 进入 `utils`。

新增价值：这是 feature envy 数据带出的新 finding。

### D9. P2：为 high-complex scripts 补直接测试邻接

证据：`build_stock_horizon_profile`、`build_feature_panel_duck`、`run_optuna_synergy_search`、`build_price_kline_tdxhub` 直接测试 import 数为 0。

复杂度下降方向：先不拆代码，先建立 narrow tests，降低后续拆分风险。

重构成本：低。

测试策略：每个高复杂 script 增一个 smoke-level direct import test 和一个 config parsing test。

新增价值：把“覆盖率不足”具体落到高风险模块。

### D10. P2：把 `pipeline_manifest.record_pipeline_run` 参数对象化

证据：21 参数、高 fan-in module。

复杂度下降方向：`PipelineRunRecord` dataclass / pydantic model，保留旧函数 wrapper。

重构成本：低中。

测试策略：旧签名与新对象路径产生相同 DB row。

新增价值：小成本降低 shotgun surgery 模块的调用复杂度。

## E. 与现有重构方案对比

### E1. 重合

- `routers/updater.py` 仍是 P0：本轮实测 5060 LOC、145 函数、23 个 >50 行函数。
- `services/workbench_read.py` 仍是 P0/P1 边界：4516 LOC、21 个 >50 行函数。
- `services/data_quality.py` 仍是大模块：4249 LOC、23 个 >50 行函数。
- Optuna runner 抽象仍有必要：参数列表 Top 20 中多个 Optuna/synergy 函数命中。
- Router common layer 仍有必要：router 到 `services.db` / `services.market_db` 直接调用明显。
- duck_adapter / DB connection 统一仍有必要：`duckdb.connect(` 当前 89 files、149 occurrences；`ATTACH` 当前 41 files。

### E2. 新增 finding（skill + codegraph + 手工代理加值）

- `services/scoring.py::calculate_stock_scores` 是函数级头号复杂度热点，超过 `workbench_read.py` 中任何单函数。
- `services/audit.py` 应从 P2-A 提前讨论：`run_quality_audit` 和 `build_smart_plan` 同时进入 Top 7。
- db.py Phase 1 已改变基线：`backend/services/db.py` 现在 6 行，新增 facade split 后应把后续注意力转向 `schema_migrations.py::init_db`。
- high-complex scripts 的测试邻接弱，尤其是 `build_stock_horizon_profile` / `run_optuna_synergy_search`。
- `services.utils` 是 feature envy 的实测中心，不只是普通 helper。
- `codegraph affected` 当前不能替代测试选择：对 top 文件返回“无受影响测试”，但 AST/import/content 代理显示存在测试邻接。

### E3. 优先级调整建议

- 保持 P0-C updater 拆分，但第一刀建议从 `_step_sync_market_data` 开始，而不是按 URL endpoint 粗切。
- 把 `services/scoring.py::calculate_stock_scores` 加入 P0 队列，至少先抽 pure scoring substeps。
- 把 `services/audit.py` 从 P2-A 提前到 P1 讨论，理由是两个超长函数正在承载工作台和更新流程风险。
- db.py 不再按旧 2478 LOC 处理；当前问题是 facade 背后的 migration/schema 文件边界。
- P1-C Optuna runner 之前，先引入 config object 降低 32 参数函数的调用风险。

## F. 待讨论决策点（供 Claude --resume 继续讨论）

1. 是否把 `services/scoring.py::calculate_stock_scores` 提升为 P0，与 updater 拆分并行推进？
2. updater 第一刀是按 endpoint 拆 router，还是先抽 `_step_sync_market_data` 这种高风险 step 函数？
3. db.py Phase 1 后，是否把验收口径从“db.py 行数”改成“schema_migrations.init_db 行数 / except 数 / migration step 粒度”？
4. `services/audit.py` 是否从 P2 提前到 P1，并先以 payload snapshot tests 固化行为？
5. 对 high-complex scripts，是否先补 direct import tests，再启动 optuna_runner / panel_builder framework 重构？

## G. 参考：codegraph 实测输出摘要

### G1. codegraph 版本与 CLI

实测命令：

```bash
/opt/homebrew/bin/codegraph --version
```

真实输出：

```text
0.6.8
```

实测命令：

```bash
/opt/homebrew/bin/codegraph query --help
```

真实输出摘要：

```text
Usage: codegraph query [options] <search>
Search for symbols in the codebase
Options:
  -p, --path <path>
  -l, --limit <number>
  -k, --kind <kind>
  -j, --json
```

### G2. codegraph index status

实测命令：

```bash
/opt/homebrew/bin/codegraph status /Users/dp/Documents/M/stock/chunkymonkey
```

真实输出摘要：

```text
Files: 813
Nodes: 12,583
Edges: 43,370
DB Size: 48.23 MB
function 5,097
import 3,926
method 897
class 394
Pending Changes:
  Added: 4 files
  Modified: 2 files
Run "codegraph sync" to update the index
```

解释：本轮没有 sync，避免写 `.codegraph/`。

### G3. `codegraph context backend/`

实测命令：

```bash
/opt/homebrew/bin/codegraph context backend/
```

真实输出摘要：

```text
## Code Context
Query: backend/
Entry Points
- BACKEND variable - backend/scripts/cron_daily.py:47
- BACKEND variable - backend/scripts/audit_stale_references.py:48
- BACKEND variable - backend/scripts/seed_dim_data_asset.py:33
```

解释：该命令把 `backend/` 当 task query，返回的是符号上下文，不是目录复杂度摘要。

### G4. `codegraph query "high complexity"`

实测命令：

```bash
/opt/homebrew/bin/codegraph query "high complexity" -p backend/ -l 20
```

真实输出摘要：

```text
test_high_calmar_high_score backend/tests/optimization/test_composite.py:24
test_kelly_high_win_high_payoff backend/tests/test_portfolio_sizer.py:72
test_audit_high_critical_feature_pit_blocks_zero_coverage_high_risk_fields backend/tests/pipeline/test_audit_registry_feature_pit.py:406
MIN_N_HIGH_CONVICTION backend/scripts/build_stock_formula_optuna.py:44
MIN_WIN_HIGH_CONVICTION backend/scripts/build_stock_formula_optuna.py:43
```

解释：`query "high complexity"` 是 fuzzy symbol search，命中了名字含 high 的测试，不是复杂度分析。

### G5. centrality query

实测命令：

```bash
/opt/homebrew/bin/codegraph query "centrality" -p backend/ -l 20
```

真实输出：

```text
No results found for "centrality"
```

实测命令：

```bash
/opt/homebrew/bin/codegraph query "node centrality" -p backend/ -l 30
```

真实输出摘要：

```text
_is_wall_clock_date_string backend/tests/test_calendar_gate.py:108
dcExport design/design-canvas.jsx:529
```

解释：当前 CLI 没有直接暴露 centrality metric；需要用 import fan-in / cross-call 代理。

### G6. workbench query

实测命令：

```bash
/opt/homebrew/bin/codegraph query "workbench" -p backend/ -l 30
```

真实输出摘要：

```text
build_workbench_pipelines backend/services/workbench_read.py:1964
build_workbench_recommendations backend/services/workbench_read.py:3609
build_workbench_champion backend/services/workbench_read.py:4294
build_workbench_features backend/services/workbench_read.py:2247
build_workbench_storage backend/services/workbench_read.py:2585
build_workbench_research backend/services/workbench_read.py:4073
services.workbench_read import backend/routers/workbench.py:7
services.workbench_read import backend/tests/contract/test_workbench_read.py:9
```

解释：codegraph 能很好找 workbench read model 和测试/route import，但不提供函数 LOC。

### G7. get_conn query

实测命令：

```bash
/opt/homebrew/bin/codegraph query "get_conn" -p backend/ -l 20
```

真实输出摘要：

```text
get_conn backend/services/db.py:37
services.db import backend/routers/institution.py:15
services.db import backend/routers/market.py:20
services.db import backend/routers/recommendation.py:9
services.db import backend/routers/screening.py:6
services.db import backend/routers/updater.py:41
services.db import backend/routers/workbench.py:6
services.db import backend/services/audit.py:15
services.db import backend/scripts/portfolio_backtest.py:29
services.db import backend/scripts/run_scoring.py:20
```

解释：这支持 `services.db` 仍是 shotgun surgery candidate，但当前 `db.py` 已是 6 行 facade。

### G8. updater query

实测命令：

```bash
/opt/homebrew/bin/codegraph query "updater" -p backend/ -l 30
```

真实输出摘要：

```text
_should_stop backend/routers/updater.py:1393
update_all backend/routers/updater.py:4085
update_status backend/routers/updater.py:4262
update_stop backend/routers/updater.py:4309
reset_derived backend/routers/updater.py:4318
run_lifeboat backend/routers/updater.py:4823
lifeboat_status backend/routers/updater.py:4865
lifeboat_report backend/routers/updater.py:4873
update_sync backend/routers/updater.py:4882
update_calc backend/routers/updater.py:4888
update_mart backend/routers/updater.py:4894
```

解释：codegraph 显示 updater HTTP endpoints 与 helpers 混在一个文件；B/C 节的 AST 指标补上函数级优先级。

### G9. affected tests

实测命令：

```bash
/opt/homebrew/bin/codegraph affected -p /Users/dp/Documents/M/stock/chunkymonkey backend/routers/updater.py backend/services/workbench_read.py backend/services/scoring.py backend/services/data_quality.py --depth 5 --filter 'backend/tests/**/*.py'
```

真实输出：

```text
No test files affected by the changed files.
```

解释：这与 AST/import/content 测试邻接代理不一致。本轮建议不要用当前 `codegraph affected` 作为唯一 test selection 依据。

### G10. 其他 grep / wc 实测摘录

实测命令：

```bash
wc -l backend/services/db.py backend/services/db_connection.py backend/services/schema_core.py backend/services/schema_marts.py backend/services/schema_migrations.py
```

真实输出：

```text
6 backend/services/db.py
27 backend/services/db_connection.py
756 backend/services/schema_core.py
692 backend/services/schema_marts.py
649 backend/services/schema_migrations.py
2130 total
```

实测命令：

```bash
rg -l 'duckdb\.connect\(' backend --glob '*.py' | wc -l
rg -l 'ATTACH DATABASE|ATTACH\s+' backend --glob '*.py' | wc -l
rg -l 'argparse\.ArgumentParser' backend --glob '*.py' | wc -l
rg -l 'logging\.getLogger' backend --glob '*.py' | wc -l
rg -l "if __name__ == ['\"]__main__['\"]" backend --glob '*.py' | wc -l
rg -l 'optuna\.create_study' backend --glob '*.py' | wc -l
```

真实输出：

```text
duckdb.connect files: 89
ATTACH files: 41
argparse files: 197
logging.getLogger files: 248
__main__ entry files: 221
optuna.create_study files: 13
```

### G11. 与 aa94bbab 的关系

aa94bbab 的 5 perf hotspots、HIGH-1/HIGH-2/HIGH-4、ATTACH-41 作为既有背景保留。

本轮不重复展开这些 finding；只引用它们解释为什么本轮转向函数级、长参数、测试邻接、cross-call 和 post-db-split migration complexity。
