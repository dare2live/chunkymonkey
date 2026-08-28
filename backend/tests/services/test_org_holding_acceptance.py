"""E0 tracer: org_holding land→validate→accept (fixture/memory; no mass fetch)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from services.data_sources.disclosure_boundaries import (
    DisclosureBoundaryError,
    authorize_nonconforming_direct_write,
    disclosure_inventory,
    refuse_accepted_publication_claim,
)
from services.data_sources.formal_execution import (
    FormalExecutionHandoffError,
    propagate_disclosure_execution_contract,
)
from services.data_sources.org_holding_acceptance import (
    OrgHoldingAcceptanceError,
    OrgHoldingLandingBatch,
    accept_org_holding_batch,
    land_org_holding_batch,
    publish_accepted_org_holding_partition,
)
from services.data_sources.org_holding_contract import load_org_holding_contract
from services.data_sources.org_holding_schema import (
    ACCEPTED_TABLE,
    CANONICAL_TABLE,
    CONTRACT_VERSION,
    DATASET_ID,
    INGEST_BATCH_TABLE,
    LANDING_TABLE,
    SOURCE,
)
from services.duck_adapter import connect

# Q1 2026 report → statutory available_date 2026-04-30
PARTITION = "20260430"
REPORT = "20260331"
OBSERVED = datetime(2026, 4, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)


def _row(**overrides):
    base = {
        "report_date": REPORT,
        "available_date": PARTITION,
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


def _batch(batch_id: str, rows, *, available_at=OBSERVED, observed_at=OBSERVED):
    return OrgHoldingLandingBatch(
        batch_id=batch_id,
        partition_value=PARTITION,
        observed_at=observed_at,
        available_at=available_at,
        rows=rows,
        request={"api": "RPT_MAIN_ORGHOLDDETAIL", "available_date": PARTITION},
        source=SOURCE,
        contract_version=CONTRACT_VERSION,
    )


@pytest.fixture
def conn():
    database = connect(":memory:")
    yield database
    database.close()


def test_inventory_declares_org_holding_formal_writers_strangler() -> None:
    inventory = {item["domain"]: item for item in disclosure_inventory()}
    item = inventory["org_holding"]
    assert item["landing_writer"] is not None
    assert item["canonical_writer"] is not None
    assert item["runtime_state"] == "formal_only"
    assert item["conformity"] == "NONCONFORMING"
    permit = authorize_nonconforming_direct_write(
        "org_holding",
        conformity="NONCONFORMING",
        allow_test_escape=True,
    )
    assert permit.publication == "nonconforming_direct_write"
    with pytest.raises(DisclosureBoundaryError, match="dataset_snapshot"):
        refuse_accepted_publication_claim("org_holding", "DatasetSnapshot")


def test_land_without_execution_handoff_fails_closed(conn) -> None:
    contract = load_org_holding_contract()
    with pytest.raises(OrgHoldingAcceptanceError, match="execution_handoff"):
        land_org_holding_batch(conn, _batch("org_holding:no-handoff", [_row()]), contract)


def test_missing_available_at_fails_closed(conn) -> None:
    contract = load_org_holding_contract()
    handed = propagate_disclosure_execution_contract("org_holding", contract)
    with pytest.raises(OrgHoldingAcceptanceError, match="available_at"):
        land_org_holding_batch(
            conn,
            _batch("org_holding:missing-avail", [_row()], available_at=None),
            handed,
            handoff=handed,
        )


def test_forged_available_at_before_available_date_fails_closed(conn) -> None:
    contract = load_org_holding_contract()
    handed = propagate_disclosure_execution_contract("org_holding", contract)
    forged = datetime(2026, 4, 29, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    batch = _batch(
        "org_holding:forged", [_row()], available_at=forged, observed_at=forged
    )
    land_org_holding_batch(conn, batch, handed, handoff=handed)
    outcome = accept_org_holding_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "FORGED_AVAILABLE_AT"


def test_available_date_before_report_period_fails_closed(conn) -> None:
    contract = load_org_holding_contract()
    handed = propagate_disclosure_execution_contract("org_holding", contract)
    bad = _row(available_date=PARTITION, report_date="20260630")
    batch = _batch("org_holding:before-report", [bad])
    land_org_holding_batch(conn, batch, handed, handoff=handed)
    outcome = accept_org_holding_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "AVAILABLE_BEFORE_REPORT"


def test_announcement_before_statutory_deadline_accepts(conn) -> None:
    """Q1 filed 4/15: known-at is 20260415, not 20260430."""
    contract = load_org_holding_contract()
    handed = propagate_disclosure_execution_contract("org_holding", contract)
    early = "20260415"
    observed = datetime(2026, 4, 15, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    batch = OrgHoldingLandingBatch(
        batch_id="org_holding:early",
        partition_value=early,
        observed_at=observed,
        available_at=observed,
        rows=[_row(available_date=early)],
        request={"api": "RPT_MAIN_ORGHOLDDETAIL", "available_date": early},
        source=SOURCE,
        contract_version=CONTRACT_VERSION,
    )
    outcome = publish_accepted_org_holding_partition(conn, batch, handed)
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == 1
    stored = conn.execute(
        f"SELECT available_date FROM {CANONICAL_TABLE}"
    ).fetchone()[0]
    assert str(stored).replace("-", "")[:8] == early


def test_publish_land_accept_roundtrip_fixture(conn) -> None:
    contract = load_org_holding_contract()
    rows = [
        _row(holder_code="10010626", holder_name="香港中央结算有限公司"),
        _row(holder_code="10020001", holder_name="某公募基金"),
    ]
    outcome = publish_accepted_org_holding_partition(
        conn,
        _batch("org_holding:ok", rows),
        contract,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == 2
    landed = conn.execute(
        f"SELECT COUNT(*) FROM {LANDING_TABLE} WHERE batch_id = ?",
        [outcome.batch_id],
    ).fetchone()[0]
    assert landed == 2
    canonical = conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0]
    assert canonical == 2
    pointer = conn.execute(
        f"""
        SELECT dataset_id, partition_value, row_count
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ?
        """,
        [DATASET_ID],
    ).fetchone()
    assert tuple(pointer) == (DATASET_ID, PARTITION, 2)
    status = conn.execute(
        f"SELECT status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?",
        [outcome.batch_id],
    ).fetchone()[0]
    assert status == "ACCEPTED"


def test_accept_merge_preserves_sibling_report_date_in_shared_partition(conn) -> None:
    """2018-12-31 and 2019-03-31 share available_date=20190430 — merge by report_date."""
    contract = load_org_holding_contract()
    handed = propagate_disclosure_execution_contract("org_holding", contract)
    shared = "20190430"
    observed = datetime(2019, 4, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    q1 = _row(
        report_date="20190331",
        available_date=shared,
        holder_code="10010626",
    )
    batch_q1 = OrgHoldingLandingBatch(
        batch_id="org_holding:q1",
        partition_value=shared,
        observed_at=observed,
        available_at=observed,
        rows=[q1],
        request={"api": "RPT_MAIN_ORGHOLDDETAIL", "available_date": shared},
        source=SOURCE,
        contract_version=CONTRACT_VERSION,
    )
    out1 = publish_accepted_org_holding_partition(conn, batch_q1, handed)
    assert out1.status == "ACCEPTED"
    annual = _row(
        report_date="20181231",
        available_date=shared,
        holder_code="10020001",
        holder_name="年报机构",
    )
    batch_annual = OrgHoldingLandingBatch(
        batch_id="org_holding:annual",
        partition_value=shared,
        observed_at=observed,
        available_at=observed,
        rows=[annual],
        request={"api": "RPT_MAIN_ORGHOLDDETAIL", "available_date": shared},
        source=SOURCE,
        contract_version=CONTRACT_VERSION,
    )
    out2 = publish_accepted_org_holding_partition(conn, batch_annual, handed)
    assert out2.status == "ACCEPTED"
    rows = conn.execute(
        f"SELECT report_date, holder_code FROM {CANONICAL_TABLE} ORDER BY 1, 2"
    ).fetchall()
    assert len(rows) == 2
    assert {str(r[0]) for r in rows} == {"20181231", "20190331"}
    # Class-A fix: accepted pointer must describe the merged partition, not last batch.
    pointer = conn.execute(
        f"""
        SELECT row_count, content_hash
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ? AND partition_value = ?
        """,
        [DATASET_ID, shared],
    ).fetchone()
    assert pointer is not None
    assert int(pointer[0]) == 2
    batch_q1_hash = conn.execute(
        f"SELECT canonical_hash, canonical_row_count FROM {INGEST_BATCH_TABLE} "
        f"WHERE batch_id = ?",
        ["org_holding:q1"],
    ).fetchone()
    batch_annual_hash = conn.execute(
        f"SELECT canonical_hash, canonical_row_count FROM {INGEST_BATCH_TABLE} "
        f"WHERE batch_id = ?",
        ["org_holding:annual"],
    ).fetchone()
    assert int(batch_q1_hash[1]) == 1
    assert int(batch_annual_hash[1]) == 1
    assert str(pointer[1]) != str(batch_annual_hash[0])
    assert str(pointer[1]) != str(batch_q1_hash[0])

    # Retrying an older accepted batch remains idempotent after a sibling
    # advances the partition-scoped pointer. The outcome is batch-scoped.
    retried = accept_org_holding_batch(conn, batch_q1.batch_id, handed, handoff=handed)
    assert retried.status == "ACCEPTED"
    assert retried.row_count == 1
    assert retried.content_hash == str(batch_q1_hash[0])


def test_disclosure_handoff_rejects_wrong_contract_type() -> None:
    from services.data_sources.holders_top10_contract import load_holders_top10_contract

    contract = load_holders_top10_contract()
    with pytest.raises(
        FormalExecutionHandoffError, match="OrgHoldingContract|mismatched"
    ):
        propagate_disclosure_execution_contract("org_holding", contract)


def _provider_row(**overrides):
    base = {
        "report_date": REPORT,
        "available_date": PARTITION,
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


def _insert_raw(conn, **overrides) -> None:
    from services.org_holding_aif10 import ensure_tables

    ensure_tables(conn)
    row = {
        "report_date": "2026-03-31",
        "available_date": "2026-04-30",
        "stock_code": "600519",
        "holder_code": "10010626",
        "fund_derivecode": "",
        "holder_name": "香港中央结算有限公司",
        "org_type_name": "QFII",
        "total_shares": 1.2e8,
        "free_shares_ratio": 7.12,
    }
    row.update(overrides)
    conn.execute(
        """
        INSERT INTO raw_org_holding_aif10
            (report_date, available_date, stock_code, holder_code, fund_derivecode,
             holder_name, org_type_name, total_shares, free_shares_ratio)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["report_date"],
            row["available_date"],
            row["stock_code"],
            row["holder_code"],
            row["fund_derivecode"],
            row["holder_name"],
            row["org_type_name"],
            row["total_shares"],
            row["free_shares_ratio"],
        ],
    )
    try:
        conn.commit()
    except Exception:  # noqa: BLE001
        pass


