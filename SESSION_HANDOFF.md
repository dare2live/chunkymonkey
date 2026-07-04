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

**Snapshot 时间**: 2026-07-04 21:46:17 CST

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
| HEAD | `4084fed6 feat: 感知 v3 (flow_regime 六标签+全模块下钻) + 加工层审计波1修复 (rzrqye 假摆动 HIGH 等 8 修)` |
| 最近 24h commits | 0 |
| 未 commit 文件 | 13 |

### 最近 10 commits

```
4084fed6 feat: 感知 v3 (flow_regime 六标签+全模块下钻) + 加工层审计波1修复 (rzrqye 假摆动 HIGH 等 8 修)
436ba2a6 docs: 市场感知 v3 设计定稿 — 资金流形态分类学 + 全模块层级下钻 (用户定调)
7b0f7714 fix: start.command 双击打开 404 根治 — 根路由指已退役 dossier 视图 + R4 轻修批 7 项
1b94dd7a feat: 数据底座六根因根治 R0止血+R1机制件 — 2 CRITICAL grain 静默丢失修复 + 四道新门
603651dc feat: 情绪温度可视化重设计 — 曲线换 climate-stripes 温度条纹带
2f98f78b feat: B4 v2 感知增强 + 前端浅色重设计 — 概念/龙头缺口闭环 + 5 新域 + 情绪周期仪表盘
1beb16bc docs: D1 结构常数敏感性留档 + v2 参数冻结 (考古修正#9 关账)
928d8b72 feat: D1 GT v2 交付 — 主升浪已知答案落库 5636 episode + Type A 纯度门列名根治
1b2a1c0a feat: B2 全量验收 + C4 感知页闭环 + D1 前置件 (GT考古/holdout立法/v2设计) — D 线开工就绪
e1a42d90 feat: C1/C2 edge 前端骨架 — React+Vite 机构档案页+实盘模拟页
```

## NEXT ACTION (auto-computed)

**13 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

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
