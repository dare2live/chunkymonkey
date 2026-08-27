"""E0 read-side shadow: legacy research tables vs accepted canonical (no cutover)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from services.data_sources.disclosure_dual_write import (
    write_holders_top10_formal_then_mirror,
    write_org_holding_formal_then_mirror,
    write_stk_holdertrade_formal_then_mirror,
)
from services.data_sources.disclosure_shadow_compare import (
    compare_disclosure_research_shadow,
    empty_disclosure_shadow,
)
from services.data_sources.holders_top10_schema import (
    COMPATIBILITY_TABLE as HOLDERS_LEGACY,
)
from services.data_sources.org_holding_schema import (
    COMPATIBILITY_TABLE as ORG_LEGACY,
)
from services.data_sources.stk_holdertrade_schema import (
    COMPATIBILITY_TABLE as STK_LEGACY,
)
from services.duck_adapter import connect
from services.schema_core import CORE_SCHEMA_SQL

PARTITION_HOLDERS = "20260429"
PARTITION_ORG = "20260430"
PARTITION_STK = "20190102"
OBSERVED_HOLDERS = datetime(
    2026, 4, 29, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")
).astimezone(timezone.utc)
OBSERVED_ORG = datetime(
    2026, 4, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")
).astimezone(timezone.utc)
OBSERVED_STK = datetime(
    2019, 1, 2, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")
).astimezone(timezone.utc)


def _holders_row(**overrides):
    base = {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "market": "",
        "report_date": "20260331",
        "holder_set": "free",
        "holder_rank": 1,
        "row_seq": 1,
        "holder_name": "香港中央结算有限公司",
        "holder_name_norm": "香港中央结算有限公司",
        "share_class": "A",
        "is_secondary_class": False,
        "is_exit_row": False,
        "shares_text": None,
        "shares_approx": 100,
        "shares_precision": None,
        "hold_amount": 100.0,
        "hold_ratio_float": 7.12,
        "hold_ratio_total": None,
        "hold_ratio": 7.12,
        "hold_market_cap": None,
        "holder_type": None,
        "share_nature": None,
        "change_status": "不变",
        "change_shares_text": None,
        "change_shares_approx": 0,
        "hold_change": "",
        "hold_change_num": 0.0,
        "notice_date": PARTITION_HOLDERS,
        "effective_date": None,
        "page_update_date": PARTITION_HOLDERS,
        "availability_source": "page_update_date",
        "source": "miaoxiang",
        "source_tier": 1,
        "raw_hash": None,
        "fetched_at": OBSERVED_HOLDERS.isoformat(),
        "created_at": OBSERVED_HOLDERS.isoformat(),
    }
    base.update(overrides)
    return base


def _org_row(**overrides):
    base = {
        "report_date": "2026-03-31",
        "available_date": "2026-04-30",
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "org_type_code": "07",
        "org_type_name": "QFII",
        "holder_code": "10010626",
        "holder_name": "香港中央结算有限公司",
        "fund_code": None,
        "fund_derivecode": "",
        "fund_manager": None,
        "fund_type": None,
        "total_shares": 1.2e8,
        "hold_value": None,
        "total_shares_ratio": None,
        "free_shares_ratio": 7.12,
        "free_market_cap": None,
        "free_shares": None,
        "fsr_change": None,
        "fsr_rate_change": None,
        "change_type": None,
        "source": "miaoxiang",
        "source_tier": 1,
        "fetched_at": OBSERVED_ORG.isoformat(),
    }
    base.update(overrides)
    return base


def _stk_row(**overrides):
    base = {
        "ts_code": "300010.SZ",
        "ann_date": PARTITION_STK,
        "holder_name": "窦昕",
        "holder_type": "P",
        "in_de": "IN",
        "change_vol": 10076031.0,
        "change_ratio": 1.5963,
        "after_share": 10076031.0,
        "after_ratio": 1.5963,
        "avg_price": 14.77,
        "total_share": 10076031.0,
    }
    base.update(overrides)
    return base


@pytest.fixture
def conn():
    database = connect(":memory:")
    for stmt in CORE_SCHEMA_SQL.split(";"):
        text = stmt.strip()
        if text:
            database.execute(text)
    from services.org_holding_aif10 import ensure_tables

    ensure_tables(database)
    database.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {STK_LEGACY} (
            ts_code VARCHAR,
            ann_date VARCHAR,
            holder_name VARCHAR,
            holder_type VARCHAR,
            in_de VARCHAR,
            change_vol DOUBLE,
            change_ratio DOUBLE,
            after_share DOUBLE,
            after_ratio DOUBLE,
            avg_price DOUBLE,
            total_share DOUBLE
        )
        """
    )
    yield database
    database.close()


