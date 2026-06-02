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

**Snapshot 时间**: 2026-06-03 02:55:59 CST

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
| HEAD | `2aeb136f refactor: extract recommendations model from workbench view | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, pytest 12 passed, audit PASS, complexity no new HIGH, codegraph sync PASS | # commit-msg: minimal` |
| 最近 24h commits | 197 |
| 未 commit 文件 | 0 |

### 最近 10 commits

```
2aeb136f refactor: extract recommendations model from workbench view | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, pytest 12 passed, audit PASS, complexity no new HIGH, codegraph sync PASS | # commit-msg: minimal
3c147c76 docs: refresh session handoff snapshot after overview model extraction | # commit-msg: minimal
a2a3e928 refactor: extract overview model from workbench view | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, pytest 11 passed, audit PASS, complexity no new HIGH, codegraph sync PASS | # commit-msg: minimal
1fffa62a docs: refresh session handoff snapshot after rank matrix cache model extraction | # commit-msg: minimal
a0c4ebcb refactor: extract rank matrix cache model from workbench view | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, pytest 10 passed, audit PASS, complexity no new HIGH, codegraph sync PASS | # commit-msg: minimal
6f0ee1d1 docs: refresh session handoff snapshot after temporal synergy model extraction | # commit-msg: minimal
f9a39da3 refactor: extract temporal synergy model from workbench view | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, pytest 9 passed, audit PASS, complexity no new HIGH, codegraph sync PASS | # commit-msg: minimal
f9a25895 docs: refresh session handoff snapshot after features model extraction | # commit-msg: minimal
86a243dd refactor: extract features model from workbench view | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, pytest 8 passed, audit PASS, complexity no new HIGH, codegraph sync PASS | # commit-msg: minimal
0271160d docs: refresh session handoff snapshot after data sources model extraction | # commit-msg: minimal
```

## NEXT ACTION (auto-computed)

**continue current goal blockers — stage-opt structural blocker triage / need_027 blocked-gap triage**

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
