# Plan re-eval evidence pack（2026-07-20）

> 生命周期：evidence-only  
> 作用：供 composer 写终裁重评；本文件**只收录可核对事实与原文引用**，不含排序裁决、不含「业主正确」结论。  
> 采集时点：2026-07-20；repo `/Users/dp/Documents/M/stock/chunkymonkey`；`main` ahead of origin（采集时 worktree 干净除本文件）。  
> 未改：`goal.md`（留给 composer）。

---

## 1. 历史文档：关键词命中 + 原文摘录

### 1.1 检索说明（事实）

| 关键词 | `rg` 结果摘要（docs/ + analysis/ + backend/config） |
|---|---|
| `四地基` / `数据四地基` | 有：根因文标题与用户定调；`gap_root_cause_20260708` 审计措辞；purge manifest「只留原始+四地基」 |
| `四大地基` | **未**找到作为独立固定词条的命中（同义多用「四地基」「数据四地基」） |
| `四大模块` | **未**找到作为正式命名表的命中 |
| `数据地基` | 有：MASTER §6 标题；多份 analysis；goal 旁路句 |
| `水龙头` | 有：`data_access.yaml` 注释（2026-07-02 用户定调） |
| `路由表` | **未**找到字面「路由表」；近义：`database_manifest`「库路由」、lineage「血缘路由中枢」 |
| `血缘` | 有：`mart_lineage`、`services/lineage/`、eng_gov 配置表、MASTER 多处 |

**未见**：仍活着的 owner 文档以「四大地基 = {命名列表}」为唯一权威枚举表。名称分布在历史 analysis / config 注释 / pipeline docstring。

### 1.2 原文摘录（path + 近似行号）

#### A. 「四地基 / 数据四地基」

```text
# 数据四地基根因分析 — 审计 38 条问题的六个系统性根因 (2026-07-03)
> 用户定调: "这些问题属于数据地基的问题, 四地基还有缺陷, 顺根因继续深挖"。
## 根因 1: … — M1 采集最重缺口
## 根因 3: … — M2 清洗地基缺口
## 根因 5: … — M2→M3 界面缺口
```

路径：`analysis/data_foundation_root_causes_20260703.md`（L1, L6, L9, L39, L67）

```text
min_rows校准 / 四地基 / PIT锚一致性 / vendor抽样(正确口径) / 治理门健康。
**裁决: READY_WITH_FIXES** — 干净面: 四地基主体全过(K线1822交易日0缺口/grain 0重复/universe
纯度100%/日历前瞻119天), …
## 第四轮: … (用户"检查数据地基是否具备向上搭建条件")
wf_d4375ef2 全栈审计(采集→清洗→加工→计算→展示→形态识别)裁决 READY_WITH_FIXES
```

路径：`analysis/gap_root_cause_20260708.md`（L234–L236, L278–L280）

```text
reason: "加工层清空(用户最严: 只留原始+四地基): L2 特征 panel …"
```

路径：`analysis/purge_u1_feature_store_manifest_20260628.yaml`（L9）

```text
# … 策略 serving 层退役 (纯净数据平台只留原始+四地基)。
```

路径：`backend/services/schema_migrations.py`（注释，约 L493；与 purge 同义）

```text
四地基的 dim_data_asset 登记表本该管这事, 但它烂掉…
```

路径：`backend/scripts/check_dead_references.py`（L7）

#### B. 「数据地基」与 MASTER 传输轴

```text
## 6. Tier 0 数据地基
```

路径：`docs/MASTER_TOPLEVEL_DESIGN.md`（L201）

```text
provider response
  -> landing (原样保留，不做业务过滤)
  -> validate (schema/grain/partition/completeness)
  -> accepted canonical (标准身份、单位、时间和质量)
  -> serve/read model (面向消费者的稳定读契约)
```

路径：`docs/MASTER_TOPLEVEL_DESIGN.md` §3.1（约 L38–L44）

#### C. 获取/清洗/加工/存储（四阶段管线措辞）

```text
"""daily_update 数据管线 — 获取/清洗/加工/存储 各司其职 (2026-06-23 重设计)。
  preflight (gate) → acquire (获取→L0) → clean (清洗 L0→L1) → process (加工 L1→L2) → store (存储/治理)
```

路径：`backend/services/pipeline/__init__.py`（L1–L4）

```text
  - backend/services/pipeline/         # daily_update 四阶段编排 (获取/清洗/加工/存储)
```

路径：`backend/config/data_module_members.yaml`（L19）

