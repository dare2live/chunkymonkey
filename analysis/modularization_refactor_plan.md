# ChunkyMonkey 模块化重构 Plan (read-only, 不实施)

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


> 起草: 2026-05-19 深夜调研, 用户原话 "模块化可复用可扩展"
> 范围: backend/services + scripts + routers + config; 不涉及 ml model / front-end
> 性质: Plan only — 不 commit / 不改代码 / 不阻塞 retrain + paper_sim 主线
> 配套: codegraph-architecture-audit skill, pit-audit / post-fix-audit / parallel-grid-runner 已有 skill, 不替代

---

## 0. 调研事实 (用 grep + wc 实测, 不估)

| 指标 | 实测值 | 备注 |
|---|---:|---|
| backend/services *.py 文件 | 110 (顶层) + 27 包 (含子目录) | 重 god-module 集中在顶层 |
| backend/scripts *.py 文件 | 229 | 60 个 build_* / 37 run_* / 22 audit_* / 10 validate_* / 10 backfill_* |
| backend/routers *.py 文件 | 17 (含 __init__) | 142 个 @router endpoint |
| backend/tests *.py 文件 | 302 | 1402+ 个 passing tests (baseline) |
| backend/config *.yaml 文件 | 25 | 含 9 个 paper_sim_*.yaml + optuna / feature_registry / panel_manifest |
| `from services.db import` (scripts) | 145 次 | god-module fan_in #1 |
| `from services.pipeline_manifest` | 45 | fan_in #2 |
| `from services.schema_versions` | 41 | fan_in #3 |
| `from services.market_db` | 30 | fan_in #4 |
| `from services.duck_adapter` | 24 (scripts) + 85 (全 backend) | 跨层公用 |
| `duckdb.connect(...)` 原生调用 | 96 files | 没走 duck_adapter 的 41% |
| `ATTACH DATABASE` 调用 | 41 files | 重复 boilerplate |
| `argparse.ArgumentParser` 调用 | 142 files | 几乎每个 script 重复模板 |
| `logging.getLogger("...")` 调用 | 193 files | 没统一 logger config |
| `if __name__ == "__main__":` entry | 212 files | scripts/ 90%+ 是 main entry |
| `optuna.create_study` 调用 | 13 files | 各 study 自己写 setup |
| `walk_forward` 引用 | 52 files (scripts) | 已部分中央化于 services/optimization/walk_forward.py |

---

## 1. 现状盘点表 — Top 重灾区文件 (LOC × fan_in)

按 "LOC × 跨模块影响" 排序, 数字均 grep + wc 实测:

| Rank | 文件 | LOC | fan_in | DDL 数 | 风险等级 | 重构候选 |
|---:|---|---:|---:|---:|---|---|
| 1 | services/workbench_read.py | 4516 | 20 | - | P0 read 巨石 | 拆 stock_view / pool_view / kpi_view / detail_view |
| 2 | services/data_quality.py | 4249 | - | - | P0 audit 巨石 | 拆 row_quality / coverage / consistency / lineage |
| 3 | services/scoring.py | 2684 | - | - | P0 scoring 巨石 | 拆 raw_score / composite / dispatcher |
| 4 | services/db.py | 2478 | **145** | 230 CREATE/ALTER | **P0 头号 god** | 拆 schema_core / schema_marts / schema_migrations / connection / domain DDL 下沉 |
| 5 | scripts/build_feature_panel_duck.py | 2291 | - | - | P0 build 巨石 | 拆 panel_builder framework + DAG step |
| 6 | services/signals_v2.py | 1999 | - | - | P1 signal 巨石 | 按 signal family 拆 |
| 7 | services/audit.py | 1749 | 8 | - | P1 audit 中等 | 跟 data_quality 合并到 audit_lib |
| 8 | services/financial_client.py | 1689 | - | - | P1 client | 按 endpoint 类型拆 (period / ratio / cashflow) |
| 9 | scripts/build_temporal_synergy_research.py | 1643 | - | - | P1 | 走 panel_builder framework |
| 10 | scripts/run_optuna_model_stability_search.py | 1629 | - | - | P1 | optuna_runner 复用 |
| 11 | scripts/run_optuna_synergy_search.py | 1541 | - | - | P1 | optuna_runner 复用 |
| 12 | scripts/validate_synergy_policy_mark_to_market.py | 1431 | - | - | P1 | walk_forward 模板 |
| 13 | services/tdx_f10_extra_client.py | 1403 | - | - | P1 | 按 F10 section 拆 |
| 14 | scripts/train_multidim_model.py | 1372 | - | - | P1 | 走 trainer framework |
| 15 | routers/updater.py | **5034** | - | - | P0 router 巨石 | 16 endpoints 拆 5 个 router (smart / sync / status / cron / health) |

