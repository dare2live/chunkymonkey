"""E0 transport strangler: land-only and accept-from-landing are independent.

Mirrors S1/S2 security-day modularity for holders_top10 / org_holding /
stk_holdertrade. Production dual_write must compose caller-only S1→S2
(not fused publish-only internals).
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.data_sources.disclosure_dual_write import (
    write_holders_top10_formal_then_mirror,
)
from services.data_sources.disclosure_transport import (
    DISCLOSURE_TRANSPORT_DOMAINS,
    accept_disclosure_from_landing,
    land_disclosure_partition_from_rows,
    land_then_accept_disclosure_partition,
)
from services.data_sources.holders_top10_schema import (
    CANONICAL_TABLE as HOLDERS_CANONICAL,
    DATASET_ID as HOLDERS_DATASET,
    LANDING_TABLE as HOLDERS_LANDING,
)
from services.data_sources.org_holding_schema import (
    CANONICAL_TABLE as ORG_CANONICAL,
    DATASET_ID as ORG_DATASET,
)
from services.data_sources.stk_holdertrade_schema import (
    CANONICAL_TABLE as STK_CANONICAL,
    DATASET_ID as STK_DATASET,
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
        "report_date": "20260331",
        "holder_set": "free",
        "holder_rank": 1,
        "row_seq": 1,
        "holder_name": "香港中央结算有限公司",
        "holder_name_norm": "香港中央结算有限公司",
        "share_class": "A",
        "shares_approx": 100,
        "change_status": "不变",
        "hold_change_num": 0.0,
        "holder_type": None,
        "hold_ratio_float": 7.12,
        "notice_date": PARTITION_HOLDERS,
        "is_exit_row": False,
    }
    base.update(overrides)
    return base


def _org_row(**overrides):
    base = {
        "report_date": "20260331",
        "available_date": PARTITION_ORG,
        "stock_code": "600519",
        "holder_code": "10010626",
        "fund_derivecode": "",
        "holder_name": "香港中央结算有限公司",
        "org_type_name": "QFII",
        "total_shares": 1.2e8,
        "free_shares_ratio": 7.12,
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
    yield database
    database.close()


def test_disclosure_transport_domains_cover_e0_inventory() -> None:
    assert DISCLOSURE_TRANSPORT_DOMAINS == frozenset(
        {"holders_top10", "org_holding", "stk_holdertrade"}
    )


def test_land_only_holders_does_not_write_canonical(conn) -> None:
    batch = land_disclosure_partition_from_rows(
        "holders_top10",
        conn,
        partition=PARTITION_HOLDERS,
        rows=[_holders_row()],
        observed_at=OBSERVED_HOLDERS,
    )
    assert batch.batch_id.startswith(f"holders_top10:{PARTITION_HOLDERS}:")
    status = conn.execute(
        "SELECT status FROM ingest_batch WHERE batch_id = ?",
        [batch.batch_id],
    ).fetchone()[0]
    assert status == "LANDED"
    assert (
        conn.execute(
            f"SELECT COUNT(*) FROM {HOLDERS_CANONICAL} WHERE notice_date = ?",
            [PARTITION_HOLDERS],
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM accepted_partition WHERE dataset_id = ?",
            [HOLDERS_DATASET],
        ).fetchone()[0]
        == 0
    )
    landing_n = conn.execute(
        f"SELECT COUNT(*) FROM {HOLDERS_LANDING} WHERE batch_id = ?",
        [batch.batch_id],
    ).fetchone()[0]
    assert landing_n == 1


def test_accept_from_landing_holders_zero_provider_fetch(conn) -> None:
    fetch_calls: list[str] = []

    def tracked_rows():
        fetch_calls.append("would_fetch")
        return [_holders_row()]

    # S1 lands from explicit rows — no fetch callback exists on transport.
    rows = tracked_rows()
    batch = land_disclosure_partition_from_rows(
        "holders_top10",
        conn,
        partition=PARTITION_HOLDERS,
        rows=rows,
        observed_at=OBSERVED_HOLDERS,
    )
    assert len(fetch_calls) == 1

    outcome = accept_disclosure_from_landing(
        "holders_top10", conn, batch.batch_id
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == 1
    assert len(fetch_calls) == 1  # unchanged — accept never re-acquires
    assert (
        conn.execute(
            f"SELECT COUNT(*) FROM {HOLDERS_CANONICAL} WHERE notice_date = ?",
            [PARTITION_HOLDERS],
        ).fetchone()[0]
        == 1
    )


def test_land_only_org_and_stk_do_not_write_canonical(conn) -> None:
    org_batch = land_disclosure_partition_from_rows(
        "org_holding",
        conn,
        partition=PARTITION_ORG,
        rows=[_org_row()],
        observed_at=OBSERVED_ORG,
    )
    stk_batch = land_disclosure_partition_from_rows(
        "stk_holdertrade",
        conn,
        partition=PARTITION_STK,
        rows=[_stk_row()],
        observed_at=OBSERVED_STK,
    )
    assert (
        conn.execute(
            f"SELECT COUNT(*) FROM {ORG_CANONICAL}",
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            f"SELECT COUNT(*) FROM {STK_CANONICAL}",
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM accepted_partition WHERE dataset_id IN (?, ?)",
            [ORG_DATASET, STK_DATASET],
        ).fetchone()[0]
        == 0
    )
    org_out = accept_disclosure_from_landing(
        "org_holding", conn, org_batch.batch_id
    )
    stk_out = accept_disclosure_from_landing(
        "stk_holdertrade", conn, stk_batch.batch_id
    )
    assert org_out.status == "ACCEPTED"
    assert stk_out.status == "ACCEPTED"


def test_land_then_accept_is_caller_only_composition(conn) -> None:
    outcome = land_then_accept_disclosure_partition(
        "holders_top10",
        conn,
        partition=PARTITION_HOLDERS,
        rows=[_holders_row()],
        observed_at=OBSERVED_HOLDERS,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == 1


def test_dual_write_source_uses_transport_not_fused_publish_only() -> None:
    """Production dual_write must call land_then_accept (S1→S2), not only publish."""

    dual_src = Path(
        inspect.getsourcefile(write_holders_top10_formal_then_mirror)
    ).read_text(encoding="utf-8")
    assert "land_then_accept_disclosure_partition" in dual_src
    assert "publish_accepted_holders_top10_partition" not in dual_src
    transport_path = Path(__file__).parents[2] / "services" / "data_sources" / (
        "disclosure_transport.py"
    )
    transport_src = transport_path.read_text(encoding="utf-8")
    # Fused publish helpers may remain as thin aliases; land/accept must exist.
    assert "def land_disclosure_partition_from_rows" in transport_src
    assert "def accept_disclosure_from_landing" in transport_src
    assert "def land_then_accept_disclosure_partition" in transport_src
