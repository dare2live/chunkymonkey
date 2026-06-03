# SESSION HANDOFF — Auto-updated by cron

> 此文件由 `scripts/session_snapshot.sh` 每 5 min cron 自动更新.
> Claude session start 时 read 此文件即可无缝衔接, 不需要用户 paste context.
> Mac 重启 / terminal 崩 后, 启动 Claude → 自动 read → 立即知道当前状态 + next action.
> 业务 pipeline 进度另见 `analysis/workflow_checkpoint.md` (pull/audit/paper_sim/KPI/gate/decision).

## 中断恢复用法 (用户必读)

### 1. Mac 重启 / terminal 崩 后:
```
cd /Users/dp/Documents/M/stock/chunkymonkey
bash scripts/cm_resume.sh          # 1 命令出当前 state + prompt 模板
claude                              # SessionStart hook 自动 inject 本 handoff
```

### 2. 用户输入哪句话给 Claude:
- **方案 A** (SessionStart hook 配好, 推荐): 不用输入, hook 自动 inject 本 handoff, Claude 看到立即继续 next_action
- **方案 B** (hook fail / 想显式 trigger): 输入 `继续, 看 SESSION_HANDOFF.md 按 next_action 推进`
- **方案 C** (复杂多步流程): 输入 `从 analysis/workflow_checkpoint.md 推断当前 pipeline step, 按 next_recovery_command 继续`

### 3. 一次性 install 全部 resilience:
```
bash scripts/install_resilience.sh   # SessionStart hook + cron + launchd 全装
bash scripts/install_resilience.sh --status   # check 装好没
```

**Snapshot 时间**: 2026-06-03 11:20:10 CST

## 主线状态

| 项 | 值 |
|---|---|
| Model ID | `lgbm_phase5_v9b_20260523T083000Z` |
| VM 状态 | ? |
| VM 上次启动 |  |
| VM 上次停止 |  |
| F2 checkpoint best_value | 0.3094819825339931 |
| F2 checkpoint best_trial | 32 |
| F2 updated_at | 2026-05-23T12:24:52+00:00 |
| F2 path | `data/reports/optuna/lgbm_phase5_v9b_20260523T083000Z.best.json` |

## 后台 process

| 项 | 状态 |
|---|---|
| Local monitor | dead |
| Codex companion threads | 0 running |

0

## GCP 成本

| 项 | 值 |
|---|---|
| 月预算用 | 25.2% |
| 剩余 spot 小时 | 105.9 h |

## Git 状态

| 项 | 值 |
|---|---|
| Branch | main |
| HEAD | `f8d00e10 docs: record data-health blocker triage complete | # commit-msg: minimal` |
| 最近 24h commits | 274 |
| 未 commit 文件 | 4 |

### 最近 10 commits

```
f8d00e10 docs: record data-health blocker triage complete | # commit-msg: minimal
8cd787f5 docs: add priority ladder for blocker triage | # commit-msg: minimal
88433830 docs: refresh handoff after data-health reframe | # commit-msg: minimal
5d1041ff docs: reframe next action to data-health blockers | # commit-msg: minimal
0ab29a23 docs: refresh session handoff snapshot after stock-view index consolidation | # commit-msg: minimal
2d932cc0 refactor: stock-view index consolidation | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, audit PASS, pytest 3 passed, targeted complexity PASS, codegraph sync PASS
3911b457 docs: refresh session handoff snapshot after data-view route search cleanup | # commit-msg: minimal
eeff4015 refactor: data-view route search cleanup | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, audit PASS, pytest 6 passed, targeted complexity PASS, codegraph sync PASS | post-fix-audit cleanup verified 无残留
5bcea950 docs: refresh session handoff snapshot after signal-adapter grouping cleanup | # commit-msg: minimal
e834f18c refactor: signal-adapter grouping cleanup | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, audit PASS, pytest 4 passed, targeted complexity PASS, codegraph sync PASS | post-fix-audit cleanup verified 无残留
```

## NEXT ACTION (auto-computed)

**continue current goal blockers — stage-opt structural blocker triage / need_027 blocked-gap triage**

