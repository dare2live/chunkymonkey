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

**Snapshot 时间**: 2026-06-15 17:35:11 CST

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
| HEAD | `69f1c5e8 chore: 刷新 Gate2 ablation json — STAT_EDGE_CONFIRMED + cohort绝对收益 (P2 改后)` |
| 最近 24h commits | 46 |
| 未 commit 文件 | 0 |

### 最近 10 commits

```
69f1c5e8 chore: 刷新 Gate2 ablation json — STAT_EDGE_CONFIRMED + cohort绝对收益 (P2 改后)
cb02825f feat: P3 实弹重裁决 — execution-aware 真引擎: 裸K线 reversal long-only A股结构性不可交易
637b2496 feat: P2 验证阶梯 R1 加固 — Gate2 两级转正+cohort绝对收益, cell-scan DSR, 绝对收益null
cb0aa881 feat: P1 引擎删除重建 — execution-aware (T+1 open/涨跌停/非对称成本/容量/仓位), 旧 return-based 删
a39d91e0 feat: P0 制度先行 — R1/R2/胜率-收益 判断法典工具化 (gate+hook+moth+法典)
ec7ed75d design: 旧设计缺陷批判 II — 8-lens 对抗复审确认 34 条 (N1-N30) + 根因链 R1/R2
6b1544af design: 旧策略设计缺陷批判+扩展 base 版 (7条, 用户'扩展深挖')
2485547c feat: 最强子格含成本 backtest — IC选格误导铁证 (IC最高+0.195但gross-34.6%)
dc173196 feat: 完整分层 Stage1.5×市值×换手 reversal IC — 验证用户思路, 最强子格小盘高换手+0.195
84ec5cd0 feat: 干净重建 return-based 回测引擎 — 旧引擎退役, Tier-2 裁决纠正 (gross +7.1% edge 真但成本结构杀)
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
