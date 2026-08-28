"""Read-only source-assignment gap recon. Does not cut primaries.

Measures remaining assignment rows that have a sibling API, or a
falsifiable ``no equivalent`` claim. Accepted / DataAccess publication
is the ruler. Empty recon is not a match. Same name is not identity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from services.data_sources.fina_margin_recon import (
    compact_yyyymmdd,
    normalize_ts_code,
    sql_table,
)

SAMPLE_LIMIT = 20
PRICE_ABS_TOL = 0.011
PE_REL_TOL = 0.02
PE_ABS_TOL = 0.05
LHB_DAY_GRAIN = "trade_date_x_ts_code"
SEAT_GRAIN = "trade_date_x_ts_code_x_seat_x_side"
DIM_TABLE = "dim_active_a_stock"
DAILY_BASIC = "raw_tushare_daily_basic"
TOP_LIST = "raw_tushare_top_list"
TOP_INST_FACT = "fact_top_inst_seat_daily"
HOLDERNUMBER = "raw_tushare_stk_holdernumber"
BLOCK_TRADE = "raw_tushare_block_trade"
SHARE_FLOAT = "raw_tushare_share_float"
SURVEY = "raw_tushare_stk_surv"
LIMIT_FACT = "fact_stock_limit_daily"
INDEX_FACT = "fact_index_daily"
FUYAO_DUMP_KINDS = ("daily-k", "daily-k-10d", "adjustment-factors")
FUYAO_VALUATION_FIELDS = frozenset(
    {"pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm"}
)
DAILY_BASIC_ABSENT_FROM_FUYAO_SNAPSHOT = (
    "turnover_rate_f",
    "circ_mv",
    "total_mv",
    "float_share",
    "free_share",
    "volume_ratio",
)
BANNED_CODESET_BASELINE = frozenset({"raw_tushare_daily"})


def reject_banned_codeset_baseline(table: str) -> str:
    name = str(table).split(".")[-1].strip('"')
    if name in BANNED_CODESET_BASELINE:
        raise ValueError(
            f"banned codeset baseline {table!r}; use {DIM_TABLE} "
            "(raw_tushare_daily is fill, not listing identity)"
        )
    return table


def dim_to_ts_code(stock_code: Any, market: Any) -> str | None:
    ticker = str(stock_code or "").strip()
    exch = str(market or "").strip().upper()
    if ticker.isdigit():
        ticker = ticker.zfill(6)
    if not ticker or exch not in {"SH", "SZ"}:
        return None
    return f"{ticker}.{exch}"


_SHANGHAI = ZoneInfo("Asia/Shanghai")


def shanghai_day_from_ms(ms: Any) -> str | None:
    try:
        n = int(ms)
    except (TypeError, ValueError):
        return None
    if 19_000_101 <= n <= 21_123_131:
        return str(n)
    if n < 10_000_000_000:
        return None
    return datetime.fromtimestamp(n / 1000, tz=_SHANGHAI).date().strftime("%Y%m%d")


def shanghai_midnight_ms(yyyymmdd: str) -> int:
    compact = compact_yyyymmdd(yyyymmdd)
    if not compact or len(compact) != 8:
        raise ValueError(f"bad day {yyyymmdd!r}")
    day = datetime.strptime(compact, "%Y%m%d").replace(tzinfo=_SHANGHAI)
    return int(day.timestamp() * 1000)


def normalize_cn_name(value: Any) -> str:
    text = str(value or "").strip()
    return (
        text.replace("（", "(")
        .replace("）", ")")
        .replace(" ", "")
    )


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cell(row: Any, idx: int, key: str) -> Any:
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    return row[idx]


def compare_sets(
    left: Iterable[Any],
    right: Iterable[Any],
    *,
    grain: str,
    left_name: str,
    right_name: str,
    same_product: bool,
    sample_limit: int = SAMPLE_LIMIT,
) -> dict[str, Any]:
    left_s = {x for x in left if x not in (None, "")}
    right_s = {x for x in right if x not in (None, "")}
    only_left = sorted(left_s - right_s)
    only_right = sorted(right_s - left_s)
    both = left_s & right_s
    union = left_s | right_s
    if not left_s and not right_s:
        status = "empty_recon"
        jaccard = None
        identity = False
    else:
        status = "compared"
        jaccard = (len(both) / len(union)) if union else None
        identity = bool(
            same_product and only_left == [] and only_right == [] and both
        )
    return {
        "status": status,
        "grain": grain,
        "left": left_name,
        "right": right_name,
        "same_product": same_product,
        "left_n": len(left_s),
        "right_n": len(right_s),
        "intersection": len(both),
        "only_left": len(only_left),
        "only_right": len(only_right),
        "only_left_sample": only_left[:sample_limit],
        "only_right_sample": only_right[:sample_limit],
        "jaccard": jaccard,
        "identity": identity,
        "primary_cut": False,
    }


def numeric_near(left: Any, right: Any, *, rel: float, abs_tol: float) -> bool:
    a = _as_float(left)
    b = _as_float(right)
    if a is None or b is None:
        return False
    return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b), 1e-12))


def compare_valuation_snapshot(
    fuyao_rows: Sequence[Mapping[str, Any]],
    basic_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    basic_by = {
        normalize_ts_code(r.get("ts_code")): r
        for r in basic_rows
        if normalize_ts_code(r.get("ts_code"))
    }
    pairs = []
    match = 0
    mismatch = 0
    missing_right = 0
    for row in fuyao_rows:
        code = normalize_ts_code(row.get("thscode") or row.get("ts_code"))
        if not code:
            continue
        basic = basic_by.get(code)
        if basic is None:
            missing_right += 1
            continue
        item = {"ts_code": code, "fields": {}}
        field_ok = True
        for fy_key, ts_key in (
            ("pe_ttm", "pe_ttm"),
            ("pb_mrq", "pb"),
            ("ps_ttm", "ps_ttm"),
        ):
            left = row.get(fy_key)
            right = basic.get(ts_key)
            near = numeric_near(left, right, rel=PE_REL_TOL, abs_tol=PE_ABS_TOL)
            item["fields"][fy_key] = {
                "fuyao": left,
                "daily_basic": right,
                "near": near,
            }
            if left is None or right is None:
                field_ok = False
            elif not near:
                field_ok = False
                mismatch += 1
        if field_ok:
            match += 1
        pairs.append(item)
    return {
        "status": "compared" if pairs else "empty_recon",
        "grain": "latest_snapshot_x_ts_code",
        "same_product": False,
        "identity": False,
        "reason": (
            "Fuyao valuations/snapshot is latest-only five ratios; "
            "daily_basic is a daily panel with turnover/share/mv. "
            "pe_ttm/pb may be near without being the same product."
        ),
        "n": len(pairs),
        "field_match_rows": match,
        "field_mismatch_fields": mismatch,
        "missing_daily_basic": missing_right,
        "fuyao_fields": sorted(FUYAO_VALUATION_FIELDS),
        "daily_basic_absent_from_fuyao": list(DAILY_BASIC_ABSENT_FROM_FUYAO_SNAPSHOT),
        "samples": pairs[:SAMPLE_LIMIT],
        "primary_cut": False,
    }


def product_mismatches() -> list[dict[str, Any]]:
    """Falsifiable no-equivalent claims. These are measurements, not guesses."""
    rows = [
        {
            "domain": "stk_holdertrade",
            "challenger": "RPT_F10_SHAREHOLDER_CHANGE",
            "left_grain": "ann_date_x_ts_code_x_holder_name",
            "right_grain": "end_date_x_ts_code_x_holder_rank",
            "reason": "TuShare 增减持是公告事件; 妙想 SHAREHOLDER_CHANGE 是十大股东季度差分",
        },
        {
            "domain": "stk_holdertrade",
            "challenger": "RPT_EXECUTIVE_HOLD_DETAILS",
            "left_grain": "ann_date_x_ts_code_x_holder_name",
            "right_grain": "change_date_x_person_name",
            "reason": "高管持股变动 ≠ 股东增减持; holder_type=G 也不是同一披露文件",
        },
        {
            "domain": "forecast",
            "challenger": "RPT_HSF10_RES_ORGPREDICT",
            "left_grain": "ann_date_x_ts_code_x_end_date_performance_notice",
            "right_grain": "org_analyst_predict_by_year",
            "reason": "业绩预告 ≠ 机构盈利预测",
        },
        {
            "domain": "daily_basic.turnover_rate_f",
            "challenger": "fuyao valuations/snapshot",
            "left_grain": "trade_date_x_ts_code_turnover_and_share",
            "right_grain": "latest_five_ratios",
            "reason": "扶摇估值快照无换手/股本/市值; dump kinds 也没有 daily_basic",
        },
        {
            "domain": "moneyflow_dc",
            "challenger": "fuyao dump",
            "left_grain": "trade_date_x_ts_code_eastmoney_flow",
            "right_grain": None,
            "reason": "DumpKind 只有 daily-k / daily-k-10d / adjustment-factors",
        },
        {
            "domain": "report_rc",
            "challenger": "RPT_HSF10_RES_ORGRATING",
            "left_grain": "report_date_x_ts_code_x_rating_tp",
            "right_grain": "org_rating_stats",
            "reason": "券商研报目标价明细 ≠ 评级统计包; 未当作同一产品对账",
        },
        {
            "domain": "adj_factor",
            "challenger": "fuyao adjustment-factors dump",
            "left_grain": "trade_date_x_ts_code_daily_factor",
            "right_grain": "ex_date_x_ts_code_corporate_action",
            "reason": "扶摇是公司行为事件流, 不是 TuShare 日频 adj_factor; 见刀1 events",
        },
    ]
    for row in rows:
        row["identity"] = False
        row["status"] = "product_mismatch"
        row["primary_cut"] = False
    return rows


def fuyao_dump_coverage() -> dict[str, Any]:
    return {
        "kinds": list(FUYAO_DUMP_KINDS),
        "has_daily_basic": False,
        "has_moneyflow": False,
        "has_index_dump": False,
        "identity": False,
        "primary_cut": False,
    }


def load_dim_active_ts_codes(con: Any, table: str = DIM_TABLE) -> list[str]:
    reject_banned_codeset_baseline(table)
    rows = con.execute(
        f"SELECT stock_code, market FROM {sql_table(table)}"
    ).fetchall()
    codes = []
    for row in rows:
        ts = dim_to_ts_code(_cell(row, 0, "stock_code"), _cell(row, 1, "market"))
        if ts:
            codes.append(ts)
    return codes


def load_codes_for_day(
    con: Any,
    table: str,
    day: str,
    *,
    date_col: str,
    code_col: str = "ts_code",
) -> list[str]:
    compact = compact_yyyymmdd(day)
    if not compact:
        return []
    dashed = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    rows = con.execute(
        f"""
        SELECT {code_col}
        FROM {sql_table(table)}
        WHERE replace(CAST({date_col} AS VARCHAR), '-', '') = ?
           OR CAST({date_col} AS VARCHAR) = ?
        """,
        [compact, dashed],
    ).fetchall()
    out = []
    for row in rows:
        ts = normalize_ts_code(_cell(row, 0, code_col))
        if ts:
            out.append(ts)
    return out


def load_limit_up_codes(con: Any, day: str, table: str = LIMIT_FACT) -> list[str]:
    compact = compact_yyyymmdd(day)
    if not compact:
        return []
    dashed = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    rows = con.execute(
        f"""
        SELECT ts_code
        FROM {sql_table(table)}
        WHERE "limit" = 'U'
          AND (
            replace(CAST(trade_date AS VARCHAR), '-', '') = ?
            OR CAST(trade_date AS VARCHAR) = ?
          )
        """,
        [compact, dashed],
    ).fetchall()
    return [
        ts
        for ts in (normalize_ts_code(_cell(r, 0, "ts_code")) for r in rows)
        if ts
    ]


def load_index_closes(
    con: Any,
    ts_code: str,
    *,
    table: str = INDEX_FACT,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = con.execute(
        f"""
        SELECT trade_date, close
        FROM {sql_table(table)}
        WHERE ts_code = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        [ts_code, int(limit)],
    ).fetchall()
    out = []
    for row in rows:
        day = compact_yyyymmdd(_cell(row, 0, "trade_date"))
        if not day:
            continue
        out.append({"ts_code": ts_code, "trade_date": day, "close": _cell(row, 1, "close")})
    return out


