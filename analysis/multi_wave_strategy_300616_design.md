# Multi-Wave Strategy Design for 300616

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


Objective: catch the full 300616 rally structure across three historical waves by generating close-of-day signals that can be bought at the next trading day's open. This is a design document only; no backend files are modified.

Important timing rule: every signal below is evaluated after market close on day T. The earliest executable entry is T+1 open. Any condition that requires T+1 close/high/low is a confirmation or exit rule, not an entry filter.

Observed wave anchors supplied by the user:

| Wave | Rally | Key observed facts | Design implication |
|---|---:|---|---|
| Wave 1 | 2022-12-29 low 19.58 to 2023-02-14 high 29.04, +48.3% | 2022-12-30 +13.6%, 2023-01-04 +15.3% with 4.5x volume; 6-day shallow pullback; 2023-01-10/11/12 dojis; 2023-01-13 +5.3%; doji succeeded | Need initial anticipation before 12-30 or 01-04, then doji-bounce add/hold. |
| Wave 2 | Apr-Jun 2023, +36.6% | 2023-05-08 +5.8% warmup; 2023-05-10 +16.9% with 4.3x volume; 8-day pullback; 2023-05-18 perfect doji body=0.02; 2023-05-19 -1.9%, then decline to 22.35; doji failed | Main breakout can be caught, but doji must be rejected or fail-fast exited. |
| Wave 3 | 2026-04-20 low 12.02 to May 2026, +60.5% | 2026-04-22 +11.6% with 7.5x volume; 2026-04-24 and 04-28 dojis; 2026-05-11 +20.0%; 2026-05-18 doji body=0.02; 2026-05-19 +20.0%; dojis succeeded | Need high-volume ignition override, continuation setup, and regime-aware doji filter. |

Local qfq inspection of 300616 confirms the existing pullback formula features are useful leads, not final proof:

- Wave 1 dojis after the 2023-01-04 breakout retained roughly 0.83-0.92 of the breakout gain and had close-based pullback depth around -2.9%.
- Wave 2 2023-05-18 doji retained roughly 0.31 of the 2023-05-10 breakout gain and had close-based pullback depth around -10%.
- Wave 3 2026-04-24/04-28 dojis retained more than the full 2026-04-22 breakout gain.
- Wave 3 2026-05-18 looks weak if measured only against the 2026-05-11 limit-up breakout, so the filter needs a broader rally-regime override based on recent 20% boards and holding above earlier wave support.

The local qfq calculations above are used only to shape hypotheses and parameter ranges. They are not performance results.

## Section 1: Signal Taxonomy

The strategy is a coordinated three-signal state machine:

1. `breakout_anticipation`: enter before the first meaningful consolidation breakout.
2. `pullback_doji_bounce`: reuse and extend the existing limit-up pullback doji formula.
3. `trend_continuation`: add or hold before the next surge inside an already active rally.

### 1.1 breakout_anticipation

Purpose: detect 1-2 trading days before the first major move or main breakout.

Observed prototypes:

- Wave 1: 2022-12-29 printed the wave low at 19.58 immediately before 2022-12-30 +13.6%. A second chance existed after the 12-30 first breakout: 2023-01-03 close allowed T+1 open entry before the 2023-01-04 +15.3% 4.5x-volume main breakout.
- Wave 2: 2023-05-08 +5.8% warmup and 2023-05-09 pause preceded the 2023-05-10 +16.9% 4.3x-volume main breakout.
- Wave 3: 2026-04-20 low at 12.02 and 2026-04-21 recovery preceded 2026-04-22 +11.6% with 7.5x volume.

Detection logic:

- `low_turn_setup`: price is near a recent low, then stops falling.
  - `low <= rolling_low(pre_pattern.low_lookback) * (1 + pre_pattern.low_pct)`.
  - No prior large breakout in the current setup window.
  - Close is flat/up versus the recent low day or has a 1-2 day recovery.
  - Hypothesis: needs backtest validation. The supplied facts prove the lows before Wave 1 and Wave 3, but not a universal predictive candle pattern.
- `warmup_pause_setup`: a moderate warmup bar appears before the main breakout.
  - Prior 1-2 trading days include a +3% to +8% close-to-close gain, grounded by Wave 2's +5.8% warmup.
  - Current day does not already trigger the main breakout threshold.
  - Current close stays above the warmup midpoint or prior close support.
  - Hypothesis: needs backtest validation. This is directly observed for Wave 2 main breakout and partially observed in Wave 3's 04-20/04-21 recovery, but not proven across a broader universe.
