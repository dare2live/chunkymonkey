# ChunkyMonkey Goal

> Current phase contract and implementation plan only. Keep this file compact:
> current objective, active priorities, live gates, and long-term direction.
> Completed work and detailed evidence belong in
> `analysis/project_state_ledger.md`; generated resume state belongs in
> `SESSION_HANDOFF.md`; pipeline artifact checkpoints belong in
> `analysis/workflow_checkpoint.md`.

## Document Contract

| Document | Owns | Startup use |
|---|---|---|
| `goal.md` | Current phase objective, priority board, implementation plan, long-term roadmap | Read first |
| `analysis/project_state_ledger.md` | Completed work, historical status, evidence notes formerly appended to `goal.md` | Query with `rg` or `tail`, not full startup read |
| `SESSION_HANDOFF.md` | Generated/manual runtime resume snapshot | Context-only; verify with live gates |
| `analysis/workflow_checkpoint.md` | Historical business pipeline checkpoint evidence | Context-only; not permission to resume obsolete provider work |
| `docs/chunkyctl_session_quickstart.md` | Startup procedure and controller workflow | Startup contract |
| `docs/README.md` | Active docs map and ownership rules | Docs authority map |

Update rule: if an item is done, move the evidence to
`analysis/project_state_ledger.md` and keep only the resulting current decision
or blocker here. `goal.md` should stay under roughly 150 lines unless the active
phase genuinely needs a larger plan.

## Current Phase

**Phase:** architecture/data governance foundation before deeper strategy work.

**Objective:** make the project controllable and auditable before expanding
data sources, provider compute, model search, or production promotion.

**Controller rule:** Codex owns direction, truth-source decisions, shared docs,
gates, staging, commits, and risky write windows. Side agents provide bounded
evidence or disjoint patches; their output is not a verdict.

## Active Priority Board

| Priority | Workstream | Current state | Next action |
|---|---|---|---|
| P0 | Documentation control plane | `goal.md` was overloaded with historical ledger content | Keep `goal.md` thin; move completed details to `analysis/project_state_ledger.md`; update startup/docs/Moth pointers |
| P0 | Provider-neutral execution surface | Old GCP scripts/launchd paths are being retired; `experiment_jobs` contract exists in dirty worktree | Finish reviewed provider-neutral cleanup slice, keep data-source work separate, then commit |
| P0 | Dirty worktree governance | `scripts/chunkyctl worktree --format markdown` reports dirty mixed slices and `unknown=0` | Stage one reviewed slice at a time; never `git add .` |
| P1 | `need_027` exact order-flow | Still blocked; rank/proxy snapshots do not satisfy exact-flow contract | Run token-backed no-persist TuShare/akshare exact-flow probe only when credentials/source stability are available |
| P1 | Data-source capability routing | iFinD/TuShare/tdxhub research evidence exists, but no production writer is approved | Convert source decisions into capability-level contracts and probes before any DB writer |
| P1 | DB retention/modularization | Manifest/connection boundary groundwork exists; retention is dry-run inventory only | Design retention/compact policy by table owner and consumer before deleting/VACUUM |
| P1 | Stage-opt supply | Structural upstream candidate supply blocker remains | Improve source/schema evidence; do not tune knobs as a substitute |
| P2 | Microsoft RD-Agent(Q) research | Useful external framework patterns identified; not yet mapped to ChunkyMonkey contracts | Deep-dive as isolated research-agent POC: map RD-Agent(Q)/Qlib research loop, Co-STEER, and factor-model co-optimization into borrow/reject decisions; keep production data/PIT/backtest/promotion in ChunkyMonkey |
| P2 | Data-health warning-only assets | No red or blocking-yellow in latest live doctor; 7 warning-quality tables remain yellow | Treat as owner-specific maintenance, not startup blockers |

## Latest Live Gate Snapshot

As of the latest checked state on 2026-06-05:

| Gate | Current result | Meaning |
|---|---|---|
| `scripts/chunkyctl doctor --fast` | `WARN` | Worktree dirty; data-health has warning-only yellow; `need_027` remains blocked |
| `scripts/chunkyctl worktree --format markdown` | `FAIL`, dirty mixed slices, `unknown=0` | Commit planning must be slice-based |
| `data_health_snapshot.py --dry-run` | `green=335 / yellow=7 / red=0 / blocking_yellow=0` | No startup data-health blocker; yellow assets are maintenance debt |
| `audit_execution_surface.py --include-live-launchd` | `PASS / 0 findings` | Retired execution-surface references are not currently detected |
| `need_coverage` | `need_027` blocked | Exact flow source evidence still missing |

Live gates override this section. Refresh before using the numbers as evidence.

## Implementation Plan

1. **Thin-doc control plane:** finish this document split and update
   quickstart/docs/Moth references so new sessions use the right entrypoints.
2. **Provider-neutral cleanup commit:** reconcile side-agent review, rerun
   execution-surface/test-tool/pytest/docs gates, stage only the cleanup slice,
   and commit with Rule 10 trailer.
3. **Data-source contracts:** keep iFinD/TuShare/tdxhub work in research/probe
   mode until capability contracts, PIT/freshness/watermark, and no-persist
   probes pass.
4. **Storage/DB governance:** continue from manifest + retention dry-run toward
   owner-based retention and compact policy. No production deletion without
   consumer evidence and rollback.
5. **Strategy/model work:** resume stage-opt and model exploration only after
   the upstream supply and data-source truth contracts are explicit.
6. **RD-Agent(Q) follow-up research:** run a read-only deep dive after P0
   cleanup, with output as a dated `analysis/` research note covering reusable
   RD-Agent(Q)/Qlib concepts, rejected pieces, integration boundaries, and gates
   required before any production code adopts the pattern.

## Long-Term Roadmap

| Area | Direction |
|---|---|
| Data sources | Capability-level routing: `tdxhub` backbone, iFinD semantic research snapshots, TuShare structured exact-flow/batch probes, each gated by PIT/freshness/watermark |
| Compute | `experiment_jobs` is the only approved job contract; `local` active, `modal` planned/blocked until adapter manifest, sandbox, cost boundary, and rollback exist |
| Research agents | Microsoft RD-Agent(Q) / Qlib ideas may feed isolated candidate generation and experiment design; production data, PIT, backtest, and promotion gates stay in ChunkyMonkey |
| Database | Manifest-owned DB aliases, read-only attached DBs by default, owner-based retention, no ad hoc scripts or hidden DB paths |
| Promotion | Measured paper sim / Phase4 / PBO / forward evidence only; in-sample or proxy metrics remain `unknown` |

## Operating Reminders

- Do not read or apply `CLAUDE.md` by default.
- Use `$architect-controller` for architecture/controller work,
  `$chunkymonkey-governance` before risky project execution, and
  `$chunkymonkey-review-gate` before commits or after `.py/.yaml/.sql` changes.
- Prefer first-principles truth sources: K-line for tradeability, calendar for
  dates, config/table/service owners for business rules.
- Remove proven-dead paths directly; do not keep obsolete code by comments,
  hidden flags, or compatibility shims.
