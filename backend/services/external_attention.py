"""
external_attention.py — 外部关注事实层（Phase 1）

第一阶段只落两类能力：
1. 批量快照：千股千评 + 机构调研统计，沉成可重算快照表
2. 单股验证：按需拉取关注度/参与度/评分历史、研报、新闻与个股信息

当前不接评分，不改前端主流程，只提供稳定的数据底座与验证接口。
"""

import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger("cm-api")
_AKSHARE_CACHE_TTL_SEC = 300
_AKSHARE_CACHE: dict[tuple, tuple[float, pd.DataFrame]] = {}


def ensure_tables(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS fact_stock_attention_snapshot (
            snapshot_date             TEXT NOT NULL,
            stock_code                TEXT NOT NULL,
            stock_name                TEXT,
            comment_trade_date        TEXT,
            latest_price              REAL,
            change_pct                REAL,
            turnover_rate             REAL,
            pe_ratio                  REAL,
            main_cost                 REAL,
            institution_participation REAL,
            composite_score           REAL,
            rank_change               REAL,
            current_rank              INTEGER,
            focus_index               REAL,
            survey_count_30d          INTEGER DEFAULT 0,
            survey_count_90d          INTEGER DEFAULT 0,
            survey_org_total_30d      INTEGER DEFAULT 0,
            survey_org_total_90d      INTEGER DEFAULT 0,
            last_survey_date          TEXT,
            last_survey_notice_date   TEXT,
            last_survey_reception     TEXT,
            comment_available         INTEGER DEFAULT 0,
            survey_available          INTEGER DEFAULT 0,
            updated_at                TEXT,
            PRIMARY KEY (snapshot_date, stock_code)
        );
        CREATE INDEX IF NOT EXISTS idx_fsas_code ON fact_stock_attention_snapshot(stock_code);
        CREATE INDEX IF NOT EXISTS idx_fsas_snapshot ON fact_stock_attention_snapshot(snapshot_date);

        CREATE TABLE IF NOT EXISTS dim_stock_attention_latest (
            stock_code                TEXT PRIMARY KEY,
            snapshot_date             TEXT,
            stock_name                TEXT,
            comment_trade_date        TEXT,
            latest_price              REAL,
            change_pct                REAL,
            turnover_rate             REAL,
            pe_ratio                  REAL,
            main_cost                 REAL,
            institution_participation REAL,
            composite_score           REAL,
            rank_change               REAL,
            current_rank              INTEGER,
            focus_index               REAL,
            survey_count_30d          INTEGER DEFAULT 0,
            survey_count_90d          INTEGER DEFAULT 0,
            survey_org_total_30d      INTEGER DEFAULT 0,
            survey_org_total_90d      INTEGER DEFAULT 0,
            last_survey_date          TEXT,
            last_survey_notice_date   TEXT,
            last_survey_reception     TEXT,
            comment_available         INTEGER DEFAULT 0,
            survey_available          INTEGER DEFAULT 0,
            updated_at                TEXT
        );
        """
    )
    conn.commit()


def _normalize_stock_code(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "." in text:
        text = text.split(".", 1)[0]
    if text.isdigit() and len(text) < 6:
        text = text.zfill(6)
    return text


def _safe_float(value) -> Optional[float]:
    if value in (None, "", "None"):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value) -> Optional[int]:
    number = _safe_float(value)
    if number is None:
        return None
    try:
        return int(round(number))
    except Exception:
        return None


def _safe_text(value) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _coerce_datetime(value) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce")
    except Exception:
        return None
    if ts is None or pd.isna(ts):
        return None
    if hasattr(ts, "to_pydatetime"):
        return ts.to_pydatetime()
    return ts


def _fmt_date(value) -> Optional[str]:
    dt = _coerce_datetime(value)
    if not dt:
        return None
    return dt.strftime("%Y-%m-%d")


def _normalize_percentage(value) -> Optional[float]:
    number = _safe_float(value)
    if number is None:
        return None
    if -1.5 <= number <= 1.5:
        number *= 100.0
    return round(max(min(number, 100.0), 0.0), 4)


def _akshare_cache_key(func_name: str, args: tuple, kwargs: dict) -> tuple:
    return (
        func_name,
        tuple(repr(arg) for arg in args),
        tuple(sorted((key, repr(value)) for key, value in kwargs.items())),
    )


def _akshare_cache_get(cache_key: tuple, *, allow_stale: bool = False) -> Optional[pd.DataFrame]:
    cached = _AKSHARE_CACHE.get(cache_key)
    if not cached:
        return None
    cached_at, value = cached
    if allow_stale or time.time() - cached_at <= _AKSHARE_CACHE_TTL_SEC:
        return value.copy()
    _AKSHARE_CACHE.pop(cache_key, None)
    return None


def _akshare_cache_put(cache_key: tuple, value: pd.DataFrame) -> pd.DataFrame:
    cached_value = value.copy()
    _AKSHARE_CACHE[cache_key] = (time.time(), cached_value)
    return cached_value.copy()


def _call_akshare_df(func_name: str, *args, retries: int = 2, retry_wait: float = 0.8, **kwargs) -> Optional[pd.DataFrame]:
    cache_key = _akshare_cache_key(func_name, args, kwargs)
    cached = _akshare_cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        import akshare as ak
    except Exception as exc:
        logger.warning(f"[外部关注] akshare 不可用: {exc}")
        return None

    func = getattr(ak, func_name, None)
    if func is None:
        logger.warning(f"[外部关注] akshare 缺少接口: {func_name}")
        return None

    last_error = None
    for attempt in range(retries + 1):
        try:
            result = func(*args, **kwargs)
            break
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                stale_cached = _akshare_cache_get(cache_key, allow_stale=True)
                if stale_cached is not None:
                    logger.warning(f"[外部关注] {func_name} 调用失败，回退到进程缓存: {exc}")
                    return stale_cached
                logger.warning(f"[外部关注] {func_name} 调用失败: {exc}")
                return None
            time.sleep(retry_wait * (attempt + 1))
    else:
        if last_error is not None:
            logger.warning(f"[外部关注] {func_name} 调用失败: {last_error}")
        return None

    if result is None:
        return None
    if isinstance(result, pd.DataFrame):
        return _akshare_cache_put(cache_key, result)
    try:
        return _akshare_cache_put(cache_key, pd.DataFrame(result))
    except Exception:
        return None


def _load_stock_name_map(conn) -> dict[str, str]:
    rows = conn.execute(
        "SELECT stock_code, stock_name FROM dim_active_a_stock WHERE stock_code IS NOT NULL"
    ).fetchall()
    mapping = {
        _normalize_stock_code(row["stock_code"]): _safe_text(row["stock_name"]) or ""
        for row in rows
        if _normalize_stock_code(row["stock_code"])
    }
    if mapping:
        return mapping

    fallback_rows = conn.execute(
        "SELECT DISTINCT stock_code, stock_name FROM market_raw_holdings WHERE stock_code IS NOT NULL"
    ).fetchall()
    return {
        _normalize_stock_code(row["stock_code"]): _safe_text(row["stock_name"]) or ""
        for row in fallback_rows
        if _normalize_stock_code(row["stock_code"])
    }


def _normalize_comment_snapshot(df: Optional[pd.DataFrame]) -> dict[str, dict]:
    if df is None or df.empty:
        return {}

    results = {}
    for _, row in df.iterrows():
        code = _normalize_stock_code(row.get("代码"))
        if not code:
            continue
        results[code] = {
            "stock_name": _safe_text(row.get("名称")),
            "comment_trade_date": _fmt_date(row.get("交易日")),
            "latest_price": _safe_float(row.get("最新价")),
            "change_pct": _safe_float(row.get("涨跌幅")),
            "turnover_rate": _safe_float(row.get("换手率")),
            "pe_ratio": _safe_float(row.get("市盈率")),
            "main_cost": _safe_float(row.get("主力成本")),
            "institution_participation": _normalize_percentage(row.get("机构参与度")),
            "composite_score": _safe_float(row.get("综合得分")),
            "rank_change": _safe_float(row.get("上升")),
            "current_rank": _safe_int(row.get("目前排名")),
            "focus_index": _safe_float(row.get("关注指数")),
            "comment_available": 1,
        }
    return results


def _aggregate_survey_snapshot(df: Optional[pd.DataFrame]) -> dict[str, dict]:
    if df is None or df.empty:
        return {}

    today = date.today()
    cutoff_30 = today - timedelta(days=30)
    cutoff_90 = today - timedelta(days=90)
    results = {}

    for _, row in df.iterrows():
        code = _normalize_stock_code(row.get("代码"))
        if not code:
            continue
        survey_dt = _coerce_datetime(row.get("接待日期")) or _coerce_datetime(row.get("公告日期"))
        survey_day = survey_dt.date() if survey_dt else None
        if survey_day and survey_day < cutoff_90:
            continue

        stat = results.setdefault(
            code,
            {
                "survey_count_30d": 0,
                "survey_count_90d": 0,
                "survey_org_total_30d": 0,
                "survey_org_total_90d": 0,
                "last_survey_date": None,
                "last_survey_notice_date": None,
                "last_survey_reception": None,
                "_latest_survey_dt": None,
                "survey_available": 0,
            },
        )
        org_count = _safe_int(row.get("接待机构数量")) or 0
        survey_reception = _safe_text(row.get("接待方式"))
        notice_date = _fmt_date(row.get("公告日期"))

        if survey_day:
            stat["survey_available"] = 1
            stat["survey_count_90d"] += 1
            stat["survey_org_total_90d"] += org_count
            if survey_day >= cutoff_30:
                stat["survey_count_30d"] += 1
                stat["survey_org_total_30d"] += org_count

            current_latest = stat.get("_latest_survey_dt")
            if current_latest is None or survey_dt > current_latest:
                stat["_latest_survey_dt"] = survey_dt
                stat["last_survey_date"] = survey_dt.strftime("%Y-%m-%d")
                stat["last_survey_notice_date"] = notice_date
                stat["last_survey_reception"] = survey_reception

    for stat in results.values():
        stat.pop("_latest_survey_dt", None)
    return results


def sync_external_attention_snapshot(conn) -> int:
    ensure_tables(conn)

    snapshot_date = date.today().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()
    stock_name_map = _load_stock_name_map(conn)
    survey_start = (date.today() - timedelta(days=90)).strftime("%Y%m%d")

    comment_map = _normalize_comment_snapshot(_call_akshare_df("stock_comment_em"))
    survey_map = _aggregate_survey_snapshot(_call_akshare_df("stock_jgdy_tj_em", date=survey_start))

    codes = sorted(set(stock_name_map) | set(comment_map) | set(survey_map))
    if not codes:
        logger.warning("[外部关注] 无可写入股票，跳过快照构建")
        return 0

    rows = []
    for code in codes:
        comment = comment_map.get(code) or {}
        survey = survey_map.get(code) or {}
        stock_name = comment.get("stock_name") or stock_name_map.get(code) or ""
        if not comment and not survey:
            continue
        rows.append(
            (
                snapshot_date,
                code,
                stock_name,
                comment.get("comment_trade_date"),
                comment.get("latest_price"),
                comment.get("change_pct"),
                comment.get("turnover_rate"),
                comment.get("pe_ratio"),
                comment.get("main_cost"),
                comment.get("institution_participation"),
                comment.get("composite_score"),
                comment.get("rank_change"),
                comment.get("current_rank"),
                comment.get("focus_index"),
                survey.get("survey_count_30d", 0),
                survey.get("survey_count_90d", 0),
                survey.get("survey_org_total_30d", 0),
                survey.get("survey_org_total_90d", 0),
                survey.get("last_survey_date"),
                survey.get("last_survey_notice_date"),
                survey.get("last_survey_reception"),
                int(comment.get("comment_available") or 0),
                int(survey.get("survey_available") or 0),
                now,
            )
        )

    conn.execute("DELETE FROM fact_stock_attention_snapshot WHERE snapshot_date = ?", (snapshot_date,))
    conn.executemany(
        """
        INSERT OR REPLACE INTO fact_stock_attention_snapshot (
            snapshot_date, stock_code, stock_name, comment_trade_date,
            latest_price, change_pct, turnover_rate, pe_ratio, main_cost,
            institution_participation, composite_score, rank_change,
            current_rank, focus_index, survey_count_30d, survey_count_90d,
            survey_org_total_30d, survey_org_total_90d, last_survey_date,
            last_survey_notice_date, last_survey_reception, comment_available,
            survey_available, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO dim_stock_attention_latest (
            stock_code, snapshot_date, stock_name, comment_trade_date,
            latest_price, change_pct, turnover_rate, pe_ratio, main_cost,
            institution_participation, composite_score, rank_change,
            current_rank, focus_index, survey_count_30d, survey_count_90d,
            survey_org_total_30d, survey_org_total_90d, last_survey_date,
            last_survey_notice_date, last_survey_reception, comment_available,
            survey_available, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row[1],
                row[0],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
                row[11],
                row[12],
                row[13],
                row[14],
                row[15],
                row[16],
                row[17],
                row[18],
                row[19],
                row[20],
                row[21],
                row[22],
                row[23],
            )
            for row in rows
        ],
    )
    conn.commit()

    comment_count = sum(1 for row in rows if row[21])
    survey_count = sum(1 for row in rows if row[22])
    logger.info(
        f"[外部关注] 快照完成: {len(rows)} 只股票, 千股千评={comment_count}, 调研统计={survey_count}"
    )
    return len(rows)