- `volume_accumulation`: optional confirmation from existing volume bank formulas.
  - Existing available functions: `obv_breakout`, `ad_line_uptrend`, `vpt_divergence_bullish`, `vwap_cross_up`, `chaikin_money_flow`.
  - For 300616, Wave 2 had visible warmup volume before the main breakout; Wave 3 had extreme breakout volume on 04-22, so pre-breakout volume conditions should be optional, not mandatory.
- `pattern_base`: optional confirmation from existing pattern formulas.
  - Existing available functions: `double_bottom_w`, `rounded_bottom`, `box_breakout`, `ascending_triangle`.
  - Hypothesis: needs backtest validation. These formulas can describe consolidation, but the supplied wave facts do not prove a specific W/cup/triangle pattern.

Existing code support:

- `backend/config/formula_limit_up_pullback.yaml` already has `pre_pattern.low_lookback`, `pre_pattern.low_pct`, `pre_pattern.recovery_min_days`, `pre_pattern.recovery_max_days`, and `pre_pattern.recovery_max_pct`.
- `backend/scripts/formula_limit_up_pullback.py::_check_pre_pattern` implements a low/recovery pre-pattern, but only as a filter after a breakout has already been found. A true anticipation signal needs this logic evaluated directly on T.
- `backend/services/bc_absorbed/bank/pattern.py` and `volume.py` already expose reusable pattern/volume primitives.

### 1.2 pullback_doji_bounce

Purpose: enter after a valid doji during a pullback from a large-volume breakout.

Base implementation:

- Use `backend/scripts/formula_limit_up_pullback.py::detect_signals` as the base.
- Use `backend/services/bc_absorbed/formula_engine.py::pullback_doji_signals` as the formula-engine integration point.
- Keep the YAML structure in `backend/config/formula_limit_up_pullback.yaml` as the parameter owner.

Current base logic already covers:

- Board-adjusted breakout threshold: `breakout.limit_ratio * limit_up_pct`, where 创业板 uses 20%.
- Breakout volume: `breakout.vol_ratio`.
- Pullback window: `pullback.min_days` / `pullback.max_days`.
- Pullback volume shrink: `pullback.vol_shrink`.
- Support check: `pullback.above_breakout_low`.
- Doji shape: `doji.body_ratio_max` and `doji.range_min`.
- T+1 entry: `entry.buy_offset`.
- Useful emitted features in the script: `gain_retained`, `pb_depth_pct`, `vol_mono_down`, `doji_vol_ratio`, `breakout_vol_x`, `gap_days`.

Required extension:

- The doji signal must not treat every perfect doji as bullish. Wave 2 had the cleanest doji body but failed.
- Add a quality gate based on `gain_retained` and `pb_depth_pct`, with a regime override for Wave 3-style 20% board stacks.

Observed pass/fail grounding:

- Wave 1 dojis succeeded after strong gain retention and shallow pullback.
- Wave 2 doji failed despite body=0.02 because the pullback was longer/deeper and the breakout gain was poorly retained.
- Wave 3 dojis succeeded when the rally had high-volume ignition and later 20% limit-up events.

### 1.3 trend_continuation

Purpose: enter before the next surge after the rally is already active.

Observed prototypes:

- Wave 1: after the 2023-01-10/11/12 doji cluster, 2023-01-13 rallied +5.3% and the wave continued to 29.04.
- Wave 3: after 2026-05-06 to 2026-05-08 recovery, 2026-05-11 printed +20.0%; after 2026-05-18 doji, 2026-05-19 printed +20.0%.
- Wave 2: after the 2023-05-18 doji, 2023-05-19 fell -1.9% and the decline continued, so continuation must require support retention and/or limit-up stack strength.

Detection logic:

- `active_rally_state` must be true:
  - A recent ignition breakout occurred inside `trend_continuation.ignition_lookback_days`.
  - The close remains above a rally anchor support, such as the first breakout close, prior successful doji close, or short moving average support.
- `flag_recovery_setup`:
  - Pullback from recent high is controlled, then close recovers for 1-3 days.
  - Current close is below the old high but above short support.
  - Hypothesis: needs backtest validation. This is consistent with Wave 3 before 2026-05-11, but the supplied data does not prove a general flag rule.
- `limit_stack_setup`:
  - At least one recent 20% limit-up or near-limit-up event exists, or a high-volume ignition exists.
  - Wave 3 directly grounds this rule: 2026-05-11 +20.0% preceded the 2026-05-18 doji and 2026-05-19 +20.0%.
