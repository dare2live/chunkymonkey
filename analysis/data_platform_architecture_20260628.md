# 数据平台架构 (Data Platform Architecture) — 重建定稿 v3.0 (2026-06-28)

> owner: 主会话 (控制面). 状态: 重建定型, 纯数据平台真相源。
> 取代 `data_module_toplevel_design_20260622.md` + `data_module_architecture_20260624.md` (二者标 superseded, 留历史)。
> 北极星: 用户 2026-06-28 决议 —— 项目 = **纯净数据平台** (原始数据 tushare+aif10 + 四地基 + SERVE + 治理), 策略/serving/edge/workbench 层全退役, 待未来在干净平台上从零重建 edge。
> **2026-07-02 更新: 数据纯化 (批0-7) 已收敛** — 非 tushare/aif10 残表全物删, 死代码/config/断言清零, 4轮 sweep 验 dry。本文件个别数字按批2-7 后现状已同步 (40 raw/30表/2 routers), 实时清单见 FEATURE_MAP.md。

## 0. 为何重建 (创世)

2026-06-28 用户决议: 之前的项目是"数据 + 策略/serving/edge/workbench 混合 app", 累积了大量加工中间变量 (L2 特征 panel / 技术 stage / 主升浪 GT / 财务 derived / 事件聚合 / 处理 mart) + 策略服务代码 (signals/scoring/recommendation/screening/sector_momentum/dossier/...) + 50 个 workbench read service。用户判断"清加工层几乎等于重建, 干脆重建"。

**重建 = 白名单裁剪**: 定义干净数据平台 KEEP 白名单, 其余 (策略/serving/edge/workbench) git rm。DATA 中加工表 archive parquet 物删。结果 = 纯数据平台 + 未来 edge 待重建。

**死亡条款** (沿用 goal.md 创世层): 感知死 (异常回测不查 leakage) / 判断死 (规则 hardcode 不进 config) / 谄媚死 (报喜不报忧)。

## 1. 四层架构 (纯数据平台)

```
vendor (tushare + aif10 十大流通股东)
   │  M1 ACQUIRE (零计算, services/data_sources/ + raw client)
   ▼
L0 raw_* (raw_tushare_* 40表 / raw_aif10_* / fact_top10_holder_period / raw_org_holding_aif10 / raw_qfii_holding_quarterly) — lhb/surveys 批2 切 tushare(top_list/top_inst/stk_surv), inst_holdings 批3c 物删
   │  M2 CLEAN (写时归一, market_*/pipeline/clean)
   ▼
L1 v_price_kline_qfq (tushare-only, PIT 复权) + 配置 dim_*
   │  M4 SERVE (services/data_access/, 唯一取数 + PIT asof + provenance)
   ▼
consume: DataAccess.get(entity, codes, as_of) → DataResult{rows, provenance}
```

**四地基不变量** (现在做对, 改之昂贵):
1. **主键+PIT锚**: 每 entity 声明 code_col + asof_col + asof_format; resolver.preflight schema 自校验; asof_gate WHERE asof_col <= t。
2. **读写边界=库分区**: 7 库按写锁域分 (manifest 路由); reader read_only; §9 reference.duckdb 拆库 (4 dim 独立库, dim_read_conn 路由, writer reference-only)。
3. **可扩展分层**: data_layers.yaml 逐表声明 L0/L1/.../infra; data_layer_audit 执法 (每活表必声明)。
4. **单一真相源**: SERVE 单一读路; 数据源 tushare 唯一 (+aif10 十大流通股东例外, §4.3); leakage洞=0。

## 2. 库分区 (7 库, manifest 路由)

| 库 | 内容 | 写锁域 |
|---|---|---|
| tushare_raw | raw_tushare_* (40 vendor 镜像 + 2 PIT 行业视图; 批2-7 后实测) | M1 采集 |
| market | price_kline_qfq_tushare → v_price_kline_qfq (批3a 后唯一 K线真相源; 旧 price_kline/price_xdxr 已物删) | M2 清洗 |
| reference | dim_active_a_stock / dim_trading_calendar / dim_all_ever_listed / dim_listing_status (读多写少) | §9 拆出 |
| smartmoney | aif10 域 fact (holders/qfii/org_holding/估值) + 配置 dim_* + 治理 mart (30 表) | 采集+治理 |
| etf | 空壳仅 deletion_record (ETF 子系统批3d 整体退役) | — |
| experiment_store | 留档 (L4, 当前空; edge 重建后用) | 治理 |
| feature_store | (L2 特征面板库, 当前仅 deletion_record 留痕; edge 重建后用) | M3 (待重建) |

## 3. 子模块清单 (KEEP 数据平台)