def load_latest_daily_basic(
    con: Any,
    codes: Sequence[str],
    table: str = DAILY_BASIC,
) -> list[dict[str, Any]]:
    if not codes:
        return []
    placeholders = ",".join(["?"] * len(codes))
    rows = con.execute(
        f"""
        WITH latest AS (
          SELECT max(CAST(trade_date AS VARCHAR)) AS d FROM {sql_table(table)}
          WHERE ts_code IN ({placeholders})
        )
        SELECT ts_code, trade_date, pe_ttm, pe, pb, ps_ttm, turnover_rate_f,
               circ_mv, total_mv, float_share
        FROM {sql_table(table)}
        WHERE ts_code IN ({placeholders})
          AND CAST(trade_date AS VARCHAR) = (SELECT d FROM latest)
        """,
        list(codes) + list(codes),
    ).fetchall()
    keys = (
        "ts_code",
        "trade_date",
        "pe_ttm",
        "pe",
        "pb",
        "ps_ttm",
        "turnover_rate_f",
        "circ_mv",
        "total_mv",
        "float_share",
    )
    out = []
    for row in rows:
        item = {k: _cell(row, i, k) for i, k in enumerate(keys)}
        item["ts_code"] = normalize_ts_code(item["ts_code"])
        item["trade_date"] = compact_yyyymmdd(item["trade_date"])
        out.append(item)
    return out


