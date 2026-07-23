"""E0 strangler: formal_only land→accept; optional legacy mirror escape + parity."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from services.data_sources.disclosure_boundaries import (
    DisclosureBoundaryError,
    attest_disclosure_research_surface,
    authorize_nonconforming_direct_write,
    disclosure_inventory,
    refuse_accepted_publication_claim,
)
from services.data_sources.disclosure_dual_write import (
    write_holders_top10_formal_then_mirror,
    write_org_holding_formal_then_mirror,
    write_stk_holdertrade_formal_then_mirror,
)
from services.data_sources.holders_top10_schema import (
    CANONICAL_TABLE as HOLDERS_CANONICAL,
    COMPATIBILITY_TABLE as HOLDERS_LEGACY,
    PROVIDER_FIELDS as HOLDERS_FIELDS,
)
from services.data_sources.org_holding_schema import (
    CANONICAL_TABLE as ORG_CANONICAL,
    COMPATIBILITY_TABLE as ORG_LEGACY,
    PROVIDER_FIELDS as ORG_FIELDS,
)
from services.data_sources.stk_holdertrade_schema import (
    CANONICAL_TABLE as STK_CANONICAL,
    COMPATIBILITY_TABLE as STK_LEGACY,
    PROVIDER_FIELDS as STK_FIELDS,
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
    # Legacy compatibility DDL used by research surfaces.
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


def test_inventory_runtime_state_is_formal_only() -> None:
    inventory = {item["domain"]: item for item in disclosure_inventory()}
    for domain in ("holders_top10", "org_holding", "stk_holdertrade"):
        assert inventory[domain]["runtime_state"] == "formal_only"
        assert inventory[domain]["conformity"] == "NONCONFORMING"
        assert inventory[domain]["legacy_mirror_deprecated"] is True
        assert inventory[domain]["legacy_mirror_default"] is False
        # Naked escape is test-only; production is formal_only.
        with pytest.raises(
            DisclosureBoundaryError, match="naked_nonconforming_escape_retired"
        ):
            authorize_nonconforming_direct_write(
                domain, conformity="NONCONFORMING"
            )
        permit = authorize_nonconforming_direct_write(
            domain, conformity="NONCONFORMING", allow_test_escape=True
        )
        assert permit.publication == "nonconforming_direct_write"
    report = attest_disclosure_research_surface()
    assert report.cutover_allowed is False
    with pytest.raises(DisclosureBoundaryError, match="dataset_snapshot"):
        refuse_accepted_publication_claim("holders_top10", "DatasetSnapshot")


def test_accept_holders_partition_from_legacy_noop_mirror(conn) -> None:
    """Canary path: formal accept from pre-seeded legacy without wiping history."""
    from services.holders_aif10 import accept_holders_top10_partition_from_legacy

    seed = [
        _holders_row(holder_rank=1, holder_name="甲"),
        _holders_row(holder_rank=2, holder_name="乙"),
        _holders_row(
            stock_code="000001",
            notice_date="20260101",
            holder_name="其它期不应被删",
        ),
    ]
    # Seed legacy via test escape (pre-canary state).
    from services.holders_aif10 import _write_legacy_direct

    _write_legacy_direct(conn, [seed[0], seed[1]], as_mirror=False)
    _write_legacy_direct(conn, [seed[2]], as_mirror=False)

    outcome = accept_holders_top10_partition_from_legacy(conn, PARTITION_HOLDERS)
    assert outcome.status == "ACCEPTED"
    assert outcome.partitions == (PARTITION_HOLDERS,)
    assert outcome.canonical_rows == 2
    # Other-period legacy row survives no-op mirror.
    other = conn.execute(
        f"""
        SELECT COUNT(*) FROM {HOLDERS_LEGACY}
         WHERE stock_code = '000001' AND notice_date = '20260101'
        """
    ).fetchone()[0]
    assert other == 1
    canon = conn.execute(
        f"SELECT COUNT(*) FROM {HOLDERS_CANONICAL} WHERE notice_date = ?",
        [PARTITION_HOLDERS],
    ).fetchone()[0]
    assert canon == 2


def test_accept_org_holding_partition_from_legacy_noop_mirror(conn) -> None:
    from services.org_holding_aif10 import (
        accept_org_holding_partition_from_legacy,
        _upsert_rows_legacy_direct,
        ensure_tables,
    )

    ensure_tables(conn)
    _upsert_rows_legacy_direct(
        conn,
        [
            _org_row(holder_code="10010626"),
            _org_row(holder_code="10020001", holder_name="某公募基金"),
            _org_row(
                available_date="2019-04-30",
                report_date="2019-03-31",
                holder_code="10010626",
                stock_code="000001",
            ),
        ],
        as_mirror=False,
    )
    outcome = accept_org_holding_partition_from_legacy(conn, PARTITION_ORG)
    assert outcome.status == "ACCEPTED"
    assert outcome.partitions == (PARTITION_ORG,)
    assert outcome.canonical_rows == 2
    other = conn.execute(
        f"""
        SELECT COUNT(*) FROM {ORG_LEGACY}
         WHERE stock_code = '000001'
           AND replace(CAST(available_date AS VARCHAR), '-', '') = '20190430'
        """
    ).fetchone()[0]
    assert other == 1


def test_accept_stk_holdertrade_partition_from_legacy_noop_mirror(conn) -> None:
    from services.data_sources.disclosure_boundaries import (
        authorize_nonconforming_direct_write,
    )
    from services.data_sources.disclosure_dual_write import (
        accept_stk_holdertrade_partition_from_legacy,
    )
    from services.data_sources.stk_holdertrade_schema import PROVIDER_FIELDS

    authorize_nonconforming_direct_write(
        "stk_holdertrade",
        conformity="NONCONFORMING",
        allow_test_escape=True,
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {STK_LEGACY} (
            ts_code VARCHAR, ann_date VARCHAR, holder_name VARCHAR, in_de VARCHAR,
            holder_type VARCHAR, change_vol DOUBLE, change_ratio DOUBLE,
            after_share DOUBLE, after_ratio DOUBLE, avg_price DOUBLE,
            total_share DOUBLE
        )
        """
    )
    seed = [_stk_row(holder_name="窦昕", in_de="IN"), _stk_row(holder_name="乙", in_de="DE")]
    cols = ", ".join(PROVIDER_FIELDS)
    placeholders = ", ".join("?" for _ in PROVIDER_FIELDS)
    conn.executemany(
        f"INSERT INTO {STK_LEGACY} ({cols}) VALUES ({placeholders})",
        [tuple(row.get(name) for name in PROVIDER_FIELDS) for row in seed],
    )
    # Other partition must survive no-op mirror.
    other = dict(_stk_row(ann_date="20190103", holder_name="其它日"))
    conn.execute(
        f"INSERT INTO {STK_LEGACY} ({cols}) VALUES ({placeholders})",
        tuple(other.get(name) for name in PROVIDER_FIELDS),
    )

    outcome = accept_stk_holdertrade_partition_from_legacy(conn, PARTITION_STK)
    assert outcome.status == "ACCEPTED"
    assert outcome.partitions == (PARTITION_STK,)
    assert outcome.canonical_rows == 2
    assert (
        conn.execute(
            f"SELECT COUNT(*) FROM {STK_LEGACY} WHERE ann_date = '20190103'"
        ).fetchone()[0]
        == 1
    )


