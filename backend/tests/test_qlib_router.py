import sys
import asyncio
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from routers import qlib as qlib_router
import services.qlib_full_engine as qlib_full_engine


client = TestClient(app)


class _DummyConn:
    def close(self):
        return None


def test_qlib_train_refreshes_sector_forecast_after_stock_forecast(monkeypatch):
    captured = []
    calls = []

    class _DummyConn:
        def close(self):
            return None

    class _DummyMarketConn:
        def close(self):
            return None

    async def _fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(qlib_full_engine, "is_available", lambda: (True, None))
    monkeypatch.setattr(qlib_router, "get_conn", lambda *args, **kwargs: _DummyConn())
    monkeypatch.setattr(qlib_router.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(
        qlib_router.asyncio,
        "create_task",
        lambda coro: captured.append(coro),
    )

    import services.market_db as market_db
    import services.scoring as scoring
    import services.sector_forecast_engine as sector_forecast_engine
    import services.stock_forecast_engine as stock_forecast_engine
    import services.stock_turtle_engine as stock_turtle_engine

    monkeypatch.setattr(qlib_full_engine, "train_full_model", lambda conn, params=None: {"model_id": "model_1"})
    monkeypatch.setattr(market_db, "get_market_conn", lambda *args, **kwargs: _DummyMarketConn())
    monkeypatch.setattr(
        stock_forecast_engine,
        "build_stock_forecast_features",
        lambda conn: calls.append("stock_forecast") or 12,
    )
    monkeypatch.setattr(
        sector_forecast_engine,
        "build_sector_forecast_features",
        lambda conn: calls.append("sector_forecast") or 5,
    )
    monkeypatch.setattr(
        stock_turtle_engine,
        "build_stock_turtle_features",
        lambda conn, mkt_conn: calls.append("turtle") or 7,
    )
    monkeypatch.setattr(
        scoring,
        "calculate_stock_scores",
        lambda conn: calls.append("stock_scores") or 9,
    )

    response = asyncio.run(qlib_router.qlib_train())

    assert response["ok"] is True
    assert "行业前瞻" in response["message"]
    assert len(captured) == 1

    asyncio.run(captured[0])

    assert calls == [
        "stock_forecast",
        "sector_forecast",
        "turtle",
        "stock_scores",
    ]