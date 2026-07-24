"""Type-B same-run fact publish catchup — bounded window after registry drain."""
from __future__ import annotations

from pathlib import Path

import pytest

from services import stock_moneyflow_publish as mf_pub
from services.duck_adapter import connect as duck_connect
from services.type_b_fact_publish_catchup import (
    TYPE_B_PUBLISH_CATCHUP_MAX_DAYS,
    catchup_type_b_fact_publish,
    plan_type_b_publish_window,
)


def test_plan_skips_when_fact_caught_up():
    plan = plan_type_b_publish_window(raw_max="20260720", fact_max="20260720")
    assert plan["action"] == "skip"
    assert plan["reason"] == "fact_caught_up"


def test_plan_window_when_raw_ahead():
    plan = plan_type_b_publish_window(raw_max="20260720", fact_max="20260715")
    assert plan["action"] == "publish_window"
    assert plan["start"] == "20260716"
    assert plan["end"] == "20260720"
    assert plan["lag_days"] == 5


def test_plan_bounds_window_to_max_days():
    plan = plan_type_b_publish_window(
        raw_max="20260720",
        fact_max="20260601",
        max_days=TYPE_B_PUBLISH_CATCHUP_MAX_DAYS,
    )
    assert plan["action"] == "publish_window"
    assert plan["start"] == "20260602"
    assert plan["end"] == "20260711"
    assert plan["lag_days"] == TYPE_B_PUBLISH_CATCHUP_MAX_DAYS


def test_plan_bootstrap_when_fact_empty():
    plan = plan_type_b_publish_window(
        raw_max="20260720",
        fact_max=None,
        max_days=10,
    )
    assert plan["action"] == "publish_window"
    assert plan["end"] == "20260720"
    assert plan["lag_days"] <= 10


def _make_raw_db(path: Path) -> None:
    con = duck_connect(str(path), read_only=False)
    con.execute(
        """
        CREATE TABLE raw_tushare_moneyflow (
            ts_code VARCHAR,
            trade_date VARCHAR,
            buy_sm_amount DOUBLE,
            buy_md_amount DOUBLE,
            buy_lg_amount DOUBLE,
            buy_elg_amount DOUBLE,
            sell_sm_amount DOUBLE,
            sell_md_amount DOUBLE,
            sell_lg_amount DOUBLE,
            sell_elg_amount DOUBLE,
            net_mf_amount DOUBLE,
            built_at VARCHAR
        )
        """
    )
    con.executemany(
        """
        INSERT INTO raw_tushare_moneyflow VALUES
        (?, ?, 1,1,1,1, 1,1,1,1, 1, 'x')
        """,
        [
            ("600001.SH", "20260717"),
            ("600001.SH", "20260718"),
            ("600001.SH", "20260719"),
        ],
    )
    con.close()


def test_catchup_publishes_bounded_window(tmp_path, monkeypatch):
    raw = tmp_path / "raw.duckdb"
    sm = tmp_path / "sm.duckdb"
    _make_raw_db(raw)
    sm_con = duck_connect(str(sm), read_only=False)
    sm_con.execute(
        """
        CREATE TABLE fact_stock_moneyflow_daily (
            trade_date VARCHAR,
            ts_code VARCHAR,
            stock_code VARCHAR,
            net_mf_amount DOUBLE,
            buy_sm_amount DOUBLE,
            buy_md_amount DOUBLE,
            buy_lg_amount DOUBLE,
            buy_elg_amount DOUBLE,
            sell_sm_amount DOUBLE,
            sell_md_amount DOUBLE,
            sell_lg_amount DOUBLE,
            sell_elg_amount DOUBLE,
            available_at TIMESTAMPTZ,
            source_table VARCHAR,
            built_at TIMESTAMPTZ
        )
        """
    )
    sm_con.executemany(
        """
        INSERT INTO fact_stock_moneyflow_daily VALUES
        (?, ?, '600001', 1,1,1,1,1,1,1,1,1, now(), 'raw', now())
        """,
        [("20260717", "600001.SH")],
    )
    sm_con.close()

    calls: list[tuple] = []

    def _only_moneyflow(*, start=None, end=None):
        calls.append((start, end))
        return {"rows": 2, "start": start, "end": end, "mode": "window"}

    monkeypatch.setattr(mf_pub, "RAW_DB", raw)
    monkeypatch.setattr(mf_pub, "SMARTMONEY_DB", sm)
    monkeypatch.setattr(
        "services.type_b_fact_publish_catchup._type_b_specs",
        lambda: (
            __import__(
                "services.type_b_fact_publish_catchup", fromlist=["TypeBPublishSpec"]
            ).TypeBPublishSpec(
                "moneyflow",
                "raw_tushare_moneyflow",
                "fact_stock_moneyflow_daily",
                _only_moneyflow,
            ),
        ),
    )

    out = catchup_type_b_fact_publish(raw_db=raw, smartmoney_db=sm)
    assert out["published_domains"] == 1
    assert len(calls) == 1
    assert calls[0][0] == "20260718"
    assert calls[0][1] == "20260719"
    assert calls[0][0] is not None and calls[0][1] is not None


def test_catchup_never_calls_full_rebuild(tmp_path, monkeypatch):
    raw = tmp_path / "raw.duckdb"
    sm = tmp_path / "sm.duckdb"
    _make_raw_db(raw)
    duck_connect(str(sm), read_only=False).close()

    def _boom(*, start=None, end=None):
        if start is None or end is None:
            raise AssertionError("full rebuild forbidden")
        return {"rows": 0, "start": start, "end": end}

    monkeypatch.setattr(mf_pub, "RAW_DB", raw)
    monkeypatch.setattr(mf_pub, "SMARTMONEY_DB", sm)
    monkeypatch.setattr(
        "services.type_b_fact_publish_catchup._type_b_specs",
        lambda: (
            __import__(
                "services.type_b_fact_publish_catchup", fromlist=["TypeBPublishSpec"]
            ).TypeBPublishSpec(
                "moneyflow",
                "raw_tushare_moneyflow",
                "fact_stock_moneyflow_daily",
                _boom,
            ),
        ),
    )
    catchup_type_b_fact_publish(raw_db=raw, smartmoney_db=sm)


def test_acquire_wires_type_b_publish_after_drain():
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    src = (backend / "services" / "pipeline" / "acquire.py").read_text(encoding="utf-8")
    catchup_src = (
        backend / "services" / "type_b_fact_publish_catchup.py"
    ).read_text(encoding="utf-8")
    drain_idx = src.index("drain_results = _sync_registry_drain(ctx)")
    type_b_idx = src.index("run_acquire_type_b_publish_catchup(ctx)", drain_idx)
    formal_idx = src.index("_sync_formal_on_demand_security_days(ctx)", type_b_idx)
    assert drain_idx < type_b_idx < formal_idx
    assert "type_b_publish" in src
    assert "publish_fn(start=start, end=end)" in catchup_src