- Existing available functions:
  - `pattern.py`: `bull_flag_continuation`, `box_breakout`, `ascending_triangle`.
  - `technical.py`: `atr_breakout`, `macd_golden_cross_above_zero`, `macd_zero_axis_bullish`, `bollinger_squeeze_breakout`.
  - `volume.py`: `obv_breakout`, `volume_spike`, `vwap_cross_up`.
  - `formula_engine.py`: `activity_breakout` and `volume_base_breakout` can provide breakout/volume context, but `trend_continuation` needs a pre-surge variant rather than only breakout-day detection.

## Section 2: Entry Rules With Concrete Conditions

### 2.1 Common Entry Constraints

- Evaluate all signals using only OHLCV and PIT-safe auxiliary data available at T close.
- Buy at T+1 open.
- Skip entry if T+1 open is effectively unbuyable because it opens at/near the 20% limit-up price.
- Do not add duplicate entries from multiple signals on the same T+1 open.
- Use a rally `episode_id` so breakout, doji, and continuation signals coordinate instead of fighting each other.

Suggested tranche behavior, to be optimized:

- `breakout_anticipation`: open initial risk unit.
- `pullback_doji_bounce`: add only if the rally episode remains valid.
- `trend_continuation`: add or hold; do not add if position is already at max episode exposure.

These tranche sizes are risk-control design, not measured performance claims.

### 2.2 breakout_anticipation Entry

Generate `breakout_anticipation` at T close when either setup is true.

Low-turn setup:

```text
low_near_recent_low =
  low[T] <= rolling_low(low, pre_pattern.low_lookback)[T] * (1 + pre_pattern.low_pct)

recovery_not_extended =
  cumulative_return_from_recent_low <= pre_pattern.recovery_max_pct

not_already_breakout =
  pct_change[T] < breakout.limit_ratio * limit_up_pct * 100

signal =
  low_near_recent_low
  AND recovery_not_extended
  AND not_already_breakout
  AND optional(pattern_base OR volume_accumulation)
```

Grounding:

- Wave 1 had a low at 19.58 on 2022-12-29 immediately before the 2022-12-30 +13.6% breakout.
- Wave 3 had a low at 12.02 on 2026-04-20 before the 2026-04-22 +11.6% high-volume breakout.

Warmup-pause setup:

```text
warmup_seen =
  max(pct_change[T-2], pct_change[T-1], pct_change[T]) between warmup_ret_min and warmup_ret_max

pause_or_hold =
  pct_change[T] <= pause_ret_max
  AND close[T] >= warmup_support

signal =
  warmup_seen
  AND pause_or_hold
  AND not_already_breakout
```

Grounding:

- Wave 2 main breakout was preceded by 2023-05-08 +5.8% warmup and 2023-05-10 +16.9% main breakout.
- A signal after 2023-05-08 close or 2023-05-09 close would buy before the 2023-05-10 main move.

Hypothesis: exact T-1/T-2 candle shape before breakouts needs backtest validation. The observed data supports low-turn and warmup-pause prototypes, not a final universal rule.

### 2.3 pullback_doji_bounce Entry

Generate `pullback_doji_bounce` at T close when all are true:

```text
breakout =
  pct_change[breakout_day] >= breakout.limit_ratio * limit_up_pct * 100
  OR (
    pct_change[breakout_day] >= breakout.high_volume_override_pct
    AND breakout_vol_x >= breakout.high_volume_override_vol_x
  )

pullback =
  pullback.min_days <= trading_days_since_breakout <= pullback.max_days
  AND pullback_mean_volume <= pullback.vol_shrink * breakout_volume
  AND low_since_breakout >= breakout_low * (1 - support_buffer)

doji =
  abs(close[T] - open[T]) / max(high[T] - low[T], epsilon) <= doji.body_ratio_max
  AND (high[T] - low[T]) / close[T] >= doji.range_min
  AND optional(abs(close[T] - open[T]) / close[T] <= doji.body_abs_pct_max)

quality =
  wave2_failure_filter_passes

signal =
  breakout AND pullback AND doji AND quality
```

Grounding:

- Wave 1: +15.3% breakout with 4.5x volume, shallow pullback, doji cluster, then +5.3% bounce and continuation.
- Wave 2: +16.9% breakout with 4.3x volume and perfect doji, but the doji failed; therefore doji shape alone is insufficient.
- Wave 3: +11.6% first breakout had 7.5x volume, so the threshold must allow high-volume non-14% ignitions on 20% boards.

