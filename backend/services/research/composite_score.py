"""Phase ε+ §6.5.1 — Risk-adjusted composite score。

公式 (开发手册 §6.5.1):
  model_composite = (WF_RankIC_avg × 100)
                    × (1 / (1 + |paper_max_drawdown|))
                    × min(1.0, n_paper_trades / 60)
                    × edge_guard                       # 0 if (DD>25% OR trades<30 OR Sharpe<0)
"""
from __future__ import annotations

import logging
import math
import time


log = logging.getLogger("research.composite_score")


def compute_composite_score(
    *,
    wf_rank_ic_avg: float | None,
    paper_sharpe: float | None,
    paper_max_drawdown: float | None,
    n_paper_trades: int | None,
) -> dict:
    """计算单一 model 的 composite。

    Returns:
        {composite_score, risk_adjust_factor, trade_penalty, edge_guard, ...}
    """
    ic = float(wf_rank_ic_avg or 0.0)
    sharpe = float(paper_sharpe or 0.0)
    dd = abs(float(paper_max_drawdown or 0.0))
    n_trades = int(n_paper_trades or 0)

    # 三道防线
    edge_guard = 0.0
    if dd > 0.25:
        edge_guard = 0.0
    elif n_trades < 30:
        edge_guard = 0.0
    elif sharpe < 0:
        edge_guard = 0.0
    else:
        edge_guard = 1.0

    # 主成分
    base = ic * 100.0
    risk_adjust = 1.0 / (1.0 + dd)
    trade_penalty = min(1.0, n_trades / 60.0) if n_trades > 0 else 0.0

    composite = base * risk_adjust * trade_penalty * edge_guard

    return {
        "wf_rank_ic_avg": ic,
        "paper_sharpe": sharpe,
        "paper_max_drawdown": -dd,  # 保留符号 (负)
        "n_paper_trades": n_trades,
        "risk_adjust_factor": risk_adjust,
        "trade_penalty": trade_penalty,
        "edge_guard": edge_guard,
        "composite_score": composite,
    }


def build_composite_for_all_models(conn, eval_date: str) -> int:
    """对所有 active model 算 composite + 排名 + 写库。

    数据源:
      - wf_rank_ic_avg: mart_signal_ic 60d rolling per formula (用 formula_id 当 model_id)
      - paper_sharpe / paper_max_drawdown / n_paper_trades: mart_paper_nav (各 model_id 序列)
    """
    t0 = time.time()
    # 拉所有 paper model
    paper_models = conn.execute(
        "SELECT DISTINCT model_id FROM mart_paper_nav"
    ).fetchall()
    if not paper_models:
        log.warning("无 paper model")
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
    ic_row = conn.execute(
        "SELECT AVG(ic_10d) FROM mart_signal_ic WHERE snapshot_date >= (SELECT MAX(snapshot_date) - 60 FROM mart_signal_ic)"
    ).fetchone()
    wf_ic = float(ic_row[0]) if ic_row and ic_row[0] is not None else 0.0

    # 计算每个 model 的 sharpe / max_dd / n_trades
    out_rows = []
    for model_id in model_ids:
        # NAV 序列
        navs = nav_by_model.get(model_id, [])
        rets = [float(r[0]) for r in navs if r[0] is not None]
        max_dd = min((float(r[1]) for r in navs if r[1] is not None), default=0.0)
        if len(rets) > 1:
            n = len(rets); mean = sum(rets)/n; var = sum((r-mean)**2 for r in rets)/(n-1); sd = math.sqrt(var) if var>0 else 0
            sharpe = mean*252/(sd*math.sqrt(252)) if sd>0 else 0.0
        else:
            sharpe = 0.0
        # n_trades = sell 行数
        n_trades = trades_by_model.get(model_id, 0)

        metrics = compute_composite_score(
            wf_rank_ic_avg=wf_ic, paper_sharpe=sharpe,
            paper_max_drawdown=max_dd, n_paper_trades=int(n_trades),
        )
        out_rows.append((model_id, metrics))

    # 排名
    out_rows.sort(key=lambda x: x[1]["composite_score"], reverse=True)
    final = []
    for rank, (model_id, m) in enumerate(out_rows, 1):
        final.append((
            model_id, eval_date,
            m["wf_rank_ic_avg"], m["paper_sharpe"], m["paper_max_drawdown"],
            m["n_paper_trades"], m["risk_adjust_factor"], m["trade_penalty"],
            m["edge_guard"], m["composite_score"], rank,
        ))

    # 写库
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "DELETE FROM mart_model_composite_score WHERE eval_date = ?", [eval_date]
        )
        conn.executemany(
            """INSERT INTO mart_model_composite_score
               (model_id, eval_date, wf_rank_ic_avg, paper_sharpe, paper_max_drawdown,
                n_paper_trades, risk_adjust_factor, trade_penalty, edge_guard,
                composite_score, composite_rank)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            final,
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    log.info(f"完成: {len(final)} model 评分 (耗时 {time.time()-t0:.2f}s)")
    return len(final)
