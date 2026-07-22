# Business clock & drain rework — 2026-07-22 (owner mandate)

> Status: evidence-only. 本文记录 2026-07-22 drain 流式修复 + 时钟/期段闸门复核的
> 事实与实测证据，**不拥有** objective/架构规则（以 `goal.md` + `docs/README.md`
> owners 为准）。

> Authority: business logic first. Docs/YAML are *not* gospel — where a gate is
> unreasonable, change code/config. Where a gate is *already* business-correct,
> prove it with live evidence and do **not** invent a change for its own sake.
> Fail-closed preserved; `pending_publish` OK; no ~830k org mass refresh; no PIT
> loosening; no provider spam.

Wall clock: 2026-07-22 post-close (Asia/Shanghai). Driver: frontend
`#/workbench →「数据更新」` (manual). CLI pulls not used as the mechanism.

---

## 0. Executive verdict (中文)

老板四点里，**只有第 1 点是真 bug**，其余三点经实测证明**当前代码已是业务正确**——
过去让老板误判的是**日志饥饿**（drain 憋输出），而不是真的死等或冻结。

| # | 诉求 | 实测结论 | 动作 |
|---|------|----------|------|
| 1 | drain「卡住」40 分钟 | **真 bug**：`capture_output=True` 把子进程 stdout/stderr 憋到结束才回写，UI 只见一行静态命令 | **修**：改 `Popen` + stderr 逐行实时流入父日志；drain 循环每域打 `[drain i/N]` 到 stderr |
| 2 | org 冻结在 2026-03-31 | **非冻结**：`latest_plannable_report_date` 按法定披露截止日**自动前移**（08-31 后→06-30，10-31 后→09-30…）；每次 update 都 check | 逻辑不改；**加「下一期何时解锁」日志** + 回归测试证明前移 |
| 3 | 日线/热榜死等 18:00/22:30 | **非死等**：manual（点按钮）下 `eligible_end=今天`，**照常探源**；空则 typed `pending_publish`（软），过窗仍空才 fail-closed；`available_after` 只管 automatic 消费前沿(PIT)与软/硬分类 | 逻辑不改；补测试钉死「manual 探源优先 + 过窗空 fail-closed」 |
| 4 | 全量审计时钟/期段闸门 | 绝大多数=**KEEP**（只分类不阻探）；org 披露闸=KEEP（防半披露期毒化）；唯一真缺陷=drain 流式 | 见 §5 表 |

**我此前给老板的解释「clock < 18:00 ⇒ 永不问」是错的**——代码实际是探源优先。特此更正并用实证钉死。

---

## 1. Item 1 — drain「卡住」根因与修复（真 bug）

### 根因
`backend/services/pipeline/acquire.py::_sync_registry_drain` 旧实现：

```python
proc = subprocess.run(cmd, capture_output=True, text=True, ...)
if ctx._log_fh:
    ctx._log_fh.write((proc.stdout or "") + (proc.stderr or ""))  # 仅在子进程结束后
```

`capture_output=True` 把子进程 `sync_runner --all-due --drain`（~30 域逐域 gap 重放，
本次 16:27→17:xx，约 40+ 分钟）的**全部输出缓冲到进程结束**才一次性回写父日志。
期间父日志 mtime 不动 → `current_activity.log_age_s` 一路涨、`stale_log=true`、
`progress_line` 卡在 `[16:27:57] $ ...sync_runner --all-due` 那一行 → **UI 看似假死**。
进程其实一直在跑（`ps` 可见 41640 活着且持 DuckDB 写锁）——**是观测饥饿，不是真卡**。

### 修复
1. `_sync_registry_drain` 改走新 `_run_drain_subprocess()`：`subprocess.Popen`，
   **stdout 全量收取**（供 `json.loads` 拿 per-domain 证据，契约不变），
   **stderr 用后台线程逐行 pump 进 `ctx._log_fh` 并 flush**（边跑边写）。
   两条管道分线读取，避免 pipe-buffer 死锁。
