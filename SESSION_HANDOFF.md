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

**Snapshot 时间**: 2026-06-27 12:33:53 CST

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
| HEAD | `692536a9 fix: data_health 删表必删caller — 跳过deleted状态 + dim_data_asset注册表reality-sync` |
| 最近 24h commits | 28 |
| 未 commit 文件 | 2 |

### 最近 10 commits

```
692536a9 fix: data_health 删表必删caller — 跳过deleted状态 + dim_data_asset注册表reality-sync
3f015bbb feat: 通达信全删 M4 akshare external_attention — 退役+物删2表 (用户决cut)
ab2e5a6e feat: 通达信全删 M4 akshare event/panel — 退役3 builder+物删3表 (用户决cut)
bf0de44a feat: 通达信全删 M4 akshare capital — 源头退役+物删7表 (用户决cut)
17ca74dd feat: 通达信全删 M4 akshare capital — 切消费侧依赖(scoring quality_capital→0 / signals_v2 D5解禁门→不过滤)
d367b59e feat: 全清 — 退役 build_fundamental_quarterly + 物删 fact_fundamental_quarterly (gpcw派生L1)
7d4dcc38 fix: 全清 — 修 mart-lineage 子系统独立bug (解db_compact阻塞 + 2 pre-existing红测试转绿)
5874e391 feat: 通达信全删 单元6/7 xdxr/server 退役 — 物删2表 (通达信7数据单元全完成)
e4ecdcda fix: 修 main 上 4 红 K线测试 + 揭真生产 bug (price_kline_qfq_tushare schema-init 缺失)
a866f7ec doc: 清理 goal.md/plan worktree 谬误 — §9非worktree-blocked + 单元6/7与main上4红K线测试真纠缠 (用户纠错)
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
