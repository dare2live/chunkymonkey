"""Shared read-side helpers for auxiliary institution endpoints.

Keep lightweight institution support endpoints in one place so the main router
stops owning SQL shaping for generic read-only payloads.
"""

from services.industry import attach_industry_aliases


_INDUSTRY_PAYLOAD_KEYS = {
    "sw_l1",
    "sw_l2",
    "sw_l3",
    "sw_l1_name",
    "sw_l2_name",
    "sw_l3_name",
}


def _normalize_row(row) -> dict:
    item = dict(row)
    if any(key in item for key in _INDUSTRY_PAYLOAD_KEYS):
        attach_industry_aliases(item, item)
    return item


def load_holdings_rows(conn, institution_id: str = None, stock_code: str = None, limit: int = 5000) -> list[dict]:
    sql = "SELECT * FROM inst_holdings WHERE 1=1"
    params = []
    if institution_id:
        sql += " AND institution_id = ?"
        params.append(institution_id)
    if stock_code:
        sql += " AND stock_code = ?"
        params.append(stock_code)
    sql += " ORDER BY report_date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_normalize_row(row) for row in rows]


def load_event_rows(
    conn,
    institution_id: str = None,
    stock_code: str = None,
    event_type: str = None,
    limit: int = 200,
) -> dict:
    sql = """
        SELECT e.*, i.display_name AS inst_display_name
        FROM fact_institution_event e
        LEFT JOIN inst_institutions i ON e.institution_id = i.id
        WHERE 1=1
    """
    params = []
    if institution_id:
        sql += " AND e.institution_id = ?"
        params.append(institution_id)
    if stock_code:
        sql += " AND e.stock_code = ?"
        params.append(stock_code)
    if event_type:
        sql += " AND e.event_type = ?"
        params.append(event_type)
    sql += " ORDER BY e.notice_date DESC, e.report_date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    count_sql = "SELECT COUNT(*) FROM fact_institution_event e WHERE 1=1"
    count_params = []
    if institution_id:
        count_sql += " AND e.institution_id = ?"
        count_params.append(institution_id)
    if stock_code:
        count_sql += " AND e.stock_code = ?"
        count_params.append(stock_code)
    if event_type:
        count_sql += " AND e.event_type = ?"
        count_params.append(event_type)
    total = conn.execute(count_sql, count_params).fetchone()[0]

    return {
        "data": [_normalize_row(row) for row in rows],
        "total": total,
    }


def load_industry_stat_rows(conn, institution_id: str = None) -> list[dict]:
    sql = "SELECT * FROM mart_institution_industry_stat WHERE 1=1"
    params = []
    if institution_id:
        sql += " AND institution_id = ?"
        params.append(institution_id)
    sql += " ORDER BY sample_events DESC"
    rows = conn.execute(sql, params).fetchall()
    return [_normalize_row(row) for row in rows]


def load_exclusion_categories(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM exclusion_categories ORDER BY category").fetchall()
    return [dict(row) for row in rows]