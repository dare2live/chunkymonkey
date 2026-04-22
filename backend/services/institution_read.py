"""Shared read-side helpers for institution research payloads.

Keep institution list, profile list, detail, and returns-history payloads in
one backend-owned module so the research workspace reads from a single source
of truth.
"""

from collections import defaultdict
import json
import re

from services.holdings import _ensure_cache, get_inst_current_holdings, get_inst_exits
from services.industry import industry_level_alias, load_industry_map


def load_tracked_institution_names(conn) -> set[str]:
    rows = conn.execute("SELECT name, aliases FROM inst_institutions").fetchall()
    tracked_names = set()

    for row in rows:
        name = str(row["name"] or "").strip()
        if name:
            tracked_names.add(name)

        aliases_text = row["aliases"]
        if not aliases_text:
            continue
        try:
            aliases = json.loads(aliases_text)
        except Exception:
            continue
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            alias_name = str(alias or "").strip()
            if alias_name:
                tracked_names.add(alias_name)

    return tracked_names


def search_institution_candidates(conn, keywords: str, holder_type: str = "") -> dict:
    _ensure_cache(conn)

    or_groups = [group.strip() for group in re.split(r"[,，、]+", keywords) if group.strip()]
    if not or_groups:
        return {"ok": False, "message": "请输入关键词"}

    search_field = "holder_name"
    or_clauses = []
    params = []
    for group in or_groups:
        and_words = [word.strip() for word in group.split() if word.strip()]
        if not and_words:
            continue
        and_parts = []
        for word in and_words:
            and_parts.append(f"{search_field} LIKE ?")
            params.append(f"%{word}%")
        or_clauses.append("(" + " AND ".join(and_parts) + ")")

    if not or_clauses:
        return {"ok": False, "message": "请输入关键词"}

    name_where = "(" + " OR ".join(or_clauses) + ")"
    extra_where = ""
    if holder_type:
        extra_where = " AND holder_type = ?"
        params.append(holder_type)

    rows = conn.execute(
        f"""
        SELECT holder_name, holder_type, stock_count, latest_notice
        FROM _cache_holder_search
        WHERE {name_where}{extra_where}
        ORDER BY stock_count DESC, COALESCE(latest_notice, '') DESC, holder_name
        LIMIT 200
        """,
        params,
    ).fetchall()

    tracked_names = load_tracked_institution_names(conn)
    results = [
        {
            "holder_name": row["holder_name"],
            "holder_type": row["holder_type"],
            "stock_count": row["stock_count"],
            "latest_notice": row["latest_notice"],
            "tracked": row["holder_name"] in tracked_names,
        }
        for row in rows
    ]
    return {"ok": True, "data": results, "total": len(results), "keywords": or_groups}


def load_tracked_institutions(conn, show: str = "active") -> list[dict]:
    if show == "archived":
        where = "WHERE (i.merged_into IS NOT NULL OR i.blacklisted = 1 OR i.enabled = 0)"
    elif show == "all":
        where = ""
    else:
        where = "WHERE i.merged_into IS NULL AND i.blacklisted = 0 AND i.enabled = 1"

    _ensure_cache(conn)
    summary_rows = conn.execute(
        """
        SELECT h.institution_id,
               COUNT(*) AS stock_count,
               MAX(h.notice_date) AS latest_notice
        FROM inst_holdings h
        INNER JOIN (
            SELECT stock_code, max_rd
            FROM _cache_stock_latest_rd
        ) lat ON h.stock_code = lat.stock_code AND h.report_date = lat.max_rd
        GROUP BY h.institution_id
        """
    ).fetchall()
    summary_map = {row["institution_id"]: dict(row) for row in summary_rows}

    rows = []
    for inst in conn.execute(f"SELECT * FROM inst_institutions i {where}").fetchall():
        item = dict(inst)
        summary = summary_map.get(inst["id"], {})
        item["stock_count"] = summary.get("stock_count", 0)
        item["latest_notice"] = summary.get("latest_notice")
        rows.append(item)

    rows.sort(key=lambda item: item.get("stock_count") or 0, reverse=True)
    return rows


def load_institution_profiles(conn) -> list[dict]:
    # 审计 5.2 整改：exit_stats 不再读时 CTE 重算，直接读 mart_institution_profile
    # 里已沉淀的 exit_* 列（每次智能更新 build_profiles 时刷新）。
    # 退出/减持表现含义：
    #   exit_event_count        退出/减持事件数
    #   exit_post_avg_gain_*    事件公告后 N 日平均涨跌（越负越说明卖点准）
    #   exit_avoid_loss_rate_*  "避损率" = gain_*d <= 0 占比（越高越说明卖点准）
    rows = conn.execute(
        """
        SELECT p.*,
               i.display_name AS _live_display_name,
               i.type AS _live_type
        FROM mart_institution_profile p
        JOIN inst_institutions i ON p.institution_id = i.id
        WHERE i.enabled = 1 AND i.blacklisted = 0 AND i.merged_into IS NULL
        ORDER BY p.win_rate_30d DESC NULLS LAST
        """
    ).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        item["display_name"] = item.pop("_live_display_name", "") or item.get("display_name", "")
        item["inst_type"] = item.pop("_live_type", "") or item.get("inst_type", "")
        result.append(item)
    return result