```text
"label": "数据底座五段手动链 (preflight/获取/清洗/加工/存储)",
```

路径：`backend/routers/ops_manual_run.py`（约 L34；五段 = preflight + 四阶段）

#### D. 水龙头 / SERVE / 血缘声明链

```text
# 每 entity 是一条声明 = 血缘的"声明链"一环 (展示→entity→源表→sync域→tushare)。
# ── 水龙头契约 (2026-07-02 用户定调: 数据与数据源分开管理 — 源=水龙头, entity=桶, SERVE=分水中转) ──
# entity 名 = 业务概念 (桶)…; vendor (水龙头) 只出现在 raw 表名/vendor 字段/provenance。
# 换源三步 (消费方零改动): ①新 adapter 落新 raw 表… ②新旧对账 ③改本文件 db/table 指针。
```

路径：`backend/config/data_access.yaml`（L5, L18–L20）

```text
"""M5 血缘路由中枢 (lineage routing hub) — …
T2 范围 …: 缝合 sync_registry (acquire 源→表) + data_access (SERVE consume)
+ 确定性 FROM/JOIN/引用扫描 … → 可查 lineage 图 (impact/provenance/dead)。
… transform … + display … 段押后, 不在 T2。
```

路径：`backend/services/lineage/__init__.py`（L1–L10）

#### E. 整体优化方案（A→H plan）中的分责表

Plan 文件：`~/.cursor/plans/gap_analysis_audit_3cdd0f6e.plan.md`

§2「清洗 / 加工 / 变量 / 展示 分责」表列能力：获取 | 清洗/接受 | 变量/状态 | 市场感知 | 展示（与「四地基」字面不同；列为五类能力）。  
§3 迁移总序：A → B-ext / B-pit → C → D → E0 → E → F → G → H。

#### F. 近期模块化缺口笔记（同日，非历史「四大」定义文）

路径：`analysis/data_foundation_modularity_gap_20260720.md`  
自称：业主澄清后的 acquire / process / compute / display 边界 vs `capture_and_publish_*` 融合；标签 **NOT SHIPPED**（编排维度）。

---

## 2. 今日存在物：事实地图

### 2.1 Formal landing / canonical 表名（代码常量）

| dataset_id（schema 常量） | LANDING_TABLE | CANONICAL_TABLE | 定义文件 |
|---|---|---|---|
| `tier0.market_data.nominal_ohlcv_daily` | `landing_tushare_daily` | `canonical_nominal_ohlcv_daily` | `backend/services/data_sources/nominal_ohlcv_schema.py` L15–17 |
| `tier0.security_identity.stock_st_daily` | `landing_tushare_stock_st` | `canonical_stock_st_daily` | `stock_st_schema.py` L15–17 |
| `tier0.reference.sse_trading_calendar_generation` | `landing_tushare_trade_cal`（另有 fragment 表） | `canonical_sse_trading_calendar_generation` | `calendar_schema.py` L16–21 |
| `tier0.market_data.margin_exchange_daily` | `landing_tushare_margin` | `canonical_margin_exchange_daily` | `margin_schema.py` L24–26 |
| `tier0.disclosure.top10_float_holders_period` | `landing_miaoxiang_holders_top10` | `canonical_top10_float_holders_period` | `holders_top10_schema.py` L16–18 |
| `tier0.disclosure.org_holding_detail_period` | `landing_miaoxiang_org_holding` | `canonical_org_holding_detail_period` | `org_holding_schema.py` L16–18 |
| `tier0.disclosure.stock_holder_trade_announcement` | `landing_tushare_stk_holdertrade` | `canonical_stk_holdertrade_announcement` | `stk_holdertrade_schema.py` L15–17 |

DDL 生成：`security_day_partition.py` / `disclosure_event_partition.py` / `margin_schema` / `calendar_schema` 内 `CREATE TABLE IF NOT EXISTS {landing|canonical}`。

### 2.2 Raw / legacy 镜像（并存）

- 大量 `raw_tushare_*` 仍由 `sync_registry.yaml` + `sync_runner` 传统路径写入（非上表 formal land→accept）。
- `nominal_ohlcv_runtime.py` 模块 docstring：**Authorized … never writes `raw_tushare_daily`**（formal daily 路径刻意不写 raw 镜像）。
- qfq 等派生仍可 UNION `canonical_nominal_ohlcv_daily` ∪ legacy `raw_tushare_daily`（`build_price_kline_qfq_tushare.py` 注释与 SQL）。

