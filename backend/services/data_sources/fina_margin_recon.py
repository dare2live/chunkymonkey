"""Read-only finance + margin recon. Does not cut primaries.

Rulers:
- Exchange margin = ``canonical_margin_exchange_daily`` SSE+SZSE.
  ``raw_tushare_margin`` is compatibility residual. Stock-level
  ``raw_tushare_margin_detail`` is a different grain — a matching sum is
  still not exchange identity. BSE is not part of the 沪深 ruler.
- Income / balancesheet = landing orphans (no accepted publication).
  Sample vs 妙想 GINCOME/GBALANCE on (ts_code, end_date). PIT = announcement
  date (``f_ann_date`` / ``NOTICE_DATE``), never report-period end.
- fina_indicator is DataAccess L0 on raw, still not accepted publication.
- Deleted gpcw / tdx F10 tables are not baselines. No primary cut.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

CANONICAL_MARGIN = "canonical_margin_exchange_daily"
INCOME_LANDING = "raw_tushare_income"
BALANCESHEET_LANDING = "raw_tushare_balancesheet"
FINA_INDICATOR_LANDING = "raw_tushare_fina_indicator"
MARGIN_DETAIL = "raw_tushare_margin_detail"
EXCHANGE_RULER = frozenset({"SSE", "SZSE"})
BANNED_EXCHANGE_BASELINE = frozenset(
    {"raw_tushare_margin", "raw_tushare_margin_detail"}
)
BANNED_GPCW = frozenset(
    {
        "raw_gpcw_financial",
        "raw_gpcw_detail",
        "raw_gpcw_idx",
        "raw_gpcw_8q",
    }
)
REPORT_END_ANCHORS = frozenset({"end_date", "REPORT_DATE", "report_date"})
INCOME_FIELD_PAIRS = (
    ("total_revenue", "TOTAL_OPERATE_INCOME"),
    ("n_income_attr_p", "PARENT_NETPROFIT"),
)
BALANCE_FIELD_PAIRS = (
    ("total_assets", "TOTAL_ASSETS"),
    ("total_liab", "TOTAL_LIABILITIES"),
    ("contract_liab", "CONTRACT_LIAB"),
)
MAINFINA_FIELD_PAIRS = (
    ("roe", "ROEJQ"),
    ("or_yoy", "TOTALOPERATEREVETZ"),
    ("grossprofit_margin", "XSMLL"),
    ("debt_to_assets", "ZCFZL"),
)
_KNOWN_SCALES = (10000.0, 1000.0, 100.0, 0.01, 0.001, 0.0001)
_TABLE_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHANGHAI = timezone(timedelta(hours=8))


def _table_leaf(table: str) -> str:
    return str(table).split(".")[-1].strip('"')


def sql_table(table: str) -> str:
    parts = [p.strip('"') for p in str(table).split(".") if p.strip('"')]
    if not parts or any(not _TABLE_PART.fullmatch(p) for p in parts):
        raise ValueError(f"bad table identifier: {table!r}")
    return ".".join(f'"{p}"' for p in parts)


def compact_yyyymmdd(value: Any) -> str | None:
    if isinstance(value, datetime):
        value = value.astimezone(_SHANGHAI).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    if isinstance(value, (int, float)):
        n = int(value)
        if 19_000_101 <= n <= 21_123_131:
            value = str(n)
        else:
            return None
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[:8]


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def reject_gpcw_revival(table: str) -> str:
    name = _table_leaf(table)
    if name in BANNED_GPCW or name.startswith("raw_gpcw"):
        raise ValueError(f"gpcw revival forbidden: {table!r}")
    return table


def reject_report_end_as_pit(anchor: str) -> str:
    if str(anchor) in REPORT_END_ANCHORS:
        raise ValueError("PIT is announcement date, not report-period end")
    return str(anchor)


def fina_publication_status(kind: str) -> dict[str, Any]:
    if kind == "income":
        return {
            "status": "sync_orphan",
            "baseline": None,
            "landing": INCOME_LANDING,
            "reason": (
                "raw_tushare_income is landing residual; no accepted "
                "publication plane. Recon samples landing vs 妙想 only."
            ),
        }
    if kind == "balancesheet":
        return {
            "status": "sync_orphan",
            "baseline": None,
            "landing": BALANCESHEET_LANDING,
            "reason": (
                "raw_tushare_balancesheet is landing residual; no accepted "
                "publication plane. Recon samples landing vs 妙想 only."
            ),
        }
    if kind == "fina_indicator":
        return {
            "status": "data_access_l0",
            "baseline": None,
            "landing": FINA_INDICATOR_LANDING,
            "reason": (
                "fina_indicator is DataAccess L0 on raw (ann_date PIT), "
                "not an accepted publication plane"
            ),
        }
    if kind == "margin_detail":
        return {
            "status": "blocked_no_publication",
            "baseline": None,
            "landing": MARGIN_DETAIL,
            "reason": (
                "stock-level margin detail is not exchange publication; "
                "ruler is canonical SSE+SZSE"
            ),
        }
    raise ValueError(f"unknown fina/margin kind {kind!r}")


def normalize_ts_code(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if not raw:
        return None
    if "." in raw:
        ticker, exch = raw.split(".", 1)
        ticker = ticker.zfill(6) if ticker.isdigit() else ticker
        exch = exch.strip()
        if not ticker or not exch:
            return None
        return f"{ticker}.{exch}"
    if raw.isdigit() and len(raw) <= 6:
        return raw.zfill(6)
    return raw


def miaoxiang_payload_rows(payload: Any) -> list[dict[str, Any]]:
    """Eastmoney F10 JSON. v0 often has rows in ``result.data``, not top-level data."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    result = payload.get("result")
    if isinstance(result, Mapping) and isinstance(result.get("data"), list):
        return [row for row in result["data"] if isinstance(row, Mapping)]
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    return []


