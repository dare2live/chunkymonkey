# CI failures triage — 2026-07-22

> Status: evidence-only / no code fix
> Authority: live `gh run` on `main` + peer ci-investigator
> Scope: confirm active reds vs historical `ci_pytest_surface` drift

## Verdict

**`main` CI is green now.** No open PRs; no in-progress/queued failures at triage time.
Last code-push success: [29884229425](https://github.com/dare2live/chunkymonkey/actions/runs/29884229425)
(`f0d9389dc` holders probe). Subsequent tip docs commits may skip CI via `paths-ignore`.

| Item | Label |
|---|---|
| Active CI break | **FIXED** (prior commits; nothing to patch) |
| Flaky / infra / token | none found |
| Peer whitelist / notification WIP | left alone |

## Recent distinct reds (already fixed)

| When (UTC) | Runs | Jobs | Cause | Status |
|---|---|---|---|---|
| 2026-07-21 ~14:30–14:40 | [29839458536](https://github.com/dare2live/chunkymonkey/actions/runs/29839458536) … [29840171475](https://github.com/dare2live/chunkymonkey/actions/runs/29840171475) | `test (3.11)`, `test (3.12)` | `test_ci_pytest_surface_drift`: `tests/test_stock_dossier_api.py` unregistered in `backend/config/ci_pytest_surface.yaml` | **Already fixed** — green from later main SHAs; path registered |
| 2026-07-20 ~14:28–14:32 | [29750812425](https://github.com/dare2live/chunkymonkey/actions/runs/29750812425), [29751024496](https://github.com/dare2live/chunkymonkey/actions/runs/29751024496) | same matrix | Same gate: `tests/services/test_main_rally_b2.py` unregistered | **Already fixed** — now in surface yaml |

Root class (both): new offline test file landed without classifying it in
`ci_pytest_surface.yaml` (`blocking_paths` / `nightly_paths` / `ci_test_optional`).
Deterministic, not flake. Ruff `continue-on-error` findings are not blockers.

Peer investigator job cites (dossier cluster):
- 3.11: https://github.com/dare2live/chunkymonkey/actions/runs/29840171475/job/88666745119
- 3.12: https://github.com/dare2live/chunkymonkey/actions/runs/29840171475/job/88666745289

## Watch

When adding **new** `backend/tests/**/*.py` files (incl. peer whitelist WIP), register
them in `backend/config/ci_pytest_surface.yaml` in the same commit — or the
surface-drift gate will red again.

## goal pointer (optional)

Gate pytest layering already noted in `goal.md` (`ci_pytest_surface.yaml` /
`run_ci_pytest.py --tier blocking`). This note is the 2026-07-22 incident
evidence only; peer WIP on `goal.md` left untouched.
