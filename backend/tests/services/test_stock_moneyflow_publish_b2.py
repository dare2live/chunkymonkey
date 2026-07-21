"""B2 stock-day moneyflow publication: fact_stock_moneyflow(_dc)_daily strangler (TDD).

Unblocks S7 serve_l0_leaf COMPAT for raw_tushare_moneyflow + raw_tushare_moneyflow_dc
by publishing stock-day grain with available_at + lineage, then redirecting DataAccess.
No fake DC membership PIT — moneyflow_dc is stock-day vendor flow, not membership.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import stock_moneyflow_publish as pub
from services.duck_adapter import connect as duck_connect


_SH = ZoneInfo("Asia/Shanghai")


def _raw_conn(tmp_path: Path):
    path = tmp_path / "tushare_raw.duckdb"
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
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("600001.SH", "20260717", 1.0, 2.0, 3.0, 4.0, 0.5, 0.5, 0.5, 0.5, 8.0, "x"),
            ("600002.SZ", "20260717", 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0, -4.0, "x"),
            ("600001.SH", "20260718", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, "x"),
        ],
    )
    con.execute(
        """
        CREATE TABLE raw_tushare_moneyflow_dc (
            trade_date VARCHAR,
            ts_code VARCHAR,
            name VARCHAR,
            pct_change DOUBLE,
            close DOUBLE,
            net_amount DOUBLE,
            net_amount_rate DOUBLE,
            built_at VARCHAR
        )
        """
    )
    con.executemany(
        """
        INSERT INTO raw_tushare_moneyflow_dc VALUES
        (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("20260717", "600001.SH", "甲", 2.0, 10.0, 5.0, 1.5, "x"),
            ("20260717", "600002.SZ", "乙", -0.5, 20.0, -3.0, -0.8, "x"),
            ("20260718", "600001.SH", "甲", 0.1, 10.1, 1.0, 0.2, "x"),
        ],
    )
    con.close()
    return path


def _sm_conn(tmp_path: Path):
    path = tmp_path / "smartmoney.duckdb"
    con = duck_connect(str(path), read_only=False)
    con.close()
    return path


def test_available_at_is_trade_date_1800_shanghai() -> None:
    at = pub.moneyflow_available_at("20260717")
    assert at == datetime(2026, 7, 17, 18, 0, tzinfo=_SH)


