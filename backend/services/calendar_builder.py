"""calendar_builder — open-day serve projection from the accepted SSE calendar.

``dim_trading_calendar`` is NOT accepted calendar truth.  Accepted open+closed
generations live under ``calendar_runtime`` / ``calendar_reader``
(``landing_tushare_trade_cal`` → ``canonical_sse_trading_calendar_generation``).
This module only maintains the open-day serve projection used by legacy ops
consumers (horizon checks, scheduling helpers).  Prototype/content hashes from
this table must never be treated as an accepted generation proof.

R2 根因修复 (历史证据: git log --grep data_foundation_root_causes；现行边界:
本文件 + services.calendar):
legacy ``raw_tushare_trade_cal`` 中间层在生产代码里零 writer (``trade_cal`` sync 是
on_demand, 不进 drain), 只有测试写它 —— dim 全靠 2026-07-16 那批冻结数据的未来余量撑着,
horizon 倒计时中。第一性原理: 能由已发布的 accepted generation 直接得到的东西, 不该再
经一层无人维护的中间表; 多一层就是多一个断链点。本模块因此改为直接从
``canonical_sse_trading_calendar_generation`` (经 ``accepted_partition`` 指针选取最新
accepted 代际) 增量构建 dim, 删掉 ``raw_tushare_trade_cal`` 这个断链点。

pipeline acquire 在 sync 后调 ``build_latest`` 只刷新 serve projection。

语义裁决 (2026-07-03, 消费方实测 rg is_trading 全仓; 换源后不变):
- dim 只存交易日 (is_trading=1)。legacy 消费方 WHERE is_trading=1。
- 增量 = 幂等补洞 + 向未来延伸；accepted 修订 open→closed 时删除对应 dim 日。
- 只取 exchange='SSE'；canonical cal_date (DATE) → trade_date ISO VARCHAR (与既有
  dim 语义一字不变)。
- 代际选取: 只取 accepted_partition 里 dataset_id 匹配、accepted_at 最新的那一行的
  batch_id (= canonical.generation_id), 绝不用 max(generation_id) 字符串排序 —— 代际
  命名里的时间戳是巧合不是指针语义, accepted_at 才是权威顺序。
"""
from __future__ import annotations

import logging
from typing import Any

from services.data_sources.accepted_schema import ACCEPTED_TABLE
from services.data_sources.calendar_schema import CANONICAL_TABLE, DATASET_ID

logger = logging.getLogger(__name__)

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

# canonical (一个 generation_id 内) → ISO 交易日行 (SSE + is_open=1)。
# cal_date 已是 DATE、is_open 已是整数 (与 legacy raw 的 VARCHAR compact/VARCHAR 状态列不同,
# 无需 strptime/TRY_CAST 双层兼容), 直接 CAST 到 VARCHAR 即 'YYYY-MM-DD' (DuckDB DATE→VARCHAR
# 默认即 ISO-8601, 与既有 dim.trade_date 格式一致)。
_CANONICAL_TRADING_ISO = (
    "SELECT DISTINCT CAST(cal_date AS VARCHAR) AS trade_date "
    "FROM {canonical} WHERE generation_id = ? AND exchange = 'SSE' AND is_open = 1"
)
_CANONICAL_SSE_STATUS_ISO = (
    "SELECT CAST(cal_date AS VARCHAR) AS trade_date, MAX(is_open) AS is_open "
    "FROM {canonical} WHERE generation_id = ? AND exchange = 'SSE' GROUP BY 1"
)


def _db(alias: str) -> str:
    from services.database_manifest import get_database_manifest

    return str(get_database_manifest().path_for(alias))


def _tushare_rel(con, table: str) -> str:
    """accepted 表定位: conn 自带 (测试 fixture) 用之; 否则 ATTACH tushare_raw 只读 (生产路径,
    与 market_pulse/technical_states 同模式)。canonical_sse_trading_calendar_generation 和
    accepted_partition 物理上都落在 tushare_raw 库 (与旧 raw_tushare_trade_cal 同库), 与
    dim_trading_calendar 所在的 reference 库分离, 沿用同一套 ATTACH 只读模式。

    存在性检查必须限定 table_catalog = current_database(): 本函数对 accepted_partition /
    canonical_sse_trading_calendar_generation 各调一次, 第一次调用 ATTACH 后, DuckDB 的
    information_schema.tables 若不加 catalog 过滤会把已 ATTACH 的 tr 目录下的表也一并看见,
    导致第二次调用误判"本地已有"而漏掉 tr. 前缀、解析到不存在的裸表名 (同 calendar_reader.py
    ``_require_formal_schema`` 的既有教训, 那里用 table_catalog = current_database() 规避)。"""
    has = con.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_catalog = current_database() AND table_name = ? LIMIT 1",
        [table],
    ).fetchone()
    if has:
        return table
    con.execute(f"ATTACH IF NOT EXISTS '{_db('tushare_raw')}' AS tr (READ_ONLY)")
    return f"tr.{table}"


