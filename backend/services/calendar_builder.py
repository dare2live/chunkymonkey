"""calendar_builder — open-day serve projection from legacy raw trade_cal.

``dim_trading_calendar`` is NOT accepted calendar truth.  Accepted open+closed
generations live under ``calendar_runtime`` / ``calendar_reader``
(``landing_tushare_trade_cal`` → ``canonical_sse_trading_calendar_generation``).
This module only maintains the open-day serve projection used by legacy ops
consumers (horizon checks, scheduling helpers).  Prototype/content hashes from
this table must never be treated as an accepted generation proof.

R1 根因 3 修复（历史证据: analysis/data_foundation_root_causes_20260703.md；
现行边界: docs/MASTER_TOPLEVEL_DESIGN.md）:
dim 曾缺生产 writer；raw trade_cal full_refresh 正常但零传导 → horizon 倒计时。
pipeline acquire 在 sync 后调 ``build_latest`` 只刷新 serve projection。

语义裁决 (2026-07-03, 消费方实测 rg is_trading 全仓):
- dim 只存交易日 (is_trading=1)。legacy 消费方 WHERE is_trading=1。
- 增量 = 幂等补洞 + 向未来延伸；raw 修订 open→closed 时删除对应 dim 日。
- 只取 exchange='SSE'；cal_date compact → trade_date ISO。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

RAW_TABLE = "raw_tushare_trade_cal"
DIM_TABLE = "dim_trading_calendar"
# Role marker for audits/gates: serve projection ≠ accepted generation.
DIM_ROLE = "serve_projection_open_days_only"
DIM_IS_ACCEPTED_TRUTH = False

# dim 重建 DDL (与 reference 现表实测 schema 一致: trade_date VARCHAR PK + is_trading BIGINT);
# 正常路径表已在, 仅 bootstrap (库重建) 时触发。
_DIM_DDL = (
    f"CREATE TABLE IF NOT EXISTS {DIM_TABLE} "
    "(trade_date VARCHAR NOT NULL, is_trading BIGINT, PRIMARY KEY(trade_date))"
)

# raw → ISO 交易日行 (SSE + is_open=1): TRY_CAST 双层兼容 is_open 落库为 VARCHAR/BIGINT/DOUBLE
_RAW_TRADING_ISO = (
    "SELECT DISTINCT strftime(strptime(CAST(cal_date AS VARCHAR), '%Y%m%d'), '%Y-%m-%d') AS trade_date "
    "FROM {raw} WHERE exchange = 'SSE' AND TRY_CAST(TRY_CAST(is_open AS DOUBLE) AS INTEGER) = 1"
)
_RAW_SSE_STATUS_ISO = (
    "SELECT strftime(strptime(CAST(cal_date AS VARCHAR), '%Y%m%d'), '%Y-%m-%d') AS trade_date, "
    "MAX(TRY_CAST(TRY_CAST(is_open AS DOUBLE) AS INTEGER)) AS is_open "
    "FROM {raw} WHERE exchange = 'SSE' GROUP BY 1"
)


def _db(alias: str) -> str:
    from services.database_manifest import get_database_manifest

    return str(get_database_manifest().path_for(alias))


def _raw_rel(con) -> str:
    """raw 表定位: conn 自带 (测试 fixture) 用之; 否则 ATTACH tushare_raw 只读 (生产路径,
    与 market_pulse/technical_states 同模式)。"""
    has = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1", [RAW_TABLE]
    ).fetchone()
    if has:
        return RAW_TABLE
    con.execute(f"ATTACH IF NOT EXISTS '{_db('tushare_raw')}' AS tr (READ_ONLY)")
    return f"tr.{RAW_TABLE}"


def build_latest(conn=None) -> dict[str, Any]:
    """raw_tushare_trade_cal → dim_trading_calendar 增量 MERGE (幂等; 无新日 = no-op).

    conn=None → 自管 reference RW 连接 + ATTACH tushare_raw RO; 注入 conn (测试) 时
    调用方保证两表可解析。raw 缺失/空 → raise (fail loud, 拒绝静默 no-op 假装刷新)。
    返回 {inserted, watermark_before, dim_max, dim_rows, raw_max_trading}。
    """
    own = conn is None
    if own:
        from services.data_access import resolver

        con = resolver.connect_rw("reference")
    else:
        con = conn
    transaction_started = False
    try:
        raw_rel = _raw_rel(con)
        raw_src = _RAW_TRADING_ISO.format(raw=raw_rel)
        raw_row = con.execute(
            f"SELECT COUNT(*), MAX(trade_date) FROM ({raw_src})"
        ).fetchone()
        if not raw_row or not raw_row[0]:
            raise RuntimeError(
                f"{RAW_TABLE} 无 SSE is_open=1 行 — trade_cal sync 未跑或表缺失; "
                "拒绝静默 no-op (dim 会停止延伸, horizon 门会 FAIL)"
            )
        con.execute(_DIM_DDL)
        wm, floor, before_rows = con.execute(
            f"SELECT COALESCE(MAX(CAST(trade_date AS VARCHAR)), ''), "
            f"COALESCE(MIN(CAST(trade_date AS VARCHAR)), ''), COUNT(*) FROM {DIM_TABLE}"
        ).fetchone()
        raw_status_src = _RAW_SSE_STATUS_ISO.format(raw=raw_rel)
        missing_before = con.execute(
            f"SELECT COUNT(*) FROM ({raw_src}) src "
            f"LEFT JOIN {DIM_TABLE} dim ON dim.trade_date = src.trade_date "
            "WHERE dim.trade_date IS NULL AND (? = '' OR src.trade_date >= ?)",
            [floor, floor],
        ).fetchone()[0]
        con.execute("BEGIN TRANSACTION")
        transaction_started = True
        con.execute(
            f"DELETE FROM {DIM_TABLE} USING ({raw_status_src}) src "
            f"WHERE {DIM_TABLE}.trade_date = src.trade_date AND src.is_open = 0"
        )
        con.execute(
            f"INSERT INTO {DIM_TABLE} (trade_date, is_trading) "
            f"SELECT trade_date, 1 AS is_trading FROM ({raw_src}) "
            "WHERE (? = '' OR trade_date >= ?) ORDER BY 1 ON CONFLICT DO NOTHING",
            [floor, floor],
        )
        dim_max, dim_rows = con.execute(
            f"SELECT MAX(trade_date), COUNT(*) FROM {DIM_TABLE}"
        ).fetchone()
        inserted = int(missing_before)
        deleted = int(before_rows) + inserted - int(dim_rows)
        con.commit()
        transaction_started = False
        out = {
            "inserted": inserted,
            "deleted": deleted,
            "watermark_before": wm or None,
            "dim_max": dim_max,
            "dim_rows": int(dim_rows),
            "raw_max_trading": raw_row[1],
        }
        logger.info("[calendar_builder] build_latest: %s", out)
        return out
    except Exception:
        if transaction_started:
            try:
                con.rollback()
            except Exception:  # noqa: BLE001 - preserve the original calendar failure
                pass
        raise
    finally:
        if own:
            con.close()


__all__ = ["build_latest", "RAW_TABLE", "DIM_TABLE"]