### 2.4 trend_continuation Entry

Generate `trend_continuation` at T close when the rally episode is active and one of these setups is true:

High-tight flag recovery:

```text
active_rally =
  recent_ignition_breakout within ignition_lookback_days
  AND close[T] >= rally_anchor_support * (1 - support_buffer)

controlled_pullback =
  pullback_from_recent_high between min_pullback_pct and max_pullback_pct
  AND close[T] >= short_ma[T] * short_ma_buffer

recovery =
  close has recovered for recovery_min_days to recovery_max_days
  AND current close remains below prior high by at least prebreakout_room_pct

signal =
  active_rally AND controlled_pullback AND recovery
```

Limit-stack continuation:

```text
signal =
  active_rally
  AND recent_limit_up_count >= min_recent_limit_ups
  AND valid_doji_or_recovery_bar
  AND close[T] >= prior_successful_doji_close * (1 - support_buffer)
```

Grounding:

- Wave 3's 2026-05-11 +20% and 2026-05-19 +20% moves make recent 20% board count a concrete continuation feature.
- Wave 2 lacked a 20% board stack and failed after the doji.

## Section 3: Exit Rules

The strategy should not use fixed 3/5/10-day exits as production exits. Fixed holding periods are useful for research reports, but the requirement is to stay through +48.3%, +36.6%, and +60.5% rally episodes when the trend remains valid.

### 3.1 Initial Breakout Entry Exit

For `breakout_anticipation` entries:

- Initial hard stop:
  - Stop below the setup low with a small buffer, or use an optimized percentage stop.
  - Candidate formula: `stop = min(setup_low * (1 - setup_low_buffer), entry_price * (1 + initial_stop_pct))`, where `initial_stop_pct` is negative.
- Confirmation window:
  - If no qualifying breakout occurs within `breakout_confirm_window` trading days after entry, exit or reduce.
  - Qualifying breakout means a +10% to +17% move on 创业板, or a lower +11% move with extreme volume like Wave 3's 7.5x event.
- Partial profit handling:
  - Do not fully exit just because the first target is hit.
  - Optional: reduce a small risk tranche at `initial_target_pct`, then keep a core position governed by full-rally trailing.

Grounding:

- Wave 1 first breakout happened immediately after the 2022-12-29 low.
- Wave 2's warmup-to-main-breakout sequence had only a short delay.
- Wave 3's low-to-breakout sequence was also short.

### 3.2 Doji Bounce Entry Exit

For `pullback_doji_bounce` entries:

- Initial hard stop:
  - Intraday fail-fast: exit if price breaks below `min(doji_low, breakout_low_or_anchor_support) * (1 - doji_stop_buffer)`.
  - Close-based fail-fast: exit if T+1 close is below doji low/support and no rally confirmation occurs.
- Confirmation:
  - Existing config has `verify.rally_pct` and `verify.rally_window`.
  - Keep this as a post-entry evidence tag, not a pre-entry filter.
- After confirmation:
  - Switch to the full-rally trailing rule.

Grounding:

- Wave 2 would have been protected by a fail-fast rule because 2023-05-19 fell after the 2023-05-18 doji and the decline continued.
- Wave 1 and Wave 3 doji bounces should not be capped by a small fixed target if the goal is full-wave capture.

### 3.3 Trend Continuation Entry Exit

For `trend_continuation` entries:

- Hard stop:
  - Stop below the continuation flag low, prior doji low, or rally anchor support.
- If continuation does not trigger:
  - Exit if no breakout/recovery confirmation occurs within `continuation_confirm_window`.
- If continuation triggers:
  - Merge into the full-rally trailing position.

Grounding:

- Wave 3 continuation entries need to survive normal pullbacks but exit if the 20% board stack fails and price loses support.

### 3.4 Full-Rally Trailing Exit

Core position exit should be state-based:

```text
full_rally_active =
  close >= rally_anchor_support * (1 - anchor_buffer)
  AND NOT support_break
  AND NOT trend_trailing_break

support_break =
  close < last_valid_pullback_low * (1 - support_buffer)
  OR close < first_breakout_close * (1 - anchor_buffer)

trend_trailing_break =
  close below selected_ma for ma_exit_confirm_days
  OR close < highest_close_since_entry * (1 - trailing_pct)
```

Recommended behavior:

