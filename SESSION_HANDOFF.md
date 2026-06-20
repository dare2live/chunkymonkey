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

**Snapshot 时间**: 2026-06-20 08:44:31 CST

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
| HEAD | `18085c89 feat: daily_basic 回补 2019 对齐 K线/GT — strata 市值覆盖 83%→100% passed` |
| 最近 24h commits | 39 |
| 未 commit 文件 | 0 |

### 最近 10 commits

```
18085c89 feat: daily_basic 回补 2019 对齐 K线/GT — strata 市值覆盖 83%→100% passed
81e4c0f4 doc: D-step-1 因子判别 finding — reversal/vol 入场判别稳定(walk-forward passed) 但待含成本验证
bc854467 doc: C 完成 — F0+B+C 详情移 ledger, goal.md hunter 行精简 → D 焦点
e970a046 feat: C#48 step2 episode 阶段切分 fact_rally_stage (鱼头/鱼身/鱼尾)
0a80e5fa doc: C step1 strata done 进度指针 → 续 step2 fact_rally_stage
3977630f feat: C#48 episode PIT 分层 fact_rally_episode_strata (申万sector+市值+长底)
b9c865c1 doc: B(#47) tradability go/no-go = GREEN passed — 主升浪起涨点可买入率99.9%
eda381d0 feat: A0 完成 — fact_feature_panel 物化 live + 对抗验证 (F0 地基止血收尾)
255c2bb1 doc: A0 锁外全done(5 commit) 同步 — 余仅锁门 RUN, 续拉释锁后物化panel+审计注册
390c8c3a feat: A0 PIT 负样本 — hard-negative 对照组 fact_rally_entry_negative (结果倒推判别)
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
