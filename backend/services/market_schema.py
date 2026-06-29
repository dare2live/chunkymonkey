"""Schema DDL for ``market.duckdb``.

Kept separate from ``market_db`` so read/write helpers do not carry large DDL
blocks. Constants are re-exported by ``market_db`` for backward compatibility.
"""
from __future__ import annotations


# 2026-06-29 批3a 数据纯化: MARKET_CORE_DDL (price_kline/price_xdxr/market_sync_state/
#   price_import_batch) 4 表整体退役物删 (db_lifecycle_delete archive 留底, 可逆)。
#   - price_kline: akshare HS300 指数残留 (1048行), 非 tushare 源 = §4.3 删除对象;
#   - price_xdxr: tdxhub 复权事件残留 (173781行), 复权已切 price_kline_qfq_tushare PIT 前复权;
#   - market_sync_state / price_import_batch: 旧 akshare/tdxhub K线管线同步状态/批次记录,
#     0 live caller (tushare K线走 build_price_kline_qfq_tushare CREATE TABLE AS 重建, 不经此管线)。
#   serving K线真相源 = price_kline_qfq_tushare → v_price_kline_qfq (下方 DDL 保留, 不受影响)。


# 2026-06-23 M3: price_kline_tdxhub (股票日线表) DDL 已移除 (表物删 5.3M行)。
# 2026-06-27 通达信全删 单元6: price_kline_tdxhub_adjustment_event (xdxr 除权热备) DDL 亦移除 (表物删 735行);
#   builder build_price_kline_tdxhub.py 已退役物删 (0 caller, manifest 已 repoint qfq_tushare); serving K线真相源 = price_kline_qfq_tushare。
# PRICE_KLINE_TDXHUB_DDL 常量随之删 (importers market_db/market_read/mini_market/test 同步清)。


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
    """Create or refresh market.duckdb canonical K-line table and view (tushare-only)."""
    conn.executescript(PRICE_KLINE_QFQ_TUSHARE_DDL)  # 须在视图前 (v_price_kline_qfq FROM 此表)
    conn.executescript(CANONICAL_KLINE_QFQ_VIEW_DDL)
