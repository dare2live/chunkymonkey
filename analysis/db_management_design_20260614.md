# DuckDB 多库管理设计 (按写锁边界分区)

> 状态: 设计提案 (2026-06-14) — 执行前过 grill + 写入面全仓扫描。owner = 本文件; goal.md 薄指针。
> 缘起: 用户问 "为避免总有写锁, 趁部分数据未获取, 设计 DB 管理方案, 最大化 DuckDB 能力,
> 是否该把所有数据放一个巨库, 并考虑后续建更新管道是否方便"。
> 核证: database_manifest.yaml (库真相源) + db_split_runbook_20260612.md (上次拆分教训) + 实测表构成。

## 1. 问题: DuckDB 单写锁 × 单体热库

- **DuckDB 锁模型 = 单写者 per 文件** (一个文件同时只能一个写进程; 读可并发, ATTACH read_only 不争锁)。
- 现状: `smartmoney.duckdb` 26GB / **348 表** (mart 213 / fact 57 / dim 33 / raw 23 / 其他)。**所有写入方挤同一文件 → 全部串行**。
- 实证痛点: `build_feature_panel` 与 `daily_update` 都写 smartmoney → 不能并发 (本 session 反复撞)。

## 2. 第一性原理

- **一个 DB 文件 = 一条写锁边界**。要写并行, 就把"独立写入方"放到独立文件。
- 跨文件读 = `ATTACH ... (READ_ONLY)` (不争写锁); 项目已有此模式 (workbench attach market)。
- 项目**已经在这么做**: `sync_registry` 把新 tushare 域路由到 `tushare_raw.duckdb`「与主库写锁解耦」; manifest 已分 smartmoney/market/alpha158/etf 四库。**方向对, 只是 smartmoney 这个 348 表单体还没按写入节奏拆开。**

## 3. "要不要一个巨库" — 否

| 方案 | 写锁 | 判定 |
|---|---|---|
| 一个巨库 (全放一起) | 所有写入串行 = 最差 | **否** — 正是现在 smartmoney 的痛点放大版 |
| 按写入节奏分区 (本方案) | 独立写入方并行 | **是** — DuckDB-native 拿写并行的唯一方式 |
| 拆成 348 个小文件 | 写并行但 JOIN 地狱 + 事务不能跨文件 | 否 — 过度拆分 |

**结论: 不放巨库, 也不碎成几百个; 按"写入节奏 tier"拆成 4-5 个文件 (sweet spot)。**

## 4. 设计: 4 写入节奏 tier (每 tier 一个独立写锁)

| Tier | 文件 | 装什么 | 写入方 (节奏) | 现状 |
|---|---|---|---|---|
| **源 (source)** | `tushare_raw.duckdb` + `market.duckdb` | tushare raw 全域 + K线/日历/benchmark + smartmoney 里 23 个 raw_* 移出 | sync_runner (每日 append-only) | 部分已分; raw_* 待移出 |
| **特征 (feature)** | `feature_store.duckdb` (新/并 alpha158) | fact_feature_panel + 下游 (drift/prune/validation) + alpha158 因子 + 研究 panel | build_feature_panel 等 (周期重算, 重) | 现混在 smartmoney+alpha158 |
| **服务/控制 (serving)** | `smartmoney.duckdb` (瘦身) | 213 marts + scores + governance + model outputs + live 服务表 | daily_update + 在线服务 (每日) | 瘦身后留这些 |
| **实验 (experiment)** | `experiment_store.duckdb` (新) | optuna 输出 + fact_optuna_governance_log + 新建 fact_experiment_verdict / fact_consumer_alpha_ic_scan / lineage / pit_audit_log | Optuna/Modal 验证 (突发, 重) | 现混在 smartmoney |

**收益 = 4 条独立写锁并行**: `sync 写源` ∥ `feature 重算` ∥ `daily 服务` ∥ `实验跑批` 互不阻塞。
跨 tier 读全走 ATTACH read_only。**本 session 撞的 feature_panel-vs-daily_update 写锁冲突直接消失** (分到 feature/serving 两库)。

