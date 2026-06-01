from __future__ import annotations

import os
import sys
import types

import pandas as pd

from services.data_sources import resolve
from services.data_sources.sources import akshare as akshare_source_module
from services.data_sources.sources.akshare import AkshareSource


def _make_fake_akshare_module() -> types.ModuleType:
    fake = types.ModuleType("akshare")

    def stock_individual_fund_flow(stock: str = "600094", market: str = "sh") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "日期": "2026-05-29",
                    "主力净流入-净额": 12.5,
                    "主力净流入-净占比": 3.2,
                    "超大单净流入-净额": 4.5,
                    "大单净流入-净额": 8.0,
                    "中单净流入-净额": -2.0,
                    "小单净流入-净额": -10.5,
                    "收盘价": 123.4,
                    "涨跌幅": 1.2,
                }
            ]
        )

    def stock_individual_fund_flow_rank(indicator: str = "5日") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "序号": 1,
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "最新价": 1234.5,
                    "5日主力净流入-净额": 99.0,
                }
            ]
        )

    fake.stock_individual_fund_flow = stock_individual_fund_flow
    fake.stock_individual_fund_flow_rank = stock_individual_fund_flow_rank
    return fake


def test_akshare_source_registers_individual_fund_flow_capabilities() -> None:
    caps = {cap.name for cap in AkshareSource().capabilities}

    assert "individual_fund_flow" in caps
    assert "individual_fund_flow_rank" in caps


def test_akshare_source_dispatches_individual_fund_flow(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "akshare", _make_fake_akshare_module())

    data = AkshareSource().fetch("individual_fund_flow", stock="600519", market="sh")

    assert data == [
        {
            "日期": "2026-05-29",
            "主力净流入-净额": 12.5,
            "主力净流入-净占比": 3.2,
            "超大单净流入-净额": 4.5,
            "大单净流入-净额": 8.0,
            "中单净流入-净额": -2.0,
            "小单净流入-净额": -10.5,
            "收盘价": 123.4,
            "涨跌幅": 1.2,
        }
    ]


def test_akshare_source_disables_proxy_env_before_fetch(monkeypatch) -> None:
    monkeypatch.setenv("http_proxy", "http://proxy.example:8080")
    monkeypatch.setenv("https_proxy", "http://proxy.example:8080")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("all_proxy", "http://proxy.example:8080")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example:8080")

    fake = types.ModuleType("akshare")

    def stock_individual_fund_flow(stock: str = "600094", market: str = "sh") -> pd.DataFrame:
        assert os.environ.get("http_proxy") is None
        assert os.environ.get("https_proxy") is None
        assert os.environ.get("HTTP_PROXY") is None
        assert os.environ.get("HTTPS_PROXY") is None
        assert os.environ.get("all_proxy") is None
        assert os.environ.get("ALL_PROXY") is None
        assert os.environ.get("NO_PROXY") == "*"
        return _make_fake_akshare_module().stock_individual_fund_flow(stock=stock, market=market)

    fake.stock_individual_fund_flow = stock_individual_fund_flow
    monkeypatch.setitem(sys.modules, "akshare", fake)

    data = AkshareSource().fetch("individual_fund_flow", stock="600519", market="sh")

    assert data[0]["日期"] == "2026-05-29"


def test_akshare_source_retries_transient_errors(monkeypatch) -> None:
    calls = {"count": 0}
    fake = types.ModuleType("akshare")

    def stock_individual_fund_flow(stock: str = "600094", market: str = "sh") -> pd.DataFrame:
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionError("temporary network hiccup")
        return _make_fake_akshare_module().stock_individual_fund_flow(stock=stock, market=market)

    fake.stock_individual_fund_flow = stock_individual_fund_flow
    monkeypatch.setitem(sys.modules, "akshare", fake)
    monkeypatch.setattr(akshare_source_module.time, "sleep", lambda *_args, **_kwargs: None)

    data = AkshareSource().fetch("individual_fund_flow", stock="600519", market="sh")

    assert calls["count"] == 2
    assert data[0]["日期"] == "2026-05-29"


def test_resolve_routes_individual_fund_flow_to_akshare(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "akshare", _make_fake_akshare_module())

    data, source = resolve("individual_fund_flow", stock="600519", market="sh")

    assert source == "akshare"
    assert isinstance(data, list)
    assert data[0]["日期"] == "2026-05-29"