def test_publish_moneyflow_materializes_stock_day_grain(tmp_path, monkeypatch) -> None:
    raw = _raw_conn(tmp_path)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)

    out = pub.publish_fact_stock_moneyflow_daily(start="20260717", end="20260718")
    assert out["rows"] == 3
    assert out["table"] == pub.TABLE_MF
    assert out["grain"] == ["trade_date", "ts_code"]

    con = duck_connect(str(sm), read_only=True)
    try:
        cols = {r[0] for r in con.execute(f"DESCRIBE {pub.TABLE_MF}").fetchall()}
        assert {
            "trade_date",
            "ts_code",
            "stock_code",
            "net_mf_amount",
            "buy_sm_amount",
            "buy_md_amount",
            "buy_lg_amount",
            "buy_elg_amount",
            "sell_sm_amount",
            "sell_md_amount",
            "sell_lg_amount",
            "sell_elg_amount",
            "available_at",
            "source_table",
            "built_at",
        } <= cols
        rows = con.execute(
            f"""
            SELECT trade_date, ts_code, stock_code, net_mf_amount,
                   available_at, source_table
            FROM {pub.TABLE_MF}
            ORDER BY trade_date, ts_code
            """
        ).fetchall()
        assert len(rows) == 3
        assert rows[0][0] == "20260717"
        assert rows[0][1] == "600001.SH"
        assert rows[0][2] == "600001"
        assert rows[0][3] == 8.0
        assert rows[0][4] == datetime(2026, 7, 17, 18, 0, tzinfo=_SH)
        assert rows[0][5] == "raw_tushare_moneyflow"
        dups = con.execute(
            f"""
            SELECT COUNT(*) FROM (
              SELECT trade_date, ts_code, COUNT(*) c
              FROM {pub.TABLE_MF} GROUP BY 1,2 HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        assert dups == 0
    finally:
        con.close()


def test_publish_moneyflow_dc_materializes_stock_day_grain(tmp_path, monkeypatch) -> None:
    raw = _raw_conn(tmp_path)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)

    out = pub.publish_fact_stock_moneyflow_dc_daily(start="20260717", end="20260718")
    assert out["rows"] == 3
    assert out["table"] == pub.TABLE_MF_DC
    assert out["grain"] == ["trade_date", "ts_code"]

    con = duck_connect(str(sm), read_only=True)
    try:
        cols = {r[0] for r in con.execute(f"DESCRIBE {pub.TABLE_MF_DC}").fetchall()}
        assert {
            "trade_date",
            "ts_code",
            "stock_code",
            "net_amount",
            "net_amount_rate",
            "pct_change",
            "available_at",
            "source_table",
            "built_at",
        } <= cols
        rows = con.execute(
            f"""
            SELECT trade_date, ts_code, stock_code, net_amount, pct_change,
                   available_at, source_table
            FROM {pub.TABLE_MF_DC}
            ORDER BY trade_date, ts_code
            """
        ).fetchall()
        assert len(rows) == 3
        assert rows[0][3] == 5.0
        assert rows[0][4] == 2.0
        assert rows[0][5] == datetime(2026, 7, 17, 18, 0, tzinfo=_SH)
        assert rows[0][6] == "raw_tushare_moneyflow_dc"
    finally:
        con.close()


def test_publish_both_is_idempotent_partition_replace(tmp_path, monkeypatch) -> None:
    raw = _raw_conn(tmp_path)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    pub.publish_fact_stock_moneyflow_daily(start="20260717", end="20260717")
    pub.publish_fact_stock_moneyflow_dc_daily(start="20260717", end="20260717")

    rcon = duck_connect(str(raw), read_only=False)
    rcon.execute("DELETE FROM raw_tushare_moneyflow WHERE trade_date = '20260717'")
    rcon.execute(
        """
        INSERT INTO raw_tushare_moneyflow VALUES
        ('600099.SH', '20260717', 1,1,1,1,1,1,1,1,9.0, 'y')
        """
    )
    rcon.execute("DELETE FROM raw_tushare_moneyflow_dc WHERE trade_date = '20260717'")
    rcon.execute(
        """
        INSERT INTO raw_tushare_moneyflow_dc VALUES
        ('20260717', '600099.SH', '丙', 1.0, 1.0, 9.0, 1.0, 'y')
        """
    )
    rcon.close()

    out_mf = pub.publish_fact_stock_moneyflow_daily(start="20260717", end="20260717")
    out_dc = pub.publish_fact_stock_moneyflow_dc_daily(start="20260717", end="20260717")
    assert out_mf["rows"] == 1
    assert out_dc["rows"] == 1
    con = duck_connect(str(sm), read_only=True)
    try:
        assert [
            r[0]
            for r in con.execute(
                f"SELECT ts_code FROM {pub.TABLE_MF} WHERE trade_date='20260717'"
            ).fetchall()
        ] == ["600099.SH"]
        assert [
            r[0]
            for r in con.execute(
                f"SELECT ts_code FROM {pub.TABLE_MF_DC} WHERE trade_date='20260717'"
            ).fetchall()
        ] == ["600099.SH"]
    finally:
        con.close()


def test_data_access_moneyflow_entities_point_at_publication() -> None:
    from services.data_access.spec import load_registry

    reg = load_registry()
    mf = reg.entity("moneyflow")
    assert mf.db == "smartmoney"
    assert mf.table == "fact_stock_moneyflow_daily"
    assert mf.layer.startswith("L1")
    assert "available_at" in mf.columns or mf.available_after

    dc = reg.entity("moneyflow_dc")
    assert dc.db == "smartmoney"
    assert dc.table == "fact_stock_moneyflow_dc_daily"
    assert dc.layer.startswith("L1")


def test_legacy_plane_moneyflow_tables_are_compatibility() -> None:
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_legacy_raw_plane.py"
    spec = importlib.util.spec_from_file_location("check_legacy_raw_plane", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    for table, surface in (
        ("raw_tushare_moneyflow", "fact_stock_moneyflow_daily"),
        ("raw_tushare_moneyflow_dc", "fact_stock_moneyflow_dc_daily"),
    ):
        meta = inv["tables"][table]
        assert meta["role"] == "compatibility", table
        assert meta["kind"] == "serve_l0_leaf", table
        assert meta["publication_surface"] == surface, table
    assert mod.collect_violations() == []
    counts = mod.role_counts()
    assert counts["ssot"] == 25
    assert counts["compatibility"] == 20


def test_pulse_entity_ref_uses_smartmoney_bare_tables(monkeypatch) -> None:
    """After redirect, pulse/serve SQL must not prefix tr. for moneyflow entities."""
    from services import market_pulse as mp
    from services import market_pulse_serve_read as serve
    from services.data_access.spec import load_registry

    monkeypatch.setattr(mp, "_ACCESS_REG", None)
    monkeypatch.setattr(serve, "_REG", None)
    reg = load_registry()
    assert reg.entity("moneyflow").db == "smartmoney"
    assert reg.entity("moneyflow_dc").db == "smartmoney"
    assert mp._tr_entity("moneyflow") == "fact_stock_moneyflow_daily"
    assert mp._tr_entity("moneyflow_dc") == "fact_stock_moneyflow_dc_daily"
    assert serve._tr("moneyflow") == "fact_stock_moneyflow_daily"
    assert serve._tr("moneyflow_dc") == "fact_stock_moneyflow_dc_daily"
    # still-raw leaf stays on tr.
    assert mp._tr_entity("dc_member").startswith("tr.raw_")
    assert serve._tr("dc_member").startswith("tr.raw_")
