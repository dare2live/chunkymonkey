# 300616 Multi-Wave Strategy Design

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


## Scope

- Target: 300616, 创业板 20% 涨跌停, 1060 daily bars from 2022-2026.
- Known waves: W1 Dec2022-Feb2023 +48%, W2 Apr-Jun2023 +37%, W3 Apr-May2026 +60%.
- Existing architecture: `stock_profiler -> formula_engine(55 formulas) -> signal_ranker -> portfolio_pool`.
- Decision timing: signal at T close, buy at T+1 open.
- Goal: detect entries 1-2 trading days before acceleration where possible, without using future bars in live logic.

## Signal Type 1: `consolidation_breakout`

Purpose: catch W1/W3-style first break from a long base, ideally before the limit-up or main acceleration day.

Detection at T close:
- `stock_profiler` state is sideways plus one of: `near_low`, `stage3`, `stage4`.
- Long base exists: 45-160 trading days of compressed range and repeated failure/recovery around MA90/MA145/MA120.
- Close is still near base top, not already extended: close <= recent 60d high * `breakout_pre_ext_max`.
- At least 2 of these formula groups fire within the last `resonance_window` days:
  - `gs_raw_buy`
  - `obv`
  - `ma_base_breakout`
  - `volume_base_breakout`
  - `activity`
- Early trigger variant for buying before the big rally:
  - volume expands versus base median but price is not yet limit-up;
  - close reclaims MA90 or MA145, or closes above base top by a small threshold;
  - OBV slope is positive for `obv_slope_days`;
  - no daily return above `pre_limit_return_cap`, so the signal is not merely chasing the first 20% board.

Entry:
- Buy T+1 open if T+1 open gap <= `max_open_gap_pct`.
- Skip if T close was already limit-up or T+1 open is above T close by more than `max_chase_gap_pct`.
- Initial position can be full strategy weight because this is the primary pre-rally setup.

Exit:
- Time stop: exit after `breakout_max_hold_days` if no continuation confirmation.
- Failure stop: exit if close falls back below base top or MA90/MA145 by `base_fail_buffer`.
- Profit defense: after gain >= `profit_arm_pct`, use ATR trailing stop.
- Hard stop: `initial_stop_atr` below entry or below base support, whichever is tighter.

## Signal Type 2: `continuation`

Purpose: ride W1/W2/W3 main legs after first breakout confirmation.

Detection at T close:
- Prior `consolidation_breakout` or first breakout occurred within `continuation_lookback_days`.
- Trend confirmation: close above MA20 and MA60, and MA20 slope > 0.
- Formula resonance is strong: at least 3 groups among `activity`, `gs_raw_buy`, `obv`, `macd`, `atr` fire within `resonance_window`.
- Price is not overextended beyond `max_extension_from_ma20`.
- Volume remains above `volume_confirm_mult` times 20d median.

Entry:
- Buy or add T+1 open if open gap <= `max_open_gap_pct`.
- If already holding from `consolidation_breakout`, add only when ranker score improves by `add_score_delta` and portfolio risk budget allows.
- Do not add after two consecutive limit-up days or after return from base top exceeds `late_entry_return_cap`.

Exit:
- Exit on close below MA20 for `ma20_break_confirm_days`, or MACD/OBV deterioration plus volume contraction.
- Scale out when gain >= `take_profit_1_pct`; keep residual with ATR trailing.
- Force exit after `continuation_max_hold_days` unless ranker remains top-decile and trend filters still pass.

## Signal Type 3: `pullback_doji`

Purpose: re-enter or add during a controlled pause after breakout, not during failed distribution.

Detection at T close:
- Existing uptrend: close above MA20/MA60 or recent breakout within `pullback_parent_days`.
- Pullback is shallow: drawdown from local high between `pullback_min_pct` and `pullback_max_pct`.
- Doji / small real body: body_pct <= `doji_body_max_pct` and lower shadow >= `lower_shadow_min_ratio`.
- Volume contracts versus breakout volume but OBV does not break down.
- Ranker base signal remains positive; profiler must not flip to downtrend/distribution.

