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
    assert "def land_disclosure_partition_from_legacy" in transport_src


def _seed_holders_legacy(conn, row: dict) -> None:
    from services.holders_aif10 import _write_legacy_direct

    legacy = {
        "stock_code": row["stock_code"],
        "stock_name": "",
        "market": "",
        "report_date": row["report_date"],
        "holder_set": row["holder_set"],
        "holder_rank": row["holder_rank"],
        "row_seq": row["row_seq"],
        "holder_name": row["holder_name"],
        "holder_name_norm": row.get("holder_name_norm") or row["holder_name"],
        "share_class": row.get("share_class"),
        "is_secondary_class": False,
        "is_exit_row": row.get("is_exit_row", False),
        "shares_text": None,
        "shares_approx": row.get("shares_approx"),
        "shares_precision": None,
        "hold_amount": None,
        "hold_ratio_float": row.get("hold_ratio_float"),
        "hold_ratio_total": None,
        "hold_ratio": row.get("hold_ratio_float"),
        "hold_market_cap": None,
        "holder_type": row.get("holder_type"),
        "share_nature": None,
        "change_status": row.get("change_status"),
        "change_shares_text": None,
        "change_shares_approx": None,
        "hold_change": "",
        "hold_change_num": row.get("hold_change_num"),
        "notice_date": row["notice_date"],
        "effective_date": None,
        "page_update_date": row["notice_date"],
        "availability_source": "page_update_date",
        "source": "miaoxiang",
        "source_tier": 1,
        "raw_hash": None,
        "fetched_at": OBSERVED_HOLDERS.isoformat(),
        "created_at": OBSERVED_HOLDERS.isoformat(),
    }
    _write_legacy_direct(conn, [legacy], as_mirror=False)


def test_assign_unique_holders_row_seq_breaks_rank_collisions() -> None:
    from services.data_sources.holders_top10_schema import (
        GRAIN,
        assign_unique_holders_row_seq,
    )

    rows = [
        {
            "stock_code": "301059",
            "report_date": "20260702",
            "holder_set": "free",
            "holder_rank": 3,
            "row_seq": 1,
            "is_exit_row": False,
            "holder_name": "乙",
            "hold_ratio_float": 1.0,
            "notice_date": "20260706",
        },
        {
            "stock_code": "301059",
            "report_date": "20260702",
            "holder_set": "free",
            "holder_rank": 3,
            "row_seq": 1,
            "is_exit_row": False,
            "holder_name": "甲",
            "hold_ratio_float": 2.0,
            "notice_date": "20260706",
        },
    ]
    fixed = assign_unique_holders_row_seq(rows)
    keys = [tuple(r[k] for k in GRAIN) for r in fixed]
    assert len(keys) == len(set(keys))
    by_name = {r["holder_name"]: r["row_seq"] for r in fixed}
    # Stable sort by holder_name (Unicode); 乙 < 甲
    assert by_name["乙"] == 1
    assert by_name["甲"] == 2