def _load_institution_industry_stat_map(conn, inst_id: str) -> dict:
    rows = conn.execute(
        """
        SELECT industry_level, industry_name, avg_gain_30d, win_rate_30d
        FROM mart_institution_industry_stat
        WHERE institution_id = ?
        """,
        (inst_id,),
    ).fetchall()
    return {
        (row["industry_level"], row["industry_name"]): dict(row)
        for row in rows
    }


def _build_institution_industry_summary(conn, inst_id: str, holdings: list[dict]) -> list[dict]:
    stock_codes = [item["stock_code"] for item in holdings if item.get("event_type") != "exit"]
    if not stock_codes:
        return []

    industry_map = load_industry_map(conn)
    stat_map = _load_institution_industry_stat_map(conn, inst_id)
    tree = defaultdict(
        lambda: {
            "stocks": 0,
            "children": defaultdict(lambda: {"stocks": 0, "children": defaultdict(int)}),
        }
    )
    total_with_industry = 0

    def _industry_level_value(industry: dict, level: int) -> str:
        if not industry:
            return ""
        return industry.get(industry_level_alias(level)) or ""

    for stock_code in stock_codes:
        industry = industry_map.get(stock_code)
        if not industry:
            continue
        level1 = _industry_level_value(industry, 1)
        level2 = _industry_level_value(industry, 2)
        level3 = _industry_level_value(industry, 3)
        if not level1:
            continue
        total_with_industry += 1
        tree[level1]["stocks"] += 1
        if level2:
            tree[level1]["children"][level2]["stocks"] += 1
            if level3:
                tree[level1]["children"][level2]["children"][level3] += 1

    industry_summary = []
    for level1, level1_data in sorted(tree.items(), key=lambda item: -item[1]["stocks"]):
        item = {
            "level1": level1,
            "stock_count": level1_data["stocks"],
            "pct": round(level1_data["stocks"] / max(total_with_industry, 1) * 100, 1),
            "children": [],
        }
        level1_stat = stat_map.get(("level1", level1))
        if level1_stat:
            item["avg_gain_30d"] = level1_stat.get("avg_gain_30d")
            item["win_rate_30d"] = level1_stat.get("win_rate_30d")

        for level2, level2_data in sorted(level1_data["children"].items(), key=lambda child: -child[1]["stocks"]):
            child_item = {
                "level2": level2,
                "stock_count": level2_data["stocks"],
                "children": [],
            }
            level2_stat = stat_map.get(("level2", level2))
            if level2_stat:
                child_item["avg_gain_30d"] = level2_stat.get("avg_gain_30d")
                child_item["win_rate_30d"] = level2_stat.get("win_rate_30d")
            for level3, stock_count in sorted(level2_data["children"].items(), key=lambda child: -child[1]):
                child_item["children"].append({"level3": level3, "stock_count": stock_count})
            item["children"].append(child_item)

        industry_summary.append(item)

    return industry_summary


def load_institution_profile_detail(conn, inst_id: str) -> dict:
    holdings = list(get_inst_current_holdings(conn, inst_id))
    exits = get_inst_exits(conn, inst_id)
    for exit_row in exits:
        holdings.append(
            {
                "stock_code": exit_row["stock_code"],
                "stock_name": exit_row["stock_name"],
                "report_date": exit_row["exit_report_date"],
                "notice_date": None,
                "hold_amount": 0,
                "hold_market_cap": 0,
                "hold_ratio": None,
                "event_type": "exit",
                "change_pct": -100.0,
                "gain_10d": None,
                "gain_30d": None,
                "gain_60d": None,
                "gain_120d": None,
                "other_institutions": [],
            }
        )

    return {
        "data": holdings,
        "total": len(holdings),
        "industry_summary": _build_institution_industry_summary(conn, inst_id, holdings),
    }


def load_institution_returns_history(conn, inst_id: str) -> dict:
    rows = conn.execute(
        """
        SELECT report_date, notice_date, event_type,
               gain_10d, gain_30d, gain_60d, gain_120d,
               max_drawdown_30d
        FROM fact_institution_event
        WHERE institution_id = ? AND event_type IN ('new_entry', 'increase')
            AND gain_30d IS NOT NULL
        ORDER BY notice_date
        """,
        (inst_id,),
    ).fetchall()

    max_gain = max((row["gain_60d"] or 0) for row in rows) if rows else 0
    max_drawdown = min((-(row["max_drawdown_30d"] or 0)) for row in rows) if rows else 0
    return {
        "data": [dict(row) for row in rows],
        "max_gain": round(max_gain, 1),
        "max_drawdown": round(max_drawdown, 1),
    }