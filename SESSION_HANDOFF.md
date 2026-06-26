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

**Snapshot 时间**: 2026-06-26 19:22:36 CST

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
| HEAD | `dbfc665f doc: 通达信全删 单元4 财务迁移 shadow验证DONE + 对抗验证字段映射(交接)` |
| 最近 24h commits | 23 |
| 未 commit 文件 | 1 |

### 最近 10 commits

```
dbfc665f doc: 通达信全删 单元4 财务迁移 shadow验证DONE + 对抗验证字段映射(交接)
6282c023 doc: goal.md architect re-anchor — 四地基对账+调整计划(财务数据质量发现重排优先级)
c1b12af7 feat: 通达信全删 单元4 值比对 — balancesheet回填DONE + 救出gpcw财务数据质量问题
612ea145 feat: 通达信全删 单元4 财务迁移 — 注册 balancesheet 域 + 值比对prep揭示模型重设计
bf65a9d5 doc: 通达信全删 单元4/5 财务迁移 spec + goal 不变量4 删源进度对齐
0d1b5e37 doc: 通达信全删 plan 更新 — 单元1/2/3 DONE(4表物删) + 单元6/7纠缠发现
5f33746d feat: 通达信全删 Batch2 — 物删 增减持意向 fact_shareholder_plan_tdx_f10 (单元1, 用户拍板覆盖归档)
7125422d feat: 通达信全删 Batch1 — 物删 户数+十大股东raw 3表 (单元2/3, 用户拍板)
11ae3d31 doc: 通达信全删迁移计划 (对抗验证+用户决议) — 7单元授权物删, 逐单元checklist
8d156979 doc: goal.md Gap1 PIT 审计 DONE 0泄漏 — 不变量4 leakage洞=0 实质收口
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
