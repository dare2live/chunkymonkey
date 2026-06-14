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

## 11. 执行决定 (2026-06-14 探索后定案)

D0 (写入面扫描) + 保真迁移引擎 (db_partition_migrate.py) + 两次实测保真迁移 (experiment 25 表 / feature 2 表,
行数/EXCEPT/约束/索引全 PASS) 完成 → **引擎与设计已证明可用**。但 **cutover (repoint 写入方 + 读取方 ATTACH +
DROP 源) 暂缓**, 理由 (实测 blast radius):

- **D1 (experiment)**: 25 表被 live 每日推荐重度读 (58 文件 / 7 live 链路) → 迁出反而增 live 跨库耦合, 收益小。暂缓。
- **D2-minimal (feature)**: fact_feature_panel 是 **106-读取方中心表** (52 中央helper / 47 继承conn / 7 raw)。cutover 需
  get_conn 默认 ATTACH feature_store + smartmoney 建 view 兜读取方 — 给**每个连接加永久 always-attach + view 间接层**。
  而竞争 (build_feature_panel vs daily_update) 在**手动工作流下罕见** (本 session 顺序跑 0.3min 没真撞)。
  **为罕见负载建永久基础设施 = architect rule6 反模式** (同策略立方体/Option A 的判断)。暂缓。

**结论**: 保留引擎 + 设计 + tier 配置 + D0 图作为 ready 资产; stale 验证副本已删 (源 smartmoney 全程未动)。
**cutover 触发条件**: 竞争真咬人 (并发跑批密集) 或上云自动化 (并发常态) → 那时引擎可几分钟完成任意 tier cutover。
当前数据底座主线回到 alpha 验证程序 (找 base-edge) — DB 分区是支撑性基建, 不阻塞主线。

## 12. Cutover-free 优化 (2026-06-14 探索, 用户"继续探索优化 DB 管理")

cutover 暂缓后, DB 优化转向**不动读写路径**的两条线:

### 12.1 关键原则: 渐进分区 ("新表走新库, 旧表不动")
**新数据/新表无历史读取方 → 直接落独立库 = 零 cutover churn**。这优雅绕过了迁中心表的 churn:
- **alpha 验证程序新表** (`fact_experiment_verdict` / `fact_consumer_alpha_ic_scan` / `pipeline_artifact_lineage` / `experiment_pit_audit_log`):
  无历史读取方 → S0 建表时**直接落 `experiment_store.duckdb` 新独立库** (database_manifest experiment status planned→active), 不放 smartmoney。
  消费方 (Optuna/Modal 验证脚本) 本就在实验 tier 工作。约束 (写文档): **实验表不可被 live daily 消费** (否则需 view 代理)。
- 原则: 旧中心表 (fact_feature_panel 等) 不迁 (cutover churn 大); 新表逐个落对的库 → 分区随时间自然长出。

### 12.2 Cutover-free 瘦身 — 实测纠偏: **没有免费午餐** (2026-06-14 工具核证)

探索 agent 声称 ~10G "0-ref 死表"可删, 但 **`db_dead_table_audit.py` 保守工具 (0行 AND 0字面引用) 实测推翻**:
- 30 张 0 行表**全部受保护** (代码有 schema 定义/writer 引用), 真死表 = **0**。
- agent 列的"历史版本可删"表实测**非测试引用 4-24 个/张** (v3=22 / lambdamart_v6=19 / candidate=24 / tdx_keep=11 / unified_v1=4)。
  它们有 builder+reader+schema, **不是死表**; 盲删会破坏 50-100+ 文件。

**结论 (对抗验证价值)**: agent 的死表清单 plausible 但错 (用窄 SELECT/JOIN/INSERT 匹配漏了 builder/schema/其他引用形)。
**smartmoney 几乎无真死表; "免费瘦身"不存在** —— 历史表都带 builder+legacy reader, 删 = 逐表移 builder+迁 reader 的真 refactor (非 cutover-free)。
→ 瘦身**降为低优先级支线** (vs alpha 验证主线); 真要做须逐表评估 (remove builder + 迁/弃 reader + EXPORT + DROP), 不批量盲删。
`db_dead_table_audit.py` (保守, 0行+0引用才判死) 作为**持续防误删的守门工具**, 周期重跑; 当前判 0 可删 = 安全。

