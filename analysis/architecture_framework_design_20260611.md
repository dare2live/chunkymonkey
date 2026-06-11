# ChunkyMonkey 整体框架设计底稿 — 六层契约 + 回测框架配置化

> 状态: 底稿 (draft), 2026-06-11 体检后顶层设计。
> 上位文档: `docs/PROJECT_CONSTITUTION.md` (宪法) · `docs/implementation_plan.md` (Active Repair Plan)。
> 本稿不新建权威体系 — 六层是宪法 L0-L4 的细化, 不是替代。冲突时宪法优先。
> 核心立场: **复用 paper_sim v2 / data_sources registry / feature_registry 三个已有骨架, 只补契约和注册制, 不建新引擎**。

---

## 0. 设计约束 (来自已踩的坑)

| 约束 | 来源反例 |
|---|---|
| 不重写回测 | bestchoice 双拷贝 = 重写路线的尸体 (implementation_plan Phase 3 既定决策) |
| 不再建平行表 | 344 表中保障覆盖仅 4%; 每股最优 5 表 / 推荐 8 表同语义重复 (G4) |
| 不再拷贝 yaml | 12 个 `paper_sim_*.yaml` 变体 = 全量拷贝 + 注释里记 diff, 无法机器比对 |
| 不再每域写 sync 脚本 | daily_update 静默失败 / 多表不在 sync 步骤 / 8-39 天滞后 (2026-05-26) |
| 数字只有一个出口 | "各算各的各展示各的" 的根因: KPI 没有唯一可引用 grain |

---

## 1. 六层契约总图

```
 D1 数据      raw_* / fact_*            ← source_sync + sync_registry.yaml
   ↓ canonical_relation 视图 (唯一读取面)
 D2 特征      feature panel             ← feature_registry.yaml (groups)
   ↓ model_input 白名单 + built_at<=t
 D3 信号      fact_*_trigger            ← formula_*.yaml (10 个)
   ↓ (stock, signal_date, formula_id)
 D4 模型      mart_*_predictions        ← model_search.yaml
   ↓ (model_id, stock, date, score)
 D5 策略      候选集 (统一推荐表)        ← strategies.yaml (新增, 注册制)
   ↓ CandidateRow contract
 D6 执行      paper_sim 4 表 + run 注册  ← paper_sim_config.yaml (唯一 base)
   ↓ sim_run_id (唯一数字出口)
 L4 展示      API / 前端 只读 D6 + lineage 链
```

**与宪法 L0-L4 的映射**: D1=L0 · D2/D3=L1/L2 · D4/D5=L3 上半 · D6=L3 下半 · 展示=L4。
**横切真相源**: K 线 / 交易日历 / `universe_rules.yaml` 对所有层只读开放, 不算跨层。
**ID 链 (反"各算各的"的核心)**: `sim_run_id → strategy_id → model_id → formula_id → feature_group → source watermark`。任何对外数字必须能沿这条链回溯到 D1 watermark。

### 1.1 各层五元组

