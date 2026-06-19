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

**Snapshot 时间**: 2026-06-19 21:39:37 CST

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
| HEAD | `8d2ad93e feat: A0-1 主升浪 stage 因子恢复进 services (消除 build_feature_panel→experiment 倒挂)` |
| 最近 24h commits | 25 |
| 未 commit 文件 | 1 |

### 最近 10 commits

```
8d2ad93e feat: A0-1 主升浪 stage 因子恢复进 services (消除 build_feature_panel→experiment 倒挂)
c5501515 docs: 数据验证+回测 refined plan (tushare字段gap+A股alpha经验+回测best-practice 研究综合)
91c005b4 feat: sync_runner socket 超时 (根治 hung) + by_ts_code 断点续拉 (用户选: 修socket超时+续拉)
38f3da8a feat: tinyshare 限流 config 驱动主动节流 (用户: 规则进配置文件+流程行为强制, 非仅文档)
59ebecd1 docs: goal.md 数据底座行同步本session (universe真相源切换 + 6/6非tushare退役) + ledger 退役批次
a5991e90 retire: 物删 financial_indicator 簇 3表(fact/dim/sync_state) + dedicated writer — 6/6 SAFE_TO_DROP 完成 (用户: 继续退役非tushare源)
1485c13b retire: 物删 fact_hsgt_daily(2767) — build_akshare_panel shared writer 删build_hsgt_daily留5表 (用户: 继续退役非tushare源)
bb245c57 retire: 物删 aif10 孤儿表 holder_count(742k) + financial_history(5713) — shared writer 删2留3 (用户: 继续退役非tushare源 Batch B)
0c2eeb8a retire: 物删 2 非tushare孤儿表 fact_orderbook_snapshot + raw_fund_flow_daily (用户: 继续退役非tushare源, Batch A 清洁)
1e9fa1c0 feat: universe 身份真相源切 tushare stock_basic + 退役 akshare dim_active (用户: 做+退役旧表+盘点非tushare源)
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
