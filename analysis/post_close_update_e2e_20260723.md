# 盘后一键更新 E2E 跟跑 — 2026-07-23

> **生命周期**：evidence-only（analysis 层；**非** owner bible）
> Verdict: **PARTIAL**
> Scope: 验证 UI「数据更新」同路径端到端（非新功能刀）
> Trigger: `POST /api/v3/ops/jobs/daily_update/run` → `manual_job_wrapper` → `scripts/daily_update.sh` → `python -m services.pipeline.run`
> Wall clock: 22:26:26 → 22:53:00 CST (~26.5 min)
> Evidence (local, gitignored): `data/audit/post_close_e2e_before_20260723.json`, `data/audit/post_close_e2e_after_20260723.json`, `data/reports/daily_20260723.json`, `/tmp/chunkymonkey_daily_update_20260723.log`

## 1. Path / boot

| 项 | 结果 |
|---|---|
| 本地 API | 启动 `python -m uvicorn main:app --port 8000`（venv 无 `uvicorn` 可执行文件） |
| 触发 | `POST /api/v3/ops/jobs/daily_update/run` → `accepted=true pid=23110`（与前端 Workbench 同 endpoint） |
| Preflight | calendar PASS；auth OK（expires 2026-08-12）；sync policy PASS domains=42 |
| 运行日 | `latest_expected=20260723`（当日为开市日） |

## 2. Before → after frontiers（关键域）

| 域 / 表面 | Before | After | 增量识别 | 动作 |
|---|---|---|---|---|
| accepted `nominal_ohlcv_daily` | 20260722 | **20260723** | due | land_then_accept 5526 rows |
| accepted `stock_st_daily` | 20260722 | **20260723** | due | land_then_accept 208 rows |
| accepted `margin_exchange_daily` | 20260722 | 20260722 | not due（eligible_end=20260722） | skip `latest_eligible_already_present` |
| qfq `price_kline_qfq_tushare` | ~2026-07-22 | **2026-07-23** | due | incremental rewrite/append |
| pulse market/sector | 20260722 | **20260723** | due | +1 day + late refresh |
| holders_aif10 wm | 20260723 | **20260724**（provider_max） | due incremental | affected=94，rows≈1.15M，errors=[] |
| org_holding plannable | 2026-03-31 present | same | check→skip | skip_current；next 2026-06-30 unlocks 2026-08-31 |
| moneyflow / moneyflow_dc / hsgt | 20260722 | **20260723** | due | drain/sync refill |
| dividend / stk_holdertrade | 20260721 | **20260722** | due（t+1 legacy） | sync ok；pending_today |
| holder_trade announcement accepted | 20260721 | **20260722** | advanced | +1 partition |
| technical_states / segments | — | +1 day | derived | form 5118 / seg 5526 |
| institution_profile | — | rebuilt | holders delta | ~184s；episodes/profiles refreshed |

## 3. Stage verdict

| Stage | Verdict | Evidence |
|---|---|---|
| boot / preflight | **OK** | calendar/auth/policy PASS；SLA alerts deferred |
| acquire | **PARTIAL** | formal daily/ST OK；org/holders 增量语义正确；drain 42 中 3 域 `unsupported` → soft DEGRADED |
| clean / derive | **OK** | qfq incremental max=2026-07-23；data_audit 6 PASS / 0 FAIL |
| process | **OK** | DC + segments + pulse + form + institution_profile 全跑通 |
| store / serve-pulse | **PARTIAL** | continuity **PASS**；pulse 已到 20260723；SLA 仍 2 alerts（见下） |
| Incremental recognition | **OK（主路径）** | 当日 due 的日更/脉冲/资金流/股东增量均识别并抓取；已齐域正确 skip |

## 4. Run outcome

- Report SSOT: `run_outcome=integrity_observe` / `ops_observe_non_hard_degraded` / exit 1
- Degraded（2）:
  1. `sync_registry drain 有残余缺口或域错误` — soft
  2. `post-acquire watermark SLA alert` — other
- Continuity: **PASS**（119 checks）
- **无 hard_fail / 无路径崩溃 / 无「当日应拉未拉」class-A**

> 日志尾 `DONE soft_waiting_clock` 与报告 `integrity_observe` 不一致：`run.py` 在 `rc!=0` 时硬编码 soft 文案（class-B 日志误标，非业务 outcome 错）。

## 5. Drain 残余（软 DEGRADED 根因）

`--all-due --drain` returncode≠0，因以下域 typed **unsupported**（非 fetch fail）：

| domain | status | batch_mode |
|---|---|---|
| `express` | unsupported | by_period |
| `fina_mainbz` | unsupported | （同批） |
| `stk_holdernumber` | unsupported | by_ts_code |

其余 ~39 域 drained/ok（含 moneyflow/hsgt/dc/sw/limit/adj_factor 等 gap_days=1 回填）。margin 在 all-due 外（on_demand + frozen）；catchup 亦 skip（local_max=eligible_end=20260722）。

## 6. SLA / 异常清单

| 级别 | 项 | 说明 | 处置 |
|---|---|---|---|
| class-B | `sync:margin` `ACCEPTED_PROJECTION_DRIFT` | wm=20260716 vs accepted=20260722；parser v2≠v3；**今日无应拉窗口** | 水位投影未跟 v3 accepted；非 should-fetch-didn't |
| class-B | `sync:stk_factor_pro` `NO_QUERY_MAPPING` | owner sunset / no_mapping；alert=true | 预期残留 |
| class-B | drain `unsupported`×3 → 每跑 soft DEGRADED | express / fina_mainbz / stk_holdernumber | registry/drain 分类债；非增量漏抓 |
| class-B | DONE 日志文案误标 soft_waiting_clock | 报告已正确写 integrity_observe | 日志 renderer 债 |
| observe | holders 进度曾 `fail=1` | 终态 `errors=[]`，wm 前进 | 可观测瞬时失败，未构成终态 FAIL |
| observe | org older_missing=27 | 依法 log-not-fill，不进 daily pipeline | 符合 owner ban |

## 7. 增量是否正常？

**是（主链路）**：盘后点击同路径下，formal daily/ST、qfq、pulse、moneyflow/hsgt、DC/SW、holders 增量、org check-skip 均按契约识别；已当前沿正确 skip（margin T+1 eligible、org plannable 已齐）。

**PARTIAL 原因**：每跑仍有 soft DEGRADED（unsupported drain + margin wm 投影漂移 + stk_factor_pro sunset），outcome=integrity_observe 而非 clean success——属 class-B 观测债，**未发现 class-A（应拉未拉 / 假 due / 路径崩溃）**，故本刀不改代码。

## 8. Label

**PARTIAL** — 端到端可跟跑、增量识别主路径 OK；残差 owner = drain unsupported 三域分类 + margin accepted-projection watermark reconcile + DONE 日志 outcome 文案；下次 verification = 再点一次「数据更新」应见多数日域 already-current skip，且不应再 land 20260723 formal（除非新交易日）。