| 层 | 模块 owner | 数据表 + grain | 配置文件 | 上下游 contract | 验证 gate |
|---|---|---|---|---|---|
| **D1 数据** | `services/data_sources/` (base/registry/sources/{tdxhub,tushare,akshare,aif10}) + 新 `sync_runner.py`; updater 仅当 supervisor | `raw_<source>_<api>` grain=(ts_code, trade_date) 或 (ts_code, ann_date); `fact_<domain>` 仅当多源归一才建 | `data_sources.yaml` (capability→primary/fallback) + **新 `sync_registry.yaml`** (§3) | 下游只读 `canonical_relation` 视图, 不直接 JOIN raw 表; 每表必有 `built_at` + watermark 行 | `data_audit` 7 项 (strict raise); freshness SLA 从 sync_registry 自动生成检查 |
| **D2 特征** | `services/features/` (build_feature_panel) | feature panel grain=(stock_code, date); 版本化 panel 现役一代 (G2: 六代→一代) | `feature_registry.yaml` (groups: enabled/production_ready/model_input/feature_role/cadence) | `model_input_excluded` 白名单强制 (forward_* 永不入模); JOIN 必带 `built_at <= t`; 新特征族先注册 group 再写代码 | `pit-audit` 5 步; ROI gate (Spearman/coverage/var, Phase 2); 相对提升 ≥+50% 触发 PIT 复审 |
| **D3 信号** | `services/formula_engine/` + bank | `fact_*_trigger` grain=(stock_code, signal_date, formula_id, formula_variant) | `formula_*.yaml` (10 个, 参数全 yaml) + `formula_shared_windows.yaml` | 信号只用 `bars[:sig_i+1]`; 每公式必有非空 Optuna search space; 输出 tier/score 字段语义统一 | `backtest_preflight.signal_pit_spotcheck` + `code_leakage_scan`; `plan_validator.search_space` |
| **D4 模型** | `services/` LambdaMART / model_search; `services/optimization/` (walk_forward/governance) 为横切治理 | `mart_*_predictions` grain=(model_id, stock_code, trade_date); `mart_per_stock_*_optimal` 收敛后 1 张 (G4, 保 `_pit`) | `model_search.yaml` + `optuna_config.yaml` | 预测行必带 model_id + train window 元数据; selector 只读 `oos_*` 列; 入库过 `governance.enforce_pre_insert` | walk-forward OOS + Phase4 gate; `fact_optuna_governance_log` 留审计 |
| **D5 策略** | `services/paper_sim/selector.py` + loaders (ml/tiered/hybrid/ensemble) + **新 `strategy_registry.py`** (§2) | 候选输出收敛到 1 张推荐表 grain=(strategy_id, stock_code, trade_date) — G4 推荐 8 表→1, 加 strategy_id 列代替建新表 | **新 `strategies.yaml`** (§2.2) + `portfolio_sizer_profiles.yaml` + `universe_rules.yaml` | CandidateRow schema 固定 (stock, score, tier, source_ref); 候选来源只能是 D3/D4 注册表; 新策略 = 注册条目, 不是新表/新引擎 | `backtest_preflight` 8 项 (universe/limit_pct/cost/freshness/walk_forward 显式声明) |
| **D6 执行** | `services/paper_sim/` driver/exit_rules/swap_rules/sizer/tx_cost/tradability/risk_control/reporter (不动); 实盘镜像复用同一 driver 接口 | 现 4 张: `mart_paper_sim_nav` (run_id,date) / `fact_paper_sim_position` (position_id) / `fact_paper_sim_trade` (动作行) / `mart_paper_sim_kpi` (run_id) + **新 `fact_sim_run`** (§2.3) | `paper_sim_config.yaml` = **唯一全量 base**; 变体只存在于 strategies.yaml overrides | run 必有 run_id + config_hash + input_snapshot + code_version; **数字出口规则: 对外只引用含成本 replay 及以上, 且必须有 fact_sim_run 行** | run registry status=complete + KPI gate + 三基准并排 (§2.5); `session_handoff_audit` 校验引用的 run_id 存在 |

### 1.2 跨层规则 (contract 通则)

1. **只读相邻下层的 canonical 面** (视图/注册表), 不跳层直查 raw。例外: K线/日历/universe_rules 是全局真相源。
2. **每层有且只有一个注册 yaml**: 加东西 = 加注册条目; 没有注册条目的表/特征/公式/模型/策略 = 不存在 (gate FAIL)。
3. **grain 进入注册表**: 表的 grain 写在 `database_manifest.yaml`, 不写在人脑里。
4. **PIT 锚点逐层传递**: D1 的 `pit_anchor` (trade_date / ann_date) 决定 D2 JOIN 条件, D2 的 `built_at<=t` 决定 D3/D4 可用性。

---

## 2. 回测框架: 模块 + 数据表 + 配置文件化 (基于 paper_sim v2, 不重写)

### 2.1 现状诊断 (为什么 12 个 yaml 会发散)

| 问题 | 现状证据 |
|---|---|
| 变体 = 全量拷贝 | `paper_sim_ml_score_champion_minhold15.yaml` 整文件复制 baseline, 真实 diff 只有 1 个字段 (`min_holding_days_before_exit: 15`), diff 记在注释里, 机器读不到 |
| 策略分发 = if-chain | `selector.py:617-657` 按 `cfg.mode ==` 硬编码分发 production/backtest/ensemble/ml_score/hybrid, 加一种策略 = 改业务代码 |
| run 元数据已存在但不完整 | `mart_paper_sim_kpi` 已有 `config_snapshot/sim_config_hash/parent_sim_run_id/param_diff_json` — 但只在跑完才写, failed/running run 无记录, 无 input_snapshot |
| 基准对比靠人工 | `hs300_nav` 一列在 nav 表里, 但等权基准/不换股基准没有同口径 run |

