# Account-switch handoff — 2026-07-20

> 生命周期：历史证据（evidence-only）。本文记录 2026-07-20 跨 Cursor 账号切换时的会话事实与运维坑，**不拥有**当前 objective/下一步（以 `goal.md` + `BOARD.md` 为准）；不拥有架构规则（以 `docs/README.md` owners 为准）。
> 续作 agent：先 `git pull` + `scripts/chunkyctl agent-boot`，再读 `goal.md`。

## 一句话（当时事实）

A→H **未暂停**；C/B-pit **生产读 cutover 已 ON**（`b38e9ac5`）；Agent-OS/Delivery-OS 已落地。续作下一刀以 **当时 `goal.md`** 为准（enrich accept / 20260720 Tier1-2 accept / F），不是重新开闸。

## 必读路径

| 用途 | 路径 |
|---|---|
| 当前 objective / 下一步 | `goal.md` |
| 生成状态板 | `BOARD.md` + `scripts/chunkyctl agent-boot` |
| 计划（A→H） | `~/.cursor/plans/gap_analysis_audit_3cdd0f6e.plan.md`（不在 repo）+ `goal.md` |
| Agent-OS | `docs/engineering_governance.md` §13（WP6）§15（编排/墙钟） |
| Cutover 证据 | `data/lineage/c_b_pit_cutover_readiness.json` |
| 退役笔记 | `data/lineage/legacy_retire_notes.md` |
| E 结论 | `data/lineage/phase_e_experiment_verdicts/`（全 reject/claimable=false） |
| Ledger | `analysis/project_state_ledger.md`（按日期 rg） |

## 已 FIXED（勿重做）

| 项 | 证据 |
|---|---|
| C+B-pit cutover ON | `b38e9ac5`；yaml `cutover_allowed: true`；resolver ACCEPTED_CUTOVER / MART_CUTOVER |
| form/qfq/pulse → 20260720 | `64c6b4b7` |
| Phase D runtime | persist + fold + measured offline（`d4cc33d0` 等） |
| Agent-OS WP0–WP4 | tiered safe_commit、BOARD 生成、`agent-boot`、AGENTS 瘦身 |
| Delivery-OS | CI L1 paths-ignore；eng_gov §15；T0 门耗时已测（慢在编排非门） |
| manual sync 非 18:00 | `trigger_mode=manual`；日历仍硬约束 |

## 未完成（切换时快照 — 现行菜单以 goal.md 为准）

切换时已识别、尚未全部闭合的项（可能已被后续 commit 推进，须 live 重查）：

1. Dual-track 退役残余 / pulse drill 单轨
2. Enrich accepted stock_states（去 scaffold）
3. Tier1/2 accept for `20260720`+
4. A→H F 主升浪 B0–B2
5. WP6 仪式 flip（owner-gated）
6. 非阻断：`index_dailybasic` min_rows；margin frozen；`test_sync_runner_integrity` trigger_mode fake drift

## 禁令（不变）

Optuna / E 松门 / StrategyRelease / margin thaw / mass backfill / 静默 cutover / plugin bus / 第二 DB / `--no-verify`

## 运维坑：后台 subagent「2 行卡死」

2026-07-20 晚多次：Fable5 / Opus / Sonnet / 部分 Shell **transcript 仅 2 行**，发出 `UpdateCurrentStep`/Shell 后 **无 tool_result**。  
**对策**：优先在**父会话直接做**；窄提交可用 `subagent_type=shell`；勿空转重派同模型空等。Multitask 激进串行会放大该故障。

## 建议启动序列（新账号）

```bash
cd <repo>
git pull
scripts/chunkyctl agent-boot
# 读 goal.md + analysis/account_switch_handoff_20260720.md + BOARD.md
# 再按 goal「下一步」开刀；L3 走 safe_commit；§15 不要每刀 sync gh watch
```

## Suggested skills

- `$mio` + `$architect-controller`（实质判断）
- `$chunkymonkey-governance` / `$chunkymonkey-review-gate`（PIT/删除/commit）
- `$post-fix-audit`（数据/PIT 后）
- 勿默认 Fable5（曾额度超限 + 本会话卡死）；能用本会话或 Opus/Sonnet/shell

## 续作验收

- `resolve_tier12_production_read('20260717')` → ACCEPTED_CUTOVER  
- `resolve_b_pit_mart_production_read('20260717')` → MART_CUTOVER  
- `20260720` 仍可缺 accept → LEGACY（预期）  
- 不把 continuity BLOCKED 当成代码失败