def get_latest_stock_attention(conn, stock_code: str) -> Optional[dict]:
    ensure_tables(conn)
    row = conn.execute(
        "SELECT * FROM dim_stock_attention_latest WHERE stock_code = ? LIMIT 1",
        (_normalize_stock_code(stock_code),),
    ).fetchone()
    return dict(row) if row else None


def _build_series(df: Optional[pd.DataFrame], value_columns: tuple[str, ...], date_columns: tuple[str, ...]) -> list[dict]:
    if df is None or df.empty:
        return []

    points = []
    for _, row in df.iterrows():
        dt = None
        for column in date_columns:
            dt = _coerce_datetime(row.get(column))
            if dt:
                break
        value = None
        for column in value_columns:
            value = _safe_float(row.get(column))
            if value is not None:
                break
        if not dt or value is None:
            continue
        points.append({"date": dt.strftime("%Y-%m-%d"), "value": round(value, 4)})

    points.sort(key=lambda item: item["date"])
    return points[-60:]


def _summarize_series(points: list[dict]) -> Optional[dict]:
    if not points:
        return None

    current = points[-1]["value"]
    delta_5d = round(current - points[-6]["value"], 4) if len(points) >= 6 else None
    delta_20d = round(current - points[-21]["value"], 4) if len(points) >= 21 else None
    values = [item["value"] for item in points]
    return {
        "current": current,
        "delta_5d": delta_5d,
        "delta_20d": delta_20d,
        "min_60d": min(values),
        "max_60d": max(values),
        "points": points,
    }


