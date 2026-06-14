# 系统重构架构设计 — 架构/流程/数据 (architect-controller, 2026-06-14, 草案/待审)

> 用户'用架构师skill好好设计架构和流程以及数据的重构; 重构前先把经验教训工具化, 本次重构要把好关'。
> 工具化已完成 (check_legacy_flow_integrity.py + moth `legacy-flow-no-pollution`, 3检现红=问题实锤)。
> 本 doc = 重构设计 (受 gate 守); owner=本文件 + 上位 docs/data_management_framework.md。实现前用户审。

## Objective / 定义权
**目标**: 清除 reset 老流程污染 (19缺失脚本调用/238孤儿引用/3表无retention/散落DDL绕门), 重构出
data_layers 驱动的干净架构+流程+数据, 且重构每步受 moth gate 守 (红→绿 即验收)。
**定义权**: 用户立法 (老daily_update退役/新数据新流程/gate先于重构/把好关)。我执行 architect 协议。

## 创世层 (本次重构, 不可变)
- **为何存在**: reset 删了层但没删流程/引用, 老流程会污染并膨胀新系统 (DB 9.1G 根因之一)。重构让
  **声明 (data_layers) 成为唯一驱动**, 流程读声明而非硬编已删步骤 — 删层即自动不被流程触及。
- **死亡线 (≤3)**: ① 重构后任一 gate (legacy-flow-no-pollution/data-layer-integrity) 仍红 = 重构未完不算数;
  ② 重构误删存活地基/源数据 (L0/L1/L1k/display/infra) = 灾难回滚; ③ 重构引入新硬编步骤 (不读 data_layers)
  = 重蹈 daily_update 自当真相源覆辙。

## 判断法典 (gate = 机器话)
| 人话 | 机器话 |
|---|---|
| 流程不调已删脚本 | gate C1 (daily_update 脚本必在盘) |
| 配置不引用已删表 | gate C2 (无 wiped 表孤儿引用) |
| 累积表必有 retention | gate C3 (append-only 必 storage_retention) |
| 建表不绕 layer 门 | 散落 ensure_tables() 包 layer-gate (新) + schema_layer_filter |
| 声明驱动流程 | daily_update 读 data_layers 跳 wiped 层 (反转, 新) |

## Controller Loop (5问)
| 检 | 答 |
|---|---|
| Substrate (真相源) | `data_layers.yaml` 表→layer (85活/56wiped); K线/日历=数据真相源。流程/config **读它**, 不自带表清单。 |
| Boundary (边界/契约) | 4 流程域: **daily-update**(L0/L1/L1k/snapshot+retention) · **alpha-validation**(L2-L4 via experiment_store, S0-S4) · **serving**(display from L1) · **infra**(watermark/audit/gate)。每域只碰自己 layer。 |
| Meta-spec | 新 daily-update: input=active-layer 表清单(读 data_layers) → 每表跑其 rebuild(data_layers.rebuild 字段) → 跳 wiped。output=只 active 表新鲜。failure=缺脚本 FATAL(非 degraded)。 |
| Falsification | moth `legacy-flow-no-pollution` 绿 + `data-layer-integrity` 绿 + daily_update dry-run 不触 wiped 层 + DB 无 undeclared 表。 |
| Attention (重点) | 载重决策: ① 反转(声明驱动) ② 散落DDL layer-gate ③ 误删防护(只动 wiped/孤儿, 不动 5 活层)。细节(逐条清引用)可委托/批量。 |

## 三重设计

### A. 架构 (8层不变, 但**驱动反转** + 补 layer-gate)
```
真相源: data_layers.yaml (表→layer) ── 唯一驱动
   ↓ 读
[L0 source] tushare_raw + market K线  ←sync_runner(registry)
[L1 foundation] dim/财报PIT/holders/LHB ←from L0
[L1k kline_int] technical_stage/macd   ←from v_price_kline_qfq
[display] UI serving ←from L1
[infra] watermark/audit/gate/deletion
[L2 feature/L3 model/L4 experiment] ←**alpha-validation 程序 (S0-S4, experiment_store), 不进 daily_update**
```
**反转 (lesson#6)**: 流程不再硬编步骤; 读 data_layers 的 active 表 + 各表 rebuild 字段, 自动跳 wiped。
**散落DDL门 (lesson#5)**: 所有 `ensure_tables()` 包 `assert_active_layer(table)` — 拒建 wiped 层表 (补
schema_layer_filter 只管 schema_core/marts 的盲区, 这是 alpha158 类循环漏过的根)。

### B. 流程 (老 daily_update 退役, 新流程声明驱动)
| 老 daily_update (退役) | 新 daily-update |
|---|---|
| Step2c alpha158(已切)/2f-2h sector/sniper/institution/3-pre signal/3a-3b p0a/4-8 model | **删** (L2+ 走 alpha-validation 非daily) |
| 硬编每步 build_*.py | **读 data_layers active + rebuild 字段**, 缺脚本 FATAL |
| 卫星 snapshot 无 retention | snapshot 步 + **retention gate** (C3) |
保留: 源同步(sync_runner drain) + L1/L1k rebuild + watermark + data_audit。

### C. 数据重构 (gate 红→绿, 受守; 顺序)
1. **退役老 daily_update**: 删 Step2f-h/3/4-8 (19缺失脚本调用) → 新声明驱动流程。→ gate C1 绿。
2. **清 238 孤儿引用**: 按 config 文件批 (panel_pipeline_manifest pipelines段/schema_versions/field_dictionary/
   data_audit_rules/model_search/routers) — 删或标 @archived。→ gate C2 绿。
3. **3 表加 retention**: dim_stock_tdx_industry_history/raw_profit_forecast_snapshot_daily/raw_tdx_industry_file_snapshot
   入 storage_retention (rolling keep_N + archive)。→ gate C3 绿。
4. **散落 ensure_tables() 包 layer-gate**: 8 散落 service DDL 加 assert_active_layer。
5. **bloat 核 + 回收**: 核 fact_stock_technical_stage(398万)/mart_macd_state_history(329万) 是否活L1k被消费
   (是=留, 否=回收) + phase5_predictions 57M 工件删。

## Delegation (可委托并行, controller 串行收编 + commit)
- 读 only: 逐 config 文件的 238 孤儿引用定位 (按文件分组, 6 agent) → controller 审后清。
- 写 owned (controller): daily_update 重构 / layer-gate / retention / commit (写窗口串行)。
- 已有 spawned session (task_024904c6) 在做 orphan-ref → **协调**: 它产出当输入, controller 收编避免双改冲突。

## Smallest reversible next step
**先做 #1 退役老 daily_update** (最大污染源 + gate C1 立刻可绿 + 可逆=git): 把 daily_update 缩到声明驱动的
source/foundation/snapshot+retention, 删 19 缺失脚本调用步骤。一步过 gate C1, 验证后再 #2/#3。

## Verdict: PROCEED (gate 已立, 设计受守; 待用户审 A/B/C 后执行)
风险: 误删活层 (死亡线②) → 每步 dry-run + 只动 wiped/孤儿; 散落DDL门改动面 (8处) → 增量 + moth 守。