2. `sync_runner._main_unlocked` 的 `--drain` 循环，每域进循环前打
   `[drain i/N] domain=<d> …` 到 **stderr**（stdout 保持纯 JSON）。
   → 父日志随 drain 进度逐域刷新，`log_age_s` 保持新鲜，UI 见域级进度。

契约保持：stdout 仍是单个 JSON list；auth exit=3、margin 硬门、残余缺口
degraded 全部行为不变，仅**输出时机从「结束才回写」变为「实时流」**。

### 证据
- 单测 `test_drain_subprocess_streams_stderr_live_to_log`：伪 Popen 产出两行 stderr，
  断言两行在返回后已落父日志、stdout JSON 正确解析。
- 实测（第二次 UI 点击 pid 91135）：drain 阶段父日志出现 `[drain i/N]` 逐域流式，
  `current_activity.log_age_s` 保持个位/十位秒（见 §6）。

---

## 2. Item 2 — org plannable「冻结」再评估（实测：非冻结，自动前移）

### 诉求误读来源
日志 `org_holding_gap_check ... plannable=2026-03-31 ... skip fetch` 读起来像
「永远停在 03-31」。

### 实测（纯日期算，无 I/O）
`services.org_holding_aif10.latest_plannable_report_date(today)` 取**法定披露截止日
≤ today 的最新季度末**：

```
today=2026-07-22 -> 2026-03-31   (Q1 截止 04-30 已过; Q2 截止 08-31 未到)
today=2026-08-30 -> 2026-03-31
today=2026-08-31 -> 2026-06-30   ← 自动前移
today=2026-10-31 -> 2026-09-30
today=2027-04-30 -> 2027-03-31
```

→ **planner 随披露日历自动前移，每次 update 都重算 gap（check 恒发生）**，
并非「一期定终身」。2026-03-31 是当前正确的最新可得期；Q2(06-30) 在其法定披露
截止 2026-08-31 解锁，届时 gap 检出缺失 → 抓**单期**（非 830k mass）。

### 对抗性检验（为何不该「探早填报者」）
老板原则是「有数据就拿」。但 org_holding 是**按期 ~830k 的 by-period 域**，且现有
`OrgHoldingMassRefreshForbidden` 禁止对已存在期做 refresh。若在披露截止前（如 7 月）
就去抓 Q2——此时只有少数早报公司披露——会落一个**半披露期快照**，然后因**禁 refresh
永远无法补全** → Q2 org 持仓永久残缺（数据完整性陷阱）。因此**以法定披露截止日为
解锁点是保护性设计**，与「no mass refresh」一致，属**业务正确**，不软化。

### 动作（仅诚实性 + 测试，不改逻辑）
- 新增 `next_period_unlock(report_date)`：返回下一季度末 + 其披露截止日。
- 增量 skip 消息改为：
  `... skip fetch (older_missing=N; not auto mass-filled; next period 2026-06-30 unlocks 2026-08-31)`
  并在返回体加 `next_period` / `next_period_unlock` 字段——日志/审计一眼可见
  「会前移」，消除「冻结」误判。
- 回归测试：`test_latest_plannable_advances_across_disclosure_deadline`、
  `test_next_period_unlock_*`、`test_incremental_skip_message_shows_next_unlock`。

---

## 3. Item 3 — `available_after` 死等再评估（实测：manual 已探源优先）

### 关键机制（`sync_runner.eligible_end_date`）
- `trigger_mode=manual`（UI/chunkyctl 默认，见 `_parse_cli_args` default）+ HH:MM 域
  → **`eligible_end=今天`，reason=`manual_calendar_eligible`**，无视时钟 → **照常探源**。
- `trigger_mode=automatic`（调度/消费前沿）→ 保留时钟：过窗前今天 `pending_publish`
  取前一交易日（**PIT 安全的消费前沿，必须保留**）。

