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

**Snapshot 时间**: 2026-06-27 21:47:51 CST

## 主线状态

| 项 | 值 |
|---|---|
| Model ID | `` |
| F2 checkpoint best_value |  |
| F2 checkpoint best_trial |  |
| F2 updated_at |  |
| F2 path | `` |

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
| HEAD | `a6b48eea feat: §9 Stage E 物删 — 4 dim 迁 reference 完成不变量#2 = 四地基全部 DONE` |
| 最近 24h commits | 38 |
| 未 commit 文件 | 1 |

### 最近 10 commits

```
a6b48eea feat: §9 Stage E 物删 — 4 dim 迁 reference 完成不变量#2 = 四地基全部 DONE
bc213639 feat: §9 Stage-E 安全账本 + Commit A 余 reader 收尾 (5 真问题修, dual-write 保留)
70657dec feat: §9 dim chunk4 — 24 reader 触点全迁 reference (19直读+5 JOIN重构) + listing writer
5a2ee11d feat: §9 dim_trading_calendar choke迁 + resolver.dim_read_conn 通用helper
1d3d98a8 feat: §9 dim_active chunk3 — active_codes helper + universe identity/ST 迁 reference
b9893e23 feat: market_perception 包整删 (reset 残留孤儿) + 修 candidate untagged
c2d613c7 doc: §9 执行 chunk1-2 模式打通 + 剩余 checklist (fresh续做手册)
e2d6fd31 feat: §9 dim_active chunk2 — active_stock_name_map auto-fallback helper + 迁 2 reader
c93c47f8 feat: §9 dim_active 迁移 chunk1 — writer dual-write reference+smartmoney
48145b5a feat: §9 执行起步 — resolver.connect_rw infra + scope 实测校准
```

## NEXT ACTION (auto-computed)

**1 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
