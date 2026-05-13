"""v3 设计稿专用聚合 API。

仅为前端 v3-data-live.jsx 服务。每个端点返回 v3 mock 字段需要的 shape，
聚合现有 mart_* / fact_* 数据。

当 v3 退役或某字段有了专用 mart 后，对应端点可整体删除。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from services.db import get_conn

logger = logging.getLogger("cm-api.v3-meta")
router = APIRouter()


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _next_trading_day(conn, after_date: str) -> str | None:
    """返回 after_date 之后第一个 is_trading=1 的交易日。"""
    if not _table_exists(conn, "dim_trading_calendar"):
        return None
    row = conn.execute(
        """
        SELECT trade_date FROM dim_trading_calendar
         WHERE trade_date > ? AND is_trading = 1
         ORDER BY trade_date LIMIT 1
        """,
        (after_date,),
    ).fetchone()
    return row[0] if row else None


@router.get("/labels")
async def get_labels():
    """UI 标签字典 (后端字段 → 中文短名). 前端启动时拉一次注入 window.CMV3.LABELS。"""
    from services.ui_labels import get_labels as _g
    labels = _g()
    return {"ok": True, "data": labels, "total": len(labels)}


@router.get("/run-meta")
async def get_run_meta():
    """v3 首页 RUN_META 区块的聚合数据。

    映射 v3-data.jsx 的 CMV3.RUN_META 字段:
      - plan_date (T+1 交易日)
      - signal_date (最近推荐 snapshot_date)
      - built_at (最近 cron_daily 完成时间)
      - duration_min (最近一次 cron_daily 总耗时分钟)
      - nav / nav_chg_pct / vs_hs300_pct / vs_eq_pct (Paper Engine 未起，全 null)
      - challenger_pending (mart_model_lifecycle status='challenger' 数)
      - system_alerts (空数组占位)
    """
    conn = get_conn()
    try:
        data: dict[str, Any] = {
            "plan_date": None,
            "signal_date": None,
            "built_at": None,
            "duration_min": None,
            "nav": None,
            "nav_chg_pct": None,
            "vs_hs300_pct": None,
            "vs_eq_pct": None,
            "challenger_pending": 0,
            "system_alerts": [],
        }

        # 最新推荐 snapshot_date = signal_date
        if _table_exists(conn, "mart_daily_recommendation"):
            row = conn.execute(
                "SELECT MAX(snapshot_date) FROM mart_daily_recommendation"
            ).fetchone()
            if row and row[0]:
                data["signal_date"] = row[0]
                data["plan_date"] = _next_trading_day(conn, row[0]) or row[0]

        # 最近 cron_daily 跑批 (mart_pipeline_run_manifest)
        if _table_exists(conn, "mart_pipeline_run_manifest"):
            row = conn.execute(
                """
                SELECT ended_at, duration_s
                  FROM mart_pipeline_run_manifest
                 WHERE pipeline_name = 'cron_daily' AND status IN ('success','warn')
                 ORDER BY ended_at DESC NULLS LAST LIMIT 1
                """
            ).fetchone()
            if row:
                data["built_at"] = row[0]
                if row[1] is not None:
                    data["duration_min"] = round(float(row[1]) / 60.0, 1)

        # challenger 待审数
        if _table_exists(conn, "mart_model_lifecycle"):
            row = conn.execute(
                "SELECT COUNT(*) FROM mart_model_lifecycle WHERE status = 'challenger'"
            ).fetchone()
            if row:
                data["challenger_pending"] = int(row[0])

        return {"ok": True, "data": data}
    finally:
        conn.close()


@router.get("/significant")
async def get_significant_holders(limit: int = Query(20, ge=1, le=100)):
    """聚合 fact_top10_holder_period 找显著股东 (mart_significant_holder_all 占位实现)。

    判定标准 (v1 粗略):
      - 跟踪机构 (inst_holdings 持仓数 >= 3) OR
      - 全市场出现在 >= 3 只股票的十大流通股东 OR
      - 单只股票持仓金额 >= 5 亿

    输出 v3 CMV3.SIGNIFICANT_HOLDERS 的 shape。
    """
    conn = get_conn()
    try:
        if not _table_exists(conn, "fact_top10_holder_period"):
            return {"ok": True, "data": [], "total": 0}

        # 跟踪机构: 从 inst_holdings 找出当前持仓 >= 1 只的机构
        # 给 alias 用 inst_institutions.display_name
        tracked_rows = []
        if _table_exists(conn, "mart_institution_profile") and _table_exists(conn, "inst_institutions"):
            tracked_rows = conn.execute(
                """
                SELECT
                    p.institution_id AS id,
                    COALESCE(i.display_name, p.institution_name) AS name,
                    COALESCE(i.type, p.inst_type) AS type,
                    p.current_stock_count AS holdings,
                    p.win_rate_60d AS win60,
                    p.win_rate_30d AS win30,
                    p.latest_notice_date AS latest_date,
                    p.recent_increase_count AS recent_inc,
                    p.recent_new_entry_count AS recent_new,
                    1 AS tracked
                FROM mart_institution_profile p
                LEFT JOIN inst_institutions i ON i.id = p.institution_id
                WHERE COALESCE(p.current_stock_count, 0) >= 1
                ORDER BY p.win_rate_60d DESC NULLS LAST
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        out = []
        for r in tracked_rows:
            recent_inc = int(r["recent_inc"] or 0)
            recent_new = int(r["recent_new"] or 0)
            action_parts = []
            if recent_new:
                action_parts.append(f"新建 {recent_new}")
            if recent_inc:
                action_parts.append(f"增持 {recent_inc}")
            last_action = (
                (str(r["latest_date"] or "") + (" " if r["latest_date"] and action_parts else "") + " / ".join(action_parts))
                if action_parts or r["latest_date"]
                else "—"
            )
            out.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "type": r["type"] or "—",
                    "holdings": int(r["holdings"] or 0),
                    "win60": (float(r["win60"]) / 100.0) if r["win60"] is not None else 0.0,
                    "stability": 0.80,  # mart_institution_profile 暂无 stability_score 字段
                    "last_action": last_action,
                    "tracked": bool(r["tracked"]),
                }
            )

        return {"ok": True, "data": out, "total": len(out)}
    finally:
        conn.close()


