"""B2 top_inst seat publication: fact_top_inst_seat_daily strangler (TDD).

Unblocks S7 multi_consumer COMPAT for raw_tushare_top_inst by publishing
honest seat grain (trade_date × ts_code × exalter × side) with available_at +
lineage, then redirecting DataAccess. Consumers: market_pulse lhb_inst_net +
institution_profile C3 LHB. Does not invent DC membership PIT.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import top_inst_seat_publish as pub
from services.duck_adapter import connect as duck_connect


_SH = ZoneInfo("Asia/Shanghai")


def _raw_conn(tmp_path: Path):
    path = tmp_path / "tushare_raw.duckdb"
    con = duck_connect(str(path), read_only=False)
    con.execute(
        """
        CREATE TABLE raw_tushare_top_inst (
            trade_date VARCHAR,
            ts_code VARCHAR,
            exalter VARCHAR,
            side VARCHAR,
            buy DOUBLE,
            buy_rate DOUBLE,
            sell DOUBLE,
            sell_rate DOUBLE,
            net_buy DOUBLE,
            reason VARCHAR,
            built_at VARCHAR
        )
        """
    )
    con.executemany(
        """
        INSERT INTO raw_tushare_top_inst VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("20260717", "600001.SH", "席位甲", "0", 100.0, 1.0, 0.0, 0.0,
             100.0, "涨停", "x"),
            ("20260717", "600001.SH", "席位甲", "1", 0.0, 0.0, 100.0, 1.0,
             100.0, "涨停", "x"),
            ("20260717", "600001.SH", "席位乙", "0", 0.0, 0.0, 30.0, 1.0,
             -30.0, "涨停", "x"),
            ("20260718", "000001.SZ", "机构专用", "0", 50.0, 1.0, 0.0, 0.0,
             50.0, "跌停", "x"),
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
    at = pub.top_inst_seat_available_at("20260717")
    assert at == datetime(2026, 7, 17, 18, 0, tzinfo=_SH)


def test_publish_materializes_seat_grain(tmp_path, monkeypatch) -> None:
    raw = _raw_conn(tmp_path)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)

    out = pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260718")
    assert out["rows"] == 4
    assert out["table"] == pub.TABLE
    assert out["grain"] == ["trade_date", "ts_code", "exalter", "side"]

    con = duck_connect(str(sm), read_only=True)
    try:
        cols = {r[0] for r in con.execute(f"DESCRIBE {pub.TABLE}").fetchall()}
        assert {
            "trade_date",
            "ts_code",
            "exalter",
            "side",
            "net_buy",
            "available_at",
            "source_table",
            "built_at",
        } <= cols
        rows = con.execute(
            f"""
            SELECT trade_date, ts_code, exalter, side, net_buy,
                   available_at, source_table
            FROM {pub.TABLE}
            ORDER BY trade_date, ts_code, exalter, side
            """
        ).fetchall()
        assert len(rows) == 4
        assert rows[0][0] == "20260717"
        assert rows[0][1] == "600001.SH"
        assert rows[0][2] == "席位乙"
        assert rows[0][3] == "0"
        assert rows[0][4] == -30.0
        assert rows[0][5] == datetime(2026, 7, 17, 18, 0, tzinfo=_SH)
        assert rows[0][6] == "raw_tushare_top_inst"
        # Honest seat grain keeps buy/sell sides (no collapse without side).
        sides = {
            (r[2], r[3], r[4])
            for r in rows
            if r[0] == "20260717" and r[1] == "600001.SH"
        }
        assert ("席位甲", "0", 100.0) in sides
        assert ("席位甲", "1", 100.0) in sides
        dups = con.execute(
            f"""
            SELECT COUNT(*) FROM (
              SELECT trade_date, ts_code, exalter, side, COUNT(*) c
              FROM {pub.TABLE} GROUP BY 1,2,3,4 HAVING COUNT(*) > 1
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
    pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")

    rcon = duck_connect(str(raw), read_only=False)
    rcon.execute("DELETE FROM raw_tushare_top_inst WHERE trade_date = '20260717'")
    rcon.execute(
        """
        INSERT INTO raw_tushare_top_inst VALUES
        ('20260717', '600519.SH', '机构专用', '0', 10.0, 1.0, 0.0, 0.0,
         10.0, '涨停', 'y')
        """
    )
    rcon.close()

    out = pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")
    assert out["rows"] == 1
    con = duck_connect(str(sm), read_only=True)
    try:
        assert [
            (r[0], r[1])
            for r in con.execute(
                f"""
                SELECT ts_code, exalter FROM {pub.TABLE}
                WHERE trade_date='20260717'
                """
            ).fetchall()
        ] == [("600519.SH", "机构专用")]
    finally:
        con.close()


def test_data_access_top_inst_points_at_publication() -> None:
    from services.data_access.spec import load_registry

    reg = load_registry()
    ent = reg.entity("top_inst")
    assert ent.db == "smartmoney"
    assert ent.table == "fact_top_inst_seat_daily"
    assert ent.layer.startswith("L1")
    assert "available_at" in ent.columns or ent.available_after
    assert "side" in ent.columns
    assert "exalter" in ent.columns
    assert "net_buy" in ent.columns


def test_legacy_plane_top_inst_is_compatibility() -> None:
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_legacy_raw_plane.py"
    spec = importlib.util.spec_from_file_location("check_legacy_raw_plane", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_top_inst"]
    assert meta["role"] == "compatibility"
    assert meta["kind"] == "multi_consumer"
    assert meta["publication_surface"] == "fact_top_inst_seat_daily"
    assert mod.collect_violations() == []
    counts = mod.role_counts()
    assert counts["ssot"] == 24
    assert counts["compatibility"] == 21


def test_consumers_resolve_top_inst_off_raw_leaf(monkeypatch) -> None:
    """pulse + institution_profile must resolve seat publication, not raw leaf."""
    from services import market_pulse as mp
    from services import institution_profile as ip
    from services.data_access.spec import load_registry

    monkeypatch.setattr(mp, "_ACCESS_REG", None)
    monkeypatch.setattr(ip, "_ACCESS_REG", None)
    reg = load_registry()
    assert reg.entity("top_inst").db == "smartmoney"
    assert reg.entity("top_inst").table == "fact_top_inst_seat_daily"
    assert mp._tr_entity("top_inst") == "fact_top_inst_seat_daily"
    assert ip._tr_entity("top_inst") == "sm.fact_top_inst_seat_daily"
    # dc_member still ssot on raw (this knife does not fake PIT).
    assert mp._tr_entity("dc_member").startswith("tr.raw_")
