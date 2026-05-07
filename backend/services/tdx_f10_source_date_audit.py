"""Section-level source-date audit for captured TDX/F10 holder research text."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from services.schema_versions import record_actual_version


DDL = """
CREATE TABLE IF NOT EXISTS mart_tdx_f10_source_date_section_audit (
    run_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    section_name TEXT NOT NULL,
    pattern_name TEXT NOT NULL,
    date_role TEXT NOT NULL,
    source_notice_candidate BOOLEAN NOT NULL,
    raw_row_count INTEGER NOT NULL,
    stock_count INTEGER NOT NULL,
    occurrence_count INTEGER NOT NULL,
    min_date TEXT,
    max_date TEXT,
    sample_json TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, section_id, section_name, pattern_name, date_role)
);
CREATE INDEX IF NOT EXISTS idx_f10_source_date_audit_run
    ON mart_tdx_f10_source_date_section_audit(run_id);
"""

SECTION_RE = re.compile(r"【(?P<section_id>\d+)\.(?P<section_name>[^】]+)】")
DATE_TOKEN = r"(?P<date>\d{4}[./-]\d{1,2}[./-]\d{1,2})"
DATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("page_update_date", re.compile(rf"更新日期[:：]\s*{DATE_TOKEN}")),
    ("latest_announce_date", re.compile(rf"最新公告日期[:：]\s*{DATE_TOKEN}")),
    ("announce_date", re.compile(rf"(?<!最新)公告日期[:：]\s*{DATE_TOKEN}")),
    ("disclosure_date", re.compile(rf"披露日期[:：]\s*{DATE_TOKEN}")),
    ("publish_date", re.compile(rf"发布日期[:：]\s*{DATE_TOKEN}")),
    ("cutoff_date", re.compile(rf"(?:截止日期|截至日期)[:：]\s*{DATE_TOKEN}")),
    ("change_date", re.compile(rf"变动日期[:：]\s*{DATE_TOKEN}")),
)


def ensure_tdx_f10_source_date_audit_table(conn: Any) -> None:
    for stmt in DDL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def _rows_as_dicts(cursor: Any) -> list[dict[str, Any]]:
    rows = cursor.fetchall()
    desc = getattr(cursor, "description", None) or []
    columns = [item[0] for item in desc]
    out: list[dict[str, Any]] = []
    for row in rows:
        if hasattr(row, "keys"):
            out.append({key: row[key] for key in row.keys()})
        else:
            out.append(dict(zip(columns, row)))
    return out


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    parts = re.findall(r"\d+", value)
    if len(parts) < 3:
        return None
    year, month, day = parts[:3]
    if len(year) != 4:
        return None
    return f"{year}-{int(month):02d}-{int(day):02d}"


def split_f10_sections(text: str) -> list[dict[str, str]]:
    matches = list(SECTION_RE.finditer(text or ""))
    sections: list[dict[str, str]] = []
    first_start = matches[0].start() if matches else len(text or "")
    header = (text or "")[:first_start]
    if header:
        sections.append({"section_id": "header", "section_name": "page_header", "text": header})
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text or "")
        sections.append(
            {
                "section_id": match.group("section_id"),
                "section_name": match.group("section_name").strip(),
                "text": (text or "")[match.start():end],
            }
        )
    return sections


def _date_role(section_id: str, pattern_name: str) -> tuple[str, bool]:
    if pattern_name == "page_update_date":
        return "page_update_availability", False
    if section_id == "2" and pattern_name in {"latest_announce_date", "announce_date"}:
        return "source_notice_date", True
    if pattern_name == "cutoff_date":
        return "fact_period_date", False
    if pattern_name == "change_date":
        return "event_date", False
    if pattern_name in {"latest_announce_date", "announce_date", "disclosure_date", "publish_date"}:
        return "ambiguous_notice_phrase", False
    return "unknown_date_phrase", False


def _sample_context(text: str, start: int, end: int, *, radius: int = 80) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _default_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:8]
    return f"tdx_f10_source_date_section_audit_{stamp}_{digest}"


def audit_tdx_f10_source_date_sections(
    conn: Any,
    *,
    run_id: str | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    ensure_tdx_f10_source_date_audit_table(conn)
    run_id = run_id or _default_run_id()
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    sql = """
        SELECT stock_code, stock_name, raw_hash, raw_text, fetched_at
          FROM raw_tdx_f10_holder_research
         WHERE raw_text IS NOT NULL AND raw_text != ''
         ORDER BY fetched_at DESC, stock_code
    """
    params: list[Any] = []
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    raw_rows = _rows_as_dicts(conn.execute(sql, params))
    aggregates: dict[tuple[str, str, str, str, bool], dict[str, Any]] = defaultdict(
        lambda: {
            "raw_hashes": set(),
            "stocks": set(),
            "occurrence_count": 0,
            "dates": [],
            "samples": [],
        }
    )

    for row in raw_rows:
        text = row.get("raw_text") or ""
        for section in split_f10_sections(text):
            section_text = section["text"]
            for pattern_name, pattern in DATE_PATTERNS:
                for match in pattern.finditer(section_text):
                    date_value = _normalize_date(match.group("date"))
                    role, candidate = _date_role(section["section_id"], pattern_name)
                    key = (
                        section["section_id"],
                        section["section_name"],
                        pattern_name,
                        role,
                        candidate,
                    )
                    bucket = aggregates[key]
                    bucket["raw_hashes"].add(row.get("raw_hash"))
                    bucket["stocks"].add(row.get("stock_code"))
                    bucket["occurrence_count"] += 1
                    if date_value:
                        bucket["dates"].append(date_value)
                    if len(bucket["samples"]) < 3:
                        bucket["samples"].append(
                            {
                                "stock_code": row.get("stock_code"),
                                "stock_name": row.get("stock_name"),
                                "date": date_value,
                                "context": _sample_context(section_text, match.start(), match.end()),
                            }
                        )

    conn.execute("DELETE FROM mart_tdx_f10_source_date_section_audit WHERE run_id = ?", (run_id,))
    rows = []
    for (section_id, section_name, pattern_name, role, candidate), bucket in sorted(aggregates.items()):
        dates = sorted(bucket["dates"])
        rows.append(
            (
                run_id,
                section_id,
                section_name,
                pattern_name,
                role,
                bool(candidate),
                len(bucket["raw_hashes"]),
                len(bucket["stocks"]),
                int(bucket["occurrence_count"]),
                dates[0] if dates else None,
                dates[-1] if dates else None,
                json.dumps(bucket["samples"], ensure_ascii=False),
                built_at,
            )
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO mart_tdx_f10_source_date_section_audit
            (run_id, section_id, section_name, pattern_name, date_role,
             source_notice_candidate, raw_row_count, stock_count,
             occurrence_count, min_date, max_date, sample_json, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    record_actual_version(conn, "mart_tdx_f10_source_date_section_audit")
    conn.commit()
    source_notice_candidates = sum(
        int(row[8])
        for row in rows
        if bool(row[5])
    )
    return {
        "run_id": run_id,
        "raw_rows": len(raw_rows),
        "audit_rows": len(rows),
        "source_notice_candidate_occurrences": source_notice_candidates,
        "built_at": built_at,
    }
