"""Stock horizon feature-effect read model for Workbench."""
from __future__ import annotations

from typing import Any


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    if row is None:
        return False
    try:
        conn.execute(f"SELECT 1 FROM {table_name} LIMIT 0").fetchone()
        return True
    except Exception:
        return False


def _columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {
        str(row["column_name"] if hasattr(row, "keys") else row[0])
        for row in conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = ?
            """,
            (table_name,),
        ).fetchall()
    }


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row[0]


def build_stock_horizon_effect_view(
    conn: Any,
    *,
    run_id: str,
    horizon_selection: list[dict[str, Any]],
    effect_limit: int = 12,
) -> dict[str, Any]:
    effect_table = "mart_stock_horizon_feature_effect"
    effect_count = 0
    top_effects: list[dict[str, Any]] = []
    feature_effects_by_horizon: list[dict[str, Any]] = []
    effect_run_id = run_id
    if not _table_exists(conn, effect_table):
        return {
            "effect_run_id": effect_run_id,
            "effect_count": effect_count,
            "top_effects": top_effects,
            "feature_effects_by_horizon": feature_effects_by_horizon,
            "horizon_selection": horizon_selection,
        }

    effect_cols = _columns(conn, effect_table)
    effect_required = {"run_id", "stock_code", "label_name", "feature_name", "corr", "abs_corr_rank", "effect_direction"}
    if not effect_required.issubset(effect_cols):
        return {
            "effect_run_id": effect_run_id,
            "effect_count": effect_count,
            "top_effects": top_effects,
            "feature_effects_by_horizon": feature_effects_by_horizon,
            "horizon_selection": horizon_selection,
        }

    local_effect_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM mart_stock_horizon_feature_effect WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        or 0
    )
    if local_effect_count == 0:
        fallback_run = _scalar(
            conn,
            """
            SELECT run_id
              FROM mart_stock_horizon_feature_effect
             GROUP BY run_id
            HAVING COUNT(*) > 0
             ORDER BY MAX(built_at) DESC NULLS LAST, run_id DESC
             LIMIT 1
            """,
        )
        if fallback_run:
            effect_run_id = str(fallback_run)
    effect_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM mart_stock_horizon_feature_effect WHERE run_id = ?",
            (effect_run_id,),
        ).fetchone()[0]
        or 0
    )
    effect_rows = conn.execute(
        """
        SELECT e.feature_name,
               e.effect_direction,
               COUNT(*) AS stock_count,
               AVG(ABS(e.corr)) AS avg_abs_corr,
               AVG(e.corr) AS avg_corr,
               MIN(e.horizon_days) AS min_horizon_days,
               MAX(e.horizon_days) AS max_horizon_days
          FROM mart_stock_horizon_feature_effect e
          JOIN mart_stock_horizon_profile p
            ON p.run_id = e.run_id
           AND p.stock_code = e.stock_code
           AND p.label_name = e.label_name
         WHERE e.run_id = ?
           AND p.is_best
           AND e.abs_corr_rank = 1
         GROUP BY e.feature_name, e.effect_direction
         ORDER BY stock_count DESC, avg_abs_corr DESC NULLS LAST, e.feature_name
         LIMIT ?
        """,
        (effect_run_id, int(effect_limit)),
    ).fetchall()
    top_effects = [
        {
            "feature_name": row["feature_name"],
            "effect_direction": row["effect_direction"],
            "stock_count": int(row["stock_count"] or 0),
            "avg_abs_corr": row["avg_abs_corr"],
            "avg_corr": row["avg_corr"],
            "min_horizon_days": row["min_horizon_days"],
            "max_horizon_days": row["max_horizon_days"],
        }
        for row in effect_rows
    ]
    detail_rows = conn.execute(
        """
        SELECT label_name,
               horizon_days,
               feature_name,
               COUNT(*) AS stock_count,
               AVG(ABS(corr)) AS avg_abs_corr,
               AVG(corr) AS avg_corr,
               AVG(CASE WHEN corr > 0 THEN 1.0 WHEN corr < 0 THEN 0.0 ELSE NULL END) AS positive_share,
               AVG(obs_count) AS avg_obs_count
         FROM mart_stock_horizon_feature_effect
         WHERE run_id = ?
         GROUP BY label_name, horizon_days, feature_name
         ORDER BY horizon_days, avg_abs_corr DESC NULLS LAST, feature_name
         LIMIT ?
        """,
        (effect_run_id, 240),
    ).fetchall()
    for row in detail_rows:
        positive_share = row["positive_share"]
        if positive_share is None:
            direction = "flat"
        elif positive_share >= 0.55:
            direction = "positive"
        elif positive_share <= 0.45:
            direction = "negative"
        else:
            direction = "mixed"
        feature_effects_by_horizon.append(
            {
                "label_name": row["label_name"],
                "horizon_days": row["horizon_days"],
                "feature_name": row["feature_name"],
                "stock_count": int(row["stock_count"] or 0),
                "avg_abs_corr": row["avg_abs_corr"],
                "avg_corr": row["avg_corr"],
                "positive_share": positive_share,
                "dominant_direction": direction,
                "avg_obs_count": row["avg_obs_count"],
            }
        )
    if horizon_selection:
        selected_by_stock = {
            str(row.get("stock_code")): row
            for row in horizon_selection
            if row.get("stock_code")
        }
        effect_stock_rows = conn.execute(
            """
            SELECT stock_code,
                   label_name,
                   horizon_days,
                   feature_name,
                   obs_count,
                   corr,
                   abs_corr_rank,
                   effect_direction
              FROM mart_stock_horizon_feature_effect
             WHERE run_id = ?
               AND stock_code IN ({})
               AND abs_corr_rank <= 3
             ORDER BY stock_code, abs_corr_rank, feature_name
            """.format(", ".join(["?"] * len(selected_by_stock))),
            (effect_run_id, *selected_by_stock.keys()),
        ).fetchall() if selected_by_stock else []
        effects_by_stock: dict[str, list[dict[str, Any]]] = {}
        for row in effect_stock_rows:
            selection = selected_by_stock.get(str(row["stock_code"]))
            if not selection:
                continue
            if str(row["label_name"]) != str(selection.get("selected_label")):
                continue
            effects_by_stock.setdefault(str(row["stock_code"]), []).append(
                {
                    "label_name": row["label_name"],
                    "horizon_days": row["horizon_days"],
                    "feature_name": row["feature_name"],
                    "obs_count": row["obs_count"],
                    "corr": row["corr"],
                    "abs_corr_rank": row["abs_corr_rank"],
                    "effect_direction": row["effect_direction"],
                }
            )
        for row in horizon_selection:
            row["top_feature_effects"] = effects_by_stock.get(str(row.get("stock_code")), [])

    return {
        "effect_run_id": effect_run_id,
        "effect_count": effect_count,
        "top_effects": top_effects,
        "feature_effects_by_horizon": feature_effects_by_horizon,
        "horizon_selection": horizon_selection,
    }
