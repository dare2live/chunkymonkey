"""Infrastructure helpers for the updater router.

This module owns UI log buffering, sync-source metric formatting, and legacy
step-status normalization. It deliberately contains no pipeline step runners.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from typing import Optional


_UI_LOG_LIMIT = 400
_ui_logs = []
_ui_log_seq = 0


class _UILogHandler(logging.Handler):
    """把 cm-api 日志同步到前端状态接口，供工作台展示。"""

    def emit(self, record):
        global _ui_log_seq
        try:
            message = record.getMessage()
            if not message:
                return
            _ui_log_seq += 1
            _ui_logs.append({
                "id": _ui_log_seq,
                "ts": datetime.now().isoformat(),
                "level": record.levelname.lower(),
                "message": message,
            })
            if len(_ui_logs) > _UI_LOG_LIMIT:
                del _ui_logs[:-_UI_LOG_LIMIT]
        except Exception as exc:
            print(f"[UILogHandler] emit error: {exc}", file=sys.stderr)


def attach_ui_log_handler(logger: logging.Logger) -> None:
    if not getattr(logger, "_cm_ui_handler_attached", False):
        logger.addHandler(_UILogHandler(level=logging.INFO))
        logger._cm_ui_handler_attached = True


def get_ui_logs() -> list[dict]:
    return list(_ui_logs)


def _reset_ui_logs():
    global _ui_logs, _ui_log_seq
    _ui_logs = []
    _ui_log_seq = 0


def _record_sync_source_metric(stats: dict, source: str, elapsed_sec: float, rows: int = 0) -> None:
    entry = stats.setdefault(source, {
        "count": 0,
        "rows": 0,
        "elapsed_total_sec": 0.0,
        "max_elapsed_sec": 0.0,
    })
    entry["count"] += 1
    entry["rows"] += max(0, int(rows or 0))
    entry["elapsed_total_sec"] += max(0.0, float(elapsed_sec or 0.0))
    entry["max_elapsed_sec"] = max(entry["max_elapsed_sec"], max(0.0, float(elapsed_sec or 0.0)))


def _snapshot_sync_source_metrics(stats: dict) -> dict:
    snapshot = {}
    ordered_sources = sorted(stats)
    for source in ordered_sources:
        entry = stats[source]
        count = int(entry.get("count") or 0)
        elapsed_total = float(entry.get("elapsed_total_sec") or 0.0)
        snapshot[source] = {
            "count": count,
            "rows": int(entry.get("rows") or 0),
            "avg_elapsed_sec": round(elapsed_total / count, 3) if count else 0.0,
            "max_elapsed_sec": round(float(entry.get("max_elapsed_sec") or 0.0), 3),
            "elapsed_total_sec": round(elapsed_total, 3),
        }
    return snapshot


def _format_sync_source_metrics(stats: dict) -> str:
    if not stats:
        return "无成功来源"
    parts = []
    for source, entry in stats.items():
        parts.append(
            f"{source}={entry['count']}只/均{entry['avg_elapsed_sec']:.2f}s/峰{entry['max_elapsed_sec']:.2f}s/行{entry['rows']}"
        )
    return "；".join(parts)


def _build_daily_sync_batch_summary(
    range_start: int,
    range_end: int,
    *,
    stats: dict,
    batch_elapsed_sec: float,
) -> dict:
    snapshot = _snapshot_sync_source_metrics(stats)
    success_count = sum(int(entry.get("count") or 0) for entry in snapshot.values())
    batch_total = max(0, range_end - range_start + 1)
    return {
        "range_start": range_start,
        "range_end": range_end,
        "count": batch_total,
        "success_count": success_count,
        "failed_count": max(0, batch_total - success_count),
        "batch_elapsed_sec": round(max(0.0, float(batch_elapsed_sec or 0.0)), 3),
        "source_stats": snapshot,
    }


def _normalize_update_step_detail(detail: Optional[dict]) -> Optional[dict]:
    if not isinstance(detail, dict):
        return None

    normalized = dict(detail)
    daily_sync = normalized.get("daily_sync")
    if isinstance(daily_sync, dict):
        normalized_daily_sync = dict(daily_sync)
        normalized_daily_sync.setdefault("prefer_fallback", False)
        normalized_daily_sync.setdefault("strategy_reason", None)
        normalized_daily_sync.setdefault("preflight_sample", None)
        normalized["daily_sync"] = normalized_daily_sync

    return normalized


def _coerce_step_record_count(value) -> Optional[int]:
    """Return a numeric step record count from clean or legacy status values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    parsed_record_count: Optional[int] = None
    try:
        parsed_record_count = int(float(text))
    except (TypeError, ValueError, OverflowError):
        parsed_record_count = None
    if parsed_record_count is not None:
        return parsed_record_count
    match = re.search(r"""['"]?count['"]?\s*:\s*([0-9]+)""", text)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return None
    return None


