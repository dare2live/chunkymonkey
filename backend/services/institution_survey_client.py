"""
institution_survey_client.py — 机构调研数据同步（D8 维度数据源）

数据来源：akshare.stock_jgdy_tj_em(date=...)
  返回从 date 起的累计调研记录（一次调用拿全量，不需要逐日翻页）

表设计：
  raw_institution_surveys         原始调研明细（只追加，不覆盖）
  mart_stock_survey_activity      当前状态聚合（as_of_date = 同步当日）

口径：
  survey_date  接待日期（调研当天）
  notice_date  公告日期（披露日期，一般晚于 survey_date）
  inst_count   接待机构数量
  Qlib 特征 survey_count_90d 用点位时间查询（raw 表直接 count）以避免 look-ahead。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger("cm-api")


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────

def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_institution_surveys (
            stock_code          TEXT NOT NULL,
            survey_date         TEXT NOT NULL,
            notice_date         TEXT NOT NULL,
            inst_count          INTEGER,
            reception_type      TEXT,
            reception_personnel TEXT,
            reception_location  TEXT,
            stock_name          TEXT,
            ingested_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, survey_date, notice_date)
        );
        CREATE INDEX IF NOT EXISTS idx_survey_stock_date
          ON raw_institution_surveys(stock_code, survey_date);
        CREATE INDEX IF NOT EXISTS idx_survey_notice_date
          ON raw_institution_surveys(notice_date);

        CREATE TABLE IF NOT EXISTS mart_stock_survey_activity (
            stock_code         TEXT NOT NULL,
            as_of_date         TEXT NOT NULL,
            survey_count_30d   INTEGER DEFAULT 0,
            survey_count_60d   INTEGER DEFAULT 0,
            survey_count_90d   INTEGER DEFAULT 0,
            inst_count_30d     INTEGER DEFAULT 0,
            inst_count_60d     INTEGER DEFAULT 0,
            inst_count_90d     INTEGER DEFAULT 0,
            latest_survey_date TEXT,
            updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, as_of_date)
        );
        CREATE INDEX IF NOT EXISTS idx_mart_survey_stock
          ON mart_stock_survey_activity(stock_code);
    """)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────
# Fetch
# ─────────────────────────────────────────────────────────────────────

_COL_MAP = {
    "代码": "stock_code",
    "名称": "stock_name",
    "接待机构数量": "inst_count",
    "接待方式": "reception_type",
    "接待人员": "reception_personnel",
    "接待地点": "reception_location",
    "接待日期": "survey_date",
    "公告日期": "notice_date",
}