- Use a looser trailing stop after a recent 20% limit-up event to avoid exiting Wave 3 too early.
- Tighten trailing after no new high appears for `stale_high_days`.
- Exit all remaining core if both support and MA/trailing fail.

Hypothesis: final high exhaustion rules, including the 2023-02-14 high, need backtest validation. The supplied data identifies the high but not a complete reversal pattern at that high.

## Section 4: Wave 2 Failure Filter Logic

The filter's job is to reject the 2023-05-18 Wave 2 doji while preserving Wave 1 and Wave 3 successful dojis.

### 4.1 Required Feature Definitions

Use the existing script features as the first layer:

- `gain_retained = (doji_close - breakout_prev_close) / (breakout_close - breakout_prev_close)`.
- `pb_depth_pct = (min_pullback_close / breakout_close - 1) * 100`.
- `gap_days = trading days from breakout to doji`.
- `breakout_vol_x = breakout volume / MA20 volume`.
- `doji_vol_ratio = doji volume / breakout volume`.
- `vol_mono_down = fraction of pullback days with volume declining`.

Add broader rally-regime features:

- `recent_limit_up_count`: count of near-20% boards in the last N trading days.
- `primary_anchor_close`: close of the first valid ignition breakout in the episode.
- `prior_successful_doji_close`: most recent doji that passed and did not fail-fast.
- `anchor_gain_retained`: close[T] versus the primary wave anchor, not only the immediate breakout.
- `support_retained`: close[T] above the chosen anchor support with buffer.

### 4.2 Concrete Filter

Doji quality passes if either local quality or regime override passes.

Local quality:

```text
local_quality =
  gain_retained >= failure_filter.min_gain_retained
  AND pb_depth_pct >= failure_filter.min_pb_depth_pct
  AND gap_days <= failure_filter.max_quality_gap_days
```

Initial candidate defaults:

- `min_gain_retained`: 0.55 to 0.75 search range.
- `min_pb_depth_pct`: -7% to -4% search range.
- `max_quality_gap_days`: 5 to 7 search range.

Regime override:

```text
regime_override =
  recent_limit_up_count >= failure_filter.override_min_recent_limit_ups
  AND support_retained
  AND (
    breakout_vol_x >= failure_filter.override_min_breakout_vol_x
    OR anchor_gain_retained >= failure_filter.override_min_anchor_gain_retained
  )
```

Initial candidate defaults:

- `override_min_recent_limit_ups`: 1.
- `override_min_breakout_vol_x`: 2.5 to 6.0 search range.
- `override_min_anchor_gain_retained`: 1.0.

Hard reject:

```text
reject =
  gain_retained < failure_filter.hard_min_gain_retained
  AND pb_depth_pct < failure_filter.hard_min_pb_depth_pct
  AND recent_limit_up_count == 0
```

Initial candidate defaults:

- `hard_min_gain_retained`: 0.45.
- `hard_min_pb_depth_pct`: -7%.

Grounding:

- Wave 1 doji: gain retained was high and pullback was shallow, so local quality passes.
- Wave 2 doji: gain retained was low, pullback was deep, pullback was longer, and there was no 20% board stack; hard reject should catch it.
- Wave 3 04-24/04-28 dojis: local quality passes because the first breakout gain was more than retained.
- Wave 3 05-18 doji: local quality may fail if measured only against the 2026-05-11 +20% breakout, so it needs the regime override because a recent 20% board existed and price still held above earlier rally supports.

### 4.3 Fail-Fast Backstop

Even after the filter, use a T+1 fail-fast:

```text
fail_fast_exit =
  low[T+1] < doji_low * (1 - doji_stop_buffer)
  OR close[T+1] < anchor_support * (1 - support_buffer)
```

This is not a substitute for the Wave 2 filter. It is a second line of defense if optimization keeps a marginal doji.

## Section 5: Optuna Parameter Search Space

The search space below preserves the existing `formula_limit_up_pullback.yaml` structure and adds explicit sections for the new multi-wave coordination. Ranges are hypotheses for walk-forward search, not chosen production values.

