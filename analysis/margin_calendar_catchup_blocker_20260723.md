# Margin calendar catchup — why frozen, what "不强拉" meant (2026-07-23)

> Status: evidence-only  
> Owner pushback: 为什么是故意的？不强拉是什么意思？交易日历在啊  
> Label: **FIXED** (honesty path) / catchup path **UNBLOCKED** by Knife 1b (`margin_v3_bounded_catchup_1b_20260723.md`); product rzrqye still UNTRUSTED

---

## 1. Why frozen (`on_demand` + `scope_blocked`)

Not because "there is no calendar."

| Fact | Evidence |
|---|---|
| Freeze root | 2026-07-18 ledger: v2 formal margin treated **venue external aggregate** (SSE/SZSE/**BSE**) as business canonical; pulse summed BSE into 两融总额 → wrong project-universe scope |
| Verdict then | `BLOCK v2 rollout / PROCEED population-scope correction` |
| Live write wall | `margin_acceptance._block_frozen_live_write` refuses writes to live `tushare_raw.duckdb` |
| Runtime wall | `sync_runner._refuse_formal_domain_runtime("margin")` → `formal_runtime_retired` |
| Registry | `execution_policy.mode=disabled` / `reason=scope_blocked`; `sync_policy=on_demand` |

Product honesty that stays: new pulse days keep `rzrqye` NULL/unknown rather than inventing from a wrong-scope thaw.

---

## 2. What "不强拉" meant (and did **not** mean)

| Phrase | Meaning |
|---|---|
| **不强拉** (2026-07-21 unblock) | Margin is **not** in `--all-due` / automatic drain, so frozen `scope_blocked` does **not** hard-fail `daily_update` preflight (exit 4 deadlock) |
| **Not** | "Never fetch / calendar irrelevant / forever silent skip" |

Calendar still computes `eligible_end` (live 2026-07-23: `eligible_end=20260722`, `reason=next_trading_session_published`).  
Local frozen evidence max: `local_max=20260716` (canonical/raw). Gap is real.

Owner is right: **calendar-driven incremental catchup ≠ mass refresh**. That path is correct *in principle* for daily/ST. For margin it is **blocked** because catchup would re-open the wrong-scope v2 writer — that *is* product thaw, banned until a population-scope correction knife.

---

## 3. Occam fix shipped (this knife)

1. **Continuity**: disabled domains with actionable tail/interior FAIL → `observe_frozen_stale` (records `local_max` / `eligible_end` / `catchup_blocked=true`). Parallel to SLA `FROZEN_STALE_OBSERVED` (CX-4). Does **not** claim Continuity READY by deleting the check.
2. **Acquire**: `_observe_frozen_on_demand_domains` logs typed `observe_frozen` JSON for margin (no provider call).
3. **Registry comments** clarify on_demand ≠ "no calendar".

Live continuity (2026-07-23):

```text
[observe_frozen_stale] margin … local_max=20260716 eligible_end=20260722 catchup_blocked=true
continuity-integrity: overall=WARN fail=0 observe=1  # was FAIL solely from margin tail
```

---

## 4. Residual / owner door

| Item | Status |
|---|---|
| Bounded calendar land/accept for margin | **OPEN** — Knife 1b v3 SSE+SZSE on_demand bounded catchup |
| Product thaw / all-due re-entry | **Banned** until shadow/consumer knife (1c) |
| Mass backfill | **Banned** |
| Continuity READY product claim | **Not** upgraded by this knife (checker may WARN from other domains) |

Tests: `test_calendar_gaps_frozen_disabled_domain_observes_not_fail`, `test_observe_frozen_margin_logs_calendar_lag_without_fetch`.
