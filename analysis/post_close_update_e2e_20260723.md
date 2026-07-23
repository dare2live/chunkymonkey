# 盘后一键更新 E2E 跟跑 — 2026-07-23

> **生命周期**：evidence-only（analysis 层；**非** owner bible）
> Verdict: **PARTIAL → FIXED (class-B in-flow residuals)**
> Scope: 验证 UI「数据更新」同路径端到端（非新功能刀）
> Trigger: `POST /api/v3/ops/jobs/daily_update/run` → `manual_job_wrapper` → `scripts/daily_update.sh` → `python -m services.pipeline.run`
> Wall clock: 22:26:26 → 22:53:00 CST (~26.5 min)
> Evidence (local, gitignored): `data/audit/post_close_e2e_before_20260723.json`, `data/audit/post_close_e2e_after_20260723.json`, `data/reports/daily_20260723.json`, `/tmp/chunkymonkey_daily_update_20260723.log`
> Follow-up knife: post-close class-B residuals（本笔记 §9）

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
| acquire | **PARTIAL → FIXED path** | formal daily/ST OK；org/holders 增量语义正确；drain unsupported×3 已退役 |
| clean / derive | **OK** | qfq incremental max=2026-07-23；data_audit 6 PASS / 0 FAIL |
| process | **OK** | DC + segments + pulse + form + institution_profile 全跑通 |
| store / serve-pulse | **PARTIAL → FIXED path** | continuity **PASS**；pulse 已到 20260723；SLA 投影/墓碑债已修路径 |
| Incremental recognition | **OK（主路径）** | 当日 due 的日更/脉冲/资金流/股东增量均识别并抓取；已齐域正确 skip |

## 4. Run outcome（当日实跑）

- Report SSOT: `run_outcome=integrity_observe` / `ops_observe_non_hard_degraded` / exit 1
- Degraded（2）:
  1. `sync_registry drain 有残余缺口或域错误` — soft（根因 = unsupported×3；已退役）
  2. `post-acquire watermark SLA alert` — other（根因 = margin 投影漂移 + stk_factor_pro 墓碑；已修路径）
- Continuity: **PASS**（119 checks）
- **无 hard_fail / 无路径崩溃 / 无「当日应拉未拉」class-A**

> 日志尾 `DONE soft_waiting_clock` 与报告 `integrity_observe` 不一致：已修 — DONE 文案跟 typed `run_outcome`。

## 5. Drain 残余（当日软 DEGRADED 根因；已 FIXED）

当日 `--all-due --drain` returncode≠0，因以下域 typed **unsupported**（非 fetch fail）：

| domain | status | batch_mode | 处置 |
|---|---|---|---|
| `express` | unsupported | by_period | **registry tombstone + inventory retired；2026-07-24 archive+DROP 26959 行** |
| `fina_mainbz` | unsupported | by_ts_code | **同上；2026-07-24 archive+DROP 25674 行** |
| `stk_holdernumber` | unsupported | by_ts_code | **registry tombstone + retired；表保留（见 `stk_holdernumber_retire_evidence_20260724.md`）** |

## 6. SLA / 异常清单（当日 → 路径修复）

| 级别 | 项 | 说明 | 处置 |
|---|---|---|---|
| class-B | `sync:margin` `ACCEPTED_PROJECTION_DRIFT` | wm=20260716/v2 vs accepted=20260722/v3 | **FIXED**：skip/publish 均 `project_margin_accepted_state` |
| class-B | `sync:stk_factor_pro` `NO_QUERY_MAPPING` | sunset residue | **FIXED**：wm tombstone purge allowlist |
| class-B | drain `unsupported`×3 | orphan 无消费者 | **FIXED**：retire like stk_factor_pro |
| class-B | DONE 日志误标 soft_waiting_clock | 报告已正确 | **FIXED**：log 跟 `run_outcome` |
| observe | holders 进度曾 `fail=1` | 终态 `errors=[]` | 可观测瞬时失败 |
| observe | org older_missing=27 | 依法 log-not-fill | 符合 owner ban |

## 7. 增量是否正常？

**是（主链路）**：盘后点击同路径下，formal daily/ST、qfq、pulse、moneyflow/hsgt、DC/SW、holders 增量、org check-skip 均按契约识别；已当前沿正确 skip（margin T+1 eligible、org plannable 已齐）。

## 8. Label（当日 E2E）

**PARTIAL** — 端到端可跟跑、增量识别主路径 OK；残差为 class-B 观测债（非 class-A）。

## 9. Follow-up knife — class-B in-flow residuals FIXED

| # | 残差 | 根因 | 修法（流程内，非补跑） | 状态 |
|---|---|---|---|---|
| 1 | drain unsupported×3 | orphan 仍进 `--all-due` | registry tombstone + `legacy_raw_plane` retired + S7 wall 19 ssot / 4 retired | **FIXED** |
| 2 | margin ACCEPTED_PROJECTION_DRIFT | accept/skip 不写 Ops 投影 | `_project_margin_accepted_ops_watermark` 挂 publish + skip | **FIXED** |
| 3 | stk_factor_pro NO_QUERY_MAPPING | wm 墓碑 | `RETIRED_WATERMARK_TOMBSTONES` 含 sync:stk_factor_pro(+三 orphan) | **FIXED** |
| 4 | DONE 误标 soft_waiting_clock | `rc!=0` 硬编码 | DONE 文案 = typed `run_outcome` | **FIXED** |

下次 verification：再点「数据更新」——不应再因上述 4 项 soft DEGRADED；margin skip 应带 `ops_watermark_projected=true`；DONE 文案应与报告 `run_outcome` 一致。
