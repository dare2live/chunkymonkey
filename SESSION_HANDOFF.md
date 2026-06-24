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

**Snapshot 时间**: 2026-06-24 15:47:56 CST

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
| HEAD | `759f2b8a doc: 主计划文档按现状重新优化 (用户: 压缩后凭记忆漂移, 重读宪法+按现状调) — aif10立场反转/holder源/bypass诚实` |
| 最近 24h commits | 25 |
| 未 commit 文件 | 2 |

### 最近 10 commits

```
759f2b8a doc: 主计划文档按现状重新优化 (用户: 压缩后凭记忆漂移, 重读宪法+按现状调) — aif10立场反转/holder源/bypass诚实
20ca3d45 refactor: 退役孤儿 tdx F10 client 代码 (旧 updater 删后 dead code)
b35066a8 refactor: 物删旧 updater UI 簇 22 文件 (routers/updater*20+etf+data_sources) — pipeline 唯一更新路径
7d707403 feat: 迁 aif10估值/同行/QFII sync 进 pipeline acquire (先迁后删旧updater 步骤)
0b497f86 refactor: pipeline lhb 解耦旧 updater — sync_lhb_incremental 搬进 services.lhb_client
94d88a66 feat: 注册 tushare stk_holdertrade 域 (增减持) — tdx F10 多产品迁移 step
b226be56 fix: 修 asset metadata — live holder 表误标 deprecated (旧切tushare计划残留)
f5a362c8 feat: holder 退役收口 — 物删 tdx_f10 行(aif10 单源) + backfill 验收 99.6% + deprecate tdx ingest
d882a590 fix: holder aif10 行设 availability_source (PIT 可用日锚, 真金白银)
2d5630cf feat: holder aif10 增量改水位驱动 (按披露日) + 写入优化 + universe 加载修复
```

## NEXT ACTION (auto-computed)

**2 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
