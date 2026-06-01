"""
事件生成引擎

优先使用东财原始数据中的 hold_change 字段（新进/加仓/减仓），
退出事件通过对比每只股票最新两个报告期推算。

§4.25 #2 幂等化 (2026-04-26):
- 引入 mart_step_fingerprint KV 表存上次 gen_events 时的输入签名
- 每次跑前先算当前 inst_holdings 签名, 跟上次比对
- 一致 → 跳过 DELETE+INSERT, 保留 fact_institution_event.calc_version 等
  下游 calc_returns 已算字段, 避免无变化触发全量重算
"""

import hashlib
import logging
from datetime import datetime

from services.constants import CHANGE_MAP as _CHANGE_MAP

logger = logging.getLogger("cm-api")


def _table_columns(conn, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    except Exception:  # rule-compliance: ok evidence=legacy schema fallback
        return set()
    columns: set[str] = set()
    for row in rows:
        if hasattr(row, "keys"):
            columns.add(str(row["name"]))
        else:
            columns.add(str(row[1]))
    return columns


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


_FACT_INSTITUTION_EVENT_INDEX_SPECS = (
    (frozenset({"event_type"}), "CREATE INDEX IF NOT EXISTS idx_event_type ON fact_institution_event(event_type)"),
    (frozenset({"report_date"}), "CREATE INDEX IF NOT EXISTS idx_event_date ON fact_institution_event(report_date)"),
    (frozenset({"notice_date"}), "CREATE INDEX IF NOT EXISTS idx_event_notice ON fact_institution_event(notice_date)"),
    (frozenset({"stock_code", "report_date"}), "CREATE INDEX IF NOT EXISTS idx_fie_stock ON fact_institution_event(stock_code, report_date DESC)"),
    (frozenset({"holder_name", "report_date"}), "CREATE INDEX IF NOT EXISTS idx_fie_holder ON fact_institution_event(holder_name, report_date DESC)"),
    (frozenset({"notice_date_source"}), "CREATE INDEX IF NOT EXISTS idx_fie_notice_source ON fact_institution_event(notice_date_source)"),
)


def _fact_institution_event_index_sqls(existing_columns: set[str]) -> tuple[str, ...]:
    return tuple(sql for required_columns, sql in _FACT_INSTITUTION_EVENT_INDEX_SPECS if required_columns <= existing_columns)


def _build_fact_institution_event_recreate_sql(conn) -> str:
    """Build a fresh fact_institution_event DDL from the live schema.

    We intentionally recreate the table before each full rebuild instead of
    doing a large DELETE in-place. That clears stale ART/index state and keeps
    the updater path resilient when the main table has accumulated historical
    index drift.
    """

    rows = conn.execute("PRAGMA table_info('fact_institution_event')").fetchall()
    if not rows:
        raise RuntimeError("fact_institution_event schema unavailable")

    pk_cols = {"institution_id", "stock_code", "report_date"}
    column_defs: list[str] = []
    for row in rows:
        if hasattr(row, "keys"):
            name = str(row["name"])
            col_type = str(row["type"] or "VARCHAR")
            notnull = bool(row["notnull"])
            default = row["dflt_value"]
        else:
            name = str(row[1])
            col_type = str(row[2] or "VARCHAR")
            notnull = bool(row[3])
            default = row[4]
        pieces = [_quote_ident(name), col_type]
        if name in pk_cols or notnull:
            pieces.append("NOT NULL")
        if default not in (None, ""):
            pieces.append(f"DEFAULT {default}")
        column_defs.append(" ".join(pieces))

    column_defs.append("PRIMARY KEY (institution_id, stock_code, report_date)")
    return "CREATE TABLE fact_institution_event (\n    " + ",\n    ".join(column_defs) + "\n)"


def _rebuild_fact_institution_event_table(conn) -> None:
    schema_sql = _build_fact_institution_event_recreate_sql(conn)
    existing_columns = _table_columns(conn, "fact_institution_event")
    conn.execute("DROP TABLE IF EXISTS fact_institution_event")
    conn.execute(schema_sql)
    for sql in _fact_institution_event_index_sqls(existing_columns):
        conn.execute(sql)


# ---------------------------------------------------------------------------
# 幂等化: 输入签名 + KV 存储 (§4.25 #2)
# ---------------------------------------------------------------------------

_STEP_FP_DDL = """
CREATE TABLE IF NOT EXISTS mart_step_fingerprint (
    step_id      TEXT PRIMARY KEY,
    fingerprint  TEXT,
    row_count    INTEGER,
    computed_at  TEXT
);
"""


def _ensure_fingerprint_table(conn):
    conn.executescript(_STEP_FP_DDL)


def compute_gen_events_input_signature(conn) -> tuple[str, int]:
    """计算 gen_events 输入 (inst_holdings + fact_top10_holder_period 最新两期 + 跟踪机构集合) 的签名.

    覆盖三类输入:
    - inst_holdings 全表 (count + sum(hold_amount) + (inst_id, stock_code, report_date) 三元组数)
    - fact_top10_holder_period 最新两期 流通股东切片 (用于 generate_exit_events)
    - 跟踪机构 enabled 集合 (退出事件需要)

    返回 (fingerprint_hex, total_holdings_rows).
    """
    h = hashlib.sha256()

    # 1. inst_holdings 主签名
    row = conn.execute("""
        SELECT
            COUNT(*) AS n_rows,
            COALESCE(SUM(hold_amount), 0) AS sum_amount,
            COUNT(DISTINCT (institution_id || '|' || stock_code || '|' || report_date)) AS n_keys,
            MAX(report_date) AS max_rd
        FROM inst_holdings
        WHERE institution_id IS NOT NULL AND stock_code IS NOT NULL
    """).fetchone()
    holdings_part = f"holdings|{row['n_rows']}|{row['sum_amount']:.2f}|{row['n_keys']}|{row['max_rd']}"
    h.update(holdings_part.encode("utf-8"))
    n_rows = int(row["n_rows"] or 0)
    inst_columns = _table_columns(conn, "inst_holdings")
    if "notice_date_source" in inst_columns:
        source_row = conn.execute("""
            SELECT
                COUNT(*) FILTER (WHERE notice_date_source = 'source_notice') AS source_notice_rows,
                COUNT(*) FILTER (WHERE notice_date_source = 'regulatory_deadline') AS regulatory_deadline_rows,
                MAX(COALESCE(notice_date, '')) AS max_notice_date,
                MAX(COALESCE(notice_date_source, '')) AS max_notice_date_source
            FROM inst_holdings
            WHERE institution_id IS NOT NULL AND stock_code IS NOT NULL
        """).fetchone()
        source_part = (
            f"|notice_source|{source_row['source_notice_rows']}|"
            f"{source_row['regulatory_deadline_rows']}|"
            f"{source_row['max_notice_date']}|{source_row['max_notice_date_source']}"
        )
        h.update(source_part.encode("utf-8"))

    # 2. fact_top10_holder_period 最新两期 (free/非二级/非退出): generate_exit_events 用 (stock_code, report_date) 序列
    try:
        rows = conn.execute("""
            SELECT stock_code, report_date
            FROM (
                SELECT stock_code, report_date,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY report_date DESC) AS rn
                FROM (SELECT DISTINCT stock_code, report_date FROM fact_top10_holder_period
                      WHERE stock_code IS NOT NULL
                        AND holder_set = 'free'
                        AND NOT is_secondary_class
                        AND NOT is_exit_row)
            )
            WHERE rn <= 2
            ORDER BY stock_code, report_date DESC
        """).fetchall()
        for r in rows:
            h.update(f"|{r['stock_code']}:{r['report_date']}".encode("utf-8"))
    except Exception:  # rule-compliance: ok evidence=legacy optional table fallback
        # 旧库可能没 fact_top10_holder_period, 忽略
        pass

    # 3. 跟踪机构集合
    try:
        ids = conn.execute(
            "SELECT id FROM inst_institutions WHERE enabled=1 AND blacklisted=0 AND merged_into IS NULL ORDER BY id"
        ).fetchall()
        for r in ids:
            h.update(f"|inst:{r['id']}".encode("utf-8"))
    except Exception:  # rule-compliance: ok evidence=legacy optional table fallback
        pass

    return h.hexdigest(), n_rows


def get_last_step_fingerprint(conn, step_id: str) -> "tuple[str | None, int | None]":
    _ensure_fingerprint_table(conn)
    row = conn.execute(
        "SELECT fingerprint, row_count FROM mart_step_fingerprint WHERE step_id = ?",
        (step_id,),
    ).fetchone()
    if not row:
        return None, None
    return row["fingerprint"], int(row["row_count"] or 0)


def update_step_fingerprint(conn, step_id: str, fingerprint: str, row_count: int) -> None:
    _ensure_fingerprint_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO mart_step_fingerprint (step_id, fingerprint, row_count, computed_at) "
        "VALUES (?, ?, ?, ?)",
        (step_id, fingerprint, int(row_count), datetime.now().isoformat()),
    )
    conn.commit()



