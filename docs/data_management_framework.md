# 数据管理框架 (Data Management Framework)

> 立于 2026-06-14 地基-reset 后。owner = 本文件 + `backend/config/data_layers.yaml` (注册表) +
> `backend/scripts/data_layer_audit.py` (执法) + `.moth/assertions/claims.yaml` (自动断言)。
> 目标: 根治"层级隐式 → 反复推导 + 耦合到无法干净分离 + 删改靠 whack-a-mole"的问题。

## 1. 为什么有这个框架 (经验教训)

2026-06-14 把 smartmoney 从 339 表/26.6G reset 回 85 表/2.5G (删整个模型/特征/寻优层)。清理**极痛**, 根因是当初**没按"最小化模块 + 最小化数据表 + 配置驱动"设计**:

| 症状 | 根因 | 后果 |
|---|---|---|
| 跑 4 个 workflow 反复推导"这表属哪层" | 层级**隐式**, 无声明 | 每次数据管理都要重新分类, 不可复用 |
| 删代码靠 import 闭包但不可靠 (漏相对/动态/子模块 import) | 模块**耦合过紧** (main.py import 全 router、routers 互引、god-file) | 迭代恢复 base/registry/data_sources/drift/perf whack-a-mole |
| 删表后 app 启动 schema-init 又空重建 | schema 定义与删除**未联动** | 删除被悄悄撤销 |
| 144 张表里多版本并存 (p0a v3/v4/v5、per_stock+per_formula+stage 多套 optimal) | 违反**最小化数据表** (一个概念拆 N 张、版本不删旧、中间产物落表) | 表爆炸, 60.8M 行冗余 |

**纪律早就有** (PROJECT_CONSTITUTION 第一/二/三条、CLAUDE.md §1.0/§3.5), **但只靠人记没有自动 gate → 漂移**。本框架 = 把纪律**固化进 moth/hook/codegraph 自动执法**。

## 2. 数据层级 (8 层声明式)

每张表在 `backend/config/data_layers.yaml` 声明所属 layer (单一真相源, 替代反复推导):

| layer | 定义 | 保留 | 重建 |
|---|---|---|---|
| **L0_source** | 原始 vendor 镜像 (raw_*), 不可重建真相源 | 永久, 永不删 | sync_runner re-sync |
| **L1_foundation** | 从 L0 直接派生的 PIT 事实+维度 (日历/十大股东/财报PIT/机构/龙虎榜) | 留 | from L0 |
| **L1k_kline_intermediate** | 仅 OHLCV K线派生 (technical_stage/macd), 无多因子 | 留 | from v_price_kline_qfq |
| **display** | 档案展示 (serving L0/L1 给 UI: picture/holders) | 留 | from L1 |
| **infra** | 治理运行时 (watermark/schema/audit/deletion_record/data_health) | 留 | runtime |
| **L2_feature** | 多因子特征工程 (panels/risk_factors/triggers) | wipeable | from L1, 参数寻优重做 |
| **L3_model** | 模型产出 (scores/predictions/champion/ensemble) | wipeable | from L2+params |
| **L4_experiment** | 寻优/消融/探索 (optuna/ablation) | summary_only | rerun; 知识在 retired_experiments.yaml |

**层间规则**: 数据只能从下层 (L0→L1→L2→L3→L4) 单向派生; 上层不可回流污染下层 (PIT/防泄露)。
**删改语义**: "reset" = wipe L2+L3+L4 (一条 layer 查询, 不靠 import 闭包); 地基 (L0/L1/display/infra) 不动。

## 3. 三原则 (新模块/表/规则必守)

1. **最小化模块**: 一个模块一个职责; 禁 god-file (>800 行) / 禁 main.py import 全 router / 禁 routers 互引。新 service 拆到能独立测试。
2. **最小化数据表**: 一个概念一张表; 禁版本并存 (v3/v4/v5 → 用 layer+version 列或即删旧); 中间产物即算即弃不落表 (落表必有消费者 + layer 声明)。
3. **配置驱动**: rules/models/strategies 全走 yaml (sync_registry/optuna_config 是样板); 禁硬编码权重/阈值/策略组合 (反例: ensemble 13 weights 业务直觉写死)。北极星 = 多维策略立方体 (segment×feature×policy 的配置组合)。

## 4. 自动执法 (固化进流程, 防复发)

