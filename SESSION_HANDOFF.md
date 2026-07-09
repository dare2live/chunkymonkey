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

**Snapshot 时间**: 2026-07-09 23:08:10 CST

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
| HEAD | `81d27913 fix: 6域数据缺口根因深查+根治 — margin查询形态/margin_detail阈值/stk_surv page_limit截断/report_rc+forecast+share_float周末数据结构性排除` |
| 最近 24h commits | 1 |
| 未 commit 文件 | 1 |

### 最近 10 commits

```
81d27913 fix: 6域数据缺口根因深查+根治 — margin查询形态/margin_detail阈值/stk_surv page_limit截断/report_rc+forecast+share_float周末数据结构性排除
356ccdc3 fix: 完整审计尾部22项WARN收口 — declared_drift抑制机制 + 12域行数骤降逐个实测(4真缺口回填+2假警报纠偏+6域annotate)
60811466 feat: legacy-flow-integrity 从informational升为真硬闸
6af3d67e fix: check_legacy_flow_integrity.py C1伪绿收口 + check_experiment_harness.py死代码清理
08c42513 fix: sw_daily 20260707源端单日空洞墓碑 + 多域尾部断流批量补拉
7c554840 fix: 数据地基收尾复核抓出的2个真尾巴清零 — doctor首次纯PASS
7f241587 chore: 重生 lineage graph.json (SERVE读层收口 T2 informational drift 清零)
9cdccda0 feat: SERVE读层门系统性收口 — 退役伪绿D1/D2, 提升scan_consumer_bypass为默认执法
e74cb228 fix: 独立核实批发现的2处文档遗留 + 1处轻微残留 — 13簇计划真正收口
c742d224 feat: 全仓库死代码普查收官 — 簇1+2+13: 旧前端整体删除 + PROJECT_INDEX架构自相矛盾收口
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