def generate_events(conn) -> int:
    """从 inst_holdings 生成事件（优先用东财原始标记，回退到持仓量对比）"""
    logger.info("[事件] 开始生成...")
    inst_columns = _table_columns(conn, "inst_holdings")
    holder_columns = _table_columns(conn, "fact_top10_holder_period")
    event_columns = _table_columns(conn, "fact_institution_event")
    can_insert_source = {
        "notice_date_source",
        "source_notice_date",
        "availability_deadline",
    } <= event_columns
    holder_source_expr = (
        "COALESCE(NULLIF(availability_source, ''), 'unknown')"
        if "availability_source" in holder_columns
        else "'unknown'"
    )
    holder_source_sort_expr = (
        """
        CASE
            WHEN availability_source = 'source_notice' THEN 0
            WHEN availability_source = 'page_update_date' THEN 1
            WHEN availability_source = 'fetched_at_observed' THEN 2
            WHEN availability_source = 'regulatory_deadline' THEN 3
            ELSE 4
        END
        """
        if "availability_source" in holder_columns
        else "2"
    )
    holder_source_notice_expr = (
        "CASE WHEN availability_source = 'source_notice' THEN notice_date ELSE NULL END"
        if "availability_source" in holder_columns
        else "NULL"
    )
    holder_deadline_expr = (
        "CASE WHEN availability_source = 'regulatory_deadline' THEN notice_date ELSE NULL END"
        if "availability_source" in holder_columns
        else "NULL"
    )
    h_notice_source_expr = (
        "NULLIF(h.notice_date_source, '')"
        if "notice_date_source" in inst_columns
        else "NULL"
    )
    h_source_notice_expr = (
        "NULLIF(h.source_notice_date, '')"
        if "source_notice_date" in inst_columns
        else "NULL"
    )
    h_deadline_expr = (
        "NULLIF(h.availability_deadline, '')"
        if "availability_deadline" in inst_columns
        else "NULL"
    )

    rows = conn.execute(f"""
        WITH holder_notice AS (
            SELECT stock_code, report_date, holder_name, notice_date,
                   notice_date_source, source_notice_date, availability_deadline
              FROM (
                    SELECT stock_code, report_date, holder_name, notice_date,
                           {holder_source_expr} AS notice_date_source,
                           {holder_source_notice_expr} AS source_notice_date,
                           {holder_deadline_expr} AS availability_deadline,
                           ROW_NUMBER() OVER (
                               PARTITION BY stock_code, report_date, holder_name
                               ORDER BY {holder_source_sort_expr}, notice_date DESC
                           ) AS rn
                      FROM fact_top10_holder_period
                     WHERE notice_date IS NOT NULL AND notice_date != ''
                       AND holder_set = 'free'
                       AND NOT COALESCE(is_secondary_class, FALSE)
                       AND NOT COALESCE(is_exit_row, FALSE)
                   )
             WHERE rn = 1
        )
        SELECT h.institution_id, h.holder_name, h.stock_code, h.stock_name,
               h.report_date,
               COALESCE(NULLIF(h.notice_date, ''), hn.notice_date) AS notice_date,
               COALESCE({h_notice_source_expr}, hn.notice_date_source, 'unknown') AS notice_date_source,
               COALESCE({h_source_notice_expr}, hn.source_notice_date) AS source_notice_date,
               COALESCE({h_deadline_expr}, hn.availability_deadline) AS availability_deadline,
               h.hold_amount, h.hold_change, h.hold_change_num
        FROM inst_holdings h
        LEFT JOIN holder_notice hn
          ON h.stock_code = hn.stock_code
         AND h.report_date = hn.report_date
         AND h.holder_name = hn.holder_name
        WHERE h.institution_id IS NOT NULL AND h.stock_code IS NOT NULL
        ORDER BY h.institution_id, h.stock_code, h.report_date
    """).fetchall()

    if not rows:
        logger.warning("[事件] 无持仓数据")
        return 0

    groups = {}
    for r in rows:
        key = (r["institution_id"], r["stock_code"])
        if key not in groups:
            groups[key] = []
        groups[key].append(dict(r))

    now = datetime.now().isoformat()
    events = []

    for (inst_id, stock_code), records in groups.items():
        records.sort(key=lambda x: x["report_date"])

        for i, rec in enumerate(records):
            cur = float(rec["hold_amount"] or 0)

            # 优先使用东财原始标记
            raw_change = (rec.get("hold_change") or "").strip()
            event_type = _CHANGE_MAP.get(raw_change)

            if i == 0:
                prev = 0
                if not event_type:
                    event_type = "new_entry"
            else:
                prev = float(records[i-1]["hold_amount"] or 0)
                # 东财没给标记时，自己算
                if not event_type:
                    if prev == 0 and cur > 0:
                        event_type = "new_entry"
                    elif cur > prev:
                        event_type = "increase"
                    elif cur < prev:
                        event_type = "decrease"
                    else:
                        event_type = "unchanged"

            change = cur - prev
            pct = (change / prev * 100) if prev > 0 else 0

            events.append({
                "institution_id": inst_id,
                "holder_name": rec["holder_name"],
                "stock_code": stock_code,
                "stock_name": rec["stock_name"],
                "report_date": rec["report_date"],
                "notice_date": rec["notice_date"],
                "notice_date_source": rec.get("notice_date_source") or "unknown",
                "source_notice_date": rec.get("source_notice_date"),
                "availability_deadline": rec.get("availability_deadline"),
                "event_type": event_type,
                "hold_amount": cur,
                "prev_hold_amount": prev,
                "change_amount": change,
                "change_pct": round(pct, 2),
                "created_at": now,
            })

    conn.execute("BEGIN TRANSACTION")
    try:
        _rebuild_fact_institution_event_table(conn)
        if can_insert_source:
            insert_columns = [
                "institution_id", "holder_name", "stock_code", "stock_name",
                "report_date", "notice_date", "notice_date_source",
                "source_notice_date", "availability_deadline", "event_type",
                "hold_amount", "prev_hold_amount", "change_amount", "change_pct", "created_at",
            ]
        else:
            insert_columns = [
                "institution_id", "holder_name", "stock_code", "stock_name",
                "report_date", "notice_date", "event_type",
                "hold_amount", "prev_hold_amount", "change_amount", "change_pct", "created_at",
            ]
        placeholders = ", ".join("?" for _ in insert_columns)
        conn.executemany(
            f"""
            INSERT INTO fact_institution_event
            ({", ".join(insert_columns)})
            VALUES ({placeholders})
            """,
            [tuple(event[column] for column in insert_columns) for event in events],
        )
        conn.commit()
    except Exception:  # rule-compliance: ok evidence=transaction rollback then re-raise
        conn.rollback()
        raise

    counts = {}
    for e in events:
        t = e["event_type"]
        counts[t] = counts.get(t, 0) + 1
    logger.info(f"[事件] 生成 {len(events)} 条: {counts}")
    return len(events)