附:
- `services/market_db.py` (728 LOC, fan_in 77) — 重构候选 (拆 read / write / sync_state)
- `services/pricing_policy.py` (869 LOC, fan_in 19) — 中等 (跟 pricing_sql 合并)
- `services/schema_versions.py` (456 LOC, fan_in 72) — 接口稳定不动, 内部组织即可
- `services/pipeline_manifest.py` (215 LOC, fan_in 62) — 接口稳定不动
- `services/duck_adapter.py` (268 LOC, fan_in 85) — **接口稳定**, 但需新加 context manager + read_only / writer 显式 (见 P0-B)

---

## 2. 重复代码模式识别 (grep 实测)

### 2.1 DuckDB connect / ATTACH boilerplate
- **96 files** 直接调 `duckdb.connect(...)` (没走 duck_adapter)
- **41 files** 重复 `ATTACH DATABASE 'xxx' AS yyy` boilerplate
- **83 处** 显式 `read_only=True` (说明开发者意识到要分流, 但缺统一 helper)
- **冲击**: parallel-grid-runner skill 教训 (2026-05-17 Wave 1) — 单 writer lock 不能并发, 必须 reader/writer 显式. 当前 96 个 raw connect 是 lock 隐患.

### 2.2 argparse / logging setup boilerplate
- **142 files** 重复写 `argparse.ArgumentParser` + `add_argument`
- **193 files** 重复 `logger = logging.getLogger("...")`
- **212 files** `if __name__ == "__main__":` 入口模板 (parse_args / setup_logger / run / sys.exit)
- 估每 file 重复 30-50 行 boilerplate → ~6000+ 行重复代码

### 2.3 Optuna study + walk_forward
- **13 files** 调 `optuna.create_study` (各自写 storage / sampler / pruner / load_if_exists)
- **52 scripts** 引用 walk_forward (已部分中央化 services/optimization/walk_forward.py — 好做法应推广)
- governance.enforce_pre_optimize / enforce_pre_insert 已有中央层, 但**新加 study 时容易忘走**

### 2.4 Feature panel build pipeline (alpha158 → label → v3 → v4)
- 6 panel builder scripts (build_alpha158_duck / build_p0a_feature_panel_v3 / v4 / build_feature_panel_duck / build_hybrid_feature_panel / build_candidate_feature_panel)
- 30 个 `build_*panel*.py` 或相关
- 每个 builder 重写: source 读取 / signal join / label join / qfq adjust / output 写入 / pipeline_manifest 记录
- **config/panel_pipeline_manifest.yaml 已是 DAG 定义** (Codex HIGH 2 已设计) — 实施层是空缺

### 2.5 Sync ETL pattern (data_sources/sources/)
- 已有 `services/data_sources/{base,registry,sources/}` 框架 (3 source: tdxhub / akshare / aif10)
- 但 12 个独立 client (`*_client.py`: tdx_affair / tdx_f10_extra / tdx_industry / financial / capital / lhb / qfii / block / institution_survey / xdxr / akshare / aif10_capability) 各写 retry / rate-limit / parse / persist 逻辑
- 部分 client 未走 base.py 统一接口

### 2.6 Audit script 公共逻辑
- 22 个 `audit_*.py` scripts + 8 个 `services/audit.py` 类函数 (refresh / build_smart_plan / get_quality_audit)
- 重复: DuckDB connect → table inventory → SQL 阈值检查 → markdown report 输出
- 没有 `audit_base.py` lib