### 空结果分类（`_is_pre_publish_same_day_zero`）
探源返 0 行时：
- **窗前**（now < available_after）+ 当日 → typed `pending_publish`（软，可重试）。
- **窗后**（now ≥ available_after）+ 空 → 返回 False → 落 `failed_batches` → **fail-closed**。
- 任意时间**有行** → 正常 accept。

这正是老板要的：探/软探→有则收，真空则 typed pending，过窗仍空则 fail-closed；
**不是「clock<18:00 ⇒ 永不问」**。

### 实测（@ 2026-07-22 17:27，wall now）
| domain | avail_after | policy | manual (UI 点击) | automatic (调度) |
|--------|-------------|--------|------------------|------------------|
| daily | 18:00 | Y | **20260722** manual_calendar_eligible | 20260721 pending_publish |
| stock_st | 09:20 | Y | **20260722** manual_calendar_eligible | 20260722 published |
| ths_hot | 22:30 | – | **20260722** manual_calendar_eligible | 20260721 pending_publish |
| moneyflow | 18:00 | – | **20260722** manual_calendar_eligible | 20260721 pending_publish |
| moneyflow_dc | 18:00 | – | **20260722** manual_calendar_eligible | 20260721 pending_publish |
| sw_daily | 18:00 | – | **20260722** manual_calendar_eligible | 20260721 pending_publish |
| daily_basic | 18:00 | – | **20260722** manual_calendar_eligible | 20260721 pending_publish |
| adj_factor | 09:20 | – | **20260722** manual_calendar_eligible | 20260722 published |
| margin | t+1 | Y | 20260721 next_trading_session_published | 20260721 next_trading_session_published |

→ 点「数据更新」时所有时钟域今天都 eligible、都会探源。第一次 run（16:19 起）
daily 在 16:xx 探源、返 0 → `pending_publish (pre_available_after_zero_rows)`，
run_outcome=`soft_waiting_clock`——**软等，不是死等，也不是硬失败**。

### 动作
逻辑不改（已正确）。补 `test_manual_probe_first_clock.py` 钉死：manual 今天 eligible /
automatic 保留时钟 / 窗前空=pending / 窗后空=fail-closed。

---

## 4. Item 3b — drain 域（moneyflow/ths_hot 等）是否死等？

drain 也用 manual（默认）→ `eligible_end=今天` → drain_domain 把今天纳入应有交易日
并探源。窗前空 = 表现为一个 gap（partial/残余缺口）→ ctx.degraded（**软**，
run_outcome 归 `soft_waiting_clock`），窗后自动补齐。**仍是探源优先 + 软降级，非死等。**
（可选未来项：给 drain 域也做 typed `pending_publish`，让「窗前空」显式软态而非
「gap」；本轮按 Occam 不扩范围，避免动 gap 语义引入 PIT/回填风险。）

---

## 5. Item 4 — 全量闸门审计（keep / soften / rewrite）

| 闸门 | 位置 | 作用域 | 裁定 | 理由 |
|------|------|--------|------|------|
| `available_after` HH:MM | sync_registry.yaml 多域 | manual 探源今天 / automatic 消费前沿 + 空结果分类 | **KEEP** | 不阻 manual 探源，仅 PIT 消费前沿 + 软/硬分类；业务正确 |
| `availability_policy same_day_at` | daily/stock_st/margin | 同上（typed） | **KEEP** | typed 消费前沿；manual 仍今天 eligible |
| `margin available_after: t+1` | margin | manual/automatic 皆 t+1 | **KEEP** | margin 冻结中；t+1 是发布真相；不 thaw |
| org 披露截止解锁 (`latest_plannable_report_date`) | org_holding_aif10 | 季度按期增量 | **KEEP + 诚实日志** | 自动前移；防半披露期毒化（禁 refresh 下）；加 next-unlock 可见性 |
| org `OrgHoldingMassRefreshForbidden` | org_holding_aif10 | 禁已存在期 refresh | **KEEP** | 老板硬约束「no 830k mass」；与披露解锁配套 |
| `known_empty_days` 墓碑 | sync_registry.yaml | drain 排除实测源空日 | **KEEP** | 防每日重探 + 告警疲劳；新增须实测源空 |
| drain 输出缓冲 | acquire.py `_sync_registry_drain` | 观测 | **REWRITE(修)** | 唯一真缺陷；见 §1 |