### 2.3 `accepted_partition`

- 控制表：`accepted_partition`（`data_layers.yaml` 标 `infra`；`database_manifest` table_patterns 含此名）。
- Writer/reader：`security_day_partition.accept_security_day_batch` 等；缺指针会 raise（如 `"accepted batch missing accepted_partition pointer"`）。
- Tier1/2：`tier12_publish_accept.accept_tier12_batch` 产出 kind `tier12_accepted_partition`；consumer cutover 读 `source=accepted_partition`。

### 2.4 Lineage / 路由相关资产

| 资产 | 角色（代码自述） | 路径 |
|---|---|---|
| `mart_lineage` + view `mart_data_lineage` | 输出表←输入表 SQL/owner/last_run 记录 | `backend/services/schema_marts.py` L62–97 |
| `services/lineage/` | 投影图：impact / provenance / dead_tables；缝合 registry + data_access + grep | `lineage/__init__.py`, `builder.py`, `query.py` |
| `data_access.yaml` | SERVE entity→db/table/vendor 声明链 | `backend/config/data_access.yaml` |
| `database_manifest.yaml` | DB 别名→路径（含 `feature_store`→`data/feature_store.duckdb`） | `backend/config/database_manifest.yaml` |
| `data/lineage/` | 实验/cutover 等 artifact 目录（phase_e/f manifests 等） | 文件系统 |

Lineage hub docstring 自述边界：T2 = acquire+consume 图；**transform / display 段押后，不在 T2**。

### 2.5 Routers / HTTP API（`backend/routers/`）

| 文件 | 已登记路由（装饰器） |
|---|---|
| `market_pulse.py` | `/heatmap`, `/rotation`, `/flow_board`, `/flow_stripe`, `/drill`, `/sentiment`, `/strongest`, `/members`, `/warnings` |
| `institution_profile.py` | `/profiles`, `/profiles/{holder}`, `/signals` |
| `paper_portfolio.py` | `/positions`, `/portfolio`, `/nav`, `/mark` |
| `ops_manual_run.py` | `/jobs`, `/jobs/{job}`, `/jobs/{job}/run` |

另：生产读契约方向见 `resolve_tier12_production_read` / `resolve_b_pit_mart_production_read`（goal 2026-07-20 dual-track 复核称 residual NONE）。本包不验证 live HTTP。

### 2.6 Feature / 加工侧（存在性，非完备性声明）

- DB：`feature_store` in `database_manifest.yaml`。
- Builders（成员 roster 注释）：`institution_profile.py`, `segments.py`, `technical_states/`, `market_pulse.py`, `rally_gt.py`, `calendar_builder.py`（`data_module_members.yaml`）。
- Pipeline stages：`pipeline/acquire.py`, `clean.py`, `process.py`, `store.py` + `run.py` 编排。
- L2 panel 层：`data_layers.yaml` 记 `L2_feature` status `wiped_20260628`。

### 2.7 sync_runner 融合点（可核对调用链）

生产 daily / stock_st：

```text
run_domain(daily|stock_st)
  → _publish_security_day_accepted_partition  # docstring: "Fetch one trade_date and publish accepted …"
       adapter = _adapter(spec["source"])
       _fetch_rows → adapter.fetch_raw
       → capture_and_publish_authorized_{nominal_ohlcv|stock_st}_partition(..., fetch_rows=_fetch_rows)
```

证据位置：

- `sync_runner.py`：`_publish_security_day_accepted_partition` ≈ L1856–1913；`run_domain` ≈ L1957+；`_adapter` ≈ L351。
- `nominal_ohlcv_runtime.py`：`capture_and_publish_authorized_nominal_ohlcv_partition` docstring = **「fetch → land → accept one trade_date」**（L60–69）；内部再调 `publish_accepted_*` → `land_*` + `accept_*`（L46–57 一带）。
- `stock_st_runtime.py`：对称 `capture_and_publish_*` / `publish_accepted_*`。
- 日历：`capture_and_publish_authorized_calendar_generation` 亦由 `sync_runner` 调用（≈ L1657–1673）。

库内另有可测的 `land_*` / `accept_*` / `publish_accepted_*`（不经 fetch）；modularity gap 笔记称运营入口未暴露 land-only / accept-from-landing CLI。

`_adapter` / formal live adapter：多源 registry 退役史见 `analysis/data_sources_registry_retirement_20260707.md`；`formal_boundaries` / LIVE_ADAPTER 约束见该笔记与 `formal_boundaries.py`（本包未重跑 inventory）。