### 2.7 Router endpoint 重复
- 17 routers / 142 endpoints
- `updater.py` 5034 LOC × 16 endpoints = 平均 314 LOC/endpoint (远超 FastAPI 单 endpoint 50-80 LOC 经验线) — 应是 service layer 直接搬到 router
- `institution.py` 1301 LOC × 30 endpoints — 30 个查询接口都自己组 SQL + shape response
- 缺统一的 `routers/_common/` (auth / db_conn / pagination / response_shape)

### 2.8 Config 散点
- 9 个 `paper_sim_*.yaml` (各策略一份) — 字段大量重复
- `optuna_config.yaml` / `model_search.yaml` 部分 overlap
- `feature_registry.yaml` + `panel_pipeline_manifest.yaml` + `field_dictionary.yaml` 三套 metadata 缺统一 schema
- 没用 pydantic / dataclass validate, 全 dict 散读

---

## 3. 模块化重构方案 (8 项, P0/P1/P2 分级)

### P0-A. db.py 拆分 (头号 god-module, 230 DDL + 145 fan_in)

> 跟 spec `analysis/chunkymonkey_architecture_audit_20260517.md` C3 节方向一致.

**拆分目标 (新文件结构, services/db_core/)**:

| 子模块 | 职责 | 估 LOC | 上游 |
|---|---|---:|---|
| `db_core/connection.py` | `DB_PATH` 常量 / `get_conn()` / 重试逻辑 | ~80 | duck_adapter |
| `db_core/schema_core.py` | `init_db()` 主入口 + raw_* / dim_* / fact_core 基础 DDL | ~600 | connection |
| `db_core/schema_marts.py` | mart_* 表 DDL (40+ marts 集中) | ~700 | connection |
| `db_core/schema_migrations.py` | ALTER TABLE / ADD COLUMN / 索引迁移 + bump_version | ~400 | connection + schema_versions |
| `db_core/modules.py` | `get_enabled_modules` / `_table_columns` 工具 | ~100 | connection |
| `db_core/domain_holders.py` | holder 相关 raw/fact/dim DDL (~9 表) | ~300 | connection |
| `db_core/domain_tdx_gpcw.py` | gpcw 自动 feature 链 (~7 表) | ~300 | connection |

**保留路径**: `services/db.py` 改 façade (`from db_core.connection import *` 等), 维持 145 个 fan_in 的 import 不破.

**风险**:
- 145 个 import 中, 99% 调 `get_conn` / `init_db` — façade 全 re-export 即可不破
- 余 ~5 个 import 调内部 helper (`_table_columns`) — 显式公开成 `table_columns()` 即可

**ETA**: 5-7 个工作日 (90% 是 SQL 文本搬迁 + 测试; 不改业务逻辑)

**Acceptance**:
- [ ] 1402 tests 全 pass (无任何调用方需改 import)
- [ ] `services/db.py` < 200 行 (纯 façade)
- [ ] 7 个新文件每个 < 800 行
- [ ] codegraph hotspot 上 db.py fan_in 从 145 拆到分散 (头号 ≤ 60)
- [ ] init_db 启动时间不增 (基准测试)

---

### P0-B. duck_adapter 强化 (fan_in 85, 接口稳定 → 加 context manager)

> 跟 parallel-grid-runner skill 教训配套 (DuckDB single-writer lock).

**当前问题**:
- `connect(db_path, read_only, attach)` 是函数式, 调用方各自 try/finally close
- 96 files 直接调 `duckdb.connect` 绕过 duck_adapter (Optuna / pandas / ad-hoc 脚本)
- `read_only` flag 各调用方手动传, 易写错 (parallel job 漏传 = lock 灾难)

**强化设计 (不改现有 connect, 加新 helper)**:

