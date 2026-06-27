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


# 2026-06-23 M3: price_kline_tdxhub (股票日线表) DDL 已移除 (表物删 5.3M行)。
# 根因 §4.5 重建循环: init_market_db→ensure_market_schema 若保留 CREATE IF NOT EXISTS 会重建空表。
# serving K线真相源 = price_kline_qfq_tushare (build_price_kline_qfq_tushare, daily_update Step 2.96)。
# 仅保留 adjustment_event (xdxr 除权事件热备 §4.3); 复权质量裁决见下方 v_price_kline_qfq 注释。
PRICE_KLINE_TDXHUB_DDL = """
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


# v_price_kline_qfq 切 tushare-only (2026-06-22 切主源根因修复, 真相源唯一):
#   旧 tier-1=price_kline_tdxhub 的 qfq 系统性算错 (把复权因子当后复权式乘数抬高分红股历史价,
#   实测茅台/比亚迪 raw*adj_factor/latest 重建证 tushare 对 / tdxhub 错, 分叉随 adj_factor 放大最高 89%).
#   price_kline_qfq_tushare = 标准前复权, 覆盖 2019+/5431股 = tdxhub(2022+/5210股) 严格超集.
#   tushare qfq 表只存 OHLCV → 视图合成 freq/adjust/factor/source/batch_id/ingested_at.
#   "没有备用源只有tushare"(用户 2026-06-22): 去掉 akshare/tdxhub fallback tier — 错 qfq 兜底=qfq
#   不连续假尖峰 (比缺口有害); 诚实缺口 > 错值 (mio: unknown > 假填). 仅 427行/55股 真缺口待从 raw 补.
# serving K线真相源表 = price_kline_qfq_tushare (build_price_kline_qfq_tushare daily Step 2.96 用 CREATE TABLE AS 重建)。
# 2026-06-27 修复: schema-init 须先声明此表空壳, 否则 v_price_kline_qfq 视图 (FROM 此表) 在新 market.duckdb
# 上建视图即崩 (生产 init 路径 landmine; 此前 live DB 因 builder 早跑过表已存在掩盖了 bug)。
# 视图只用 code/date/open/high/low/close/volume/amount 8 列 (其余为字面量); builder DROP+CREATE AS 会覆盖空壳。
# 注: SQL 串内禁用 -- 注释 (duck_adapter.executescript 的 _split_statements 会在注释处截断语句).
PRICE_KLINE_QFQ_TUSHARE_DDL = """
CREATE TABLE IF NOT EXISTS price_kline_qfq_tushare (
    code   TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    volume REAL,
    amount REAL,
    PRIMARY KEY (code, date)
);
"""

CANONICAL_KLINE_QFQ_VIEW_DDL = """
CREATE OR REPLACE VIEW v_price_kline_qfq AS
WITH primary_rows AS (
    SELECT
        code,
        date,
        'daily' AS freq,
        'qfq' AS adjust,
        open,
        high,
        low,
        close,
        volume,
        amount,
        1.0 AS factor,
        'tushare' AS source_name,
        1::SMALLINT AS source_tier,
        FALSE AS is_fallback,
        CAST(NULL AS VARCHAR) AS batch_id,
        CAST(NULL AS TIMESTAMP) AS ingested_at
    FROM price_kline_qfq_tushare
    WHERE open IS NOT NULL AND open > 0
      AND high IS NOT NULL AND high > 0
      AND low IS NOT NULL AND low > 0
      AND close IS NOT NULL AND close > 0
      AND volume IS NOT NULL AND volume >= 1e-6
      AND amount IS NOT NULL AND amount >= 1e-6
      AND high >= open AND high >= close AND high >= low
      AND low <= open AND low <= close AND low <= high
)
SELECT * FROM primary_rows
"""


def ensure_market_schema(conn) -> None:
    """Create or refresh market.duckdb core tables, TDXHub tables, and canonical view."""
    conn.executescript(MARKET_CORE_DDL)
    conn.executescript(PRICE_KLINE_TDXHUB_DDL)
    conn.executescript(PRICE_KLINE_QFQ_TUSHARE_DDL)  # 须在视图前 (v_price_kline_qfq FROM 此表)
    conn.executescript(CANONICAL_KLINE_QFQ_VIEW_DDL)
