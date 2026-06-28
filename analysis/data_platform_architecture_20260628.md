# 数据平台架构 (Data Platform Architecture) — 重建定稿 v3.0 (2026-06-28)

> owner: 主会话 (控制面). 状态: 重建定型, 纯数据平台真相源。
> 取代 `data_module_toplevel_design_20260622.md` + `data_module_architecture_20260624.md` (二者标 superseded, 留历史)。
> 北极星: 用户 2026-06-28 决议 —— 项目 = **纯净数据平台** (原始数据 tushare+aif10 + 四地基 + SERVE + 治理), 策略/serving/edge/workbench 层全退役, 待未来在干净平台上从零重建 edge。

## 0. 为何重建 (创世)

2026-06-28 用户决议: 之前的项目是"数据 + 策略/serving/edge/workbench 混合 app", 累积了大量加工中间变量 (L2 特征 panel / 技术 stage / 主升浪 GT / 财务 derived / 事件聚合 / 处理 mart) + 策略服务代码 (signals/scoring/recommendation/screening/sector_momentum/dossier/...) + 50 个 workbench read service。用户判断"清加工层几乎等于重建, 干脆重建"。

**重建 = 白名单裁剪**: 定义干净数据平台 KEEP 白名单, 其余 (策略/serving/edge/workbench) git rm。DATA 中加工表 archive parquet 物删。结果 = 纯数据平台 + 未来 edge 待重建。

**死亡条款** (沿用 goal.md 创世层): 感知死 (异常回测不查 leakage) / 判断死 (规则 hardcode 不进 config) / 谄媚死 (报喜不报忧)。

## 1. 四层架构 (纯数据平台)

```
vendor (tushare + aif10 十大流通股东)
   │  M1 ACQUIRE (零计算, services/data_sources/ + raw client)
   ▼
L0 raw_* (raw_tushare_* / raw_aif10_* / fact_top10_holder_period / raw_lhb_daily / raw_org_holding_aif10 / raw_qfii / raw_institution_surveys / inst_holdings)
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
| tushare_raw | raw_tushare_* (43 vendor 镜像) | M1 采集 |
| market | price_kline / price_kline_qfq_tushare / price_xdxr | M2 清洗 |
| reference | dim_active_a_stock / dim_trading_calendar / dim_all_ever_listed / dim_listing_status (读多写少) | §9 拆出 |
| smartmoney | raw fact (holders/lhb/qfii/org_holding/surveys/inst) + 配置 dim_* + 11 治理 mart | 采集+治理 |
| etf | ETF raw + import batch | M1 ETF |
| experiment_store | 留档 (L4, 当前空; edge 重建后用) | 治理 |
| feature_store | (L2 特征面板库, 当前仅 deletion_record 留痕; edge 重建后用) | M3 (待重建) |

## 3. 子模块清单 (KEEP 数据平台)

| 模块 | 职责 | 路径 |
|---|---|---|
| **M1 ACQUIRE** | vendor→L0 raw 镜像 | `services/data_sources/` (sources: aif10/tushare) + holders_aif10/org_holding_aif10/qfii_client/lhb_client/institution_survey_client/aif10_capability_client/industry |
| **M2 CLEAN** | L0→L1 qfq 归一 | `services/market_db/market_read/market_schema/kline_source` + `pipeline/clean.py` |
| **M4 SERVE** | 唯一取数+PIT | `services/data_access/` (resolver/spec/asof/keys/drivers) |
| **四地基** | universe/calendar/security_master + reference 库 | `services/universe/calendar/security_master` + `migrate_reference_db` |
| **编排** | 采集→清洗→存储 门链 | `services/pipeline/` (acquire/clean/store/run/stage_runner/stage_status/context) |
| **血缘** | acquire+consume DAG | `services/lineage/` + `chunkyctl lineage` |
| **治理** | 审计/质量/留痕/门 | `services/audit/data_audit/data_quality/data_deletion/data_deprecation/storage_retention/source_watermarks/source_policy/sandbox_guard` + `.moth/` + `check_*.py` |
| **DB infra** | schema/连接/路由 | `services/db/duck_adapter/database_manifest/schema_core/schema_migrations/schema_versions/schema_layer_filter/schema_marts/primitives` |

**治理 mart (11, KEEP)**: mart_data_health / mart_pipeline_run_manifest / mart_data_source_watermark / mart_data_source_failure_queue / mart_data_audit_report / mart_data_deletion_record / mart_data_deprecation_record / mart_global_data_quality_gate / mart_pipeline_lock / mart_step_fingerprint / mart_lineage。

**routers (KEEP)**: ops_manual_run (手动跑数据链) / v3_config / strategy_preset (配置)。无策略 serving HTTP API。

## 4. 重建删除了什么 (history)

**代码 ~245 文件 git rm** (git 史可逆): 策略服务 (signals_v2/scoring/recommendation_universe/screening_engine/sector_momentum/industry_context_engine/event_engine/return_engine/risk_factors/holdings/business_facts/dossier/read_model/financial_client/pricing_policy*/portfolio*/experiment_*/leakage_detect + institution_*_read/stock_*_read + 50 workbench_*_read) + 25 子目录 (backtest/formula_engine/ml_*/optimization/portfolio*/strategies/selection/sentiment/picture/technical_states/research/paper_engine/...) + routers (signals/dossier/v3_picture/v3_paper/v3_selection/v3_portfolio_builder/stock_graph/workbench/market) + 69 策略测试 + tdxhub/akshare 源 client + leakage/harness 闸脚本。

**数据 ~40 表物删** (archive parquet 在 `data/archive/purge_processed/` + deletion_record 留痕, 可逆):
- L2 特征 panel: fact_feature_panel/segment_panel/signal_panel (23M 行)
- 主升浪 D1 GT: fact_rally_ground_truth/entry_pit/entry_negative/episode_strata + fact_macd_episode_ground_truth (37万)
- 技术 stage: fact_stock_technical_stage/fact_rally_stage/mart_macd_state_history (9.2M)
- 财务 derived: fact_financial_derived/dim_financial_latest
- 事件聚合+处理 mart: fact_institution_event/fact_lhb_event/fact_risk_factors/mart_current_relationship/mart_stock_picture_daily/mart_stock_survey_activity + 死策略 mart (dual_confirm/sector_momentum/screening/formula_*/model_lifecycle/fund_flow_rank/feature_drift/...)

**schema DDL trim**: schema_marts + schema_migrations 移除 73 句非 KEEP mart 的 CREATE/INDEX/ALTER (防 init_db 重建)。

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
