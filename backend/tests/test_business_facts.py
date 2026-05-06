import sys
from pathlib import Path

import duckdb


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.business_facts import (  # noqa: E402
    load_institution_scorecard_business_facts,
    load_stock_holder_gate_coverage_map,
    load_sector_active_business_facts_map,
    load_sector_candidate_business_facts_map,
    normalize_holder_gate_counts,
)


def test_load_stock_holder_gate_coverage_map_counts_gate_states():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_current_relationship (
                stock_code TEXT,
                institution_id TEXT,
                follow_gate TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO mart_current_relationship VALUES (?, ?, ?)",
            [
                ("000001", "i1", "follow"),
                ("000001", "i2", "watch"),
                ("000001", "i3", "observe"),
                ("000001", "i4", "avoid"),
                ("000001", "i5", None),
                ("000002", "i6", "watch"),
            ],
        )

        coverage = load_stock_holder_gate_coverage_map(conn, stock_codes=["000001"])

        assert coverage == {
            "000001": {
                "holder_total": 5,
                "holder_follow_count": 1,
                "holder_watch_count": 1,
                "holder_observe_count": 1,
                "holder_avoid_count": 1,
            }
        }
    finally:
        conn.close()


def test_load_stock_holder_gate_coverage_map_empty_filter_skips_scan():
    class _Conn:
        def execute(self, sql, params=()):
            assert "WHERE 1 = 0" in sql
            assert params == ()

            class _Cursor:
                def fetchall(self):
                    return []

            return _Cursor()

    assert load_stock_holder_gate_coverage_map(_Conn(), stock_codes=[]) == {}


def test_normalize_holder_gate_counts_backfills_missing_keys():
    counts = normalize_holder_gate_counts({"holder_total": "3", "holder_follow_count": None})

    assert counts == {
        "holder_total": 3,
        "holder_follow_count": 0,
        "holder_watch_count": 0,
        "holder_observe_count": 0,
        "holder_avoid_count": 0,
    }


def test_sector_business_facts_match_active_and_candidate_semantics():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_current_relationship (
                institution_id TEXT,
                stock_code TEXT,
                tdx_l1 TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_stock_trend (
                stock_code TEXT,
                discovery_score DOUBLE,
                company_quality_score DOUBLE,
                stage_score DOUBLE,
                composite_priority_score DOUBLE,
                price_20d_pct DOUBLE,
                priority_pool TEXT,
                setup_tag TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE dim_stock_industry_context_latest (
                stock_code TEXT,
                tdx_l1 TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO mart_current_relationship VALUES (?, ?, ?)",
            [
                ("inst_a", "600001", "汽车"),
                ("inst_b", "600001", "汽车"),
                ("inst_c", "600002", "半导体"),
            ],
        )
        conn.executemany(
            "INSERT INTO mart_stock_trend VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("600001", 70.0, 85.0, 81.0, 82.0, 5.0, "A池", "setup"),
                ("600002", 65.0, 64.0, 55.0, 68.0, -2.0, "B池", None),
            ],
        )
        conn.executemany(
            "INSERT INTO dim_stock_industry_context_latest VALUES (?, ?)",
            [
                ("600001", "汽车"),
                ("600002", "半导体"),
            ],
        )

        active = load_sector_active_business_facts_map(conn)
        candidate = load_sector_candidate_business_facts_map(conn)

        assert active["汽车"]["active_institution_count"] == 2
        assert active["汽车"]["current_stock_count"] == 1
        assert candidate["汽车"]["candidate_count"] == 1
        assert candidate["汽车"]["a_pool_count"] == 1
        assert candidate["汽车"]["quality_band_80_plus"] == 1
        assert candidate["半导体"]["b_pool_count"] == 1
        assert candidate["半导体"]["win_rate_20d"] == 0.0
    finally:
        conn.close()


def test_institution_scorecard_business_facts_load_raw_distributions():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_institution_profile (
                institution_id TEXT,
                inst_type TEXT,
                quality_score DOUBLE,
                followability_score DOUBLE,
                score_basis TEXT,
                score_confidence TEXT,
                followability_confidence TEXT,
                safe_follow_event_count INTEGER,
                avg_premium_pct DOUBLE,
                buy_event_count INTEGER,
                followability_hint TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO mart_institution_profile VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("inst_a", "fund", 72.0, 68.0, "buy", "high", "high", 5, 1.2, 10, "样本充足"),
                ("inst_b", "fund", 58.0, 40.0, "fallback_all", "medium", "low", 0, 4.8, 0, "样本偏少"),
                ("inst_c", None, 66.0, 71.0, "buy", "high", "medium", 3, 2.0, 8, None),
            ],
        )

        facts = load_institution_scorecard_business_facts(conn)

        assert facts["summary"]["total"] == 3
        assert facts["summary"]["buy_basis_count"] == 2
        assert facts["summary"]["safe_follow_inst_count"] == 2
        assert facts["type_top"][0]["inst_type"] == "fund"
        assert facts["type_top"][0]["total"] == 2
        assert facts["hint_top"][0]["followability_hint"] == "未标注"
        assert facts["confidence_rows"][0]["metric"] == "followability"
        assert facts["confidence_rows"][0]["confidence"] == "high"
    finally:
        conn.close()