def _pivot_basic_info(df: Optional[pd.DataFrame]) -> dict:
    if df is None or df.empty:
        return {}
    result = {}
    for _, row in df.iterrows():
        key = _safe_text(row.get("item"))
        value = row.get("value")
        if key:
            result[key.replace(" ", "")] = value
    return result


def _summarize_research_reports(df: Optional[pd.DataFrame]) -> dict:
    if df is None or df.empty:
        return {
            "count_total": 0,
            "count_30d": 0,
            "count_90d": 0,
            "latest_date": None,
            "institutions_90d": [],
            "rating_breakdown_90d": [],
            "monthly_report_count_hint": None,
        }

    today = date.today()
    cutoff_30 = today - timedelta(days=30)
    cutoff_90 = today - timedelta(days=90)
    rating_counter = Counter()
    institution_counter = Counter()
    count_30d = 0
    count_90d = 0
    latest_date = None
    monthly_hint = None

    for _, row in df.iterrows():
        row_dt = _coerce_datetime(row.get("日期"))
        if row_dt:
            row_day = row_dt.date()
            latest_date = max(latest_date, row_day) if latest_date else row_day
            if row_day >= cutoff_90:
                count_90d += 1
                institution = _safe_text(row.get("机构"))
                rating = _safe_text(row.get("东财评级"))
                if institution:
                    institution_counter[institution] += 1
                if rating:
                    rating_counter[rating] += 1
                if row_day >= cutoff_30:
                    count_30d += 1
        monthly_hint = monthly_hint or _safe_int(row.get("近一月个股研报数"))

    return {
        "count_total": int(len(df)),
        "count_30d": count_30d,
        "count_90d": count_90d,
        "latest_date": latest_date.strftime("%Y-%m-%d") if latest_date else None,
        "institutions_90d": [
            {"name": name, "count": count}
            for name, count in institution_counter.most_common(8)
        ],
        "rating_breakdown_90d": [
            {"rating": rating, "count": count}
            for rating, count in rating_counter.most_common(8)
        ],
        "monthly_report_count_hint": monthly_hint,
    }