def test_announcement_reaccept_moves_canonical_off_statutory_deadline(
    conn, monkeypatch
) -> None:
    """RED: statutory 20260430 + announcement 20260422 → canonical is 20260422."""
    from services.data_sources.disclosure_dual_write import (
        write_org_holding_formal_then_mirror,
    )
    from services.org_holding_aif10 import reaccept_org_holding_period_announced

    def _boom_land(*_a, **_k):
        raise AssertionError("historical reaccept must not use land_date=today")

    def _boom_resolve(*_a, **_k):
        raise AssertionError("historical reaccept must not call resolve_available_iso")

    monkeypatch.setattr(
        "services.data_sources.org_holding_announcement.land_calendar_date",
        _boom_land,
    )
    monkeypatch.setattr(
        "services.data_sources.org_holding_announcement.resolve_available_iso",
        _boom_resolve,
    )

    _insert_raw(conn)
    _insert_raw(
        conn,
        report_date="2025-12-31",
        available_date="2026-04-30",
        stock_code="600519",
        holder_code="10010626",
        holder_name="年报同grain另一期",
    )
    write_org_holding_formal_then_mirror(
        conn,
        [
            _provider_row(),
            _provider_row(report_date="20251231", available_date=PARTITION),
        ],
        observed_at=OBSERVED,
        available_at=OBSERVED,
    )
    before = conn.execute(
        f"""
        SELECT available_date FROM {CANONICAL_TABLE}
         WHERE stock_code = '600519'
           AND replace(CAST(report_date AS VARCHAR), '-', '') = '{REPORT}'
        """
    ).fetchone()[0]
    assert str(before).replace("-", "")[:8] == PARTITION

    out = reaccept_org_holding_period_announced(
        conn,
        "20260331",
        announcement_by_stock={"600519": "20260422"},
        dry_run=False,
    )
    assert out["status"] == "accepted"
    assert out["with_announcement"] == 1
    assert out["skipped_no_announcement"] == 0
    stored = conn.execute(
        f"""
        SELECT available_date FROM {CANONICAL_TABLE}
         WHERE stock_code = '600519' AND holder_code = '10010626'
           AND replace(CAST(report_date AS VARCHAR), '-', '') = '{REPORT}'
        """
    ).fetchone()
    assert stored is not None
    assert str(stored[0]).replace("-", "")[:8] == "20260422"
    leftover = conn.execute(
        f"""
        SELECT COUNT(*) FROM {CANONICAL_TABLE}
         WHERE replace(CAST(available_date AS VARCHAR), '-', '') = ?
           AND replace(CAST(report_date AS VARCHAR), '-', '') = ?
        """,
        [PARTITION, REPORT],
    ).fetchone()[0]
    assert leftover == 0
    sibling = conn.execute(
        f"""
        SELECT available_date FROM {CANONICAL_TABLE}
         WHERE replace(CAST(report_date AS VARCHAR), '-', '') = '20251231'
        """
    ).fetchone()
    assert sibling is not None
    assert str(sibling[0]).replace("-", "")[:8] == PARTITION
    raw_avail = conn.execute(
        """
        SELECT available_date FROM raw_org_holding_aif10
         WHERE stock_code = '600519'
           AND replace(CAST(report_date AS VARCHAR), '-', '') = '20260331'
        """
    ).fetchone()[0]
    assert str(raw_avail).replace("-", "")[:8] == "20260422"


