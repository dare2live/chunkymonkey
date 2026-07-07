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

**Snapshot 时间**: 2026-07-07 22:37:55 CST

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
| HEAD | `9c5a1824 chore: storage_retention.yaml payload_audit 整段退役 — 双重孤儿(消费脚本已死+全部8表已死)` |
| 最近 24h commits | 9 |
| 未 commit 文件 | 28 |

### 最近 10 commits

```
9c5a1824 chore: storage_retention.yaml payload_audit 整段退役 — 双重孤儿(消费脚本已死+全部8表已死)
1166fa3e refactor: data_sources 多源注册表框架精简收口 — 删fallback机制/base+registry, 留sync_runner真实活线
8d001512 feat: aif10_capability_client 模块整体退役 + 3项顺带发现清理(用户"先深入研究再决定"→workflow验证)
a74ad13a feat: raw_aif10_peer_valuation(同行估值排名)整表退役 — 唯一消费方v3_picture已随重建死亡
c1c01398 fix: aif10 SLA 二轮更正 — 逐表实测节奏而非一刀切(peer_valuation实为年度/holder_period实为近日频)
2b3ac51a fix: doctor 全绿收口 — sync_registry drain瞬态网络gap补齐 + aif10/QFII SLA误报修正 + register()慢路径bug
56b19bd8 feat: dim_all_ever_listed/dim_listing_status 整表退役 (用户拍板选项A) + 全面文档更新
be7ab2bc chore: 重生 lineage graph.json 消解 kpl_list/cyq_perf available_after 改动带来的血缘漂移
78209bd3 fix: kpl_list/cyq_perf available_after 声明实测更正 (18:00→t+1 / 18:00→22:40)
64a26565 fix: 数据基础"是否具备条件"复审补 4 项 HIGH — live-guard盲区/dim_active_a_stock刷新/watermark冻结/5治理脚本接线
```

## NEXT ACTION (auto-computed)

**28 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