def _seed_all_matched(conn) -> None:
    write_holders_top10_formal_then_mirror(
        conn,
        [
            _holders_row(holder_rank=1, holder_name="香港中央结算有限公司"),
            _holders_row(holder_rank=2, holder_name="中国证券金融股份有限公司"),
        ],
        observed_at=OBSERVED_HOLDERS,
        available_at=OBSERVED_HOLDERS,
        enable_legacy_mirror=False,
    )
    write_org_holding_formal_then_mirror(
        conn,
        [
            _org_row(holder_code="10010626"),
            _org_row(holder_code="10020001", holder_name="某公募基金"),
        ],
        observed_at=OBSERVED_ORG,
        available_at=OBSERVED_ORG,
        enable_legacy_mirror=True
    )
    write_stk_holdertrade_formal_then_mirror(
        conn,
        [
            _stk_row(holder_name="窦昕", in_de="IN"),
            _stk_row(holder_name="窦昕", in_de="DE", change_vol=5000.0),
        ],
        observed_at=OBSERVED_STK,
        available_at=OBSERVED_STK,
        enable_legacy_mirror=True
    )


def test_shadow_match_allows_cutover_for_three_domains(conn) -> None:
    _seed_all_matched(conn)
    report = compare_disclosure_research_shadow(
        conn,
        partitions={
            "holders_top10": PARTITION_HOLDERS,
            "org_holding": PARTITION_ORG,
            "stk_holdertrade": PARTITION_STK,
        },
    )
    assert report.overall_status == "MATCH"
    assert report.cutover_allowed is True
    by_domain = {item.domain: item for item in report.domains}
    assert set(by_domain) == {"holders_top10", "org_holding", "stk_holdertrade"}
    for item in report.domains:
        assert item.status == "MATCH"
        assert item.rows_match is True
        assert item.mismatch_count == 0
        assert item.canonical_row_count >= 1
        if item.domain == "holders_top10":
            assert item.legacy_row_count == 0
            assert "holders_compat_retired_canonical_ssot" in item.issues
        else:
            assert item.legacy_row_count >= 1
            assert item.canonical_row_count == item.legacy_row_count
    payload = report.as_dict()
    assert payload["cutover_allowed"] is True
    assert payload["overall_status"] == "MATCH"
    assert "cutover_allowed_true" in payload["notes"]


