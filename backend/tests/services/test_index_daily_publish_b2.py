"""B2 index×trade_date publication: fact_index_daily strangler (TDD).

Unblocks S7 multi_consumer COMPAT for raw_tushare_index_daily by publishing
index-day close series with available_at + lineage, then redirecting DataAccess.
Consumers: paper/RS/tech/inst. Does not touch top_inst or dc_member PIT.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import index_daily_publish as pub
from services.duck_adapter import connect as duck_connect


_SH = ZoneInfo("Asia/Shanghai")


def _raw_conn(tmp_path: Path):
    path = tmp_path / "tushare_raw.duckdb"
    con = duck_connect(str(path), read_only=False)
    con.execute(
        """
        CREATE TABLE raw_tushare_index_daily (
            ts_code VARCHAR,
            trade_date VARCHAR,
            close DOUBLE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            pre_close DOUBLE,
            change DOUBLE,
            pct_chg DOUBLE,
            vol DOUBLE,
            amount DOUBLE,
            built_at VARCHAR
        )
        """
    )
    con.executemany(
        """
        INSERT INTO raw_tushare_index_daily VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("000300.SH", "20260717", 3800.0, 3790.0, 3810.0, 3780.0,
             3795.0, 5.0, 0.13, 1e9, 2e10, "x"),
            ("000905.SH", "20260717", 5800.0, 5790.0, 5810.0, 5780.0,
             5795.0, 5.0, 0.09, 1e9, 2e10, "x"),
            ("000300.SH", "20260718", 3810.0, 3800.0, 3820.0, 3790.0,
             3800.0, 10.0, 0.26, 1e9, 2e10, "x"),
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
    at = pub.index_daily_available_at("20260717")
    assert at == datetime(2026, 7, 17, 18, 0, tzinfo=_SH)


def test_publish_materializes_index_day_close_grain(tmp_path, monkeypatch) -> None:
    raw = _raw_conn(tmp_path)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)

    out = pub.publish_fact_index_daily(start="20260717", end="20260718")
    assert out["rows"] == 3
    assert out["table"] == pub.TABLE
    assert out["grain"] == ["trade_date", "ts_code"]

    con = duck_connect(str(sm), read_only=True)
    try:
        cols = {r[0] for r in con.execute(f"DESCRIBE {pub.TABLE}").fetchall()}
        assert {
            "trade_date",
            "ts_code",
            "close",
            "available_at",
            "source_table",
            "built_at",
        } <= cols
        rows = con.execute(
            f"""
            SELECT trade_date, ts_code, close, available_at, source_table
            FROM {pub.TABLE}
            ORDER BY trade_date, ts_code
            """
        ).fetchall()
        assert len(rows) == 3
        assert rows[0][0] == "20260717"
        assert rows[0][1] == "000300.SH"
        assert rows[0][2] == 3800.0
        assert rows[0][3] == datetime(2026, 7, 17, 18, 0, tzinfo=_SH)
        assert rows[0][4] == "raw_tushare_index_daily"
        dups = con.execute(
            f"""
            SELECT COUNT(*) FROM (
              SELECT trade_date, ts_code, COUNT(*) c
              FROM {pub.TABLE} GROUP BY 1,2 HAVING COUNT(*) > 1
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
    pub.publish_fact_index_daily(start="20260717", end="20260717")

    rcon = duck_connect(str(raw), read_only=False)
    rcon.execute("DELETE FROM raw_tushare_index_daily WHERE trade_date = '20260717'")
    rcon.execute(
        """
        INSERT INTO raw_tushare_index_daily VALUES
        ('000016.SH', '20260717', 2500.0, 2490.0, 2510.0, 2480.0,
         2495.0, 5.0, 0.2, 1e8, 1e9, 'y')
        """
    )
    rcon.close()

    out = pub.publish_fact_index_daily(start="20260717", end="20260717")
    assert out["rows"] == 1
    con = duck_connect(str(sm), read_only=True)
    try:
        assert [
            r[0]
            for r in con.execute(
                f"SELECT ts_code FROM {pub.TABLE} WHERE trade_date='20260717'"
            ).fetchall()
        ] == ["000016.SH"]
    finally:
        con.close()


def test_data_access_index_daily_points_at_publication() -> None:
    from services.data_access.spec import load_registry

    reg = load_registry()
    ent = reg.entity("index_daily")
    assert ent.db == "smartmoney"
    assert ent.table == "fact_index_daily"
    assert ent.layer.startswith("L1")
    assert "available_at" in ent.columns or ent.available_after
    assert ent.code_input == "ts_passthrough"


def test_legacy_plane_index_daily_is_compatibility() -> None:
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_legacy_raw_plane.py"
    spec = importlib.util.spec_from_file_location("check_legacy_raw_plane", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_index_daily"]
    assert meta["role"] == "compatibility"
    assert meta["kind"] == "multi_consumer"
    assert meta["publication_surface"] == "fact_index_daily"
    assert mod.collect_violations() == []
    counts = mod.role_counts()
    assert counts["ssot"] == 23
    assert counts["compatibility"] == 22


def test_consumers_resolve_index_daily_off_raw_leaf(monkeypatch) -> None:
    """pulse / tech / inst must resolve publication, not tr.raw_tushare_index_daily."""
    from services import market_pulse as mp
    from services import institution_profile as ip
    from services import technical_states as ts
    from services.data_access.spec import load_registry

    monkeypatch.setattr(mp, "_ACCESS_REG", None)
    monkeypatch.setattr(ip, "_ACCESS_REG", None)
    reg = load_registry()
    assert reg.entity("index_daily").db == "smartmoney"
    assert reg.entity("index_daily").table == "fact_index_daily"
    assert mp._tr_entity("index_daily") == "fact_index_daily"
    assert ip._tr_entity("index_daily") == "sm.fact_index_daily"
    assert ts._index_daily_rel() == "fact_index_daily"
    # B2 seat plane also off raw.
    assert mp._tr_entity("top_inst") == "fact_top_inst_seat_daily"
    assert ip._tr_entity("top_inst") == "sm.fact_top_inst_seat_daily"
    # B1: dc_member observation-date PIT off raw (not fake range PIT).
    assert mp._tr_entity("dc_member") == "fact_dc_member_daily"