def parse_miaoxiang_finance_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        code = normalize_ts_code(row.get("SECUCODE") or row.get("SECURITY_CODE"))
        end = compact_yyyymmdd(row.get("REPORT_DATE"))
        ann = compact_yyyymmdd(row.get("NOTICE_DATE"))
        if not code or not end:
            continue
        item = {
            "ts_code": code,
            "end_date": end,
            "ann_date": ann,
            "payload": dict(row),
        }
        key = (code, end)
        prev = best.get(key)
        if prev is None or (ann or "") > (prev.get("ann_date") or ""):
            best[key] = item
    return list(best.values())


def _scale_factor(left: float, right: float) -> float | None:
    if left == 0 or right == 0:
        return None
    ratio = left / right
    for scale in _KNOWN_SCALES:
        if abs(ratio - scale) / scale < 0.02:
            return scale
    return None


def compare_numeric_fields(
    left: Any,
    right: Any,
    *,
    rel_tol: float | None = None,
    abs_tol: float | None = None,
) -> dict[str, Any]:
    lft = _as_float(left)
    rgt = _as_float(right)
    if lft is None and rgt is None:
        return {"status": "both_null", "match": False}
    if lft is None or rgt is None:
        return {"status": "one_null", "match": False, "left": lft, "right": rgt}
    scale = _scale_factor(lft, rgt)
    if scale is not None and abs(scale - 1.0) > 1e-12:
        return {
            "status": "scale_mismatch",
            "match": False,
            "scale": scale,
            "left": lft,
            "right": rgt,
        }
    span = max(abs(lft), abs(rgt))
    if abs_tol is None:
        abs_tol = 1.0 if span >= 1000.0 else 0.05
    if rel_tol is None:
        rel_tol = 1e-4 if span >= 1000.0 else 1e-3
    diff = abs(lft - rgt)
    denom = max(span, 1e-12)
    rel = diff / denom
    matched = diff <= abs_tol or rel <= rel_tol
    return {
        "status": "equal" if matched else "divergent",
        "match": matched,
        "left": lft,
        "right": rgt,
        "abs_diff": diff,
        "rel_diff": rel,
    }


