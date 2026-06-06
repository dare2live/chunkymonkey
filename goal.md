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
| P0 | Documentation control plane | Thin `goal.md` / ledger split committed in `8371e60c` | Keep current-state facts here; put completed evidence in `analysis/project_state_ledger.md` |
| P0 | Provider-neutral execution surface | Retired GCP execution surface removed and `experiment_jobs` contract committed in `8371e60c` | Treat `local` as active and `modal` as planned/blocked until adapter gates exist |
| P0 | Dirty worktree governance | Clean after `3e9fafc8`; `worktree` gate reports `unknown=0` | Keep future commits slice-based; never `git add .` |
| P1 | `need_027` exact order-flow | Still blocked; no-persist gate now emits source-group `controller_blockers` and `post_probe_gates`; latest focused run is `BLOCKED` with AkShare `akshare_remote_disconnected=3`, TuShare `tushare_token_missing=3`, and all post-probe gates `not_checked` | Restore source stability or provide token, rerun no-persist source-group gate, then require PIT/freshness, writer, watermark, and failure-queue evidence before production use |
| P1 | Data-source capability routing | Capability contracts committed in `3e9fafc8`; TuShare `moneyflow` adapter/gate wiring is local no-persist probe scope only | Keep new providers in capability-level probe mode before any DB writer or provider promotion |
| P1 | DB retention/modularization | Retention inventory now has owner/consumer/truth/compact contract; former unknown panels are protected by known runtime/research consumers | Migrate or retire panel consumers before any cleanup; keep production delete/VACUUM blocked unless copied-DuckDB validation and manifests exist |
| P1 | Stage-opt supply | Structural upstream blocker remains; readiness, source/schema, signal-date K-line, and source-aware density diagnostics are now reportable; latest live coverage is 100%, top short-window blocker is `fact_technical_trigger × live × reversal × stage 4` with only 1/2 signal rows per key | Repair upstream formula/source density using source-aware top cells; do not tune knobs or chase K-line coverage as a substitute |
| P2 | Microsoft RD-Agent(Q) research | Tracked as follow-up only; no production integration approved | Create a dated `analysis/rd_agent_q_research_*.md` deep-dive note mapping RD-Agent(Q)/Qlib reusable components, Co-STEER feedback, factor mining, report-to-code flow, experiment loop, agent role split, and experiment-manager contracts into borrow/reject/POC decisions under ChunkyMonkey gates |
| P2 | Data-health warning-only assets | No red or blocking-yellow in latest live doctor; 7 warning-quality tables remain yellow | Treat as owner-specific maintenance, not startup blockers |

## Latest Live Gate Snapshot

As of the latest checked state, with full doctor/stage-opt evidence and focused
`need_027` source-probe evidence from 2026-06-06:

| Gate | Current result | Meaning |
|---|---|---|
| `scripts/chunkyctl doctor --fast` | `WARN` | `need_027` remains blocked; stage-opt remains `upstream_candidate_supply` |
| `scripts/chunkyctl worktree --format markdown` | Use live gate before staging | Latest pre-commit check had `unknown=0`; keep future commits slice-based |
| `moth snapshot --repo . --format markdown` | CodeGraph `PASS` | CodeGraph is up to date; complexity diff has `new_high_count=0` |
| `data_health_snapshot.py --dry-run` | `green=335 / yellow=7 / red=0 / blocking_yellow=0` | No startup data-health blocker; yellow assets are maintenance debt |
| `plan_storage_retention.py` | `candidate_count=0 / table_inventory_count=12 / policy_contract=PASS / compaction.recommended=false` | Contract is complete, but no production delete/VACUUM path is open |
| `audit_storage_retention_consumers.py` | `PASS / audited_tables=11 / runtime_ref_tables=11` | Cleanup candidates are protected by explicit consumer evidence instead of unknown placeholders |
| `audit_execution_surface.py --include-live-launchd` | `PASS / 0 findings` | Retired execution-surface references are not currently detected |
| `need_coverage` | `need_027` blocked | Latest focused no-persist gate: `6` probes / `0` valid; AkShare exact-flow classified as `akshare_remote_disconnected=3`, TuShare `moneyflow` classified as `tushare_token_missing=3`; `post_probe_gates` remain `not_checked` |
| `audit_stage_opt_candidate_supply.py` | `WARN` | Short live audit `2026-06-01..2026-06-05`: `signal_kline_coverage_pct=100.0`, `signal_rows_without_bars=0`, `12155` keys blocked by `below_min_signals`; top source-aware cell is `fact_technical_trigger × live × reversal × stage 4` with buckets `1=1202 / 2=2183`; blocker remains upstream supply |

Live gates override this section. Refresh before using the numbers as evidence.

## Implementation Plan

1. **Data-source contracts:** keep iFinD/TuShare/tdxhub work in research/probe
   mode until capability contracts, PIT/freshness/watermark, and no-persist
   probes pass.
2. **Storage/DB governance:** continue from manifest + retention dry-run toward
   owner-based retention and compact policy. No production deletion without
   consumer migration/retirement evidence, copied-DuckDB validation, manifests,
   and rollback.
3. **Strategy/model work:** resume stage-opt and model exploration only after
   upstream signal density, data-source truth contracts, and live audit gates
   are explicit.
4. **RD-Agent(Q) follow-up research:** run a read-only deep dive after P0
   cleanup or in an explicitly bounded parallel research slot, with output as a
   dated `analysis/` research note. Compare the upstream RD-Agent(Q)/Qlib
   research loop, Co-STEER feedback, factor mining, report-to-code flow,
   experiment manager, and agent role split against this project's PIT,
   data-source, paper-sim, promotion, and `experiment_jobs` contracts. The
   expected result is a borrow/reject table, an integration-risk map, a mature
   component reuse list, and the smallest reversible POC plan. Mature ideas may
   be copied as isolated research tooling; no upstream framework, agent loop,
   generated factor, or model result can bypass ChunkyMonkey's truth-source,
   leakage, paper-sim, and promotion gates.

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