### 12.2b 真正的 DB 最佳实践 (持续, 用户"随新数据/回测/日更持续探索")
- **渐进分区** (§12.1): 新表走新库 = 唯一 churn-free 优化, 主推。
- **守门**: `db_dead_table_audit.py` 周期跑 (新死表累积时才删, 永不盲删有引用表)。
- **增长监控**: tushare_raw append-only +2-4G/S1; smartmoney DROP 后 CHECKPOINT; data_health 加 db_size/disk_free 告警。
- **教训**: 探索 agent 的"可删"清单必须工具核证引用 (本次挡住误删), 不直接执行。

### 12.4 实测执行: lifecycle 分析删出 62M 行 (2026-06-14, 用户"有引用≠不能删, 请分析")
§12.2 "0-ref 规则找 0 可删"是**过度保守** —— 用户纠正: 有引用≠不能删, 若引用是死代码 (旧 builder/废实验/测试) 且表是过时派生 (非原始数据), 可删。
**lifecycle 分析 (workflow) + 对抗核证 (live 路径 0 引用)** 找出 11 张真过时表, **实删 61,938,652 行**:
| 表 | 行 | 过时原因 |
|---|---|---|
| mart_p0b_lambdamart_v6_predictions | 23.2M | v6 被 v7/v4 champion 取代, daily 不调度 |
| fact_tdx_gpcw_auto_feature_quarterly | 16.4M | gpcw 自动特征实验, 不在 live 链 |
| mart_stock_regime_full(138列)/fact_candle_pattern_daily/fact_feature_panel_candidate/mart_temporal_research_panel/mart_shareholder_plan_initial_feature_panel/mart_p0a_feature_label_panel_unified_v1/mart_synergy_policy_mtm_position/mart_stock_horizon_feature_effect/mart_unified_v1_oos_predictions | ~22M | 废实验/研究/中间产物/superseded panel |

执行: 事务 DROP + CHECKPOINT; **6 live 表 (v4/v5/label_panel/fact_feature_panel/mcap_decile/industry_beta) 全完好**。
post-fix-audit: dim_data_asset 11 删表条目 DELETE (16→12 blockers, 删表 fallout 清零)。
**对抗核证又救场**: agent 标 mcap_decile/industry_beta 可删, 实测各 2 live 引用喂 v4 → KEEP (挡住误删)。
**遗留 (低优先级)**: (1) 文件仍 25G — DuckDB CHECKPOINT 释放内部块 (未来写入复用防增长), 文件缩需整库重写 (EXPORT/IMPORT 或 db_split_execute逐表重建, 26G 大操作另案); (2) 死 builder 脚本 (build_candle_pattern_daily/build_mart_stock_regime_full/build_ensemble_v7_*/train_unified_ranker_v1 等) 未删 — 不在 live 链 (跑了会 CREATE 空表无害), 单独清理另案。
**正确结论 (修正 §12.2)**: 死表清理**不能靠 0-ref 字面规则** (过保守, 找 0); 须 lifecycle 分析 (live-current/superseded/dead-experiment) + live 路径对抗核证, 才能安全删出真过时表。db_dead_table_audit 守 0行0引用底线; 大表过时判定走 lifecycle 分析。

### 12.3 卫生 + tushare_raw 增长
- tushare_raw: S1 基本面四件套预计 +2-4G (append-only, 无需 VACUUM; DELETE 才评估)。
- smartmoney: 现无需定期 VACUUM; DROP 死表后 CHECKPOINT 回收。补 `chunkyctl storage --checkpoint` 包装 + data_health 加 db_size/disk_free 比率告警 (阈 0.6)。
- **时序**: G4 panel 收敛应在 S1 数据入库后评估 (防 S1+收敛并行短期破 30G 线)。
