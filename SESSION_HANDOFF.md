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

**Snapshot 时间**: 2026-06-28 10:31:05 CST

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
| HEAD | `468a843e chore: db_compact 缩盘 + moth size-band 下限更新 (纯数据平台 3.0->0.34G)` |
| 最近 24h commits | 35 |
| 未 commit 文件 | 5 |

### 最近 10 commits

```
468a843e chore: db_compact 缩盘 + moth size-band 下限更新 (纯数据平台 3.0->0.34G)
68a37c0a feat: 重建 schema DDL 策略 trim — init 不再重建策略 mart (smartmoney 70->42 纯数据平台)
181768a1 feat: 重建 U2/U5 数据表物删 — 策略 mart/事件/处理表 (smartmoney 70→44 纯数据平台)
a078351e feat: 重建 = 白名单裁剪 — 项目降为纯数据平台 (git rm ~245 策略/serving 文件)
8c6f8909 feat: 加工层清空 U4 — 财务 derived 退役
e909e548 feat: 加工层清空 U3 — 主升浪 D1 GT + 技术 stage 退役
0320a505 feat: 加工层清空 U1 — L2 特征 panel 退役 (用户最严: 只留原始+四地基)
95a1bb4e doc: 数据模块设计文档更新到最新 — 四地基全 DONE + §9 Stage E 完成 (核对 workflow wajh30veq)
973842f1 chore: 刷新 SESSION_HANDOFF 快照 (§9 Stage E 完成 = 四地基全 DONE)
a6b48eea feat: §9 Stage E 物删 — 4 dim 迁 reference 完成不变量#2 = 四地基全部 DONE
```

## NEXT ACTION (auto-computed)

**5 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