结论: **骨架是对的, 缺的是注册制 + run 登记 + 基准内建**。与 implementation_plan Phase 3 决策一致。

### 2.2 strategy 注册制 — `backend/config/strategies.yaml` (新增)

一个策略 = 一个条目 = base config + diff overrides。**禁止全量拷贝**。

```yaml
version: 1
defaults:
  base_config: paper_sim_config.yaml      # 唯一全量 base, 所有字段默认值
  benchmark_set: [bench_hs300_hold, bench_equal_weight, bench_no_swap]

strategies:
  prod_champion:
    status: production                    # production / candidate / benchmark / archived
    loader: ml_score_tiered               # strategy_registry 注册的 loader id
    candidate_source: mart_p0b_lambdamart_v6_predictions   # 必须是 D4 注册表
    model_id: lgbm_phase5_session_20260518T160747
    overrides:                            # 相对 base 的 deep-merge diff (lint: 重复 base 值 = FAIL)
      portfolio: {position_sizing: equal, min_cash_pct: 0.30}
      exit: {min_holding_days_before_exit: 15}
    evidence:                             # champion 身份唯一注册点 (Phase 0 要求)
      kpi_run_id: <sim_run_id>            # 最近一次 PASS 的 run
      approved: 2026-06-XX

  exp_minhold5:                           # 实验变体: 同样只写 diff
    status: candidate
    inherits: prod_champion               # 单层继承, 禁止链式 (防 yaml 里长出类体系)
    overrides:
      exit: {min_holding_days_before_exit: 5}

  bench_hs300_hold:                       # 基准 = 一等公民策略, 同 runner 同表
    status: benchmark
    loader: benchmark_hs300_hold
```

### 2.3 run registry — `fact_sim_run` (新表, 唯一新增表)

为什么 KPI 表不够: KPI grain = "完整跑完的 run 摘要"; run registry grain = "尝试过的 run" (含 running/failed), 且 input_snapshot 必须在**开跑时**锁定。生命周期不同 → 单独一张, 4 张现有表 schema 不动。

```sql
CREATE TABLE IF NOT EXISTS fact_sim_run (
    sim_run_id     TEXT PRIMARY KEY,
    strategy_id    TEXT NOT NULL,          -- strategies.yaml key
    config_hash    TEXT NOT NULL,          -- base+overrides 解析后全量 config 的 sha256
    code_version   TEXT NOT NULL,          -- git commit hash
    input_snapshot TEXT NOT NULL,          -- JSON: 各上游表 max(date) + row_count watermark
    period_start   TEXT NOT NULL,
    period_end     TEXT NOT NULL,
    status         TEXT NOT NULL,          -- running / complete / failed
    fail_reason    TEXT,
    benchmark_of   TEXT,                   -- 非 NULL 时指向被对照的主 run_id
    started_at     TIMESTAMP NOT NULL,
    finished_at    TIMESTAMP
);
```

复现契约: 同 `(strategy_id, config_hash, input_snapshot, code_version)` 必须可复现同 KPI; `sim_cache.py` 的 cache key 直接换用 config_hash, 不另算。

### 2.4 结果表 grain 评估 — 现 4 张够, 不加结果表

| 表 | grain | 判定 |
|---|---|---|
| `mart_paper_sim_nav` | (sim_run_id, date) | 够。基准 run 各自有 run_id, 自然落同表; `hs300_nav` 列保留兼容, 长期由基准 run 取代 |
| `fact_paper_sim_position` | position_id | 够 |
| `fact_paper_sim_trade` | 动作行 | 够 |
| `mart_paper_sim_kpi` | sim_run_id | 够。`config_snapshot/sim_config_hash` 列与 fact_sim_run 暂双写, 收敛期后 KPI 表这两列退役 |

**多策略并行**: DuckDB 单 writer (CLAUDE.md §10) → 默认**串行循环跑 N 个策略 run** (一个 run 分钟级, 串行成本可接受); 确需并行 (大 sweep) 时走 `experiment_jobs` gate, 每 run 写 parquet sidecar 到 `data/artifacts/<run_id>/`, 末尾单 writer 统一 ingest。不为并行建新表。

