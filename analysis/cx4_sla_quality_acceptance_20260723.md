# CX-4 acceptance — SLA / quality de-false-alarm

> Status: evidence-only / phased acceptance
> Authority: `MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md` §7.2 CX-4 + §7.1 C
> UX detail: `workbench_incremental_orchestrator_ux_20260722.md` P0.1
> Named consumer: workbench / daily_update `sla_warn` → `run_outcome` soft banner (no unknown-as-stale)
> Label: **PASS**

---

## Adversarial design (Grok × Composer-style REVISE → applied)

Composer CLI auth unavailable this session; forks applied as Grok self-adversarial + Composer-critique (same pattern as CX-3).

| Fork | Conservative (Grok) | Aggressive (Composer-critique) | Decision |
|---|---|---|---|
| `aif10_lhb` tomb | Exact PK delete allowlist only | Generic prune of all non-DOMAIN_SPECS rows | **Allowlist PK only** + refuse if still in live `DOMAIN_SPECS` |
| `miaoxiang_fact` | typed `no_probe` (observer ≠ publication) | Real probe `page_update_date` | **`no_probe`** — observer lag must not drive `sla_warn` |
| `qfii` | typed `no_probe` (disclosure clock) | Real `MAX(report_date)` + raise SLA | **Real probe + SLA 160d** (Q1→Q2 worst ≈153d to 8/31); keeps visibility, alerts after window |
| `sync:margin` frozen | Leave `DATA_STALE` alert (honest) | Silence all `on_demand` | **`observe_only` iff `execution_policy.mode=disabled`** — record `FROZEN_STALE_OBSERVED`, `alert=false`; daily/ST stay alertable |
| Continuity READY | Out of knife | Chase green | **Banned** — honesty lift = SLA unknown≠stale only |

Kill held: no live-source watermark deleted (`aif10_qfii` / `miaoxiang*` retained); true actionable stale not silenced.

---

## Scope delivered

| CX-4 item | Delivered | Evidence |
|---|---|---|
| Clear `aif10_lhb` tombstone watermark | YES | `_purge_retired_watermark_tombs` allowlist; live `lhb_daily` count=0; unit test proves delete without touching qfii |
| `miaoxiang_fact` typed no_probe | YES | `DATA_SOURCE_QUERIES[holders_top10_float_legacy_observer].no_probe` |
| `qfii` typed probe (not no_mapping) | YES | query `MAX(report_date)` + `SLA_DAYS_OVERRIDE=160` |
| Frozen margin observe≠alert | YES | `sync:margin` `observe_only` from `execution_policy.mode=disabled` |
| coverage/continuity honesty (named consumer) | YES | `sla_warn` / soft banner no longer lit by unknown/tomb/frozen; Continuity READY **not** chased |

**Explicitly NOT in this knife:** Continuity READY chase; margin thaw; mass org; Optuna; RX without owner签字; north-star rewrite.

Handoff draft `analysis/cx4_successor_prompt_20260723.md` consumed and **deleted** (no parallel bible).

---

## Kill criteria (held)

1. **No live-source watermark deleted** — post-purge: `lhb_daily/aif10_lhb` count=0; `aif10_qfii` + `miaoxiang`/`miaoxiang_fact` still present; `sync:top_list` watermark retained.
2. **True stale not silenced** — enabled domains still alert on `DATA_STALE_VS_SLA`; margin lag still **recorded** as `FROZEN_STALE_OBSERVED`.
3. **No Continuity READY wash** — no continuity checker / READY upgrades in this knife.

---

## Machine acceptance (2026-07-23)

```text
pytest backend/tests/scripts/test_update_watermark_sla.py
→ 26 passed (incl. purge allowlist + observer no_probe + qfii probe +
  margin observe_only + inventory gate + unknown still alerts)

Live (single-writer smartmoney, 2026-07-23):
  before dry-run: n_alerts=4 (observer/lhb/qfii NO_QUERY_MAPPING; margin DATA_STALE)
  apply:          lhb tomb absent (count=0; purge allowlist idempotent);
                  watermark rows=52; n_alerts=0
  focus:          observer=NO_PROBE_RULE; qfii=OK(sla=160, age=114);
                  margin=FROZEN_STALE_OBSERVED (observe_only=scope_blocked)
  local report:   data/audit/watermark_sla_latest.json (gitignored)
```

| Domain / source | Before | After |
|---|---|---|
| `lhb_daily` / `aif10_lhb` | `NO_QUERY_MAPPING` alert | **row purged** |
| `holders_top10_float_legacy_observer` / `miaoxiang_fact` | `NO_QUERY_MAPPING` alert | `NO_PROBE_RULE` alert=false |
| `qfii_holding_quarterly` / `aif10_qfii` | `NO_QUERY_MAPPING` alert | `OK` probe=observed actual=`2026-03-31` sla=160 |
| `sync:margin` / `tushare` | `DATA_STALE_VS_SLA` alert | `FROZEN_STALE_OBSERVED` alert=false |

---

## §7.1 C mapping

| Signal | Status |
|---|---|
| SLA unknown/tomb ≠ stale alert | **PASS** |
| `run_outcome` soft from fake SLA unknown-as-stale | **PASS** (n_alerts=0; `sla_warn` off for these false sources) |
| Continuity READY chase | **NOT DONE** (banned; residual ops) |
| Soft states may still include true clock-wait (`pending_publish`, etc.) | **expected** — not fake |

---

## Verdict

**CX-4 = PASS** for P0.1 SLA de-false-alarm + honesty lift on the named `sla_warn` consumer.

**CX-* gates complete** (CX-1…CX-4 PASS).

**Residual (owned, non-knife unless named consumer):**
- Continuity / coverage READY chase — **banned as code knife**; ops observe
- moneyflow_dc / facet lag fail-closed — honest; catchup = ops
- RX (E/F remeasure) — **BLOCKED until owner签字**
- Optuna / Phase N — **BANNED**

**Next:** stop. Do **not** open Optuna/RX unless owner already scheduled. Use shipped product surface + ops observe.

Chain rollup: `analysis/cx_closeout_rx_honesty_20260723.md` (CX-* PASS · RX BLOCKED · Optuna BANNED).
