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

**Snapshot 时间**: 2026-05-21 16:23:13 CST

## 主线 retrain 状态

| 项 | 值 |
|---|---|
| Model ID | `lgbm_phase5_gcp_20260520T010718` |
| VM 状态 | ? |
| VM 上次启动 |  |
| VM 上次停止 |  |
| F2 checkpoint best_value | 0.42370918210702596 |
| F2 checkpoint best_trial | 36 |
| F2 updated_at | 2026-05-20T08:57:45+00:00 |
| F2 path | `data/reports/optuna/lgbm_phase5_gcp_20260520T010718.best.json` |

## 后台 process

| 项 | 状态 |
|---|---|
| Local monitor | dead |
| Codex companion threads | 0 running |

0

## GCP 成本

| 项 | 值 |
|---|---|
| 月预算用 | 74.9% |
| 剩余 spot 小时 | 13.7 h |

## Git 状态

| 项 | 值 |
|---|---|
| Branch | main |
| HEAD | `6005aadb Resolve HS300 source gap # PIT-strict` |
| 最近 24h commits | 2 |
| 未 commit 文件 | 272 |

### 最近 10 commits

```
6005aadb Resolve HS300 source gap # PIT-strict
d2b88143 Validate market perception browser UI # PIT-strict
12553fc5 Refresh market cap decile input # PIT-strict
6360918d Add stock context aggregation MVP # PIT-strict
ee2e7b32 Add style rotation crowding MVP # PIT-strict
f321c8a1 Add leader follower market diffusion MVP # PIT-strict
895a52c2 Add under reaction dashboard # PIT-strict
7d884042 Add under reaction engine MVP # PIT-strict
afc724ec Add theme lifecycle dashboard # PIT-strict
b7c7f3b1 Add theme lifecycle engine MVP # PIT-strict
```

## NEXT ACTION (auto-computed)

**272 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