def _summarize_news(df: Optional[pd.DataFrame]) -> dict:
    if df is None or df.empty:
        return {
            "count_total": 0,
            "count_7d": 0,
            "count_30d": 0,
            "latest_time": None,
            "top_sources": [],
        }

    now = datetime.now()
    cutoff_7d = now - timedelta(days=7)
    cutoff_30d = now - timedelta(days=30)
    count_7d = 0
    count_30d = 0
    latest_dt = None
    source_counter = Counter()

    for _, row in df.iterrows():
        published_at = _coerce_datetime(row.get("发布时间"))
        source = _safe_text(row.get("文章来源"))
        if source:
            source_counter[source] += 1
        if not published_at:
            continue
        latest_dt = max(latest_dt, published_at) if latest_dt else published_at
        if published_at >= cutoff_30d:
            count_30d += 1
        if published_at >= cutoff_7d:
            count_7d += 1

    return {
        "count_total": int(len(df)),
        "count_7d": count_7d,
        "count_30d": count_30d,
        "latest_time": latest_dt.strftime("%Y-%m-%d %H:%M:%S") if latest_dt else None,
        "top_sources": [
            {"name": name, "count": count}
            for name, count in source_counter.most_common(8)
        ],
    }


def _clip_text(value: object, limit: int = 60) -> str:
    text = _safe_text(value) or ""
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip() + "…"


