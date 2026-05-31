#!/usr/bin/env python3
"""Read-only audit for oversized or recursive JSON/TEXT payloads in DuckDB."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb


REPO = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO / "data" / "smartmoney.duckdb"
DEFAULT_CONFIG = REPO / "backend" / "config" / "storage_retention.yaml"

PAYLOAD_TYPE_TOKENS = ("char", "string", "text", "json", "blob")
DEFAULT_PAYLOAD_COLUMN_NAME_TOKENS = (
    "json",
    "payload",
    "audit",
    "evidence",
    "summary",
    "signals",
    "blob",
    "bytes",
    "raw_text",
    "raw_json",
    "raw_bytes",
    "command",
    "path",
    "url",
    "artifact",
    "details",
    "preview",
    "warning",
    "blocker",
    "reason",
    "lineage",
)

DEFAULT_POLICY = {
    "max_value_warn_bytes": 1_048_576,
    "max_value_fail_bytes": 10_485_760,
    "total_value_warn_bytes": 10_485_760,
    "total_value_fail_bytes": 104_857_600,
    "top_sample_rows": 20,
    "duplicate_sample_warn_count": 2,
    "fail_on_recursive_keyword": True,
    "warn_on_path_marker": True,
    "payload_column_name_tokens": DEFAULT_PAYLOAD_COLUMN_NAME_TOKENS,
    "recursive_keywords": (
        "latest_dispatch",
        "queue_items",
        "audit_json",
        "signals_json",
        "preview",
    ),
    "path_markers": (
        "data/reports/",
        "analysis/",
        "/logs/",
        ".log",
        "file://",
    ),
    "reviewed_columns": (),
}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - local runtime has PyYAML.
        raise RuntimeError("PyYAML is required to load storage_retention.yaml") from exc
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def load_payload_policy(path: str | Path | None = None) -> dict[str, Any]:
    raw = _load_yaml(Path(path) if path else DEFAULT_CONFIG)
    configured = raw.get("payload_audit", {}) if isinstance(raw.get("payload_audit"), dict) else {}
    policy = dict(DEFAULT_POLICY)
    for key, value in configured.items():
        if key in {"recursive_keywords", "path_markers", "payload_column_name_tokens"}:
            policy[key] = tuple(str(item).lower() for item in (value or []))
        elif key in {"fail_on_recursive_keyword", "warn_on_path_marker"}:
            policy[key] = bool(value)
        elif key == "reviewed_columns":
            policy[key] = _normalize_reviewed_columns(value)
        else:
            policy[key] = int(value)
    return policy


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _normalize_reviewed_columns(raw: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, list):
        return ()
    rules: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        table = str(item.get("table") or "").strip()
        column = str(item.get("column") or "").strip()
        if not table or not column:
            continue
        rules.append(
            {
                "table": table,
                "column": column,
                "classification": str(item.get("classification") or "reviewed_payload"),
                "owner": str(item.get("owner") or ""),
                "reason": str(item.get("reason") or ""),
                "allow_path_marker": bool(item.get("allow_path_marker", False)),
                "max_value_bytes": _optional_int(item.get("max_value_bytes")),
                "max_total_value_bytes": _optional_int(item.get("max_total_value_bytes")),
            }
        )
    return tuple(rules)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_payload_like_column(column_name: str, data_type: str, policy: dict[str, Any]) -> bool:
    type_text = str(data_type or "").lower()
    if not any(token in type_text for token in PAYLOAD_TYPE_TOKENS):
        return False
    if any(token in type_text for token in ("json", "blob")):
        return True
    name_text = str(column_name or "").lower()
    return any(token in name_text for token in tuple(policy["payload_column_name_tokens"]))


def _payload_columns(conn, policy: dict[str, Any], tables: set[str] | None = None) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT table_name, column_name, data_type
          FROM information_schema.columns
         ORDER BY table_name, ordinal_position
        """
    ).fetchall()
    columns: list[dict[str, str]] = []
    for table_name, column_name, data_type in rows:
        table = str(table_name)
        if tables and table not in tables:
            continue
        if _is_payload_like_column(str(column_name), str(data_type), policy):
            columns.append(
                {
                    "table": table,
                    "column": str(column_name),
                    "data_type": str(data_type),
                }
            )
    return columns


def _like_any_expr(column_sql: str, markers: tuple[str, ...]) -> str:
    if not markers:
        return "FALSE"
    clauses = []
    for marker in markers:
        safe = marker.replace("'", "''").lower()
        clauses.append(f"lower(CAST({column_sql} AS VARCHAR)) LIKE '%{safe}%'")
    return " OR ".join(clauses)