def _extract_legacy_step_field(raw: str, key: str) -> Optional[str]:
    text = str(raw or "")
    match = re.search(rf"""['"]?{re.escape(key)}['"]?\s*:\s*('([^']*)'|"([^"]*)"|[^,}}]+)""", text)
    if not match:
        return None
    value = match.group(2) if match.group(2) is not None else (
        match.group(3) if match.group(3) is not None else match.group(1)
    )
    value = str(value).strip().strip("'\"")
    return value or None


def _parse_step_detail(raw) -> Optional[dict]:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return _normalize_update_step_detail(parsed)


def _legacy_step_detail_from_records(raw) -> Optional[dict]:
    """Recover detail from old rows where a whole dict was written into records."""
    text = str(raw or "").strip()
    if not text.startswith("{"):
        return None
    detail: dict = {}
    count = _coerce_step_record_count(text)
    if count is not None:
        detail["count"] = count
    for key in ("status", "mode", "message", "target_date", "range", "report_date", "trade_date"):
        value = _extract_legacy_step_field(text, key)
        if value:
            detail[key] = value
    for key in ("written", "skipped", "empty", "failed", "total", "existing", "mart_rows"):
        value = _extract_legacy_step_field(text, key)
        if value is None:
            continue
        try:
            detail[key] = int(float(value))
        except Exception:
            detail[key] = value
    return _normalize_update_step_detail(detail) if detail else None


def _sanitize_step_status_item(item: dict) -> dict:
    """Normalize legacy step_status rows before sending them to the UI."""
    cleaned = dict(item)
    detail = _parse_step_detail(cleaned.get("error"))
    if detail is None:
        detail = _legacy_step_detail_from_records(cleaned.get("records"))
    if detail is not None:
        cleaned["detail"] = detail

    count = _coerce_step_record_count(cleaned.get("records"))
    cleaned["records"] = count if count is not None else 0
    if detail and detail.get("status") and cleaned.get("status") not in {"failed", "stopped", "running", "pending"}:
        cleaned["status"] = _normalize_step_status(detail.get("status"))
    return cleaned


def _normalize_step_status(status) -> str:
    text = str(status or "completed").strip().lower()
    return {
        "success": "completed",
        "done": "completed",
        "ok": "completed",
        "complete": "completed",
        "skip": "skipped",
        "warning": "partial",
        "partial_success": "partial",
        "error": "failed",
    }.get(text, text if text in {"completed", "partial", "failed", "blocked", "skipped", "stopped", "running", "pending"} else "completed")


def _format_step_result_for_log(status: str, count: int, detail_text: Optional[str]) -> str:
    detail = _parse_step_detail(detail_text)
    if detail and detail.get("message"):
        return str(detail["message"])
    if detail:
        for key in ("written", "count", "total", "existing"):
            if detail.get(key) is not None:
                return f"{detail.get(key)}"
    if status == "skipped":
        return str(detail_text or "已是最新，无需更新")
    if status == "blocked":
        return str(detail_text or "阻断")
    if status == "partial":
        return f"{count} 条，有缺口"
    return f"{count}"
