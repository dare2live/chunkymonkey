# SESSION HANDOFF — 快照 (2026-06-15)

> 压缩前手动快照, 防上下文丢失。已 commit 全在 git。新会话: invoke `chunkymonkey-ops` skill +
> 读 `docs/MASTER_TOPLEVEL_DESIGN.md` + `analysis/refactor_execution_plan_20260614.md` + 本文件。

## 用户当前指令 (持续推进, 不征求同意)
"按方案最终目标推进直至实现" + daily_update 保持手动 + 数据底座系统方案并执行。

## 本轮干了什么 (9 commits, 91329248..HEAD)
**Phase A 数据底座收口** (owner=`analysis/refactor_execution_plan_20260614.md`):
- A2: 3 表 retention 声明 → **gate C3 PASS**。
- A1: `daily_update.sh` 855→457 行重写为纯数据底座流 (删 Step4-8 model/paper_sim/champion + 19 缺失脚本调用;
  保留 sync/L1k macd + 加 retention/data-health report) → **gate C1 PASS**; DRY 实跑全流程通过。
- A3a/A3d: gate 精度修 (词边界防 substring 假阳性 + 跳二进制) + 删 2 真孤儿 config → C2 238→149。
- A3b: 退役 7 死 serving router (v3_market_perception bundled/recommendation/institution/screening/
  v3_meta/v3_views/v3_perception_legacy) + main.py → C2 149→70; app import 124 routes OK。
- A3c: schema_versions 删 23 wiped 版本条目 + 7 config 18 处 @archived → C2 70→**29**。
- gate 改 ratchet (C1+C3 PASS + C2≤29); **moth 22/22 全绿**。
- A5: 删 phase5 死 model 工件 158M (.duckdb 57M + exports 101M tracked parquet) + manifest 去分区。

## 当前 gate 状态 (诚实)
- C1 daily_update [PASS] / C3 retention [PASS] / C2 wiped-ref **29** (raw gate FAIL, **moth ratchet PASS** C2≤29)。
- C2 余 29 = **updater 死 builder** (build_profiles/trends/industry_stat/screening/sector_momentum 建已删 L2/L3 表)
  嵌在 live update-DAG (RUNNERS/STEPS/HARD_DEPS, data_sources/etf 依赖) = 显式 **P2 增量解耦**
  (framework §6 禁 big-bang; 精确移除步骤见 plan doc)。

## 待用户决策 (高后果不可逆, 我没自作主张)
1. **data/archive/ 3.4G** (lifecycle_20260614 reset 删前回滚网 2.5G + dead_tables_20260612 985M): 非 git 删不可逆,
   重建尚未 KPI 验证 → 保留待重建达标后删。要现在删省盘? 用户定。
2. **updater 死 builder P2 解耦** (C2 29→0): 须跑 test 验 data_sources/etf 不破, 是 framework 钦定增量项。

## 剩余 Phase A polish (低风险, 继续)
- A4: 8 散落 ensure_tables() 包 layer-gate (assert_active_layer) — **预防性**硬化 (防未来重建循环, 当前无活 bug;
  站点: institution_survey/turtle/picture/qfii/stage/signals_v2, 建的都是 active 表)。
- A6: 文档收口 — 候选多被控制面引用 (README authority 链 + strategy_validation_contract) 或已诚实标"已偏离(留证据)";
  仅 multi_wave_strategy_300616/system_architecture_audit_20260521 零引用安全删; CLAUDE.md 瘦身 (§4.5反例→skill指针) 待。

## 下一步路线 (owner=MASTER §10)
```
Phase A 收尾 (A4/A6) → **Phase B 证 base-edge** (per-stage L0 IC: reversal 在低位是否远超 +0.064 + Alpha158 重算)
  → Phase C 可靠性阶梯+Tier-2 backtest引擎 → Phase D 逐数据 alpha 验证 → Phase E 立方体 → Phase F KPI
```
**战略**: cube 实例化已 BLOCK (板块维实测失效); 数据菜单无 high-edge → 瓶颈在 base-edge,
Phase B (per-stage L0 IC) 是最便宜的"edge 存不存在"探针, 决定 C-F 是否值得做。

## 关键真相源
- 全局 `docs/MASTER_TOPLEVEL_DESIGN.md` · 执行清单 `analysis/refactor_execution_plan_20260614.md` ·
  操作 `chunkymonkey-ops` skill · KPI `goal.md` · L0 标尺 reversal +0.064 `analysis/l0_bare_kline_baseline_spec_20260614.md`
- live gate: `scripts/chunkyctl doctor --fast` + `moth assert --repo .` (22/22)