def test_announcement_reaccept_drops_stock_without_announcement(conn) -> None:
    from services.data_sources.disclosure_dual_write import (
        write_org_holding_formal_then_mirror,
    )
    from services.org_holding_aif10 import reaccept_org_holding_period_announced

    _insert_raw(conn)
    _insert_raw(
        conn,
        stock_code="000001",
        holder_code="20000001",
        holder_name="无公告机构",
    )
    write_org_holding_formal_then_mirror(
        conn,
        [
            _provider_row(),
            _provider_row(stock_code="000001", holder_code="20000001"),
        ],
        observed_at=OBSERVED,
        available_at=OBSERVED,
    )
    out = reaccept_org_holding_period_announced(
        conn,
        "20260331",
        announcement_by_stock={"600519": "20260422"},
        dry_run=False,
    )
    assert out["skipped_no_announcement"] == 1
    codes = {
        str(row[0])
        for row in conn.execute(
            f"SELECT stock_code FROM {CANONICAL_TABLE}"
        ).fetchall()
    }
    assert codes == {"600519"}


def test_announcement_reaccept_missing_period_from_raw_only(conn) -> None:
    """Raw has 20260630, canonical does not → execute accepts announced grains only."""
    from services.org_holding_aif10 import reaccept_org_holding_period_announced

    _insert_raw(
        conn,
        report_date="2026-06-30",
        available_date="2026-08-31",
        stock_code="600519",
    )
    _insert_raw(
        conn,
        report_date="2026-06-30",
        available_date="2026-08-31",
        stock_code="000001",
        holder_code="20000001",
        holder_name="无公告机构",
    )
    try:
        n = conn.execute(
            f"""
            SELECT COUNT(*) FROM {CANONICAL_TABLE}
             WHERE replace(CAST(report_date AS VARCHAR), '-', '') = '20260630'
            """
        ).fetchone()[0]
    except Exception:  # noqa: BLE001 — table may not exist yet
        n = 0
    assert int(n or 0) == 0

    out = reaccept_org_holding_period_announced(
        conn,
        "20260630",
        announcement_by_stock={"600519": "20260820"},
        dry_run=False,
    )
    assert out["status"] == "accepted"
    assert out["with_announcement"] == 1
    assert out["skipped_no_announcement"] == 1
    rows = conn.execute(
        f"""
        SELECT stock_code, available_date FROM {CANONICAL_TABLE}
         WHERE replace(CAST(report_date AS VARCHAR), '-', '') = '20260630'
        """
    ).fetchall()
    assert len(rows) == 1
    assert str(rows[0][0]) == "600519"
    assert str(rows[0][1]).replace("-", "")[:8] == "20260820"