## 追加记录

- `bc_absorbed` 的 5 个 challenger formulas 已经通过 live `REGISTRY` 进入 `build_formula_signals_history`，并已回填 `704,661` 条信号和 `35` 行 horizon evidence；stage-opt audit 现在显示 `live_formula_count=12`，但当前 blocker 仍然是 `below_min_signals`，weakest formulas 已切到 `ma_base_breakout` / `gs_pullback_confirm` / `volume_base_breakout`。
- 你给的高吞吐量化蓝图已记录，后续推进顺序按 `data pipeline / truth-source` -> `CatBoost + Bayesian/Optuna + VectorBT` -> `GCP Spot / Cloud Run` 走；`bestchoice` 公式事项先排队，不和当前 blocker 线程混在一起。
- `build_lhb_events.py` 和 `run_daily_topk.py` 已把最后两条 warning-only writer 补完，最新 `scripts/chunkyctl doctor --fast` 结果是 `data_health PASS` (`green=342 / yellow=0 / red=0`)；overall `WARN` 现在只来自 `need_coverage` 的 `need_027` blocked、`stage-opt` 的 `below_min_signals`，以及当前 dirty worktree / complexity 历史，不再是 data-health blocker。
- `assets/js/data-view.js` 这一轮继续把渲染热点收口到直线型 `for...of` / 拼接路径，`renderHealthHeatmap()`、`renderSourcePriority()`、`renderFallbackPanel()`、`renderDriftQueue()`、`renderCapTable()`、`renderStepGrid()` 和 `startPolling()` 的日志聚合都已去掉 `.map().join()` / `forEach()` 热回调；targeted complexity scan 这一个文件已经不再报明显热点，但全仓 broad scan 仍保留历史 HIGH 残余，后续继续按热路径推进。
- `assets/js/data-view.js` 的第二轮收口又把 `buildRouteSearchText()`、`buildSourceCardsModel()`、`buildHealthHeatmapModel()`、`buildSourcePriorityModel()`、`buildLinkOverviewModel()`、`renderLinkOverview()`、`renderSourceCards()`、`renderAuditResults()`、`renderRoutesTable()` 和 `_setUpdateButtonsBusy()` 的残余 `.map()` / `.forEach()` 也收掉了；`data-view` 的 targeted complexity scan 继续是 clean，但 broad scan 仍有其他历史 HIGH，暂时不把这次当成全仓复杂度清零。

## Resilience 配置 (verified)

| 机制 | 状态 |
|---|---|
| F1 Optuna SQLite storage | deployed (`sqlite:///data/reports/optuna/$MODEL_ID.db` resume on preempt) |
| F2 per-trial checkpoint | deployed (`data/reports/optuna/$MODEL_ID.best.json` atomic write) |
| nohup + setsid + disown | retrain detached, SSH 断不影响 |
| monitor MAX_DURATION_HOURS=24 | Mac sleep proof |
| cron session_snapshot.sh | 5min auto update, 不依赖 Claude session 活 |
| SessionStart hook (~/.claude/settings.json) | 启动时 auto-read SESSION_HANDOFF.md |
| Stop hook session_rule_audit | 防 multi-agent / continuous-mode 违规 |

## 一旦中断如何无缝衔接

1. **Mac 重启 / terminal 崩 后**: 启动 terminal → `cd /Users/dp/Documents/M/stock/chunkymonkey` → 启动 `claude`
2. Claude SessionStart hook 自动 cat `SESSION_HANDOFF.md` 注入 context
3. Claude 看到: 当前主线状态 / local artifacts / next action
4. Claude 按 NEXT ACTION 执行本地工作 (audit / compare / commit / etc)
5. 用户 0 需要 paste 长 summary

GCP controlled-use (2026-05-21 用户澄清):
- 可用于大计算、寻优、长 replay、主项目与 BestChoice 综合寻优。
- 启动前说明 scope、wall time/成本、输入快照、输出路径、artifact 保存与 stop/rollback。
- 脚本层仍要求 `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`, 防误触。
