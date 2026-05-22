# Formula Sell Rule Audit

- formulas: `gs_pullback_confirm, gs_raw_buy, ma_base_breakout, activity_breakout, volume_base_breakout`
- stocks: `5201`
- execution_model: `vwap_tradable_v1`
- elapsed_sec: `190.6`

## Best Sell Rule By Formula

- `activity_breakout`: `fixed_60`, score `44.82`, win `44.2%`, avg_ret `2.23%`, avg_dd `-16.82%`
- `gs_pullback_confirm`: `fixed_60`, score `46.57`, win `46.0%`, avg_ret `2.41%`, avg_dd `-14.80%`
- `gs_raw_buy`: `fixed_60`, score `47.15`, win `46.7%`, avg_ret `2.48%`, avg_dd `-14.27%`
- `ma_base_breakout`: `fixed_60`, score `23.07`, win `40.7%`, avg_ret `-1.65%`, avg_dd `-17.13%`
- `volume_base_breakout`: `fixed_60`, score `42.92`, win `44.2%`, avg_ret `1.84%`, avg_dd `-14.73%`

## Scope

- First sell-rule audit compares fixed holding periods with formula exit signals capped at 60 trading days.
- This is an audit artifact; production recommendation still uses the fixed-holding execution path until sell-rule selection is wired into caches and strategy cards.