| 机制 | 守什么 | 挂在哪 |
|---|---|---|
| `data_layer_audit.py --check` | 每张活表必声明 layer (未声明=FAIL, 强制新表声明) | moth 断言 + 人工 |
| moth `data-layer-integrity` | 同上, 自动对账 | `moth assert` / doctor |
| moth `minimal-module-main-routers` | main.py router 数 <= 45 (耦合回潮告警) | `moth assert` / doctor |
| moth `minimal-module-no-new-godfile` | god-file(>800行) 数 ratchet <=23 (禁新增, 存量逐步拆) | `moth assert` / doctor |
| `schema_layer_filter.filter_schema_sql/keep_stmt` | schema-init 只建活层表 (滤除 wiped 层 CREATE/ALTER/引用) — 梳理"删表后**启动**空重建"的 recreation loop | schema_core/marts/migrations 执行点 |
| `check_legacy_flow_integrity.py` (C1/C2/C3) | C1 daily_update 无缺失脚本调用 (删层必删 caller) / C2 无 wiped 表孤儿引用 / C3 append-only 必 retention — **覆盖 schema-init 之外的污染面** (daily_update/散落 service DDL/config 引用) | moth `legacy-flow-no-pollution` |
| codegraph 耦合 gate (规划) | substantial change 前查扇入扇出/跨层 import | preflight (见 §5) |

新增数据/表/模块时: **先在 data_layers.yaml 声明 layer**, 否则 `data_layer_audit` + moth 断言 FAIL。
**recreation loop 已梳理 (用户洞察"先梳理循环和hook")**: schema-init 经 `schema_layer_filter` layer 门控, 删表不再被启动重建; layer 改回活层即自动恢复建表 (声明式)。

## 5. 与现有体系衔接

- `database_manifest.yaml` retention_class (库级) + 本 `data_layers.yaml` (表级 layer) = 互补; manifest 管物理库, layers 管逻辑层。
- `storage_retention.yaml` 删除门槛 + `db_lifecycle_delete.py` (layer-based 删除) + `db_compact.py` (缩盘) = 删除工具链。
- `retired_experiments.yaml` = L4 实验知识 (摘要替代留全表)。
- 待补 (workflow 综合后): codegraph 耦合 gate 接 preflight; 新表 layer 声明 hook; schema-init 与 layer 联动 (删 layer 同步删 schema-def 防重建)。

## 6. reset 老流程污染 — 教训 + 收尾 (2026-06-14 全面 sweep wa3kxgj13)

### 6.1 教训 (为何老流程污染新系统 + DB 为何巨大)
1. **删层必删 caller, 非只删 schema**: 删 144 表只删了 schema, 没删 orchestration 里的调用步骤 (daily_update.sh
   仍调 19 个已删脚本)。删任何层: `grep -r <表名/脚本名>` daily_update + manifest + audit config, 不止 DB schema。
2. **删 schema 留 caller = 静默 degraded 假活**: daily_update 调缺失脚本 → `step_degraded` (非 FATAL) → 用户
   以为管线还活。关键步骤须 FATAL 不 degraded; 可选卫星 sync 才 degraded。
3. **append-only 无 retention = 无界膨胀 (DB 巨大根因之一)**: dim_*_history/*_snapshot 不在 storage_retention
   → 每年 +百万行。storage_retention 必须 100% 覆盖; schema_layer_filter/gate 验。
4. **孤儿 config 引用**: 238 处 config/service/router 引用已删表 (panel_pipeline_manifest/schema_versions/
   field_dictionary/data_audit_rules/routers)。删表无自动化清引用 → stale。post-reset 须 sweep 全 config 清。
5. **散落 service DDL 绕 layer 门**: 部分表在 service import 时 `ensure_tables()` 自建, **绕过 schema_layer_filter**
   (那门只管 schema_core/marts, 不管散落 DDL)。这是 alpha158 类 recreation loop 漏过 schema 门的根。须 layer-gate
   包住所有 ensure_tables()。
6. **daily_update 自当 schema 真相源 ≠ data_layers 真相源**: 应**反转** — data_layers.yaml 驱动, daily_update
   读它自动跳 wiped 层步骤 (声明式, 非脚本里硬编步骤)。

### 6.2 收尾 (按 layer 系统做, 受 §4 gate 守; 不 whack-a-mole)
- **老 daily_update 退役** (用户决: 不复用, 新数据走新流程): 切 Step 2c(alpha158 已切)/2f-2h(sector/capital/
  sniper/institution)/3-pre/3a-3b(signal/p0a panel) + Step4-8(model/ensemble/champion/inference, 19 缺失脚本)。
- 新每日流程 = source/foundation/snapshot + retention gate only (架构师设计 owner=待出重构设计 doc)。
- 清 238 孤儿引用 + 3 表加 retention + 散落 DDL 包 layer-gate。
- kept routers 懒加载已删 services / schema_core 含 deleted-layer CREATE → 同 layer 系统清。
- 验收 = moth `legacy-flow-no-pollution` 转绿 (重构前红=问题实锤)。