def test_announcement_reaccept_empty_map_does_not_wipe_or_stamp_today(conn) -> None:
    from services.data_sources.disclosure_dual_write import (
        write_org_holding_formal_then_mirror,
    )
    from services.org_holding_aif10 import reaccept_org_holding_period_announced

    _insert_raw(conn)
    write_org_holding_formal_then_mirror(
        conn, [_provider_row()], observed_at=OBSERVED, available_at=OBSERVED
    )
    out = reaccept_org_holding_period_announced(
        conn, "20260331", announcement_by_stock={}, dry_run=False
    )
    assert out["status"] == "blocked_empty_announcement_map"
    stored = conn.execute(
        f"SELECT available_date FROM {CANONICAL_TABLE} WHERE stock_code = '600519'"
    ).fetchone()[0]
    assert str(stored).replace("-", "")[:8] == PARTITION


def test_announcement_reaccept_unmatched_map_does_not_wipe_canonical(conn) -> None:
    """Non-empty map with no matching stock must not DELETE the period."""
    from services.data_sources.disclosure_dual_write import (
        write_org_holding_formal_then_mirror,
    )
    from services.org_holding_aif10 import reaccept_org_holding_period_announced

    _insert_raw(conn)
    write_org_holding_formal_then_mirror(
        conn, [_provider_row()], observed_at=OBSERVED, available_at=OBSERVED
    )
    out = reaccept_org_holding_period_announced(
        conn,
        "20260331",
        announcement_by_stock={"999999": "20260422"},
        dry_run=False,
    )
    assert out["status"] == "skipped_no_announced_grains"
    assert out["with_announcement"] == 0
    stored = conn.execute(
        f"""
        SELECT available_date FROM {CANONICAL_TABLE}
         WHERE stock_code = '600519'
           AND replace(CAST(report_date AS VARCHAR), '-', '') = '{REPORT}'
        """
    ).fetchone()
    assert stored is not None
    assert str(stored[0]).replace("-", "")[:8] == PARTITION


