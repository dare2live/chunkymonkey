# ChunkyMonkey 综合架构审计与经验蒸馏

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


审计日期: 2026-05-17  
审计范围: `/Users/dp/Documents/M/stock/chunkymonkey`  
只读证据: 代码结构、`backend/config/*.yaml`、`data/*.duckdb` 元数据、现有审计/治理脚本。  
本文件只做文档规划, 不修改源代码、不删数据。

## 0. 关键事实

| 项 | 实际审计结果 | 证据 |
|---|---:|---|
| `backend/config` YAML | 22 个, 不是任务描述中的 21 个 | `find backend/config -name '*.yaml'` |
| `backend/services` Python | 280 个 | `find backend/services -type f -name '*.py'` |
| `backend/scripts` Python | 200 个 | `find backend/scripts -type f -name '*.py'` |
| `backend/routers/updater.py` | 5034 行 / 111 个函数 / 16 个 route | `backend/routers/updater.py:1`, 函数统计 |
| DuckDB 有效库 | `smartmoney`, `market`, `etf`, `alpha158`; `stock.db` 不是 DuckDB | DuckDB read-only connect |
| `smartmoney.duckdb` | 319 objects = 316 tables + 3 views | information_schema |
| `dim_data_asset` | 250 rows, 但漏登记 70 个现存对象 | `data/smartmoney.duckdb:dim_data_asset` |
| `dim_schema_version` | 207 rows, 当前 drift_count=0 | `data/smartmoney.duckdb:dim_schema_version` |
| K 线 watermark | `mart_data_source_watermark.kline_daily/tdxhub_quote` 仍停在 `2026-04-30`, 但 `market.price_kline_tdxhub` 到 `2026-05-15` | watermark 表 + market 表 |

---

## 1. 系统模块分配表

| Layer | 模块路径 | 责任 | 当前问题 | 改进建议 | 优先级 |
|---|---|---|---|---|---|
| Data ingestion | `backend/routers/updater.py` | 手工/智能更新、行情/财务/股东/行业/调研同步总入口 | 5034 行、111 函数, 同时承担 plan、budget、connectivity、runner、UI 状态; 见 `backend/routers/updater.py:408` 动态 budget 与 `:444` watermark 估算 | 拆成 `update_plan.py`、`step_registry.py`、`connectivity.py`、`runner.py`、`status_api.py`; router 只保留 HTTP adapter | P0 |
| Data ingestion | `backend/services/data_sources/*` | tdxhub/aif10/akshare capability registry 与实际路线 | registry 与真实 write path 分离; `clients_registry.py:1-9` 已说明写入登记动机, 但仍无法覆盖全部现存表 | `ClientSpec` 升级为强制 table registry 源; 新 writer 未登记 CI fail | P0 |
| Data ingestion | `backend/scripts/build_price_kline_tdxhub.py`, `gcp/fetch_kline_via_vm.sh` | tdxhub K 线构建、本地/GCP 拉取 | tdxhub server pool stale 时 watermark 没主动 alert; `analysis/data_integrity_audit_20260517.md` 记录 91% codes 缺失 | watermark freshness contract + alert sink, 超 SLA 阻断 paper/live | P0 |
| Data PIT | `backend/scripts/build_stage_opt_pit.py` | stage_opt walk-forward PIT 表构建 | 注释承认旧表是 latest snapshot, 再 ETL 到 PIT; `:106-108`; `--limit-stocks` 只限 ETL, 不限 subprocess, `:82-85` | 将 cutoff 作为优化脚本一等参数, 直接写 PIT 表; smoke 必须真正限 stock | P0 |
| Data PIT | `backend/services/labels/feature_join_v3.py` | v3 feature-label panel JOIN | 文件内标出 `inst_quality_*` latest leakage, `:20-23`; 训练层靠排除列兜底 | 物理 DROP leakage 列或入 `mart_institution_profile_pit`; CI 禁止 `latest` 特征入训练表 | P0 |
| Data PIT | `backend/services/labels/feature_join_v4.py` | Phase 4 features 接入 canonical panel | v4 接入后 feature ROI 审计显示 13 dead/noise, sector_momentum 0% coverage | panel build 后自动跑 var~0 + Spearman + coverage gate, fail 不允许 promote | P0 |
| Feature engineering | `backend/services/features/*` | market_cap_decile、industry_beta、capital_flow、sector_momentum、survey 等特征模块 | `sector_momentum.py:10-15` 明确 14.3% fallback contamination; `AUDIT_2026_05_17.md:34-40` 证明仅 `mc_decile` + `lhb` 真有效 | 新 feature group 必须先过 `feature-engineering-roi` skill: 100K spearman、coverage、var~0、SHAP/ablation | P0 |
| ML training | `backend/services/ml_ranking/*`, `backend/scripts/train_p0b_lightgbm.py` | walk-forward LGBM/LambdaMART、RankIC、OOS prediction | 有 RankIC gate, 但特征质量 gate 与 config registry 未完全串联 | 训练入口统一调用: config registry + PIT registry + feature ROI gate + final_holdout_freeze | P1 |
| ML training | `backend/scripts/run_p0b_lightgbm_optuna_v4.py` | Optuna v4 perf-wired training | 已从 v3 24-day ETA 改为 PreparedPanel, `:1-17`; 但 GCP runner 仍可选 generic/v4 双路径 | GCP job config 记录 runner、snapshot hash、git SHA、feature registry hash; Wave 结果回灌后统一 gate | P1 |
| Optimization | `backend/services/optimization/*` | Optuna governance、walk-forward、约束、objectives | `optuna_config.yaml` 是唯一 Optuna 配置, 但 paper_sim/ML/model_search 仍分散 | 所有 experiment 进入 config registry; Optuna study metadata 必须含 config_hash 和 PIT snapshot_id | P1 |
| Optimization | `gcp/*` | Cloud Batch/GCS job generation, result pull | `gcp/experiment_config.yaml:140-156` 当前 selection 只开 1 job; `run_feature_ablation_grid.sh:78-89` 用 `--no-persist` 避 DuckDB lock | 形成 4-parallel x N-core 标准 runner: worker 写 jsonl/parquet, reducer 单写 DuckDB | P0 |
| Paper sim | `backend/services/paper_sim/*` | paper trading selection/sizer/exit/risk/report | 12 个 `paper_sim_*.yaml` 分散; `selector.py:596-645` 分发 5 种 mode; score loader 有 legacy latest path | base+overlay+experiment 配置层级; production 只允许 registry mode=prod 的 config | P0 |
| Paper sim | `backend/services/paper_engine/*`, `backend/routers/v3_paper.py` | live/paper NAV、position、signal IC | paper_engine 与 paper_sim 并存, 表名 `mart_paper_nav` / `mart_paper_sim_nav` 易混 | 明确 v3 paper engine 与 v2 paper_sim 边界; table registry 标注 owner/mode/deprecated | P1 |
| Routers | `backend/main.py`, `backend/routers/*` | FastAPI app + API routers | `main.py:92-162` 注册大量 router; updater 是 god file; 业务 SQL 仍散在 router | router 只做 schema/HTTP; read/write 逻辑进 service; updater P0 拆分 | P0 |
| Frontend | `index.html`, `assets/js/*`, `assets/css/main.css` | 单页工作台、股票、数据链路、模型实验室 | 原生 JS 模块多、状态全局; 数据链路 UI 有但不是强治理入口 | 将 data governance 页面绑定 registry drift/freshness alert; 前端只展示后端 contract 结果 | P2 |
| Ops | `ops/*`, `backend/scripts/launchd/*`, `configs/launchd/*` | launchd/cron, nightly audit | nightly audit 有报告但无主动告警; `analysis/data_integrity_audit_20260517.md` 已指出 | `nightly_data_audit` 写 `mart_alert_event`, 本地 notification/Slack/email 任选一个 sink | P0 |
| Ops | `backend/services/pipeline_lock.py` | pipeline lock ledger | 只保护命名 pipeline, 不能阻止所有脚本直接写同一 DuckDB; `:80-108` 已有 acquire 逻辑 | 所有写 DuckDB 脚本必须通过 `with_pipeline_lock`; pre-commit grep `duckdb.connect(... read_only=False)` | P0 |
| Governance | `backend/services/data_governance/*` | field dictionary loader/enforcer/ETL hook | 机制已存在; 但 `field_dictionary.yaml` 只覆盖 21 张核心表, 远少于 319 objects | dictionary 不应承担全表 registry; 拆 `field_dictionary` 与 `table_registry`; 新表必须 registry + schema_version | P0 |
| Governance | `backend/services/schema_versions.py` | schema version declaration and drift table | drift 当前为 0, 但注释承认“不是 ORM, 不强制”, `:24-26` | migration script 强制 up/down; ALTER TABLE 只允许 migration runner | P0 |
| Governance | `backend/services/data_lineage/registry.py` | 派生 lineage metadata | `:37-40` 说明仅登记关键路径, long tail 后续补; 实际 70 objects 漏 asset registry | lineage registry 从 metadata-only 升级为 CI gate: production mart 没 lineage 不能新增 | P1 |
| Tests | `backend/tests/*` | 单测/集成/contract/perf | 大量测试 `from conftest import duck_mem`; `conftest.py:14-20` 主动把 tests 目录塞进 `sys.path` | 迁移 test helpers 到 `backend/tests/helpers` 或 `backend/testing`; 禁止 production import tests | P1 |
| Storage/table mgmt | `backend/services/storage_retention.py`, `backend/config/storage_retention.yaml` | model artifact/table retention | retention 覆盖模型与部分 artifact, 未覆盖全部 deprecated table/cols | table registry 增加 TTL/grace/drop_after; cleanup 先 archive metadata 再 DROP | P1 |

