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

**Snapshot 时间**: 2026-06-16 22:12:26 CST

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
| HEAD | `fbbd400f feat: D3 富因子+多因子crux — 主升浪买点判别全弱(OOS AUC 0.51≈null=LOW_CEILING), 待Optuna+Modal系统搜` |
| 最近 24h commits | 38 |
| 未 commit 文件 | 8 |

### 最近 10 commits

```
fbbd400f feat: D3 富因子+多因子crux — 主升浪买点判别全弱(OOS AUC 0.51≈null=LOW_CEILING), 待Optuna+Modal系统搜
85820f72 feat: D3 轨迹特征广扫(量价弱~1.1x) + moneyflow_dc 东财资金全拉(2023-10+, 384万行)
c0540c0c feat: 正确流程 D1+D2 — 主升浪 ground truth 重建(clean tushare, 2025-06前)+ episode 形态分类
7a5bd308 docs: alpha 方法论跑偏固化 — MASTER §5 加执行顺序铁律 (D1-first/禁信号正推)
ce334a39 feat: F2 cell条件化首验 — 低换手×大盘低波 +19.5%/-9%dd/Sharpe1.34 (方法论验证, 近6m衰减待DSR)
c0cb98b3 feat: F1 含成本裁决 — 位置-反转 IC真(+0.05)但全4cell KPI_FAIL, 形态=结构非alpha (印证方法论)
2ee26ebd feat: F0 形态面板 + F1 分离度诊断 — range_pos 反转信号 -0.043 (regime翻转), 正交轴方向验证
10db7da3 refactor: 耦合检查收敛到 moth coupling 单一真相源 (本地 check_coupling 退役)
2caaaad8 refactor: 去 chunkymonkey settings 的 architect_gate 重复 wiring (全局已 wire)
01c0b041 feat: wire architect_controller_gate 进 chunkymonkey UserPromptSubmit — 固化'改前计划/审计'前半环
```

## NEXT ACTION (auto-computed)

**8 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