def _spread_timeline_events(items: list[dict], max_items: int = 10) -> list[dict]:
    if len(items) <= max_items:
        return items
    if max_items <= 1:
        return [items[-1]]
    step = (len(items) - 1) / float(max_items - 1)
    picked_indexes = []
    for idx in range(max_items):
        picked_indexes.append(min(len(items) - 1, round(idx * step)))
    deduped_indexes = []
    seen = set()
    for index in picked_indexes:
        if index in seen:
            continue
        seen.add(index)
        deduped_indexes.append(index)
    return [items[index] for index in deduped_indexes]


def _build_research_timeline(df: Optional[pd.DataFrame], max_items: int = 10) -> list[dict]:
    if df is None or df.empty:
        return []

    items = []
    seen = set()
    for _, row in df.iterrows():
        row_dt = _coerce_datetime(row.get("日期"))
        if not row_dt:
            continue
        date_text = row_dt.strftime("%Y-%m-%d")
        institution = _safe_text(row.get("机构"))
        rating = _safe_text(row.get("东财评级")) or _safe_text(row.get("评级"))
        report_title = _clip_text(row.get("报告名称") or row.get("标题") or row.get("报告标题"), 44)
        target_price = _safe_float(row.get("目标价") or row.get("预测目标价"))
        body_parts = []
        if report_title:
            body_parts.append(report_title)
        if institution:
            body_parts.append(institution)
        if rating:
            body_parts.append(rating)
        if target_price is not None:
            body_parts.append(f"目标价 {target_price:.2f}")
        body = " · ".join(body_parts) or "个股研报更新"
        item = {
            "date": date_text,
            "lane": "research",
            "tone": "research",
            "title": "个股研报",
            "body": body,
        }
        dedupe_key = (item["date"], item["title"], item["body"])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append(item)

    items.sort(key=lambda item: item["date"])
    return _spread_timeline_events(items, max_items=max_items)


