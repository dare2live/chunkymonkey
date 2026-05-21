"""Phase ε+ §6.5.2 — Edge-Case 三道防线。

| 标记 | 触发条件 | 自动动作 |
| ⚠ OVERFIT | n_paper_trades < 30 OR holdout_IC / WF_IC > 2.5 | 冻结 model 不允许 promote |
| 🔥 RISKY   | paper_max_drawdown > 25% OR single_day_loss > 5% | 强制减仓 50% + 标记调参 |
| 💤 DEAD    | 滚动 60d Rank IC 连续 4 周变化 < 0.005 | 触发探索: 换 feature / 换参 / 换 horizon |
"""
from __future__ import annotations

import logging
import time


log = logging.getLogger("research.edge_flags")


def classify_edge_flag(
    *,
    n_paper_trades: int,
    paper_max_drawdown: float,
    single_day_max_loss: float,
    rolling_ic_4w_change: float,
) -> dict:
    """三道防线优先级判定 (按严重度: RISKY > OVERFIT > DEAD > NORMAL)。"""
    dd = abs(paper_max_drawdown)
    sdl = abs(single_day_max_loss)

    if dd > 0.25:
        return {
            "flag_type": "RISKY",
            "trigger_metric": "paper_max_drawdown",
            "trigger_value": -dd,
            "trigger_threshold": -0.25,
            "auto_action": "强制减仓 50% + 标记需调参",
        }
    if sdl > 0.05:
        return {
            "flag_type": "RISKY",
            "trigger_metric": "single_day_max_loss",
            "trigger_value": -sdl,
            "trigger_threshold": -0.05,
            "auto_action": "强制减仓 50% + 标记需调参",
        }
    if n_paper_trades < 30:
        return {
            "flag_type": "OVERFIT",
            "trigger_metric": "n_paper_trades",
            "trigger_value": float(n_paper_trades),
            "trigger_threshold": 30,
            "auto_action": "冻结 model 不允许 promote",
        }
    if abs(rolling_ic_4w_change) < 0.005:
        return {
            "flag_type": "DEAD",
            "trigger_metric": "rolling_ic_4w_change",
            "trigger_value": rolling_ic_4w_change,
            "trigger_threshold": 0.005,
            "auto_action": "触发探索: 换 feature / 换参 / 换 horizon",
        }
    return {
        "flag_type": "NORMAL",
        "trigger_metric": None,
        "trigger_value": None,
        "trigger_threshold": None,
        "auto_action": None,
    }


def build_edge_flags_for_all_models(conn, eval_date: str) -> int:
    """对所有 paper model 算 edge flag + 写库。"""
    t0 = time.time()
    paper_models = conn.execute(
        "SELECT DISTINCT model_id FROM mart_paper_nav"
    ).fetchall()
    if not paper_models:
        return 0
    model_ids = [row[0] for row in paper_models]
    nav_by_model: dict[str, list[tuple[float | None, float | None]]] = {model_id: [] for model_id in model_ids}
    nav_rows = conn.execute(
        """
        SELECT model_id, daily_ret, drawdown
        FROM mart_paper_nav
        ORDER BY model_id, snapshot_date
        """
    ).fetchall()
    for model_id, daily_ret, drawdown in nav_rows:
        nav_by_model.setdefault(model_id, []).append((daily_ret, drawdown))
    trade_rows = conn.execute(
        """
        SELECT model_id, COUNT(*) AS n_trades
        FROM fact_paper_position
        WHERE side='sell'
        GROUP BY model_id
        """
    ).fetchall()
    trades_by_model = {row[0]: int(row[1] or 0) for row in trade_rows}
    # IC 4 周变化: 最近 20d IC - 20-40d IC
    ic_row = conn.execute(
        """
        SELECT
            AVG(ic_10d) FILTER (
                WHERE snapshot_date >= (SELECT MAX(snapshot_date) - 20 FROM mart_signal_ic)
            ) AS ic_recent,
            AVG(ic_10d) FILTER (
                WHERE snapshot_date >= (SELECT MAX(snapshot_date) - 40 FROM mart_signal_ic)
                  AND snapshot_date <  (SELECT MAX(snapshot_date) - 20 FROM mart_signal_ic)
            ) AS ic_prev
        FROM mart_signal_ic
        """
    ).fetchone()
    ic_change = (float(ic_row[0]) - float(ic_row[1])) if (ic_row and ic_row[0] and ic_row[1]) else 0.0

    out_rows = []
    for model_id in model_ids:
        # paper 指标
        navs = nav_by_model.get(model_id, [])
        if not navs:
            continue
        rets = [float(r[0]) for r in navs if r[0] is not None]
        max_dd = min((float(r[1]) for r in navs if r[1] is not None), default=0.0)
        sdl = min(rets, default=0.0) if rets else 0.0
        n_trades = trades_by_model.get(model_id, 0)

        flag = classify_edge_flag(
            n_paper_trades=int(n_trades),
            paper_max_drawdown=max_dd,
            single_day_max_loss=sdl,
            rolling_ic_4w_change=ic_change,
        )

        out_rows.append((
            model_id, eval_date, flag["flag_type"], flag["trigger_metric"],
            flag["trigger_value"], flag["trigger_threshold"], flag["auto_action"],
            False, None,
        ))

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM mart_model_edge_flags WHERE eval_date = ?", [eval_date])
        conn.executemany(
            """INSERT INTO mart_model_edge_flags
               (model_id, eval_date, flag_type, trigger_metric, trigger_value,
                trigger_threshold, auto_action, is_resolved, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            out_rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    log.info(f"完成: {len(out_rows)} model edge_flags (耗时 {time.time()-t0:.2f}s)")
    return len(out_rows)