def _safe_int(val) -> Optional[int]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_str(val) -> Optional[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s if s else None


def _normalize_date(val) -> Optional[str]:
    """把 '2026-04-16' / datetime / pandas Timestamp 统一成 'YYYY-MM-DD'。"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        if isinstance(val, (datetime, date)):
            return val.strftime("%Y-%m-%d")
        s = str(val).strip()
        if not s or s.lower() == "nan":
            return None
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        parsed = datetime.strptime(s[:10], "%Y-%m-%d")
        return parsed.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _fetch_from_akshare(start_date: str) -> pd.DataFrame:
    """调用 akshare.stock_jgdy_tj_em，返回从 start_date 起的累计调研记录。"""
    import akshare as ak

    df = ak.stock_jgdy_tj_em(date=start_date)
    if df is None or df.empty:
        return pd.DataFrame()
    return df


# ─────────────────────────────────────────────────────────────────────
# Sync
# ─────────────────────────────────────────────────────────────────────

def sync_institution_surveys(
    conn: sqlite3.Connection,
    days_back: int = 180,
) -> dict:
    """
    同步近 days_back 天的机构调研数据到 raw_institution_surveys，并重算 mart。

    Parameters
    ----------
    conn : smartmoney.db 连接
    days_back : 回溯天数，默认 180（6 个月）。akshare 一次调用返回全量。

    Returns
    -------
    dict : { 'rows_fetched': int, 'rows_upserted': int, 'mart_rows': int, 'errors': list }
    """
    _ensure_tables(conn)

    start_date = (date.today() - timedelta(days=days_back)).strftime("%Y%m%d")
    logger.info(f"[survey] 拉取机构调研数据 (start_date={start_date})")

    result: dict = {"rows_fetched": 0, "rows_upserted": 0, "mart_rows": 0, "errors": []}

    try:
        df = _fetch_from_akshare(start_date)
    except Exception as exc:
        logger.error(f"[survey] akshare 拉取失败: {exc}")
        result["errors"].append(f"fetch failed: {exc}")
        return result

    if df.empty:
        logger.warning("[survey] akshare 返回空")
        return result

    result["rows_fetched"] = len(df)
    logger.info(f"[survey] 获取 {len(df)} 条原始记录，准备写库")

    rows = []
    for _, row in df.iterrows():
        stock_code = _safe_str(row.get("代码"))
        survey_date = _normalize_date(row.get("接待日期"))
        notice_date = _normalize_date(row.get("公告日期"))
        if not stock_code or not survey_date or not notice_date:
            continue
        rows.append((
            stock_code,
            survey_date,
            notice_date,
            _safe_int(row.get("接待机构数量")),
            _safe_str(row.get("接待方式")),
            _safe_str(row.get("接待人员")),
            _safe_str(row.get("接待地点")),
            _safe_str(row.get("名称")),
        ))

    if not rows:
        result["errors"].append("no valid rows after normalization")
        return result

    upsert_sql = """
        INSERT OR REPLACE INTO raw_institution_surveys
          (stock_code, survey_date, notice_date, inst_count,
           reception_type, reception_personnel, reception_location, stock_name)
        VALUES (?,?,?,?,?,?,?,?)
    """
    conn.executemany(upsert_sql, rows)
    conn.commit()
    result["rows_upserted"] = len(rows)
    logger.info(f"[survey] 写入 {len(rows)} 条到 raw_institution_surveys")

    # 重算 mart
    mart_rows = rebuild_survey_mart(conn)
    result["mart_rows"] = mart_rows

    return result


def rebuild_survey_mart(
    conn: sqlite3.Connection,
    as_of_date: Optional[str] = None,
) -> int:
    """按 as_of_date（默认今天）聚合出每股当前调研活跃度，upsert 到 mart。"""
    _ensure_tables(conn)

    if as_of_date is None:
        as_of_date = date.today().strftime("%Y-%m-%d")

    logger.info(f"[survey] 重算 mart_stock_survey_activity (as_of={as_of_date})")

    # 用单条 SQL 按 survey_date 到 as_of_date 窗口聚合
    # 注意：判空比较用 BETWEEN '(as_of - 90d)' AND as_of
    sql = """
        WITH windowed AS (
            SELECT
                stock_code,
                survey_date,
                COALESCE(inst_count, 0) AS inst_count,
                julianday(?) - julianday(survey_date) AS days_ago
            FROM raw_institution_surveys
            WHERE survey_date <= ?
              AND julianday(?) - julianday(survey_date) <= 90
              AND julianday(?) - julianday(survey_date) >= 0
        )
        SELECT
            stock_code,
            SUM(CASE WHEN days_ago <= 30 THEN 1 ELSE 0 END) AS survey_count_30d,
            SUM(CASE WHEN days_ago <= 60 THEN 1 ELSE 0 END) AS survey_count_60d,
            COUNT(*) AS survey_count_90d,
            SUM(CASE WHEN days_ago <= 30 THEN inst_count ELSE 0 END) AS inst_count_30d,
            SUM(CASE WHEN days_ago <= 60 THEN inst_count ELSE 0 END) AS inst_count_60d,
            SUM(inst_count) AS inst_count_90d,
            MAX(survey_date) AS latest_survey_date
        FROM windowed
        GROUP BY stock_code
    """
    cursor = conn.execute(sql, (as_of_date, as_of_date, as_of_date, as_of_date))
    agg_rows = cursor.fetchall()

    conn.execute(
        "DELETE FROM mart_stock_survey_activity WHERE as_of_date = ?",
        (as_of_date,),
    )

    if agg_rows:
        insert_sql = """
            INSERT INTO mart_stock_survey_activity
              (stock_code, as_of_date,
               survey_count_30d, survey_count_60d, survey_count_90d,
               inst_count_30d, inst_count_60d, inst_count_90d,
               latest_survey_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        payload = [
            (
                r[0], as_of_date,
                r[1], r[2], r[3],
                r[4] or 0, r[5] or 0, r[6] or 0,
                r[7],
            )
            for r in agg_rows
        ]
        conn.executemany(insert_sql, payload)

    conn.commit()
    logger.info(f"[survey] mart 入库 {len(agg_rows)} 条")
    return len(agg_rows)
