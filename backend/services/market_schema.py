"""Schema DDL for ``market.duckdb``.

Kept separate from ``market_db`` so read/write helpers do not carry large DDL
blocks. Constants are re-exported by ``market_db`` for backward compatibility.
"""
from __future__ import annotations


MARKET_CORE_DDL = """
-- K 线数据主表
CREATE TABLE IF NOT EXISTS price_kline (
    code        TEXT    NOT NULL,
    date        TEXT    NOT NULL,
    freq        TEXT    NOT NULL DEFAULT 'daily',
    adjust      TEXT    NOT NULL DEFAULT 'qfq',
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,
    amount      REAL,
    source      TEXT,
    batch_id    TEXT,
    ingested_at TEXT,
    PRIMARY KEY (code, date, freq, adjust)
);
CREATE INDEX IF NOT EXISTS idx_pk_code_freq
    ON price_kline(code, freq);
CREATE INDEX IF NOT EXISTS idx_pk_date
    ON price_kline(date);

-- 除权除息 / 股本变动事件（TDX xdxr）
CREATE TABLE IF NOT EXISTS price_xdxr (
    code            TEXT NOT NULL,
    date            TEXT NOT NULL,
    category        INTEGER NOT NULL,
    name            TEXT,
    fenhong         REAL,
    peigujia        REAL,
    songzhuangu     REAL,
    peigu           REAL,
    suogu           REAL,
    panqianliutong  REAL,
    panhouliutong   REAL,
    qianzongguben   REAL,
    houzongguben    REAL,
    fenshu          REAL,
    xingquanjia     REAL,
    source          TEXT,
    batch_id        TEXT,
    ingested_at     TEXT,
    PRIMARY KEY (code, date, category)
);
CREATE INDEX IF NOT EXISTS idx_xdxr_code_date
    ON price_xdxr(code, date);

-- 同步状态表（覆盖状态交给审计层推导，不在此表堆字段）
CREATE TABLE IF NOT EXISTS market_sync_state (
    dataset         TEXT NOT NULL DEFAULT 'price_kline',
    code            TEXT NOT NULL,
    freq            TEXT NOT NULL DEFAULT 'daily',
    adjust          TEXT NOT NULL DEFAULT 'qfq',
    source          TEXT,
    min_date        TEXT,
    max_date        TEXT,
    row_count       INTEGER DEFAULT 0,
    last_success_at TEXT,
    last_attempt_at TEXT,
    last_error      TEXT,
    PRIMARY KEY (dataset, code, freq, adjust)
);

-- 导入批次记录
CREATE TABLE IF NOT EXISTS price_import_batch (
    batch_id        TEXT PRIMARY KEY,
    source_type     TEXT,
    source_name     TEXT,
    freq            TEXT,
    adjust          TEXT,
    rows_imported   INTEGER DEFAULT 0,
    min_date        TEXT,
    max_date        TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    status          TEXT DEFAULT 'running',
    error           TEXT,
    detail          TEXT
);
"""


PRICE_KLINE_TDXHUB_DDL = """
CREATE TABLE IF NOT EXISTS price_kline_tdxhub (
    code          TEXT NOT NULL,
    date          TEXT NOT NULL,
    freq          TEXT NOT NULL DEFAULT 'daily',
    adjust        TEXT NOT NULL DEFAULT 'qfq',
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    volume        REAL,
    amount        REAL,
    factor        REAL,
    source        TEXT DEFAULT 'tdxhub',
    batch_id      TEXT,
    ingested_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date, freq, adjust)
);
CREATE INDEX IF NOT EXISTS idx_pkt_code ON price_kline_tdxhub(code);
CREATE INDEX IF NOT EXISTS idx_pkt_date ON price_kline_tdxhub(date);

CREATE TABLE IF NOT EXISTS price_kline_tdxhub_adjustment_event (
    code          TEXT NOT NULL,
    event_date    TEXT NOT NULL,
    event_hash    TEXT NOT NULL,
    adjust_factor REAL NOT NULL,
    prev_close    REAL,
    source        TEXT,
    batch_id      TEXT,
    applied_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, event_date, event_hash)
);
CREATE INDEX IF NOT EXISTS idx_pkt_adj_code_date
    ON price_kline_tdxhub_adjustment_event(code, event_date);
"""


CANONICAL_KLINE_QFQ_VIEW_DDL = """
CREATE OR REPLACE VIEW v_price_kline_qfq AS
WITH primary_rows AS (
    SELECT
        code,
        date,
        freq,
        adjust,
        open,
        high,
        low,
        close,
        volume,
        amount,
        COALESCE(factor, 1.0) AS factor,
        COALESCE(NULLIF(source, ''), 'tdxhub') AS source_name,
        1::SMALLINT AS source_tier,
        FALSE AS is_fallback,
        batch_id,
        ingested_at
    FROM price_kline_tdxhub
    WHERE freq = 'daily' AND adjust = 'qfq'
      AND open IS NOT NULL AND open > 0
      AND high IS NOT NULL AND high > 0
      AND low IS NOT NULL AND low > 0
      AND close IS NOT NULL AND close > 0
      AND volume IS NOT NULL AND volume >= 1e-6
      AND amount IS NOT NULL AND amount >= 1e-6
      AND high >= open AND high >= close AND high >= low
      AND low <= open AND low <= close AND low <= high
),
fallback_rows AS (
    SELECT
        f.code,
        f.date,
        f.freq,
        f.adjust,
        f.open,
        f.high,
        f.low,
        f.close,
        f.volume,
        f.amount,
        1.0 AS factor,
        COALESCE(NULLIF(f.source, ''), 'akshare_multi_source') AS source_name,
        3::SMALLINT AS source_tier,
        TRUE AS is_fallback,
        f.batch_id,
        f.ingested_at
    FROM price_kline f
    WHERE f.freq = 'daily'
      AND f.adjust = 'qfq'
      AND f.open IS NOT NULL AND f.open > 0
      AND f.high IS NOT NULL AND f.high > 0
      AND f.low IS NOT NULL AND f.low > 0
      AND f.close IS NOT NULL AND f.close > 0
      AND f.volume IS NOT NULL AND f.volume >= 1e-6
      AND f.amount IS NOT NULL AND f.amount >= 1e-6
      AND f.high >= f.open AND f.high >= f.close AND f.high >= f.low
      AND f.low <= f.open AND f.low <= f.close AND f.low <= f.high
      AND NOT EXISTS (
          SELECT 1
          FROM primary_rows p
          WHERE p.code = f.code
            AND p.date = f.date
            AND p.freq = f.freq
            AND p.adjust = f.adjust
      )
)
SELECT * FROM primary_rows
UNION ALL
SELECT * FROM fallback_rows
"""


def ensure_market_schema(conn) -> None:
    """Create or refresh market.duckdb core tables, TDXHub tables, and canonical view."""
    conn.executescript(MARKET_CORE_DDL)
    conn.executescript(PRICE_KLINE_TDXHUB_DDL)
    conn.executescript(CANONICAL_KLINE_QFQ_VIEW_DDL)