### 2.5 基准对比内建

| 基准 | loader | 含义 |
|---|---|---|
| `bench_hs300_hold` | 买入持有 HS300 | 市场 beta |
| `bench_equal_weight` | universe 等权月调仓 | 选股 alpha 是否存在 |
| `bench_no_swap` | 主策略关 swap (`swap.enabled: false` override) | swap 增量归因 |

`reporter.py` 增一个函数: 给定主 run_id, JOIN `fact_sim_run.benchmark_of` 找到同 period + 同 input_snapshot 的基准 runs, 输出并排 KPI 表。**对外引用主策略数字时必须带三基准对照** — 这是"数字出口规则"的展示形态。

### 2.6 最小改动路径

| 动作 | 文件 | 量级 |
|---|---|---|
| 新增 | `backend/config/strategies.yaml` | 注册 prod 1 + 实验模板 1 + 基准 3 |
| 新增 | `backend/services/paper_sim/strategy_registry.py` | ~120 LOC: 读 yaml → deep-merge → config_hash → loader 查表 (dict, 非 if-chain); `AbstractStrategy` = Protocol(`load_candidates(cfg, date) -> list[CandidateRow]`), 默认实现全配置驱动, 自定义策略只需实现这一个方法 |
| 修改 | `selector.py` | `load_candidates` if-chain (L617-657) → registry 查表分发, 净删行 |
| 修改 | `config.py` | 支持 base + overrides merge (取代 12 份全量 yaml 的加载) |
| 修改 | `driver.py` | run 开始/结束写 `fact_sim_run` (~30 LOC) |
| 修改 | `ddl.py` | 追加 `fact_sim_run` DDL |
| 修改 | `reporter.py` | 基准并排函数 (~60 LOC) |
| 归档 | 12 个 `paper_sim_ml_score_*.yaml` 等变体 → `analysis/archive_paper_sim_yaml/` | 落实 Phase 3 "prod 1 + 模板 1"; 历史 KPI 行不删 (validation artifact 不覆盖) |
| 不动 | driver 主循环 / exit_rules / swap_rules / sizer / tx_cost / tradability / risk_control / 4 张表 schema | 质量已过线 |

**实盘镜像**: 实盘不是新引擎 — 同一 strategies.yaml 条目 + 同一 selector/sizer/exit 模块, driver 换 execution adapter (paper fill ↔ broker fill), run 同样登记 `fact_sim_run` (status 加 live)。paper→live 偏差记录 (Phase 5) 即两类 run 的同表对比。

---

## 3. 新数据域接入范式 (TuShare 30+ 接口)

### 3.1 原则: 一个 yaml 条目 = 一个数据域

已有资产: `services/data_sources/` 已有 BaseDataSource/Capability/register_source + tushare adapter (probe 用); `data_sources.yaml` 已有 capability→primary/fallback/canonical_relation/SLA 模式 (目前仅 kline_daily)。**缺的只是: 把 "probe 能力" 升级为 "持久化 sync 能力", 且全配置驱动。**

### 3.2 `backend/config/sync_registry.yaml` (新增)

```yaml
version: 1
defaults:
  retry: {max_attempts: 3, backoff_seconds: [5, 30, 120]}
  zero_row_policy: fail            # 0 行 = 失败入队重试, 不静默 (Phase 1 既定)
  failure_queue_table: fact_sync_failure_queue
  watermark_table: fact_sync_watermark    # grain: (domain, watermark_date)

domains:
  moneyflow_dc:
    source: tushare
    api: moneyflow_dc
    target_table: raw_tushare_moneyflow_dc
    grain: [ts_code, trade_date]
    batch_mode: by_trade_date      # by_trade_date / by_ts_code / by_month / full_refresh
    pit_anchor: trade_date         # PIT 锚点: 该行何时"当时可知"
    available_after: "17:00"       # 盘后可用时刻 (calendar gate 用)
    freshness_sla_trading_days: 1
    canonical_relation: market.v_moneyflow   # 下游唯一读取面
    audit: {min_rows_per_day: 4000, board_coverage: true}

  stock_st:
    source: tushare
    api: stock_st
    target_table: raw_tushare_stock_st
    grain: [ts_code, ann_date]
    batch_mode: by_month
    pit_anchor: ann_date           # 公告类: ann_date 才是 PIT 锚, 不是生效日
    freshness_sla_trading_days: 5
    canonical_relation: market.v_stock_st
    audit: {min_rows_per_month: 1}
```

