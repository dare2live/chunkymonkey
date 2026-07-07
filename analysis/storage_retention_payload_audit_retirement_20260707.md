# storage_retention.yaml payload_audit 整段退役 (2026-07-07)

## 背景

`raw_aif10_peer_valuation` 退役收尾时留了"顺带发现": `storage_retention.yaml` 里
`mart_stock_picture_daily` 的 payload-size 保留策略条目, 对应表已物删。用户要求"研究解决
方案", workflow 调研发现这不只是单条目问题——同一个 `payload_audit.reviewed_columns`
段落里还有其余 9 条同类条目, 且唯一消费脚本 `audit_storage_payloads.py` 本身也已物删。
用户拍板"B: 删这7行+判断整个段落是否该删"。

## 证据

1. **消费脚本已死**: 唯一原本会读 `payload_audit`/`reviewed_columns` 这个 key 的脚本
   `backend/scripts/audit_storage_payloads.py` 已随 2026-06-16 前后代码层 reset(commit
   `639e0dfb`)物删, 磁盘不存在(含 `__pycache__` 都无残迹)。
2. **现存2个治理脚本都不读它**: `audit_storage_retention_consumers.py` 实跑
   verdict=PASS, JSON 输出不含此 key(只遍历 `policy.table_inventory`); `check_legacy_flow_integrity.py`
   实跑 overall=PASS, 其 C3 检查只对照 `data_layers.yaml`。全仓库 `grep payload_audit`/
   `reviewed_columns` 除 `storage_retention.yaml` 自身外 0 命中。
3. **`storage_retention.py` 加载器不读这个 key**: 走查 `load_storage_retention_policy()`
   实现, 只提取 `defaults`/`candidate_feature_panels`/`model_prediction_tables`/
   `protected_artifact_tables`/`table_inventory`/`model_file_roots`/`optuna_study_roots`,
   多余 key 被 `yaml.safe_load` 静默忽略, 不会因缺失 `payload_audit` 报错。
4. **逐表核实全部 8 张表**(`fact_technical_trigger`/`mart_macd_state_history`/
   `mart_stock_picture_daily`/`mart_today_signal_cache_signal`/
   `mart_architecture_inventory_asset`/`mart_paper_sim_kpi`/`mart_strategy_result_registry`
   [段内注释已自行标注 `@archived wiped-2026-06-14-reset`]/
   `mart_stock_formula_optuna_bestchoice_v1`)在全部 7 个 DuckDB 文件(etf/experiment_store/
   feature_store/market/reference/smartmoney/tushare_raw)里逐一查 `duckdb_tables()`,
   **无一存活**——2026-06-28 纯数据平台重建批已把这些策略/形态/信号/建筑清单类 mart 表
   全部物删。

**结论**: 是双重孤儿(消费脚本已死 + 全部数据源已死), 不是"头痛医头"只删单条目能解决的
情况——整个 `payload_audit` 段落(top-level 设置 `max_value_warn_bytes` 等 + 全部 10 条
`reviewed_columns`)本身就是废弃代码遗留下的死配置。

## 执行 (2026-07-07, 用户拍板"B")

删除 `backend/config/storage_retention.yaml` 第 8-129 行整个 `payload_audit:` 段落
(top-level 设置 + `payload_column_name_tokens`/`recursive_keywords`/`path_markers` 三个
辅助清单 + 全部 10 条 `reviewed_columns` 条目), 替换为一行说明性注释记录退役原因。

## 验证

- 全量测试 617 passed(无测试引用此 key, 数量不变)。
- `audit_storage_retention_consumers.py` / `check_legacy_flow_integrity.py` 两个现存治理
  脚本 + `plan_storage_retention.py` 计划脚本均实跑确认 PASS/无错误。
- `chunkyctl doctor --fast` / `moth assert` 保持全绿。
