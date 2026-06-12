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

**Snapshot 时间**: 2026-06-13 06:09:59 CST

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
| HEAD | `3e3e9283 fix: 概念事件 raw 源带点全名引号根因 + daily_basic 2020-2022 回填获批` |
| 最近 24h commits | 26 |
| 未 commit 文件 | 9 |

### 最近 10 commits

```
3e3e9283 fix: 概念事件 raw 源带点全名引号根因 + daily_basic 2020-2022 回填获批
9d48bf99 feat: LHB gate 转 GO — 首批类型推断陷阱根治 + G4 按实测剖面修法
26f913e7 chore: 纠正上一 commit 误入的本地工具链文件 — .claude 回归 settings.json 单文件在册
ce461328 chore: 残留大清理 — 5 线审计执行 (直删 21 文件 + 死代码 6 块 + 消费方 12 处同步 + 预存红 7 测清零)
fe111127 docs: 数据获取 v2 设计 + v2.1 三判官修订落账 — 渠道治理替代补丁循环
1d2f940e feat: 文档治理机器执法 — 状态标头契约 + 执法器入弹仓 (新旧混用防线)
222cf607 fix: 相同页守卫升级序不敏感 — 三判官抓的行序漂移盲区
1405fb0f fix: 分页相同页守卫 — 网关无视 limit/offset 的三态处置 (top_inst 风暴止血)
070e0759 docs: 前端统筹设计 v5 — 角色版面 x 档案体系 x verdict 流 (继承旧 v3 档案思路)
1270df95 docs: 前端重设计 v4 设计稿包 — 6 模块每日动线 IA, 已推 Claude Design
```

## NEXT ACTION (auto-computed)

**9 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
