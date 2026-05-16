"""Phase 1 — fact_candle_pattern_daily DDL (Codex aa4a41ca Path 3, 2026-05-16).

PIT-safe per-(stock, date) candle features. Source v_price_kline_qfq + 20-day prior window.

Columns:
- 6 数值: body_ratio / upper_shadow_ratio / lower_shadow_ratio / close_position / volume_relative / breakout_strength_20
- 6 binary: is_bullish / is_doji / is_long_lower_shadow / is_long_upper_shadow / is_marubozu / is_high_volume
- PIT 锚点: source_max_trade_date (assertion: ≤ trade_date)
- 元: built_at / source_version
"""

CANDLE_PATTERN_DDL = """
CREATE TABLE IF NOT EXISTS fact_candle_pattern_daily (
    stock_code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    -- 6 数值特征 (services/candle_pattern/features.py compute_features_for_signal)
    body_ratio               DOUBLE,
    upper_shadow_ratio       DOUBLE,
    lower_shadow_ratio       DOUBLE,
    close_position           DOUBLE,
    volume_relative          DOUBLE,
    breakout_strength_20     DOUBLE,
    -- 6 binary 派生
    is_bullish               BOOLEAN,
    is_doji                  BOOLEAN,
    is_long_lower_shadow     BOOLEAN,
    is_long_upper_shadow     BOOLEAN,
    is_marubozu              BOOLEAN,
    is_high_volume           BOOLEAN,
    -- PIT 锚点 (Codex aa4a41ca: source_max_trade_date ≤ trade_date assertion)
    source_max_trade_date    DATE NOT NULL,
    -- 元
    built_at                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_version           TEXT DEFAULT 'v1',
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_candle_date ON fact_candle_pattern_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_candle_stock_date ON fact_candle_pattern_daily(stock_code, trade_date);
"""


CANDLE_PATTERN_PIT_AUDIT_SQL = """
-- Codex aa4a41ca D acceptance audit #1: PIT integrity
-- 任何 source_max_trade_date > trade_date → bad_rows > 0 → FAIL
SELECT COUNT(*) AS bad_rows
FROM fact_candle_pattern_daily
WHERE source_max_trade_date > trade_date;
"""


CANDLE_PATTERN_COVERAGE_SQL = """
-- Codex aa4a41ca D acceptance audit #2: feature completeness
-- 核心 6 数值 non-null 覆盖率 ≥ 98%
SELECT
    AVG(CASE WHEN body_ratio IS NOT NULL THEN 1 ELSE 0 END) AS body_ratio_cov,
    AVG(CASE WHEN upper_shadow_ratio IS NOT NULL THEN 1 ELSE 0 END) AS upper_shadow_cov,
    AVG(CASE WHEN lower_shadow_ratio IS NOT NULL THEN 1 ELSE 0 END) AS lower_shadow_cov,
    AVG(CASE WHEN close_position IS NOT NULL THEN 1 ELSE 0 END) AS close_pos_cov,
    AVG(CASE WHEN volume_relative IS NOT NULL THEN 1 ELSE 0 END) AS vol_rel_cov,
    AVG(CASE WHEN breakout_strength_20 IS NOT NULL THEN 1 ELSE 0 END) AS breakout_cov
FROM fact_candle_pattern_daily;
"""
