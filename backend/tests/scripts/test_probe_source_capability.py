from __future__ import annotations

import logging

import pandas as pd

from conftest import duck_mem
from scripts import probe_source_capability as probe


class _ConnProxy:
    def __init__(self, conn):
        self._conn = conn

    def close(self) -> None:
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_probe_source_capability_summarizes_records(monkeypatch) -> None:
    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        assert capability == "individual_fund_flow"
        assert prefer_source == "akshare"
        assert kwargs == {"stock": "600519", "market": "sh"}
        return (
            [
                {"日期": "2026-05-28", "主力净流入-净额": 1.0},
                {"日期": "2026-05-29", "主力净流入-净额": 2.0},
            ],
            "akshare",
        )

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_source_capability(
        "individual_fund_flow",
        {"stock": "600519", "market": "sh"},
        prefer_source="akshare",
    )

    assert report["status"] == "ok"
    assert report["source_used"] == "akshare"
    assert report["row_count"] == 2
    assert report["columns"] == ["日期", "主力净流入-净额"]
    assert report["date_range"] == {"field": "日期", "min": "2026-05-28", "max": "2026-05-29"}


def test_probe_source_capability_summarizes_dataframe(monkeypatch) -> None:
    df = pd.DataFrame(
        [
            {"trade_date": "2026-05-29", "main_net_amount": 12.0},
        ]
    )

    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        return (df, "akshare")

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_source_capability(
        "individual_fund_flow_rank",
        {"indicator": "5日"},
        prefer_source="akshare",
    )

    assert report["status"] == "ok"
    assert report["type"] == "DataFrame"
    assert report["row_count"] == 1
    assert report["columns"] == ["trade_date", "main_net_amount"]
    assert report["date_range"] == {"field": "trade_date", "min": "2026-05-29", "max": "2026-05-29"}


def test_probe_source_capability_summarizes_rank_snapshot(monkeypatch) -> None:
    df = pd.DataFrame(
        [
            {"序号": 1, "代码": "600519", "最新价": 1234.5},
        ]
    )

    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        assert capability == "individual_fund_flow_rank_snapshot"
        assert prefer_source == "akshare"
        assert kwargs == {"symbol": "即时"}
        return (df, "akshare")

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_source_capability(
        "individual_fund_flow_rank_snapshot",
        {"symbol": "即时"},
        prefer_source="akshare",
    )

    assert report["status"] == "ok"
    assert report["type"] == "DataFrame"
    assert report["row_count"] == 1
    assert report["columns"] == ["序号", "代码", "最新价"]


def test_probe_source_capability_marks_blocked_on_error(monkeypatch) -> None:
    def fake_resolve(*_args, **_kwargs):
        raise RuntimeError("proxy blocked")

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_source_capability(
        "individual_fund_flow",
        {"stock": "600519", "market": "sh"},
        prefer_source="akshare",
    )

    assert report["status"] == "blocked"
    assert report["error_type"] == "RuntimeError"
    assert report["error"] == "proxy blocked"


def test_probe_source_capability_quiets_registry_warnings(monkeypatch, caplog) -> None:
    registry_logger = logging.getLogger("data_sources.registry")
    original_level = registry_logger.level

    def fake_resolve(*_args, **_kwargs):
        logging.getLogger("data_sources.registry").warning("[registry] noisy fallback")
        raise RuntimeError("proxy blocked")

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    with caplog.at_level(logging.WARNING, logger="data_sources.registry"):
        report = probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
        )

    assert report["status"] == "blocked"
    assert report["error"] == "proxy blocked"
    assert not any(record.name == "data_sources.registry" for record in caplog.records)
    assert registry_logger.level == original_level


def test_probe_source_capability_persists_blocked_status(monkeypatch) -> None:
    conn = duck_mem()
    try:
        def fake_resolve(*_args, **_kwargs):
            raise RuntimeError("proxy blocked")

        monkeypatch.setattr(probe, "resolve", fake_resolve)
        monkeypatch.setattr(probe, "get_conn", lambda: _ConnProxy(conn))

        report = probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
            persist_status=True,
            data_domain="order_flow_fund_flow",
            source_name="akshare",
            source_tier=3,
            stock_code="600519",
        )

        assert report["status"] == "blocked"
        assert report["persisted"]["status"] == "open"
        row = conn.execute(
            """
            SELECT data_domain, source_name, source_tier, stock_code,
                   error_type, last_error, status
              FROM mart_data_source_failure_queue
            """
        ).fetchone()
        assert tuple(row) == (
            "order_flow_fund_flow",
            "akshare",
            3,
            "600519",
            "RuntimeError",
            "proxy blocked",
            "open",
        )
    finally:
        conn.close()


def test_probe_source_capability_resolves_existing_failure_on_success(monkeypatch) -> None:
    conn = duck_mem()
    try:
        def fake_blocked(*_args, **_kwargs):
            raise RuntimeError("proxy blocked")

        monkeypatch.setattr(probe, "get_conn", lambda: _ConnProxy(conn))
        monkeypatch.setattr(probe, "resolve", fake_blocked)
        probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
            persist_status=True,
            data_domain="order_flow_fund_flow",
            source_name="akshare",
            source_tier=3,
            stock_code="600519",
        )

        def fake_ok(capability: str, *, prefer_source=None, **kwargs):
            assert capability == "individual_fund_flow"
            assert prefer_source == "akshare"
            return ([{"日期": "2026-05-29"}], "akshare")

        monkeypatch.setattr(probe, "resolve", fake_ok)
        report = probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
            persist_status=True,
            data_domain="order_flow_fund_flow",
            source_name="akshare",
            source_tier=3,
            stock_code="600519",
        )

        assert report["status"] == "ok"
        assert report["persisted"]["status"] == "resolved"
        row = conn.execute(
            """
            SELECT status, resolved_at
              FROM mart_data_source_failure_queue
             WHERE data_domain = 'order_flow_fund_flow'
               AND source_name = 'akshare'
               AND stock_code = '600519'
            """
        ).fetchone()
        assert row["status"] == "resolved"
        assert row["resolved_at"] is not None
    finally:
        conn.close()
