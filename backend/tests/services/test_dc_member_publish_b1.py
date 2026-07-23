"""B1 dc_member observation-date PIT: fact_dc_member_daily strangler (TDD).

Honest PIT = daily membership snapshots (trade_date × board ts_code × con_code)
with available_at + lineage. Vendor history exists via TuShare dc_member
trade_date loops (not range in/out — those columns are absent on this API).
dim_stock_dc_* remain current-snapshot display residuals; retired
v_dc_industry_pit (first-seen only) stays unused.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import dc_member_publish as pub
from services.duck_adapter import connect as duck_connect

_SH = ZoneInfo("Asia/Shanghai")


def _raw_conn(tmp_path: Path):
    path = tmp_path / "tushare_raw.duckdb"
    con = duck_connect(str(path), read_only=False)
    con.execute(
        """
        CREATE TABLE raw_tushare_dc_member (
            trade_date VARCHAR,
            ts_code VARCHAR,
            con_code VARCHAR,
            name VARCHAR,
            built_at VARCHAR
        )
        """
    )
    con.executemany(
        """
        INSERT INTO raw_tushare_dc_member VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("20260715", "BK0001.DC", "600001.SH", "甲", "x"),
            ("20260715", "BK0001.DC", "600002.SZ", "乙", "x"),
            ("20260715", "BK0145.DC", "600503.SH", "华丽家族", "x"),
            ("20260716", "BK0001.DC", "600001.SH", "甲", "x"),
            ("20260716", "BK0001.DC", "600003.SZ", "丙", "x"),
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
    at = pub.dc_member_available_at("20260716")
    assert at == datetime(2026, 7, 16, 18, 0, tzinfo=_SH)


def test_publish_materializes_observation_date_grain(tmp_path, monkeypatch) -> None:
    raw = _raw_conn(tmp_path)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)

    out = pub.publish_fact_dc_member_daily(start="20260715", end="20260716")
    assert out["rows"] == 5
    assert out["table"] == pub.TABLE
    assert out["grain"] == ["trade_date", "ts_code", "con_code"]
    assert out["pit_model"] == "observation_date_snapshot"

    con = duck_connect(str(sm), read_only=True)
    try:
        cols = {r[0] for r in con.execute(f"DESCRIBE {pub.TABLE}").fetchall()}
        assert {
            "trade_date",
            "ts_code",
            "con_code",
            "name",
            "available_at",
            "source_table",
            "built_at",
        } <= cols
        rows = con.execute(
            f"""
            SELECT trade_date, ts_code, con_code, name, available_at, source_table
            FROM {pub.TABLE}
            ORDER BY trade_date, ts_code, con_code
            """
        ).fetchall()
        assert len(rows) == 5
        assert (rows[0][0], rows[0][1], rows[0][2], rows[0][3]) == (
            "20260715",
            "BK0001.DC",
            "600001.SH",
            "甲",
        )
        assert rows[0][4] == datetime(2026, 7, 15, 18, 0, tzinfo=_SH)
        assert rows[0][5] == "raw_tushare_dc_member"
        # Membership drift across days is preserved (not collapsed to latest).
        d15 = {
            (r[1], r[2])
            for r in rows
            if r[0] == "20260715" and r[1] == "BK0001.DC"
        }
        d16 = {
            (r[1], r[2])
            for r in rows
            if r[0] == "20260716" and r[1] == "BK0001.DC"
        }
        assert d15 == {("BK0001.DC", "600001.SH"), ("BK0001.DC", "600002.SZ")}
        assert d16 == {("BK0001.DC", "600001.SH"), ("BK0001.DC", "600003.SZ")}
        dups = con.execute(
            f"""
            SELECT COUNT(*) FROM (
              SELECT trade_date, ts_code, con_code, COUNT(*) c
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
    pub.publish_fact_dc_member_daily(start="20260715", end="20260715")

    rcon = duck_connect(str(raw), read_only=False)
    rcon.execute("DELETE FROM raw_tushare_dc_member WHERE trade_date = '20260715'")
    rcon.execute(
        """
        INSERT INTO raw_tushare_dc_member VALUES
        ('20260715', 'BK0552.DC', '300775.SZ', '三角防务', 'y')
        """
    )
    rcon.close()

    out = pub.publish_fact_dc_member_daily(start="20260715", end="20260715")
    assert out["rows"] == 1
    con = duck_connect(str(sm), read_only=True)
    try:
        assert [
            (r[0], r[1], r[2])
            for r in con.execute(
                f"""
                SELECT ts_code, con_code, name FROM {pub.TABLE}
                WHERE trade_date='20260715'
                """
            ).fetchall()
        ] == [("BK0552.DC", "300775.SZ", "三角防务")]
    finally:
        con.close()


def test_data_access_dc_member_points_at_publication() -> None:
    from services.data_access.spec import load_registry

    reg = load_registry()
    ent = reg.entity("dc_member")
    assert ent.db == "smartmoney"
    assert ent.table == "fact_dc_member_daily"
    assert ent.layer.startswith("L1")
    assert "available_at" in ent.columns or ent.available_after
    assert "trade_date" in ent.columns
    assert "ts_code" in ent.columns
    assert "con_code" in ent.columns


def test_legacy_plane_dc_member_is_compatibility() -> None:
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_legacy_raw_plane.py"
    spec = importlib.util.spec_from_file_location("check_legacy_raw_plane", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_dc_member"]
    assert meta["role"] == "compatibility"
    assert meta["kind"] == "membership_l0"
    assert meta["publication_surface"] == "fact_dc_member_daily"
    assert mod.collect_violations() == []
    counts = mod.role_counts()
    assert counts["ssot"] == 20
    assert counts["compatibility"] == 22


def test_consumers_resolve_dc_member_off_raw_leaf(monkeypatch) -> None:
    """pulse + serve must resolve observation-date publication, not raw leaf."""
    from services import market_pulse as mp
    from services import market_pulse_serve_read as serve
    from services.data_access.spec import load_registry

    monkeypatch.setattr(mp, "_ACCESS_REG", None)
    monkeypatch.setattr(serve, "_REG", None)
    reg = load_registry()
    assert reg.entity("dc_member").db == "smartmoney"
    assert reg.entity("dc_member").table == "fact_dc_member_daily"
    assert mp._tr_entity("dc_member") == "fact_dc_member_daily"
    assert serve._tr("dc_member") == "fact_dc_member_daily"


def test_refuses_fake_range_pit_columns() -> None:
    """Publication must not invent in_date/out_date the vendor does not provide."""
    assert "in_date" not in pub.DDL
    assert "out_date" not in pub.DDL
    assert "valid_from" not in pub.DDL
    assert "valid_to" not in pub.DDL
