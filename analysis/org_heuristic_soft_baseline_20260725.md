# Org truncation heuristic: hard vs soft baseline (2026-07-25)

> 状态：evidence-only
> Authority: live canary + pagination_integrity hard/soft split.

## Canary (truth source)

`org_holding_period_repair_truncated.py --period 2019-03-31`:

| field | value |
|---|---|
| before/after rows | 54895 / 54895 |
| before/after stocks | 3607 / 3607 |
| provider_count | 54895 |
| fetched_rows | 54895 |
| truncated (pagination) | false |
| shard_count | 1 |

Log: `analysis/org_canary_q1_20190331_20260725.log`

Re-fetch adds **zero** rows when provider already returned a complete land.
The prior 「19 truncated」 queue was almost entirely
`landed_stocks < 0.95 × modern_max_accepted_stocks` (baseline=5562) — older thinner
universes / Q1–Q3 density, **not** East Money 100-page cap.

## Fix

- Hard repair trigger = page-cap land and/or `provider_count` shortfall only.
- Soft observe = `under_modern_baseline` (kept in population reasons; **not**
  `provider_truncated`, **not** repair queue).

## Live after fix

| metric | value |
|---|---|
| `list_truncated_org_periods` (hard) | **0** |
| soft under_modern_baseline | 19 (observe only) |
| tests | pagination + truncation audit + org/pipeline 37 passed |

**Verdict**: page-cap repair track **FIXED**; residual 19 = soft observe, do not mass re-pull.