def compare_margin_totals(exchange: Any, detail: Any) -> dict[str, Any]:
    left = _as_float(exchange["rzrqye"] if isinstance(exchange, Mapping) else exchange)
    right = _as_float(detail["rzrqye"] if isinstance(detail, Mapping) else detail)
    if isinstance(exchange, Mapping) and exchange.get("status") == "empty_recon":
        left = None
    if isinstance(detail, Mapping) and detail.get("status") == "empty_recon":
        right = None
    if left is None or right is None:
        return {
            "status": "empty_recon",
            "identity": False,
            "jaccard": None,
            "relation": "stock_sum_is_not_exchange_publication",
            "grain_left": "exchange_sse_szse",
            "grain_right": "stock_margin_detail",
        }
    diff = abs(left - right)
    denom = max(abs(left), abs(right), 1e-12)
    return {
        "status": "compared",
        "identity": False,
        "relation": "stock_sum_is_not_exchange_publication",
        "grain_left": "exchange_sse_szse",
        "grain_right": "stock_margin_detail",
        "exchange_rzrqye": left,
        "detail_rzrqye": right,
        "abs_diff": diff,
        "rel_diff": diff / denom,
    }


def _sql_ident(name: str) -> str:
    if not _TABLE_PART.fullmatch(name):
        raise ValueError(f"bad identifier: {name!r}")
    return f'"{name}"'


def _sql_day(day: Any) -> str:
    compact = compact_yyyymmdd(day)
    if compact is None:
        raise ValueError(f"bad trade_date {day!r}")
    return compact


def latest_canonical_margin_day(
    con: Any, *, table: str = CANONICAL_MARGIN
) -> str | None:
    reject_gpcw_revival(table)
    if _table_leaf(table) != CANONICAL_MARGIN:
        raise ValueError(
            f"banned baseline {table!r}; use {CANONICAL_MARGIN} "
            "(legacy residual is not recon truth)"
        )
    row = con.execute(
        f"""
        SELECT max(replace(CAST(trade_date AS VARCHAR), '-', ''))
        FROM {sql_table(table)}
        """
    ).fetchone()
    return compact_yyyymmdd(row[0] if row else None)


def load_exchange_margin(
    con: Any,
    day: Any,
    *,
    table: str = CANONICAL_MARGIN,
) -> dict[str, Any]:
    reject_gpcw_revival(table)
    name = _table_leaf(table)
    if name == MARGIN_DETAIL:
        raise ValueError("stock-level detail is not exchange publication")
    if name != CANONICAL_MARGIN:
        raise ValueError(
            f"banned baseline {table!r}; use {CANONICAL_MARGIN} "
            "(legacy residual is not recon truth)"
        )
    compact = _sql_day(day)
    rows = con.execute(
        f"""
        SELECT exchange_id, rzrqye
        FROM {sql_table(table)}
        WHERE replace(CAST(trade_date AS VARCHAR), '-', '') = ?
        """,
        [compact],
    ).fetchall()
    per: dict[str, float] = {}
    excluded = 0.0
    bse = 0.0
    for row in rows:
        exch = str(row[0] or "").strip().upper()
        amount = _as_float(row[1]) or 0.0
        if exch in EXCHANGE_RULER:
            per[exch] = per.get(exch, 0.0) + amount
        else:
            excluded += amount
            if exch in {"BSE", "BJSE"}:
                bse += amount
    total = sum(per.values())
    if not per:
        return {
            "status": "empty_recon",
            "day": compact,
            "rzrqye": None,
            "exchanges": [],
            "n": 0,
            "excluded_bse_rzrqye": bse,
        }
    return {
        "status": "ok",
        "day": compact,
        "rzrqye": total,
        "exchanges": sorted(per),
        "n": len(per),
        "per_exchange": per,
        "excluded_other_rzrqye": excluded,
        "excluded_bse_rzrqye": bse,
    }


def load_margin_detail_sum(
    con: Any,
    day: Any,
    *,
    table: str = MARGIN_DETAIL,
) -> dict[str, Any]:
    reject_gpcw_revival(table)
    name = _table_leaf(table)
    if name != MARGIN_DETAIL:
        raise ValueError(f"margin detail landing is {MARGIN_DETAIL}, not {table!r}")
    compact = _sql_day(day)
    row = con.execute(
        f"""
        SELECT
          COUNT(*) AS n,
          SUM(CAST(rzrqye AS DOUBLE)) AS rzrqye,
          SUM(CASE WHEN ts_code LIKE '%.BJ' THEN CAST(rzrqye AS DOUBLE) ELSE 0 END) AS bj,
          SUM(CASE WHEN ts_code LIKE '%.BJ' THEN 1 ELSE 0 END) AS bj_n
        FROM {sql_table(table)}
        WHERE replace(CAST(trade_date AS VARCHAR), '-', '') = ?
        """,
        [compact],
    ).fetchone()
    n = int(row[0] or 0)
    total = _as_float(row[1])
    bj = _as_float(row[2]) or 0.0
    if n == 0 or total is None:
        return {
            "status": "empty_recon",
            "day": compact,
            "n": 0,
            "rzrqye": None,
            "rzrqye_ex_bj": None,
            "bj_rzrqye": 0.0,
            "bj_n": 0,
        }
    return {
        "status": "ok",
        "day": compact,
        "n": n,
        "rzrqye": total,
        "rzrqye_ex_bj": total - bj,
        "bj_rzrqye": bj,
        "bj_n": int(row[3] or 0),
    }


