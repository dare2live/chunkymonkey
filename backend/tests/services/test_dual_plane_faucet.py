"""Dual-plane faucet: holders DataAccess + health tip must track canonical."""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def test_data_access_holders_top10_points_at_canonical():
    raw = yaml.safe_load(
        (REPO / "config" / "data_access.yaml").read_text(encoding="utf-8")
    )
    ent = raw["entities"]["holders_top10"]
    assert ent["table"] == "canonical_top10_float_holders_period"
    assert ent["asof_col"] == "notice_date"


def test_holders_watermark_probe_uses_canonical_notice():
    from scripts import update_watermark_sla as sla

    q = sla.DATA_SOURCE_QUERIES["holders_top10_float"]["query"]
    assert "canonical_top10_float_holders_period" in q
    assert "notice_date" in q
    assert "fact_top10_holder_period" not in q


def test_data_layers_holders_health_overrides_canonical_notice():
    raw = yaml.safe_load(
        (REPO / "config" / "data_layers.yaml").read_text(encoding="utf-8")
    )
    ov = raw["table_health_overrides"]
    canon = ov["canonical_top10_float_holders_period"]
    assert canon["expected_freshness"] == "daily"
    assert canon["date_column"] == "notice_date"
    assert "fact_top10_holder_period" not in ov
    assert "fact_top10_holder_period" not in raw.get("tables", {})


def test_data_health_honors_date_column_override():
    import duckdb

    from scripts.data_health_snapshot import compute_health_for_table

    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR,
            report_date VARCHAR,
            notice_date VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period VALUES "
        "('600000', '20260630', '20260725')"
    )
    asset = {
        "table_name": "canonical_top10_float_holders_period",
        "layer": "L1_foundation",
        "expected_freshness": "daily",
        "sla_hours": 168,
        "date_column": "notice_date",
    }
    from datetime import datetime, timezone

    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    snap = compute_health_for_table(con, asset, now)
    assert snap["last_data_date"] in {"20260725", "2026-07-25"}
    assert snap["severity"] == "green"
    con.close()