```yaml
formula_limit_up_multi_wave:
  inherits: backend/config/formula_limit_up_pullback.yaml

  breakout:
    # Existing keys from formula_limit_up_pullback.yaml
    limit_ratio:
      type: float
      low: 0.45
      high: 0.85
      reason: "20% board threshold; Wave 3 +11.6% requires <=0.58 unless high-volume override applies."
    pct_min:
      type: float
      low: 7.0
      high: 17.0
      reason: "Fallback absolute threshold; keep compatible with existing config."
    vol_ratio:
      type: float
      low: 1.2
      high: 8.0
      reason: "Observed main breakouts were 4.3x, 4.5x, and 7.5x."
    close_eq_high:
      type: categorical
      choices: [false, true]
    high_volume_override_pct:
      type: float
      low: 9.0
      high: 13.0
      reason: "Allow Wave 3-style +11.6% ignition with extreme volume."
    high_volume_override_vol_x:
      type: float
      low: 4.0
      high: 8.0

  pre_pattern:
    # Existing keys from formula_limit_up_pullback.yaml, reused for anticipation.
    enabled:
      type: categorical
      choices: [true, false]
    low_lookback:
      type: int
      low: 10
      high: 40
    low_pct:
      type: float
      low: 0.01
      high: 0.08
    recovery_min_days:
      type: int
      low: 0
      high: 2
    recovery_max_days:
      type: int
      low: 1
      high: 5
    recovery_max_pct:
      type: float
      low: 0.02
      high: 0.08

  breakout_anticipation:
    warmup_ret_min:
      type: float
      low: 0.03
      high: 0.06
    warmup_ret_max:
      type: float
      low: 0.06
      high: 0.10
    warmup_lookback_days:
      type: int
      low: 1
      high: 3
    pause_ret_max:
      type: float
      low: 0.01
      high: 0.04
    max_gap_entry_pct:
      type: float
      low: 0.03
      high: 0.15
    require_volume_confirmation:
      type: categorical
      choices: [false, true]
    volume_confirmation_formula:
      type: categorical
      choices: [obv_breakout, ad_line_uptrend, vpt_divergence_bullish, vwap_cross_up, none]
    pattern_confirmation_formula:
      type: categorical
      choices: [double_bottom_w, rounded_bottom, box_breakout, ascending_triangle, none]

  pullback:
    # Existing keys from formula_limit_up_pullback.yaml
    min_days:
      type: int
      low: 2
      high: 4
    max_days:
      type: int
      low: 5
      high: 9
      reason: "Wave 1/2 descriptions include 6-8 day pullbacks; current default 5 can be too narrow."
    vol_shrink:
      type: float
      low: 0.25
      high: 0.80
    above_breakout_low:
      type: categorical
      choices: [true]
    support_buffer:
      type: float
      low: 0.003
      high: 0.02

  doji:
    # Existing keys plus optional absolute body guard.
    body_ratio_max:
      type: float
      low: 0.05
      high: 0.30
    range_min:
      type: float
      low: 0.002
      high: 0.015
    body_abs_pct_max:
      type: float
      low: 0.001
      high: 0.010

  entry:
    # Existing key from formula_limit_up_pullback.yaml
    buy_offset:
      type: categorical
      choices: [1]
      reason: "Signal after close; T+1 open is earliest valid buy."
    max_episode_exposure_units:
      type: int
      low: 1
      high: 3

  verify:
    # Existing keys from formula_limit_up_pullback.yaml
    enabled:
      type: categorical
      choices: [true, false]
    rally_pct:
      type: float
      low: 0.03
      high: 0.08
    rally_window:
      type: int
      low: 1
      high: 3

  failure_filter:
    min_gain_retained:
      type: float
      low: 0.45
      high: 0.85
    min_pb_depth_pct:
      type: float
      low: -10.0
      high: -3.0
    max_quality_gap_days:
      type: int
      low: 5
      high: 8
    hard_min_gain_retained:
      type: float
      low: 0.25
      high: 0.55
    hard_min_pb_depth_pct:
      type: float
      low: -12.0
      high: -6.0
    override_min_recent_limit_ups:
      type: int
      low: 1
      high: 2
    override_lookback_days:
      type: int
      low: 5
      high: 20
    override_min_breakout_vol_x:
      type: float
      low: 2.5
      high: 6.0
    override_min_anchor_gain_retained:
      type: float
      low: 0.8
      high: 1.3

  trend_continuation:
    ignition_lookback_days:
      type: int
      low: 5
      high: 25
    min_recent_limit_ups:
      type: int
      low: 0
      high: 2
    min_pullback_pct:
      type: float
      low: 0.02
      high: 0.08
    max_pullback_pct:
      type: float
      low: 0.10
      high: 0.22
    recovery_min_days:
      type: int
      low: 1
      high: 3
    recovery_max_days:
      type: int
      low: 2
      high: 6
    short_ma_window:
      type: categorical
      choices: [5, 10, 13]
    short_ma_buffer:
      type: float
      low: 0.96
      high: 1.02
    prebreakout_room_pct:
      type: float
      low: 0.00
      high: 0.08

  exit:
    initial_stop_pct:
      type: float
      low: -0.12
      high: -0.04
    initial_target_pct:
      type: float
      low: 0.08
      high: 0.25
    breakout_confirm_window:
      type: int
      low: 1
      high: 3
    doji_stop_buffer:
      type: float
      low: 0.003
      high: 0.02
    continuation_confirm_window:
      type: int
      low: 1
      high: 4
    trailing_pct:
      type: float
      low: 0.06
      high: 0.18
    loose_trailing_pct_after_limit_up:
      type: float
      low: 0.10
      high: 0.25
    ma_exit_window:
      type: categorical
      choices: [5, 10, 20]
    ma_exit_confirm_days:
      type: int
      low: 1
      high: 3
    stale_high_days:
      type: int
      low: 5
      high: 20

  aux_filters:
    # Existing structure from formula_limit_up_pullback.yaml.
    price_position:
      enabled:
        type: categorical
        choices: [false, true]
      min_60d_pct:
        type: float
        low: 0.2
        high: 0.9
    turnover:
      enabled:
        type: categorical
        choices: [false, true]
      min_ratio:
        type: float
        low: 0.8
        high: 4.0
      max_ratio:
        type: float
        low: 3.0
        high: 10.0
    sector_momentum:
      enabled:
        type: categorical
        choices: [false, true]
      min_5d_ret:
        type: float
        low: -0.05
        high: 0.08
    market_cap:
      enabled:
        type: categorical
        choices: [false, true]
      max_decile:
        type: int
        low: 3
        high: 10
    lhb:
      enabled:
        type: categorical
        choices: [false, true]
      max_30d_count:
        type: int
        low: 0
        high: 3

  backtest:
    # Existing structure from formula_limit_up_pullback.yaml.
    hold_days:
      type: categorical
      choices:
        - [1, 2, 3, 5, 10, 15, 20]
    tx_cost_bps:
      type: fixed
      value: 15
    start_date:
      type: fixed
      value: "2022-01-01"
```