@router.get("/health")
async def v3_health():
    """v3 router 自检，用于前端启动时确认 backend 可达。"""
    return {
        "ok": True,
        "service": "v3-meta",
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# 公式元信息 + 当日命中 + 历史 horizon 胜率
FORMULA_METADATA: list[dict] = [
    {"id": "macd_golden_cross",          "name": "MACD 金叉",        "tag": "MA"},
    {"id": "turtle_breakout_20",         "name": "海龟突破 20 日",   "tag": "T2"},
    {"id": "turtle_breakout_55",         "name": "海龟突破 55 日",   "tag": "T5"},
    {"id": "dynamic_ma_iterative_cross", "name": "动态均线迭代金叉", "tag": "DM"},
]


@router.get("/formulas")
async def get_formulas():
    """v3 选股台公式库 tab 的聚合数据。

    返回每个公式:
      - id / name / tag
      - hit_today (今日命中股票数, 来自 fact_technical_trigger)
      - win_rate (历史胜率, 用 default_horizon 取值)
      - horizon (default_horizon_days)
      - n_signals (历史总信号数)
    """
    conn = get_conn()
    try:
        out = []
        # 找最新信号日
        if _table_exists(conn, "fact_technical_trigger"):
            latest_date_row = conn.execute(
                "SELECT MAX(date) FROM fact_technical_trigger"
            ).fetchone()
            latest_date = latest_date_row[0] if latest_date_row else None
        else:
            latest_date = None

        for meta in FORMULA_METADATA:
            fid = meta["id"]
            hit_today = 0
            win_rate = 0.0
            horizon = 20
            n_total = 0

            if _table_exists(conn, "fact_technical_trigger") and latest_date:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM fact_technical_trigger
                     WHERE formula_id = ? AND date = ?
                    """,
                    (fid, latest_date),
                ).fetchone()
                hit_today = int(row[0] or 0)
                row = conn.execute(
                    "SELECT COUNT(*) FROM fact_technical_trigger WHERE formula_id = ?",
                    (fid,),
                ).fetchone()
                n_total = int(row[0] or 0)

            # 取该公式最高 sharpe 的 horizon (从 mart_formula_horizon_evidence)
            if _table_exists(conn, "mart_formula_horizon_evidence"):
                row = conn.execute(
                    """
                    SELECT holding_days, win_rate
                      FROM mart_formula_horizon_evidence
                     WHERE formula_id = ?
                     ORDER BY sharpe DESC NULLS LAST LIMIT 1
                    """,
                    (fid,),
                ).fetchone()
                if row:
                    horizon = int(row[0] or 20)
                    win_rate = float(row[1] or 0)

            out.append({
                "id": fid,
                "name": meta["name"],
                "tag": meta["tag"],
                "hit_today": hit_today,
                "win_rate": win_rate,
                "horizon": horizon,
                "n_signals_total": n_total,
                "state_dist": None,
            })

        return {
            "ok": True,
            "data": out,
            "latest_date": latest_date,
            "total": len(out),
        }
    finally:
        conn.close()


@router.get("/fitness")
async def get_fitness_matrix(limit: int = Query(800, ge=1, le=2000)):
    """v3 选股台适配矩阵 tab 的数据。

    返回 mart_stage_formula_fitness 全部行 (按 win_rate 排序)。
    """
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_stage_formula_fitness"):
            return {"ok": True, "data": [], "total": 0}
        rows = conn.execute(
            """
            SELECT fundamental_stage AS fund,
                   technical_stage   AS tech,
                   formula_id,
                   holding_days,
                   n_signals,
                   win_rate,
                   avg_ret,
                   avg_dd,
                   sharpe,
                   calmar,
                   is_recommended
              FROM mart_stage_formula_fitness
             ORDER BY win_rate DESC NULLS LAST
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        data = [
            {
                "fund": r["fund"],
                "tech": r["tech"],
                "formula_id": r["formula_id"],
                "holding_days": int(r["holding_days"]),
                "n_signals": int(r["n_signals"]),
                "win_rate": float(r["win_rate"] or 0),
                "avg_ret": float(r["avg_ret"] or 0),
                "avg_dd": float(r["avg_dd"] or 0),
                "sharpe": float(r["sharpe"] or 0),
                "calmar": float(r["calmar"] or 0),
                "is_recommended": bool(r["is_recommended"]),
            }
            for r in rows
        ]
        return {"ok": True, "data": data, "total": len(data)}
    finally:
        conn.close()


@router.get("/selections")
async def get_selection_board(limit: int = Query(50, ge=1, le=500)):
    """v3 选股台优选追踪 tab 的数据 (聚合 fact_technical_trigger 当日命中)。

    简化版 v1: 按今日所有公式命中数排序股票。等 Phase ε 上线 mart_stock_selection_summary
    再切到真正的"优选追踪"语义。
    """
    conn = get_conn()
    try:
        if not _table_exists(conn, "fact_technical_trigger"):
            return {"ok": True, "data": [], "total": 0}
        latest_date_row = conn.execute("SELECT MAX(date) FROM fact_technical_trigger").fetchone()
        latest_date = latest_date_row[0] if latest_date_row else None
        if not latest_date:
            return {"ok": True, "data": [], "total": 0}

        rows = conn.execute(
            """
            WITH today AS (
                SELECT stock_code, COUNT(*) AS hit_count,
                       MAX(formula_id) AS last_formula
                  FROM fact_technical_trigger
                 WHERE date = ?
                 GROUP BY stock_code
            ),
            history AS (
                SELECT stock_code,
                       COUNT(*) AS n_total,
                       SUM(CASE WHEN date >= ? THEN 1 ELSE 0 END) AS n30
                  FROM fact_technical_trigger
                 GROUP BY stock_code
            )
            SELECT t.stock_code,
                   COALESCE(d.stock_name, t.stock_code) AS stock_name,
                   t.hit_count,
                   t.last_formula,
                   h.n_total,
                   h.n30
              FROM today t
              LEFT JOIN dim_active_a_stock d ON d.stock_code = t.stock_code
              LEFT JOIN history h            ON h.stock_code = t.stock_code
             ORDER BY t.hit_count DESC, h.n_total DESC
             LIMIT ?
            """,
            (latest_date, _date_30_days_ago(latest_date), limit),
        ).fetchall()

        data = [
            {
                "code": r["stock_code"],
                "name": r["stock_name"],
                "hit_today": int(r["hit_count"] or 0),
                "last_formula": r["last_formula"],
                "n_total": int(r["n_total"] or 0),
                "n30": int(r["n30"] or 0),
                # 占位 - 等 Phase ε
                "win": None,
                "avg_ret": None,
                "last_outcome": "active",
                "last_date": latest_date,
            }
            for r in rows
        ]
        return {"ok": True, "data": data, "total": len(data), "latest_date": latest_date}
    finally:
        conn.close()


def _date_30_days_ago(date_str: str) -> str:
    """从 YYYY-MM-DD 减 30 天。"""
    from datetime import datetime as _dt, timedelta
    try:
        d = _dt.strptime(date_str, "%Y-%m-%d")
        return (d - timedelta(days=30)).strftime("%Y-%m-%d")
    except Exception:
        return date_str
