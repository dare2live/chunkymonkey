from __future__ import annotations

import pandas as pd

from scripts import probe_source_capability as probe


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