---

## 3. Gap plan A→H：排期 vs 仓库自称 shipped 状态

来源交叉：`~/.cursor/plans/gap_analysis_audit_3cdd0f6e.plan.md` §3；`goal.md`（2026-07-20）；`BOARD.md` / `data/board/agent_context.json`；`analysis/forward_program_efgh_20260720.md`；ledger 2026-07-19 条目。

| Phase（plan 名） | Plan 退出意图（摘要） | 仓库当前自称状态（证据指针） |
|---|---|---|
| **A** Tier0 硬门 | contract / calendar / 名义K+ST / landing 纯度 / adapter 边界；`live_readiness` 可评估 | goal/ledger：A1–A5 代码路径标 FIXED；live_readiness 历史上常 BLOCKED/NOT_EVALUATED；daily/ST frontier goal 称 **20260720** current |
| **B-ext** | external_aggregate 诚实化；切断错误 scope | B-ext 代码路径 FIXED（诚实化）；数值切读面另账 |
| **B-pit** | project_universe_pit；shadow 后切读 | `cutover_allowed=true`（b_pit）；BOARD：shadow match=120/diverge=0（窗口至 20260717） |
| **C** Tier1/2 发布契约 | StockState / Pattern / MarketContext + available_at 等 | `tier12` consumer `cutover_allowed=true`；goal：form enrich re-accept 20260717/20260720 |
| **D** 研究运行时 | DatasetSnapshot + ExperimentRun/Verdict | goal：**FIXED**；board `phase_d`：persist/fold/measured_offline 存在；verdict 样例 inconclusive |
| **E0** 披露 formal | holders/org/stk → land/accept；退直写 | ledger：E0 slice1–6+ **PARTIAL / in progress**；full E0 未宣称闭合（仍有 NONCONFORMING / cutover false 研究读面叙述） |
| **E** 机构跟随 | B0→B4；ExperimentVerdict | board `phase_e`：`overall_status=measured_reject_no_gain`；B0–B2 reject；B4 inconclusive；`claimable=false`；`strategy_release=false` |
| **F** 主升浪 B0–B2 | 同 runtime 消融 | goal：F0–F3 **FIXED**（protocol-complete；均 reject / claimable=false）；更长窗 remeasure **BLOCKED**（日历/禁 backfill） |
| **G** 公式 + BestChoice | B5 / challenger | **未开**（goal 禁；forward program：无 claimable 不开 G） |
| **H** Release / 纸面 | StrategyRelease 后 | **未开**（机械封锁） |

补充事实（board `next_knives_frozen` 投影，可能滞后于 goal 手写）：

- 仍列出：`A→H next: F main_rally B0–B2…`；`accept Tier1/2 … after 20260720`；WP6 ceremony；`or stop`。  
- goal 手写下一步指针：`forward_program_efgh` 的 **P0+P1**；并另有「地基模块化 — NOT SHIPPED」旁路句指向 modularity gap。

Plan §2 多源目标态 vs 实况句（plan 原文摘要）：正式 registry 域 live adapter=TuShare；miaoxiang 披露域 live 但 NONCONFORMING（直写）— 归 E0。

---

## 4. 相关路径索引（便于 composer 复核）

| 主题 | 路径 |
|---|---|
| A→H 方案稿 | `~/.cursor/plans/gap_analysis_audit_3cdd0f6e.plan.md` |
| 现行 goal / board | `goal.md`, `BOARD.md`, `data/board/agent_context.json` |
| 前向程序（E/F 后） | `analysis/forward_program_efgh_20260720.md` |
| 模块化缺口（同日） | `analysis/data_foundation_modularity_gap_20260720.md` |
| 四地基根因 | `analysis/data_foundation_root_causes_20260703.md` |
| MASTER | `docs/MASTER_TOPLEVEL_DESIGN.md` |
| Sync 融合 | `backend/services/data_sources/sync_runner.py`, `nominal_ohlcv_runtime.py`, `stock_st_runtime.py` |
| 水龙头 | `backend/config/data_access.yaml` |
| 血缘 | `backend/services/lineage/`, `backend/services/schema_marts.py` |

---

## 5. 本包明确不做

- 不给「整体优化方案继续/改序/收缩」裁决。  
- 不主张业主四条与历史「四地基」等价或不等价。  
- 不改 `goal.md` / 不写终裁重评文。  
- 不实施 pipeline rewrite。