### 3.3 通用模块 — `backend/services/data_sources/sync_runner.py` (新增, 唯一新代码)

```
职责 (读 sync_registry.yaml, 零域专属代码):
  1. 按 batch_mode 切批 (交易日历驱动, 不 hardcode 日期)
  2. 调 source adapter fetch (复用现有 sources/tushare.py 的 _pro_api/_to_records)
  3. 写 raw 表 (api 字段镜像 + built_at), MERGE on grain
  4. 更新 watermark; 0 行/异常 → failure_queue + 重试
  5. 完成后调 data_audit (检查项由 registry 条目自动生成, 不手写)
```

| 规则 | 说明 |
|---|---|
| raw 表 = api 镜像 | 字段不改名不加工, 加 `built_at`; 加工归 D2, 不在 sync 层做 (防 sync 层长出业务逻辑) |
| `fact_<domain>` 仅多源归一才建 | 单源域下游直接读 canonical view; 这是 344 表膨胀的主要刹车 |
| data_audit registry 驱动 | `data_audit.py` 增一个 generic checker: 遍历 sync_registry 生成 freshness/min_rows 检查 — 新域接入**自动**被审计覆盖, 不再"多表不在 sync 步骤" |
| daily_update 集成 | supervisor 只调 `sync_runner.run_all(due_only=True)`, 不再每域一个 step (修 18 步覆盖缺口的同时防再发) |
| 接入序不变 | Phase 1 既定: moneyflow → cyq_perf/chips → stk_limit/stock_st/suspend_d → 北向 → margin → top_inst → trade_cal/stock_basic 去 akshare 化; 每项 = 加 1 个 registry 条目 + 5 项 required gate |

接入流程固定四步: `probe_source_capability.py` 验源 → sync_registry 加条目 → `sync_runner --domain X --backfill` → data_audit PASS。**全程不写新 .py 文件** (字段确需清洗时允许在条目里声明 `field_map`, 仍是配置)。

---

## 4. 新策略挂载范式 ("全新设计几套策略"怎么进来)

### 4.1 决策树

```
新策略想法
 ├─ 只是参数/组合不同 (换 exit/sizer/swap/候选源)
 │    → strategies.yaml 加条目 + overrides。0 行代码。
 ├─ 候选生成逻辑全新 (新打分/新池)
 │    → 1 个新 loader 文件: services/paper_sim/<name>_loader.py
 │      实现 load_candidates(cfg, date) -> list[CandidateRow]
 │      + strategies.yaml 条目 loader: <name>。其余全复用。
 ├─ 需要新信号/新特征
 │    → 先走 D2/D3 注册 (feature_registry group / formula yaml + search space),
 │      策略层仍只是 loader + 条目。
 └─ "我想建一套独立目录/独立表/独立 runner"
      → 禁止。这就是 bestchoice 双拷贝。挑战者以 namespaced loader 挂入,
        冻结物走 git tag, 不进 services/ 第二棵树。
```

### 4.2 一套新策略的完整足迹 (上限)

| 层 | 允许新增 | 不允许 |
|---|---|---|
| 配置 | strategies.yaml 1 条目 (+ 需要时 formula yaml / feature group) | 新 `paper_sim_*.yaml` 文件 |
| 代码 | 1 个 loader 文件 (实现 AbstractStrategy Protocol) | 新 driver/sizer/swap/reporter 拷贝 |
| 表 | 0 张 — 候选写统一推荐表 (strategy_id 列区分), 结果写 4+1 张 paper_sim 表 (sim_run_id 区分) | 任何 `*_<strategy_name>` 专属结果表 |
| 验证 | 同一条 gate 链: backtest_preflight → run → KPI + 三基准 → fact_sim_run | 自报 KPI / 旁路出数 |

这样"几套策略"天然在同一表同一口径里并排比较 — 可维护 (改 driver 一处全体受益)、可扩展 (加条目)、可复用 (loader/exit/sizer 互相组合)、不各算各的 (同 KPI 出口)。

---

## 5. 防发散机制 (gate 化, 不靠人记)

