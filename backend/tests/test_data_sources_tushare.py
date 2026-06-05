from __future__ import annotations

import sys
import types

import pandas as pd

from services.data_sources import resolve
from services.data_sources.sources.tushare import TuShareSource


def _make_fake_tushare_module(calls: dict[str, object] | None = None) -> types.ModuleType:
    fake = types.ModuleType("tushare")
    calls = calls if calls is not None else {}

    class FakePro:
        def moneyflow(self, **kwargs):
            calls["moneyflow_kwargs"] = kwargs
            return pd.DataFrame(
                [
                    {
                        "trade_date": "20260605",
                        "ts_code": "600519.SH",
                        "net_mf_amount": 30.0,
                        "buy_elg_amount": 100.0,
                        "sell_elg_amount": 60.0,
                        "buy_lg_amount": 40.0,
                        "sell_lg_amount": 50.0,
                        "buy_md_amount": 20.0,
                        "sell_md_amount": 15.0,
                        "buy_sm_amount": 10.0,
                        "sell_sm_amount": 25.0,
                    }
                ]
            )

        def moneyflow_dc(self, **kwargs):
            calls["moneyflow_dc_kwargs"] = kwargs
            return pd.DataFrame([{"trade_date": "20260605", "ts_code": "600519.SH"}])

        def moneyflow_ths(self, **kwargs):
            calls["moneyflow_ths_kwargs"] = kwargs
            return pd.DataFrame([{"trade_date": "20260605", "ts_code": "600519.SH"}])

    def pro_api(token: str):
        calls["token"] = token
        return FakePro()

    fake.pro_api = pro_api
    return fake


def test_tushare_source_registers_moneyflow_capabilities() -> None:
    caps = {cap.name for cap in TuShareSource().capabilities}

    assert {"moneyflow", "moneyflow_dc", "moneyflow_ths"} <= caps


def test_tushare_source_requires_env_token(monkeypatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("TUSHARE_PRO_TOKEN", raising=False)
    monkeypatch.delenv("TS_TOKEN", raising=False)

    try:
        TuShareSource().fetch("moneyflow", ts_code="600519.SH")
    except RuntimeError as exc:
        assert "TuShare token missing" in str(exc)
    else:
        raise AssertionError("TuShareSource should require an env token")


def test_tushare_source_dispatches_moneyflow_and_normalizes_columns(monkeypatch) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    monkeypatch.setitem(sys.modules, "tushare", _make_fake_tushare_module(calls))

    data = TuShareSource().fetch("moneyflow", ts_code="600519.SH", start_date="20260601")

    assert calls["token"] == "test-token"
    assert calls["moneyflow_kwargs"] == {"ts_code": "600519.SH", "start_date": "20260601"}
    assert data[0]["main_net_amount"] == 30.0
    assert data[0]["super_large_net_amount"] == 40.0
    assert data[0]["large_net_amount"] == -10.0
    assert data[0]["medium_net_amount"] == 5.0
    assert data[0]["small_net_amount"] == -15.0


def test_resolve_routes_moneyflow_to_tushare_when_requested(monkeypatch) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    monkeypatch.setitem(sys.modules, "tushare", _make_fake_tushare_module(calls))

    data, source = resolve("moneyflow", prefer_source="tushare", ts_code="600519.SH")

    assert source == "tushare"
    assert data[0]["trade_date"] == "20260605"
    assert data[0]["super_large_net_amount"] == 40.0
