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

**Snapshot 时间**: 2026-07-02 14:10:46 CST

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
| Backends | unavailable |
| Job plan | `scripts/chunkyctl jobs --family model_training --model-id <id> --input-snapshot <snapshot> --objective <why> --rollback-plan <plan> --gate-evidence <gate>=<artifact>` |

## Git 状态

| 项 | 值 |
|---|---|
| Branch | main |
| HEAD | `c22ec626 doc: 市场感知 follow-the-money 架构设计 + perception 残留清 (audit measured)` |
| 最近 24h commits | 18 |
| 未 commit 文件 | 2 |

### 最近 10 commits

```
c22ec626 doc: 市场感知 follow-the-money 架构设计 + perception 残留清 (audit measured)
e37337f4 feat: B1 股票分层模块 — 833万行全史分层标签, 策略cell单一计算点 (measured)
9e092351 feat: Phase A 机构档案 API — 排名/档案/建仓信号流 (总体路线第一站, measured)
81563e40 fix: institution_profile 补登数据模块成员 roster — moth serve-bypass 门红修复 (measured)
769089b6 doc: 文档保鲜 — changelog 滚动归档 + 正文/goal/地图同步 W1-W2 与总体路线 (audit measured)
ca1a2707 doc: 总体实施方案顶层定稿 — 主升浪方法论评估+形态/分层架构裁决+全局路线 (audit measured)
46b854a7 feat: W2 实盘模拟通用件 (手动版) — 各策略共用, 按地基构建 (SERVE第一个正式消费方)
7787a22e feat: W1 机构画像引擎 promote — edge 第一个正式模块 (探索弧→backend/services)
b29956e3 doc: 机构跟随探索弧 E1/E2 完成 — episode构建器+收益画像 (sandbox, verdict落库)
b8ff4cd2 doc: 机构跟随策略设计定稿 v1 — 数据地基实测 + 成本三方案 + episode模型 + execution-aware跟随
```

## NEXT ACTION (auto-computed)

**2 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
