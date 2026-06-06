# Stage-Opt Reversal Supply Boundary — 2026-06-06

## Decision

The current short-window `fact_technical_trigger × live × reversal` blocker is
first a source freshness / audit-window problem, not proof that reversal
formulas need another threshold-tuning pass or a new production state-history
table.

Keep `readiness.min_signals_per_key=5` for optimizer readiness. For short live
audits, require the audit to report whether the selected source has enough
available signal dates in the window before treating `below_min_signals` as a
formula-density defect.

## Evidence

- `fact_technical_trigger` and the three reversal formulas currently max at
  `2026-06-02`.
- `fact_signal_context`, required by `stage_opt_candidate_supply.yaml` as a
  join dependency, also currently maxes at `2026-06-02`.
- `v_price_kline_qfq` has daily qfq bars through `2026-06-04`.
- `dim_trading_calendar` has trading dates through `2026-06-05` for the checked
  window, but the freshness repair target is the trusted K-line max
  (`2026-06-04`), not the calendar max alone.
- Short audit `2026-06-01..2026-06-05` with
  `--formula reversal_1m_mild reversal_1m_deep reversal_1w` now reports:
  `candidate_supply_freshness`, `source_max_date_before_kline_max`, and
  `source_window_signal_dates_below_min_signals`.
- 2026 YTD reversal-only audit is not zero-supply:
  `raw_signal_rows=306247`, `unique_keys=33946`, `ready_keys=21117`,
  `ready_coverage_pct=62.21`, `signal_kline_coverage_pct=100.0`.
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

1. Refresh `fact_signal_context` through the latest trusted K-line date.
2. Refresh or rebuild `fact_technical_trigger` through the same trusted K-line
   date.
3. Rerun the short live audit and the longer historical audit.
4. If `candidate_supply_freshness` clears but `below_min_signals` remains a
   material blocker, design a no-persist reversal state/source POC before any
   table or writer is introduced.
