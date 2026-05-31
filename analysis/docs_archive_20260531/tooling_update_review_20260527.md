# Tooling Update Review — 2026-05-27

## FAIL / Risk First

| Item | Current Evidence | Risk |
|---|---:|---|
| Git worktree | `scripts/chunkyctl doctor --fast`: 183 dirty entries | Long-lived mixed state makes scope review, staging, and agent ownership fragile |
| CodeGraph | pending total 49 added files; `scripts/chunkyctl audit --run ...` ran `codegraph sync .`, but status still reports them while they remain untracked | Future agents can see stale structure unless sync/reporting is explicit |
| Complexity | baseline defaults to ignored local file `data/reports/tooling/complexity_baseline.json` when present; otherwise current findings are reported as `unclassified`, not `new` | Historical HIGH and new HIGH must not be confused without a loaded baseline |
| External tools | `lazygit`, `delta`, `git-absorb`, `git-town`, `git-branchless`, `jj`, `wt` not installed locally | Installing new workflow tools before classifying current dirty state could add process noise |

## First-Principles Decision

The core problem is not that Git lacks another UI. The problem is that project
state is not yet machine-classified into current work, accepted architecture
slices, archival moves, generated artifacts, and deletion candidates.

Therefore the first tooling update is local and auditable:

| Decision | Rationale |
|---|---|
| Keep using native Git + existing project scripts for now | Lowest assumption path; no new workflow semantics while `main` is already dirty |
| Add project wrapper `backend/scripts/audit_tooling_gate.py` | Converts `git status`, `codegraph status`, and complexity markdown into JSON |
| Add baseline/diff support before relying on broad complexity scans | Separates historical debt from new risk |
| Treat worktree managers as Phase 2 | Useful only after current main dirty state is classified and future parallel agents can use isolated write scopes |

## GitHub Tool Survey

