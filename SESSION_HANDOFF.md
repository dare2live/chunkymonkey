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

**Snapshot 时间**: 2026-06-19 08:21:20 CST

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
| HEAD | `2af8b39d feat: 清验证墓地+恢复干净地基 + universe升交易日历级硬真相源 + 结构型GT重建` |
| 最近 24h commits | 0 |
| 未 commit 文件 | 0 |

### 最近 10 commits

```
2af8b39d feat: 清验证墓地+恢复干净地基 + universe升交易日历级硬真相源 + 结构型GT重建
090e383a feat: 主升浪结构型定义重建 — 底→顶>60%+长底+多头排列+平滑, 排北交所/ST/退市, 9072个匹配用户图样型
e546debd feat: R2可交易性衰减检验 — 二次突破R2 robust(仅0.9%不可买/衰减-0.05pp), 前沿在buyability维站得住
27ecbf6d docs: Tushare数据资产盘点(10000积分档) — 已拉32域/已用4 live/16死数据 + 未拉可能有价值清单
d71d66af feat: return↔回撤前沿(culmination) — 二次突破平市真超额+24~32pp但回撤-42~-64%, OOS+152%是§4.2牛beta警报
cf280837 feat: 出场择时对比 — 脚本auto裁决误导(已纠), 真结论=出场是时长×return/回撤权衡无免费午餐 + CYQ触发器坏
08c734f8 feat: 3源alt-data(机构调研+龙虎榜+券商)+meta-labeling — 买点无显著regime稳定edge, 入场空间含alt-data穷尽
2550c061 docs: 记录 tinyshare 限流 120单/200多/并发2 (用户2026-06-17) — CLAUDE§4.3+INDEX§3.7+data_acq
84a378c8 feat: tushare代理切tinyshare — 旧jiaoch.site反刷量墙弃用, 新网关解封stk_surv机构调研
9aed0776 feat: 另类数据(龙虎榜+券商预期)买点因子 — ALTDATA_PARTIAL但券商上调在平市真区分(+2.37%vs-0.53%)
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