| 新接口 | 用途 | 替换 |
|---|---|---|
| `with read_only_conn(path) as conn:` | 只读 query (parallel-safe) | `duckdb.connect(path, read_only=True)` |
| `with writer_conn(path, timeout=30) as conn:` | 排他写 (单 writer 锁) | `duckdb.connect(path)` + manual close |
| `with attached_conn(path, attach={'mkt': ...}) as conn:` | 跨库 query | `ATTACH` boilerplate |
| `table_exists(conn, name)` / `table_columns(conn, name)` | 通用 introspect (db.py + pricing_policy 都重写过) | 3 处重复 |
| `safe_alter_add_column(conn, table, col, type)` | IF NOT EXISTS 兼容包装 | 散在 schema_migrations 各处 |

**新 helper 不替换老 `connect()`**, 主动迁移分阶段:
- Phase 1 (1 周): 新 scripts / 新 PR 必须用新接口
- Phase 2 (2 周): codegraph 找出 96 个 raw connect, 批量迁移 (PR by source 类别)
- Phase 3 (待 retrain 稳定后): 老 `connect()` 加 deprecation warning, 1 季度后 remove

**风险**: low — 纯 additive, 不破现有 85 个 fan_in

**ETA**: 主体 3-5 工作日; 全量迁移 1 月 (背景任务, 不阻主线)

---

### P0-C. updater.py router 拆分 (5034 LOC 单 router 异常)

**拆分**:
- `routers/updater_smart.py` — POST /api/inst/update/smart + build_smart_plan (~1500 LOC)
- `routers/updater_sync.py` — 主动 sync 触发 (~1000)
- `routers/updater_status.py` — sync status / lineage 查询 (~800)
- `routers/updater_cron.py` — cron 计划 / 状态 (~800)
- `routers/updater_health.py` — health / audit snapshot (~600)
- `routers/_common/db.py` — DB conn / dependency (Depends FastAPI 风格)
- `routers/_common/response.py` — 统一 response shape / pagination

**风险**: medium — 142 endpoints 客户端 (前端 / cron / Codex) 调用路径不变 (URL 不改), 但内部 import 需更新

**ETA**: 3-4 工作日

---

### P1-A. sync_*.py / *_client.py 共用 base class

> 12 个 `*_client.py` (1689 LOC financial / 1403 LOC tdx_f10_extra / ...) 各写 retry / rate-limit / parse / persist.
> CLAUDE.md Rule 3 数据源可信度 → 3 个 tier 区分.

**现状已有**: `services/data_sources/{base,registry,clients_registry}.py` 框架 (但只 3 个 source).

**扩展设计**:

```
services/data_sources/
  base.py                # 已有, SyncBase abstract (fetch / parse / validate / persist / cleanup)
  registry.py            # 已有
  source_tiers.py        # 新: TIER_1_TDXHUB / TIER_2_AKSHARE / TIER_3_OTHER (跟 Rule 3 对齐)
  sources/
    tdxhub.py            # 已有
    akshare.py           # 已有
    aif10.py             # 已有
    tdx_affair.py        # 新: 把 services/tdx_affair_client.py 迁过来
    tdx_f10_extra.py     # 新: 拆 tdx_f10_extra_client.py 1403 LOC
    tdx_industry.py      # 新
    financial.py         # 新: 拆 financial_client.py 1689 LOC
    capital.py / lhb.py / qfii.py / block.py / xdxr.py / institution_survey.py
```

**SyncBase 抽象**:
- `fetch(symbols, date_range) -> list[RawRecord]` — source 实现, 含 retry/rate-limit hook
- `parse(raw: RawRecord) -> ParsedRecord` — JSON / HTML → dict
- `validate(parsed) -> bool` — 字段完整性 / PIT 时序
- `persist(parsed, conn) -> int` — UPSERT 到 raw_/fact_ 表, 走统一 batch_id / pipeline_manifest
- `cleanup(batch_id)` — 失败回滚 / dedup

**风险**: medium — 12 个 client 的调用方 (sync scripts) 需更新. 走 1 client = 1 PR 增量迁移.

**ETA**: 单 client 1-2 天 × 12 = 1 月 (背景, 不阻主线; tdxhub / akshare 已迁过, 剩 9 个)

---

### P1-B. Panel builder framework (跟 panel_pipeline_manifest.yaml 配套)

