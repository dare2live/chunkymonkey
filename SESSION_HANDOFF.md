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

**Snapshot 时间**: 2026-06-21 00:19:38 CST

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
| HEAD | `19908acf doc: P1 决胜裁决 — cap-条件化含成本OOS验证方向(beats random+4.6pp)但远未达KPI` |
| 最近 24h commits | 20 |
| 未 commit 文件 | 0 |

### 最近 10 commits

```
19908acf doc: P1 决胜裁决 — cap-条件化含成本OOS验证方向(beats random+4.6pp)但远未达KPI
110fa654 doc: 主升浪猎手计划重优化 — 方法论锁定 + 条件化验证 + 优化后优先级 (P1含成本决胜)
9d750f99 feat: 负样本分层 fact_rally_negative_strata + 证特征形态条件化(净化后多因子OOS)
53842401 chore: 全删买点 detour — 方法论重定向回鱼身延续+鱼尾出场 (用户纠偏)
06bb9432 doc: SESSION_HANDOFF 再生 — D-step-4a C-R1 裁决后快照
c3ac6acd doc: D-step-4a 含成本现实入场裁决 — GBDT edge 真实但远不及 KPI (C-R1)
20526d45 doc: 进度保存 — INDEX D-step 状态同步 GBDT 翻案 (0.738) + SESSION_HANDOFF 再生
0cd1e8d4 doc: D-step-3 翻案 BREAKTHROUGH — 多因子 GBDT 拐点判别 OOS AUC 0.738 (单因子0.61)
abb77073 doc: 沉淀新反例 — event 定义点(pivot/peak)当入场=前瞻泄漏 (CLAUDE §4.5)
619171b2 doc: D-step-2 裁决 — 买点 reversal/vol 不可交易 (pivot 前瞻泄漏, C-R1 实证)
```

## NEXT ACTION (auto-computed)

**run startup checks first — scripts/chunkyctl doctor --fast; prioritize data_health blocking_yellow, then stage-opt structural blocker / need_027 blocked-gap triage**

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
