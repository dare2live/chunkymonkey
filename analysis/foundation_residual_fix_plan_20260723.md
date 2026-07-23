# Foundation residual fix plan (2026-07-23)

> Status: evidence-only / live plan (execution board for residual knives)  
> Parents: `margin_calendar_catchup_blocker_20260723.md` · `dossier_100_usable_20260723.md` ·
> `serve_derive_closed_loop_law_20260723.md` · goal.md near-end focus  
> Label: **IN PROGRESS** (Knife 1a DONE · Knife 1b shipping)

---

## 0. Occam order

| # | Knife | Why first |
|---|---|---|
| **1** | Margin population-scope correction → then bounded calendar catchup | Sole Continuity `observe_frozen_stale` / catchup blocker with product meaning; calendar gap real (`eligible_end`≫`local_max`) |
| **2** | Ops Continuity residuals (non-cosmetic) | dividend/hsgt/etc. warns — only after margin path honest |
| **3** | Cap / product reopen | **Do not** reopen Cap F dossier unless live broken |
| **∅** | Org mass / Optuna / holdout / READY cosmetics | **Banned** |

---

## 1. Knife 1 — Margin population-scope (+ bounded catchup)

### Problem
v2 treated venue aggregates (**SSE/SZSE/BSE**) as business-facing canonical; pulse summed BSE into 两融总额. Freeze = `on_demand` + `scope_blocked` + live-write wall + formal runtime retired. Calendar still computes eligible_end; catchup blocked until scope is correct (not “no calendar”).

### Slice 1a — DONE (`e6b3e44c5`)
**Shipped**
- Registry `population_scope`: accepted claim = **SSE+SZSE** `external_aggregate` only
- Fail-closed binder: reject BSE-in-accepted-scope / project-universe relabel
- Kept v2 transport/BSE evidence shape + live-write freeze + formal runtime retired
- Continuity stayed `observe_frozen_stale`; pulse `rzrqye` UNTRUSTED

### Slice 1b — THIS knife — DONE (see `margin_v3_bounded_catchup_1b_20260723.md`)
**In scope / shipped**
- `contract_version=3`: transport `split_by` / completeness = SSE+SZSE only; no BSE
- Evidence reads filter current generation; v2 partitions remain read-only wrong-scope evidence
- `coverage_start=20260717`; execution `enabled` / `bounded_calendar_catchup` / `on_demand`
- Bounded land/accept path (explicit `--start/--end`, cap 10d, first-error stop); drain inapplicable; not `--all-due`
- Acquire bounded catchup; product hard-gate stays off (`product_blocking` absent)
- Continuity honest (no READY cosmetics); rzrqye stays UNTRUSTED

**Exit 1b**
1. `local_max` advances toward `eligible_end` on manual/bounded path — **verify live after catchup run**
2. Continuity observe clears or shrinks without claiming READY by deleting checks
3. No BSE rows in new-generation canonical; old v2 partitions remain read-only wrong-scope evidence

### Slice 1c — optional later
Consumer cutover for pulse margin fields (trusted external_aggregate SSE+SZSE label) — only with shadow evidence; not required to unblock catchup.

---

## 2. Knife 2 — Ops Continuity residuals (honest)

| Residual | Action |
|---|---|
| dividend / hsgt / other WARN | Measure; fix only if typed gate wrong — not green-by-silence |
| Cap F dossier | **Closed** (`dossier_100_usable_20260723.md`) — reopen only if live broken |
| Closed-loop org | **Closed** — incremental check only; **~830k mass ban** |

---

## 3. Hard bans (all knives)

- Optuna / holdout loosen / StrategyRelease / E–F remeasure without owner
- Org mass refresh / by-date invent
- Continuity READY via deleting observe / cosmetics
- Plugin-DAG / event-bus / Tier0←Tier3
- Margin product thaw of rzrqye while scope/generation unknown
- Mass historical margin backfill in one knife

---

## 4. Evidence anchors

| Claim | Path |
|---|---|
| Freeze why + calendar lag | `analysis/margin_calendar_catchup_blocker_20260723.md` |
| Cap F 100% usable | `analysis/dossier_100_usable_20260723.md` |
| Serve→derive law | `analysis/serve_derive_closed_loop_law_20260723.md` |
| Knife 1b v3 catchup | `analysis/margin_v3_bounded_catchup_1b_20260723.md` |
| v2 BLOCK / PROCEED scope | ledger 2026-07-18 margin superseding verdict |
| Live: accepted 1823d / BSE 827 in v2 canonical | `tushare_raw` read-only (do not rewrite) |

---

## 5. Delivery

§15: one logical knife = one Rule10 + one `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh`; L3 `chunkyctl pre-knife <name>`; push async (no `gh watch`). Parallel only if moth non-overlap.
