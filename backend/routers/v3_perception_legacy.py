"""v3 Perception legacy display router (read-only).

Per goal.md Phase 1.7 + MASTER_SYNTHESIS Track A.
Sibling Perception repo marts already merged into chunkymonkey/data/smartmoney.duckdb.
This router provides read-only display endpoints for legacy Perception UI tab.

No write operations. Track A is FROZEN — only data refresh happens via Perception sibling repo's own pipelines.

Endpoints:
- GET /api/v3/perception/themes — latest theme states (P3 ThemeBoundary)
- GET /api/v3/perception/leaders — leader-follower stocks (P5)
- GET /api/v3/perception/style_rotation — sector style daily (P6)
- GET /api/v3/perception/under_reaction — under-reaction events (P4)
- GET /api/v3/perception/stock_context/{code} — per-stock context tags (P7)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.duck_adapter import connect

router = APIRouter(prefix="/api/v3/perception", tags=["v3-perception-legacy"])

DB_PATH = "data/smartmoney.duckdb"


@router.get("/themes")
def get_themes(limit: int = 30) -> dict:
    """Latest P3 theme states."""
    with connect(DB_PATH, read_only=True) as conn:
        try:
            rows = conn.execute(f"""
                SELECT trade_date, theme_name, theme_score, member_count, lifecycle_stage
                  FROM mart_market_perception_theme_daily
                 ORDER BY trade_date DESC, theme_score DESC
                 LIMIT {limit}
            """).fetchall()
            return {
                "source": "Perception P3 ThemeBoundary (Track A legacy)",
                "themes": [
                    {"trade_date": str(r[0]), "name": r[1], "score": r[2],
                     "members": r[3], "lifecycle": r[4]}
                    for r in rows
                ],
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"theme query failed: {e}")


@router.get("/leaders")
def get_leader_follower(limit: int = 30) -> dict:
    """P5 LeaderFollower latest diffusion records."""
    with connect(DB_PATH, read_only=True) as conn:
        try:
            rows = conn.execute(f"""
                SELECT trade_date, theme_name, leader_code, follower_code,
                       leader_ret_5d, follower_ret_5d, diffusion_score, lifecycle_stage
                  FROM mart_market_perception_leader_follower_daily
                 ORDER BY trade_date DESC, diffusion_score DESC
                 LIMIT {limit}
            """).fetchall()
            return {
                "source": "Perception P5 LeaderFollower (Track A legacy)",
                "diffusion": [
                    {"trade_date": str(r[0]), "theme": r[1], "leader": r[2], "follower": r[3],
                     "leader_ret_5d": r[4], "follower_ret_5d": r[5],
                     "diffusion_score": r[6], "lifecycle": r[7]}
                    for r in rows
                ],
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"leader query failed: {e}")


@router.get("/style_rotation")
def get_style_rotation(limit: int = 30) -> dict:
    """P6 StyleRotation latest sector rotation."""
    with connect(DB_PATH, read_only=True) as conn:
        try:
            rows = conn.execute(f"""
                SELECT trade_date, style_name, style_score, crowding_score, lifecycle_stage
                  FROM mart_market_perception_style_daily
                 ORDER BY trade_date DESC, style_score DESC
                 LIMIT {limit}
            """).fetchall()
            return {
                "source": "Perception P6 StyleRotation (Track A legacy)",
                "styles": [
                    {"trade_date": str(r[0]), "style": r[1], "score": r[2],
                     "crowding": r[3], "lifecycle": r[4]}
                    for r in rows
                ],
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"style query failed: {e}")


@router.get("/under_reaction")
def get_under_reaction(limit: int = 30) -> dict:
    """P4 UnderReaction latest events."""
    with connect(DB_PATH, read_only=True) as conn:
        try:
            rows = conn.execute(f"""
                SELECT trade_date, stock_code, under_reaction_score, lifecycle_stage
                  FROM mart_market_perception_under_reaction_daily
                 ORDER BY trade_date DESC, under_reaction_score DESC
                 LIMIT {limit}
            """).fetchall()
            return {
                "source": "Perception P4 UnderReaction (Track A legacy)",
                "events": [
                    {"trade_date": str(r[0]), "stock": r[1], "score": r[2], "lifecycle": r[3]}
                    for r in rows
                ],
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"under_reaction query failed: {e}")


@router.get("/stock_context/{code}")
def get_stock_context(code: str, limit: int = 14) -> dict:
    """P7 StockContext per-stock latest context (last N trading days)."""
    with connect(DB_PATH, read_only=True) as conn:
        try:
            rows = conn.execute("""
                SELECT trade_date, stock_code, context_score, completeness_score, lifecycle_stage
                  FROM mart_market_perception_stock_context_daily
                 WHERE stock_code = ?
                 ORDER BY trade_date DESC
                 LIMIT ?
            """, [code, limit]).fetchall()
            return {
                "source": "Perception P7 StockContext (Track A legacy)",
                "stock_code": code,
                "context_history": [
                    {"trade_date": str(r[0]), "stock": r[1], "context_score": r[2],
                     "completeness": r[3], "lifecycle": r[4]}
                    for r in rows
                ],
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"context query failed: {e}")
