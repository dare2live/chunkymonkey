# daily_update 通知刷屏分诊（2026-07-22）

> Status: evidence-only

面向 owner 的白话结论。证据：`/tmp/chunkymonkey_daily_update.log`、
`data/reports/daily_20260721.json` / `daily_20260722.json`、
`/tmp/chunkymonkey_ALERT_*.flag`、截图通知栈。

## 一句话

通知中心不是「脚本坏了 8 次」，而是 **同一轮软降级被 3 条通道各报一次**，再叠加
**多次 UI 点击重跑**（含早盘真实硬阻断）。多数是早盘/库存噪音；真正要盯的是
continuity 尾部与 margin SLA，不是把 soft degrade 当 FAIL。

## 当前跑次状态（截至 10:37）

| 项 | 状态 |
|---|---|
| 最新 `daily_update` | **已结束** `DONE with degraded`，exit **1**（非硬阻断） |
| Peer drain follow | **已收尾**（09:52→10:31 drain，随后 clean/process/store） |
| 进行中进程 | 无 `daily_update` / `sync_runner` |
| ALERT flags | 仍在：`daily_update` / `_degraded` / `continuity`（诚实残留，非僵死任务） |

详见 peer 证据：`analysis/foundation_acquire_all_due_unblock_20260722.md`。

## 截图里每条在说什么

时间线（本地日志）：

| 时间 | 通知 | rc / 文案 | 根因归类 |
|---|---|---|---|
| 07-21 20:42 | job FAIL | **rc=4** | **真实但已过**：preflight `margin:scope_blocked`（产品冻结域挡整链） |
| 07-21 22:08 | WARN + 4步降级 + FAIL rc=1 | 软降级三连 | drain 残余 + data_audit(327 kline∉universe) + continuity + SLA |
| 07-22 09:05/09:23 | job FAIL | **rc=5** | **早盘硬阻断（已知 RCA）**：formal `stock_st`/`daily` `zero_rows` 在 `--all-due` 前 kidnap |
| 07-22 ~10:37 | WARN + 3步降级 + FAIL rc=1 | 软降级三连 | drain 同日真空 + continuity(margin 尾断) + post-SLA；**formal 已 soft** |

### rc 语义（对照 `pipeline/run.py`）

| rc | 含义 | 早盘是否常见 | 算不算「还开着的 bug」 |
|---|---|---|---|
| **4** | preflight 硬挡（如 margin scope_blocked） | 偶发 | 策略/产品冻结域；不应伪装绿，但也不该当「采集全挂」 |
| **5** | Tier0 acquire 硬挡（formal zero_rows 曾 kidnap） | 开盘后～上午常见 | **编排刀已修**（drain-first）；今日 10:32 已见 `pending_publish` / ST ok |
| **1** | 全链跑完但有 degraded | 几乎每天 | **多数是预期软降级**；旧通知把它标成 FAIL = 误导 |

## 噪音 vs 真问题

### 噪音 / 预期（早盘或软降级）

1. **`pending_publish` / `pre_available_after_zero_rows`（daily / ths_hot 同日）**  
   时钟未到 `available_after` 的真空 —— 应 soft continue，不应再 exit 5。  
   今日 10:32 已按此路径；**不应弹「job FAIL」**。

2. **drain「有残余缺口」**  
   多域 `still_failed=['20260722']` 早盘空行 —— 发布滞后，不是架构回退。

3. **post-acquire SLA warn（miaoxiang_fact / aif10_* / 部分 tushare 观察者）**  
   与「今日 K 线是否进库」不同轴；会点亮 `sla_warn`。

4. **把 exit 1 标成 Script Editor「job FAIL」**  
   设计上 exit 1 = degraded 诚实退出；旧 wrapper 一律「FAIL」= **假失败告警**。

### 仍要跟的真问题（非通知层）

1. **continuity FAIL**（今日唯一 fail）：`calendar_gaps margin canonical_margin_exchange_daily` 尾部断流 3 日 > SLA 2。  
   margin 产品冻结 / on_demand —— 库存门持续红，**会稳定制造 degraded**。

2. **rc=5 sibling kidnap**（今早 09:xx）—— 编排已 rebuild；需靠后续早上窗口回归确认不再复现。

3. **07-21 data_audit「327 kline codes not in universe」** —— 与 ST/白名单刀相关的库存一致性；今日 10:32 clean 已 0 FAIL，但是否根治要另证。

4. **多次 UI 点击** 在硬挡/长 drain 期间重跑 —— 放大通知条数（不是同一 bug 复制八次）。

## 为什么会刷屏？

单次软降级结束时，旧链路发 **3 条 macOS 通知**：

1. `store._degraded_summary` → 「ChunkyMonkey degraded / N 步降级」
2. `notification.dispatcher` → 「ChunkyMonkey daily alerts … WARN」（sla_warn）
3. `manual_job_wrapper` → 「ChunkyMonkey job FAIL rc=1」

再乘以多次点击（rc=4 / rc=5 / rc=1）→ 通知中心堆满。  
不是 Script Editor 自己坏了，是 **我们主动 `osascript display notification` 三次**。

## 已做的小修复（本刀）

目标：软降级 **只留 1 条 macOS 横幅**；硬阻断仍响一次 FAIL。

| 改动 | 行为 |
|---|---|
| `scripts/manual_job_wrapper.py` | `rc==1` 且存在 `ALERT_<job>_degraded.flag` → **不弹 FAIL**（flag 仍写，doctor 可见） |
| `store.py` + `dispatcher --skip-macos` | 有 degraded 时 dispatcher **跳过 macos**（email 等仍可发）；degraded 横幅附带 SLA stale 摘要 |

硬阻断 rc=2/3/4/5 **仍通知 FAIL**（早盘真挡不吞）。

## CI ↔ daily_update

**无耦合。** GitHub CI 只跑离线 contract/unit（`engineering_governance`：live continuity / DuckDB / ALERT 不进 CI）。  
本地 Script Editor 刷屏 **不会** 导致 CI 红；CI 红也 **不会** 触发这些通知。  
近期 `gh run list` 最新若干 push 为 success；更早 failure（Cap E / schedule docs 等）交给 peer CI investigator（`analysis/ci_failures_triage_20260722.md`），本笔记不重复修 CI。

## 下一步（建议顺序）

1. **用下一次 UI 日更验证**：软降级应只见 1 条「degraded」，不见「FAIL rc=1」三连。  
2. **margin continuity**：产品冻结域如何从 continuity hard-fail 降级/豁免（单独刀，勿在通知层假绿）。  
3. **早上窗口回归**：确认不再出现 formal kidnap → rc=5（drain-first 已上线）。  
4. CI：跟 peer 分诊文档，与通知无关。

## Verdict

| 范围 | 标签 |
|---|---|
| 通知刷屏根因（三通道 + 假 FAIL） | **FIXED**（代码合入后下一跑生效） |
| 早盘 rc=5 kidnap | **FIXED**（编排；待早盘复验） |
| pending_publish 软路径 | **FIXED**（live 10:32） |
| continuity / margin 库存红 | **OPEN**（真数据/产品域，非通知） |
| 全链 Continuity READY | **PARTIAL** |

总体：**通知噪音 FIXED；地基软降级路径诚实；库存 margin/continuity 仍 OPEN。**
