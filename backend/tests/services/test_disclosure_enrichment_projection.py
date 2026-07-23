"""Typed holders enrichment projection: canonical spine + legacy join."""
from __future__ import annotations

from services.data_sources.disclosure_enrichment_projection import (
    feature_store_profiles_attestation,
    holders_episode_events_sql,
    holders_field_attestations,
)
from services.data_sources.holders_top10_schema import (
    CANONICAL_TABLE,
    COMPATIBILITY_TABLE,
)
from services.duck_adapter import connect


def test_field_attestations_are_typed_not_blanket_legacy() -> None:
    fields = {item.field: item for item in holders_field_attestations()}
    assert fields["stock_code"].status == "ACCEPTED"
    assert fields["stock_code"].source == "canonical"
    assert fields["shares_approx"].status == "ACCEPTED"
    assert fields["shares_approx"].source == "canonical"
    assert fields["hold_change_num"].status == "ACCEPTED"
    assert "xinjin" in fields["hold_change_num"].reason
    att = feature_store_profiles_attestation()
    assert att["status"] == "ACCEPTED"
    assert att["rebuild_source"] == "canonical_spine_legacy_enrichment_projection"
    assert "all_episode_fields_on_canonical" in att["reason"]
    assert not any(f["status"] == "PARTIAL" for f in att["fields"])


def test_projection_prefers_canonical_enrichment_over_legacy() -> None:
    conn = connect(":memory:")
    try:
        conn.execute(
            f"""
            CREATE TABLE {CANONICAL_TABLE} (
                stock_code VARCHAR, report_date VARCHAR, holder_set VARCHAR,
                holder_rank INTEGER, row_seq INTEGER, holder_name VARCHAR,
                hold_ratio_float DOUBLE, notice_date VARCHAR, is_exit_row BOOLEAN,
                holder_name_norm VARCHAR, share_class VARCHAR, shares_approx BIGINT,
                change_status VARCHAR, hold_change_num DOUBLE, holder_type VARCHAR
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE {COMPATIBILITY_TABLE} (
                stock_code VARCHAR, report_date VARCHAR, holder_set VARCHAR,
                holder_rank INTEGER, row_seq INTEGER, holder_name VARCHAR,
                hold_ratio_float DOUBLE, notice_date VARCHAR, is_exit_row BOOLEAN,
                holder_name_norm VARCHAR, share_class VARCHAR, shares_approx BIGINT,
                change_status VARCHAR, hold_change_num DOUBLE, holder_type VARCHAR,
                raw_hash VARCHAR, source VARCHAR
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO {CANONICAL_TABLE} VALUES
            ('600000','20240331','free',1,1,'基金一号',1.0,'20240430',false,
             '基金一号','A',100,'新进',NULL,'基金')
            """
        )
        conn.execute(
            f"""
            INSERT INTO {COMPATIBILITY_TABLE} VALUES
            ('600000','20240331','free',1,1,'基金一号',1.0,'20240430',false,
             '基金一号','A',999,'新进',NULL,'基金','h1','miaoxiang'),
            ('600000','20240630','free',1,1,'基金一号',1.0,'20240730',true,
             '基金一号','A',100,'退出',-100,'基金','h2','miaoxiang')
            """
        )
        sql = holders_episode_events_sql()
        rows = {
            (r[1], r[2], r[3]): r  # stock, report_date, change_status
            for r in conn.execute(
                f"""
                SELECT holder_name_norm, stock_code, report_date, change_status,
                       shares_approx, is_exit_row
                  FROM ({sql}) AS t
                """
            ).fetchall()
        }
        assert ("600000", "20240331", "新进") in rows
        assert rows[("600000", "20240331", "新进")][4] == 100  # canonical wins
        assert ("600000", "20240630", "退出") in rows  # legacy_only
    finally:
        conn.close()