| Tool | Usefulness Here | Decision |
|---|---|---|
| [Worktrunk](https://github.com/max-sixty/worktrunk) | Git worktree manager aimed at parallel AI-agent workflows | Candidate after current dirty state is clean/classified; do not introduce mid-dirty |
| [lazygit](https://github.com/jesseduffield/lazygit) | Terminal UI for staging hunks, browsing diffs, undo, rebase | Optional human UI; not a controller evidence source |
| [delta](https://github.com/dandavison/delta) | Better diff pager with syntax/word-level highlighting | Optional display improvement; low risk, no governance semantics |
| [git-absorb](https://github.com/tummychow/git-absorb) | Auto-generates fixup commits from staged hunks | Not suitable until scoped commits exist; risky for current mixed main state |
| [git-town](https://github.com/git-town/git-town) | High-level branch workflow automation | Defer; project currently operates on `main` with `safe_commit.sh` |
| [git-branchless](https://github.com/arxanas/git-branchless) | Advanced stacked/branchless Git workflow | Defer; too much workflow change for current architecture phase |

## Local Tool Update

New local tool:

```bash
PYTHONPATH=backend python backend/scripts/audit_tooling_gate.py \
  --repo . \
  --complexity-target backend \
  --baseline data/reports/tooling/complexity_baseline.json \
  --output data/reports/tooling/tooling_gate.json
```

Capabilities:

| Capability | Output |
|---|---|
| Git status JSON | total dirty count, staged/unstaged/untracked/modified/deleted counts, per-file entries |
| CodeGraph status JSON | project, index stats, node/language counts, pending counts, `sync_required` |
| Complexity parse | structured findings from complexity-optimizer markdown |
| Complexity diff | with loaded baseline: `new_findings`, `resolved_findings`, `unchanged_findings`, `new_high_count`; without baseline: `unclassified_count` / `unclassified_high_count` only |
| Baseline write | `--write-baseline <path>` creates reusable JSON baseline; `chunkyctl doctor` auto-loads `data/reports/tooling/complexity_baseline.json` when it exists |
| Preflight task risk matching | Task words are token-matched, so `build` no longer triggers the `ui` frontend gate |

Current local baseline evidence (2026-05-31):

```json
{
  "baseline_path": "data/reports/tooling/complexity_baseline.json",
  "baseline_status": "loaded",
  "diff_status": "compared",
  "new_high_count": 0,
  "unchanged_count": 40
}
```

The baseline is under gitignored `data/reports/`; it is local tooling evidence,
not a committed strategy artifact.

Smoke evidence:

```json
{
  "verdict": "FAIL",
  "git_total": 183,
  "git_counts": {
    "deleted": 3,
    "modified": 112,
    "unstaged": 115,
    "untracked": 68
  },
  "codegraph_pending": {
    "added": 49,
    "sync_required": true,
    "total": 49
  },
  "baseline_status": "not_configured",
  "diff_status": "baseline_unavailable",
  "new_high_count": 0,
  "unclassified_high_count": 40
}
```

The fast doctor uses a capped backend complexity target. With no baseline loaded,
`unclassified_high_count` is historical debt inventory, not a regression claim.
Generate or load a scoped baseline before treating `new_high_count` as evidence.

Post-sync note: `scripts/chunkyctl audit --run ...` completed its `codegraph sync .`
step, but `codegraph status .` still reports 48 files as Added pending because
the architecture split files are still Git-untracked. Treat that as worktree
classification risk, not as permission to bulk stage.

## Project Assistant Direction

The useful end-state is a repo-local audit/development assistant, tentatively
`chunkyctl`, not a large opaque prompt. It should make project rules executable
and reproducible for every Codex session or terminal.

| Command | Purpose | Source of Truth |
|---|---|---|
| `chunkyctl doctor` | One-shot project health: dirty worktree, worktree bucket summary, CodeGraph pending/untracked reconciliation, complexity diff, test-tool registry, universe gate | local scripts + JSON evidence |
| `chunkyctl worktree` | Read-only dirty worktree bucket report for review/stage/delete planning; JSON by default, `--format markdown` for controller review | `git status --short` + repo bucket policy |
| `chunkyctl preflight --task "<task>"` | Task-specific entry report with touched symbols, risks, required gates, and likely tests | CodeGraph + AGENTS/goal/config |
| `chunkyctl audit --scope <path>` | Narrow post-change audit for files or modules | test-tool registry + complexity + rule scanners |
| `chunkyctl handoff` | Generate/update handoff evidence without depending on chat memory | goal/handoff templates + latest reports |

Design constraints:

| Constraint | Reason |
|---|---|
| Keep rules in config/docs/scripts, not hidden prompt memory | Every agent sees the same executable policy |
| Produce JSON first, Markdown second | Controllers and worker agents can consume the same facts |
| Prefer small composable commands over one giant agent | Easier to test, diff, and review |
| Let Codex skills call the tool, not replace it | Skills stay thin; repo truth stays in the repo |
| Keep `docs/chunkyctl_session_quickstart.md` current with startup changes | A new session must not inherit stale docs, commands, gates, or controller/agent rules |

Daily use is intentionally short:

```bash
# 0. Fresh session: tell Codex to follow docs/chunkyctl_session_quickstart.md.

# 1. New session / before asking another agent to work.
scripts/chunkyctl doctor --fast
scripts/chunkyctl worktree --format markdown
scripts/chunkyctl worktree --bucket startup_tooling --format markdown

# 2. Before a concrete task.
scripts/chunkyctl preflight "拆 updater status glue" backend/routers/updater.py backend/tests/test_updater_status.py

# 3. After edits, for the files touched in this slice.
scripts/chunkyctl audit --run backend/scripts/chunkyctl.py backend/tests/scripts/test_chunkyctl.py
```

Use full `doctor` when you need stronger evidence:

```bash
scripts/chunkyctl doctor
```

Interpretation:

| Verdict | Meaning |
|---|---|
| `PASS` | The scoped checks passed; still read `next_actions` for non-blocking work |
| `WARN` | Usable for planning, but not production evidence |
| `FAIL` | Stop and fix/classify the reported gate before claiming readiness |

Full quickstart: `docs/chunkyctl_session_quickstart.md`.

## Upstream PR / Fork Candidates

| Priority | Target | Proposal |
|---:|---|---|
| P0 | CodeGraph | Add native `status --json`, including pending file lists and tracked/untracked classification |
| P0 | complexity-optimizer | Add native baseline/diff mode with `new_high_count` and `resolved_findings` |
| P1 | CodeGraph | Add delete-impact mode: refs, entry points, tests, docs, and confidence level |
| P1 | complexity-optimizer | Add machine-readable JSON output and configurable path/hot-path severity weights |
| P2 | Project-local wrapper | Add ownership buckets for dirty files: current-scope, accepted-architecture, archive-move, generated, deletion-candidate |

## Next Steps

| Order | Action | Gate |
|---:|---|---|
| 1 | Keep validation for `audit_tooling_gate.py` and test-tool registry green | scoped audit + pytest + CodeGraph sync + complexity |
| 2 | Generate an initial backend complexity baseline JSON only after controller chooses the baseline scope | no DB/GCP writes |
| 3 | Use tooling gate output to classify the 180 dirty entries into review/stage/delete buckets | no `git add .` |
| 4 | Reconsider `Worktrunk` only after main is clean enough to safely create isolated future agent worktrees | user approval before install |