def load_holdernumber_sample(
    con: Any,
    ts_code: str,
    table: str = HOLDERNUMBER,
) -> dict[str, Any] | None:
    rows = con.execute(
        f"""
        SELECT ts_code, ann_date, end_date, holder_num
        FROM {sql_table(table)}
        WHERE ts_code = ?
        ORDER BY CAST(ann_date AS VARCHAR) DESC
        LIMIT 1
        """,
        [ts_code],
    ).fetchall()
    if not rows:
        return None
    row = rows[0]
    return {
        "ts_code": normalize_ts_code(_cell(row, 0, "ts_code")),
        "ann_date": compact_yyyymmdd(_cell(row, 1, "ann_date")),
        "end_date": compact_yyyymmdd(_cell(row, 2, "end_date")),
        "holder_num": _as_float(_cell(row, 3, "holder_num")),
    }


def load_share_float_stock_days(
    con: Any,
    day: str,
    table: str = SHARE_FLOAT,
) -> list[str]:
    compact = compact_yyyymmdd(day)
    if not compact:
        return []
    rows = con.execute(
        f"""
        SELECT DISTINCT ts_code
        FROM {sql_table(table)}
        WHERE replace(CAST(float_date AS VARCHAR), '-', '') = ?
        """,
        [compact],
    ).fetchall()
    return [
        ts
        for ts in (normalize_ts_code(_cell(r, 0, "ts_code")) for r in rows)
        if ts
    ]


