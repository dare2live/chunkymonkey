# tdx F10 freshness 机器退役 — 2 张冻结表 triage 收口 (2026-06-24)

> 状态: DONE。本次收口 tdx F10 退役遗留的最后 2 张"待 triage 冻结表" +
> 它们挂着的整个 `_check_tdx_f10_source_availability` data_quality 机器 (已验证全死)。
> 接续: commit `f5a362c8` (holder 退役收口) + `0b5f77aa` (tdx F10 3产品迁移/归档)。
> 真相源政策 owner = CLAUDE.md §4.3 (tushare 唯一 + aif10 例外)。

## 1. 触发与背景

`data_quality.py` 的 `F10_SOURCE_AVAILABILITY_TABLES` freshness 注册表里挂着 2 张
tdx F10 退役遗留表, 注释标 `TODO 待 source-equiv triage`:
- `fact_fund_holding_tdx_f10` (公募基金持有本股, tdx F10 第7章)
- `fact_shareholder_trade_tdx_b` (股东增减持实际交易)

前 3 张同批表 (`fact_holder_count_period`→tushare 迁移 / `fact_shareholder_plan_tdx_f10`
+ `fact_common_major_holder_stock`→归档冻结) 已收口, 这 2 张是尾巴。

## 2. 审计发现 (2 只读 Explore agent + 主会话亲核, mythos§14 不信枚举)

### 表内容核证 (全 7 库直查)
| 表 | 存在? | 历史 | 结论 |
|---|---|---|---|
| `fact_fund_holding_tdx_f10` | **不存在** | 2026-06-14 地基-reset 物删 17145 行 (lifecycle_delete_manifest) | 无数据 |
| `fact_shareholder_trade_tdx_b` | **不存在** | 孤儿骨架, 无任何 sync/ingest 写入路径 | 无数据 |

### Fan-in 审计 (零 live 消费方)
- **零写入路径**: 全仓 `rg` 仅 `clients_registry.py:324` 一处死 builder (`build_candidate_feature_panel`,
  写 `@archived` 的 candidate 表) 的 `upstream_source` 元数据字符串提及 fund_holding。
- **DDL 重建路径** (休眠态): `schema_core.py` CREATE TABLE IF NOT EXISTS + `schema_migrations.py`
  index/ALTER。但 `schema_layer_filter.filter_schema_sql` 按 `data_layers.yaml` 过滤 —
  这 2 表**不在 data_layers.yaml 任何层** → 不在 keep 集 → CREATE/ALTER 在 schema-init 时被滤除
  (这就是物删后空壳没被重建的真因; 层过滤器已中和重建循环)。

### `_check_tdx_f10_source_availability` 整函数已死 (3 检查对象全死)
| 检查对象 | 状态 |
|---|---|
| `F10_SOURCE_AVAILABILITY_TABLES` (2 表) | 不存在 + DDL 被层过滤 |
| `plan_table = None` | 前一会话归档时已置 None (死守卫, 永不执行) |
| `initial_plan_table = mart_shareholder_plan_initial_event` | 表不存在 + builder 文件 `build_shareholder_plan_initial_event.py` 已删 + 不在 daily DAG → 永不重建 |

实测: 该函数 5 个单测中 **4 个当前就 FAIL** (前一会话部分退役留下的失效断言:
移除 fact_holder_count_period / 置 plan_table=None 后没更新测试), 1 个 pass (自建 mart 测死逻辑)。
= 半退役破损态, 不是本次引入的债。

## 3. 源等价初判 (供参考, 本次无迁移动作)
| 退役表 | tushare/aif10 等价 | 说明 |
|---|---|---|
| `fact_fund_holding_tdx_f10` | tushare `fund_portfolio` (公募基金持仓) | 语义可覆盖, 但无 live 消费方 + 无现存数据 → 不迁移; 将来真需要再从 tushare 拉 |
| `fact_shareholder_trade_tdx_b` | tushare `stk_holdertrade` (实际增减持) | 同上 |

## 4. 裁决: 两表均 → **彻底退役骨架** (三选一)
- **不迁移** (无 live 消费方 + 无现存数据, 真需要再拉 tushare)
- **不归档冻结** (无数据可冻 — 一个 reset 物删、一个孤儿空壳)
- **退役**: 删源 DDL (断重建路径) + 删 freshness registry + 退役整个已死的
  `_check_tdx_f10_source_availability` 机器 + 删 5 个已红的死测试。

## 5. 本次改动 (验证: schema init 端到端实测 + 2 测试套全绿)
| 文件 | 改动 |
|---|---|
| `backend/services/schema_core.py` | 删 2 张表 CREATE TABLE IF NOT EXISTS 块 |
| `backend/services/schema_migrations.py` | 删 2 表 index/ALTER (4+2 行) |
| `backend/services/data_quality.py` | 删 `F10_SOURCE_AVAILABILITY_TABLES` 常量 + `_check_tdx_f10_source_availability` 函数 (439 行) + caller 块 |
| `backend/tests/test_global_data_quality.py` | 删 5 个死测试 (331 行) |
| `backend/services/data_sources/clients_registry.py` | 死 builder upstream_source 字符串移除退役表名 |

验证证据:
- schema init 端到端 (临时库, duck_adapter): 2 退役表 absent / 2 归档表 present(活层) / 无错。
- `test_global_data_quality.py` 31 passed; `test_workbench_read.py` (前端读层 contract) 13 passed。
- 全仓 `rg` 残留: data_quality 0; 代码层仅文档/已 flag 后续。

## 6. 已 flag 的独立后续清理 (不在本次, 避免 blast radius)
1. **前端 workbench tdx_f10_source DQ 视图**: `workbench_tdx_f10_read.py` `build_tdx_f10_source_dq_view()`
   查 domain=`tdx_f10_source_availability` (本次退役后该 domain 不再产生 → 视图优雅空降级)。
   `/workbench/data-sources` 端点 + 前端组件 + contract/smoke 测试是一个独立前端退役单元。
2. **`mart_shareholder_plan_initial_event` 家族元数据**: 该 mart 已死 (无表/无 builder)。残留引用:
   `schema_versions.py:117`, `seed_dim_data_asset.py` (6 处: builder 映射/上下游/freshness/contract),
   `pipeline_performance_policy.yaml`, `test_workbench_read.py` + `test_workbench_frontend_render_smoke.py`
   (前端期望硬编码)。独立 asset-退役单元。