> `backend/config/panel_pipeline_manifest.yaml` 已是 DAG (Codex HIGH 2 设计), 实施层空缺.
> 6 个 panel builder + 2291 LOC build_feature_panel_duck.py 重写 source/join/output.

**框架设计**:

```
services/panel_builder/
  __init__.py
  runner.py        # 主 runner: 读 manifest → 拓扑排序 → 执行 step
  step_base.py     # PanelStep abstract: input_relations / sql_template / output_table / validate
  step_registry.py # 注册 step type (raw_load / signal_join / label_join / qfq_adjust / persist)
  templates/
    alpha158_features.sql.j2
    label_horizon_returns.sql.j2
    v3_panel_join.sql.j2
    v4_panel_join.sql.j2
  validators.py    # PIT validate / NULL check / row count gate
```

**调用方**:
- `scripts/build_p0a_feature_panel_v3.py` 100+ 行 → 10 行 (load yaml + 跑 runner)
- `scripts/build_p0a_feature_panel_v4.py` 同
- 新 panel 加 yaml 1 个 step 块, 不写 python

**ETA**: framework 1 周 + 迁移 6 builder 1 周 = 2 周

**Acceptance**:
- [ ] v3 panel 通过新 framework 跑出来 byte-equal 现有 panel (5 列 NULL diff 容差)
- [ ] v4 panel 同
- [ ] 新加 v5 panel 只改 yaml 即可

---

### P1-C. Optuna runner 框架

> 13 个 optuna study 各自重写 storage / sampler / pruner / governance hook.

**框架设计 (基于 services/optimization/ 已有层)**:

```
services/optimization/
  walk_forward.py        # 已有
  governance.py          # 已有
  composite.py / config.py / constraints.py / ...
  optuna_runner.py       # 新: OptunaRunner class
    - __init__(study_name, search_space, objective_fn, sampler, pruner)
    - run(n_trials, n_jobs) — 跑 study + governance gate
    - best() / best_oos() — 拿 best params (governance.enforce_pre_insert 守门)
    - persist(conn, table) — 写入 mart 表
```

**调用方**:
- `scripts/run_optuna_synergy_search.py` 1541 LOC → 200 LOC (只写 search space + objective)
- 同理 run_optuna_model_stability_search.py 1629 LOC

**配套**: parallel-grid-runner skill — runner 必须显式 `read_only_conn` for read + `writer_conn` for reducer + N>1 worker 走独立 study_name + 串行 reducer.

**ETA**: framework 1 周 + 迁移 13 个 study 1-2 周 = 2-3 周

---

### P1-D. Router common layer

> 17 routers / 142 endpoints 大量重复 DB conn / response shaping.

**新设计**:

```
routers/_common/
  __init__.py
  db.py            # FastAPI Depends: db_conn / market_conn / etf_conn / read_only_conn (auto close)
  pagination.py    # Pagination(limit, offset, sort_by) + 统一 response.meta.pagination
  response.py      # 统一 ApiResponse[T] schema (data / meta / errors)
  auth.py          # (如未来加 auth) Depends(get_current_user)
  errors.py        # 统一 HTTPException → 4xx/5xx 响应包装
```

**P0 不动 endpoint 路径**, 只内部重构. 客户端无感知.

**ETA**: 1 周

---

### P2-A. Audit script 公共 lib

> 22 个 `audit_*.py` scripts + services/audit.py 1749 LOC + services/data_quality.py 4249 LOC.

**抽象 audit_lib**:

```
services/audit_lib/
  __init__.py
  audit_base.py     # AuditTask abstract: query / threshold / report
  inventory.py     # table inventory / row count / coverage
  thresholds.py    # 统一阈值定义 (load from yaml) + 比对工具
  reporters/
    markdown.py    # generate audit_*_report.md (统一格式)
    json_dump.py   # audit_*_results.json
    duckdb_persist.py  # 写入 mart_data_health / mart_*_audit
  diff.py          # 跑 baseline → new 对比 (用于 codegraph audit_n_plus_one diff 集成)
```

