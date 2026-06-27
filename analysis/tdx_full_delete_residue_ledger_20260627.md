# 通达信全删 / 非tushare源退役 — 零残留收尾 ledger (2026-06-27)

> autonomous loop + ultracode Workflow (wf_8585638e 残留审计 6agent 467K tok + 对抗验证) 驱动。
> 真相源 = 各 commit + deletion_record; 本 ledger 是 cleaned vs flagged 的人话总账。

## 已清 (actionable dead 残留, 全 commit + 验证)

| Batch | 内容 | 量 |
|---|---|---|
| #13a financial_client | dead sync body 整段物删 (gpcw sina/akshare 抓取 + sync_financial_data, 0-live-caller) | 1699→715 行 |
| #13b tdx_source | server_health DB 函数整段物删 (ensure/record/load+helper); 保 live circuit-breaker | -~200 行 |
| C1 dead 脚本 | holders_resolver/migrate_holders_to_tdxhub/check_sina_tdxhub_overlap + 2 test + .pyc; 清 connect_policy/test_registry/module_members/claims | 5 文件 |
| C3 price_kline_tdxhub+gpcw config | market_db.upsert_price_kline_tdxhub_rows(0caller) + risk_factors/audit_data_completeness/source_watermarks 死表 ref + seed/clients_registry + feature_registry industry→gap | 7 文件 |
| C4 schema f10_extra | schema_core CREATE 块 + schema_migrations 5 行 (raw_tdx_f10_extra_parse_status, gone 表 index 风险) | 2 文件 |

**akshare M4 (前序 same session)**: capital 7表 + event 3表 + attention 2表 物删 (archive+deletion_record) + 切消费侧 + 退役 5 source + db_compact 3.0→0.9G + data_health 删表必删caller修。

**验证 (最终)**: CI offline 90 passed · moth 45/0/0 · data_layer_audit PASS(76/0/0) · 全包 import 0 坏(除下方flagged) · 测试收集 1225/1258 无 import 错。
**loop-until-dry round1**: 0 live import/call 已删模块 (holders_resolver/capital_client/external_attention/tdx_affair/server_health DB/financial sync body) → DRY。

## Flagged (未清, 各有 owner/track, 非 actionable 残留)

| 项 | 为何不清 | owner/track |
|---|---|---|
| schema_marts mart_tdx_gpcw_auto DDL (5表) | undeclared=schema_layer_filter 过滤=从不执行=inert; SQL-string 手术风险>收益 | 低优先 follow-up |
| source_watermarks + audit kline_daily (tdxhub/akshare spec) | table_missing 优雅降级; 正解=整体 repoint tushare canonical | **M3 kline 源迁移** |
| tdxhub source adapter capabilities (financial_gpcw_8q 等) | 整个 tdxhub adapter 退役 + 有 contract test | **M3 tdxhub adapter 退役** |
| tdx_data_need_coverage audit 子系统 (脚本+yaml+mart 表) | live-ish audit_tdx_data_need_coverage.py 写; tdx 全退后该 audit 是否还需 = 判断 | 子系统去留决策 |
| 档B alpha metadata (panel_pipeline_manifest/field_dictionary capital_flow_pit/financial_pit_daily) | reset alpha 流水线脚手架; 引 GONE 表但 metadata 非执行; 档B 重建时重填 | **档B alpha** |
| workbench build_tdx_server_health_view | _relation_exists 守卫优雅降级(空); 耦合 2 contract test fixture | 低优先 (server_health 已退役展示残) |
| market_perception.regime_engine 坏 import | 缺 .utils, 0 importer 孤儿死; **非通达信全删** = market_perception reset 残留 | **spawn_task task_80035be8** |
| _enrich_events_with_gpcw | 名 legacy 但实为 live (加载 forecast/survey live 源 map) | 非死, 保留 |
| financial_gpcw_8q SLA (update_watermark_sla) | 已 repoint 查 live fact_financial_derived (功能正常, 名 stale); rename 风险>收益 | functional, 留 |

## 预存在失败 (非本轮引入, HEAD 前已 fail)

- doctor data_health red=1: `fact_common_major_holder_stock` writer 432h stale = **aif10 org_holding builder 未刷新该 fact 表** (aif10 迁移 freshness gap, 非通达信全删)。
- test_audit_financial (dim_stock_dc_industry 缺表) / test_audit_tdx_data_need_coverage (evidence 文件) = 需 DB fixture, 不在 offline CI 集。