def test_shadow_domain_conns_routes_stk_to_secondary_db(conn) -> None:
    """API sidecar pattern: holders on default conn, stk on tushare_raw."""

    write_holders_top10_formal_then_mirror(
        conn,
        [_holders_row()],
        observed_at=OBSERVED_HOLDERS,
        available_at=OBSERVED_HOLDERS,
        enable_legacy_mirror=False
    )
    write_org_holding_formal_then_mirror(
        conn,
        [_org_row()],
        observed_at=OBSERVED_ORG,
        available_at=OBSERVED_ORG,
        enable_legacy_mirror=True
    )
    stk_db = connect(":memory:")
    try:
        stk_db.execute(
            f"""
            CREATE TABLE {STK_LEGACY} (
                ts_code VARCHAR, ann_date VARCHAR, holder_name VARCHAR,
                holder_type VARCHAR, in_de VARCHAR, change_vol DOUBLE,
                change_ratio DOUBLE, after_share DOUBLE, after_ratio DOUBLE,
                avg_price DOUBLE, total_share DOUBLE
            )
            """
        )
        write_stk_holdertrade_formal_then_mirror(
            stk_db,
            [_stk_row()],
            observed_at=OBSERVED_STK,
            available_at=OBSERVED_STK,
        enable_legacy_mirror=True
        )
        report = compare_disclosure_research_shadow(
            conn,
            partitions={
                "holders_top10": PARTITION_HOLDERS,
                "org_holding": PARTITION_ORG,
                "stk_holdertrade": PARTITION_STK,
            },
            domain_conns={"stk_holdertrade": stk_db},
        )
        assert report.overall_status == "MATCH"
        assert report.cutover_allowed is True
        stk = next(d for d in report.domains if d.domain == "stk_holdertrade")
        assert stk.status == "MATCH"
    finally:
        stk_db.close()


def test_shadow_detects_intentional_legacy_drift(conn) -> None:
    _seed_all_matched(conn)
    # Holders compat retired — drift probe uses org legacy plane instead.
    conn.execute(
        f"""
        UPDATE {ORG_LEGACY}
           SET holder_name = '漂移公募'
         WHERE holder_code = '10010626'
        """
    )
    report = compare_disclosure_research_shadow(
        conn,
        partitions={
            "holders_top10": PARTITION_HOLDERS,
            "org_holding": PARTITION_ORG,
            "stk_holdertrade": PARTITION_STK,
        },
    )
    assert report.overall_status == "MISMATCH"
    assert report.cutover_allowed is False
    holders = next(d for d in report.domains if d.domain == "holders_top10")
    assert holders.status == "MATCH"
    assert holders.rows_match is True
    org = next(d for d in report.domains if d.domain == "org_holding")
    assert org.status == "MISMATCH"
    assert org.rows_match is False
    assert org.mismatch_count >= 1


def test_shadow_detects_missing_canonical_side(conn) -> None:
    """Empty canonical (compat retired) must not look like MATCH."""

    report = compare_disclosure_research_shadow(
        conn, partitions={"holders_top10": PARTITION_HOLDERS}
    )
    holders = next(d for d in report.domains if d.domain == "holders_top10")
    assert holders.status == "UNAVAILABLE"
    assert holders.rows_match is False
    assert report.cutover_allowed is False
    assert report.overall_status != "MATCH"


def test_empty_shadow_sidecar_is_fail_closed() -> None:
    report = empty_disclosure_shadow(reason="smartmoney_not_attached")
    assert report.overall_status == "UNAVAILABLE"
    assert report.cutover_allowed is False
    assert "smartmoney_not_attached" in report.notes
    for item in report.domains:
        assert item.status == "UNAVAILABLE"
        assert item.rows_match is False


def test_org_date_normalization_does_not_false_mismatch(conn) -> None:
    """Legacy ISO dates vs canonical YYYYMMDD must still MATCH after normalize."""

    write_org_holding_formal_then_mirror(
        conn, [_org_row()], observed_at=OBSERVED_ORG, available_at=OBSERVED_ORG,
        enable_legacy_mirror=True
    )
    # Prove legacy still stores ISO while canonical is compact.
    legacy_avail = conn.execute(
        f"SELECT available_date FROM {ORG_LEGACY} LIMIT 1"
    ).fetchone()[0]
    assert "-" in str(legacy_avail)
    report = compare_disclosure_research_shadow(
        conn, partitions={"org_holding": PARTITION_ORG}
    )
    org = next(d for d in report.domains if d.domain == "org_holding")
    assert org.status == "MATCH"
    # Single-domain sample is not the full inventory → cutover stays false.
    assert report.cutover_allowed is False
