#!/usr/bin/env python3
"""Probe a registered data-source capability and summarize the live result."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.data_sources import resolve  # noqa: E402


DATE_KEYS = (
    "日期",
    "date",
    "trade_date",
    "report_date",
    "notice_date",
    "snapshot_date",
    "as_of_date",
)


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
) -> dict[str, Any]:
    try:
        data, source_used = resolve(capability, prefer_source=prefer_source, **kwargs)
    except Exception as exc:
        return {
            "capability": capability,
            "prefer_source": prefer_source,
            "status": "blocked",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "kwargs": kwargs,
        }

    report = {
        "capability": capability,
        "prefer_source": prefer_source,
        "source_used": source_used,
        "status": "ok",
        "kwargs": kwargs,
    }
    report.update(_summarize(data))
    return report


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

    report = probe_source_capability(args.capability, kwargs, prefer_source=args.prefer_source)
    print(json.dumps(report, ensure_ascii=False, indent=args.indent, default=str))
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
