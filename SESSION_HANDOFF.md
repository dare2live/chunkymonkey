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

**Snapshot 时间**: 2026-06-02 08:44:50 CST

- latest code/docs snapshot (2026-06-02) is commit `525fab9a`; `reversal_1w` only loosened `rel_std_max` from `0.06` to `0.07`, lifting the history rebuild to `reversal_1w: 280,065 / 13,639 / 75.54%`, and the full stage-opt audit to `raw_signal_rows=6,421,901 / filtered_signal_rows=3,177,918 / unique_keys=159,805 / ready_keys=120,242 / ready coverage=75.24% / below_min_signals=39,563`, with `min_signals=4/3/2` at `80.19% / 85.55% / 91.74%` and weakest formulas now `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_deep`; controller recommendation remains `P1 / upstream_candidate_supply`, and `need_027` still sits in blocked-gap triage with `aif10 exact individual_fund_flow unavailable`. Live recommendation PIT attrition is still 5/1/3 for short/mid/long, all `cross_stage_fallback`, so exact PIT coverage remains structurally sparse.
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
| HEAD | `525fab9a feat: widen reversal 1w rel_std to 0.07 and lift stage-opt supply | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 40 passed, audit PASS, stage-opt 75.24% ready coverage, docs graph PASS, complexity no new HIGH` |
| 最近 24h commits | 114 |
| 未 commit 文件 | 0 |

### 最近 10 commits

```
525fab9a feat: widen reversal 1w rel_std to 0.07 and lift stage-opt supply | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 40 passed, audit PASS, stage-opt 75.24% ready coverage, docs graph PASS, complexity no new HIGH
a394af81 feat: widen reversal deep rel_std and lift stage-opt supply | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 40 passed, audit PASS, stage-opt 74.56% ready coverage, docs graph PASS
d041862e feat: lower turtle breakout 55 volume gate to 0.5 and lift stage-opt supply | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 40 passed, audit PASS, stage-opt 74.16% ready coverage, docs graph PASS
2738fe56 feat: lower turtle breakout 55 volume gate to 0.6 and lift stage-opt supply | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 40 passed, audit PASS, stage-opt 74.15% ready coverage, docs graph PASS
199e0932 feat: widen reversal mild threshold and lift stage-opt supply | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 40 passed, audit PASS, stage-opt 74.12% ready coverage, docs graph PASS
e4d8dae3 feat: widen reversal deep threshold and lift stage-opt supply | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 40 passed, audit PASS, stage-opt 74.00% ready coverage, docs graph PASS
55e607ca feat: widen reversal mild and turtle breakout gates | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 40 passed, audit PASS, stage-opt 73.61% ready coverage, docs graph PASS
b560909b feat: lower turtle breakout 55 volume gate to 0.9 and lift stage-opt supply | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 39 passed, audit PASS, stage-opt 72.65% ready coverage, docs graph PASS, complexity no new HIGH
cec08c8e feat: widen reversal_1w to 1-10% and lift stage-opt supply | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 66 passed, audit PASS, stage-opt 73.28% ready coverage, docs graph PASS, complexity no new HIGH
da5c60d9 feat: externalize reversal_1m_mild threshold and lift stage-opt supply | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 39 passed, audit PASS, stage-opt 71.96% ready coverage, docs graph PASS
30b4e0ce docs: refresh session handoff after reversal short-term lift | test pass: docs graph PASS, worktree clean, no code changes
286969b3 feat: externalize reversal short-term thresholds and sync stage-opt docs | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 39 passed, audit PASS, stage-opt 71.59% ready coverage, docs graph PASS
969ad89b docs: refresh controller snapshot after stage-opt latest rebuild | Codex-Reviewed: APPROVE_WITH_NOTES | commit-msg: minimal | test pass: docs graph PASS, worktree clean
ed5a3ee6 feat: widen stage-opt evidence and sync controller docs | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: formula_engine 33 passed, audit PASS, docs PASS, codegraph synced
2c177d1b feat: widen MACD state history evidence and sync controller docs | Codex-Reviewed: APPROVE_WITH_NOTES | test pass; audit PASS; complexity no new HIGH; docs synced
2cb49141 docs: surface stage-opt blocked-reason counts in doctor | Codex-Reviewed: APPROVE_WITH_NOTES (chunkymonkey-review-gate)
b1bf7181 docs: sync quickstart with stage-opt and need_027 controller-visible blockers
7c99b0af docs: sync stage-opt current audit stats and controller-state timestamps | audit docs graph PASS
54974968 docs: surface min_signals=2 in stage-opt doctor output | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: stage-opt audit and chunkyctl tests passed
cb2ca58c docs: sync min_signals=2 stage-opt evidence and controller state | test pass: docs graph PASS, doctor PASS, worktree PASS
c61e4d86 docs: sync source-watermark UTC cleanup and current controller state | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: docs PASS, doctor PASS, codegraph synced | post-fix-audit cleanup verified 无残留 no stale
efc6cd5a fix: silence source_watermarks utcnow warnings and sync project index | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: source_watermarks and probe tests 15 passed, audit PASS, docs PASS, doctor PASS, post-fix-audit cleanup verified 无残留 no stale
ddea9ec9 docs: sync probe persistence downgrade and current triage state | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: probe_source_capability 9 passed, audit PASS, docs PASS, doctor PASS
```

## NEXT ACTION (auto-computed)

**0 uncommitted files — continue stage-opt upstream_candidate_supply / need_027 blocked-gap triage**

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
3. Claude 看到: 当前 retrain model_id / local artifacts / next action
4. Claude 按 NEXT ACTION 执行本地工作 (audit / compare / commit / etc)
5. 用户 0 需要 paste 长 summary

GCP controlled-use (2026-05-21 用户澄清):
- 可用于大计算、寻优、长 replay、主项目与 BestChoice 综合寻优。
- 启动前说明 scope、wall time/成本、输入快照、输出路径、artifact 保存与 stop/rollback。
- 脚本层仍要求 `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`, 防误触。