def test_land_then_accept_after_row_seq_assign_accepts_rank_collisions(
    conn,
) -> None:
    """Renumber row_seq before land→accept so same-rank holder_names accept."""

    from services.data_sources.disclosure_transport import (
        land_then_accept_disclosure_partition,
    )
    from services.data_sources.holders_top10_schema import (
        assign_unique_holders_row_seq,
    )

    base = _holders_row()
    twin = _holders_row(
        holder_name="同名冲突乙",
        holder_name_norm="同名冲突乙",
        hold_ratio_float=0.5,
        row_seq=1,
    )
    fixed = assign_unique_holders_row_seq([base, twin])
    outcome = land_then_accept_disclosure_partition(
        "holders_top10",
        conn,
        partition=PARTITION_HOLDERS,
        rows=fixed,
        observed_at=OBSERVED_HOLDERS,
        available_at=OBSERVED_HOLDERS,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == 2
    seqs = {
        r[0]: r[1]
        for r in conn.execute(
            f"""
            SELECT holder_name, row_seq FROM {HOLDERS_CANONICAL}
             WHERE notice_date = ?
            """,
            [PARTITION_HOLDERS],
        ).fetchall()
    }
    assert len(seqs) == 2
    assert seqs[base["holder_name"]] != seqs[twin["holder_name"]]


def test_land_from_legacy_holders_does_not_write_canonical(conn) -> None:
    """S1 from local legacy: landing only; no canonical / accepted_partition."""

    from services.data_sources.disclosure_transport import (
        land_disclosure_partition_from_legacy,
    )

    _seed_holders_legacy(conn, _holders_row())
    batch = land_disclosure_partition_from_legacy(
        "holders_top10",
        conn,
        partition=PARTITION_HOLDERS,
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


def test_land_from_legacy_empty_partition_fails_closed(conn) -> None:
    from services.data_sources.disclosure_transport import (
        DisclosureTransportError,
        land_disclosure_partition_from_legacy,
    )

    with pytest.raises(DisclosureTransportError, match="no legacy"):
        land_disclosure_partition_from_legacy(
            "holders_top10",
            conn,
            partition=PARTITION_HOLDERS,
            observed_at=OBSERVED_HOLDERS,
        )


def test_sync_runner_disclosure_land_only_org_requires_from_local_raw() -> None:
    """org_holding provider land BLOCKED (by-period mass; no by-date faucet)."""

    from argparse import Namespace

    from services.data_sources import sync_runner as sr
    from services.data_sources.availability import SyncWindowError

    args = Namespace(
        land_only=True,
        accept_from_landing=False,
        land_then_accept=False,
        from_local_raw=False,
        drain=False,
        backfill=False,
        resume=False,
        all_due=False,
        domain="org_holding",
        batch_id=None,
        start="20260715",
        end="20260715",
        max_dates=None,
        trigger_mode="manual",
    )
    with pytest.raises(SyncWindowError, match="from-local-raw"):
        sr._preflight_disclosure_cli_shape(args, transport="land_only")


def test_sync_runner_disclosure_land_only_holders_allows_provider() -> None:
    """holders_top10 provider land-only preflight (by_notice_date; no mass dump)."""

    from argparse import Namespace
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from services.data_sources import sync_runner as sr

    args = Namespace(
        land_only=True,
        accept_from_landing=False,
        land_then_accept=False,
        from_local_raw=False,
        drain=False,
        backfill=False,
        resume=False,
        all_due=False,
        domain="holders_top10",
        batch_id=None,
        start="20260715",
        end="20260715",
        max_dates=None,
        trigger_mode="manual",
    )
    sr._preflight_disclosure_cli_shape(
        args,
        transport="land_only",
        now=datetime(2026, 7, 20, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def test_sync_runner_disclosure_land_only_stk_allows_provider() -> None:
    """stk_holdertrade provider land-only preflight (by_ann_date; no mass dump)."""

    from argparse import Namespace
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from services.data_sources import sync_runner as sr

    args = Namespace(
        land_only=True,
        accept_from_landing=False,
        land_then_accept=False,
        from_local_raw=False,
        drain=False,
        backfill=False,
        resume=False,
        all_due=False,
        domain="stk_holdertrade",
        batch_id=None,
        start="20260715",
        end="20260715",
        max_dates=None,
        trigger_mode="manual",
    )
    sr._preflight_disclosure_cli_shape(
        args,
        transport="land_only",
        now=datetime(2026, 7, 20, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def test_land_from_provider_stk_does_not_write_canonical(conn) -> None:
    """S1 provider land: landing only; inject fetch; no canonical."""

    from services.data_sources.disclosure_transport import (
        land_disclosure_partition_from_provider,
    )

    row = _stk_row()
    batch = land_disclosure_partition_from_provider(
        "stk_holdertrade",
        conn,
        partition=PARTITION_STK,
        observed_at=OBSERVED_STK,
        fetch_rows=lambda _domain, _part: [row],
    )
    assert batch.batch_id.startswith(f"stk_holdertrade:{PARTITION_STK}:")
    status = conn.execute(
        "SELECT status FROM ingest_batch WHERE batch_id = ?",
        [batch.batch_id],
    ).fetchone()[0]
    assert status == "LANDED"
    assert (
        conn.execute(
            f"SELECT COUNT(*) FROM {STK_CANONICAL} WHERE ann_date = ?",
            [PARTITION_STK],
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM accepted_partition WHERE dataset_id = ?",
            [STK_DATASET],
        ).fetchone()[0]
        == 0
    )


def test_land_from_provider_holders_does_not_write_canonical(conn) -> None:
    """S1 holders provider land: landing only; inject fetch; no canonical."""

    from services.data_sources.disclosure_transport import (
        land_disclosure_partition_from_provider,
    )

    row = _holders_row()
    batch = land_disclosure_partition_from_provider(
        "holders_top10",
        conn,
        partition=PARTITION_HOLDERS,
        observed_at=OBSERVED_HOLDERS,
        fetch_rows=lambda _domain, _part: [row],
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


def test_land_from_provider_org_fails_without_inject(conn) -> None:
    """org_holding has no safe by-date provider land (by-period ~830k mass)."""

    from services.data_sources.disclosure_transport import (
        DisclosureTransportError,
        land_disclosure_partition_from_provider,
    )

    with pytest.raises(DisclosureTransportError, match="from-local-raw|by-date|period"):
        land_disclosure_partition_from_provider(
            "org_holding",
            conn,
            partition=PARTITION_ORG,
            observed_at=OBSERVED_ORG,
        )


def test_sync_runner_disclosure_provider_land_stop_on_first_fail(monkeypatch) -> None:
    """Provider land-only stops on first failed partition (≤40d window)."""

    from argparse import Namespace
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from services.data_sources import sync_runner as sr

    calls: list[str] = []

    def fake_land(domain, *, partition):
        calls.append(partition)
        if partition == "20260715":
            return {
                "domain": domain,
                "status": "error",
                "batches": 0,
                "rows": 0,
                "failed_batches": 1,
                "error": "no provider rows",
                "partition_value": partition,
                "publication": "land_only_disclosure_from_provider",
                "transport": "land_only",
                "acquire_mode": "provider",
            }
        return {
            "domain": domain,
            "status": "ok",
            "batches": 1,
            "rows": 1,
            "failed_batches": 0,
            "batch_id": f"stk_holdertrade:{partition}:test",
            "partition_value": partition,
            "publication": "land_only_disclosure_from_provider",
            "transport": "land_only",
            "acquire_mode": "provider",
        }

    monkeypatch.setattr(
        sr, "land_disclosure_partition_from_provider_batch", fake_land
    )
    args = Namespace(
        land_only=True,
        accept_from_landing=False,
        land_then_accept=False,
        from_local_raw=False,
        drain=False,
        backfill=False,
        resume=False,
        all_due=False,
        domain="stk_holdertrade",
        batch_id=None,
        start="20260714",
        end="20260716",
        max_dates=None,
        trigger_mode="manual",
    )
    sr._preflight_disclosure_cli_shape(
        args,
        transport="land_only",
        now=datetime(2026, 7, 20, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    result = sr._run_disclosure_transport_window(
        "stk_holdertrade",
        transport="land_only",
        start="20260714",
        end="20260716",
        from_local_raw=False,
    )
    assert calls == ["20260714", "20260715"]
    assert int(result.get("failed_batches") or 0) >= 1
    assert result.get("window_days_attempted") == 2


def test_sync_runner_disclosure_land_only_cli_allows_domains() -> None:
    from argparse import Namespace
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from services.data_sources import sync_runner as sr

    args = Namespace(
        land_only=True,
        accept_from_landing=False,
        land_then_accept=False,
        from_local_raw=True,
        drain=False,
        backfill=False,
        resume=False,
        all_due=False,
        domain="stk_holdertrade",
        batch_id=None,
        start="20260715",
        end="20260715",
        max_dates=None,
        trigger_mode="manual",
    )
    assert sr._cli_transport_mode(args) == "land_only"
    sr._preflight_disclosure_cli_shape(
        args,
        transport="land_only",
        now=datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    with pytest.raises(sr.SyncWindowError, match="40 calendar"):
        sr._disclosure_partition_dates("20260101", "20260301")


def test_disclosure_cli_rejects_future_window_before_writer_lock(
    monkeypatch, capsys
) -> None:
    """Tier0: future disclosure land-only must not take writer lock / DB I/O."""

    import sys

    import services.writer_lock as writer_lock_module
    from services.data_sources import sync_runner as sr

    monkeypatch.setattr(
        writer_lock_module,
        "writer_lock",
        lambda *_a, **_k: pytest.fail("future disclosure acquired writer lock"),
    )
    monkeypatch.setattr(
        sr,
        "land_disclosure_partition_from_legacy_batch",
        lambda *_a, **_k: pytest.fail("future disclosure reached land helper"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sync_runner.py",
            "--domain",
            "holders_top10",
            "--land-only",
            "--from-local-raw",
            "--start",
            "20990101",
            "--end",
            "20990101",
        ],
    )
    rc = sr.main()
    captured = capsys.readouterr().out
    assert rc == 1
    assert "writer_lock" not in captured
    assert "20990101" in captured or "future" in captured.lower() or "after" in captured


def test_disclosure_window_stops_on_first_failure(monkeypatch) -> None:
    """Multi-day disclosure must not crawl past the first real failed day."""

    from services.data_sources import sync_runner as sr

    calls: list[str] = []

    def fake_land(domain, *, partition):
        calls.append(partition)
        if partition == "20260716":
            return {
                "domain": domain,
                "status": "error",
                "batches": 0,
                "rows": 0,
                "failed_batches": 1,
                "error": "writer lock busy",
                "partition_value": partition,
                "publication": "land_only_disclosure_from_local_raw",
                "transport": "land_only",
            }
        return {
            "domain": domain,
            "status": "ok",
            "batches": 1,
            "rows": 1,
            "failed_batches": 0,
            "batch_id": f"{domain}:{partition}:x",
            "partition_value": partition,
            "publication": "land_only_disclosure_from_local_raw",
            "transport": "land_only",
        }

    monkeypatch.setattr(sr, "land_disclosure_partition_from_legacy_batch", fake_land)
    result = sr._run_disclosure_transport_window(
        "holders_top10",
        transport="land_only",
        start="20260715",
        end="20260717",
        from_local_raw=True,
    )
    assert calls == ["20260715", "20260716"]  # stopped; never 20260717
    assert result["failed_batches"] == 1
    assert result["window_days_attempted"] == 2
    assert len(result["day_results"]) == 2


def test_disclosure_local_raw_empty_partition_skips_and_continues(
    monkeypatch,
) -> None:
    """Local-raw: 0 legacy rows = typed empty day, not hard-stop (no mass invent).

    Provider empty still fails closed elsewhere; this unlocks ≤40d window
    broaden across weekends / no-announcement calendar days.
    """

    from services.data_sources import sync_runner as sr

    calls: list[str] = []
    accepts: list[str] = []

    def fake_land(domain, *, partition):
        calls.append(partition)
        if partition == "20260716":
            return {
                "domain": domain,
                "status": "empty_skip",
                "batches": 0,
                "rows": 0,
                "failed_batches": 0,
                "error": "no legacy rows",
                "partition_value": partition,
                "publication": "land_only_disclosure_from_local_raw",
                "transport": "land_only",
                "acquire_mode": "local_legacy_raw_materialize",
            }
        return {
            "domain": domain,
            "status": "ok",
            "batches": 1,
            "rows": 3,
            "failed_batches": 0,
            "batch_id": f"{domain}:{partition}:x",
            "partition_value": partition,
            "publication": "land_only_disclosure_from_local_raw",
            "transport": "land_only",
            "acquire_mode": "local_legacy_raw_materialize",
        }

    def fake_accept(domain, *, batch_id):
        accepts.append(batch_id)
        return {
            "domain": domain,
            "status": "ok",
            "batches": 1,
            "rows": 3,
            "failed_batches": 0,
            "batch_id": batch_id,
            "partition_value": batch_id.split(":")[1],
            "publication": "accept_from_landing",
            "transport": "accept_from_landing",
        }

    monkeypatch.setattr(sr, "land_disclosure_partition_from_legacy_batch", fake_land)
    monkeypatch.setattr(sr, "accept_disclosure_from_landing_batch", fake_accept)
    result = sr._run_disclosure_transport_window(
        "stk_holdertrade",
        transport="land_then_accept",
        start="20260715",
        end="20260717",
        from_local_raw=True,
    )
    assert calls == ["20260715", "20260716", "20260717"]
    assert accepts == [
        "stk_holdertrade:20260715:x",
        "stk_holdertrade:20260717:x",
    ]
    assert result["status"] == "ok"
    assert result["failed_batches"] == 0
    assert result["batches"] == 2
    assert result["empty_skips"] == 1
    assert result["rows"] == 6


def test_land_from_legacy_empty_returns_typed_empty_skip(
    conn, monkeypatch
) -> None:
    """CLI local-raw land of empty partition → empty_skip (not failed_batches)."""

    from services.data_sources import sync_runner as sr

    class _Manifest:
        def path_for(self, _alias: str) -> str:
            return ":memory:"

    monkeypatch.setattr(
        "services.database_manifest.get_database_manifest", lambda: _Manifest()
    )
    monkeypatch.setattr(
        "services.duck_adapter.connect", lambda *_a, **_k: conn
    )
    monkeypatch.setattr(conn, "close", lambda: None)

    result = sr.land_disclosure_partition_from_legacy_batch(
        "holders_top10",
        partition="20990101",
    )
    assert result["status"] == "empty_skip"
    assert result["failed_batches"] == 0
    assert result["rows"] == 0
    assert "no legacy" in str(result.get("error") or "").lower()


def test_sync_runner_land_disclosure_from_legacy_helper_zero_accept(
    conn, monkeypatch
) -> None:
    """CLI land helper must land only (composition point for chunkyctl)."""

    from services.data_sources import sync_runner as sr

    _seed_holders_legacy(conn, _holders_row())

    class _Manifest:
        def path_for(self, _alias: str) -> str:
            return ":memory:"

    monkeypatch.setattr(
        "services.database_manifest.get_database_manifest", lambda: _Manifest()
    )
    monkeypatch.setattr(
        "services.duck_adapter.connect", lambda *_a, **_k: conn
    )
    # sync helper closes the conn; keep the fixture usable.
    monkeypatch.setattr(conn, "close", lambda: None)

    result = sr.land_disclosure_partition_from_legacy_batch(
        "holders_top10",
        partition=PARTITION_HOLDERS,
    )
    assert result["status"] == "ok"
    assert result["transport"] == "land_only"
    assert result["publication"] == "land_only_disclosure_from_local_raw"
    assert result["failed_batches"] == 0
    assert (
        conn.execute(
            f"SELECT COUNT(*) FROM {HOLDERS_CANONICAL}",
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
