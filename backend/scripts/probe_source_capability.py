#!/usr/bin/env python3
"""Probe a registered data-source capability and summarize the live result."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.data_sources import resolve  # noqa: E402
from services.source_watermarks import record_source_failure, resolve_source_failures  # noqa: E402


DATE_KEYS = (
    "日期",
    "date",
    "trade_date",
    "report_date",
    "notice_date",
    "snapshot_date",
    "as_of_date",
)

CAPABILITY_DATA_DOMAIN_HINTS = {
    "individual_fund_flow": "order_flow_fund_flow",
    "individual_fund_flow_rank": "order_flow_fund_flow",
}

CAPABILITY_SOURCE_HINTS = {
    "individual_fund_flow": "akshare",
    "individual_fund_flow_rank": "akshare",
}


def _normalize_records(data: Any) -> tuple[list[dict[str, Any]], str]:
    if data is None:
        return [], "none"

    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return [dict(row) for row in data], "records"
        return [{"value": item} for item in data], "list"

    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list) and (not data["data"] or isinstance(data["data"][0], dict)):
            return [dict(row) for row in data["data"]], "dict[data]"
        return [dict(data)], "dict"

    to_dict = getattr(data, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict("records")
        except TypeError:
            records = to_dict()
        if isinstance(records, list) and (not records or isinstance(records[0], dict)):
            return [dict(row) for row in records], type(data).__name__
        if isinstance(records, dict):
            return [dict(records)], type(data).__name__

    return [{"value": str(data)}], type(data).__name__


def _infer_date_range(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for key in DATE_KEYS:
        values = []
        for row in records:
            value = row.get(key)
            if value in (None, ""):
                continue
            values.append(str(value))
        if values:
            return {"field": key, "min": min(values), "max": max(values)}
    return None


def _summarize(data: Any) -> dict[str, Any]:
    records, kind = _normalize_records(data)
    columns: list[str] = []
    for row in records[:5]:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            if key not in columns:
                columns.append(key)

    summary: dict[str, Any] = {
        "type": kind,
        "row_count": len(records),
        "columns": columns,
        "head": records[:5],
    }
    date_range = _infer_date_range(records)
    if date_range:
        summary["date_range"] = date_range
    return summary


def probe_source_capability(
    capability: str,
    kwargs: dict[str, Any],
    *,
    prefer_source: str | None = None,
    persist_status: bool = False,
    data_domain: str | None = None,
    source_name: str | None = None,
    source_tier: int | None = None,
    stock_code: str | None = None,
) -> dict[str, Any]:
    try:
        data, source_used = resolve(capability, prefer_source=prefer_source, **kwargs)
    except Exception as exc:
        report = {
            "capability": capability,
            "prefer_source": prefer_source,
            "status": "blocked",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "kwargs": kwargs,
        }
        _persist_probe_status(
            report,
            capability=capability,
            persist_status=persist_status,
            prefer_source=prefer_source,
            data_domain=data_domain,
            source_name=source_name,
            source_tier=source_tier,
            stock_code=stock_code,
        )
        return report

    report = {
        "capability": capability,
        "prefer_source": prefer_source,
        "source_used": source_used,
        "status": "ok",
        "kwargs": kwargs,
    }
    report.update(_summarize(data))
    _persist_probe_status(
        report,
        capability=capability,
        persist_status=persist_status,
        prefer_source=prefer_source,
        data_domain=data_domain,
        source_name=source_name,
        source_tier=source_tier,
        stock_code=stock_code,
    )
    return report


def _persist_probe_status(
    report: dict[str, Any],
    *,
    capability: str,
    persist_status: bool,
    prefer_source: str | None,
    data_domain: str | None,
    source_name: str | None,
    source_tier: int | None,
    stock_code: str | None,
) -> None:
    if not persist_status:
        return

    resolved_domain = data_domain or CAPABILITY_DATA_DOMAIN_HINTS.get(capability)
    resolved_source = source_name or report.get("source_used") or prefer_source or CAPABILITY_SOURCE_HINTS.get(capability)
    if not resolved_domain or not resolved_source:
        report["persisted"] = {
            "status": "skipped",
            "reason": "missing data_domain/source_name",
        }
        return

    conn = get_conn()
    try:
        if report.get("status") == "blocked":
            record_source_failure(
                conn,
                data_domain=resolved_domain,
                source_name=resolved_source,
                source_tier=source_tier,
                stock_code=stock_code or str(report.get("kwargs", {}).get("stock") or report.get("kwargs", {}).get("symbol") or ""),
                error_type=str(report.get("error_type") or "source_probe_blocked"),
                last_error=str(report.get("error") or ""),
                commit=True,
            )
            report["persisted"] = {
                "status": "open",
                "table": "mart_data_source_failure_queue",
                "data_domain": resolved_domain,
                "source_name": resolved_source,
            }
        elif report.get("status") == "ok":
            resolved_rows = resolve_source_failures(
                conn,
                data_domain=resolved_domain,
                source_name=resolved_source,
                stock_code=stock_code or str(report.get("kwargs", {}).get("stock") or report.get("kwargs", {}).get("symbol") or ""),
                commit=True,
            )
            report["persisted"] = {
                "status": "resolved",
                "table": "mart_data_source_failure_queue",
                "data_domain": resolved_domain,
                "source_name": resolved_source,
                "resolved_rows": resolved_rows,
            }
    finally:
        conn.close()


def _parse_kwargs_json(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("--kwargs-json must decode to an object")
    return loaded


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a registered data-source capability and summarize the live result.")
    parser.add_argument("--capability", required=True, help="Capability name registered in services.data_sources")
    parser.add_argument("--prefer-source", default=None, help="Optional source name to force during probe")
    parser.add_argument("--persist-status", action="store_true", help="Write blocked/resolved probe state to mart_data_source_failure_queue")
    parser.add_argument("--data-domain", default=None, help="Optional data domain for persistence; defaults from capability hints")
    parser.add_argument("--source-name", default=None, help="Optional source name for persistence; defaults from resolved/preferred source")
    parser.add_argument("--source-tier", type=int, default=None, help="Optional source tier for persistence records")
    parser.add_argument("--stock-code", default=None, help="Optional stock code to attach to persistence records")
    parser.add_argument("--kwargs-json", default="{}", help="JSON object forwarded to resolve(..., **kwargs)")
    parser.add_argument("--indent", type=int, default=2, help="JSON indentation level for stdout")
    args = parser.parse_args()

    try:
        kwargs = _parse_kwargs_json(args.kwargs_json)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
                indent=args.indent,
                default=str,
            ),
            file=sys.stderr,
        )
        return 1

    report = probe_source_capability(
        args.capability,
        kwargs,
        prefer_source=args.prefer_source,
        persist_status=bool(args.persist_status),
        data_domain=args.data_domain,
        source_name=args.source_name,
        source_tier=args.source_tier,
        stock_code=args.stock_code,
    )
    print(json.dumps(report, ensure_ascii=False, indent=args.indent, default=str))
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
