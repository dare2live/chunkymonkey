# CX-3 acceptance — capability bricks (briefing + facet serve)

> Status: evidence-only / phased acceptance
> Authority: `MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md` §7.2 CX-3 + §7.1 A
> Named consumers: `#/briefing` + Market assist briefing panel; `#/explore` sector_membership / flow_streak; intersection sector chips; dossier streak/sector chips
> Label: **PASS**

---

## Adversarial design (Grok × Composer-style REVISE → applied)

| Fork | Conservative (Grok) | Aggressive (Composer-critique) | Decision |
|---|---|---|---|
| Briefing | Thin aggregator over Cap A/B/D; no new Tier2 math | Fancy LLM rewrite of conclusions | **Aggregator only** — conclusions/why already published |
| Cap B in briefing | Optional so A/D alone can narrate | Require Cap B trust | **Require Cap A+B+D trust** — CX-3 acceptance lists all three input bricks |
| Sector membership | Reuse pulse `list_sector_members` | New membership table | **Trust wrapper** over pulse leaf; SLA fail-closed for DC; SW honesty note (not PIT) |
| Stock streak | New serve on `fact_stock_moneyflow_dc_daily` | Browser invent from horizons | **Serve brick** with same-sign streak semantics as sector `flow_streak` |
| Kill | Stale/UNTRUSTED → empty narrative / empty universe | Soft-degrade with caveats | **Fail-closed** |
| Duplicate modules | Keep sms+sfs+briefing | Parallel `facet_serve.py` | **Dropped facet_serve** — one surface per brick |

Composer CLI auth unavailable this session; forks applied as Grok self-adversarial + peer Composer-style WIP reconciliation.

---

## Scope delivered

| CX-3 item | Delivered | Evidence |
|---|---|---|
| Briefing aggregation serve | YES | `daily_briefing.build_daily_briefing` → `GET /api/v3/decision/briefing/daily` |
| Briefing consumer | YES | `#/briefing` + Market assist `DailyBriefingPanel` |
| Sector-membership facet (was stub) | YES | `sector_membership_serve` → chips on intersection lists + dossier → `#/explore?kind=sector_membership` |
| Stock-level net-inflow streak universe | YES | `stock_flow_streak` → `GET /api/v3/decision/moneyflow/stock_streak` + dossier chip |
| Fail-closed on stale/UNTRUSTED | YES | Briefing `narrative=null` + empty sections; streak/membership empty rows when SLA lag |

**Explicitly NOT in this knife:** CX-4 SLA tombstones; Optuna; Continuity READY chase; Tier0 fusion; mass org re-pull; north-star rewrite; moneyflow_dc catchup (live lag is honest fail-closed, residual for ops/CX-4).

---

## Kill criteria (held)

1. **No Tier0 fusion** — all modules `tier0_write: false` / read-only smartmoney(+raw attach for membership).
2. **No stub facets narrating** — `facetStatus(sector_membership|flow_streak)=live`; intersection sector names are chips not dead text.
3. **Stale still narrating forbidden** — unit: moneyflow/intersection lag or mismatch → briefing `status=stale`, `narrative=None`; streak/membership lag → `status=stale`, `rows=[]`.
4. **Vendor imbalance honesty** — streak disclaimer + unit `wan_yuan`; never conserved-cash claim.

---

## Machine acceptance (2026-07-23)

```text
pytest backend/tests/test_cx3_capability_bricks.py \
       backend/tests/test_moneyflow_assist.py \
       backend/tests/test_decision_intersection.py
→ 22 passed

frontend: npx tsc --noEmit → 0 errors
scripts/chunkyctl pre-knife cx3-capability-bricks → OK
```

Live probe (read-only smartmoney, calendar expected≈20260722):

| Surface | Live result |
|---|---|
| `daily_briefing` | `status=ok`, moneyflow/intersection/screener **trusted**, narrative present |
| `stock_flow_streak` inflow≥5 | `status=stale` (`as_of_lag_6… as_of=20260716`) — **empty rows, no fake universe** |
| `sector_membership` DC | `status=stale` (`as_of_lag_6… as_of=20260716`) — **empty rows, no fake members** |

Live lag on moneyflow_dc / dc_member is honest fail-closed for those facet bricks; briefing uses Cap A pulse board + Cap D + Cap B as_of trust (currently ready).

---

## §7.1 A mapping

| Signal | Status |
|---|---|
| Briefing input bricks resolver green (trusted → narrative) | **PASS** |
| Facet serve bricks non-stub (sector membership + stock streak) | **PASS** |
| Consumers read real bricks (API + explore + chips) | **PASS** |
| Kill — stale/UNTRUSTED still narrating | **PASS** (fail-closed) |

---

## Verdict

**CX-3 = PASS** — briefing aggregation surface + sector-membership facet + stock flow-streak universe ship as Tier3 serve bricks with named product consumers; stale/UNTRUSTED inputs cannot keep narrating.

**Next:** CX-4 SLA/质量收口 per MASTER §7.2.
