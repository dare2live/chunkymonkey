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

**Snapshot 时间**: 2026-06-14 12:29:30 CST

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
| HEAD | `9ac37595 perf: DB 瘦身实删 62M 行过时表 (lifecycle 分析+对抗核证, 用户'有引用≠不能删')` |
| 最近 24h commits | 31 |
| 未 commit 文件 | 1 |

### 最近 10 commits

```
9ac37595 perf: DB 瘦身实删 62M 行过时表 (lifecycle 分析+对抗核证, 用户'有引用≠不能删')
d201815a feat: db_dead_table_audit 保守死表审计工具 + 纠偏 — 对抗验证挡住误删 (探索 agent over-claim)
1c1b1516 design: DB cutover-free 优化方案 (探索) — 渐进分区原则 + ~10G 瘦身清单
2935bd1d feat: S1 — express/fina_indicator 注册 + sync_runner by_period 分支 (基本面四件套补齐配置)
6e65b50d feat: alpha 验证程序 S1 — income 正式利润表接入 (基本面四件套第二个) + express/fina_indicator 设计就绪
5d747ffe feat: alpha 验证程序 S1 — forecast 业绩预告接入 (基本面四件套第一个, PEAD 事件因子)
c3447f42 design: DB 分区 cutover 暂缓定案 (探索后) — 引擎已证明, 为罕见竞争建永久 attach=rule6 反模式
6563613e design: D2-minimal 定案 + feature_store 迁验 PASS (探索后决策: 转 D2 比 D1 干净)
763d2c9f feat: DB 分区 D1a — 保真迁移引擎 + experiment_store 25 表迁验 PASS (用户: 平滑过渡)
f4ce3910 fix: governance_log 写入与业务表同事务原子 — 防 orphan governance (D0 扫描发现)
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
