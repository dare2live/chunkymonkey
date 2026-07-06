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

**Snapshot 时间**: 2026-07-06 21:12:56 CST

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
| HEAD | `d4571bc2 feat: by_trade_date 域"今日补拉"机制 — 修 drain 永远排除今天的结构性缺口` |
| 最近 24h commits | 6 |
| 未 commit 文件 | 7 |

### 最近 10 commits

```
d4571bc2 feat: by_trade_date 域"今日补拉"机制 — 修 drain 永远排除今天的结构性缺口
d56c30cd fix: test_known_safe_list_entries_still_match_reality 改 skip 而非 assert (CI 无库场景)
91f1973c fix: check_dead_references scan_e 修复 CI 假阳性 (0 库文件误判成"确认死引用")
481dcad7 chore: 重生 lineage graph.json (T2 informational drift 清零)
fca5531b fix: stk_limit page_limit 静默丢数根治 + 全面数据审计治理机制修复
3033f067 feat: R4 数据连续性审计收口 — 41条逐项闭环 + K线边界孤立数据审计 + 资金流向曲线
eef8da75 feat: 数据地基根因根治 R1机制件 — 消费侧连续性硬门+审查器首跑+grain三层根因终结
4084fed6 feat: 感知 v3 (flow_regime 六标签+全模块下钻) + 加工层审计波1修复 (rzrqye 假摆动 HIGH 等 8 修)
436ba2a6 docs: 市场感知 v3 设计定稿 — 资金流形态分类学 + 全模块层级下钻 (用户定调)
7b0f7714 fix: start.command 双击打开 404 根治 — 根路由指已退役 dossier 视图 + R4 轻修批 7 项
```

## NEXT ACTION (auto-computed)

**7 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
