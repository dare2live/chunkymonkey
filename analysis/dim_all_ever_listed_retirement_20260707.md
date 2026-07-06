# dim_all_ever_listed / dim_listing_status 整表退役 (2026-07-07)

## 决策链
1. 2026-07-06 全面数据审计 (`analysis/comprehensive_data_module_audit_20260706.md`) 发现
   `dim_all_ever_listed` 静默 stale — 无自动刷新契约, 与同批修复的 `dim_active_a_stock`
   (同样发现 stale, 已修 auto-refresh) 不同, 深挖后判断其为 vestigial 表, 留作决策点
   未直接补刷新脚本 (见 `PROJECT_INDEX.md` 2026-07-06 changelog "决策点留用户"一节)。
2. 用户问"数据基础是否具备开展后续工作条件, 对照本轮方案看是否全部完成" → 呈报此决策点。
3. Workflow 多角度核证 (4路独立 scan + 2路对抗 verify, 见下"证据"): 确认该表**无存活
   writer**(冻结 10+ 周), 其唯一真业务下游 `dim_listing_status`(经 `build_dim_listing_status.py`
   构建)本身也**零消费方**, 整条链是死胡同。
4. 用户拍板选项 A: 退役, 且要求全面更新项目文档。

## 证据 (workflow 4 scan + 2 verify 摘要, 全文见 session transcript)

| 维度 | 结论 |
|---|---|
| 谁写 `dim_all_ever_listed` | 无。原 builder `survivorship.py::build_dim_all_ever_listed()` 已于 2026-04-24 (`f68a67cc`) 随 SEF 模块清理一并删除, 之后从未有替代 writer。唯一"写入"痕迹是已硬废弃(`raise SystemExit`)脚本 `migrate_reference_db.py` 的一次性搬库拷贝, 非重建。 |
| 数据新鲜度 | 直接查库实测: 5210 行, `MAX(updated_at)=2026-04-21T15:59:06`, 10+ 周零刷新。 |
| 谁读 — 活跃但冗余 | `data_audit.py::_check_cross_table_consistency` 的 `inactive_still_trading` 子检查(经 `pipeline/clean.py` 每次 sync 后跑) —— 但该子检查存在的意义是"拿外部 is_active 声明去比对 K 线活跃度", 项目已确立 K 线本身即活跃真相源(`universe.py` 头注), 一旦外部声明源(此表)本就该退役, 子检查便无比对对象, 沦为空转。 |
| 谁读 — 死链终点 | 唯一"下游依赖" `build_dim_listing_status.py` **零生产调用方**(仅 `__main__` 和自身测试引用), 其产出表 `dim_listing_status` 在全代码库 `backend/services/`、`backend/routers/` **零业务读者**, 唯一有价值列 `delisted_date` 也零消费方(仅归档分析文档提及)。 |
| moth 治理 | 3 条断言(`section9-dims-in-reference`/`reference-dims-have-pk`/`section9-dims-absent-smartmoney`)硬编码 `IN(4表)`/`count==4`, 是元数据自省(表存在+主键), 非业务消费, 已同步改 `count==2`。 |
| 易踩陷阱 | `data/archive/lifecycle/dim_all_ever_listed.parquet`(2026-06-27 归档)**不是**已退役证据 — 那是 §9 拆库时 smartmoney 旧副本的归档, 当前活表在 `reference.duckdb`, 从未被处理过。 |
| 项目自身定性 | 与 CLAUDE.md §4.5 "反例"记录的"快照比对判退市→573只误标"是同一张表; `universe.py` 开头文档已宣告"不需要 dim_all_ever_listed"。 |

## 执行 (2026-07-07, 选项 A)

1. `backend/config/data_audit_rules.yaml` — 删 `inactive_still_trading` 规则块。
2. `backend/services/data_audit.py` — `_check_cross_table_consistency` 去掉整个 inactive-check
   分支(含 fallback 配置项), 只保留 `kline_universe_coverage`(北交所/非A股板块 leak 检测,
   不依赖此表, 独立保留)。
3. `backend/services/universe.py` — 整段删除 `audit_strategy_universe_contamination()`
   (0 生产调用方, 唯一功能是审计已退役的策略预测表)。
4. `backend/scripts/build_dim_listing_status.py` + `backend/tests/scripts/test_build_dim_listing_status.py`
   — 整体物删(git rm)。
5. `backend/tests/test_universe.py` — 删 `test_audit_contamination`; 清理 2 处死 fixture
   (`test_get_active_universe`/`test_get_active_universe_excludes_index_not_in_dim_active`
   创建 `dim_all_ever_listed` 表但被测函数从未查询, 纯历史遗留噪音)。
6. `.moth/assertions/claims.yaml` — 3 条断言 `IN(4表)`/`count==4` → `IN(2表)`/`count==2`。
7. `backend/config/data_layers.yaml` — 删两表登记条目。
8. `backend/config/duckdb_connect_policy.yaml` — 删 `build_dim_listing_status.py` 白名单条目。
9. `backend/services/schema_versions.py` — 删 `dim_listing_status` 版本登记。
10. `backend/services/data_access/resolver.py`、`backend/services/primitives/__init__.py`、
    `backend/services/primitives/ddl.py`、`backend/scripts/data_health_snapshot.py`、
    `backend/scripts/migrate_reference_db.py` — 更新文档性注释, 反映当前状态(不改写历史事实,
    只加状态头注)。
11. `analysis/lifecycle_delete_manifest_dim_all_ever_listed_20260707.yaml` — 物理执行:
    `db_lifecycle_delete.py --manifest ... --execute`(archive 到 parquet + 写
    `mart_data_deletion_record`, 非裸 DROP)。**执行前已清空 code 引用, 预期不需要 `--force`**
    (与 §9 Stage E 那次不同, 那次是"迁移后 smartmoney 冗余副本"仍被 live 面正确引用 reference
    真身, 这次是"表本身整体死亡", 引用已提前全清零)。

## 结果

- 全量测试: 626 → 621 passed (删 5 个测试: `test_audit_contamination` 1 + `test_build_dim_listing_status.py` 4)。
- 物理: `dim_all_ever_listed`(5210行) + `dim_listing_status`(5210行) 从 `data/reference.duckdb` 归档删除,
  archive 见 `data/archive/lifecycle/`, `mart_data_deletion_record` 留痕。
- reference.duckdb 现存 dim 从 4 张(active/trading_calendar/all_ever_listed/listing_status)
  减至 2 张(active/trading_calendar)。