def _json_key_regex_any(markers: tuple[str, ...]) -> str:
    escaped = [re.escape(str(marker)) for marker in markers if str(marker)]
    if not escaped:
        return "$a"
    return r'(?i)"(?:' + "|".join(escaped) + r')"\s*:'


def _json_key_match_expr(column_sql: str, markers: tuple[str, ...]) -> str:
    if not markers:
        return "FALSE"
    pattern = _json_key_regex_any(markers).replace("'", "''")
    return f"regexp_matches(CAST({column_sql} AS VARCHAR), '{pattern}')"


def _top_sample_duplicate_count(conn, table: str, column: str, limit: int) -> int:
    if limit <= 1:
        return 0
    table_sql = _quote_ident(table)
    column_sql = _quote_ident(column)
    rows = conn.execute(
        f"""
        SELECT CAST({column_sql} AS VARCHAR) AS payload
          FROM {table_sql}
         WHERE {column_sql} IS NOT NULL
         ORDER BY length(CAST({column_sql} AS VARCHAR)) DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        return 0
    digests = Counter(
        hashlib.sha256(str(row[0]).encode("utf-8", errors="ignore")).hexdigest()
        for row in rows
    )
    return max(digests.values()) if digests else 0


def _severity_for(stats: dict[str, Any], policy: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    severity = "PASS"
    if stats["max_value_bytes"] >= int(policy["max_value_fail_bytes"]):
        severity = "FAIL"
        reasons.append("max_value_bytes exceeds fail threshold")
    elif stats["max_value_bytes"] >= int(policy["max_value_warn_bytes"]):
        severity = "WARN"
        reasons.append("max_value_bytes exceeds warn threshold")
    if stats["total_value_bytes"] >= int(policy["total_value_fail_bytes"]):
        if stats["max_value_bytes"] >= int(policy["max_value_warn_bytes"]):
            severity = "FAIL"
            reasons.append("total_value_bytes exceeds fail threshold for large payload values")
        elif severity != "FAIL":
            severity = "WARN"
            reasons.append("total_value_bytes exceeds fail threshold but individual values are bounded")
    elif stats["total_value_bytes"] >= int(policy["total_value_warn_bytes"]) and severity != "FAIL":
        severity = "WARN"
        reasons.append("total_value_bytes exceeds warn threshold")
    if stats["recursive_keyword_hits"] > 0 and policy.get("fail_on_recursive_keyword", True):
        severity = "FAIL"
        reasons.append("recursive keyword detected")
    if stats["path_marker_hits"] > 0 and policy.get("warn_on_path_marker", True) and severity != "FAIL":
        severity = "WARN"
        reasons.append("path marker detected in payload")
    if (
        stats["top_sample_duplicate_count"] >= int(policy["duplicate_sample_warn_count"])
        and stats["max_value_bytes"] >= int(policy["max_value_warn_bytes"])
        and severity != "FAIL"
    ):
        severity = "WARN"
        reasons.append("duplicate large top-sample payloads detected")
    return _apply_reviewed_column_rule(stats, severity, reasons, policy)


def _reviewed_rule_for(table: str, column: str, policy: dict[str, Any]) -> dict[str, Any] | None:
    for rule in tuple(policy.get("reviewed_columns") or ()):
        if rule.get("table") == table and rule.get("column") == column:
            return rule
    return None


def _apply_reviewed_column_rule(
    stats: dict[str, Any],
    severity: str,
    reasons: list[str],
    policy: dict[str, Any],
) -> tuple[str, list[str]]:
    rule = _reviewed_rule_for(str(stats["table"]), str(stats["column"]), policy)
    if not rule:
        return severity, reasons

    blockers: list[str] = []
    if stats["recursive_keyword_hits"] > 0:
        blockers.append("recursive keyword hits")
    max_value_bytes = rule.get("max_value_bytes")
    if max_value_bytes is not None and stats["max_value_bytes"] > int(max_value_bytes):
        blockers.append("max_value_bytes exceeds reviewed cap")
    max_total_value_bytes = rule.get("max_total_value_bytes")
    if max_total_value_bytes is not None and stats["total_value_bytes"] > int(max_total_value_bytes):
        blockers.append("total_value_bytes exceeds reviewed cap")
    if stats["path_marker_hits"] > 0 and not rule.get("allow_path_marker"):
        blockers.append("path marker not allowed by reviewed rule")

    stats["review"] = {
        "status": "blocked" if blockers else "accepted",
        "classification": rule.get("classification") or "reviewed_payload",
        "owner": rule.get("owner") or "",
        "reason": rule.get("reason") or "",
        "blockers": blockers,
    }
    if severity == "WARN" and not blockers:
        reasons = [f"reviewed payload: {stats['review']['classification']}"]
        return "PASS", reasons
    return severity, reasons


def audit_column(conn, table: str, column: str, data_type: str, policy: dict[str, Any]) -> dict[str, Any]:
    table_sql = _quote_ident(table)
    column_sql = _quote_ident(column)
    value_sql = f"CAST({column_sql} AS VARCHAR)"
    recursive_expr = _json_key_match_expr(column_sql, tuple(policy["recursive_keywords"]))
    path_expr = _like_any_expr(column_sql, tuple(policy["path_markers"]))
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS row_count,
               COUNT({column_sql}) AS non_null_count,
               COALESCE(MAX(length({value_sql})), 0) AS max_value_bytes,
               COALESCE(SUM(length({value_sql})), 0) AS total_value_bytes,
               SUM(CASE WHEN {recursive_expr} THEN 1 ELSE 0 END) AS recursive_keyword_hits,
               SUM(CASE WHEN {path_expr} THEN 1 ELSE 0 END) AS path_marker_hits
          FROM {table_sql}
        """
    ).fetchone()
    stats = {
        "table": table,
        "column": column,
        "data_type": data_type,
        "row_count": int(row[0] or 0),
        "non_null_count": int(row[1] or 0),
        "max_value_bytes": int(row[2] or 0),
        "total_value_bytes": int(row[3] or 0),
        "recursive_keyword_hits": int(row[4] or 0),
        "path_marker_hits": int(row[5] or 0),
        "top_sample_duplicate_count": _top_sample_duplicate_count(
            conn,
            table,
            column,
            int(policy["top_sample_rows"]),
        ),
    }
    severity, reasons = _severity_for(stats, policy)
    stats["severity"] = severity
    stats["reasons"] = reasons
    return stats


