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

**Snapshot 时间**: 2026-06-13 22:53:37 CST

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
| HEAD | `03204a35 evidence: 压缩前探索 workflow 孤儿证据落档 (CYQ/IC/S3 ablation/资金流) + index sync fixtures` |
| 最近 24h commits | 39 |
| 未 commit 文件 | 0 |

### 最近 10 commits

```
03204a35 evidence: 压缩前探索 workflow 孤儿证据落档 (CYQ/IC/S3 ablation/资金流) + index sync fixtures
41f2fb8c design: 多维策略立方体顶层设计 (Segment×Feature-set×Policy) — 立法 PROCEED + 实例化 BLOCK
6dff6e00 chore: v1 旧库 34G 删除收尾 (用户授权删) — moth 断言移除 + goal 状态更新
8005079a feat: CYQ 筹码买卖点深挖 — px_pctile 真卖点信号但扣成本不独立, 落地=LHB 退出闸 (用户: 深挖)
f1e21345 feat: tushare raw 域挖掘 — daily_basic 风格因子是唯一真增量 (用户: 同步挖掘)
52a5bcc7 feat: 地基 index 族落库 — KPI 超额 HS300 真相源 + 申万 L2 PIT (用户: 先打地基)
9c3e3003 docs: 作战图对齐 06-13 进度 — 三判决落定/泄漏治理立/转攻地基 (用户: 文档对齐最新进度)
f0143b4a feat: 干净特征 forward-IC 探索结果 — 有诚实弱 alpha 0 泄漏 (充分利用 duckdb)
c1024c1c docs: 异常核查协议入泄漏模块+验证计划 (用户: 异常不应直接排除而应核查)
d86d8347 fix: safe_commit Step 3.6 变量名修正 — STAGED 未定义致消费方泄漏闸静默不触发
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