def _in_clause(codes: Sequence[str]) -> tuple[str, list[str]]:
    cleaned = [c for c in (normalize_ts_code(x) for x in codes) if c and "." in c]
    if not cleaned:
        return "", []
    return ",".join(["?"] * len(cleaned)), cleaned


def _latest_landing(
    con: Any,
    *,
    table: str,
    codes: Sequence[str],
    ann_col: str,
    value_cols: Sequence[str],
) -> list[dict[str, Any]]:
    reject_gpcw_revival(table)
    placeholders, params = _in_clause(codes)
    if not params:
        return []
    ann_sql = _sql_ident(ann_col)
    value_sql = ", ".join(_sql_ident(col) for col in value_cols)
    sql = f"""
        SELECT
          ts_code,
          replace(CAST(end_date AS VARCHAR), '-', '') AS end_date,
          replace(CAST({ann_sql} AS VARCHAR), '-', '') AS ann_date,
          {value_sql}
        FROM (
          SELECT
            ts_code,
            end_date,
            {ann_sql},
            update_flag,
            {value_sql},
            row_number() OVER (
              PARTITION BY ts_code, replace(CAST(end_date AS VARCHAR), '-', '')
              ORDER BY TRY_CAST(update_flag AS INTEGER) DESC NULLS LAST,
                       replace(CAST({ann_sql} AS VARCHAR), '-', '') DESC
            ) AS rn
          FROM {sql_table(table)}
          WHERE ts_code IN ({placeholders})
        ) t
        WHERE rn = 1
    """
    rows = con.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "ts_code": normalize_ts_code(row[0]),
            "end_date": compact_yyyymmdd(row[1]),
            "ann_date": compact_yyyymmdd(row[2]),
        }
        for idx, col in enumerate(value_cols, start=3):
            item[col] = _as_float(row[idx])
        if item["ts_code"] and item["end_date"]:
            out.append(item)
    return out


def load_landing_income(
    con: Any,
    codes: Sequence[str],
    *,
    table: str = INCOME_LANDING,
) -> list[dict[str, Any]]:
    if _table_leaf(table) != INCOME_LANDING:
        reject_gpcw_revival(table)
        raise ValueError(f"income landing is {INCOME_LANDING}, not {table!r}")
    return _latest_landing(
        con,
        table=table,
        codes=codes,
        ann_col="f_ann_date",
        value_cols=("total_revenue", "n_income_attr_p"),
    )


def load_landing_balancesheet(
    con: Any,
    codes: Sequence[str],
    *,
    table: str = BALANCESHEET_LANDING,
) -> list[dict[str, Any]]:
    if _table_leaf(table) != BALANCESHEET_LANDING:
        reject_gpcw_revival(table)
        raise ValueError(f"balancesheet landing is {BALANCESHEET_LANDING}, not {table!r}")
    return _latest_landing(
        con,
        table=table,
        codes=codes,
        ann_col="f_ann_date",
        value_cols=("total_assets", "total_liab", "contract_liab"),
    )


def load_landing_fina_indicator(
    con: Any,
    codes: Sequence[str],
    *,
    table: str = FINA_INDICATOR_LANDING,
) -> list[dict[str, Any]]:
    if _table_leaf(table) != FINA_INDICATOR_LANDING:
        reject_gpcw_revival(table)
        raise ValueError(
            f"fina_indicator landing is {FINA_INDICATOR_LANDING}, not {table!r}"
        )
    return _latest_landing(
        con,
        table=table,
        codes=codes,
        ann_col="ann_date",
        value_cols=("roe", "or_yoy", "grossprofit_margin", "debt_to_assets"),
    )