## 5. 为什么方便后续更新管道 (用户关注点)

- **daily_update 只写 serving tier** → 不与 feature 重算 / 实验跑批撞锁, 跑得快、可与 sync 并发。
- **build_feature_panel 只写 feature tier** → 可与 daily_update 同时跑 (S0 撞锁问题根治)。
- **alpha 验证程序 (Optuna/Modal) 只写 experiment tier** → 永不阻塞 live 服务 (直接服务 alpha_validation_program spec)。
- **新数据 sync 只写 source tier** (现有 tushare_raw 解耦, 扩成全 raw)。
- 路由已有底座: `database_manifest.yaml` (库 alias) + `sync_registry.target_db` (新域声明去哪库) → 加 tier 即可, **零业务代码改** (registry-driven)。

## 6. 迁移方法 (06-12 教训, 不可重蹈)

- **必用 `EXPORT DATABASE` / `IMPORT DATABASE`** (schema.sql 含 PK/UNIQUE/索引), **禁 `COPY FROM DATABASE`** (只搬数据, 丢约束 = 上次 315→1, upsert 全 Binder Error)。
- **验收 v2** (上次漏的): 行数 + 抽样值 + **约束计数 + 索引计数 + upsert 冒烟 + 写入面全仓扫描对账** (静态扫所有 INSERT/REPLACE 目标, 不靠记忆)。
- 执行窗口: 交易日 03:00-16:00 (避开 daily_update); 预计 30-60min/tier。
- 拆 feature tier 前先做 panel 家族收敛决议 (8 变体留谁, 06-12 runbook G4 未完成项)。

## 7. 边界 / 反例 (别过度工程)

- **事务不能跨文件**: 必须原子写在一起的表 (如 builder 同事务写 A + validation B) 必须同库 → 分区前查事务边界。
- **marts 别过拆**: 213 marts 多是读重的服务输出, 同属 daily 管道, 留 serving tier 一起, 不按主题碎拆。
- **JOIN 成本**: 跨 tier JOIN 要 ATTACH; 高频 JOIN 的表尽量同 tier (如 feature_panel 与其 label 同 feature tier)。
- **不碎成 348 文件**: 4 tier 是 sweet spot — 够破主要写锁竞争, 又不让 JOIN/运维爆炸。

## 8. 时机 (用户洞察: 趁数据没获取前)

新数据 (forecast/income/cyq_chips/kpl_list...) **没落库前重构** → 它们直接进对的 tier
(raw→source, 派生特征→feature), **省二次迁移**。一旦先入了 smartmoney 再拆 = 多搬一次。
→ DB 分区应**前置于** alpha 验证程序的 S1 数据获取。

## 9. 落地分阶段 (与 alpha 验证程序 S0 并行)

| 步 | 内容 | 验收 |
|---|---|---|
| D0 | 写入面全仓扫描 → 每表标 tier + 事务边界 (定哪些必须同库) | 348 表 tier 分配表 + 0 事务跨库 |
| D1 | 建 experiment_store + 迁移 optuna/governance/实验输出 (EXPORT/IMPORT) | 约束/索引计数对齐 + upsert 冒烟 |
| D2 | 建 feature_store + 迁 feature_panel 家族 (panel 收敛后) + alpha158 | 同上 + feature build 写新库通 |
| D3 | source tier: smartmoney 23 raw_* 移 tushare_raw | 同上 |
| D4 | 扩 database_manifest.yaml (4 tier) + sync_registry target_db 路由 + ATTACH 配置 | manifest 驱动跨库读通 + daily_update ∥ feature build 实测不撞锁 |

## 10. 与 alpha 验证程序的关系

实验 tier (`experiment_store.duckdb`) **直接服务** alpha_validation_program_spec: (数据×消费者) 验证的
OOS/ablation/verdict 全写实验 tier, 与 live 服务隔离 → 验证跑批永不阻塞每日更新, 且留档表集中一库便于 query。
**DB 分区 (D0-D4) 应先于或并行于验证程序 S0/S1。**