| 模块 | 职责 | 路径 |
|---|---|---|
| **M1 ACQUIRE** | vendor→L0 raw 镜像 | `services/data_sources/` (sources: aif10/tushare) + holders_aif10/org_holding_aif10/qfii_client/aif10_capability_client/industry — lhb_client/institution_survey_client 批2 退役 (切 tushare 域) |
| **M2 CLEAN** | L0→L1 qfq 归一 | `build_price_kline_qfq_tushare.py` (daily CTAS 重建+自sanity) + `services/market_db/market_read/market_schema/kline_source` + `pipeline/clean.py` |
| **M4 SERVE** | 唯一取数+PIT | `services/data_access/` (resolver/spec/asof/keys/drivers) |
| **四地基** | universe/calendar/security_master + reference 库 | `services/universe/calendar/security_master` + `migrate_reference_db` |
| **编排** | 采集→清洗→存储 门链 | `services/pipeline/` (acquire/clean/store/run/stage_runner/stage_status/context) |
| **血缘** | acquire+consume DAG (producer/consumer 真相源) | `services/lineage/` + `chunkyctl lineage` |
| **治理** | 审计/质量/留痕/门 | `services/data_audit/data_quality/data_deletion/storage_retention/source_watermarks/source_policy/sandbox_guard` + `.moth/` + `check_*.py` (含 **check_dead_references** 死引用硬门) |
| **DB infra** | schema/连接/路由 | `services/db/duck_adapter/database_manifest/schema_core/schema_migrations/schema_versions/schema_layer_filter/schema_marts/primitives` |

**治理 mart (10, KEEP)**: mart_data_health / mart_pipeline_run_manifest / mart_data_source_watermark / mart_data_source_failure_queue / mart_data_deletion_record / mart_data_deprecation_record(只读历史) / mart_global_data_quality_gate / mart_pipeline_lock / mart_step_fingerprint / mart_lineage。(mart_data_audit_report 2026-06-28 退役物删: audit 改写 data/reports JSON; services/audit.py 1568行孤儿 + data_deprecation.py 同退役。)

### 3.5 数据登记/路由 + 治理硬门 (2026-06-28 根因根治 F1-F4)

**根因**: 之前每波清理删"供给侧"(模块/表) 漏"需求侧"(引用方); 验收够不到孤儿脚本/懒import/guarded垫片/config死路径 → 残留静默累积。`dim_data_asset` 登记表烂掉(67stale/68漏/0强制) + 同件登记散 5 处。

**数据登记/路由 = 4 真相源 (各有强制门, dim_data_asset 已退役归并进它们)**:
| 真相源 | 管 | 强制门 |
|---|---|---|
| `sync_registry.yaml` | 哪来的 (采集契约) | sync_runner |
| `data_layers.yaml` | layer + **asset_class A/B** + health 默认/覆盖 | `data_layer_audit` (untagged=0 + **Type A 列纯度**) |
| `data_access.yaml` | SERVE entity (怎么取+PIT) | resolver.preflight |
| `lineage` graph | producer/consumer DAG (在哪用) | check_lineage_drift + **check_dead_references** |

**加工分两种 (asset_class, 客观划线"PIT确定性重排 vs 含前瞻/策略")**: **A**=L1_foundation/L1k/display(确定性PIT重排→平台常驻SERVE) / **B**=L2/L3/L4(策略派生→edge隔离, 当前空) / raw=L0 / infra。Type A 层表禁现 forward/label/score/signal/ic/predicted 列 (`data_layer_audit` type_a_leak 门防 Type B 伪装混入)。

**死引用硬门 `check_dead_references.py`** (safe_commit Step3.97 + CI + moth): import-services 库层 / dead-services-ref 全.py / config-dead-path registry列表 — 删任何模块/表/文件引用方没清 = commit 红。**残留无法再静默累积**。

**routers (KEEP)**: ops_manual_run (手动跑数据链) / v3_config (前端参数下发)。无策略 serving HTTP API。(strategy_preset 批7 退役物删, 2026-07-02)

## 4. 重建删除了什么 (history)

**代码 ~245 文件 git rm** (git 史可逆): 策略服务 (signals_v2/scoring/recommendation_universe/screening_engine/sector_momentum/industry_context_engine/event_engine/return_engine/risk_factors/holdings/business_facts/dossier/read_model/financial_client/pricing_policy*/portfolio*/experiment_*/leakage_detect + institution_*_read/stock_*_read + 50 workbench_*_read) + 25 子目录 (backtest/formula_engine/ml_*/optimization/portfolio*/strategies/selection/sentiment/picture/technical_states/research/paper_engine/...) + routers (signals/dossier/v3_picture/v3_paper/v3_selection/v3_portfolio_builder/stock_graph/workbench/market) + 69 策略测试 + tdxhub/akshare 源 client + leakage/harness 闸脚本。