Entry:
- Buy T+1 open only if open is not below doji low by more than `pullback_fail_gap_pct`.
- Prefer half weight for first pullback; add to full only after next close confirms above doji high.
- Skip if pullback occurs after `late_pullback_max_days` from initial breakout or after the move is already above `late_entry_return_cap`.

Exit:
- Stop below doji low minus `doji_stop_buffer_atr`.
- Exit if confirmation does not occur within `pullback_confirm_days`.
- If confirmation succeeds, inherit `continuation` exit logic.

## Layer 2: Combine With `signal_ranker`

- `formula_engine` emits raw formula hits and grouped formula counts at T close.
- New signal module emits one normalized candidate row per stock/date/signal_type:
  - `setup_score`: base quality, stage/profile match, MA/base geometry.
  - `timing_score`: early breakout, resonance recency, volume/OBV/MACD alignment.
  - `risk_score`: gap risk, extension, ATR risk, limit-up chase penalty.
  - `expected_horizon`: short pre-breakout, main continuation, or pullback confirmation.
- `signal_ranker` treats signal type as a feature, not a separate hardcoded strategy.
- Ranking formula should prefer:
  - `consolidation_breakout` when profile is sideways/near_low/stage3/stage4 and signal is before limit-up;
  - `continuation` when 5-formula resonance is active and extension is still controlled;
  - `pullback_doji` when trend is intact and drawdown is shallow.
- Portfolio layer keeps one active thesis per stock:
  - breakout can upgrade into continuation;
  - pullback can add to an existing thesis;
  - conflicting weak signals do not create duplicate positions.

## Optuna Search Parameters

- Base detection:
  - `base_min_days`, `base_max_days`, `base_range_max_pct`, `base_fail_buffer`
  - `ma_reclaim_ma`: MA90 vs MA145 vs MA120
  - `breakout_pre_ext_max`, `pre_limit_return_cap`
- Resonance:
  - `resonance_window`, `min_formula_groups_breakout`, `min_formula_groups_continuation`
  - formula group weights for `gs_raw_buy`, `obv`, `activity`, `macd`, `atr`, `volume_base_breakout`, `ma_base_breakout`
- Volume / OBV:
  - `volume_expand_mult`, `volume_confirm_mult`, `obv_slope_days`
- Entry filters:
  - `max_open_gap_pct`, `max_chase_gap_pct`, `late_entry_return_cap`
- Pullback:
  - `pullback_min_pct`, `pullback_max_pct`, `doji_body_max_pct`
  - `lower_shadow_min_ratio`, `pullback_confirm_days`, `doji_stop_buffer_atr`
- Exits:
  - `initial_stop_atr`, `profit_arm_pct`, `atr_trail_mult`
  - `take_profit_1_pct`, `breakout_max_hold_days`, `continuation_max_hold_days`
- Ranker integration:
  - `setup_score_weight`, `timing_score_weight`, `risk_penalty_weight`
  - per-signal-type rank thresholds and portfolio weight caps.

## Validation Steps

- Build a point-in-time event table for 300616 only: one row per date, with profiler state, formula hits, signal type, ranker features, and next tradable open.
- Confirm all signal features use bars available at or before T close; buy price is strictly T+1 open.
- Replay W1, W2, W3 and record:
  - first eligible signal date;
  - trading-day distance to acceleration or limit-up;
  - entry gap, max adverse excursion, max favorable excursion, realized exit.
- Label success as measured, not estimated:
  - pre-rally hit if entry is 1-2 trading days before acceleration;
  - breakout hit if entry is before the main leg and not after the first limit-up chase;
  - continuation hit if risk-adjusted return beats buy-and-hold from the same T+1 open.
- Run expanding walk-forward Optuna across 2022-2026 with embargo around each wave; do not tune on W3 and report W3 as final holdout if feasible.
- Add negative controls: dates with similar profile/formula hits that did not rally, to measure false positives.
- Validate through `signal_ranker` and `portfolio_pool`, not standalone formula PnL only.
- Report OOS return, Sharpe, max drawdown, hit rate, average holding days, false-positive rate, and skipped gap/chase cases.
- Treat any Sharpe > 5, win rate > 95%, or annualized return > 100% as a leakage warning requiring PIT audit before promotion.