---

## 2. 配置文件管理规划

### 2.1 实际 YAML 审计

| 文件 | 行数 | Top-level keys | 实际 owner/loader | 当前定位 |
|---|---:|---|---|---|
| `backend/config/data_sources.yaml` | 12 | `version, capabilities` | `services/source_policy.py` | prod |
| `backend/config/feature_registry.yaml` | 315 | `version, model_input_excluded, groups` | `services/feature_registry.py` | prod |
| `backend/config/field_dictionary.yaml` | 515 | `conventions, databases, join_templates, known_inconsistencies` | `services/data_governance/config.py` | prod |
| `backend/config/model_search.yaml` | 920 | `version, defaults, ranker_policy, research_schedule` | `scripts/plan_research_schedule.py` | prod/experiment |
| `backend/config/optuna_config.yaml` | 105 | `governance, walk_forward, search_space, composite, constraints, execution, output, deflated_sharpe` | `services/optimization/config.py` | prod |
| `backend/config/paper_sim_config.yaml` | 119 | `portfolio, selection, exit, swap, tx_cost, risk, validation, data` | `services/paper_sim/config.py` | prod_legacy |
| `backend/config/paper_sim_cross_formula.yaml` | 93 | same paper_sim schema | `services/paper_sim/config.py` | experiment |
| `backend/config/paper_sim_ensemble.yaml` | 257 | same paper_sim schema | `services/paper_sim/config.py` | experiment |
| `backend/config/paper_sim_hybrid.yaml` | 94 | same paper_sim schema | `services/paper_sim/config.py` | experiment |
| `backend/config/paper_sim_ml_score.yaml` | 124 | same paper_sim schema | `services/paper_sim/config.py` | prod_live |
| `backend/config/paper_sim_ml_score_C_5178.yaml` | 145 | same paper_sim schema | `services/paper_sim/config.py` | experiment |
| `backend/config/paper_sim_ml_score_governance_v1.yaml` | 124 | same paper_sim schema | `services/paper_sim/config.py` | experiment |
| `backend/config/paper_sim_ml_score_governance_v1_rank_diff.yaml` | 123 | same paper_sim schema | `services/paper_sim/config.py` | experiment |
| `backend/config/paper_sim_ml_score_sector_budget.yaml` | 136 | same paper_sim schema | `services/paper_sim/config.py` | experiment |
| `backend/config/paper_sim_ml_score_tiered.yaml` | 146 | same paper_sim schema | `services/paper_sim/config.py` | experiment |
| `backend/config/paper_sim_momentum.yaml` | 121 | same paper_sim schema | `services/paper_sim/config.py` | experiment |
| `backend/config/paper_sim_reversal.yaml` | 120 | same paper_sim schema | `services/paper_sim/config.py` | experiment |
| `backend/config/paper_sim_reversal_deep_only.yaml` | 118 | same paper_sim schema | `services/paper_sim/config.py` | experiment |
| `backend/config/pipeline_performance_policy.yaml` | 30 | `policy_id, version, ... budgets` | `services/pipeline_performance_policy.py` | prod |
| `backend/config/pricing_label_policy.yaml` | 330 | `version, policy_id, price_adjustment, announcement_policy, ...` | `services/pricing_policy.py` | prod |
| `backend/config/recommendation_universe.yaml` | 8 | `policy_id, description, require_stock_name, ...` | `services/recommendation_universe.py` | prod |
| `backend/config/storage_retention.yaml` | 60 | `version, defaults, protected_model_statuses, ...` | `services/storage_retention.py` | prod |

### 2.2 YAML registry schema and entries

