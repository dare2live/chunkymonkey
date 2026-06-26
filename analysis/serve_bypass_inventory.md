# SERVE 绕过清单 — 数据流执法基线 (2026-06-23)

> **[状态校正 2026-06-26]** 本清单的**迁移工单部分已实质完成** —— `--bypass-scan` **实测当前 consumer_bypass_violations=0**
> (P0 data_loaders 已迁 SERVE / build_segment_panel+build_signal_panel 是 `build_` 前缀加工成员非违规 / signals_v2+stock_detail_read 已收口)。
> 2026-06-26 最后 4 个"违规"经核全是 roster 漏登的合法成员 (lineage 元数据 infra + lhb/org_holding_aif10/qfii 采集层 watermark 读), 已补 data_module_members.yaml → 0。
> moth `serve-consumer-bypass-zero` 棘轮 ==0 真绿锁死 (新增非成员消费者内联 raw = 硬红)。**下方 P0/P1/P2 表留作分类法 + 历史溯源参考, 不再是 active 工单。**
> 真正剩余的 Gap1 深层工作 = 逐 builder PIT 锚正确性审计 (build_ 成员读 canonical 时 asof 是否正确), 非"绕过点"问题。

> owner=task#55 (Gap1) + task#56。来源: 数据流地图 Workflow wv6dp6o0k + 主会话 grep 核证。
> 用途: "强制数据只能从数据管理模块按流程取"(用户诉求)的迁移工单 single source。
> 纪律: 按真金白银/根性排序; 区分**必迁**(消费 PIT 数据该走 SERVE)vs**合法豁免**(infra/审计/写侧/真相源)。

## 0. 核证事实 (measured)

- `check_serve_read_layer.py` D1/D2 门 **只 hardcode 扫 `dossier.py` 一个文件** (line 28 `DOSSIER=`, line 53/63 `_read(DOSSIER)`) → "violations=0 全绿"是**伪绿**, 只证 dossier 干净, 其他文件不在扫描范围。**这是执法最大缺环: 门存在但覆盖面=1。**
- grep `FROM raw_|FROM price_kline|FROM market.|duck_connect(|duckdb.connect(` 命中 **58 文件** (backend/services+scripts, 排 test/sandbox/data_access层)。
- 走 SERVE 正路 (import DataAccess): 仅 **4** — dossier.py(合规) / signals_v2.py(混合) / technical_states/limits.py / check_serve_read_layer.py(门自身)。

## 1. 必迁 (MIGRATE) — 消费 PIT 数据该走 SERVE, leakage 风险

### P0 真金白银 (喂模型/算收益, 漏 PIT 锚 = leakage 直接进策略)
| 文件 | 角色 | 风险 |
|---|---|---|
| `services/data_loaders.py` | builder 取数底座 (feature_panel 经它读 L0) | 上游污染下游全部因子; 无 asof 锚 |
| `scripts/build_segment_panel.py` | L2 形态面板物化 | 直读 raw 算 L2, 绕分层+PIT |
| `scripts/build_signal_panel.py` | L2 事件信号面物化 | 同上 |
| `services/return_engine.py` | 收益/excess 计算 | 回测分母, 口径错=年化错 |
| `services/market_read.py` | K线读 helper | serving K线入口, 边界模糊 |

### P1 (引擎/builder/client 消费)
| 文件 | 角色 |
|---|---|
| `services/signals_v2.py` | 信号引擎 (已 import DataAccess 但 10 内联 SQL 混合 = 半合规假象最危险) |
| `services/financial_client.py` / `capital_client.py` | 财务/资金消费 (季度 ann_date PIT 靠人写) |
| `services/market_perception/regime_engine.py` | regime 消费 |
| `scripts/build_macd_state_history.py` | L1k MACD 中间层物化 |
| `scripts/build_rally_ground_truth.py` / `build_rally_entry_pit.py` / `build_rally_negatives.py` / `build_macd_episode_ground_truth.py` | GT/标签 builder 读 K线/panel |

