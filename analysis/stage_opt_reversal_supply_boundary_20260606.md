# Stage-Opt Reversal Supply Boundary — 2026-06-06

## Decision

The original short-window `fact_technical_trigger × live × reversal` blocker was
first a source freshness / audit-window problem, not proof that reversal
formulas needed another threshold-tuning pass or a new production state-history
table.

That freshness issue is now repaired through the current trusted K-line max
(`2026-06-04`). The remaining short-window blocker is upstream candidate supply
density under `readiness.min_signals_per_key=5`, not stale trigger/context
tables.

Keep `readiness.min_signals_per_key=5` for optimizer readiness. For short live
audits, require the audit to report whether the selected source has enough
available signal dates in the window before treating `below_min_signals` as a
formula-density defect.

## Evidence

- Before repair, `fact_technical_trigger`, the three reversal formulas, and
  `fact_signal_context` maxed at `2026-06-02`, while `v_price_kline_qfq` had
  daily qfq bars through `2026-06-04`.
- Repair executed serially to the trusted K-line max:
  `fact_signal_context` first, then `fact_technical_trigger`, then the
  diagnostic `mart_macd_state_history` source.
- Current table maxima after repair:
  `fact_signal_context=2026-06-04`,
  `fact_technical_trigger=2026-06-04`,
  `mart_macd_state_history=2026-06-04`.
- `dim_trading_calendar` has trading dates through `2026-06-05` for the checked
  window, but the freshness repair target is the trusted K-line max
  (`2026-06-04`), not the calendar max alone.
- Latest actual 5-K-line-day default audit `2026-05-29..2026-06-04` reports:
  `raw_signal_rows=54206`, `ready_keys=3010`, `ready_coverage_pct=12.72`,
  `source_freshness_warnings=0`, and both
  `fact_technical_trigger` / `mart_macd_state_history` freshness `PASS`.
- Latest actual 5-K-line-day reversal-only audit reports:
  `raw_signal_rows=24446`, `unique_keys=9218`, `ready_keys=1401`,
  `ready_coverage_pct=15.2`, `source_freshness_warnings=0`.
- 2026 YTD reversal-only audit after trigger repair is not zero-supply:
  `raw_signal_rows=315825`, `ready_keys=21530`,
  `ready_coverage_pct=62.7`, `signal_kline_coverage_pct=100.0`.
- Verification: scoped `audit_test_tool_health.py` passed; targeted audit tests
  passed (`22 passed`); related stage-opt service/script/CLI tests passed
  (`67 passed`); `scripts/chunkyctl audit --run ...` passed.

## Boundary

| Object | Owner | Current contract |
|---|---|---|
| `fact_technical_trigger` | Event trigger table | One event row per stock/date/formula; not a continuous state table |
| `stage_opt_candidate_supply.yaml` | Readiness/source contract | Owns `min_signals_per_key=5` and allowed source roles |
| `audit_stage_opt_candidate_supply.py` | Gate evidence | Must distinguish K-line coverage, source freshness, window feasibility, and true formula density |
| Reversal formulas | Formula engine + config | Do not tune thresholds again until source freshness is aligned and the audit still proves density failure |
| Reversal state/history table | Not approved | Only consider after a no-write POC proves a state source adds useful PIT candidate supply |

## Next Slice

1. Keep freshness validation on actual available K-line dates, not future
   calendar-only dates.
2. Treat `below_min_signals` as the active stage-opt blocker.
3. Design a no-persist reversal/state-source POC before introducing any new
   table or writer.
4. Only after the POC proves PIT candidate-supply lift should threshold tuning,
   state-history materialization, or optimizer-profile changes be considered.