def test_announcement_reaccept_restores_canonical_if_write_fails(
    conn, monkeypatch
) -> None:
    from services.data_sources.disclosure_dual_write import (
        write_org_holding_formal_then_mirror,
    )
    from services.org_holding_aif10 import reaccept_org_holding_period_announced

    _insert_raw(conn)
    write_org_holding_formal_then_mirror(
        conn, [_provider_row()], observed_at=OBSERVED, available_at=OBSERVED
    )
    before = conn.execute(
        f"""
        SELECT COUNT(*) FROM {CANONICAL_TABLE}
         WHERE replace(CAST(report_date AS VARCHAR), '-', '') = '{REPORT}'
        """
    ).fetchone()[0]
    assert int(before) == 1

    def _boom(*_a, **_k):
        raise RuntimeError("forced write failure after delete would have run")

    monkeypatch.setattr(
        "services.data_sources.disclosure_dual_write.write_org_holding_formal_then_mirror",
        _boom,
    )
    out = reaccept_org_holding_period_announced(
        conn,
        "20260331",
        announcement_by_stock={"600519": "20260422"},
        dry_run=False,
    )
    assert out["status"] == "accept_failed"
    after = conn.execute(
        f"""
        SELECT COUNT(*) FROM {CANONICAL_TABLE}
         WHERE replace(CAST(report_date AS VARCHAR), '-', '') = '{REPORT}'
        """
    ).fetchone()[0]
    assert int(after) == int(before)
    stored = conn.execute(
        f"""
        SELECT available_date FROM {CANONICAL_TABLE}
         WHERE stock_code = '600519'
           AND replace(CAST(report_date AS VARCHAR), '-', '') = '{REPORT}'
        """
    ).fetchone()[0]
    assert str(stored).replace("-", "")[:8] == PARTITION


def test_institution_profile_attach_sources_does_not_attach_org_holding() -> None:
    """Deferral stays honest: _attach_sources still does not ATTACH org_holding."""
    import ast
    import inspect

    from services import institution_profile as ip

    src = inspect.getsource(ip._attach_sources)
    tree = ast.parse(src)
    aliases: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_db":
            if node.args and isinstance(node.args[0], ast.Constant):
                aliases.append(str(node.args[0].value))
    assert "org_holding" not in aliases
    assert set(aliases) == {"smartmoney", "market", "tushare_raw"}


def test_repair_script_refuses_execute_combined_with_dry_run() -> None:
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "repair_org_holding_announcement_reaccept.py"
    )
    spec = importlib.util.spec_from_file_location("repair_org_reaccept", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    assert mod.main(["--execute", "--dry-run"]) == 2
    with pytest.raises(FileNotFoundError, match="--db"):
        mod._resolve_db_file(
            "org_holding",
            override="/tmp/chunkymonkey-missing-org-holding.duckdb",
        )
    source = path.read_text(encoding="utf-8")
    assert "/Users/dp/Documents/M/stock/chunkymonkey/data" not in source
    assert "_LIVE_DATA" not in source