def load_survey_stock_days(
    con: Any,
    day: str,
    table: str = SURVEY,
) -> list[str]:
    compact = compact_yyyymmdd(day)
    if not compact:
        return []
    rows = con.execute(
        f"""
        SELECT DISTINCT ts_code
        FROM {sql_table(table)}
        WHERE replace(CAST(surv_date AS VARCHAR), '-', '') = ?
        """,
        [compact],
    ).fetchall()
    return [
        ts
        for ts in (normalize_ts_code(_cell(r, 0, "ts_code")) for r in rows)
        if ts
    ]


def load_block_keys(
    con: Any,
    day: str,
    table: str = BLOCK_TRADE,
) -> list[tuple[str, str, str]]:
    compact = compact_yyyymmdd(day)
    if not compact:
        return []
    dashed = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    rows = con.execute(
        f"""
        SELECT ts_code, buyer, seller
        FROM {sql_table(table)}
        WHERE replace(CAST(trade_date AS VARCHAR), '-', '') = ?
           OR CAST(trade_date AS VARCHAR) = ?
        """,
        [compact, dashed],
    ).fetchall()
    keys = []
    for row in rows:
        ts = normalize_ts_code(_cell(row, 0, "ts_code"))
        if not ts:
            continue
        buyer = normalize_cn_name(_cell(row, 1, "buyer"))
        seller = normalize_cn_name(_cell(row, 2, "seller"))
        keys.append((ts, buyer, seller))
    return keys