**配套 data_quality.py 拆分** (4249 LOC):
- `services/audit_lib/row_quality.py` — NULL / dup / range
- `services/audit_lib/coverage.py` — date coverage / symbol coverage / source coverage
- `services/audit_lib/consistency.py` — 跨表一致性 / xdxr 一致
- `services/audit_lib/lineage_quality.py` — 跟 data_lineage 包对齐

**ETA**: 2-3 周 (data_quality.py 拆是大头)

---

### P2-B. Config 统一入口 (services/config_loader)

> 25 yaml 散读, 没 schema validate.

**设计**:

```
services/config_loader/
  __init__.py
  schemas/                # pydantic models per yaml
    paper_sim.py
    optuna.py
    feature_registry.py
    panel_manifest.py
    pricing_label_policy.py
    pipeline_performance.py
  loader.py               # load_yaml_validated(path, schema) → instance
  merge.py                # paper_sim_<strategy>.yaml = paper_sim_base + override
  env_override.py         # CM_OPTUNA_TRIALS env var 覆盖
```

**好处**:
- paper_sim_*.yaml 9 个文件 → 1 base + 8 override (减重复)
- 启动时 validate (fail fast)
- 字段重命名 / 移除可 grep + IDE refactor (现 dict 散读完全靠 grep)

**ETA**: 2 周 (schema 编写 + 现有 load 调用方迁移)

**风险**: low — 老 yaml 路径不变, loader 加 validate 层

---

### P2-C. Paper sim driver 模块化

> `services/paper_sim/driver.py` 已是包结构 (15 子模块: selector/sizer/swap_rules/...) 比 god-module 好.
> 但 driver.py 内部主循环仍较大, 加新 strategy 时改 driver.

**改善**:
- driver.py 抽 `StrategyHook` protocol (entry / exit / size / rebalance)
- 各 strategy (ml_score / momentum / reversal / hybrid) 实现 hook
- 新策略只加 1 个 strategy 文件 + 1 个 yaml, 不改 driver

**ETA**: 1 周 (paper_sim 已半模块化, 改 driver 主循环不大)

---

## 4. 可复用 component library (新建 services/_lib/)

> Rule 1: 单次不抽象成框架. 出现 3+ 次重复才抽. 以下都 grep 实测 3+ 次.

| 库 | 内容 | 来源重复点 | 估 LOC |
|---|---|---|---:|
| `_lib/db_helpers.py` | read_only_query / write_with_lock / table_inventory / safe_alter / row_count | 见 P0-B | ~300 |
| `_lib/audit_helpers.py` | markdown_report / json_dump / threshold_table / diff_baseline | 22 audit scripts | ~250 |
| `_lib/config_helpers.py` | load_yaml_validated / merge_yaml / env_override | 12 个 yaml load 调用 | ~150 |
| `_lib/cli_helpers.py` | build_argparser(name, args) / setup_logger(name, level) / progress_bar / colored_log | 142 argparse + 193 logger | ~200 |
| `_lib/pit_helpers.py` | assert_no_temporal_leak / asof_join_sql / build_pit_filter | pit-audit skill 重复 | ~200 |
| `_lib/optuna_helpers.py` | make_study(name, sampler) / load_search_space(yaml) / governance_decorator | 见 P1-C | ~200 |

**约束**: `_lib/` 包**只依赖** stdlib + duckdb + pydantic + optuna, **不依赖 services 其他模块** (防循环 import).

---

## 5. 可扩展架构原则 (新加 alpha / strategy / audit 时的 plug-in 模板)

### 5.1 新加 alpha (e.g. 关系图谱 / 大宗交易 / SUE)

模板化 5 步:

| 步骤 | 涉及框架 | 输出 |
|---|---|---|
| 1. data backfill | `services/data_sources/sources/<new>.py` (inherit SyncBase) | raw_/fact_ 表写入 |
| 2. feature build | `services/panel_builder/` step yaml + sql template | mart_feature_<alpha> |
| 3. PIT audit | `services/audit_lib/` + pit-audit skill | audit report |
| 4. paper_sim integration | `services/paper_sim/strategy_<alpha>.py` (StrategyHook) | paper_sim run |
| 5. KPI compare | `paper_sim_kpi_compare_plan.md` 流程 | KPI table vs baseline |