| # | 发散模式 | 历史反例 | Gate | 实现挂载点 |
|---|---|---|---|---|
| 1 | yaml 变体拷贝 | 12 个 paper_sim yaml | **config_divergence_lint**: (a) 新增 `paper_sim_*.yaml` / `formula_*` 之外的同前缀变体文件 = FAIL; (b) strategies.yaml overrides 含与 base 相同值的字段 = FAIL (必须是真 diff); (c) 任意两个 config yaml 相似度 >80% = FAIL | `safe_commit.sh` preflight + `chunkyctl doctor`; 新脚本 `scripts/audit_config_divergence.py` |
| 2 | 同语义表 | 每股最优 5 张 / 推荐 8 张 | **表注册制**: CREATE TABLE 前必须在 `database_manifest.yaml` 注册 owner/grain/truth_source/"为什么现有表不行"; data_audit 增 cross-check: information_schema 中存在但 manifest 未注册 = FAIL; 注册时与现有表 grain 相同 + 列重叠 >60% = 需 occam 豁免说明 | 复用现有 `database_manifest.yaml` + `data_audit.py` 加 1 个 checker |
| 3 | 平行引擎 | bestchoice/ vs bc_absorbed/ 双拷贝 | **architecture lint**: services/ 下出现第二个含 driver/selector/sizer 同名模块的目录 = FAIL; 挑战者只能以 loader 挂 strategies.yaml | `chunkyctl preflight` + codegraph 重复结构扫描 (§7.4 双扫已是强制) |
| 4 | if-chain 增生 | selector.py mode 分发 5 分支 | **rule_compliance PATTERNS 加一条**: `cfg.mode ==` 新分支 = reject; 分发只许走 strategy_registry 查表 | 现有 pre-commit `rule-compliance` hook |
| 5 | 数字旁路 | "+312%" 假象 / 各展示各的 | **数字出口唯一**: 对外/goal.md 引用的 KPI 必须能 JOIN 到 `fact_sim_run` (status=complete, 含 config_hash+input_snapshot); `session_handoff_audit` 校验文档中 run_id 真实存在; UI 只读 D6 + lineage | `session_handoff_audit.py` 加 keyword 检查; API contract 测试 |
| 6 | sync 脚本增生 | 每域一个脚本 + 静默失败 | 新数据域必须走 sync_registry 条目; rg 扫到 `services/` 新增 `*_sync.py` / `*_client.py` 直写表 = preflight WARN→FAIL | `chunkyctl preflight` 路径检查 |

**通则 (宪法第八条的工程化)**: 每个 gate = 代码实现 + FAIL 阻断 + 新增功能自动被覆盖 (加策略→#1#4 自动管, 加表→#2 自动管, 加数据域→#6 自动管)。规则不进 gate = 等于没立。

---

## 6. 落地顺序建议 (与 Active Repair Plan 对齐, 不另开战线)

| 步 | 内容 | 挂靠 Phase | 改动量 |
|---|---|---|---|
| 1 | `sync_registry.yaml` + `sync_runner.py` + data_audit registry 驱动 checker | Phase 1 (TuShare) — moneyflow 第一个条目即验证范式 | 1 yaml + 1 模块 |
| 2 | `strategies.yaml` + `strategy_registry.py` + selector 查表化 + `fact_sim_run` | Phase 3 (回测收敛) | 1 yaml + 1 模块 + 3 文件小改 |
| 3 | 三基准 loader + reporter 并排 + 12 yaml 归档 | Phase 3 | 2 文件小改 + 归档 |
| 4 | 防发散 gate #1/#2/#4 (lint 三件) | 治理瘦身贯穿项 | 1 审计脚本 + hook 配置 |
| 5 | 推荐表 8→1 (strategy_id 列) / 每股最优 5→1 | G4 (旧名只读 VIEW 过渡 30 天) | 迁移 + VIEW |
| 6 | 实盘镜像 execution adapter | Phase 5 | driver 接口抽 1 层 |

新增物总清单: **2 个 yaml (sync_registry / strategies) + 2 个模块 (sync_runner / strategy_registry) + 1 张表 (fact_sim_run) + 1 个 lint 脚本**。其余全是复用与收敛 — 符合宪法第二条: 每个新增物上面都已回答"为什么现有的不行"。
