# Strategy snapshot + holdout actual-bound (2026-07-27)

> Lifecycle: evidence-only · Label: **FIXED** (snapshot nominal freeze + actual holdout bound)

## Problem (audit)

1. Institution-follow B0 measured coverage listed **live** `accepted_partition`
   nominal calendars (`20190102–20260724`), not DatasetSnapshot inputs.
2. Holdout guard only checked **declared** `data_end_date`; actual loaded max
   could enter holdout (`holdout_start=20250601`) without failing.

## Fix

| Surface | Change |
|---|---|
| `disclosure_dataset_snapshot.freeze_*` | Freeze `domains.nominal_ohlcv` through `training_cutoff_before_holdout()` (default `require_nominal=True`) |
| `freeze_disclosure_dataset_snapshot.py` | Pass `nominal_conn=tushare_raw` |
| `institution_follow_b0.measure_bare_k_coverage` | Membership **only** from `domains.nominal_ohlcv.date_set` |
| `institution_follow_b0.build_b0_run` | `assert_holdout_untouched(declared, actual_data_end=snapshot_max)` |
| `holdout_guard.assert_holdout_untouched` | Also reject actual ≥ holdout or actual > declared |

## Tests

`pytest` holdout + disclosure freeze + institution_follow_b0: **30 passed**.

## Residual

- Live `data/lineage/disclosure_dataset_snapshot.json` refresh still needs
  three-domain shadow `cutover_allowed` (currently may be blocked). Code path
  is ready; stale freeze without `nominal_ohlcv` → B0 coverage EMPTY /
  insufficient (fail-closed), not silent live expansion.
- Next knives: atomic prereg / single-touch / factor K3–K4; fresh freeze +
  preflight; `goal.md` RX schedule only on owner ask.