def _build_news_timeline(df: Optional[pd.DataFrame], max_items: int = 10) -> list[dict]:
    if df is None or df.empty:
        return []

    items = []
    seen = set()
    for _, row in df.iterrows():
        row_dt = _coerce_datetime(row.get("发布时间"))
        if not row_dt:
            continue
        date_text = row_dt.strftime("%Y-%m-%d %H:%M:%S")
        source = _safe_text(row.get("文章来源")) or _safe_text(row.get("来源"))
        title = _clip_text(row.get("标题") or row.get("新闻标题") or row.get("摘要"), 46)
        content = _clip_text(row.get("新闻内容") or row.get("内容") or row.get("摘要"), 56)
        body_parts = []
        if source:
            body_parts.append(source)
        if title:
            body_parts.append(title)
        if content and content != title:
            body_parts.append(content)
        body = " · ".join(body_parts) or "新闻脉冲更新"
        item = {
            "date": date_text,
            "lane": "news",
            "tone": "news",
            "title": "新闻脉冲",
            "body": body,
        }
        dedupe_key = (item["date"], item["title"], item["body"])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append(item)

    items.sort(key=lambda item: item["date"])
    return _spread_timeline_events(items, max_items=max_items)


def fetch_stock_attention_detail(stock_code: str) -> dict:
    code = _normalize_stock_code(stock_code)
    diagnostics = {}

    basic_df = _call_akshare_df("stock_individual_info_em", symbol=code)
    diagnostics["basic_info"] = {"ok": basic_df is not None and not basic_df.empty}
    basic_info = _pivot_basic_info(basic_df)

    jgcyd_df = _call_akshare_df("stock_comment_detail_zlkp_jgcyd_em", symbol=code)
    diagnostics["institution_participation"] = {"ok": jgcyd_df is not None and not jgcyd_df.empty}

    rating_df = _call_akshare_df("stock_comment_detail_zhpj_lspf_em", symbol=code)
    diagnostics["rating_score"] = {"ok": rating_df is not None and not rating_df.empty}

    focus_df = _call_akshare_df("stock_comment_detail_scrd_focus_em", symbol=code)
    diagnostics["focus_index"] = {"ok": focus_df is not None and not focus_df.empty}

    desire_df = _call_akshare_df("stock_comment_detail_scrd_desire_em", symbol=code)
    diagnostics["desire_index"] = {"ok": desire_df is not None and not desire_df.empty}

    research_df = _call_akshare_df("stock_research_report_em", symbol=code)
    diagnostics["research_report"] = {"ok": research_df is not None and not research_df.empty}

    news_df = _call_akshare_df("stock_news_em", symbol=code)
    diagnostics["news"] = {"ok": news_df is not None and not news_df.empty}

    institution_participation = _summarize_series(
        _build_series(jgcyd_df, ("机构参与度",), ("交易日", "日期"))
    )
    rating_score = _summarize_series(
        _build_series(rating_df, ("评分",), ("交易日", "日期"))
    )
    focus_index = _summarize_series(
        _build_series(focus_df, ("用户关注指数", "关注指数"), ("交易日", "日期"))
    )
    desire_index = _summarize_series(
        _build_series(desire_df, ("市场参与意愿", "参与意愿"), ("交易日", "日期"))
    )

    stock_name = _safe_text(basic_info.get("股票简称")) or _safe_text(basic_info.get("名称"))
    research_timeline = _build_research_timeline(research_df)
    news_timeline = _build_news_timeline(news_df)
    return {
        "stock_code": code,
        "stock_name": stock_name,
        "basic_info": basic_info,
        "series": {
            "institution_participation": institution_participation,
            "rating_score": rating_score,
            "focus_index": focus_index,
            "desire_index": desire_index,
        },
        "research": _summarize_research_reports(research_df),
        "news": _summarize_news(news_df),
        "timeline_events": sorted(
            research_timeline + news_timeline,
            key=lambda item: item.get("date") or "",
        ),
        "diagnostics": diagnostics,
    }