Optimization objective:

- Primary: OOS full-wave capture with controlled drawdown, not in-sample Sharpe.
- Include explicit penalties for:
  - missing Wave 1 initial or doji continuation;
  - accepting Wave 2 2023-05-18 failed doji without fail-fast exit;
  - missing Wave 3 2026-04-22 high-volume ignition because the 20% board threshold is too high;
  - exiting before a valid active-rally trailing break.
- Report unmeasured KPIs as `unknown`; do not invent forward returns or Sharpe.

Governance:

- Use time-aware walk-forward.
- Use fixed seed and 50-500 trials per existing Optuna governance.
- Keep T+1 execution and limit-up buyability checks in the objective.
- Treat suspiciously high metrics as leakage warnings.

## Section 6: Validation Plan

### 6.1 300616 Walk-Forward / Case-Study Validation

300616 has only three known waves, so it cannot by itself prove a deployable strategy. It is still useful as a case-study acceptance test.

Build a dated event ledger:

- Wave 1:
  - Expected anticipation: signal by 2022-12-29 close for 2022-12-30 open, or signal by 2023-01-03 close for 2023-01-04 open.
  - Expected doji handling: accept 2023-01-10/11/12 doji cluster and stay in for the continued rally unless trailing support breaks.
- Wave 2:
  - Expected anticipation: accept 2023-05-08/09 setup before 2023-05-10 main breakout.
  - Expected doji handling: reject 2023-05-18 doji by `gain_retained`/`pb_depth_pct`, or if accepted during optimization, fail-fast exit on 2023-05-19 support break.
- Wave 3:
  - Expected anticipation: detect the 2026-04-20/21 low-turn setup before 2026-04-22.
  - Expected doji handling: accept 2026-04-24/04-28 dojis.
  - Expected continuation: detect the setup before 2026-05-11 and accept the 2026-05-18 doji before 2026-05-19.

Evaluation metrics:

- `entry_lead_days`: trading days between buy date and major move date.
- `missed_wave_count`: waves where no valid entry happened before the major move.
- `failed_doji_accepted`: whether the 2023-05-18 doji was bought and not fail-fast exited.
- `full_wave_capture_fraction`: measured from actual trade rows only.
- `max_adverse_excursion`: measured per entry after T+1 open.
- `premature_exit_before_wave_high`: yes/no and date.

No promotion decision should be made from these three waves alone.

