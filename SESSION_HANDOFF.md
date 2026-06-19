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

**Snapshot 时间**: 2026-06-19 14:12:28 CST

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
| HEAD | `c76b3139 fix: by_ts_code 拉取走 services.universe 排除过滤 + 撤 namechange (用户: 排除列表硬真相源, 不拉退市股)` |
| 最近 24h commits | 13 |
| 未 commit 文件 | 4 |

### 最近 10 commits

```
c76b3139 fix: by_ts_code 拉取走 services.universe 排除过滤 + 撤 namechange (用户: 排除列表硬真相源, 不拉退市股)
6cc321e9 feat: 注册 5 P0 数据域 (tushare 潜力研究, 口径对齐项目)
1bf1ad6d docs: 修 tushare 研究口径一致 — 撤同花顺第三套资金流/概念 (用户铁律)
27244469 docs: tushare 10000积分选股潜力研究 — 拉取优先级 (6类评估 241接口)
29e90954 feat: 沙盒边界水密硬门 — 运行时 guard 堵裸连主库 (审计 BLOCKER)
d8332e7e docs: 主升浪猎手可执行方案 (架构审计 REVISE + 用户阶段框架) + goal.md 路线更新
f244e055 chore: 收尾彻底清除 — 7 归档档加"数字全作废"头注 (防误导)
19837577 feat: 探索沙盒机制 — 隔离区用完直接删, 根治探索散进主代码/文档
232b800c chore: 彻底清除残留污染期产物 (穷尽 sweep 第二轮, 64 删 + 引用修)
6b668552 fix: fact_feature_panel 三处虚假 active 声明 → 诚实状态 (架构审计抓到)
```

## NEXT ACTION (auto-computed)

**4 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