```yaml
version: 1
registry_id: chunkymonkey_config_registry_v1
defaults:
  env_override_prefix: CM_
  require_owner: true
  require_mode: true
  require_depends_on: true
  valid_modes: [prod, prod_live, prod_legacy, experiment, deprecated]
  valid_pit_eligible: [true, false]
entries:
  - path: backend/config/data_sources.yaml
    owner: services.source_policy
    mode: prod
    pit_eligible: true
    env_override: CM_DATA_SOURCES_CONFIG
    depends_on: []
  - path: backend/config/feature_registry.yaml
    owner: services.feature_registry
    mode: prod
    pit_eligible: true
    env_override: CM_FEATURE_REGISTRY_CONFIG
    depends_on: [backend/config/data_sources.yaml]
  - path: backend/config/field_dictionary.yaml
    owner: services.data_governance.config
    mode: prod
    pit_eligible: true
    env_override: CM_FIELD_DICTIONARY_CONFIG
    depends_on: [backend/config/data_sources.yaml]
  - path: backend/config/model_search.yaml
    owner: scripts.plan_research_schedule
    mode: experiment
    pit_eligible: true
    env_override: CM_MODEL_SEARCH_CONFIG
    depends_on: [backend/config/feature_registry.yaml, backend/config/pricing_label_policy.yaml]
  - path: backend/config/optuna_config.yaml
    owner: services.optimization.config
    mode: prod
    pit_eligible: true
    env_override: CM_OPTUNA_CONFIG
    depends_on: [backend/config/pricing_label_policy.yaml]
  - path: backend/config/paper_sim_config.yaml
    owner: services.paper_sim.config
    mode: prod_legacy
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/pricing_label_policy.yaml, backend/config/recommendation_universe.yaml]
  - path: backend/config/paper_sim_cross_formula.yaml
    owner: services.paper_sim.config
    mode: experiment
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/paper_sim_config.yaml]
  - path: backend/config/paper_sim_ensemble.yaml
    owner: services.paper_sim.config
    mode: experiment
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/paper_sim_config.yaml, backend/config/optuna_config.yaml]
  - path: backend/config/paper_sim_hybrid.yaml
    owner: services.paper_sim.config
    mode: experiment
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/paper_sim_ml_score.yaml]
  - path: backend/config/paper_sim_ml_score.yaml
    owner: services.paper_sim.config
    mode: prod_live
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/pricing_label_policy.yaml, backend/config/optuna_config.yaml]
  - path: backend/config/paper_sim_ml_score_C_5178.yaml
    owner: services.paper_sim.config
    mode: experiment
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/paper_sim_ml_score.yaml]
  - path: backend/config/paper_sim_ml_score_governance_v1.yaml
    owner: services.paper_sim.config
    mode: experiment
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/paper_sim_ml_score.yaml]
  - path: backend/config/paper_sim_ml_score_governance_v1_rank_diff.yaml
    owner: services.paper_sim.config
    mode: experiment
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/paper_sim_ml_score_governance_v1.yaml]
  - path: backend/config/paper_sim_ml_score_sector_budget.yaml
    owner: services.paper_sim.config
    mode: experiment
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/paper_sim_ml_score.yaml]
  - path: backend/config/paper_sim_ml_score_tiered.yaml
    owner: services.paper_sim.config
    mode: experiment
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/paper_sim_ml_score.yaml]
  - path: backend/config/paper_sim_momentum.yaml
    owner: services.paper_sim.config
    mode: experiment
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/paper_sim_config.yaml]
  - path: backend/config/paper_sim_reversal.yaml
    owner: services.paper_sim.config
    mode: experiment
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/paper_sim_config.yaml]
  - path: backend/config/paper_sim_reversal_deep_only.yaml
    owner: services.paper_sim.config
    mode: experiment
    pit_eligible: true
    env_override: CM_PAPER_SIM_CONFIG
    depends_on: [backend/config/paper_sim_reversal.yaml]
  - path: backend/config/pipeline_performance_policy.yaml
    owner: services.pipeline_performance_policy
    mode: prod
    pit_eligible: false
    env_override: CM_PIPELINE_PERFORMANCE_POLICY
    depends_on: []
  - path: backend/config/pricing_label_policy.yaml
    owner: services.pricing_policy
    mode: prod
    pit_eligible: true
    env_override: CM_PRICING_LABEL_POLICY
    depends_on: []
  - path: backend/config/recommendation_universe.yaml
    owner: services.recommendation_universe
    mode: prod
    pit_eligible: true
    env_override: CM_RECOMMENDATION_UNIVERSE_CONFIG
    depends_on: []
  - path: backend/config/storage_retention.yaml
    owner: services.storage_retention
    mode: prod
    pit_eligible: false
    env_override: CM_STORAGE_RETENTION_CONFIG
    depends_on: []
```

### 2.3 可执行 Python validator

```python
from __future__ import annotations

from pathlib import Path
import os
import yaml

VALID_MODES = {"prod", "prod_live", "prod_legacy", "experiment", "deprecated"}

def load_config_registry(path: str | Path) -> dict:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = raw.get("entries") or []
    seen: set[str] = set()
    errors: list[str] = []
    for item in entries:
        p = item.get("path")
        if not p or not Path(p).exists():
            errors.append(f"{p}: file missing")
        if p in seen:
            errors.append(f"{p}: duplicate registry entry")
        seen.add(p)
        if not item.get("owner"):
            errors.append(f"{p}: missing owner")
        if item.get("mode") not in VALID_MODES:
            errors.append(f"{p}: invalid mode={item.get('mode')}")
        if not isinstance(item.get("pit_eligible"), bool):
            errors.append(f"{p}: pit_eligible must be bool")
        for dep in item.get("depends_on") or []:
            if not Path(dep).exists():
                errors.append(f"{p}: missing dependency {dep}")
        env_name = item.get("env_override")
        if env_name and env_name in os.environ and not Path(os.environ[env_name]).exists():
            errors.append(f"{p}: env override {env_name} points to missing file")
    actual = {str(p) for p in Path("backend/config").glob("*.y*ml")}
    missing = sorted(actual - seen)
    if missing:
        errors.append(f"unregistered config files: {missing}")
    if errors:
        raise SystemExit("\n".join(errors))
    return raw
```

### 2.4 Base + Overlay + Experiment 三层 hierarchy

| 层 | 目录建议 | 内容 | 规则 |
|---|---|---|---|
| base | `backend/config/base/*.yaml` | 生产默认: pricing、tx_cost、risk hard limits、PIT policy、数据路径逻辑名 | 只允许稳定字段; 不写 run_id; 不写临时日期 |
| overlay | `backend/config/overlays/local.yaml`, `gcp.yaml`, `paper_live.yaml` | 环境差异: DB path、GCS bucket、CPU/thread、alert sink | 可覆盖 leaf value; 不能新增未知 key |
| experiment | `backend/config/experiments/20260517_wave1.yaml` | 试验轴: feature groups、model、horizon、seed、selection mode | 必须有 owner、expiry、promotion_gate、base_ref、overlay_ref |

合并顺序: `base -> overlay -> experiment -> env_override`。  
冲突策略: 类型不一致 fail; 删除 key 必须显式 `_delete: true`; experiment 过 `expires_at` 后 CI warn, 30 天后 fail。

### 2.5 Migration plan

| Step | 动作 | 输出 | 验证 |
|---:|---|---|---|
| 1 | 新增 `backend/config/config_registry.yaml` | 上述 22 entries | `python backend/scripts/check_config_registry.py` |
| 2 | 把 `paper_sim_config.yaml` 拆成 `base/paper_sim.yaml` | base defaults | 与旧 config deep-merge 后 hash 相同 |
| 3 | 把 11 个 paper_sim 变体迁到 `experiments/paper_sim/*.yaml` | experiment overlay | `load_config()` 支持三层 merge |
| 4 | `run_paper_sim_v2.py --config-path` 改为 `--config-id` 优先 | registry lookup | 旧 path 保留 30 天并 warning |
| 5 | GCP `experiment_config.yaml` 纳入 registry | GCP experiment entry | job config 写入 `config_hash` |
| 6 | CI 增加 config registry check | unregistered YAML fail | 新 YAML 不登记不能 commit |
| 7 | 删除/归档 deprecated YAML | `docs/config_deprecations.md` | retention 记录 + no callers |

