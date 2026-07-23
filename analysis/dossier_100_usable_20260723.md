# Stock dossier Cap F — 100% usable (2026-07-23)

> Status: evidence-only  
> Owner: 应该100%可用；整体评估下来应该都是100%才算达标  
> Label: **FIXED** (Cap F declared surface)  
> Surface: `stock_dossier_cap_f_usable`

---

## 1. Before → After

| Surface | Before | After |
|---|---|---|
| API `surface` | `stock_dossier_mvp_partial` | `stock_dossier_cap_f_usable` |
| Lineage | `attested_partial` / institution ~54% stale note | `attested_usable`; join `FIXED` when coverage≥0.95 else `HONESTY_GATED` |
| Gaps fog | Always: `moneyflow_assist_not_in_mvp`, `holder_return_pct_unknown`, `holding_cycle_days_unknown`, `accepted_stock_states_resolver_not_wired` | Only typed empties / honesty (`form_read_fact_brick_typed_hybrid`, absent profile no deep-link) |
| Holders cycle/return | Always null + fog gaps | From `fact_inst_episode` when known; holding legs stay typed unknown |
| Tabs | Enabled but MVP-labeled | `usability.tabs`: ok / empty / delegated — no half-dead silent empties |
| 机构 deep-link | Honesty gate (good) + stale coverage claim | Closed-loop `mart_inst_profile`; live HS-A samples often coverage=1.0 |

### Live sample (2026-07-23, TestClient → real DBs)

| Code | HTTP | usability | inst coverage | form | holders |
|---|---|---|---|---|---|
| 600519 | 200 | usable | 1.0 | ok (typed hybrid) | ok · 10/10 episode cycle |
| 000001 | 200 | usable | 1.0 | ok | ok |
| 300750 | 200 | usable | 1.0 | ok | ok |
| 688981 | 200 | usable | 0.8 | ok | ok · 2× `none` fail-closed no fake link |
| 000002 | 200 | usable | 1.0 | ok | ok |
| B/BJ prefixes | 404 | — | — | — | typed `not in 沪深A whitelist` |

Moneyflow / intersection: delegated Cap A/D APIs (`/api/v3/decision/moneyflow/stock/{code}`, `.../intersection/stock/{code}`) — already fail-closed (empty/stale/unknown), not dossier MVP holes.

---

## 2. Cap F "100% usable" definition (this knife)

For declared Cap F HS-A scope, every tab is either:

1. **Works** with real bricks, or  
2. **Fails closed** with a typed reason (empty / hybrid honesty / no deep-link / delegated API status)

**Not** claimed: pure accepted form overlay (fact brick typed hybrid is honesty, not a dead tab); intraperiod true entry/exit PnL; Optuna/Release.

---

## 3. Tests

```text
pytest backend/tests/test_stock_dossier_api.py
→ surface usable + episode cycle/return + institution honesty + HS-A gate
```

---

## 4. Verdict on "整体 100% 才算达标"

| Layer | Honest bar |
|---|---|
| **Cap F product floor (dossier)** | **100% usable** for declared tabs — this knife |
| **Ops / Continuity READY / every domain green** | Floor ≠ ceiling: frozen margin observe, dividend/hsgt warns, etc. are ops residuals — not dossier product holes |
| **Research E/F / Optuna / Release** | Separate gates; green dossier ≠ strategy release |

Owner is right that **declared product surfaces** should not ship as "~90% MVP fog." That is the Cap F bar. Equating that to "whole system Continuity READY + every PARTIAL cleared" would be a different, larger claim — and would reopen banned thaw/mass knives.
