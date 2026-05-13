"""Phase ε D1 — logger.py 单测。"""
from __future__ import annotations

import pytest


@pytest.fixture
def conn():
    from services.duck_adapter import connect as duck_connect
    from services.selection.ddl import ensure_selection_tables
    c = duck_connect(":memory:")
    ensure_selection_tables(c)
    yield c
    c.close()


class TestLogTopkSelection:
    def test_basic_write(self, conn):
        from services.selection.logger import log_topk_selection
        n = log_topk_selection(
            conn, "2026-05-12",
            [
                {"stock_code": "600519", "rank_in_date": 1, "pred_score": 0.9, "model_id": "champion"},
                {"stock_code": "000001", "rank_in_date": 2, "pred_score": 0.7},
            ],
        )
        assert n == 2
        rows = conn.execute(
            "SELECT stock_code, rank_in_date, pred_score, select_source FROM fact_stock_selection_log"
        ).fetchall()
        assert len(rows) == 2
        assert {r[0] for r in rows} == {"600519", "000001"}
        assert all(r[3] == "daily_topk" for r in rows)

    def test_idempotent_replaces(self, conn):
        """同日跑两次, 不累加 (DELETE+INSERT 替换)。"""
        from services.selection.logger import log_topk_selection
        log_topk_selection(conn, "2026-05-12", [{"stock_code": "600519", "rank_in_date": 1}])
        log_topk_selection(conn, "2026-05-12", [{"stock_code": "000001", "rank_in_date": 1}])
        n = conn.execute("SELECT COUNT(*) FROM fact_stock_selection_log").fetchone()[0]
        # 第二次替换第一次, 总数仍 1 (同日 daily_topk 全删)
        assert n == 1
        # 留下来的是 000001
        sc = conn.execute("SELECT stock_code FROM fact_stock_selection_log").fetchone()[0]
        assert sc == "000001"

    def test_skip_rows_without_stock_code(self, conn):
        from services.selection.logger import log_topk_selection
        n = log_topk_selection(
            conn, "2026-05-12",
            [{"stock_code": "600519"}, {"rank_in_date": 99}, {"stock_code": ""}],
        )
        assert n == 1


class TestLogFormulaSelection:
    def test_basic_write(self, conn):
        from services.selection.logger import log_formula_selection
        n = log_formula_selection(
            conn, "2026-05-12",
            [
                {"stock_code": "600519", "formula_id": "macd_golden_cross", "strength": 0.8},
                {"stock_code": "000001", "formula_id": "macd_golden_cross", "strength": 0.6},
                {"stock_code": "600519", "formula_id": "turtle_breakout_20", "strength": 0.5},
            ],
        )
        assert n == 3
        rows = conn.execute(
            "SELECT stock_code, source_id, strength FROM fact_stock_selection_log "
            "WHERE select_source='formula' ORDER BY stock_code, source_id"
        ).fetchall()
        assert len(rows) == 3

    def test_multi_formula_same_stock_same_day(self, conn):
        """同日同股不同公式 → 多行 (PK 含 source_id)。"""
        from services.selection.logger import log_formula_selection
        log_formula_selection(
            conn, "2026-05-12",
            [
                {"stock_code": "600519", "formula_id": "macd_golden_cross"},
                {"stock_code": "600519", "formula_id": "turtle_breakout_20"},
                {"stock_code": "600519", "formula_id": "turtle_breakout_55"},
            ],
        )
        n = conn.execute(
            "SELECT COUNT(*) FROM fact_stock_selection_log WHERE stock_code='600519'"
        ).fetchone()[0]
        assert n == 3

    def test_only_delete_same_formulas_not_others(self, conn):
        """重新跑 macd 不应删 turtle 数据。"""
        from services.selection.logger import log_formula_selection
        # 1. 写两个公式
        log_formula_selection(
            conn, "2026-05-12",
            [
                {"stock_code": "600519", "formula_id": "macd_golden_cross"},
                {"stock_code": "600519", "formula_id": "turtle_breakout_20"},
            ],
        )
        # 2. 重写只 macd
        log_formula_selection(
            conn, "2026-05-12",
            [{"stock_code": "000001", "formula_id": "macd_golden_cross"}],
        )
        # turtle 数据应保留
        rows = conn.execute(
            "SELECT source_id, stock_code FROM fact_stock_selection_log ORDER BY 1, 2"
        ).fetchall()
        assert ("turtle_breakout_20", "600519") in [(r[0], r[1]) for r in rows]
        # macd: 只剩 000001
        macd_rows = [r for r in rows if r[0] == "macd_golden_cross"]
        assert len(macd_rows) == 1
        assert macd_rows[0][1] == "000001"