不改任何核心代码, 全是 plug-in.

### 5.2 新加 strategy

- 1 个 `services/paper_sim/strategies/<name>.py` (实现 StrategyHook)
- 1 个 `backend/config/paper_sim_<name>.yaml` (override base)
- driver 自动加载 — 不改

### 5.3 新加 audit

- 1 个 `services/audit_lib/checks/<name>.py` (inherit AuditTask)
- 1 个 `scripts/audit_<name>.py` (10 行 boilerplate, 调 runner)
- codegraph-architecture-audit skill 自动桥接 (5 步流程)

---

## 6. ETA + 实施 phase + commits 估算

| Phase | 任务 | 工作日 | 并行可否 | Commit 数估 | 阻 retrain 主线? |
|---|---|---:|---|---:|---|
| 1 | P0-A db.py 拆分 | 5-7 | 单线 | 15-20 (按子模块拆) | No (read-only refactor, 行为不变) |
| 1 | P0-B duck_adapter 强化 | 3-5 | 跟 P0-A 并行 | 8-10 | No |
| 1 | P0-C updater.py router 拆 | 3-4 | 跟 P0-A 并行 | 10-12 | No (URL 不变) |
| 2 | P1-A sync base class (12 client) | 12 × 1.5 = 18 | 1 client = 1 PR | 12 | No (1 source 1 PR) |
| 2 | P1-B panel builder framework | 10 | 单线 | 15 | No (新 panel 不阻老 panel) |
| 2 | P1-C optuna runner framework | 12 | 跟 P1-B 并行 | 15 | No |
| 2 | P1-D router common layer | 5 | 跟 P1-A/B/C 并行 | 8 | No |
| 3 | P2-A audit lib + data_quality 拆 | 15 | 单线 (data_quality 大) | 20 | No |
| 3 | P2-B config_loader | 10 | 跟 P2-A 并行 | 12 | No |
| 3 | P2-C paper_sim driver hook | 5 | 跟 P2-A/B 并行 | 8 | No |
| **总** | | **70-80 工作日** | 3 phase 并行 → 6 周钟表时间 | ~125 commits | **零阻塞** |

**关键**: 每 P0/P1/P2 块跟 retrain + paper_sim 主线**独立路径**, 顺序无依赖.

---

## 7. 跟 codegraph audit infra 协同 (强制)

### 7.1 大改 PR 必跑 codegraph-architecture-audit SKILL

> SKILL.md 已 deploy: `~/.claude/skills/codegraph-architecture-audit/SKILL.md`
> 5 步流程: hotspot 抓 god → callers 找受影响 → affected 算 blast radius → 串 pit-audit/post-fix-audit/data-integrity-audit/parallel-grid-runner → 综合 verdict

每个 P0/P1 PR 必走 5 步, 输出贴 commit message.

### 7.2 audit_n_plus_one.py 新 hits diff WARN

- pre-commit hook: 当前 commit 跑 `audit_n_plus_one.py --diff baseline` → 新增 hot path WARN (不 block)
- 用 `audit_n_plus_one_results.json` 作 baseline

### 7.3 pre-commit hook codegraph diff-check (新加)

阈值 WARN-only, 不 block:
- 单 file LOC > 800 → "考虑拆分"
- 单 file imports > 30 → "fan_out 高, 可能 god-module"
- import path 跨 4+ 层 → "可能循环依赖"

输出在 commit message footer (不 reject commit).

---

## 8. 风险 + reverse 计划

### 8.1 主要风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| db.py 拆完老 import 失败 | high | façade 全 re-export + grep 验证 0 个调用方需改 |
| duck_adapter 新接口跟老 connect 并存导致混用 | medium | 1 month deprecation period, 不 force migrate |
| router 拆完前端 URL 变 | low | URL 路径不变, 只内部 import 变 |
| sync base class 迁移破 sync 数据 | high | 1 client 1 PR + 跑 data-integrity-audit skill 验 row count |
| panel builder framework byte-diff | high | 老 + 新并跑 1 周, hash diff = 0 才切 |
| optuna runner 改 study seed | critical | 必须保 seed 100% 不变, 复跑 best params byte-equal |
| audit lib 拆破 mart 写入 | medium | mart 表 DDL 不动, 只改 audit script 调用层 |

