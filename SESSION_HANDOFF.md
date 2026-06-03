# SESSION HANDOFF — Manual resume snapshot

> 此文件由 `scripts/session_snapshot.sh` / `scripts/cm_resume.sh` 按需手动刷新.
> Codex app/CLI 不再通过 cron 或 SessionStart hook 自动注入本 handoff，避免 stale state 被静默加载.
> 新会话应先按 `docs/chunkyctl_session_quickstart.md` 做启动检查，再把本文件当 context-only 状态快照.
> 业务 pipeline 进度另见 `analysis/workflow_checkpoint.md` (pull/audit/paper_sim/KPI/gate/decision).

## 中断恢复用法 (用户必读)

### 1. Mac 重启 / terminal 崩 后:
```
cd /Users/dp/Documents/M/stock/chunkymonkey
bash scripts/cm_resume.sh          # 1 命令出当前 state + prompt 模板
```

### 2. 新 Codex 会话输入哪句话:
- **推荐**: `请按照 docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查，再看 SESSION_HANDOFF.md 的 next_action。`
- **简短恢复**: `继续，看 SESSION_HANDOFF.md 和 analysis/workflow_checkpoint.md，按 next_action 推进。`
- **复杂 pipeline**: `从 analysis/workflow_checkpoint.md 推断当前 pipeline step，按 next_recovery_command 继续。`

### 3. 自动注入状态:
```
bash scripts/install_resilience.sh --status
```
默认不再安装 cron snapshot / SessionStart auto-inject；如需恢复旧自动化，必须显式设置脚本里的 legacy opt-in。

**Snapshot 时间**: 2026-06-03 14:16:15 CST

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
| HEAD | `4d5b4343 refactor: flatten app etf sync render loops` |
| 最近 24h commits | 263 |
| 未 commit 文件 | 7 |

### 最近 10 commits

```
4d5b4343 refactor: flatten app etf sync render loops
3e894e9a refactor: flatten settings view render loops
154f5407 docs: refresh handoff snapshot after commit
5fd6d5c9 docs: record need_027 fallback snapshot audit evidence
5831b37d docs: refresh handoff after rank snapshot fallback probe
08f25e1b docs: record rank snapshot fallback probe
d2280cc2 docs: refresh session handoff snapshot after live boundary tighten
d83f7d5f feat: narrow stage-opt live supply
8dcfcdac docs: refresh session handoff after data-health pass | # commit-msg: minimal
277d589a refactor: data-view hot path second pass
```

## NEXT ACTION (auto-computed)

**7 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

## Resilience 配置 (verified)

| 机制 | 状态 |
|---|---|
| F1 Optuna SQLite storage | deployed (`sqlite:///data/reports/optuna/$MODEL_ID.db` resume on preempt) |
| F2 per-trial checkpoint | deployed (`data/reports/optuna/$MODEL_ID.best.json` atomic write) |
| nohup + setsid + disown | retrain detached, SSH 断不影响 |
| monitor MAX_DURATION_HOURS=24 | Mac sleep proof |
| manual session_snapshot.sh | active; run via `bash scripts/cm_resume.sh` |
| cron session_snapshot.sh | disabled by default for Codex app/CLI |
| SessionStart handoff auto-inject | disabled by default for Codex app/CLI |
| Stop hook session_rule_audit | 防 multi-agent / continuous-mode 违规 |

## 一旦中断如何无缝衔接

1. **Mac 重启 / terminal 崩 后**: 启动 terminal → `cd /Users/dp/Documents/M/stock/chunkymonkey`
2. 运行 `bash scripts/cm_resume.sh` 刷新本 handoff 和 snapshot
3. 新 Codex 会话输入: `请按照 docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查，再看 SESSION_HANDOFF.md 的 next_action。`
4. Codex 先跑 live checks，再按 NEXT ACTION 执行本地工作 (audit / compare / commit / etc)

GCP controlled-use (2026-05-21 用户澄清):
- 可用于大计算、寻优、长 replay、主项目与 BestChoice 综合寻优。
- 启动前说明 scope、wall time/成本、输入快照、输出路径、artifact 保存与 stop/rollback。
- 脚本层仍要求 `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`, 防误触。
