"""``raw_baostock_daily_k`` — L0_source 水库表: baostock ``query_history_k_
data_plus`` 每一次真实调用返回的行原样落库 (append-only 版本化)。

**为什么需要这张水库** (2026-09-18, ST 历史补数刀2): daily 适配器今天已经对
全部沪深股票每天调一遍这个接口 (``sources/fuyao_daily_k.py::
_BaostockReferenceCache``), 取 ``pre_close`` 用的那一条腿——但结果只进程内
缓存, 一次 ``chunkyctl sync`` 运行结束就丢 (F3)。同一响应行里还有一列 ``isST``
(ST 契约 v2 的第二条派生路径要用), 若不落库, 每天都在问服务端同一批数据却每天
都在扔掉大半个答案。这张表把"水龙头已经在流的水"接进"水库"——一表一
writer (本模块), 两个生产者 (daily 适配器 drain + 独立回填命令)。

**红线 7 (版本是列不是表名)**: append-only 版本化。同一 ``(ts_code,
trade_date)`` 若供应商改口 (同一天两次查询给出不同答案), 新版本插一行新
``fetched_at``, 旧版本原样保留——不是 UPDATE 覆盖。读侧永远取 ``fetched_at``
最新的一版 (:func:`latest_rows_for_date`)。同一 ``(ts_code, trade_date,
fetched_at)`` 三元组已存在但 ``row_hash`` 不同则 ``raise``: 同一次调用
(``fetched_at`` 相同即同一次 ``_query``) 不可能给出两个不同的答案, 出现这种情况
说明调用方把两次不同的调用错误地打上了同一个 ``fetched_at``, 这是调用方的
bug, 不是可以静默吞掉的数据分歧。

**主键 == 版本键**: ``(ts_code, trade_date, fetched_at)``。表结构与
``ensure_security_day_schema`` 系同宗但更简单——不是 ``SecurityDayDomain``,
不走 land→accept 两段式 (这张水库不做 universe 过滤/契约戳/PIT availability
判断, 它只是"服务端在这个时刻说了什么"的原样存根), 所以本模块不复用
``security_day_partition`` 的 land/accept 机制, 只借它的 ``stable_json`` /
``sha256_text`` 两个纯函数 (行内容指纹用同一套序列化, 不第二次发明)。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import json

from services.data_sources.security_day_partition import sha256_text, stable_json

TABLE = "raw_baostock_daily_k"

_EXPECTED_COLUMNS = {
    "ts_code", "trade_date", "fetched_at", "baostock_code", "fields_csv",
    "payload_json", "row_hash", "fetch_context", "request_start", "request_end",
}
_EXPECTED_PRIMARY_KEY = ("ts_code", "trade_date", "fetched_at")

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    ts_code VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    fetched_at TIMESTAMP WITH TIME ZONE NOT NULL,
    baostock_code VARCHAR NOT NULL,
    fields_csv VARCHAR NOT NULL,
    payload_json VARCHAR NOT NULL,
    row_hash VARCHAR NOT NULL,
    fetch_context VARCHAR NOT NULL,
    request_start DATE NOT NULL,
    request_end DATE NOT NULL,
    PRIMARY KEY (ts_code, trade_date, fetched_at)
)
"""


class ReservoirSchemaError(RuntimeError):
    """水库表结构漂移 (列集合或主键不符) —— fail-closed, 从不静默放行。"""


class ReservoirWriteConflictError(RuntimeError):
    """同一 (ts_code, trade_date, fetched_at) 已存在但 row_hash 不同——同一次
    调用不可能给出两个不同的答案, 调用方把两次不同的调用错误地打上了同一个
    fetched_at。"""


@dataclass(frozen=True)
class ReservoirRow:
    ts_code: str
    trade_date: str  # YYYYMMDD compact
    fetched_at: datetime  # aware UTC
    baostock_code: str
    fields_csv: str
    payload: dict[str, Any]
    fetch_context: str
    request_start: str  # YYYYMMDD compact
    request_end: str  # YYYYMMDD compact


@dataclass(frozen=True)
class ReservoirWriteOutcome:
    rows_seen: int
    rows_inserted: int
    rows_unchanged: int


