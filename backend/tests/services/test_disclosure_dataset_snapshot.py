"""E0 disclosure DatasetSnapshot freeze — canary accepted partitions + hashes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.data_sources.disclosure_boundaries import (
    DisclosureBoundaryError,
    refuse_accepted_publication_claim,
)
from services.data_sources.disclosure_dataset_snapshot import (
    DISCLOSURE_SNAPSHOT_RELPATH,
    freeze_disclosure_dataset_snapshot,
)
from services.data_sources.disclosure_dual_write import (
    write_holders_top10_formal_then_mirror,
    write_org_holding_formal_then_mirror,
    write_stk_holdertrade_formal_then_mirror,
)
from services.data_sources.disclosure_shadow_compare import (
    compare_disclosure_research_shadow,
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


def test_freeze_requires_three_domain_shadow_match(conn, tmp_path: Path) -> None:
    write_holders_top10_formal_then_mirror(
        conn, [_holders_row()], observed_at=OBSERVED_HOLDERS,
        enable_legacy_mirror=False
    )
    # org + stk missing → cutover false → freeze blocked
    shadow = compare_disclosure_research_shadow(
        conn, partitions={"holders_top10": PARTITION_HOLDERS}
    )
    out = tmp_path / "disclosure_dataset_snapshot.json"
    with pytest.raises(DisclosureBoundaryError, match="dataset_snapshot|cutover"):
        freeze_disclosure_dataset_snapshot(
            {"holders_top10": conn, "org_holding": conn, "stk_holdertrade": conn},
            shadow=shadow,
            path=out,
        )
    assert not out.exists()


def test_freeze_writes_minimal_snapshot_on_match(conn, tmp_path: Path) -> None:
    write_holders_top10_formal_then_mirror(
        conn, [_holders_row()], observed_at=OBSERVED_HOLDERS,
        enable_legacy_mirror=False
    )
    write_org_holding_formal_then_mirror(
        conn, [_org_row()], observed_at=OBSERVED_ORG,
        enable_legacy_mirror=True
    )
    write_stk_holdertrade_formal_then_mirror(
        conn, [_stk_row()], observed_at=OBSERVED_STK,
        enable_legacy_mirror=True
    )
    shadow = compare_disclosure_research_shadow(
        conn,
        partitions={
            "holders_top10": PARTITION_HOLDERS,
            "org_holding": PARTITION_ORG,
            "stk_holdertrade": PARTITION_STK,
        },
    )
    assert shadow.overall_status == "MATCH"
    assert shadow.cutover_allowed is True

    # Gate opens for DatasetSnapshot claim once cutover partitions MATCH.
    refuse_accepted_publication_claim(
        "holders_top10", "DatasetSnapshot", cutover_allowed=True
    )

    out = tmp_path / "disclosure_dataset_snapshot.json"
    snap = freeze_disclosure_dataset_snapshot(
        {"holders_top10": conn, "org_holding": conn, "stk_holdertrade": conn},
        shadow=shadow,
        path=out,
    )
    assert out.exists()
    assert snap.snapshot_id.startswith("disclosure_e0_")
    assert snap.cutover_allowed is True
    assert snap.scope == "canary_accepted_partitions"
    assert set(snap.domains) == {"holders_top10", "org_holding", "stk_holdertrade"}
    for domain, part in snap.domains.items():
        assert part["partition"]
        assert part["config_hash"]
        assert part["content_hash"]
        assert int(part["row_count"]) >= 1
        assert part["batch_id"]
        assert part["date_set"] == [part["partition"]]
        assert len(part["accepted"]) == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["relpath"] == DISCLOSURE_SNAPSHOT_RELPATH
    assert payload["phase_e_ablation"] == "blocked_canary_scope_only"


def test_freeze_bounded_partition_sets(conn, tmp_path: Path) -> None:
    """Explicit multi-date sets → bounded_accepted_partitions scope."""

    write_holders_top10_formal_then_mirror(
        conn, [_holders_row()], observed_at=OBSERVED_HOLDERS,
        enable_legacy_mirror=False,
    )
    write_org_holding_formal_then_mirror(
        conn, [_org_row()], observed_at=OBSERVED_ORG,
        enable_legacy_mirror=True,
    )
    write_stk_holdertrade_formal_then_mirror(
        conn, [_stk_row()], observed_at=OBSERVED_STK,
        enable_legacy_mirror=True,
    )
    # Second stk ann_date keeps grain disjoint; cutover shadow stays on canary.
    observed_stk2 = datetime(
        2019, 1, 3, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    ).astimezone(timezone.utc)
    write_stk_holdertrade_formal_then_mirror(
        conn,
        [_stk_row(ann_date="20190103")],
        observed_at=observed_stk2,
        available_at=observed_stk2,
        enable_legacy_mirror=True,
    )
    shadow = compare_disclosure_research_shadow(
        conn,
        partitions={
            "holders_top10": PARTITION_HOLDERS,
            "org_holding": PARTITION_ORG,
            "stk_holdertrade": PARTITION_STK,
        },
    )
    assert shadow.cutover_allowed is True

    out = tmp_path / "disclosure_dataset_snapshot_bounded.json"
    snap = freeze_disclosure_dataset_snapshot(
        {"holders_top10": conn, "org_holding": conn, "stk_holdertrade": conn},
        shadow=shadow,
        path=out,
        partition_sets={
            "holders_top10": [PARTITION_HOLDERS],
            "org_holding": [PARTITION_ORG],
            "stk_holdertrade": [PARTITION_STK, "20190103"],
        },
        extra_notes=("test_bounded",),
    )
    assert snap.scope == "bounded_accepted_partitions"
    assert snap.phase_e_ablation == "bounded_scope_measured_b0_short_window"
    assert snap.domains["stk_holdertrade"]["date_set"] == sorted(
        [PARTITION_STK, "20190103"]
    )
    assert len(snap.domains["stk_holdertrade"]["accepted"]) == 2
    assert "test_bounded" in snap.notes