def build_storage_payload_report(
    conn,
    *,
    policy: dict[str, Any] | None = None,
    tables: list[str] | None = None,
) -> dict[str, Any]:
    active_policy = policy or load_payload_policy()
    table_filter = set(tables or []) or None
    findings = [
        audit_column(conn, item["table"], item["column"], item["data_type"], active_policy)
        for item in _payload_columns(conn, active_policy, table_filter)
    ]
    fail_count = sum(1 for item in findings if item["severity"] == "FAIL")
    warn_count = sum(1 for item in findings if item["severity"] == "WARN")
    reviewed_count = sum(1 for item in findings if item.get("review", {}).get("status") == "accepted")
    verdict = "FAIL" if fail_count else "WARN" if warn_count else "PASS"
    return {
        "schema_version": 1,
        "verdict": verdict,
        "policy": {
            key: value
            for key, value in active_policy.items()
            if key not in {"recursive_keywords", "path_markers"}
        },
        "summary": {
            "columns_scanned": len(findings),
            "fail": fail_count,
            "warn": warn_count,
            "reviewed": reviewed_count,
            "pass": sum(1 for item in findings if item["severity"] == "PASS"),
        },
        "findings": findings,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Storage Payload Audit",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Verdict | {report['verdict']} |",
        f"| Columns scanned | {report['summary']['columns_scanned']} |",
        f"| FAIL | {report['summary']['fail']} |",
        f"| WARN | {report['summary']['warn']} |",
        f"| Reviewed PASS | {report['summary'].get('reviewed', 0)} |",
        "",
        "## Findings",
        "",
        "| Severity | Table | Column | Max bytes | Total bytes | Recursive hits | Path hits | Reasons |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for item in report["findings"]:
        if item["severity"] == "PASS":
            continue
        reasons = ", ".join(item["reasons"]) or "-"
        display = dict(item)
        display["reasons"] = reasons
        lines.append(
            "| {severity} | `{table}` | `{column}` | {max_value_bytes} | {total_value_bytes} | "
            "{recursive_keyword_hits} | {path_marker_hits} | {reasons} |".format(
                **display,
            )
        )
    if all(item["severity"] == "PASS" for item in report["findings"]):
        lines.append("| PASS | - | - | 0 | 0 | 0 | 0 | - |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--table", action="append", default=[])
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        report = build_storage_payload_report(
            conn,
            policy=load_payload_policy(args.config),
            tables=args.table,
        )
    finally:
        conn.close()
    if args.format == "markdown":
        sys.stdout.write(render_markdown(report))
    else:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