---

## 3. 数据表管理规划

### 3.1 实际 DuckDB inventory

| DB | objects | tables | views | prefix 分布 | 备注 |
|---|---:|---:|---:|---|---|
| `data/smartmoney.duckdb` | 319 | 316 | 3 | `mart:187`, `fact:54`, `dim:33`, `raw:23`, 其他 22 | 主业务库 |
| `data/market.duckdb` | 13 | 12 | 1 | `price:5`, `mart:4`, `dim:1`, `fact:1`, `market:1`, `v:1` | K 线/市场源 |
| `data/etf.duckdb` | 8 | 8 | 0 | `etf:4`, `mart:4` | ETF 子系统 |
| `data/alpha158.duckdb` | 1 | 1 | 0 | `fact:1` | Alpha158 panel |
| `data/stock.db` | 0 | 0 | 0 | invalid DuckDB | 文件存在但不是 DuckDB |

### 3.2 表登记现状

| Registry | 覆盖 | 问题 |
|---|---:|---|
| `dim_data_asset` | 250 rows | 漏 70 个现存对象, 包括 `fact_capital_flow_pit_daily`, `mart_p0a_feature_label_panel_v4`, `mart_p1_optuna_trials` |
| `dim_schema_version` | 207 rows | 当前 drift=0, 但 `schema_versions.py:24-26` 明确不是强制机制 |
| `field_dictionary.yaml` | 21 table specs | 是字段字典, 不应当承担全库 table registry |
| `data_lineage/registry.py` | 关键 lineage | `:37-40` 写明只登记关键路径, long tail 未补 |

`dim_data_asset` 漏登记 70 个对象:

```text
dim_data_asset, dim_fee_schedule, dim_liquidity_threshold, dim_listing_status, dim_market_segment, dim_price_limit_rules, dim_stock_stage_days, dim_style_factor, dim_trading_rule, dim_trading_session, fact_candle_pattern_daily, fact_capital_flow_pit_daily, fact_financial_pit_daily, fact_industry_beta_daily, fact_market_cap_decile_daily, fact_optuna_governance_log, fact_paper_position, fact_paper_sim_position, fact_paper_sim_trade, fact_sector_momentum_daily, fact_sector_predicted_ret_daily, fact_signal_context, fact_stock_fundamental_stage_daily, fact_stock_selection_log, fact_stock_technical_stage, fact_stock_type_daily, fact_technical_trigger, mart_daily_blended_recommendation, mart_daily_formula_buys, mart_daily_position_recommendation, mart_data_health, mart_forecast_upside_live, mart_formula_horizon_evidence, mart_formula_weight_history, mart_model_composite_score, mart_model_edge_flags, mart_model_feature_ablation, mart_p0a_feature_label_panel, mart_p0a_feature_label_panel_v3, mart_p0a_feature_label_panel_v4, mart_p0a_label_panel, mart_p0b_oos_predictions, mart_p0b_walkforward_eval, mart_p1_ablation_result, mart_p1_optuna_trials, mart_p3_acceptance_result, mart_paper_nav, mart_paper_sim_kpi, mart_paper_sim_nav, mart_per_formula_stage_optimal, mart_per_stock_optuna_best, mart_per_stock_stage_strategy_optimal, mart_per_stock_stage_strategy_optimal_pit, mart_per_stock_strategy_optimal, mart_research_reflection_log, mart_signal_ic, mart_stage_formula_fitness, mart_stock_formula_buy_signal_daily, mart_stock_formula_optuna, mart_stock_formula_optuna_v2, mart_stock_picture_daily, mart_stock_pool_assignment, mart_stock_regime_full, mart_stock_selection_outcome, mart_stock_selection_summary, mart_stock_survey_features, mart_stock_trade_plan, raw_profit_forecast_snapshot_daily, v_stock_sector_momentum_daily, v_stock_sector_predicted_ret
```

### 3.3 完整 table registry schema

```yaml
version: 1
registry_id: chunkymonkey_table_registry_v1
defaults:
  require_schema_version: true
  require_lineage_for_layers: [fact, mart]
  require_pit_fields_for_layers: [fact, mart]
  valid_layers: [raw, fact, mart, dim, research, cache, sys, view, other]
tables:
  - database: smartmoney.duckdb
    table: fact_financial_pit_daily
    layer: fact
    mode: prod
    owner: scripts.backfill_financial_pit
    writer_module: backend/scripts/backfill_financial_pit.py
    primary_key: [stock_code, trade_date]
    grain: stock_code x trade_date
    pit_fields:
      event_date: report_date
      announce_date: announce_date
      available_date: announce_date
      built_at: built_at
    source_tables: [raw_gpcw_detail, market.v_price_kline_qfq]
    downstream_tables: [mart_p0a_feature_label_panel_v3, mart_p0a_feature_label_panel_v4]
    row_lineage_fields: [source_table, source_row_hash, built_at]
    expected_freshness_hours: 48
    schema_version: v1
    retention:
      mode: keep
      ttl_days: null
      cleanup_after_grace_days: null
  - database: smartmoney.duckdb
    table: mart_p0a_feature_label_panel_v4
    layer: mart
    mode: prod
    owner: services.labels.feature_join_v4
    writer_module: backend/scripts/build_p0a_feature_panel_v4.py
    primary_key: [stock_code, signal_date]
    grain: stock_code x signal_date
    pit_fields:
      signal_date: signal_date
      available_date: signal_date
      built_at: built_at
    source_tables:
      - mart_p0a_feature_label_panel_v3
      - fact_capital_flow_pit_daily
      - fact_market_cap_decile_daily
      - fact_industry_beta_daily
      - fact_sector_momentum_daily
      - mart_stock_industry_pit
    downstream_tables: [backend/scripts/run_p0b_lightgbm_optuna_v4.py]
    row_lineage_fields: [source_table, source_row_hash, built_at]
    expected_freshness_hours: 48
    schema_version: v1
    retention:
      mode: rebuildable_keep_latest_n
      retain_latest_versions: 3
      ttl_days: 180
      cleanup_after_grace_days: 30
```

### 3.4 Lineage DAG