**数据 ~40 表物删** (archive parquet 在 `data/archive/purge_processed/` + deletion_record 留痕, 可逆):
- L2 特征 panel: fact_feature_panel/segment_panel/signal_panel (23M 行)
- 主升浪 D1 GT: fact_rally_ground_truth/entry_pit/entry_negative/episode_strata + fact_macd_episode_ground_truth (37万)
- 技术 stage: fact_stock_technical_stage/fact_rally_stage/mart_macd_state_history (9.2M)
- 财务 derived: fact_financial_derived/dim_financial_latest
- 事件聚合+处理 mart: fact_institution_event/fact_lhb_event/fact_risk_factors/mart_current_relationship/mart_stock_picture_daily/mart_stock_survey_activity + 死策略 mart (dual_confirm/sector_momentum/screening/formula_*/model_lifecycle/fund_flow_rank/feature_drift/...)

**schema DDL trim**: schema_marts + schema_migrations 移除 73 句非 KEEP mart 的 CREATE/INDEX/ALTER (防 init_db 重建)。

## 4.5 换源 SOP — 水龙头模型 (2026-07-02 用户定调固化)

**原则**: 数据与数据源分开管理。源=水龙头 (raw 表, 绑 vendor 名), entity=桶 (业务概念, 消费方唯一可见),
M4 SERVE=分水中转, M5 lineage=水表 (谁接了哪根管)。消费方永远只读桶, 不知道水龙头。

**换源三步 (消费方零改动)**:
1. **接新水龙头**: sync_registry 注册新域 → 新 raw 表落库 (M1 零计算镜像, 表名绑 vendor 合法);
   若字段/单位/PIT 锚与 entity 契约不一致, 在 M2 归一层写 adapter/builder 达标 (参照 build_price_kline_qfq_tushare:
   手→股/千元→元/PIT 前复权 = K线桶的标准水质)。
2. **对账**: 新旧源重叠期按 entity 契约列逐日对账 (K线对收益率/事实类对值); 达标才切。
3. **切指针**: 改 data_access.yaml 该 entity 的 db/table 一处 → 消费方零改动。旧源按 §4.3 铁律退役
   (lineage impact 查 fan-in → 物删 archive 留底 → check_dead_references 挡残留)。

**两类水 (客观划线 = "换源后还是不是同一桶水")**:
| 类 | 例 | 换源语义 | entity 命名 |
|---|---|---|---|
| **事实类** | K线/成交/股东名册/财报数字/涨跌停/解禁 | 换水龙头, 水不变 → 上述三步全适用 | **禁 vendor 名** (kline_qfq/holders_top10) |
| **观点类** | 行业/概念分类 (taxonomy), 资金流单别 (vendor 算法) | **换桶** — 分类体系是数据身份, 历史不可比 | **必须带体系名** (sw_industry/dc_member/moneyflow_dc), 切换打 taxonomy_version 分段, 禁跨体系拼接 (CLAUDE §4.5 反例) |

**历史教训定位**: tdx行业→申万/东财、akshare→tushare 换源之痛的根因 = SERVE 中转站 2026-06-22 才建成,
之前一年的消费方全是直连 raw 的强绑定存量 (已随 06-28 重建清光)。今后换源走本 SOP, 痛不复现。

## 5. 未来 edge 重建 (在干净平台上)

北极星 = 主升浪猎手 (episode-first 结果倒推)。重建必守:
- 从 raw K线重新生成 D1 GT (旧 GT archive 仅参考, 不复用)。
- 四地基不变量 + 含成本可交易裁决 (R1 IC≠可赚钱 / R2 execution-aware 涨跌停+T+1 open+非对称成本+容量)。
- 重建 = 透明重写 + 单测证伪门, 非复活旧码 (旧码 git 史可逐行核)。
- L2 特征层 / 策略 serving / edge 验证 (experiment_store/harness) 按需在干净平台上分模块重建。

## 6. 真相源文档地图

| 要什么 | 看哪 |
|---|---|
| 当前阶段/KPI/路线 | `goal.md` |
| 全局蓝图 (含未实现 edge 段) | `docs/MASTER_TOPLEVEL_DESIGN.md` |
| 表/模块/脚本/反例 活索引 | `PROJECT_INDEX.md` |
| 数据层级框架 | `docs/data_management_framework.md` |
| 完成项/历史/重建删除清单 | `analysis/project_state_ledger.md` |
| 重建删除 manifest | `analysis/purge_*_manifest_20260628.yaml` |
| 旧策略架构 (superseded, 历史参考) | `analysis/data_module_toplevel_design_20260622.md` + `data_module_architecture_20260624.md` |