def load_seat_keys(
    con: Any,
    day: str,
    table: str = TOP_INST_FACT,
) -> list[tuple[str, str, str]]:
    compact = compact_yyyymmdd(day)
    if not compact:
        return []
    dashed = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    rows = con.execute(
        f"""
        SELECT ts_code, exalter, side
        FROM {sql_table(table)}
        WHERE replace(CAST(trade_date AS VARCHAR), '-', '') = ?
           OR CAST(trade_date AS VARCHAR) = ?
        """,
        [compact, dashed],
    ).fetchall()
    keys = []
    for row in rows:
        ts = normalize_ts_code(_cell(row, 0, "ts_code"))
        if not ts:
            continue
        seat = normalize_cn_name(_cell(row, 1, "exalter"))
        side = str(_cell(row, 2, "side") or "").strip()
        keys.append((ts, seat, side))
    return keys


def miaoxiang_codes(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    out = []
    for row in rows:
        ts = normalize_ts_code(row.get("SECUCODE") or row.get("DERIVE_SECURITY_CODE"))
        if ts:
            out.append(ts)
    return out


def miaoxiang_seat_keys(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, str, str]]:
    keys = []
    for row in rows:
        ts = normalize_ts_code(row.get("SECUCODE"))
        if not ts:
            continue
        seat = normalize_cn_name(row.get("OPERATEDEPT_NAME"))
        side = str(row.get("TRADE_DIRECTION") or "").strip()
        keys.append((ts, seat, side))
    return keys


def miaoxiang_block_keys(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, str, str]]:
    keys = []
    for row in rows:
        ts = normalize_ts_code(row.get("SECUCODE"))
        if not ts:
            continue
        buyer = normalize_cn_name(row.get("BUYER_NAME"))
        seller = normalize_cn_name(row.get("SELLER_NAME"))
        keys.append((ts, buyer, seller))
    return keys


