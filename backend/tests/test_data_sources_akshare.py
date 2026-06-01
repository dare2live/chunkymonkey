from __future__ import annotations

import sys
import types

import pandas as pd

from services.data_sources import resolve
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


def test_resolve_routes_individual_fund_flow_to_akshare(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "akshare", _make_fake_akshare_module())

    data, source = resolve("individual_fund_flow", stock="600519", market="sh")

    assert source == "akshare"
    assert isinstance(data, list)
    assert data[0]["日期"] == "2026-05-29"