def generate_exit_events(conn) -> int:
    """检测退出事件：每只股票取自己最新的报告期和上一期对比，上期有该机构、最新期没有 → 退出"""
    holder_columns = _table_columns(conn, "fact_top10_holder_period")
    event_columns = _table_columns(conn, "fact_institution_event")
    can_insert_source = {
        "notice_date_source",
        "source_notice_date",
        "availability_deadline",
    } <= event_columns
    holder_source_expr = (
        "COALESCE(NULLIF(availability_source, ''), 'unknown')"
        if "availability_source" in holder_columns
        else "'unknown'"
    )
    holder_source_sort_expr = (
        """
        CASE
            WHEN availability_source = 'source_notice' THEN 0
            WHEN availability_source = 'page_update_date' THEN 1
            WHEN availability_source = 'fetched_at_observed' THEN 2
            WHEN availability_source = 'regulatory_deadline' THEN 3
            ELSE 4
        END
        """
        if "availability_source" in holder_columns
        else "2"
    )
    holder_source_notice_expr = (
        "CASE WHEN availability_source = 'source_notice' THEN notice_date ELSE NULL END"
        if "availability_source" in holder_columns
        else "NULL"
    )
    holder_deadline_expr = (
        "CASE WHEN availability_source = 'regulatory_deadline' THEN notice_date ELSE NULL END"
        if "availability_source" in holder_columns
        else "NULL"
    )

    # 每只股票最新的两个报告期
    stock_periods = conn.execute("""
        SELECT stock_code, report_date,
               ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY report_date DESC) as rn
        FROM (SELECT DISTINCT stock_code, report_date FROM fact_top10_holder_period
              WHERE stock_code IS NOT NULL
                AND holder_set = 'free'
                AND NOT is_secondary_class
                AND NOT is_exit_row)
    """).fetchall()

    # 按股票分组，取最新两期
    latest_two = {}  # stock_code -> [最新, 次新]
    for r in stock_periods:
        code = r["stock_code"]
        rn = r["rn"]
        if rn <= 2:
            if code not in latest_two:
                latest_two[code] = [None, None]
            latest_two[code][rn - 1] = r["report_date"]

    # 获取所有跟踪机构
    inst_ids = set()
    for r in conn.execute("SELECT id FROM inst_institutions WHERE enabled=1 AND blacklisted=0 AND merged_into IS NULL").fetchall():
        inst_ids.add(r["id"])

    # 获取所有 inst_holdings 的 (institution_id, stock_code, report_date) 索引
    holdings_index = set()
    holdings_detail = {}
    for r in conn.execute("""
        SELECT institution_id, stock_code, report_date, holder_name, stock_name, hold_amount
        FROM inst_holdings WHERE institution_id IS NOT NULL
    """).fetchall():
        key = (r["institution_id"], r["stock_code"], r["report_date"])
        holdings_index.add(key)
        holdings_detail[key] = r

    # 批量查出每个 (stock_code, report_date) 的公告日与来源，供 exit 事件使用。
    # 若真实公告日和监管兜底日期同时存在，优先保留真实公告日。
    notice_map = {}  # (stock_code, report_date) -> availability payload
    for r in conn.execute(f"""
        SELECT stock_code, report_date, notice_date, notice_date_source,
               source_notice_date, availability_deadline
          FROM (
                SELECT stock_code, report_date, notice_date,
                       {holder_source_expr} AS notice_date_source,
                       {holder_source_notice_expr} AS source_notice_date,
                       {holder_deadline_expr} AS availability_deadline,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_code, report_date
                           ORDER BY {holder_source_sort_expr}, notice_date DESC
                       ) AS rn
                  FROM fact_top10_holder_period
                 WHERE stock_code IS NOT NULL AND notice_date IS NOT NULL AND notice_date != ''
                   AND holder_set = 'free'
                   AND NOT is_secondary_class
                   AND NOT is_exit_row
               )
         WHERE rn = 1
    """).fetchall():
        notice_map[(r["stock_code"], r["report_date"])] = dict(r)

    now = datetime.now().isoformat()
    exits = []

    for stock_code, periods in latest_two.items():
        latest_rd = periods[0]
        prev_rd = periods[1]
        if not latest_rd or not prev_rd:
            continue

        # exit 的公告日 = 该股票最新报告期在原始数据中的公告日
        exit_notice_payload = notice_map.get((stock_code, latest_rd)) or {}
        exit_notice = exit_notice_payload.get("notice_date")

        for inst_id in inst_ids:
            prev_key = (inst_id, stock_code, prev_rd)
            latest_key = (inst_id, stock_code, latest_rd)

            # 上期有、最新期没有 → 退出
            if prev_key in holdings_index and latest_key not in holdings_index:
                prev_rec = holdings_detail[prev_key]
                prev_amt = float(prev_rec["hold_amount"] or 0)
                exits.append({
                    "institution_id": inst_id,
                    "holder_name": prev_rec["holder_name"],
                    "stock_code": stock_code,
                    "stock_name": prev_rec["stock_name"],
                    "report_date": latest_rd,
                    "notice_date": exit_notice,
                    "notice_date_source": exit_notice_payload.get("notice_date_source") or "unknown",
                    "source_notice_date": exit_notice_payload.get("source_notice_date"),
                    "availability_deadline": exit_notice_payload.get("availability_deadline"),
                    "event_type": "exit",
                    "hold_amount": 0,
                    "prev_hold_amount": prev_amt,
                    "change_amount": -prev_amt,
                    "change_pct": -100.0,
                    "created_at": now,
                })

    if exits:
        if can_insert_source:
            insert_columns = [
                "institution_id", "holder_name", "stock_code", "stock_name",
                "report_date", "notice_date", "notice_date_source",
                "source_notice_date", "availability_deadline", "event_type",
                "hold_amount", "prev_hold_amount", "change_amount", "change_pct", "created_at",
            ]
        else:
            insert_columns = [
                "institution_id", "holder_name", "stock_code", "stock_name",
                "report_date", "notice_date", "event_type",
                "hold_amount", "prev_hold_amount", "change_amount", "change_pct", "created_at",
            ]
        placeholders = ", ".join("?" for _ in insert_columns)
        conn.executemany(
            f"""
            INSERT INTO fact_institution_event
            ({", ".join(insert_columns)})
            VALUES ({placeholders})
            """,
            [tuple(event[column] for column in insert_columns) for event in exits],
        )
        conn.commit()

    logger.info(f"[事件] 退出: {len(exits)} 条")
    return len(exits)
