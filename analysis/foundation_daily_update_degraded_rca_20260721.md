# Foundation daily_update degraded RCA (2026-07-21 click run)

> Status: evidence-only
> Scope: UI「数据更新」`21:53:18→22:08:12` CST, rc=1, `DONE with degraded (4 项)`.
> Evidence: `/tmp/chunkymonkey_daily_update_20260721.log`, `data/reports/daily_20260721.json`,
> `data/audit/continuity_20260721.json`, `data/audit/watermark_sla_{before_,}20260721.json`,
> live DuckDB read-only probes (2026-07-21 night).
> Related: `analysis/foundation_daily_update_ui_click_20260721.md`.
> Label: **PARTIAL** (RCA complete; no gate loosen; no mass backfill).

---

## A. Four degradations — root cause table

| # | Symptom | What check | Why fail | Severity | This run vs pre-existing | Next |
|---|---|---|---|---|---|---|
| 1 | `sync_registry drain 有残余缺口或域错误` | Acquire: `sync_runner --all-due --drain`; pipeline degrades on **nonzero exit** (`acquire.py`) | Two domain errors (not calendar residual gaps): (a) **`share_float`** `ann_date=20260720` batch **rollback** — `validate_universe_filter_column` saw mixed `ts_code` sample `['300999.SZ','874075','874075']` (bare BJ-like code without `.BJ`); fail-closed refused write; (b) **`ths_hot`** `trade_date=20260721` **`zero_rows`** at **22:04** (before `available_after=22:30`) → `failed_batches=1` `ok=False` — **same job already wrote 2214 ths_hot rows** and drained other Tushare domains; **not** `missing_token`. Other drains mostly `drained/clean` with empty `still_failed` | **Mixed**: share_float = **truth-guard working** (ann_date 20260720 float rows not landed); ths_hot = **ops freshness** (pre-publish same-day empty) | **Caused/exposed this run** (provider batch shape + same-day empty). Not a silent drop; later residual “缺 token” was agent-shell `.env` miss, not this run | **Knife**: share_float provider row normalize-or-quarantine **without** loosening `\d{6}\.(SH\|SZ\|BJ\|OC)` gate; ths_hot same-day empty → typed `pending_publish` / later retry post-22:30. **Do not** accept bare codes as OK |
| 2 | `data_audit` `cross_table_consistency` (~327) | Clean: `data_audit._check_cross_table_consistency` on `market.v_price_kline_qfq` via `classify_exclusion` | **327/327 extras = 北交所 `92x`**. qfq rebuilt `--from-accepted` from `canonical_nominal_ohlcv_daily` which already contains **328** `*.BJ` codes (history from `2026-01-16`; qfq 92x dates `20260717/20/21`). Message text still says “not in universe **tables**” (stale wording); rule is **board-prefix** exclusion | **Truth risk** for analysis/serving qfq (project universe = 60/00/30/68). Gate correctly FAIL — not cry-wolf | **Pre-existing leak into accepted nominal** (since ≥20260717 on qfq surface). Tonight rebuild **re-exposed** FAIL | **Knife**: stop BJ entering `canonical_nominal_ohlcv_daily` accept **or** explicit publication decision to expand universe. **Do not** disable audit. Optional L1: fix FAIL message wording |
| 3 | `continuity/integrity` FAIL | Store: `check_continuity_integrity` → overall FAIL | Exactly **2** `fail_stale_tail`: `raw_tushare_daily` + `raw_tushare_stock_st` — raw MAX=`20260716`, missing from **`20260717`**, 3 trading days > SLA 1. Formal accepted already at **`20260721`** (`tier0.market_data.nominal_ohlcv_daily` / `tier0.security_identity.stock_st_daily`); tonight acquire **skipped** formal daily/ST (`latest_eligible_already_accepted`). Continuity still audits **legacy raw** tables | **Dual-path observability**, not “accepted daily missing”. Raw lag ≠ execution/canonical hole | **Pre-existing dual-path** (formal ahead of raw). Continuity `20260716` already FAIL on other domain; tonight’s daily/ST raw fail is the same architecture gap | **Knife**: teach continuity (or watermark) to treat formal accepted frontier as daily/ST truth **or** optional raw catchup drain that does not fight modular skip. **Accept-as-known** until that knife — do not loosen SLA |
| 4 | post-acquire watermark SLA alert | Store: `update_watermark_sla` → `n_alerts=4` | Alerts: `aif10_lhb` / `aif10_qfii` = **`NO_QUERY_MAPPING`** (config/probe debt); `sync:daily` / `sync:stock_st` = **`DATA_STALE_VS_SLA`** watermark/actual=`20260716` vs sla_days=1 — same raw lag as #3. Preflight had **7** alerts; post-acquire improved (cyq/kpl/margin_detail drained) but these 4 remained | lhb/qfii = **cosmetic/config**; daily/ST sync WM = **same dual-path as #3** | **Pre-existing** (present in `watermark_sla_before_20260721.json`) | **Accept-as-known** for NO_QUERY_MAPPING until probe map knife; daily/ST tied to #3 knife |

