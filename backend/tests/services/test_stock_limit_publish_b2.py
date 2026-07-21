"""B2 stock-day limit publication: fact_stock_limit_daily strangler (TDD).

Unblocks S7 serve_l0_leaf COMPAT for raw_tushare_limit_list_d by publishing
stock-day grain with available_at + lineage, then redirecting DataAccess.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import stock_limit_publish as pub
from services.duck_adapter import connect as duck_connect


_SH = ZoneInfo("Asia/Shanghai")


def _raw_conn(tmp_path: Path):
    path = tmp_path / "tushare_raw.duckdb"
    con = duck_connect(str(path), read_only=False)
    con.execute(
        """
        CREATE TABLE raw_tushare_limit_list_d (
            trade_date VARCHAR,
            ts_code VARCHAR,
            name VARCHAR,
            "limit" VARCHAR,
            limit_times DOUBLE,
            first_time VARCHAR,
            fd_amount DOUBLE,
            open_times BIGINT,
            built_at VARCHAR
        )
        """
    )
    con.executemany(
        """
        INSERT INTO raw_tushare_limit_list_d VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("20260717", "600001.SH", "甲", "U", 2.0, "92500", 3000.0, 1, "x"),
            ("20260717", "600002.SZ", "乙", "D", None, None, None, None, "x"),
            ("20260717", "600001.SH", "甲", "Z", None, "94000", None, 2, "x"),
            ("20260718", "600001.SH", "甲", "U", 1.0, "131757", 1000.0, 0, "x"),
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
    at = pub.limit_available_at("20260717")
    assert at == datetime(2026, 7, 17, 18, 0, tzinfo=_SH)


def test_publish_materializes_stock_day_grain_with_lineage(tmp_path, monkeypatch) -> None:
    raw = _raw_conn(tmp_path)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)

    out = pub.publish_fact_stock_limit_daily(start="20260717", end="20260718")
    assert out["rows"] == 4
    assert out["table"] == pub.TABLE
    assert out["grain"] == ["trade_date", "ts_code", "limit"]

    con = duck_connect(str(sm), read_only=True)
    try:
        cols = {r[0] for r in con.execute(f"DESCRIBE {pub.TABLE}").fetchall()}
        assert {"trade_date", "ts_code", "stock_code", "limit", "limit_times",
                "first_time", "fd_amount", "open_times", "available_at",
                "source_table", "built_at"} <= cols
        rows = con.execute(
            f"""
            SELECT trade_date, ts_code, stock_code, "limit", limit_times,
                   available_at, source_table
            FROM {pub.TABLE}
            ORDER BY trade_date, ts_code, "limit"
            """
        ).fetchall()
        assert len(rows) == 4
        assert rows[0][0] == "20260717"
        assert rows[0][1] == "600001.SH"
        assert rows[0][2] == "600001"
        assert rows[0][3] == "U"
        assert rows[0][4] == 2.0
        assert rows[0][5] == datetime(2026, 7, 17, 18, 0, tzinfo=_SH)
        assert rows[0][6] == "raw_tushare_limit_list_d"
        dups = con.execute(
            f"""
            SELECT COUNT(*) FROM (
              SELECT trade_date, ts_code, "limit", COUNT(*) c
              FROM {pub.TABLE} GROUP BY 1,2,3 HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        assert dups == 0
    finally:
        con.close()


def test_publish_is_idempotent_partition_replace(tmp_path, monkeypatch) -> None:
    raw = _raw_conn(tmp_path)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    pub.publish_fact_stock_limit_daily(start="20260717", end="20260717")
    # mutate source: only 1 U row remains for 20260717
    rcon = duck_connect(str(raw), read_only=False)
    rcon.execute("DELETE FROM raw_tushare_limit_list_d WHERE trade_date = '20260717'")
    rcon.execute(
        """
        INSERT INTO raw_tushare_limit_list_d VALUES
        ('20260717', '600099.SH', '丙', 'U', 1.0, '100000', 1.0, 0, 'y')
        """
    )
    rcon.close()
    out = pub.publish_fact_stock_limit_daily(start="20260717", end="20260717")
    assert out["rows"] == 1
    con = duck_connect(str(sm), read_only=True)
    try:
        codes = [r[0] for r in con.execute(
            f"SELECT ts_code FROM {pub.TABLE} WHERE trade_date='20260717'"
        ).fetchall()]
        assert codes == ["600099.SH"]
        # untouched day remains
        n18 = con.execute(
            f"SELECT COUNT(*) FROM {pub.TABLE} WHERE trade_date='20260718'"
        ).fetchone()[0]
        assert n18 == 0  # never published in first call for 18 in this test path
    finally:
        con.close()


def test_data_access_limit_list_d_points_at_publication() -> None:
    from services.data_access.spec import load_registry

    ent = load_registry().entity("limit_list_d")
    assert ent.db == "smartmoney"
    assert ent.table == "fact_stock_limit_daily"
    assert ent.layer.startswith("L1")
    assert "available_at" in ent.columns or ent.available_after


def test_legacy_plane_limit_list_d_is_compatibility() -> None:
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_legacy_raw_plane.py"
    spec = importlib.util.spec_from_file_location("check_legacy_raw_plane", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_limit_list_d"]
    assert meta["role"] == "compatibility"
    assert meta["kind"] == "serve_l0_leaf"
    assert meta["publication_surface"] == "fact_stock_limit_daily"
    assert mod.collect_violations() == []
    counts = mod.role_counts()
    assert counts["ssot"] == 28
    assert counts["compatibility"] == 17


def test_pulse_entity_ref_uses_smartmoney_bare_table(monkeypatch) -> None:
    """After redirect, pulse SQL must not prefix tr. for smartmoney entities."""
    from services import market_pulse as mp
    from services import market_pulse_serve_read as serve
    from services.data_access.spec import load_registry

    monkeypatch.setattr(mp, "_ACCESS_REG", None)
    monkeypatch.setattr(serve, "_REG", None)
    reg = load_registry()
    assert reg.entity("limit_list_d").db == "smartmoney"
    assert mp._tr_entity("limit_list_d") == "fact_stock_limit_daily"
    assert serve._tr("limit_list_d") == "fact_stock_limit_daily"
    # still-raw entities keep tr. prefix
    assert mp._tr_entity("moneyflow").startswith("tr.raw_")
    assert serve._tr("moneyflow").startswith("tr.raw_")
