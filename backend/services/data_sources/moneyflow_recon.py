"""Read-only moneyflow layer recon. Does not cut primaries.

Named layers, never summed:
- EOD vendor imbalance proxy = ``fact_stock_moneyflow_dc_daily`` (eastmoney
  via TuShare) and separately ``fact_stock_moneyflow_daily`` (tushare
  order-size). Two EOD vendors are still not one conserved flow.
- TDX MAC ``capital_flow`` (``0x1218`` / ``Stock_ZJLX``) = a third vendor
  imbalance proxy. Parallel named layer, not a TuShare replacement.
- Eastmoney datacenter F10 ``reportName`` fund-flow = probed separately.
  If no moneyflow reportName returns rows, residual is ``product_mismatch``;
  do not invent a table. Control ``RPT_MUTUAL_STOCK_HOLDRANKN_NEW`` is not
  a flow layer.
- Intraday vendor minute proxy = no accepted publication in this repo
  (eastmoney/THS 主力净流入 polling is not a research plane).
- Tick active buy/sell delta = tdxhub ``transactions`` sample, bounded,
  truncated-honest. Not identity with either EOD vendor.

qfq kline is not an input. No new TuShare domain. Sample only. Never sum
tdx MAC + eastmoney (or any other pair of) layers.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

FACT_DC = "fact_stock_moneyflow_dc_daily"
FACT_TS = "fact_stock_moneyflow_daily"
LAYER_EOD_DC = "eod_vendor_imbalance"
LAYER_EOD_TS = "eod_vendor_tushare_imbalance"
LAYER_MINUTE = "intraday_vendor_minute"
LAYER_TICK = "tick_active_imbalance"
LAYER_TDX_MAC = "tdx_mac_capital_flow"
LAYER_EM_DC = "eastmoney_datacenter_flow"
TICK_METHOD = "tdx_tick_active_delta_v1"
TDX_MAC_METHOD = "tdx_mac_0x1218_stock_zjlx"
EASTMONEY_FLOW_CANDIDATES = (
    "RPT_DMSK_FUND_FLOW",
    "RPT_F10_FUNDFLOW",
    "RPT_STOCK_FUNDFLOW",
    "RPT_F10_MAIN_FUNDFLOW",
)
EASTMONEY_CONTROL_REPORT = "RPT_MUTUAL_STOCK_HOLDRANKN_NEW"
_FLOW_COLUMN_NEEDLES = (
    "net_inflow",
    "main_net",
    "main_in",
    "fund_flow",
    "zljlr",
    "super_net",
    "large_net",
    "net_mf",
    "主力",
    "净流入",
    "netamount",
    "net_amount",
)
BUYORSSELL_CONVENTION = "0_buy_1_sell"
BANNED_TICK_MARKETS = frozenset({2})  # BJ
_TABLE_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHANGHAI = timezone(timedelta(hours=8))
_QFQ = re.compile(r"qfq", re.IGNORECASE)


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


def reject_qfq_input(table: str) -> str:
    name = _table_leaf(table)
    if _QFQ.search(name):
        raise ValueError(f"banned qfq as moneyflow input: {table!r}")
    return table


def reject_cross_source_sum(parts: Sequence[tuple[str, float]]) -> None:
    layers = {str(name) for name, _value in parts}
    if len(layers) > 1:
        raise ValueError(
            "forbidden to sum moneyflow layers across sources: "
            + ",".join(sorted(layers))
        )


def minute_vendor_status() -> dict[str, Any]:
    return {
        "layer": LAYER_MINUTE,
        "status": "blocked_no_publication",
        "accepted": False,
        "reason": (
            "no accepted eastmoney/THS minute 主力净流入 plane; "
            "polling caches are not tick truth and are not a research OS"
        ),
    }


def tdx_mac_layer_status() -> dict[str, Any]:
    return {
        "layer": LAYER_TDX_MAC,
        "status": "sample_adapter",
        "accepted": False,
        "method": TDX_MAC_METHOD,
        "vendor": "tdx_mac",
        "unit": "vendor_imbalance_unspecified",
        "sync_domain": None,
    }


def _looks_like_flow_columns(columns: Sequence[str]) -> bool:
    joined = " ".join(str(c) for c in columns).lower()
    return any(needle in joined for needle in _FLOW_COLUMN_NEEDLES)


def classify_datacenter_exception(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    text = f"{name} {exc}".lower()
    if "timeout" in name or "timed out" in text or "timeout" in text:
        return "timeout"
    if (
        "connectionerror" in name
        or "connection" in text
        or "connectfailed" in text
        or "broken pipe" in text
    ):
        return "connection_failure"
    return "http_error"


def classify_datacenter_envelope(
    report_name: str, raw: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Classify a datacenter JSON envelope. 9501 / missing result ≠ zero_rows."""
    payload = raw or {}
    success = payload.get("success")
    code = payload.get("code")
    message = str(payload.get("message") or "")
    result = payload.get("result")
    if success is False or code in {9501, "9501"} or "报表配置不存在" in message:
        return {
            "report_name": report_name,
            "status": "product_mismatch",
            "rows": 0,
            "count": 0,
            "columns": [],
            "code": code,
            "message": message[:200],
        }
    if not isinstance(result, dict):
        return classify_datacenter_payload(report_name, {"data": [], "count": 0})
    return classify_datacenter_payload(
        report_name,
        {
            "data": list(result.get("data") or []),
            "count": result.get("count"),
            "pages": result.get("pages"),
        },
    )


