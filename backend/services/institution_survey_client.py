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
  多维模型特征 survey_count_90d 用点位时间查询（raw 表直接 count）以避免 look-ahead。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger("cm-api")


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────

def _ensure_tables(conn: Any) -> None:
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


def _fetch_from_eastmoney_skill(start_date: str) -> pd.DataFrame:
    """调用 eastmoney_skill datacenter-web (替代 akshare.stock_jgdy_tj_em).

    start_date: YYYYMMDD, 返回从该日起的累计调研记录 (按 NOTICE_DATE 排序).
    返回的 DataFrame 字段名兼容旧版 (中文): 代码 / 名称 / 公告日期 / 接待日期 /
    接待方式 / 接待人员 / 接待地点 / 接待机构数量, 这样下游 _normalize* 不需改.
    """
    # P6.4 (2026-04-28): datacenter-web → miaoxiang. reportName / 字段全兼容.
    from aif10_scraper import fetch_all_pages

    # YYYYMMDD → YYYY-MM-DD
    if len(start_date) == 8 and start_date.isdigit():
        start_iso = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    else:
        start_iso = start_date

    rows = fetch_all_pages(
        report_name="RPT_ORG_SURVEYNEW",
        page_size=500,
        sort_columns="NOTICE_DATE,SECURITY_CODE",
        sort_types="-1,1",
        # 注意: filter 用单引号 + 严格 > (不支持 >=)
        extra_filters=[f"(NOTICE_DATE>'{start_iso}')"],
    )
    if not rows:
        return pd.DataFrame()

    # datacenter-web → 旧 akshare 中文列名
    # 字段映射经 2026-04-27 实地探查 (RPT_ORG_SURVEYNEW)
    norm: list[dict] = []
    for r in rows:
        norm.append({
            "代码": r.get("SECURITY_CODE"),
            "名称": r.get("SECURITY_NAME_ABBR"),
            "公告日期": r.get("NOTICE_DATE"),
            "接待日期": r.get("RECEIVE_START_DATE"),
            "接待机构数量": r.get("NUMBERNEW"),
            "接待方式": r.get("RECEIVE_WAY_EXPLAIN"),
            "接待人员": r.get("RECEPTIONIST"),
            "接待地点": r.get("RECEIVE_PLACE"),
        })
    return pd.DataFrame(norm)


# 兼容别名: 老代码可能仍引用此名 (虽然只在本文件用)
_fetch_from_akshare = _fetch_from_eastmoney_skill


# ─────────────────────────────────────────────────────────────────────
# Sync
# ─────────────────────────────────────────────────────────────────────

def sync_institution_surveys(
    conn: Any,
    days_back: int = 180,
) -> dict:
    """
    增量同步机构调研数据到 raw_institution_surveys, 并重算 mart.

    Parameters
    ----------
    conn : 当前 DuckDB 业务库连接
    days_back : 首次回溯天数 (DB 空时), 默认 180 (6 个月).
                有数据时按 DB MAX(notice_date) 增量, 不再全量回拉.

    Returns
    -------
    dict : { 'rows_fetched': int, 'rows_upserted': int, 'mart_rows': int, 'errors': list }
    """
    _ensure_tables(conn)

    # 增量起点: MAX(notice_date) - 1 天 (减 1 天容错避免错过当天延迟披露的记录).
    # DB 空时回退 days_back.
    row = conn.execute(
        "SELECT MAX(notice_date) FROM raw_institution_surveys WHERE notice_date IS NOT NULL"
    ).fetchone()
    if row and row[0]:
        latest = row[0]  # 'YYYY-MM-DD'
        try:
            base = datetime.strptime(latest[:10], "%Y-%m-%d").date()
            # MAX 当天可能还在持续披露 (披露当日下午陆续上传) → 退 1 天保险
            start_dt = base - timedelta(days=1)
        except ValueError:
            start_dt = date.today() - timedelta(days=days_back)
        start_date = start_dt.strftime("%Y%m%d")
        logger.info(f"[survey] 增量拉取 (DB 最新 notice_date={latest}, start={start_date})")
    else:
        start_date = (date.today() - timedelta(days=days_back)).strftime("%Y%m%d")
        logger.info(f"[survey] 首次全量拉取 (start_date={start_date}, days_back={days_back})")

    result: dict = {"rows_fetched": 0, "rows_upserted": 0, "mart_rows": 0, "errors": []}

    try:
        df = _fetch_from_eastmoney_skill(start_date)
    except Exception as exc:
        logger.error(f"[survey] eastmoney_skill 拉取失败: {exc}")
        result["errors"].append(f"fetch failed: {exc}")
        return result

    if df.empty:
        logger.warning("[survey] eastmoney_skill 返回空")
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
    conn: Any,
    as_of_date: Optional[str] = None,
) -> int:
    """按 as_of_date（默认今天）聚合出每股当前调研活跃度，upsert 到 mart。"""
    _ensure_tables(conn)

    if as_of_date is None:
        as_of_date = date.today().strftime("%Y-%m-%d")

    logger.info(f"[survey] 重算 mart_stock_survey_activity (as_of={as_of_date})")

    # 用单条 SQL 按 survey_date 到 as_of_date 窗口聚合
    # 注意：判空比较用 BETWEEN '(as_of - 90d)' AND as_of
    # DuckDB DATE 减法直接得 INTEGER 天数。
    sql = """
        WITH windowed AS (
            SELECT
                stock_code,
                survey_date,
                COALESCE(inst_count, 0) AS inst_count,
                CAST(CAST(? AS DATE) - CAST(survey_date AS DATE) AS INTEGER) AS days_ago
            FROM raw_institution_surveys
            WHERE survey_date <= ?
              AND CAST(CAST(? AS DATE) - CAST(survey_date AS DATE) AS INTEGER) <= 90
              AND CAST(CAST(? AS DATE) - CAST(survey_date AS DATE) AS INTEGER) >= 0
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
