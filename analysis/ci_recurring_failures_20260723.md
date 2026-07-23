# CI recurring failures — 2026-07-23

> **生命周期**：evidence-only（analysis 层；**非** owner bible）

Evidence: `gh run list` last ~150 main runs → 13 failures (~9%).

## Dominant patterns (not flake / not secrets)

1. **`test_ci_pytest_surface_drift` (≥5/13)** — new `test_*.py` committed without
   `ci_pytest_surface.yaml` classification. L1 docs commits skipped local
   `ci_pytest`, so red only appeared on public CI and every later push stayed
   red until yaml fixed (2026-07-21×3 dossier; 2026-07-20×2 main_rally_b2).

2. **`test_calendar_gate` wall-clock false positive (≥2/13)** — AST lint treated
   timestamp `strftime` containing `%Y%m%d` / `%Y-%m-%d` as substring as
   end_date abuse:
   - `tier12_publish_accept`: `%Y-%m-%dT%H:%M:%SZ` (2026-07-20)
   - `db_holders_landing_retention`: `%Y%m%dT%H%M%SZ` multi-line `run_id`
     (2026-07-23; symptomatically uuid-fixed in F4, lint still fragile)

## Systemic fix (this knife)

- calendar_gate: date-only strftime only; AST LHS allowlist for multi-line
  `run_id=` / `ts=` etc.
- safe_commit Step 3.35: always-on surface drift pytest (L1 included).

## Other one-offs (not fixed here)

- board cutover_allowed assertion drift (2026-07-20 pulse/cutover commits)
- alert-flag test isolation: already patched in pipeline/margin tests; no
  recent CI hit in this window