### P2 (展示读, 风险低)
| 文件 | 角色 |
|---|---|
| `services/stock_detail_read.py` | 档案展示读 |
| `scripts/build_picture_daily.py` / `build_dim_listing_status.py` | display/L1 物化 |

## 2. 合法豁免 (EXEMPT) — 加 `# serve-exempt: <理由>` 白名单, 不迁

| 类 | 文件 | 豁免理由 |
|---|---|---|
| **infra/连接/写侧** | `duck_adapter.py` `db_connection.py` `market_db.py` `market_schema.py` `etf_db.py` `experiment_store.py` `security_master.py` `data_sources/sync_runner.py` `perf/shard_runner.py` `update_watermark_sla.py` | SERVE 建在它们之上, 强迁=循环依赖; 写侧/DDL/采集 runner 本就不是消费 |
| **审计工具** | `audit.py` `data_audit.py` `data_health_snapshot.py` `data_layer_audit.py` `leakage_probe.py` `audit_data_completeness.py` `audit_delivery_readiness.py` `audit_pit_coverage.py` `audit_tdx_data_need_coverage.py` `db_dead_table_audit.py` | 审计本应直查真相源 (不能经被审对象的读层) |
| **门脚本** | `check_serve_read_layer.py` `check_universe_filter.py` | 门自身 |
| **DB 生命周期/迁移** | `db_compact.py` `db_lifecycle_delete.py` `db_partition_migrate.py` `cleanup_holder_dup.py` `migrate_holders_to_tdxhub.py` `plan_storage_retention.py` `build_experiment_store.py` | 库管理工具, 非数据消费 |
| **真相源自身** | `universe.py` | universe 是交易日历级真相源, 直读 K线判在市 (第零条真相源不能依赖派生读层) |

## 3. 随源退役处理 (SOURCE) — 不单独迁, 随 task#56 切源/退役一并处置

| 文件 | 源 | 处置 (见 §4.3 + 处置矩阵) |
|---|---|---|
| `tdx_affair_client.py` `tdx_f10_extra_client.py` `ingest_holders_tdxhub.py` `build_tdx_gpcw_auto_features.py` `profile_tdx_gpcw_fields.py` `build_lhb_events.py` | tdxhub | hot_backup_keep (切主源后冻结留表) |
| `institution_survey_client.py` `ingest_profit_forecast_snapshot.py` `check_sina_tdxhub_overlap.py` | aif10/akshare | retire (双轨核对后退役) |
| `build_price_kline_tdxhub.py` | tdxhub | orphan (表已 M3 物删, 脚本 dormant 待删) |
| `measure_form_separation.py` | 一次性 analysis | 评估后入 sandbox 或删 |

## 4. 执法计划 (棘轮 WARN→硬FAIL, 禁 big-bang)

1. **暴露真相 (零风险, 可逆)**: check_serve_read_layer 加 `--inventory` 全量扫模式 (exit 0 WARN), 不动现有 dossier 硬门 (moth 保持绿)。把本清单做成机器可重生。
2. **补 data_access 实体**: 迁 builder 前先把 builder 所需源补进 data_access.yaml (现 23 实体 < 所需)。
3. **逐迁 P0→P1→P2**: 每迁一个, 前后数值一致验证 (纯口径统一不该改值) + 加 `serve-exempt` 给豁免类。
4. **棘轮收紧**: 一类迁完, 该类纳入硬门扫描范围 (violations 不许增长, 对标 universe `assert_universe_clean`)。
5. **采集侧**: 9 个 hardcoded heredoc 采集步收编进 sync_registry (派生/物化步豁免), safe_commit 检 daily_update 无外部采集 heredoc。

**起步 = 第1步 (暴露真相)**, 然后 P0 第一个 = `data_loaders.py` (builder 取数底座, 迁它=feature_panel 自动走 SERVE)。