### 8.2 Reverse 计划

每 P0/P1 commit 后:
- [ ] 1402+ tests 全 pass (baseline)
- [ ] Optuna reproducibility 测试 (固定 seed 跑 3 trial, 对比 best_value byte-equal)
- [ ] paper_sim KPI 不变 (跑 1 个 strategy, ann_ret / sharpe / max_dd 全相等)
- [ ] codegraph affected 找出受影响 tests, 跑覆盖率 ≥ 95%
- [ ] 不 pass → revert PR, 不 push 主分支

**单分支 main**: 每 commit 都可 revert (Rule 8). 不开 feature branch.

---

## 9. 关键观察 + 决策 push back

### 9.1 真正的头号 god 不是 db.py, 是 workbench_read.py (4516 LOC) 和 data_quality.py (4249 LOC)
- 用户 task 描述里 db.py 2478 LOC 是 P0, 但实测 workbench_read 是 4516 + data_quality 4249
- db.py 高 fan_in (145) 影响面更广, 拆动力强; 但单纯 LOC 排序 db.py 第 4

**建议**: P0-A 优先级**保持 db.py** (fan_in 决定影响面), 但 P0 加 workbench_read 拆 (4516 LOC 是 read 巨石, 影响响应延迟)

### 9.2 updater.py 5034 LOC 是隐形 P0
- 用户 task 描述未列 (因为它在 routers/), 但 LOC 比 db.py 高 2 倍, 跟 16 endpoints 混在一起
- **建议**: 升级为 P0-C (本 plan 已含)

### 9.3 panel_pipeline_manifest.yaml 已存在, 只是没实施
- Codex HIGH 2 已设计 DAG, 但 6 个 panel builder 仍各自实现
- **建议**: P1-B 优先级提到 P0 边界 (跟 v4 panel rebuild 配套)

### 9.4 不做的事 (避免 over-engineering, Rule 1)
- **不引入** DI 框架 (FastAPI Depends 够用)
- **不引入** ORM (SQLAlchemy / Django ORM, DuckDB SQL 模板更直接)
- **不引入** 微服务拆分 (单仓库 monolith 仍合理, 团队 1 人)
- **不写 abstract factory / strategy pattern** 套娃 (Python duck typing 直接)
- **不动 ml_ranking / optimization 子包**, 它们已模块化

### 9.5 不影响主线的证据
- 全部重构 = read-only refactor (代码组织变 + 函数签名不变 + 接口稳定)
- DB schema 0 改动 (DDL 文本搬位置 ≠ schema 变更)
- yaml 0 改动 (config_loader 加 validate 层不改文件)
- 测试 baseline 1402+ 不变, 任何 PR fail 即 revert
- 跟 retrain Optuna study / paper_sim 跑 schedule **路径无 overlap** (它们走 ml_ranking / paper_sim 包, 本 plan 不动这两包内部)

---

## 10. 验收 + 决策权交还

**本 plan 是 read-only 调研**, 实施需用户:
1. 同意 P0/P1/P2 优先级 (本 plan 默认按 fan_in × LOC 排)
2. 同意 phase 时长 6-8 周
3. 决定 P0 起跑时机 (建议 v4 panel rebuild 跑完 + Optuna grid Wave 2 跑完后启, 不 race)
4. 决定是否同时启 P1 (建议: P0-A db.py 跑 1 周稳定后启 P1)

**输出物清单 (本 plan 已完成)**:
- 现状盘点表 (Top 15 文件 LOC + fan_in)
- 8 项重构方案 (P0 × 3 / P1 × 4 / P2 × 3)
- 可复用 component library 6 个
- 可扩展原则 (新加 alpha / strategy / audit 模板)
- ETA + commits 估
- codegraph 协同 (skill + pre-commit + diff check)
- 风险 + reverse 计划
- 关键 push back (4 条)

不实施代码, 不 commit. 等用户决策启动 P0.