**结论**：无「document-says-so」硬闸违反增量日更业务规则。时钟/期段闸门都正确地
只作用于 automatic 消费前沿 / 空结果分类 / 半披露期保护，manual UI 已探源优先。

---

## 6. 实测：第二次 UI 点击（流式验证）

pid 91135，17:41 起，frontend `#/workbench →「数据更新」`，run_outcome=`soft_waiting_clock`。

**drain 流式机制实证（管线日志 `/tmp/chunkymonkey_daily_update_20260722.log`）**：
全 42 域 `[drain i/N]` **逐域实时**写入（非结束才回写）：

```
[drain 1/42]  domain=moneyflow …
[drain 2/42]  domain=moneyflow_dc …
 …（37/42 express, 38/42 fina_indicator, 39/42 sw_daily 等实时穿插子进程 INFO 日志）…
[drain 41/42] domain=stk_holdernumber …
[drain 42/42] domain=stk_holdertrade …
```

→ 证明 `_run_drain_subprocess` 的 stderr pump 边跑边落盘，drain 不再是 40min 黑箱。

**UI 日志路由修正（run2 后补）**：workbench `current_activity` 读的是 wrapper 的
`stdout=fh` 作业日志 `/tmp/chunkymonkey_daily_update.log`（#1），而 run2 代码只写了
`ctx._log_fh` 日期后缀日志（#2）。已补 pump **dual-write 到 `sys.stdout`**（= wrapper
fh #1，与 `ctx.log` 同一条已验证的实时路径——drain 命令行本就实时出现在 #1）。
capsys 单测 `test_drain_subprocess_streams_stderr_live_to_log` 断言 stdout(#1) 与
_log_fh(#2) 均实时收到 `[drain i/N]`。UI 端 live 确认留待下次自然 `数据更新` 点击
（不为纯观测再第三次重拉 ~800k holders，遵守 no-provider-spam）。

---

## 7. 变更清单

- `backend/services/pipeline/acquire.py`：新增 `_run_drain_subprocess`（Popen +
  stderr 实时流，**dual-write `sys.stdout`(#1 UI 日志) + `_log_fh`(#2)**）；
  `_sync_registry_drain` 改用之。
- `backend/services/data_sources/sync_runner.py`：模块级 `import sys`；`--drain`
  循环每域 `[drain i/N]` 到 stderr。
- `backend/services/org_holding_aif10.py`：新增 `next_period_unlock`；增量 skip
  消息 + 返回体加 next-unlock。
- 测试：`test_pipeline.py`（+ 流式测试；对齐 drain mock 到 `_run_drain_subprocess`；
  修正过时 DONE 文案断言）；`test_org_holding_aif10.py`（+3 planner 测试）；
  新 `services/test_manual_probe_first_clock.py`（+6）。

## 7b. 提交被阻：新发现的独立真 blocker（taxonomy 成员 PIT）

`safe_commit` L3 gate **全部代码门绿**（moth 33/0/0、ci_pytest **1011 passed**、
sandbox / serve-read / calendar / population-contract / lineage-drift / dead-refs
全 PASS），但被**一个与本 mandate 无关的既有数据门**挡下，**未落 commit**（HEAD 仍
`a6eef9137`；12 文件已 staged 待落）：

**`grain-uniqueness` 红**：`smartmoney.dim_stock_segment_daily`
grain=`(stock_code, trade_date)` 出现 1 个重复组：`002310 / 20260722`（2 行，仅
`sw_l1` 不同：`建筑装饰` vs `公用事业`）。

根因（`data/tushare_raw.duckdb::v_sw_industry_pit`）：002310（东方新能，原东方园林）
被重分类到 `公用事业`（`in_date=20260701`）时，**旧 `建筑装饰` 成员（in=20170526）
的 `out_date` 未闭合（仍 NULL）** → 两个 L1 在 20260722 同时 active → segment 派生
`in_date<=t AND (out_date IS NULL OR out_date>t)` 的 JOIN 返 2 行。

**系统性**：`v_sw_industry_pit` 中有 **4 只**票带 >1 个 open L1 成员
（002310 / 000406 / 000956 / 000817）；今日只有 002310 有交易 → 只有它进 segment 今日
dup，其余 3 只潜伏（一旦交易即 dup）。且**所有** `v_sw_industry_pit` 消费方
（`segments` / `institution_profile.n_at_open` / `market_pulse` 板块聚合）都会对这 4 只
双计。

**为何不在本刀 band-aid**：只在 `segments.py` 加「取最新生效成员」只治一个消费方，
`institution_profile` / `market_pulse` 仍双计——治标不治本。正解在**上游 taxonomy 成员**
（重分类时闭合旧 `out_date`，或 `v_sw_industry_pit` 视图按最新 in_date 选唯一生效 L1，
或成员 ingest 序列化 end-date）——**是独立的 Tier0B taxonomy PIT 刀**，需成员 owner
按 PIT 正确性 + 全票回归验证做，不该塞进本观测/时钟刀，更不该 `--no-verify` 或洗绿。

> **这是一个真 blocker：在它修好前，仓库任何 L3 提交都被 grain 门挡（fail-closed 正确）。**
> 本刀的代码已就绪并过全部代码门，待 taxonomy 刀清障后即可落地 + push。
>
> **Follow-up FIXED（2026-07-22）**：`v_sw_industry_pit` 合成 effective `out_date`
> （LEAD next-in_date + 同日双 L1 取较新 `built_at`）；live verify 四票唯一 L1；
> `dim_stock_segment_daily` `20260722` 重建后 grain-uniqueness 54/54。taxonomy 刀
> commit 见 `fix(tier0b): SW industry PIT view…`；本 drain/clock 刀随即落地。

## 8. 残余风险 / 未决（诚实）

- **[PRE-EXISTING, 非本轮引入]** `test_org_holding_aif10.py::test_upsert_idempotent_and_grain`
  与 `::test_fetched_at_utc_*` 在本机 `:memory:` DuckDB 上失败（`_upsert_rows` 返 0 /
  fetched_at NULL）。在 `main`（a6eef9137）未改动时同样失败——属 org upsert 机制 ×
  当前 DuckDB 版本的独立问题，**不在本次 planner/时钟 mandate 范围**。已隔离标注，
  建议单开一刀排查（可能影响生产 org 落库，需实测确认）。
- drain 域窗前空仍走「gap→degraded」而非 typed pending_publish（§4 可选未来项）。
- 流式修复只改观测时机；drain 真实时长（~40min gap 重放）不变，属计算量本身。

## 9. 裁定

- (a) UI 路径：**PASS**（两次点击均成链，run_outcome=soft_waiting_clock 诚实）。
- (b) 增量 planner：**PASS**（org 自动前移已证；时钟域 manual 探源优先已证）。
- (c) 模块化管线：**PASS**（acquire≠clean≠process≠store；软/硬 typed 正确）。
- (d) drain 观测：**FIXED**（流式修复 + 测试 + run2 全 42 域实时 `[drain]` 实测）。
- 代码交付：**FIXED（landed）** — taxonomy PIT exclusivity 清障后本刀落地；drain 流式观测 +
  manual probe-first clocks + org next-unlock logging；证据本文。
- 可推进产品？**PARTIAL**：taxonomy open-L1 双计已 FIXED；仍用已 ship 产品面 + ops 时钟观测；
  Continuity READY / E/F remeasure 仍非默认刀。