def _latest_accepted_generation_id(con, accepted_rel: str) -> str:
    """唯一权威指针: accepted_partition 里该 dataset_id 的最新 accepted_at 行的 batch_id
    (= canonical.generation_id)。找不到 → raise (fail loud, 拒绝静默 no-op)。"""
    row = con.execute(
        f"SELECT batch_id FROM {accepted_rel} WHERE dataset_id = ? "
        "ORDER BY accepted_at DESC LIMIT 1",
        [DATASET_ID],
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError(
            f"{ACCEPTED_TABLE} 无 dataset_id={DATASET_ID!r} 的 accepted 指针 — "
            "日历尚未发布过任何 accepted generation (换源后 land+accept 从未跑过或表缺失); "
            "拒绝静默 no-op (dim 会停止延伸, horizon 门会 FAIL)"
        )
    return str(row[0])


def build_latest(conn=None) -> dict[str, Any]:
    """accepted canonical SSE calendar (最新 generation) → dim_trading_calendar 增量 MERGE (幂等;
    无新日 = no-op).

    conn=None → 自管 reference RW 连接 + ATTACH tushare_raw RO; 注入 conn (测试) 时调用方保证
    canonical_sse_trading_calendar_generation / accepted_partition 可解析。accepted 指针缺失 /
    该 generation 无 SSE is_open=1 行 → raise (fail loud, 拒绝静默 no-op 假装刷新)。
    返回 {inserted, watermark_before, dim_max, dim_rows, raw_max_trading, generation_id}。
    """
    own = conn is None
    if own:
        from services.data_access import resolver

        con = resolver.connect_rw("reference")
    else:
        con = conn
    transaction_started = False
    try:
        accepted_rel = _tushare_rel(con, ACCEPTED_TABLE)
        canonical_rel = _tushare_rel(con, CANONICAL_TABLE)
        generation_id = _latest_accepted_generation_id(con, accepted_rel)

        raw_src = _CANONICAL_TRADING_ISO.format(canonical=canonical_rel)
        raw_row = con.execute(
            f"SELECT COUNT(*), MAX(trade_date) FROM ({raw_src})", [generation_id]
        ).fetchone()
        if not raw_row or not raw_row[0]:
            raise RuntimeError(
                f"{CANONICAL_TABLE} generation_id={generation_id!r} 无 SSE is_open=1 行 — "
                "accepted generation 内容异常或指针指向空代际; "
                "拒绝静默 no-op (dim 会停止延伸, horizon 门会 FAIL)"
            )
        con.execute(_DIM_DDL)
        wm, floor, before_rows = con.execute(
            f"SELECT COALESCE(MAX(CAST(trade_date AS VARCHAR)), ''), "
            f"COALESCE(MIN(CAST(trade_date AS VARCHAR)), ''), COUNT(*) FROM {DIM_TABLE}"
        ).fetchone()
        raw_status_src = _CANONICAL_SSE_STATUS_ISO.format(canonical=canonical_rel)
        missing_before = con.execute(
            f"SELECT COUNT(*) FROM ({raw_src}) src "
            f"LEFT JOIN {DIM_TABLE} dim ON dim.trade_date = src.trade_date "
            "WHERE dim.trade_date IS NULL AND (? = '' OR src.trade_date >= ?)",
            [generation_id, floor, floor],
        ).fetchone()[0]
        con.execute("BEGIN TRANSACTION")
        transaction_started = True
        con.execute(
            f"DELETE FROM {DIM_TABLE} USING ({raw_status_src}) src "
            f"WHERE {DIM_TABLE}.trade_date = src.trade_date AND src.is_open = 0",
            [generation_id],
        )
        con.execute(
            f"INSERT INTO {DIM_TABLE} (trade_date, is_trading) "
            f"SELECT trade_date, 1 AS is_trading FROM ({raw_src}) "
            "WHERE (? = '' OR trade_date >= ?) ORDER BY 1 ON CONFLICT DO NOTHING",
            [generation_id, floor, floor],
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
            "generation_id": generation_id,
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


__all__ = ["build_latest", "DIM_TABLE", "DIM_ROLE", "DIM_IS_ACCEPTED_TRUTH"]