def _to_date(compact: str) -> date:
    text = str(compact)
    return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))


def _from_date(value: Any) -> str:
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "")


def ensure_baostock_daily_k_schema(conn: Any) -> None:
    """Create-if-absent + verify shape (column set + primary key). Fail-closed
    on drift (e.g. a column added out-of-band) — ``CREATE TABLE IF NOT
    EXISTS`` alone is a silent no-op against an already-drifted table."""

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(_DDL)
        cols = {
            str(r[0]) for r in conn.execute(f"DESCRIBE {TABLE}").fetchall()
        }
        if cols != _EXPECTED_COLUMNS:
            raise ReservoirSchemaError(
                f"{TABLE} schema drift: missing={sorted(_EXPECTED_COLUMNS - cols)} "
                f"extra={sorted(cols - _EXPECTED_COLUMNS)}"
            )
        pk_rows = conn.execute(
            "SELECT constraint_type, constraint_column_names FROM duckdb_constraints() "
            "WHERE table_name = ?",
            [TABLE],
        ).fetchall()
        primary_keys = {
            tuple(str(c) for c in r[1])
            for r in pk_rows
            if str(r[0]).upper() == "PRIMARY KEY"
        }
        if primary_keys != {_EXPECTED_PRIMARY_KEY}:
            raise ReservoirSchemaError(
                f"{TABLE} primary-key drift: actual={sorted(primary_keys)} "
                f"expected={[_EXPECTED_PRIMARY_KEY]}"
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def record_baostock_daily_k_rows(
    conn: Any, rows: Sequence[ReservoirRow]
) -> ReservoirWriteOutcome:
    """Append-only versioned write. **自己 BEGIN/COMMIT**, 失败 ROLLBACK 后原样
    抛出——调用方 (daily 适配器 / 回填命令) 不需要自己管这张水库的事务。"""

    ensure_baostock_daily_k_schema(conn)
    rows_seen = 0
    rows_inserted = 0
    rows_unchanged = 0
    conn.execute("BEGIN TRANSACTION")
    try:
        for row in rows:
            rows_seen += 1
            payload_json = stable_json(row.payload)
            row_hash = sha256_text(payload_json)
            trade_date = _to_date(row.trade_date)

            exact = conn.execute(
                f"SELECT row_hash FROM {TABLE} WHERE ts_code = ? AND trade_date = ? "
                "AND fetched_at = ?",
                [row.ts_code, trade_date, row.fetched_at],
            ).fetchone()
            if exact is not None:
                if str(exact[0]) != row_hash:
                    raise ReservoirWriteConflictError(
                        f"{TABLE}: (ts_code={row.ts_code}, trade_date={row.trade_date}, "
                        f"fetched_at={row.fetched_at.isoformat()}) 已存在但 row_hash 不同 "
                        f"(existing={exact[0]!r} new={row_hash!r}) —— 同一次调用不可能给出"
                        "两个不同的答案"
                    )
                rows_unchanged += 1
                continue

            latest = conn.execute(
                f"SELECT row_hash FROM {TABLE} WHERE ts_code = ? AND trade_date = ? "
                "ORDER BY fetched_at DESC LIMIT 1",
                [row.ts_code, trade_date],
            ).fetchone()
            if latest is not None and str(latest[0]) == row_hash:
                rows_unchanged += 1
                continue

            conn.execute(
                f"""
                INSERT INTO {TABLE} (
                    ts_code, trade_date, fetched_at, baostock_code, fields_csv,
                    payload_json, row_hash, fetch_context, request_start, request_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row.ts_code,
                    trade_date,
                    row.fetched_at,
                    row.baostock_code,
                    row.fields_csv,
                    payload_json,
                    row_hash,
                    row.fetch_context,
                    _to_date(row.request_start),
                    _to_date(row.request_end),
                ],
            )
            rows_inserted += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return ReservoirWriteOutcome(
        rows_seen=rows_seen, rows_inserted=rows_inserted, rows_unchanged=rows_unchanged
    )


def latest_rows_for_date(conn: Any, trade_date: str) -> list[ReservoirRow]:
    """每码最新一版, 限定某一日。只读, 不建表 (调用前须已 ``ensure_baostock_
    daily_k_schema`` 或已经写过数据)。"""

    day = _to_date(trade_date)
    rows = conn.execute(
        f"""
        SELECT r.ts_code, r.trade_date, r.fetched_at, r.baostock_code, r.fields_csv,
               r.payload_json, r.fetch_context, r.request_start, r.request_end
          FROM {TABLE} r
         WHERE r.trade_date = ?
           AND r.fetched_at = (
                 SELECT MAX(r2.fetched_at) FROM {TABLE} r2
                  WHERE r2.ts_code = r.ts_code AND r2.trade_date = r.trade_date
               )
         ORDER BY r.ts_code
        """,
        [day],
    ).fetchall()
    return [
        ReservoirRow(
            ts_code=str(r[0]),
            trade_date=_from_date(r[1]),
            fetched_at=r[2],
            baostock_code=str(r[3]),
            fields_csv=str(r[4]),
            payload=json.loads(str(r[5])),
            fetch_context=str(r[6]),
            request_start=_from_date(r[7]),
            request_end=_from_date(r[8]),
        )
        for r in rows
    ]


def dates_with_isst_rows(conn: Any, dates: Iterable[str], *, isst_field: str) -> set[str]:
    """子集 of ``dates`` (YYYYMMDD compact): 该日至少一行且其 ``fields_csv``
    含 ``isst_field`` (逗号切分后精确成员判断, 不是子串匹配——避免误配一个偶然
    含该字段名子串的别的字段)。没有该字段的旧行 (契约升版前落的水库行, 若曾经
    存在过) 不计入。

    ``isst_field`` 无默认值、调用方必须显式传入 (B4 修法, 2026-09-19 返修):
    字段名是 ``stock_st_acquire.yaml`` 的 ``reservoir.isst_field``, 这里不留
    字面量副本, 调用方从配置现读后传入——否则配置改了这里悄悄不跟, 两处判据
    分歧。"""

    wanted = {str(d) for d in dates}
    if not wanted:
        return set()
    days = [_to_date(d) for d in wanted]
    placeholders = ", ".join(["?"] * len(days))
    rows = conn.execute(
        f"SELECT DISTINCT trade_date, fields_csv FROM {TABLE} "
        f"WHERE trade_date IN ({placeholders})",
        days,
    ).fetchall()
    result: set[str] = set()
    for trade_date, fields_csv in rows:
        compact = _from_date(trade_date)
        if compact in wanted and isst_field in str(fields_csv).split(","):
            result.add(compact)
    return result


def ts_code_from_baostock_code(baostock_code: str, exchange_suffix_to_baostock_prefix) -> str:
    """反解 ``sh.600000`` -> ``600000.SH``。复用 daily 域已有的正向映射表 (读
    ``load_nominal_ohlcv_acquire_rules().exchange_suffix_to_baostock_prefix``,
    调用方传入即可, 本函数不再自己 import/构造第二份映射来源)——**登记为债**:
    这张映射本属 baostock 源级参数, 日后应上收到 ``sources.baostock`` 级配置;
    本刀不动, 只在这里做反向查表, 不重写正向定义。"""

    prefix, sep, numeric = baostock_code.partition(".")
    if not sep or not numeric:
        raise ValueError(
            f"baostock_daily_k_reservoir: baostock_code={baostock_code!r} 形状不是 "
            "'<prefix>.<code>'"
        )
    reverse = {v.rstrip("."): k for k, v in exchange_suffix_to_baostock_prefix.items()}
    suffix = reverse.get(prefix)
    if suffix is None:
        raise ValueError(
            f"baostock_daily_k_reservoir: baostock_code={baostock_code!r} 前缀 {prefix!r} "
            f"不在 exchange_suffix_to_baostock_prefix 反查表里 (已知: {sorted(reverse)})"
        )
    return f"{numeric}.{suffix}"


__all__ = [
    "TABLE",
    "ReservoirRow",
    "ReservoirSchemaError",
    "ReservoirWriteConflictError",
    "ReservoirWriteOutcome",
    "dates_with_isst_rows",
    "ensure_baostock_daily_k_schema",
    "latest_rows_for_date",
    "record_baostock_daily_k_rows",
    "ts_code_from_baostock_code",
]