def classify_datacenter_payload(
    report_name: str, payload: Mapping[str, Any] | None
) -> dict[str, Any]:
    data = list((payload or {}).get("data") or [])
    count = (payload or {}).get("count")
    if not data:
        return {
            "report_name": report_name,
            "status": "zero_rows",
            "rows": 0,
            "count": count,
            "columns": [],
        }
    columns = sorted({str(k) for row in data[:1] for k in dict(row)})
    if not _looks_like_flow_columns(columns):
        return {
            "report_name": report_name,
            "status": "missing_fields",
            "rows": len(data),
            "count": count,
            "columns": columns,
        }
    return {
        "report_name": report_name,
        "status": "ok",
        "rows": len(data),
        "count": count,
        "columns": columns,
        "sample": dict(data[0]),
    }


def discover_fundflow_report_names() -> tuple[str, ...]:
    """Registry siblings that look like fund-flow, excluding cashflow/raise."""
    names = list(EASTMONEY_FLOW_CANDIDATES)
    try:
        from services.data_sources.sibling_repos import ensure_import_path

        ensure_import_path("miaoxiang")
        from aif10_scraper.registry import REPORTS  # noqa: E402
    except Exception:  # noqa: BLE001 — probe still has the K2 candidate list
        return tuple(dict.fromkeys(names))
    skip = ("CASHFLOW", "CAPITAL_RAISE", "CAPITAL_ITEM")
    for spec in REPORTS:
        name = str(getattr(spec, "name", "") or "")
        upper = name.upper()
        if any(token in upper for token in skip):
            continue
        if "FUNDFLOW" in upper or "FUND_FLOW" in upper or "MAIN_FUNDFLOW" in upper:
            names.append(name)
    return tuple(dict.fromkeys(names))


def _probe_one_report(
    client: Any,
    name: str,
    *,
    secucode: str,
    page_size: int,
) -> dict[str, Any]:
    request_json = getattr(client, "_request_json", None)
    if callable(request_json):
        try:
            from aif10_scraper.client import URL_V1
        except Exception:  # noqa: BLE001
            URL_V1 = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
        raw = request_json(
            URL_V1,
            {
                "reportName": name,
                "columns": "ALL",
                "pageNumber": 1,
                "pageSize": page_size,
                "source": "HSF10",
                "client": "PC",
                "filter": f'(SECUCODE="{secucode}")',
            },
        )
        return classify_datacenter_envelope(name, raw)
    payload = client.get_v1(name, secucode=secucode, page_size=page_size)
    return classify_datacenter_payload(name, payload)


