"""Schema DDL for ``market.duckdb``.

Kept separate from ``market_db`` so read/write helpers do not carry large DDL
blocks. Constants are re-exported by ``market_db`` for backward compatibility.
"""
from __future__ import annotations


# MARKET_CORE_DDL (旧管线4表) 批3a 物删；当前仅保留 qfq 派生分析读面。


# [防重建] 旧 K线管线 DDL 均已移除勿复加 (price_kline_tdxhub 簇 06-23~27 / MARKET_CORE_DDL 4表 批3a, 详 ledger + git史)。


# v_price_kline_qfq 切 tushare-only (2026-06-22 派生序列来源收敛):
#   旧 tier-1=price_kline_tdxhub 的 qfq 系统性算错 (把复权因子当后复权式乘数抬高分红股历史价,
#   实测茅台/比亚迪 raw*adj_factor/latest 重建证 tushare 对 / tdxhub 错, 分叉随 adj_factor 放大最高 89%).
#   price_kline_qfq_tushare = 标准前复权, 覆盖 2019+/5431股 = tdxhub(2022+/5210股) 严格超集.
#   Physical lineage: batch_id / ingested_at / factor_as_of written on rebuild;
#   view passthrough (no NULL/COALESCE placeholders).
#   "没有备用源只有tushare"(用户 2026-06-22): 去掉 akshare/tdxhub fallback tier — 错 qfq 兜底=qfq
#   不连续假尖峰 (比缺口有害); 诚实缺口 > 错值 (mio: unknown > 假填). 仅 427行/55股 真缺口待从 raw 补.
# analysis/serving 表 = price_kline_qfq_tushare；它不是 nominal execution truth 或 AcceptedPartition。
# 2026-06-27 修复: schema-init 须先声明此表空壳, 否则 v_price_kline_qfq 视图 (FROM 此表) 在新 market.duckdb
# 上建视图即崩 (生产 init 路径 landmine; 此前 live DB 因 builder 早跑过表已存在掩盖了 bug)。
# 视图只用 code/date/open/high/low/close/volume/amount + lineage; builder DROP+CREATE AS 会覆盖空壳。
# 注: SQL 串内禁用 -- 注释 (duck_adapter.executescript 的 _split_statements 会在注释处截断语句).
#
# 2026-09-01 修 source_name 假血缘 (moth qfq-serving-tushare-only 从建立起数学上恒为0, 从未真查过):
#   source_name/source_tier/is_fallback 三列从来不是 PRICE_KLINE_QFQ_TUSHARE_DDL 里的物理列,
#   是本视图 SELECT 里的字面量 —— 'tushare' 硬编码只声明"取数方法没有兜底分支"(source_tier=1
#   恒定 / is_fallback 恒 FALSE 仍准确, 因 2026-06-22 移除的那条 akshare/tdxhub 兜底 tier 确未复活),
#   但拿它冒充"这行数据的真实供货商"是假血缘: nominal OHLCV 现经 canonical_nominal_ohlcv_daily 摄入
#   (build_price_kline_qfq_tushare.py --from-accepted 默认), 该表真实供货商已按裁决从 tushare 转
#   向通达信 —— 裁决现由 backend/config/tushare_sunset.yaml 的 domains.daily 记录 (decision=
#   replace/replacement=tdxhub/status=done@2026-09-01); 原引"goal.md 2026-08-31 业主拍板"那段
#   文本已随 09-01 goal.md 160→41 行瘦身重写(commit b28449b6a)被删, goal.md 现全文查不到"三源/
#   裁决/tdxhub"任一词, 故改引现存 owner, 勿再引已不存在的原文。实测 ingest_batch 2026-08-31
#   批次 tier0.market_data.nominal_ohlcv_daily 的 source_name 已是 tdxhub。price_kline_qfq_tushare
#   未透传 canonical 的 ingest_batch_id, 本表/本视图故不掌握逐行真实供货商, 不再假冒常量骗自己。
#   source_name 改 NULL (诚实缺口, 呼应上面第 22 行 mio: unknown > 假填); 复权因子仍恒定锁死
#   raw_tushare_adj_factor (JOIN 见 build_price_kline_qfq_tushare.py) 未变, 这才是 2026-06-22
#   真正要防的回归 (tdxhub 自算 adj_factor 系统性错, 见上); 该不变量现由
#   .moth/assertions/claims.yaml 的 qfq-lineage-guard 断言守 (解析真实 JOIN 图 + ingest_batch
#   全历史真血缘, 不再查字符串 presence 或已知不可能查到东西的字面量列)。真实逐行供货商查
#   ingest_batch, 不查本视图。注: tushare_sunset.yaml 的 domains.adj_factor 已裁决 replace->
#   tdxhub (must_by 2026-09-10), 但尚无 status:done —— 该域一旦切换完成, 本注释与
#   qfq-lineage-guard 的期望物理表集合都要同步改, 否则会重演本次修的同一种病 (门锁死昨天的
#   真相, 见门顶 claim 开头)。
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
    batch_id TEXT,
    ingested_at TIMESTAMP,
    factor_as_of TEXT,
    PRIMARY KEY (code, date)
);
"""

_QFQ_LINEAGE_MIGRATE = (
    ("batch_id", "TEXT"),
    ("ingested_at", "TIMESTAMP"),
    ("factor_as_of", "TEXT"),
)

ANALYSIS_KLINE_QFQ_VIEW_DDL = """
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
        CAST(NULL AS VARCHAR) AS source_name,
        1::SMALLINT AS source_tier,
        FALSE AS is_fallback,
        batch_id,
        ingested_at,
        factor_as_of
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
    """Create or refresh the current TuShare-derived qfq analysis table and view."""
    conn.executescript(PRICE_KLINE_QFQ_TUSHARE_DDL)  # 须在视图前 (v_price_kline_qfq FROM 此表)
    # Live DBs may still have the pre-lineage 8-col shell; ADD IF NOT EXISTS then recreate view.
    for col, typ in _QFQ_LINEAGE_MIGRATE:
        conn.execute(
            f"ALTER TABLE price_kline_qfq_tushare ADD COLUMN IF NOT EXISTS {col} {typ}"
        )
    conn.executescript(ANALYSIS_KLINE_QFQ_VIEW_DDL)