| 链路 | DAG |
|---|---|
| 行情主线 | `tdxhub.quotes` -> `market.price_kline_tdxhub` -> `market.v_price_kline_qfq` -> `fact_feature_panel` / `alpha158.fact_alpha158_panel` -> `mart_p0a_label_panel` -> `mart_p0a_feature_label_panel_v4` -> `mart_p0b_oos_predictions` -> `mart_daily_recommendation` / `paper_sim` |
| 财务 PIT | `tdxhub.affair(gpcw)` -> `raw_gpcw_detail`, `raw_tdx_gpcw_wide` -> `fact_financial_derived`, `fact_financial_pit_daily` -> `mart_p0a_feature_label_panel_v3/v4` |
| 股东/F10 | `tdxhub.holders` -> `raw_tdx_f10_holder_research`, `raw_tdx_f10_holder_count_history` -> `fact_top10_holder_period`, `fact_holder_count_period`, `fact_shareholder_plan_tdx_f10` -> `fact_capital_flow_pit_daily` / `mart_shareholder_plan_initial_feature_panel` -> model panels |
| LHB/调研 | `akshare/aif10` -> `raw_lhb_daily`, `raw_institution_surveys` -> `fact_lhb_event`, `mart_stock_survey_features` -> `fact_capital_flow_pit_daily` / v4 panel |
| 行业/板块 | `tdxhub.block` -> `raw_tdx_industry_file_snapshot`, `dim_stock_tdx_industry_history` -> `mart_stock_industry_pit` -> `fact_sector_momentum_daily` join -> v4 panel |
| Optuna/GCP | `mart_p0a_feature_label_panel_v4` snapshot -> GCP job local DuckDB -> `trials.jsonl` -> `gcp/pull_results_to_duckdb.py` -> `mart_p1_optuna_trials` -> retrain/final model |
| Paper sim | `mart_p0b_oos_predictions` + `mart_per_stock_stage_strategy_optimal_pit` + `market.v_price_kline_qfq` -> `fact_paper_sim_trade`, `fact_paper_sim_position`, `mart_paper_sim_nav`, `mart_paper_sim_kpi` |

### 3.5 命名约定

| Prefix | 语义 | 允许写入 | 典型 PIT 字段 | Retention |
|---|---|---|---|---|
| `raw_` | 原始/半原始源快照 | ingestion only | `fetched_at`, `ingested_at`, `snapshot_date`, `notice_date` | append, 长期保留 |
| `fact_` | 可回放事实/特征事实 | ETL/build script | `trade_date`, `notice_date`, `available_date`, `built_at` | append 或可重建 |
| `mart_` | 下游消费/模型/运维集市 | build script/reducer | `snapshot_date`, `signal_date`, `built_at`, `cutoff_date` | 可重建, TTL/版本保留 |
| `dim_` | 维表/字典/latest snapshot | sync or seed | `effective_from`, `effective_to`, `updated_at` | SCD 保留, latest 可覆盖 |
| `v_` | view | migration only | 继承底表 | 不存储 |
| `_cache_` | UI/read cache | service cache | `built_at`, `updated_at` | TTL 短 |

### 3.6 Retention / TTL / cleanup policy

| 表类 | TTL | Cleanup |
|---|---:|---|
| raw source replay 表 | 永久或 3 年 | 禁止直接 DROP; 只允许压缩/archive |
| fact PIT daily | >= 3 年 | 按 date 分段归档, 不删除近期训练窗口 |
| mart feature panels | 保留 latest 3 versions + champion refs | 未被 lifecycle/model_selection 引用后 grace 30 天 DROP |
| Optuna trials | 保留 promoted/challenger/shadow; 其他 180 天 | 先导出 jsonl/parquet 再删 |
| paper_sim runs | prod_live 永久; experiment 180 天 | KPI summary 永久, trade/position 可归档 |
| cache/temp | 7-30 天 | 自动 DROP, 必须 registry 标注 `mode: cache` |
| deprecated tables/cols | grace 30 天 | `mart_data_deprecation_record` -> archive metadata -> DROP |

### 3.7 schema_version enforcement

1. 每个 `fact_`/`mart_`/`dim_derived` 在 table registry 声明 `schema_version`。
2. `ALTER TABLE` 禁止散落在业务脚本; 只能出现在 `backend/migrations/YYYYMMDD_HHMM__name.py`。
3. migration 必须有 `up(conn)` 与 `down(conn)`。
4. migration runner 写 `mart_schema_migration`。
5. `schema_versions.py` 只保留 expected version map; `record_actual_version` 只能由 migration/build 成功后调用。
6. CI grep: `ALTER TABLE` 不在 `backend/migrations/` 直接 fail。

---

## 4. 数据治理深挖: PIT-as-code

### 4.a PIT-as-code decorator

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

PIT_TABLES: dict[str, "PITSpec"] = {}

@dataclass(frozen=True)
class PITSpec:
    table_name: str
    primary_key: tuple[str, ...]
    pit_fields: dict[str, str]
    expected_freshness_hours: int
    owner: str
    source_tables: tuple[str, ...] = field(default_factory=tuple)

    def validate(self) -> None:
        required = {"available_date", "built_at"}
        missing = required - set(self.pit_fields)
        if missing:
            raise ValueError(f"{self.table_name}: missing pit_fields {sorted(missing)}")
        if not self.primary_key:
            raise ValueError(f"{self.table_name}: primary_key required")

def pit_table(
    *,
    table_name: str,
    primary_key: tuple[str, ...],
    pit_fields: dict[str, str],
    expected_freshness_hours: int,
    owner: str,
    source_tables: tuple[str, ...] = (),
) -> Callable[[type], type]:
    def wrap(cls: type) -> type:
        spec = PITSpec(
            table_name=table_name,
            primary_key=primary_key,
            pit_fields=pit_fields,
            expected_freshness_hours=expected_freshness_hours,
            owner=owner,
            source_tables=source_tables,
        )
        spec.validate()
        PIT_TABLES[table_name] = spec
        cls.__pit_spec__ = spec
        return cls
    return wrap

@pit_table(
    table_name="fact_capital_flow_pit_daily",
    primary_key=("stock_code", "trade_date"),
    pit_fields={
        "event_date": "trade_date",
        "announce_date": "holder_count_q_report_date",
        "available_date": "trade_date",
        "built_at": "built_at",
    },
    expected_freshness_hours=48,
    owner="backend/scripts/backfill_capital_flow_pit.py",
    source_tables=("fact_lhb_event", "fact_executive_trade_event", "fact_holder_count_period"),
)
class FactCapitalFlowPIT:
    pass
```

### 4.b Schema evolution protocol

```python
# backend/migrations/20260517_001__add_table_registry.py
from __future__ import annotations

