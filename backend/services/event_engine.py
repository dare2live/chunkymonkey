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
    except Exception:
        # 旧库可能没 fact_top10_holder_period, 忽略
        pass

    # 3. 跟踪机构集合
    try:
        ids = conn.execute(
            "SELECT id FROM inst_institutions WHERE enabled=1 AND blacklisted=0 AND merged_into IS NULL ORDER BY id"
        ).fetchall()
        for r in ids:
            h.update(f"|inst:{r['id']}".encode("utf-8"))
    except Exception:
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

    rows = conn.execute("""
        SELECT institution_id, holder_name, stock_code, stock_name,
               report_date, notice_date, hold_amount, hold_change, hold_change_num
        FROM inst_holdings
        WHERE institution_id IS NOT NULL AND stock_code IS NOT NULL
        ORDER BY institution_id, stock_code, report_date
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

            events.append((
                inst_id, rec["holder_name"], stock_code, rec["stock_name"],
                rec["report_date"], rec["notice_date"], event_type,
                cur, prev, change, round(pct, 2), now
            ))

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM fact_institution_event")
        conn.executemany("""
            INSERT INTO fact_institution_event
            (institution_id, holder_name, stock_code, stock_name,
             report_date, notice_date, event_type,
             hold_amount, prev_hold_amount, change_amount, change_pct, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, events)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    counts = {}
    for e in events:
        t = e[6]
        counts[t] = counts.get(t, 0) + 1
    logger.info(f"[事件] 生成 {len(events)} 条: {counts}")
    return len(events)


def generate_exit_events(conn) -> int:
    """检测退出事件：每只股票取自己最新的报告期和上一期对比，上期有该机构、最新期没有 → 退出"""

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

    # 批量查出每个 (stock_code, report_date) 的公告日，供 exit 事件使用
    notice_map = {}  # (stock_code, report_date) -> notice_date
    for r in conn.execute("""
        SELECT stock_code, report_date, MAX(notice_date) AS notice_date
        FROM fact_top10_holder_period
        WHERE stock_code IS NOT NULL AND notice_date IS NOT NULL AND notice_date != ''
          AND holder_set = 'free'
          AND NOT is_secondary_class
          AND NOT is_exit_row
        GROUP BY stock_code, report_date
    """).fetchall():
        notice_map[(r["stock_code"], r["report_date"])] = r["notice_date"]

    now = datetime.now().isoformat()
    exits = []

    for stock_code, periods in latest_two.items():
        latest_rd = periods[0]
        prev_rd = periods[1]
        if not latest_rd or not prev_rd:
            continue

        # exit 的公告日 = 该股票最新报告期在原始数据中的公告日
        exit_notice = notice_map.get((stock_code, latest_rd))

        for inst_id in inst_ids:
            prev_key = (inst_id, stock_code, prev_rd)
            latest_key = (inst_id, stock_code, latest_rd)

            # 上期有、最新期没有 → 退出
            if prev_key in holdings_index and latest_key not in holdings_index:
                prev_rec = holdings_detail[prev_key]
                prev_amt = float(prev_rec["hold_amount"] or 0)
                exits.append((
                    inst_id, prev_rec["holder_name"],
                    stock_code, prev_rec["stock_name"],
                    latest_rd, exit_notice, "exit",
                    0, prev_amt, -prev_amt, -100.0, now
                ))

    if exits:
        conn.executemany("""
            INSERT INTO fact_institution_event
            (institution_id, holder_name, stock_code, stock_name,
             report_date, notice_date, event_type,
             hold_amount, prev_hold_amount, change_amount, change_pct, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, exits)
        conn.commit()

    logger.info(f"[事件] 退出: {len(exits)} 条")
    return len(exits)
