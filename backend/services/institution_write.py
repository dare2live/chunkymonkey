import json
import re
from datetime import datetime
from typing import Optional


def make_institution_id(name: str) -> str:
    normalized = name.lower().strip()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return f"inst_{normalized}"[:64]


def create_institution_record(
    conn,
    name: str,
    *,
    display_name: str = "",
    institution_type: str = "other",
    aliases: Optional[list] = None,
    now: Optional[str] = None,
) -> str:
    inst_id = make_institution_id(name)
    timestamp = now or datetime.now().isoformat()
    aliases_json = json.dumps(aliases or [], ensure_ascii=False)
    conn.execute(
        """
        INSERT OR IGNORE INTO inst_institutions
        (id, name, display_name, type, enabled, aliases, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (inst_id, name, display_name or "", institution_type or "other", aliases_json, timestamp, timestamp),
    )
    conn.commit()
    return inst_id


def batch_create_institution_records(conn, items: list[dict], *, now: Optional[str] = None) -> int:
    timestamp = now or datetime.now().isoformat()
    created = 0
    for item in items or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        inst_id = make_institution_id(name)
        aliases_json = json.dumps(item.get("aliases", []), ensure_ascii=False)
        conn.execute(
            """
            INSERT OR IGNORE INTO inst_institutions
            (id, name, display_name, type, enabled, aliases, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                inst_id,
                name,
                item.get("display_name", ""),
                item.get("type", "other"),
                aliases_json,
                timestamp,
                timestamp,
            ),
        )
        created += 1
    conn.commit()
    return created


def update_institution_record(conn, inst_id: str, body: dict, *, now: Optional[str] = None) -> bool:
    updates = []
    params = []
    for field in ["display_name", "type", "enabled", "blacklisted", "manual_type", "merged_into"]:
        if field in body:
            updates.append(f"{field} = ?")
            params.append(body[field])
    if "aliases" in body:
        updates.append("aliases = ?")
        params.append(json.dumps(body["aliases"], ensure_ascii=False))
    if not updates:
        return False

    updates.append("updated_at = ?")
    params.append(now or datetime.now().isoformat())
    params.append(inst_id)
    conn.execute(f"UPDATE inst_institutions SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    return True


def delete_institution_record(conn, inst_id: str) -> None:
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM inst_institutions WHERE id = ?", (inst_id,))
        conn.execute("DELETE FROM inst_holdings WHERE institution_id = ?", (inst_id,))
        conn.execute("DELETE FROM fact_institution_event WHERE institution_id = ?", (inst_id,))
        conn.execute("DELETE FROM mart_current_relationship WHERE institution_id = ?", (inst_id,))
        conn.execute("DELETE FROM mart_institution_profile WHERE institution_id = ?", (inst_id,))
        conn.execute("DELETE FROM mart_institution_industry_stat WHERE institution_id = ?", (inst_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def resolve_stock_name(conn, stock_code: str) -> str:
    row = conn.execute(
        """
        SELECT COALESCE(
            (
                SELECT NULLIF(d.stock_name, '')
                FROM dim_active_a_stock d
                WHERE d.stock_code = ?
                LIMIT 1
            ),
            (
                SELECT mr.stock_name
                FROM fact_top10_holder_period mr
                WHERE mr.stock_code = ?
                  AND mr.holder_set = 'free'
                  AND NOT mr.is_secondary_class
                  AND NOT mr.is_exit_row
                ORDER BY mr.report_date DESC, mr.notice_date DESC
                LIMIT 1
            ),
            ?
        ) AS stock_name
        LIMIT 1
        """,
        (stock_code, stock_code, stock_code),
    ).fetchone()
    if row and row["stock_name"]:
        return row["stock_name"]
    return stock_code


def upsert_manual_stock_blacklist(
    conn,
    stock_code: str,
    *,
    stock_name: str = "",
    reason: str = "",
    now: Optional[str] = None,
) -> str:
    timestamp = now or datetime.now().isoformat()
    resolved_name = stock_name.strip() or resolve_stock_name(conn, stock_code)
    resolved_reason = reason.strip() or "手工拉黑"
    conn.execute(
        """
        INSERT OR REPLACE INTO excluded_stocks
        (stock_code, category, stock_name, reason, created_at)
        VALUES (?, 'MANUAL', ?, ?, ?)
        """,
        (stock_code, resolved_name, resolved_reason, timestamp),
    )
    conn.commit()
    return resolved_name


def delete_manual_stock_blacklist(conn, stock_code: str) -> str:
    stock_name = resolve_stock_name(conn, stock_code)
    conn.execute(
        "DELETE FROM excluded_stocks WHERE stock_code = ? AND category = 'MANUAL'",
        (stock_code,),
    )
    conn.commit()
    return stock_name


def upsert_watchlist_entry(conn, body: dict, *, now: Optional[str] = None) -> None:
    timestamp = now or datetime.now().isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO stock_watchlist
        (stock_code, stock_name, added_date, added_price, added_reason,
         source_institution, source_event_type, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
        """,
        (
            body.get("stock_code"),
            body.get("stock_name"),
            body.get("added_date", timestamp[:10]),
            body.get("added_price"),
            body.get("added_reason", ""),
            body.get("source_institution", ""),
            body.get("source_event_type", ""),
            timestamp,
        ),
    )
    conn.commit()
