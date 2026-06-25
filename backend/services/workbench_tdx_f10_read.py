"""TDX F10 source-date and data-quality read models for Workbench."""
from __future__ import annotations

from typing import Any
import json


def _relation_exists(conn: Any, relation: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {relation} LIMIT 0").fetchone()
        return True
    except Exception:
        return False


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None and _relation_exists(conn, table_name)


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _json_count(value: Any) -> int:
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, list):
        return len(value)
    return 0


def build_f10_source_date_audit_view(conn: Any, *, limit: int = 30) -> dict[str, Any]:
    table = "mart_tdx_f10_source_date_section_audit"
    empty = {
        "run_id": None,
        "built_at": None,
        "summary": {
            "audit_rows": 0,
            "occurrence_count": 0,
            "future_occurrence_count": 0,
            "source_notice_candidate_occurrences": 0,
            "source_notice_candidate_future_occurrences": 0,
            "raw_row_count": 0,
        },
        "rows": [],
    }
    if not _table_exists(conn, table):
        return empty
    row = conn.execute(
        """
        SELECT run_id, MAX(CAST(built_at AS VARCHAR)) AS built_at
          FROM mart_tdx_f10_source_date_section_audit
         GROUP BY run_id
         ORDER BY built_at DESC, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    if not row:
        return empty
    run_id = row["run_id"]
    summary = conn.execute(
        """
        SELECT COUNT(*) AS audit_rows,
               SUM(COALESCE(occurrence_count, 0)) AS occurrence_count,
               SUM(COALESCE(future_occurrence_count, 0)) AS future_occurrence_count,
               SUM(CASE WHEN source_notice_candidate
                        THEN COALESCE(occurrence_count, 0) ELSE 0 END) AS source_notice_candidate_occurrences,
               SUM(CASE WHEN source_notice_candidate
                        THEN COALESCE(future_occurrence_count, 0) ELSE 0 END) AS source_notice_candidate_future_occurrences,
               MAX(COALESCE(raw_row_count, 0)) AS raw_row_count
          FROM mart_tdx_f10_source_date_section_audit
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    rows = conn.execute(
        """
        SELECT section_id, section_name, pattern_name, date_role,
               source_notice_candidate, raw_row_count, stock_count,
               occurrence_count, future_occurrence_count, min_date, max_date,
               sample_json
          FROM mart_tdx_f10_source_date_section_audit
         WHERE run_id = ?
         ORDER BY source_notice_candidate DESC,
                  future_occurrence_count DESC,
                  section_id, pattern_name, date_role
         LIMIT ?
        """,
        (run_id, int(limit)),
    ).fetchall()
    return {
        "run_id": run_id,
        "built_at": row["built_at"],
        "summary": {
            "audit_rows": int((summary or {})["audit_rows"] or 0),
            "occurrence_count": int((summary or {})["occurrence_count"] or 0),
            "future_occurrence_count": int((summary or {})["future_occurrence_count"] or 0),
            "source_notice_candidate_occurrences": int(
                (summary or {})["source_notice_candidate_occurrences"] or 0
            ),
            "source_notice_candidate_future_occurrences": int(
                (summary or {})["source_notice_candidate_future_occurrences"] or 0
            ),
            "raw_row_count": int((summary or {})["raw_row_count"] or 0),
        },
        "rows": [
            {
                "section_id": row["section_id"],
                "section_name": row["section_name"],
                "pattern_name": row["pattern_name"],
                "date_role": row["date_role"],
                "source_notice_candidate": bool(row["source_notice_candidate"]),
                "raw_row_count": int(row["raw_row_count"] or 0),
                "stock_count": int(row["stock_count"] or 0),
                "occurrence_count": int(row["occurrence_count"] or 0),
                "future_occurrence_count": int(row["future_occurrence_count"] or 0),
                "min_date": row["min_date"],
                "max_date": row["max_date"],
                "samples": _safe_json(row["sample_json"]) or [],
            }
            for row in rows
        ],
    }


# M2/tdx-F10 退役 (2026-06-25): build_tdx_f10_source_dq_view 已退役删除 —
# domain 'tdx_f10_source_availability' 写入方 (_check_tdx_f10_source_availability) 已于 commit 7646b316 退役,
# 读目标表 mart_global_data_quality_detail live 不存在 → 该视图永久空降级。
# 保留 build_f10_source_date_audit_view (上方, 不同 LIVE 函数, 属待定的 DQ-detail 子系统决策)。
