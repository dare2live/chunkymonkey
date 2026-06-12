# SESSION HANDOFF — Manual resume snapshot

> 此文件由 `scripts/session_snapshot.sh` / `scripts/cm_resume.sh` 按需手动刷新.
> Codex app/CLI 不再通过 cron 或 SessionStart hook 自动注入本 handoff，避免 stale state 被静默加载.
> 新会话应先按 `docs/chunkyctl_session_quickstart.md` 做启动检查，再把本文件当 context-only 状态快照.
> 当前计划看薄入口 `goal.md`; 已完成证据查 `analysis/project_state_ledger.md`.
> `analysis/workflow_checkpoint.md` 只在其声明 active pipeline 时参与恢复。

## 中断恢复用法

恢复流程唯一 owner = `docs/chunkyctl_session_quickstart.md` (2026-06-11 文档治理收口)。
速记: `bash scripts/cm_resume.sh` 刷新本快照, 新会话按 quickstart 启动检查。
定时任务告警: 启动时检查 `/tmp/chunkymonkey_ALERT_*.flag`, 存在 = 有 job 失败未处理。

**Snapshot 时间**: 2026-06-12 14:24:06 CST

## 主线状态

| 项 | 值 |
|---|---|
| Model ID | `lgbm_phase5_v9b_20260523T083000Z` |
| F2 checkpoint best_value | 0.3094819825339931 |
| F2 checkpoint best_trial | 32 |
| F2 updated_at | 2026-05-23T12:24:52+00:00 |
| F2 path | `data/reports/optuna/lgbm_phase5_v9b_20260523T083000Z.best.json` |

## 后台 process

| 项 | 状态 |
|---|---|
| Codex companion threads | 0 running |

0

## Compute backend

| 项 | 值 |
|---|---|
| Backends | local:active, modal:active |
| Job plan | `scripts/chunkyctl jobs --family model_training --model-id <id> --input-snapshot <snapshot> --objective <why> --rollback-plan <plan> --gate-evidence <gate>=<artifact>` |

## Git 状态

| 项 | 值 |
|---|---|
| Branch | main |
| HEAD | `389ff6de chore: cm_resume 顺带刷新 workflow_checkpoint 时间戳 (机器生成文件) # commit-msg: minimal` |
| 最近 24h commits | 36 |
| 未 commit 文件 | 3 |

### 最近 10 commits

```
389ff6de chore: cm_resume 顺带刷新 workflow_checkpoint 时间戳 (机器生成文件) # commit-msg: minimal
9794f16e docs: session 收尾交接 — modal 实弹/34G 真相/goal-app 诊断入档, CLI 接手快照
e9758a9b feat: modal 计算面实弹打通 — CYQ 全市场复算函数 deploy 常驻
3ad48346 feat: 34G 拆分执行完成 — smartmoney 36.1G 紧缩至 19.3G, validation 全 PASS
a0bf931e feat: 三决议落地 — codex review 强制解除 + C0 实验脚本 + 34G 回收提级 P0
5094728e docs: alpha 组合矩阵定稿 — 16 设计经三轴评审, 4 run_first 带执行序
e8b9a8c0 feat: 筹码胜率 cyq_perf 注册排队 + 34G 库死表先行处置与拆分 runbook
7d7a1105 test: 复查问题系统性根治 — 按根因四 Phase, suite 红海清零恢复信号价值
adac636a test: 复查二次修正 — UTC 时区乌龙还原真相 + run_domain ok 双标改严格
3da900cb fix: Fable-5 复查降级期工作 — 概念事件字段方向反 + 验收文档引用错 两个实质问题
```

## NEXT ACTION (auto-computed)

**3 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
3. 新 Codex 会话输入: `请按照 docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查，再看 goal.md 和 live gates。`
4. Codex 先跑 live checks，再按 NEXT ACTION 执行本地工作 (audit / compare / commit / etc)