def probe_eastmoney_datacenter_flow(
    client: Any,
    *,
    secucode: str = "600519.SH",
    page_size: int = 10,
    report_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Classify each reportName separately. 0 rows is not success."""
    candidates = (
        tuple(report_names)
        if report_names is not None
        else discover_fundflow_report_names()
    )
    rows: list[dict[str, Any]] = []
    for name in candidates:
        try:
            rows.append(
                _probe_one_report(
                    client, name, secucode=secucode, page_size=page_size
                )
            )
        except Exception as exc:  # noqa: BLE001 — classified, not swallowed
            rows.append(
                {
                    "report_name": name,
                    "status": classify_datacenter_exception(exc),
                    "rows": 0,
                    "columns": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    try:
        control = _probe_one_report(
            client,
            EASTMONEY_CONTROL_REPORT,
            secucode=secucode,
            page_size=page_size,
        )
        if control["status"] in {"missing_fields", "ok"}:
            control = {**control, "status": "ok_control"}
    except Exception as exc:  # noqa: BLE001
        control = {
            "report_name": EASTMONEY_CONTROL_REPORT,
            "status": classify_datacenter_exception(exc),
            "rows": 0,
            "columns": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    ok_layers = [item for item in rows if item.get("status") == "ok"]
    unmatched = {"zero_rows", "product_mismatch"}
    if ok_layers:
        aggregate = "ok"
        reason = "datacenter returned fund-flow rows; named layer, never summed"
    elif control.get("status") == "ok_control" and all(
        item.get("status") in unmatched for item in rows
    ):
        aggregate = "product_mismatch"
        reason = (
            "control report returned rows but no fund-flow reportName did; "
            "do not invent a table"
        )
    else:
        aggregate = "probe_failed"
        reason = "fund-flow reportNames did not return usable rows"

    return {
        "layer": LAYER_EM_DC,
        "accepted": False,
        "primary_cut": False,
        "status": aggregate,
        "reason": reason,
        "secucode": secucode,
        "candidates": rows,
        "control": control,
        "ok_report_names": [item["report_name"] for item in ok_layers],
        "sample_columns": ok_layers[0]["columns"] if ok_layers else [],
    }


def eastmoney_datacenter_flow_status(
    probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Publication stub. Without a probe this is ``unprobed``, not success.

    Live K2 probe residual (see probe_eastmoney_datacenter_flow): fund-flow
    reportNames return datacenter code 9501 「报表配置不存在」 while the HSGST
    control works. Honest status is ``product_mismatch`` — F10 datacenter has
    no TuShare 日终主力净流入 equivalent. Do not invent a table.
    """
    if probe is None:
        return {
            "layer": LAYER_EM_DC,
            "status": "unprobed",
            "accepted": False,
            "table": None,
        }
    return {
        "layer": LAYER_EM_DC,
        "status": probe.get("status"),
        "accepted": False,
        "table": None,
        "reason": probe.get("reason"),
        "ok_report_names": list(probe.get("ok_report_names") or []),
        "sample_columns": list(probe.get("sample_columns") or []),
        "primary_cut": False,
    }


def moneyflow_publication_status(
    *,
    eastmoney_probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "eod_dc": {
            "layer": LAYER_EOD_DC,
            "table": FACT_DC,
            "status": "derived_publication",
            "accepted": False,
            "unit": "CNY_10k",
            "vendor": "eastmoney_via_tushare",
        },
        "eod_tushare": {
            "layer": LAYER_EOD_TS,
            "table": FACT_TS,
            "status": "derived_publication",
            "accepted": False,
            "unit": "CNY_10k",
            "vendor": "tushare",
        },
        "tdx_mac": tdx_mac_layer_status(),
        "eastmoney_datacenter": eastmoney_datacenter_flow_status(eastmoney_probe),
        "minute": minute_vendor_status(),
        "tick": {
            "layer": LAYER_TICK,
            "status": "sample_adapter",
            "accepted": False,
            "method": TICK_METHOD,
            "unit": "price_times_lot",
        },
        "formula_winner_rate": False,
        "primary_cut": False,
    }


def tick_active_delta(ticks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    buy = sell = unknown = 0.0
    n_buy = n_sell = n_unknown = 0
    for raw in ticks:
        side = raw.get("buyorsell")
        px = _as_float(raw.get("price"))
        vol = _as_float(raw.get("vol"))
        if px is None or vol is None:
            n_unknown += 1
            continue
        notion = px * vol
        try:
            flag = int(side)
        except (TypeError, ValueError):
            unknown += notion
            n_unknown += 1
            continue
        if flag == 0:
            buy += notion
            n_buy += 1
        elif flag == 1:
            sell += notion
            n_sell += 1
        else:
            unknown += notion
            n_unknown += 1
    n = n_buy + n_sell + n_unknown
    if n == 0:
        return {
            "status": "empty_recon",
            "identity": False,
            "method": TICK_METHOD,
            "layer": LAYER_TICK,
            "buyorsell_convention": BUYORSSELL_CONVENTION,
        }
    return {
        "status": "ok",
        "method": TICK_METHOD,
        "layer": LAYER_TICK,
        "buyorsell_convention": BUYORSSELL_CONVENTION,
        "unit": "price_times_lot",
        "buy": buy,
        "sell": sell,
        "unknown": unknown,
        "delta": buy - sell,
        "n_buy": n_buy,
        "n_sell": n_sell,
        "n_unknown": n_unknown,
        "n": n,
    }


def compare_flow_layers(
    left: Mapping[str, Any] | None,
    right: Mapping[str, Any] | None,
    *,
    relation: str,
) -> dict[str, Any]:
    left_v = _as_float((left or {}).get("value"))
    right_v = _as_float((right or {}).get("value"))
    if left_v is None or right_v is None:
        return {
            "status": "empty_recon",
            "identity": False,
            "jaccard": None,
            "relation": relation,
        }
    return {
        "status": "compared",
        "identity": False,
        "relation": relation,
        "left": dict(left or {}),
        "right": dict(right or {}),
        "abs_diff": abs(left_v - right_v),
        "formula_winner_rate": False,
        "primary_cut": False,
    }


def compare_eod_vendors(dc_net: Any, ts_net: Any) -> dict[str, Any]:
    return compare_flow_layers(
        {"layer": LAYER_EOD_DC, "unit": "CNY_10k", "value": dc_net},
        {"layer": LAYER_EOD_TS, "unit": "CNY_10k", "value": ts_net},
        relation="tushare_order_size_is_not_eastmoney_net",
    )


def compare_tick_vs_eod(tick: Mapping[str, Any] | None, eod_net: Any) -> dict[str, Any]:
    delta = None if not tick or tick.get("status") != "ok" else tick.get("delta")
    return compare_flow_layers(
        {
            "layer": LAYER_TICK,
            "unit": "price_times_lot",
            "value": delta,
            "method": TICK_METHOD,
        },
        {"layer": LAYER_EOD_DC, "unit": "CNY_10k", "value": eod_net},
        relation="tick_active_delta_is_not_eod_vendor_imbalance",
    )


def latest_fact_day(con: Any, table: str = FACT_DC) -> str | None:
    reject_qfq_input(table)
    row = con.execute(f"SELECT max(trade_date) FROM {sql_table(table)}").fetchone()
    if row is None or row[0] is None:
        return None
    return compact_yyyymmdd(row[0])


def load_eod_dc(
    con: Any,
    ts_code: str,
    day: str,
    *,
    table: str = FACT_DC,
) -> dict[str, Any]:
    reject_qfq_input(table)
    if _table_leaf(table) != FACT_DC:
        raise ValueError(f"eastmoney EOD must be {FACT_DC}, got {table!r}")
    rows = con.execute(
        f"""
        SELECT trade_date, ts_code, net_amount
        FROM {sql_table(table)}
        WHERE ts_code = ?
        """,
        [ts_code],
    ).fetchall()
    for row in rows:
        if compact_yyyymmdd(row[0]) == day:
            return {
                "trade_date": day,
                "ts_code": row[1],
                "net_amount": _as_float(row[2]),
                "layer": LAYER_EOD_DC,
                "unit": "CNY_10k",
            }
    return {"status": "empty_recon", "layer": LAYER_EOD_DC, "trade_date": day, "ts_code": ts_code}


def load_eod_tushare(
    con: Any,
    ts_code: str,
    day: str,
    *,
    table: str = FACT_TS,
) -> dict[str, Any]:
    reject_qfq_input(table)
    if _table_leaf(table) != FACT_TS:
        raise ValueError(f"tushare EOD must be {FACT_TS}, got {table!r}")
    rows = con.execute(
        f"""
        SELECT trade_date, ts_code, net_mf_amount
        FROM {sql_table(table)}
        WHERE ts_code = ?
        """,
        [ts_code],
    ).fetchall()
    for row in rows:
        if compact_yyyymmdd(row[0]) == day:
            return {
                "trade_date": day,
                "ts_code": row[1],
                "net_mf_amount": _as_float(row[2]),
                "layer": LAYER_EOD_TS,
                "unit": "CNY_10k",
            }
    return {"status": "empty_recon", "layer": LAYER_EOD_TS, "trade_date": day, "ts_code": ts_code}


def _tick_records(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if getattr(raw, "empty", None) is True:
        return []
    to_dict = getattr(raw, "to_dict", None)
    if callable(to_dict):
        rows = to_dict(orient="records")
        return [dict(item) for item in rows]
    if isinstance(raw, list):
        return [dict(item) for item in raw]
    return []


def fetch_history_ticks(
    client: Any,
    ts_code: str,
    day: str,
    *,
    max_ticks: int = 4000,
    page: int = 800,
) -> dict[str, Any]:
    from services.data_sources.tdxhub_kline_recon import protocol_market

    compact = compact_yyyymmdd(day)
    if compact is None:
        raise ValueError(f"bad tick day {day!r}")
    market, code = protocol_market(ts_code)
    if market in BANNED_TICK_MARKETS:
        raise ValueError(f"BJ ticks are out of this bounded sample: {ts_code}")
    rows: list[dict[str, Any]] = []
    start = 0
    truncated = False
    while len(rows) < max_ticks:
        take = min(page, max_ticks - len(rows))
        chunk = client.transactions(symbol=code, start=start, offset=take, date=compact)
        records = _tick_records(chunk)
        if not records:
            break
        rows.extend(records)
        if len(records) < take:
            break
        start += len(records)
        if len(rows) >= max_ticks:
            truncated = True
            break
    return {
        "ts_code": ts_code,
        "trade_date": compact,
        "ticks": rows,
        "n": len(rows),
        "truncated": truncated,
        "coverage": "truncated_sample" if truncated else "page_exhausted",
    }