### 6.2 Cross-Validation on Similar 创业板 20% Limit Stocks

Universe:

- Clean active historical universe, PIT-safe.
- 创业板 / 20% limit-up stocks, using `services.universe.get_limit_up_pct`.
- Exclude ST, delisted-ineligible, 北交所, and unbuyable limit-up opens according to the existing preflight rules.

Event mining:

- Positive episodes:
  - 20%-70% rally over 10-60 trading days.
  - At least one +10% to +20% breakout with volume above 1.5x MA20.
  - Include episodes with pullback dojis after breakout.
- Negative episodes:
  - Breakout followed by doji where no bounce occurs within the verification window.
  - Pullbacks where `gain_retained` is low and `pb_depth_pct` is deep.
  - This class is required so the Wave 2 filter does not overfit to one stock/date.

Splits:

- Time split: train early periods, validate later periods, final test on the latest unseen period.
- Group split: hold out whole stocks or whole episodes to avoid tuning to one stock's microstructure.
- Leave-300616-2026-out final check if 300616 Wave 3 is used as the headline example.

Comparisons:

- Existing `pullback_doji` default from `formula_limit_up_pullback.yaml`.
- Doji strategy with no Wave 2 failure filter.
- Breakout anticipation only.
- Full coordinated multi-wave strategy.

Required evidence:

- OOS trade list with signal date, buy date, buy open, sell date, exit reason, net return, max adverse excursion.
- Per-signal confusion table: accepted successful dojis, rejected failed dojis, rejected successful dojis.
- Cost-aware results with current transaction cost model.
- PIT/preflight pass before trusting any KPI.

## Section 7: Implementation Roadmap

No implementation is performed in this task. Future implementation should be scoped as follows.

1. Config
   - Add `backend/config/formula_limit_up_multi_wave.yaml`, or extend `formula_limit_up_pullback.yaml` with `breakout_anticipation`, `failure_filter`, `trend_continuation`, and `exit` sections.
   - Preserve existing keys so current `pullback_doji` behavior remains reproducible.

2. Pullback doji feature exposure
   - Extend `backend/scripts/formula_limit_up_pullback.py::detect_signals` or wrap it so `gain_retained`, `pb_depth_pct`, `gap_days`, `doji_vol_ratio`, and `breakout_vol_x` are available to the formula engine.
   - Add filter parameters for `min_gain_retained`, `min_pb_depth_pct`, and regime override.
   - Keep T+1 `buy_offset` behavior.

3. Formula engine
   - In `backend/services/bc_absorbed/formula_engine.py`, add separate formula definitions:
     - `breakout_anticipation`
     - `pullback_doji_bounce`
     - `trend_continuation`
     - optional coordinator `multi_wave_300616`
   - Do not replace the existing `pullback_doji` formula until regression tests prove compatibility.

4. Bank functions
   - Reuse existing `technical.py` formulas for MACD, Bollinger, KDJ/RSI, and ATR context.
   - Reuse existing `pattern.py` formulas for `bull_flag_continuation`, `box_breakout`, `ascending_triangle`, `double_bottom_w`, and `rounded_bottom`.
   - Reuse existing `volume.py` formulas for `obv_breakout`, `volume_spike`, `vwap_cross_up`, `ad_line_uptrend`, and `vpt_divergence_bullish`.
   - Add only the missing pre-surge variants if backtest shows current formulas fire too late:
     - `low_turn_breakout_anticipation`
     - `warmup_pause_breakout_anticipation`
     - `high_tight_flag_prebreakout`

5. Backtest and Optuna
   - Add a dedicated script such as `backend/scripts/optuna_multi_wave_300616.py` or extend `backend/scripts/optuna_pullback_doji.py`.
   - Use the existing clean universe, board limit adaptation, transaction costs, and preflight checks.
   - Add a case-study assertion file for the three 300616 waves before broader cross-validation.

6. Tests
   - Unit-test T+1 timing: no entry condition may read T+1 data.
   - Unit-test Wave 2 doji filter with a fixture around 2023-05-18.
   - Unit-test high-volume override so 2026-04-22-style +11.6% / 7.5x ignition can be recognized on 20% boards.
   - Regression-test existing `pullback_doji` output when new filters are disabled.

7. Evidence and handoff
   - Store dated validation outputs under `analysis/` or `data/reports/` with stable names.
   - Update `goal.md`, `SESSION_HANDOFF.md`, and `analysis/workflow_checkpoint.md` only when implementation or validation state changes.
