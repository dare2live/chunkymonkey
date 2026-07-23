# Margin update-flow catchup — 2026-07-23

> Status: evidence-only  
> Parent: `margin_v3_bounded_catchup_1b_20260723.md` · `global_cleanup_rebuild_plan_20260723.md` Knife 1  
> Label: **FIXED** (click-update / `daily_update` acquire path) — one-shot CLI is verification only

---

## Why 补跑 ≠ 正解

One-shot:

```bash
PYTHONPATH=backend python -m services.data_sources.sync_runner \
  --domain margin --start 20260717 --end 20260722
```

closes *today's* gap once. It does **not** guarantee the next workbench「数据更新」click
discovers and lands the next calendar-eligible margin day. Owner correction:
根治 = update orchestrator always plans by policy; 补跑 only verifies the writer.

---

## Product path (boundary)

```text
UI click / scripts/daily_update.sh
  → source .env (TUSHARE_TOKEN; never chat-paste into git)
  → pipeline.run → run_acquire
       → --all-due drain          # automatic domains only
       → formal daily/ST on_demand
       → margin_catchup_acquire   # THIS knife
       → frozen observe (only if still disabled)
  → margin stays in sync_runner + margin_* transport
  → NO import into dossier / holders / org / UI
```

| Layer | Owns | Does not own |
|---|---|---|
| Registry | `sync_policy=on_demand`, `execution_policy.enabled`, v3 SSE+SZSE | UI dates |
| `margin_catchup_acquire` | calendar gap plan → `run_domain` | landing writers |
| `sync_runner` / `margin_catchup` | land→validate→accept | orchestrator |
| Pulse `rzrqye` | stays **UNTRUSTED** | product thaw |

Hard bans kept: no `--all-due` margin, no hardcoded dates, no mass history, no product thaw.

---

## What changed (this commit)

1. **`sync_runner._publish_margin_bounded_catchup`** — stop importing
   `QuotaExhaustedError` from `sources.tushare` (class lives in `sync_runner`).
   That ImportError **broke the click-update path** as well as one-shot CLI.
2. **`margin_catchup_acquire`** — `local_max` = accepted_partition (current
   `contract_version`) first, then same-version canonical. Frozen v2 BSE
   evidence cannot mask or advance the v3 gap.
3. **Tests** — stale → schedule once; already-current → skip; `run_acquire`
   wires `run_margin_bounded_catchup` once.

---

## Verification (optional; not the deliverable)

Live DB after a prior verification run (data-only; not a substitute for the
pipeline fix):

| Metric | Value |
|---|---|
| v3 `accepted_partition` | 4 days `20260717`–`20260722`, `row_count=2` |
| canonical `local_max` | `20260722` (SSE+SZSE only on that day) |
| Continuity `--domain margin` | overall=**WARN** (honest `declared_vs_actual`; fail=0) |
| `attest_market_pulse_scope` rzrqye | **UNTRUSTED** |

Token: gitignored `.env` via `daily_update.sh` / `chunkyctl`
(`set=True len=64 prefix4=zkH9***` only — never full secret).

Next click with `local_max == eligible_end` must **skip**; when calendar
advances, acquire must schedule without operator dates.

---

## Residual

- Continuity WARN typed drift (v3 `coverage_start` vs retained v2 history) → Knife 4
- rzrqye product thaw → Knife 1c shadow only
- Peer Knife 3 compact WIP left unstaged (non-overlap)