def _normalize_landing_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["ts_code"] = normalize_ts_code(item.get("ts_code"))
        item["end_date"] = compact_yyyymmdd(item.get("end_date"))
        item["ann_date"] = compact_yyyymmdd(item.get("ann_date"))
        if item["ts_code"] and item["end_date"]:
            out.append(item)
    return out


def _source_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    first = rows[0]
    if "SECUCODE" in first or "REPORT_DATE" in first:
        return parse_miaoxiang_finance_rows(rows)
    return _normalize_landing_rows(rows)


def compare_statement_sample(
    landing: Sequence[Mapping[str, Any]],
    source: Sequence[Mapping[str, Any]],
    *,
    field_pairs: Sequence[tuple[str, str]],
    left_ns: str,
    right_ns: str,
    pit_left: str,
    pit_right: str,
) -> dict[str, Any]:
    reject_report_end_as_pit(pit_left)
    reject_report_end_as_pit(pit_right)
    left_rows = _normalize_landing_rows(landing)
    right_rows = _source_rows(source)
    left_map = {(r["ts_code"], r["end_date"]): r for r in left_rows}
    right_map = {(r["ts_code"], r["end_date"]): r for r in right_rows}
    keys = sorted(left_map.keys() & right_map.keys())
    if not keys:
        return {
            "status": "empty_recon",
            "identity": False,
            "periods": 0,
            "landing_n": len(left_rows),
            "source_n": len(right_rows),
            "left_ns": left_ns,
            "right_ns": right_ns,
        }
    per_period: list[dict[str, Any]] = []
    for key in keys:
        left = left_map[key]
        right = right_map[key]
        payload = right.get("payload") or right
        fields = {}
        for left_name, right_name in field_pairs:
            fields[left_name] = compare_numeric_fields(
                left.get(left_name), payload.get(right_name)
            )
        per_period.append(
            {
                "ts_code": key[0],
                "end_date": key[1],
                "ann_left": left.get("ann_date"),
                "ann_right": right.get("ann_date"),
                "pit_left": pit_left,
                "pit_right": pit_right,
                "fields": fields,
                "identity": False,
                "relation": "vendor_landing_candidate",
            }
        )
    return {
        "status": "compared",
        "identity": False,
        "periods": len(per_period),
        "landing_n": len(left_rows),
        "source_n": len(right_rows),
        "left_ns": left_ns,
        "right_ns": right_ns,
        "per_period": per_period,
    }


def compare_income_sample(
    landing: Sequence[Mapping[str, Any]],
    source: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return compare_statement_sample(
        landing,
        source,
        field_pairs=INCOME_FIELD_PAIRS,
        left_ns=INCOME_LANDING,
        right_ns="miaoxiang_gincome",
        pit_left="f_ann_date",
        pit_right="NOTICE_DATE",
    )


def compare_balancesheet_sample(
    landing: Sequence[Mapping[str, Any]],
    source: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return compare_statement_sample(
        landing,
        source,
        field_pairs=BALANCE_FIELD_PAIRS,
        left_ns=BALANCESHEET_LANDING,
        right_ns="miaoxiang_gbalance",
        pit_left="f_ann_date",
        pit_right="NOTICE_DATE",
    )


def compare_mainfina_sample(
    landing: Sequence[Mapping[str, Any]],
    source: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return compare_statement_sample(
        landing,
        source,
        field_pairs=MAINFINA_FIELD_PAIRS,
        left_ns=FINA_INDICATOR_LANDING,
        right_ns="miaoxiang_mainfinadata",
        pit_left="ann_date",
        pit_right="NOTICE_DATE",
    )


__all__ = [
    "BANNED_EXCHANGE_BASELINE",
    "BANNED_GPCW",
    "CANONICAL_MARGIN",
    "INCOME_LANDING",
    "compare_balancesheet_sample",
    "compare_income_sample",
    "compare_mainfina_sample",
    "compare_margin_totals",
    "compact_yyyymmdd",
    "fina_publication_status",
    "latest_canonical_margin_day",
    "load_exchange_margin",
    "load_landing_balancesheet",
    "load_landing_fina_indicator",
    "load_landing_income",
    "load_margin_detail_sum",
    "miaoxiang_payload_rows",
    "parse_miaoxiang_finance_rows",
    "reject_gpcw_revival",
    "reject_report_end_as_pit",
]