### Stage map (this run)

| Stage | Status | Note |
|---|---|---|
| preflight | OK | SLA alerts deferred |
| acquire | check_fail | drain residual (#1); holders formal incremental OK; org skip OK; daily/ST skip accepted |
| clean | check_fail | qfq rebuild PASS self-check; data_audit FAIL (#2) |
| process | check_pass | DC dims + pulse SW +1d |
| store | check_fail | continuity (#3) + SLA (#4) |
| exit | rc=1 | degraded_total=4 |

**No gate was loosened in this RCA.** Fail-closed on share_float mis-shaped codes is correct behavior.

---

## B. 十大流通股东 (`holders_aif10` / `holders_top10`) parse correctness

### Run facts

| Item | Evidence |
|---|---|
| Log | `holders_aif10: watermark=20260717 affected=76 rows=987036 exits=3941 errors=[]` |
| Watermark source | Legacy `fact_top10_holder_period` `MAX(page_update_date)=20260717`; incremental `since = wm − 7d` → provider `UPDATE_DATE>=…` |
| Write path | Production `_write` → `write_holders_top10_formal_then_mirror(..., enable_legacy_mirror=False)` → **landing → canonical → accepted** (`formal_only`) |
| Tonight accepts | `tier0.disclosure.top10_float_holders_period`: **450** partitions accepted in `21:50–22:10` CST; partition span `20190201`–`20260721`; sum `row_count`≈**209021** (partition sizes after merge, not identical to sync `rows_written`) |
| Legacy fact | `fetched_at` max still `2026-07-16` — **not mirrored** tonight (by design) |

### Sample proof (canonical, notice≥20260715, non-exit)

| stock_code | report_date | rank | holder_name (abbrev) | hold_ratio_float | notice_date |
|---|---|---:|---|---:|---|
| 002161 | 20260714 | 1 | 徐玉锁 | 12.40 | 20260721 |
| 002161 | 20260714 | 3 | 深圳泽源私募…42号 | 4.19 | 20260721 |
| 300122 | 20260717 | 1 | 蒋仁生 | 12.11 | 20260721 |
| 300122 | 20260717 | 3 | 香港中央结算有限公司 | 1.47 | 20260721 |
| 300143 | 20260716 | 1 | 青岛盈康医疗投资有限公司 | 35.26 | 20260721 |
| 300491 | 20260714 | 6–8 | UBSAG / 高盛国际 / J.P.Morgan… | ~1.1–1.4 | 20260721 |

Integrity on tonight-accepted notice partitions in canonical: **bad_code=0, non-digit6=0, empty_holder=0, numeric_holder_name=0, holder_eq_code=0**; exits≈42k (typed exit rows present). Board prefixes dominated by 60/30/00/68; 2 B-share codes `900921`/`900938` present (venue edge, not column garbage).

Landing payload sample keys align with contract (`stock_code`, `holder_name`, `report_date`, `notice_date`, `holder_rank`, …) — not shifted garbage columns.

### Incremental window vs watermark `20260717`

- Provider-facing scan used safety window back to ~`2026-07-10`.
- Canonical notice dates after wm include **`20260718` / `20260721`** (and a later `20260722` partition exists outside the click window) — gap from wm **was filled on formal path**.
- `report_date` samples stay on/near report periods (e.g. `20260714`–`20260720`); `notice_date` is disclosure/update day — sensible.

### Accepted vs raw-only

| Plane | Role tonight |
|---|---|
| `landing_miaoxiang_holders_top10` | raw landing (payload JSON preserved) |
| `canonical_top10_float_holders_period` | accepted canonical rows |
| `accepted_partition` | partition registry for `tier0.disclosure.top10_float_holders_period` |
| `fact_top10_holder_period` | **legacy mirror skipped** — SLA/watermark probe still often reads this table → observability lag vs formal truth |

### Holders verdict

**FIXED** — parse integrity on the **formal land→accept** path for this run (codes, names, ranks, ratios, notice/report dates, incremental beyond `20260717`).

Residual (not parse failure): legacy fact watermark not advanced; SLA `holders_top10_float` still largely legacy-probed; rare `90xxxx` B-shares in canonical; optional knife to point watermark/SLA at canonical/accepted notice frontier.

---

## C. Code changes this knife

- **None** for gates. share_float fail-closed and BJ qfq audit FAIL are correct alarms.
- Tiny message-only audit wording left for a follow-up L1 if desired (not required for truth).

---

## D. Commits

- L1 docs: `docs(foundation): RCA daily_update degraded rc=1 and holders parse` (this file only; peer worktree untouched).
