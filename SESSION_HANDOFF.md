# SESSION HANDOFF — 压缩前快照 (2026-06-14)

> 本快照在会话压缩前手动写, 防上下文丢失。已 commit 的全在 git (安全); 本文件存**只在对话里的状态**:
> 待决策 / 路线 / 红 gate 语义 / spawned session。新会话: 先 invoke `chunkymonkey-ops` skill +
> 读 `docs/MASTER_TOPLEVEL_DESIGN.md` + 本文件。HEAD ≈ 3b7f79bf (+ 本次 handoff commit)。

## 本会话干了什么 (15+ commits, b4236b73..HEAD)
地基-reset 后的重生 + 顶层设计会话。全链:
- **S0 实验台** (b4236b73): experiment_store 4留档表 + consumer_alpha family + config驱动执行器 + 10单测。
- **L0 裸K线基准** (b5722a64→fe9a519d): oos_ic 引擎(walk-forward OOS RankIC) + features 提取器 + pit_guard
  行为门 + 3门固化(PIT/embargo/异常红线) + 寻参治理层(DSR/plan_validator/网格) + 对抗审计修(embargo死闸)。
  **标尺 = reversal_short_term OOS RankIC +0.064** (lookback=20, 全市场5204股, 对抗审计清)。
- **设计 (草案)**: tushare数据菜单(cb25fb6b) + 可靠性阶梯(fb7851c0) + 条件化策略立方体(79d95491) + 重构架构(e315aa0f)。
- **清 alpha158** (be519d84): 删 reset 漏网特征层孤儿 + 切 daily_update Step2c 重建循环。
- **工具化教训** (8c31bd3c): check_legacy_flow_integrity gate (3检) + moth `legacy-flow-no-pollution`。
- **顶层设计 + skill** (22dd9a53, 3b7f79bf): `docs/MASTER_TOPLEVEL_DESIGN.md` + `chunkymonkey-ops` skill。
- moth 工具坑修 (lifehack 报): moth 仓 commit 78da9d7 (相对路径 cwd 解析, 不在本项目 git)。

## 红 gate 语义 (别误判成回归)
- `moth legacy-flow-no-pollution` = **LEGACY_PENDING (红)** —— **故意的**: 它是重构验收 gate, 现红=
  老流程污染未清实锤 (daily_update 调19缺失脚本 + 238 wiped表孤儿引用 + 3表缺retention)。重构执行后转绿。
- 其余 moth 断言全绿。

## 待决策 (只在对话里, 必读)
1. **文档退役** (consolidation): 36 个已标"已偏离" analysis + 过时 docs → 移 `analysis/_retired_/`(推荐可恢复)还是删? 待用户定。
2. **CLAUDE.md 进一步瘦身**: 坑库/工具细节已进 skill, 可削到红线+指针 — 待用户审 diff。
3. **重构执行 3 点** (system_refactor_architecture 设计已出, 未执行):
   (a) retention 时长: tdx_industry/attention/forecast 快照 (建议 1yr/6mo/1yr+archive)。
   (b) bloat 核实: fact_stock_technical_stage(398万)+mart_macd_state_history(329万) 是否活L1k被消费(留)否则回收; phase5_predictions 57M 工件删?
   (c) 与 spawned session task_024904c6 (orphan-ref 收口, 独立worktree) 协调防双改冲突。

## 下一步路线 (用户认可顺序)
```
[当前决策点] 文档体系收口 (退役+CLAUDE瘦身, 用户确认后执行)
  → 重构执行 (老daily_update退役+清238引用+3表retention+散落DDL包layer-gate, gate转绿)
  → per-stage L0 IC (reuse oos_ic+technical_stage, 证条件化: reversal在低位是否远超+0.064 → 解锁形态维)
  → Tier-2 backtest引擎 + MC截面置换/PBO恢复 (可靠性阶梯补全)
  → 逐数据alpha验证 (cashflow/block_trade/资金流/筹码, 超双标尺才入)
  → 策略立方体逐维解锁 → 含成本paper_sim → KPI
```

## 关键真相源 (新会话定位)
- 全局: `docs/MASTER_TOPLEVEL_DESIGN.md` · 操作: `chunkymonkey-ops` skill · 当前阶段/KPI: `goal.md`
- L0/标尺: `analysis/l0_bare_kline_baseline_spec_20260614.md` (reversal +0.064)
- 策略立方体: `analysis/conditional_stage_strategy_design_20260614.md` (形态×公式×因子, 用户核心想法)
- 可靠性: `analysis/model_validation_reliability_design_20260614.md` (Gate0-5)
- 数据菜单: `analysis/tushare_alpha_potential_menu_20260614.md` (资金流/筹码 focus, 口径铁律)
- 重构: `analysis/system_refactor_architecture_20260614.md` + `docs/data_management_framework.md` §6
- live gate: `scripts/chunkyctl doctor --fast` (不引文档旧数字)