def test_holders_formal_only_skips_legacy_mirror_by_default(conn) -> None:
    rows = [
        _holders_row(holder_rank=1, holder_name="香港中央结算有限公司"),
        _holders_row(holder_rank=2, holder_name="中国证券金融股份有限公司"),
    ]
    outcome = write_holders_top10_formal_then_mirror(
        conn, rows, observed_at=OBSERVED_HOLDERS, available_at=OBSERVED_HOLDERS
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.legacy_rows_written == 0
    assert outcome.canonical_rows == 2
    assert (
        conn.execute(
            f"SELECT COUNT(*) FROM {HOLDERS_LEGACY} WHERE source = 'miaoxiang'"
        ).fetchone()[0]
        == 0
    )
    # Enrichment columns land on canonical without legacy mirror.
    enrich = conn.execute(
        f"""
        SELECT holder_name_norm, share_class, shares_approx, change_status
          FROM {HOLDERS_CANONICAL}
         ORDER BY holder_rank
        """
    ).fetchall()
    assert enrich[0][0] == "香港中央结算有限公司"
    assert enrich[0][1] == "A"
    assert enrich[0][2] == 100


def test_holders_dual_write_formal_legacy_parity_with_escape(conn) -> None:
    rows = [
        _holders_row(holder_rank=1, holder_name="香港中央结算有限公司"),
        _holders_row(holder_rank=2, holder_name="中国证券金融股份有限公司"),
    ]
    outcome = write_holders_top10_formal_then_mirror(
        conn,
        rows,
        observed_at=OBSERVED_HOLDERS,
        available_at=OBSERVED_HOLDERS,
        enable_legacy_mirror=True,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.legacy_rows_written == 2
    assert outcome.canonical_rows == 2

    canon = [
        tuple(row)
        for row in conn.execute(
            f"""
            SELECT {", ".join(HOLDERS_FIELDS)}
              FROM {HOLDERS_CANONICAL}
             ORDER BY holder_rank
            """
        ).fetchall()
    ]
    legacy = [
        tuple(row)
        for row in conn.execute(
            f"""
            SELECT {", ".join(HOLDERS_FIELDS)}
              FROM {HOLDERS_LEGACY}
             WHERE source = 'miaoxiang'
             ORDER BY holder_rank
            """
        ).fetchall()
    ]
    assert canon == legacy
    assert len(canon) == 2


def test_holders_per_stock_merge_does_not_wipe_other_stock(conn) -> None:
    first = write_holders_top10_formal_then_mirror(
        conn,
        [_holders_row(stock_code="600519", holder_name="甲")],
        observed_at=OBSERVED_HOLDERS,
        available_at=OBSERVED_HOLDERS,
    )
    assert first.status == "ACCEPTED"
    second = write_holders_top10_formal_then_mirror(
        conn,
        [_holders_row(stock_code="000001", holder_name="乙")],
        observed_at=OBSERVED_HOLDERS,
        available_at=OBSERVED_HOLDERS,
    )
    assert second.status == "ACCEPTED"
    codes = {
        row[0]
        for row in conn.execute(
            f"SELECT stock_code FROM {HOLDERS_CANONICAL}"
        ).fetchall()
    }
    assert codes == {"600519", "000001"}


def _approx_row(row: tuple) -> tuple:
    """Normalize REAL/DOUBLE drift on legacy float columns for parity asserts."""

    out = []
    for value in row:
        if isinstance(value, float):
            out.append(round(value, 6))
        else:
            out.append(value)
    return tuple(out)


def test_org_holding_formal_only_skips_legacy_by_default(conn) -> None:
    rows = [
        _org_row(holder_code="10010626"),
        _org_row(holder_code="10020001", holder_name="某公募基金"),
    ]
    outcome = write_org_holding_formal_then_mirror(
        conn, rows, observed_at=OBSERVED_ORG, available_at=OBSERVED_ORG
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.legacy_rows_written == 0
    assert outcome.canonical_rows == 2
    assert conn.execute(f"SELECT COUNT(*) FROM {ORG_LEGACY}").fetchone()[0] == 0


def test_org_holding_dual_write_formal_legacy_parity_with_escape(conn) -> None:
    rows = [
        _org_row(holder_code="10010626"),
        _org_row(holder_code="10020001", holder_name="某公募基金"),
    ]
    outcome = write_org_holding_formal_then_mirror(
        conn,
        rows,
        observed_at=OBSERVED_ORG,
        available_at=OBSERVED_ORG,
        enable_legacy_mirror=True,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.legacy_rows_written == 2

    canon = [
        _approx_row(tuple(row))
        for row in conn.execute(
            f"""
            SELECT report_date, available_date, stock_code, holder_code,
                   fund_derivecode, holder_name, org_type_name,
                   total_shares, free_shares_ratio
              FROM {ORG_CANONICAL}
             ORDER BY holder_code
            """
        ).fetchall()
    ]
    legacy = [
        _approx_row(tuple(row))
        for row in conn.execute(
            f"""
            SELECT replace(report_date, '-', ''),
                   replace(available_date, '-', ''),
                   stock_code, holder_code, fund_derivecode,
                   holder_name, org_type_name, total_shares, free_shares_ratio
              FROM {ORG_LEGACY}
             ORDER BY holder_code
            """
        ).fetchall()
    ]
    assert canon == legacy


def test_stk_holdertrade_formal_only_skips_legacy_by_default(conn) -> None:
    rows = [
        _stk_row(holder_name="窦昕", in_de="IN"),
        _stk_row(holder_name="窦昕", in_de="DE", change_vol=5000.0),
    ]
    outcome = write_stk_holdertrade_formal_then_mirror(
        conn, rows, observed_at=OBSERVED_STK, available_at=OBSERVED_STK
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.legacy_rows_written == 0
    assert outcome.canonical_rows == 2
    assert conn.execute(f"SELECT COUNT(*) FROM {STK_LEGACY}").fetchone()[0] == 0


def test_stk_holdertrade_dual_write_formal_legacy_parity_with_escape(conn) -> None:
    rows = [
        _stk_row(holder_name="窦昕", in_de="IN"),
        _stk_row(holder_name="窦昕", in_de="DE", change_vol=5000.0),
    ]
    outcome = write_stk_holdertrade_formal_then_mirror(
        conn,
        rows,
        observed_at=OBSERVED_STK,
        available_at=OBSERVED_STK,
        enable_legacy_mirror=True,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.legacy_rows_written == 2

    canon = [
        tuple(row)
        for row in conn.execute(
            f"""
            SELECT {", ".join(STK_FIELDS)}
              FROM {STK_CANONICAL}
             ORDER BY in_de
            """
        ).fetchall()
    ]
    legacy = [
        tuple(row)
        for row in conn.execute(
            f"""
            SELECT {", ".join(STK_FIELDS)}
              FROM {STK_LEGACY}
             ORDER BY in_de
            """
        ).fetchall()
    ]
    assert canon == legacy


def test_formal_rejection_does_not_mirror_legacy(conn) -> None:
    """Fail closed: forged available_at rejects formal and skips legacy mirror."""

    forged = datetime(2026, 4, 28, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    with pytest.raises(Exception, match="REJECTED|FORGED|formal"):
        write_holders_top10_formal_then_mirror(
            conn,
            [_holders_row()],
            observed_at=forged,
            available_at=forged,
        )
    legacy_n = conn.execute(
        f"SELECT COUNT(*) FROM {HOLDERS_LEGACY} WHERE source = 'miaoxiang'"
    ).fetchone()[0]
    assert legacy_n == 0


def test_sync_runner_routes_stk_formal_only_no_default_mirror(conn) -> None:
    from services.data_sources import sync_runner as sr

    assert (
        sr._route_disclosure_formal_dual_write(
            conn, "stk_holdertrade", {"legacy_direct_only": True}, [_stk_row()]
        )
        is None
    )
    assert (
        sr._route_disclosure_formal_dual_write(
            conn, "holders_top10", {}, [_holders_row()]
        )
        is None
    )
    written = sr._route_disclosure_formal_dual_write(
        conn, "stk_holdertrade", {}, [_stk_row()]
    )
    assert written == 1
    assert (
        conn.execute(f"SELECT COUNT(*) FROM {STK_CANONICAL}").fetchone()[0] == 1
    )
    assert conn.execute(f"SELECT COUNT(*) FROM {STK_LEGACY}").fetchone()[0] == 0