def compare_holdernumber_sample(
    local: Mapping[str, Any] | None,
    miaoxiang: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not local or not miaoxiang:
        return {
            "status": "empty_recon",
            "identity": False,
            "primary_cut": False,
            "grain": "end_date_x_ts_code",
        }
    end_match = compact_yyyymmdd(local.get("end_date")) == compact_yyyymmdd(
        miaoxiang.get("end_date")
    )
    num_near = numeric_near(
        local.get("holder_num"),
        miaoxiang.get("holder_num"),
        rel=0.0,
        abs_tol=0.5,
    )
    ann_match = compact_yyyymmdd(local.get("ann_date")) == compact_yyyymmdd(
        miaoxiang.get("ann_date")
    )
    return {
        "status": "compared",
        "identity": False,
        "primary_cut": False,
        "grain": "end_date_x_ts_code",
        "same_product": True,
        "end_date_match": end_match,
        "holder_num_exact": num_near,
        "ann_notice_match": ann_match,
        "reason": (
            "户数产品可对 end_date×holder_num; PIT 仍是公告日 "
            "(ann_date vs NOTICE_DATE), 数字对上也不切 primary"
        ),
        "local": dict(local),
        "miaoxiang": dict(miaoxiang),
    }


def compare_index_closes(
    accepted: Sequence[Mapping[str, Any]],
    fuyao: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    fy = {
        compact_yyyymmdd(r.get("trade_date")): _as_float(r.get("close"))
        for r in fuyao
        if compact_yyyymmdd(r.get("trade_date"))
    }
    acc = {
        compact_yyyymmdd(r.get("trade_date")): _as_float(r.get("close"))
        for r in accepted
        if compact_yyyymmdd(r.get("trade_date"))
    }
    days = sorted(set(fy) & set(acc))
    match = 0
    mismatch = 0
    samples = []
    for day in days:
        a = acc[day]
        b = fy[day]
        ok = (
            a is not None
            and b is not None
            and abs(a - b) <= PRICE_ABS_TOL
        )
        if ok:
            match += 1
        else:
            mismatch += 1
            if len(samples) < SAMPLE_LIMIT:
                samples.append({"trade_date": day, "accepted": a, "fuyao": b})
    return {
        "status": "compared" if days else "empty_recon",
        "identity": bool(days and mismatch == 0),
        "same_product": True,
        "grain": "trade_date_x_index_code_close",
        "intersection_days": len(days),
        "close_match": match,
        "close_mismatch": mismatch,
        "mismatch_samples": samples,
        "primary_cut": False,
        "note": "sample window only; not 10y. Index has no adjust.",
    }


def parse_fuyao_index_bars(payload: Any, *, ts_code: str) -> list[dict[str, Any]]:
    data = payload if isinstance(payload, Mapping) else {}
    items = data.get("item") if isinstance(data.get("item"), list) else []
    out = []
    for row in items:
        if not isinstance(row, Mapping):
            continue
        day = shanghai_day_from_ms(row.get("date_ms")) or compact_yyyymmdd(
            row.get("trade_date")
        )
        close = _as_float(row.get("close_price") or row.get("close"))
        if not day or close is None:
            continue
        out.append({"ts_code": ts_code, "trade_date": day, "close": close})
    return out


def parse_fuyao_limit_pool(payload: Any) -> list[str]:
    data = payload if isinstance(payload, Mapping) else {}
    items = data.get("item") if isinstance(data.get("item"), list) else []
    codes = []
    for row in items:
        if not isinstance(row, Mapping):
            continue
        ts = normalize_ts_code(row.get("thscode"))
        if ts:
            codes.append(ts)
    return codes


def parse_fuyao_lhb_codes(payload: Any) -> list[str]:
    data = payload if isinstance(payload, Mapping) else {}
    items = data.get("stock_items") if isinstance(data.get("stock_items"), list) else []
    codes = []
    for row in items:
        if not isinstance(row, Mapping):
            continue
        ts = normalize_ts_code(row.get("thscode"))
        if ts:
            codes.append(ts)
    return codes


def parse_fuyao_tickers(payload: Any) -> list[str]:
    data = payload if isinstance(payload, Mapping) else {}
    items = data.get("item") if isinstance(data.get("item"), list) else []
    codes = []
    for row in items:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("asset_type") or "") not in {"", "a-share"}:
            continue
        ts = normalize_ts_code(row.get("thscode"))
        if ts and ts.endswith((".SH", ".SZ")):
            codes.append(ts)
    return codes


def parse_miaoxiang_holdernumber(
    rows: Sequence[Mapping[str, Any]],
    ts_code: str,
) -> dict[str, Any] | None:
    want = normalize_ts_code(ts_code)
    best = None
    for row in rows:
        code = normalize_ts_code(row.get("SECUCODE"))
        if code != want:
            continue
        item = {
            "ts_code": code,
            "ann_date": compact_yyyymmdd(row.get("NOTICE_DATE")),
            "end_date": compact_yyyymmdd(row.get("END_DATE")),
            "holder_num": _as_float(row.get("HOLDER_TOTAL_NUM")),
        }
        if best is None:
            best = item
            continue
        if (item["end_date"] or "") > (best["end_date"] or ""):
            best = item
    return best


def build_report(sections: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),  # rule-compliance: ok evidence=audit metadata, not trade_date
        "primary_cut": False,
        "contract": {
            "ruler": "accepted_or_data_access_publication",
            "banned_codeset_baseline": sorted(BANNED_CODESET_BASELINE),
            "empty_recon_is_not_match": True,
        },
        "product_mismatches": product_mismatches(),
        "fuyao_dump_coverage": fuyao_dump_coverage(),
        **dict(sections),
    }


__all__ = [
    "DAILY_BASIC_ABSENT_FROM_FUYAO_SNAPSHOT",
    "build_report",
    "compare_holdernumber_sample",
    "compare_index_closes",
    "compare_sets",
    "compare_valuation_snapshot",
    "dim_to_ts_code",
    "fuyao_dump_coverage",
    "load_block_keys",
    "load_codes_for_day",
    "load_dim_active_ts_codes",
    "load_holdernumber_sample",
    "load_index_closes",
    "load_latest_daily_basic",
    "load_limit_up_codes",
    "load_seat_keys",
    "load_share_float_stock_days",
    "load_survey_stock_days",
    "miaoxiang_block_keys",
    "miaoxiang_codes",
    "miaoxiang_seat_keys",
    "parse_fuyao_index_bars",
    "parse_fuyao_lhb_codes",
    "parse_fuyao_limit_pool",
    "parse_fuyao_tickers",
    "parse_miaoxiang_holdernumber",
    "product_mismatches",
    "normalize_cn_name",
    "reject_banned_codeset_baseline",
    "shanghai_day_from_ms",
    "shanghai_midnight_ms",
]