def up(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mart_table_registry (
            database_name TEXT NOT NULL,
            table_name TEXT NOT NULL,
            layer TEXT NOT NULL,
            owner TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            pit_fields_json TEXT,
            expected_freshness_hours INTEGER,
            built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (database_name, table_name)
        )
    """)

def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS mart_table_registry")
```

```python
# backend/scripts/run_migrations.py
from __future__ import annotations

import importlib.util
from pathlib import Path

def run_migrations(conn, migrations_dir: str = "backend/migrations") -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mart_schema_migration (
            migration_id TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    applied = {r[0] for r in conn.execute("SELECT migration_id FROM mart_schema_migration").fetchall()}
    for path in sorted(Path(migrations_dir).glob("*.py")):
        migration_id = path.stem
        if migration_id in applied:
            continue
        spec = importlib.util.spec_from_file_location(migration_id, path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        if not hasattr(mod, "up") or not hasattr(mod, "down"):
            raise RuntimeError(f"{path}: migration must define up(conn) and down(conn)")
        mod.up(conn)
        conn.execute("INSERT INTO mart_schema_migration (migration_id) VALUES (?)", [migration_id])
        conn.commit()
```

### 4.c Row lineage tracking

```python
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

def source_row_hash(row: dict) -> str:
    payload = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def add_lineage(row: dict, *, source_table: str) -> dict:
    out = dict(row)
    out["source_table"] = source_table
    out["source_row_hash"] = source_row_hash(row)
    out["built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out

LINEAGE_COLUMNS_SQL = """
source_table TEXT NOT NULL,
source_row_hash TEXT NOT NULL,
built_at TIMESTAMP NOT NULL
"""
```

### 4.d SLA/freshness contract implementation

```python
from __future__ import annotations

from datetime import datetime, timezone

FRESHNESS_DDL = """
CREATE TABLE IF NOT EXISTS mart_data_freshness_alert (
    alert_id TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    date_column TEXT NOT NULL,
    max_data_time TEXT,
    freshness_hours DOUBLE,
    expected_freshness_hours INTEGER NOT NULL,
    severity TEXT NOT NULL,
    checked_at TEXT NOT NULL
);
"""

def check_table_freshness(
    conn,
    *,
    table_name: str,
    date_column: str,
    expected_freshness_hours: int,
) -> dict:
    conn.execute(FRESHNESS_DDL)
    row = conn.execute(
        f'SELECT CAST(MAX("{date_column}") AS TEXT) FROM "{table_name}"'
    ).fetchone()
    max_text = row[0] if row else None
    checked_at = datetime.now(timezone.utc)
    severity = "unknown"
    hours = None
    if max_text:
        dt = datetime.fromisoformat(str(max_text)[:19].replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = (checked_at - dt).total_seconds() / 3600
        severity = "ok" if hours <= expected_freshness_hours else "critical"
    alert_id = f"{table_name}:{date_column}:{checked_at.date().isoformat()}"
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_data_freshness_alert
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            alert_id,
            table_name,
            date_column,
            max_text,
            hours,
            expected_freshness_hours,
            severity,
            checked_at.isoformat(timespec="seconds"),
        ],
    )
    conn.commit()
    return {
        "table_name": table_name,
        "max_data_time": max_text,
        "freshness_hours": hours,
        "expected_freshness_hours": expected_freshness_hours,
        "severity": severity,
    }
```

### 4.e Auto-cleanup deprecated cols/tables

```python
from __future__ import annotations

from datetime import date

def cleanup_deprecated_tables(conn, registry: list[dict], *, execute: bool = False) -> list[dict]:
    plan: list[dict] = []
    today = date.today().isoformat()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mart_data_deletion_record (
            deletion_id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL,
            object_name TEXT NOT NULL,
            reason TEXT NOT NULL,
            execute_requested BOOLEAN NOT NULL,
            recorded_at TEXT NOT NULL
        )
    """)
    for item in registry:
        if item.get("mode") != "deprecated":
            continue
        object_name = item["table"]
        grace_until = item.get("grace_until")
        if grace_until and today <= grace_until:
            continue
        deletion_id = f"deprecated_table:{object_name}"
        plan.append({"object_type": "table", "object_name": object_name, "execute": execute})
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_data_deletion_record
            VALUES (?, 'table', ?, 'deprecated_after_grace_period', ?, CURRENT_TIMESTAMP)
            """,
            [deletion_id, object_name, execute],
        )
        if execute:
            conn.execute(f'DROP TABLE IF EXISTS "{object_name}"')
    conn.commit()
    return plan
```

---

## 5. 根因 + 检验 mapping

| 根因类 | 历史反例 | Root cause | 已部署检查 | 缺失检查 (待加) | 自动化实现方案 |
|---|---|---|---|---|---|
| PIT/leakage | stage_opt latest snapshot; v3 `inst_quality_*`; inclusive cutoff | 训练/回测时缺 `available_date <= signal_date` 强 contract | `audit_pit_integrity.py`, `audit_registry_feature_pit.py`, `final_holdout_freeze.py`, `feature_join_v4` PIT 注释 | decorator registry 强制 pit_fields; CI 禁 latest/inclusive/fallback 入训练 | pytest + pre-commit grep |
| Sync/freshness | `tdxhub_quote` watermark 停 `2026-04-30`, live 数据缺失 | watermark 有记录无 alert; smart plan 可被旧 snapshot 误导 | `source_watermarks.py`, `refresh_source_watermarks.py`, updater dynamic budget | freshness SLA alert + paper/live block | pytest 查 watermark lag; nightly CI |
| Feature noise | 31 Phase4 features 只有 `mcap_decile`/`lhb` 有效 | 新 feature 上线没有 ROI gate | `AUDIT_2026_05_17.md`, `run_feature_ablation.py`, `rank_ic.py` | var~0 + coverage + 100K Spearman + SHAP gate | pre-train CI |
| Optuna slow | v3 dict-records 24-day ETA | 每 trial 重切窗/重复 dict scan, pruner 没 report | v4 PreparedPanel, MedianPruner, per-trial persist | perf baseline regression gate, GCP job ETA gate | pytest/perf budget |
| DuckDB single writer | 4-parallel grid lock; commits `417e6b98`, `424ae6f7` | 多进程直接写同一 DuckDB | `pipeline_lock.py`, `--no-persist`, `shard_runner.py` reducer | 禁 worker 写 DB, 只能 artifact -> reducer | pre-commit AST/grep |
| Version sprawl | feature_join v3/v3_ext/v4; paper_sim loaders ml/hybrid/tiered | 没有 version owner/deprecation registry | `schema_versions.py`, `data_deprecation.py` | version registry: owner/mode/replaced_by/drop_after | CI 查同族版本数 |
| God file | `routers/updater.py` 5034 行/111 funcs | HTTP、pipeline、sync、budget、status 混在一个模块 | 部分 helpers 已抽 services | max-lines/max-funcs check; 拆 router | pre-commit complexity |
| Test helper antipattern | `conftest.py` 把 `backend/tests` 插入 sys.path, 大量 `from conftest import duck_mem` | 测试 helper 变成隐式模块, 易被生产误 import | 无 production import tests 证据 | 禁 `sys.path` 加 tests; helper 迁移到 `backend/testing` | pre-commit grep |
| Config fragmentation | 22 YAML, 12 个 paper_sim 变体 | 无 config registry/hierarchy/promotion 状态 | 各 loader 自己读 YAML | registry + base/overlay/experiment + expiry | CI registry validator |

### 5.1 精简检查代码片段

```python
# R1 PIT/leakage pytest
def test_training_tables_have_pit_fields(table_registry):
    for item in table_registry:
        if item["layer"] in {"fact", "mart"} and item["mode"] == "prod":
            assert "available_date" in item["pit_fields"] or "signal_date" in item["pit_fields"]
            assert "built_at" in item["pit_fields"]
```

```python
# R2 freshness pytest
def test_tdxhub_kline_watermark_not_stale(conn):
    row = conn.execute("""
        SELECT last_data_date FROM mart_data_source_watermark
        WHERE data_domain='kline_daily' AND source_name='tdxhub_quote'
    """).fetchone()
    assert row and row[0] >= "2026-05-15"
```

```python
# R3 feature ROI pytest
def test_no_constant_new_feature_group(feature_audit):
    bad = [r for r in feature_audit if r["coverage_pct"] < 0.2 or r["variance"] <= 1e-12]
    assert not bad, bad
```

```python
# R4 Optuna perf pytest
def test_optuna_trial_perf_budget(perf_summary):
    assert perf_summary["duration_per_trial_s"] <= perf_summary["baseline_per_trial_s"] * 1.25
    assert perf_summary["trial_report_count"] > 0
```

```python
# R5 DuckDB single-writer pre-commit snippet
import re, sys, pathlib
bad = []
for p in pathlib.Path("backend").rglob("*.py"):
    s = p.read_text(encoding="utf-8")
    if "duckdb.connect" in s and "read_only=True" not in s and "pipeline_lock" not in s:
        bad.append(str(p))
if bad:
    sys.exit("DuckDB writer must use pipeline_lock/reducer: " + ", ".join(bad))
```

```python
# R6 version sprawl pytest
def test_feature_join_versions_have_registry(version_registry):
    active = [v for v in version_registry if v["family"] == "feature_join" and v["mode"] != "deprecated"]
    assert len(active) <= 2
    assert all(v.get("owner") and v.get("replaced_by") is not None for v in active)
```

```python
# R7 god file pre-commit snippet
from pathlib import Path
for p in Path("backend/routers").glob("*.py"):
    lines = p.read_text(encoding="utf-8").splitlines()
    funcs = sum(1 for x in lines if x.startswith("def ") or x.startswith("async def "))
    if len(lines) > 1200 or funcs > 40:
        raise SystemExit(f"{p}: too large ({len(lines)} lines, {funcs} funcs)")
```

```python
# R8 tests helper antipattern grep
import pathlib, sys
bad = []
for p in pathlib.Path("backend").rglob("*.py"):
    s = p.read_text(encoding="utf-8")
    if "sys.path.insert" in s and "tests" in s:
        bad.append(str(p))
    if str(p).startswith("backend/services") and ("from tests" in s or "import tests" in s):
        bad.append(str(p))
if bad:
    sys.exit("test helper antipattern: " + ", ".join(bad))
```

```python
# R9 config registry CI
def test_all_backend_yaml_registered(config_registry):
    registered = {x["path"] for x in config_registry["entries"]}
    actual = {str(p) for p in Path("backend/config").glob("*.y*ml")}
    assert actual <= registered
```

---

## 6. Skill 候选 + 模板

### 6.a `/data-integrity-audit`

```markdown
# Skill: /data-integrity-audit

## 触发条件
- 用户怀疑数据不完整、同步滞后、PIT 污染、coverage 异常。
- 改动 `raw_` / `fact_` / `mart_` 表构建逻辑后。
- paper/live/Optuna 结果异常好或异常差。

## 执行步骤
1. 用 read-only DuckDB 连接列出目标库表、行数、max(date/trade_date/signal_date/built_at)。
2. 检查 `mart_data_source_watermark` 中核心源 freshness: kline_daily、financial_gpcw_8q、holders_top10_float、lhb_daily。
3. 对训练 feature panel 做 coverage audit: 每列 non-null ratio、zero ratio、distinct count。
4. 对数值列做 var~0 detect: 方差 <= 1e-12 或 distinct<=1 标记 CONST。
5. 对所有入模列抽样最多 100K 行, 计算 Spearman 与 label 的相关性。
6. 检查 fallback lineage: `*_is_fallback`, `source_tier`, `confidence_level` 比例。
7. 输出 blocker/warn/pass 三档, blocker 必须给表名、列名、样本 SQL。

## 验证清单
- [ ] 所有核心表有 row_count 和 max_data_date。
- [ ] watermark lag 未超过 SLA。
- [ ] 入模列无未解释的 CONST/noise。
- [ ] fallback 比例有阈值和 owner。
- [ ] 输出含可复跑 SQL。

## 反例 / 禁止
- 禁止只看 row_count 不看日期水位。
- 禁止把 `built_at` 当作业务可用日期。
- 禁止用 latest snapshot 判断历史训练数据安全。
- 禁止发现 blocker 后继续 promote model。
```

### 6.b `/feature-engineering-roi`

```markdown
# Skill: /feature-engineering-roi

## 触发条件
- 新 feature group 准备进入 `mart_p0a_feature_label_panel_*`。
- LGBM/RankIC 没改善, 需要判断 feature 是否有边际价值。
- 用户要求删 CONST/noise 或评估 Phase 4/Phase 5 特征。

## 执行步骤
1. 从目标 panel read-only 抽样最多 100K 行, 固定 seed=42。
2. 对每个候选列计算 coverage、distinct_count、variance、zero_ratio。
3. 对每个候选列计算 Spearman(feature, label), label 默认 `fwd_cost_after_20d`。
4. 训练轻量 LGBM baseline, 输出 gain importance top/bottom。
5. 如已存在模型 artifact, 读取 SHAP 或 permutation importance 排名。
6. 规则: CONST、coverage<20%、abs(spearman)<0.005 且 SHAP bottom 30% 的列默认淘汰。
7. 输出 KEEP / DROP / SHADOW 三类, DROP 必须列出证据数字。

## 验证清单
- [ ] 样本 SQL 可复跑。
- [ ] 每个 feature group 有 group-level best abs(corr)。
- [ ] `mc_decile`、`lhb_*` 等已知有效列不能误删。
- [ ] 所有 DROP 列有方差/coverage/corr 证据。

## 反例 / 禁止
- 禁止只凭 feature importance 保留高泄漏列。
- 禁止只看训练集 IC。
- 禁止把 NULL fill 之后的 0 当真实信号。
- 禁止无审计直接把新特征接入 prod panel。
```

### 6.c `/parallel-grid-runner`

```markdown
# Skill: /parallel-grid-runner

## 触发条件
- 需要在 GCP 或本机跑多模型/多 horizon/多 feature set grid。
- Optuna / paper_sim / feature ablation 需要并行。
- DuckDB 出现 lock conflict 或 writer timeout。

## 执行步骤
1. 固定 input snapshot: 复制或下载 DuckDB 到每个 worker 的本地只读路径。
2. 生成 manifest: run_id、git_sha、config_hash、snapshot_hash、shards。
3. 采用 4-parallel x N-cores 设计: 每 worker 只读 DuckDB, 输出 `trials.jsonl` / parquet artifact。
4. worker 必须加 `--no-persist` 或等价选项, 禁止直接写共享 DuckDB。
5. reducer 单进程读取 artifacts, 顺序 upsert 到 `mart_p1_optuna_trials` 或目标表。
6. reducer 完成后跑 schema_version、row_count、best trial、RankIC gate。
7. 输出 wave summary: 成功/失败 job、耗时、best score、缺失 artifact。

## 验证清单
- [ ] worker 连接均为 read-only。
- [ ] 没有多个进程同时写同一 DuckDB。
- [ ] 每个 job 有独立 log 和 result dir。
- [ ] reducer 可幂等重跑。
- [ ] GCP/local 路径都记录 config_hash。

## 反例 / 禁止
- 禁止 worker `INSERT` 共享 DuckDB。
- 禁止把 Optuna study SQLite 和 DuckDB 结果混在同一文件锁里。
- 禁止没有 reducer 就宣称 grid 完成。
- 禁止没有 snapshot_id 的实验结果进入 registry。
```

### 6.d `/yaml-registry-add`

```markdown
# Skill: /yaml-registry-add

## 触发条件
- 新增或重命名 `backend/config/*.yaml`。
- 新增 experiment config、paper_sim variant、GCP config。
- CI 报 unregistered config。

## 执行步骤
1. 读取新增 YAML 的 top-level keys 和使用方代码。
2. 判断 mode: prod / prod_live / prod_legacy / experiment / deprecated。
3. 在 `backend/config/config_registry.yaml` 增加 path、owner、mode、pit_eligible、env_override、depends_on。
4. 若 mode=experiment, 必须加 expires_at、base_ref、promotion_gate。
5. 运行 registry validator, 确认所有 `backend/config/*.yaml` 都已登记。
6. 如 YAML 是 paper_sim 变体, 检查是否能表达为 base+overlay+experiment。
7. 输出变更摘要和验证命令。

## 验证清单
- [ ] path 存在且唯一。
- [ ] owner 是真实 import path 或脚本 path。
- [ ] depends_on 都存在。
- [ ] env_override 名称不冲突。
- [ ] experiment 有 expiry 和 promotion gate。

## 反例 / 禁止
- 禁止新增孤儿 YAML。
- 禁止用文件名表达生产/实验状态而不进 registry。
- 禁止 experiment 永不过期。
- 禁止 prod config 依赖 deprecated config。
```

### 6.e `/config-hierarchy-check`

```markdown
# Skill: /config-hierarchy-check

## 触发条件
- 迁移配置到 base/overlay/experiment 三层。
- paper_sim、Optuna、GCP experiment 运行前。
- 配置结果和预期不一致。

## 执行步骤
1. 读取 config registry, 找到目标 config_id。
2. 按 `base -> overlay -> experiment -> env_override` 进行 deep merge。
3. 检查类型冲突: dict/list/scalar 不允许隐式互换。
4. 检查未知 key: experiment 不能新增 schema 外 key。
5. 检查生产安全: prod/prod_live 不允许 `use_pit=false`、latest snapshot、fallback default 未声明。
6. 输出 resolved YAML、config_hash、diff summary。
7. 对 paper_sim/Optuna 调用原 loader dataclass 校验。

## 验证清单
- [ ] resolved config 可被原 loader 解析。
- [ ] config_hash 写入 run metadata。
- [ ] env_override 只覆盖允许的 leaf key。
- [ ] experiment 未过期。
- [ ] 无 PIT 冲突。

## 反例 / 禁止
- 禁止 shallow merge 覆盖整段 dict 导致默认丢失。
- 禁止同一字段在 overlay 和 experiment 给出不同类型。
- 禁止生产运行使用未登记 experiment config。
- 禁止手工复制 12 份近似 YAML 继续扩散。
```

---

## 7. Memory feedback 候选

### 7.a `feedback_continuous_no_questions.md`

```markdown
# feedback_continuous_no_questions

## 问题描述
用户在 ChunkyMonkey 项目中通常要求持续自主推进, 不希望因为可自行验证的小问题被频繁打断问澄清。

## 根因
过早询问会中断长链路调试/审计节奏, 特别是数据完整性、Optuna、PIT、paper_sim 这类需要先 grep/find/duckdb read-only 验证的任务。

## 规则
默认先执行只读审计和可逆文档输出; 遇到实现细节缺省时按仓库现有模式保守决策。只有涉及删除数据、改生产源、外部凭据、付费资源或不可逆操作时才停下确认。

## 反例 commit hash (如有)
N/A
```

### 7.b `feedback_data_source_constraints.md`

```markdown
# feedback_data_source_constraints

## 问题描述
行情同步问题不能假设换源即可解决: tdxhub server pool 会 stale, akshare push2his.eastmoney.com 在用户网络下已确认 block。

## 根因
数据源可达性是生产约束, 不是代码 retry 能完全解决。watermark stale 若无 alert, 会让 paper/live 继续消费旧数据。

## 规则
涉及 kline/tdxhub/akshare 时, 先查 `mart_data_source_watermark` 和 `market.price_kline_tdxhub` max(date)。若 tdxhub stale 或 push2his blocked, 明确标注“数据源约束”, 不把问题包装成普通代码 bug。

## 反例 commit hash (如有)
d3698035
```

### 7.c `feedback_optuna_perf_baseline.md`

```markdown
# feedback_optuna_perf_baseline

## 问题描述
Optuna 不能靠直觉估时。Mac 本地曾出现 v3 约 60min/trial 量级, 200 trials 推到 24-day ETA; v4/GCP 必须先用真实 trial 速度校准。

## 根因
v3 每 trial 重复 DataFrame to dict / 重切窗 / pruner 未 report, 导致复杂度远超预期。

## 规则
任何 Optuna 大跑前先跑 1-3 trial benchmark, 记录 per_trial_s、n_windows、feature_cols、row_count。GCP scaling 用“worker 只读 snapshot + artifact + reducer 单写”设计, 不允许多个 worker 直接写共享 DuckDB。

## 反例 commit hash (如有)
417e6b98, 424ae6f7
```

### 7.d `feedback_duckdb_concurrency.md`

```markdown
# feedback_duckdb_concurrency

## 问题描述
DuckDB 是 single-writer。并行 grid、paper_sim、Optuna、feature build 同时写同一 `.duckdb` 会 lock conflict 或隐式失败。

## 根因
DuckDB 适合多读单写, 不适合多个 Python worker 同时 `INSERT/DDL` 同一个数据库文件。

## 规则
并行任务必须 read-only snapshot; worker 输出 jsonl/parquet/SQLite-local artifact; reducer 单进程写 DuckDB。需要直接写生产 DuckDB 的 pipeline 必须通过 `pipeline_lock` 或串行执行。

## 反例 commit hash (如有)
417e6b98, 424ae6f7
```

### 7.e `feedback_feature_audit_gate.md`

```markdown
# feedback_feature_audit_gate

## 问题描述
Phase 4 一次接入 31 个特征, 审计后只有 `mc_decile` 和 `lhb` 真有用, 多个 feature group 是 CONST/noise。

## 根因
新 feature group 缺少上线前 ROI gate, 只验证 PIT/构建成功不等于有预测价值。

## 规则
每个新 feature group 上线前必须跑 100K sample Spearman、coverage、var~0、SHAP/importance 或 ablation。CONST、coverage 低、abs(corr) 低且无业务强证据的列只能 SHADOW, 不进 prod training。

## 反例 commit hash (如有)
d3698035
```
