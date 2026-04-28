import asyncio
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from routers import screening as screening_router
from services.screening_read import (
    get_screening_detail,
    get_screening_summary,
    list_screening_results,
    load_dual_confirm_snapshot_map,
    load_screening_snapshot_map,
)


class _SharedConnProxy:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        return None


def _make_conn():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE mart_stock_screening (
            stock_code TEXT PRIMARY KEY,
            screen_date TEXT,
            f1_hit INTEGER,
            f3_hit INTEGER,
            f5_hit INTEGER,
            hit_count INTEGER,
            float_market_cap REAL
        );

        CREATE TABLE mart_dual_confirm (
            stock_code TEXT NOT NULL,
            report_date TEXT,
            dual_confirm INTEGER
        );
        """
    )
    conn.executemany(
        "INSERT INTO mart_stock_screening VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("600001", "2026-04-15", 1, 0, 1, 2, 100.0),
            ("600002", "2026-04-15", 0, 1, 0, 1, 90.0),
            ("600003", "2026-04-15", 0, 0, 0, 0, 80.0),
        ],
    )
    conn.executemany(
        "INSERT INTO mart_dual_confirm VALUES (?, ?, ?)",
        [
            ("600001", "2026-04-12", 1),
            ("600001", "2026-04-15", 1),
            ("600002", "2026-04-14", 0),
        ],
    )
    conn.commit()
    return conn


def test_screening_read_service_builds_shared_snapshot_maps():
    conn = _make_conn()
    try:
        screening_map = load_screening_snapshot_map(conn)
        dual_confirm_map = load_dual_confirm_snapshot_map(conn)

        assert screening_map["600001"]["f1_hit"] is True
        assert screening_map["600001"]["hit_count"] == 2
        assert screening_map["600003"]["hit_count"] == 0
        assert dual_confirm_map["600001"]["dual_confirm_count"] == 2
        assert dual_confirm_map["600001"]["dual_confirm_latest_report_date"] == "2026-04-15"
        assert "600002" not in dual_confirm_map
    finally:
        conn.close()


def test_screening_read_service_keeps_route_payload_shapes(monkeypatch):
    conn = _make_conn()
    try:
        proxy = _SharedConnProxy(conn)
        monkeypatch.setattr(screening_router, "get_conn", lambda *args, **kwargs: proxy)

        rows, total = list_screening_results(conn, formula="f1", hits_only=True, limit=50, offset=0)
        detail = get_screening_detail(conn, "600001")
        summary = get_screening_summary(conn)
        route_results = asyncio.run(screening_router.get_results(formula="f1", hits_only=True, limit=50, offset=0))
        route_dual = asyncio.run(screening_router.get_dual_confirm(hits_only=True))
        route_summary = asyncio.run(screening_router.get_summary())

        assert total == 1
        assert len(rows) == 1
        assert rows[0]["stock_code"] == "600001"
        assert detail["stock_code"] == "600001"
        assert summary["total_stocks"] == 3
        assert summary["f1_hits"] == 1
        assert summary["f3_hits"] == 1
        assert summary["f5_hits"] == 1
        assert summary["any_hit"] == 2

        assert route_results["ok"] is True
        assert route_results["total"] == 1
        assert route_results["data"][0]["stock_code"] == "600001"
        assert route_dual["ok"] is True
        assert route_dual["count"] == 2
        assert route_summary == {"ok": True, **summary}
    finally:
        conn.close()