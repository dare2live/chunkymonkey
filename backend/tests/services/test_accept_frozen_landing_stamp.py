"""Frozen landing seal vs live contract restamp — target-behaviour red/green tests.

A landed batch row in ``ingest_batch`` carries ``contract_hash`` / ``config_hash`` /
``source_name`` stamped at landing time. Those three columns are part of a *frozen
evidence seal*: ``payload_hash`` is a sha256 over them plus the landed rows, computed
once at land time and never recomputed afterwards (``ingest_batch`` is "落地时刻的封印,
不应被后续同步触碰" — see ``stamp_checks.py`` check④). They are a historical fact about
what the writer believed the contract was *at the moment it landed*, not a live pointer.

``accepted_partition`` is different: it is a *pointer*, and it gets **restamped** with
the live contract's ``contract_hash``/``config_hash`` every time ``accept_*`` runs — and
did, wholesale, on 2026-09-01 when the contract fingerprint algorithm changed (source/api
stopped participating in ``config_hash``; see the 2026-09-01 comments in
``stock_st_contract.py`` / ``stk_holdertrade_contract.py`` / ``holders_top10_contract.py``).
After that change, a batch that landed under the *old* algorithm legitimately carries
``contract_hash``/``config_hash`` values that differ from what the *live* contract now
computes — that is expected, not corruption.

Accept-time code that rejects a LANDED batch because its frozen
``contract_hash``/``config_hash``/``source_name`` differ from the live
contract/handoff/pointer is therefore a bug: it strands leftover LANDED batches after
every restamp, forever, with no way to accept them short of re-landing (which may not
even be possible if the provider no longer serves the old partition). The production fix
(2026-09-02, alongside this test file) makes accept-time code stop comparing
``ingest_batch.contract_hash`` / ``config_hash`` / ``source_name`` against the live
contract; it keeps comparing ``contract_version`` and ``writer_id`` (those are declared
identities, not fingerprints, and a real drift there — e.g. a rogue writer, or a batch
landed against a contract version this build no longer knows how to validate — should
still fail closed). The pointer written at accept time still carries the *live*
contract's hashes, so downstream readers keep seeing a single current fingerprint per
accepted partition.

The positive tests in this file (``test_*_stale_landed_stamps_are_accepted``, and
``test_holders_skip_reland_matches_on_pointer_not_frozen_batch_stamp``) encode that
TARGET behaviour and were RED before the 2026-09-02 fix — this file was written and
first run against the pre-fix code specifically to capture that red evidence. The
negative-control tests (``test_stock_st_stale_contract_version_is_still_rejected``,
``test_stock_st_stale_writer_id_is_still_rejected``) assert the drift checks that must
keep failing closed, and pass both before and after the fix.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.formal_execution import propagate_disclosure_execution_contract
from services.data_sources.holders_top10_acceptance import (
    HoldersTop10AcceptanceError,
    HoldersTop10LandingBatch,
    accept_holders_top10_batch,
    land_holders_top10_batch,
)
from services.data_sources.holders_top10_contract import load_holders_top10_contract
from services.data_sources.holders_top10_schema import (
    DATASET_ID as HOLDERS_DATASET_ID,
    LANDING_TABLE as HOLDERS_LANDING_TABLE,
)
from services.data_sources.security_day_partition import SecurityDayLandingBatch
from services.data_sources.stamp_checks import _recompute_payload_hash_simple
from services.data_sources.stamp_types import DOMAIN_BY_DATASET_ID
from services.data_sources.stk_holdertrade_acceptance import (
    StkHoldertradeAcceptanceError,
    StkHoldertradeLandingBatch,
    accept_stk_holdertrade_batch,
    land_stk_holdertrade_batch,
)
from services.data_sources.stk_holdertrade_contract import load_stk_holdertrade_contract
from services.data_sources.stk_holdertrade_schema import (
    CONTRACT_VERSION as STK_CONTRACT_VERSION,
    DATASET_ID as STK_DATASET_ID,
    SOURCE as STK_SOURCE,
)
from services.data_sources.stock_st_acceptance import (
    accept_stock_st_batch,
    land_stock_st_batch,
)
from services.data_sources.stock_st_contract import load_stock_st_contract
from services.data_sources.stock_st_schema import DATASET_ID as ST_DATASET_ID
from services.duck_adapter import connect

_ST = json.loads(
    (
        Path(__file__).parents[1] / "fixtures" / "domain_samples" / "stock_st.json"
    ).read_text(encoding="utf-8")
)

# Same deterministic partitions/times already proven to accept cleanly in
# test_nominal_ohlcv_acceptance.py / test_holders_top10_acceptance.py /
# test_stk_holdertrade_acceptance.py — reused here (not re-derived) so this file's
# only variable is the frozen-vs-live stamp behaviour, not publication-time or
# forged-available-at edge cases those constants already dodge.
ST_PARTITION = "20220104"
ST_OBSERVED = datetime(2022, 1, 4, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)
HOLDERS_PARTITION = "20260429"
HOLDERS_OBSERVED = datetime(
    2026, 4, 29, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")
).astimezone(timezone.utc)
STK_PARTITION = "20190102"
STK_OBSERVED = datetime(2019, 1, 2, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)

STALE_CONTRACT_HASH = "1" * 64
STALE_CONFIG_HASH = "2" * 64
STALE_SOURCE_NAME = "retired_vendor"


@pytest.fixture
def conn():
    database = connect(":memory:")
    yield database
    database.close()


def _st_rows() -> list[dict]:
    rows = [dict(row) for row in _ST["rows"]]
    for row in rows:
        row["trade_date"] = ST_PARTITION
    return rows


def _holders_row(**overrides) -> dict:
    base = {
        "stock_code": "600519",
        "report_date": "20260331",
        "holder_set": "free",
        "holder_rank": 1,
        "row_seq": 1,
        "holder_name": "香港中央结算有限公司",
        "hold_ratio_float": 7.12,
        "notice_date": HOLDERS_PARTITION,
        "is_exit_row": False,
    }
    base.update(overrides)
    return base


def _stk_row(**overrides) -> dict:
    base = {
        "ts_code": "300010.SZ",
        "ann_date": STK_PARTITION,
        "holder_name": "窦昕",
        "holder_type": "P",
        "in_de": "IN",
        "change_vol": 10076031,
        "change_ratio": 1.5963,
        "after_share": 10076031,
        "after_ratio": 1.5963,
        "avg_price": 14.77,
        "total_share": 10076031,
    }
    base.update(overrides)
    return base


def _ingest_batch_row_dict(conn, batch_id: str) -> dict:
    """Pull exactly the columns ``_recompute_payload_hash_simple`` needs, plus
    ``payload_hash`` itself for comparison."""

    row = conn.execute(
        f"""
        SELECT batch_id, landing_row_count, request_json, partition_value, source_name,
               contract_version, contract_hash, config_hash, observed_at, available_at,
               payload_hash
          FROM {INGEST_BATCH_TABLE}
         WHERE batch_id = ?
        """,
        [batch_id],
    ).fetchone()
    assert row is not None, f"no ingest_batch row for batch_id={batch_id!r}"
    cols = [
        "batch_id",
        "landing_row_count",
        "request_json",
        "partition_value",
        "source_name",
        "contract_version",
        "contract_hash",
        "config_hash",
        "observed_at",
        "available_at",
        "payload_hash",
    ]
    return dict(zip(cols, row, strict=True))


def _assert_recompute_matches_stored(conn, dataset_id: str, batch_id: str) -> None:
    row = _ingest_batch_row_dict(conn, batch_id)
    domain_spec = DOMAIN_BY_DATASET_ID[dataset_id]
    recomputed, err = _recompute_payload_hash_simple(conn, domain_spec, row)
    assert err is None, err
    assert recomputed == row["payload_hash"]


def _make_batch_stamps_stale(conn, dataset_id: str, batch_id: str) -> None:
    """Simulate a batch that landed before the 2026-09-01 contract-fingerprint
    restamp: its frozen ``contract_hash``/``config_hash``/``source_name`` no longer
    match what the live contract computes today. ``contract_version`` and
    ``writer_id`` are deliberately left untouched — those stay comparable.

    Re-seals ``payload_hash`` via the exact same recipe production uses
    (``stamp_checks._recompute_payload_hash_simple``) so the row remains a
    self-consistent frozen seal, not a corrupted one.
    """

    conn.execute(
        f"""
        UPDATE {INGEST_BATCH_TABLE}
           SET contract_hash = ?, config_hash = ?, source_name = ?
         WHERE batch_id = ?
        """,
        [STALE_CONTRACT_HASH, STALE_CONFIG_HASH, STALE_SOURCE_NAME, batch_id],
    )
    row = _ingest_batch_row_dict(conn, batch_id)
    domain_spec = DOMAIN_BY_DATASET_ID[dataset_id]
    recomputed, err = _recompute_payload_hash_simple(conn, domain_spec, row)
    assert err is None, err
    conn.execute(
        f"UPDATE {INGEST_BATCH_TABLE} SET payload_hash = ? WHERE batch_id = ?",
        [recomputed, batch_id],
    )


def test_reseal_recipe_reproduces_production_seal_for_each_family(conn) -> None:
    """Prove the recipe used by _make_batch_stamps_stale (and by stamp_checks'
    check④ in production) before any UPDATE ever touches payload_hash: for each of
    the three families, land a batch and recompute payload_hash from the freshly
    landed row. It must equal the value production itself just stamped."""

    st_contract = load_stock_st_contract()
    st_batch_id = "stock_st:reseal-recipe:1"
    land_stock_st_batch(
        conn,
        SecurityDayLandingBatch(
            source=st_contract.source,
            batch_id=st_batch_id,
            partition_value=ST_PARTITION,
            observed_at=ST_OBSERVED,
            available_at=ST_OBSERVED,
            rows=_st_rows(),
            request={"api": "stock_st", "trade_date": ST_PARTITION},
        ),
        st_contract,
    )
    _assert_recompute_matches_stored(conn, ST_DATASET_ID, st_batch_id)

    holders_contract = load_holders_top10_contract()
    holders_handed = propagate_disclosure_execution_contract(
        "holders_top10", holders_contract
    )
    holders_batch_id = f"holders_top10:{HOLDERS_PARTITION}:reseal-recipe"
    land_holders_top10_batch(
        conn,
        HoldersTop10LandingBatch(
            batch_id=holders_batch_id,
            partition_value=HOLDERS_PARTITION,
            observed_at=HOLDERS_OBSERVED,
            available_at=HOLDERS_OBSERVED,
            rows=[_holders_row()],
            request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": HOLDERS_PARTITION},
        ),
        holders_handed,
        handoff=holders_handed,
    )
    _assert_recompute_matches_stored(conn, HOLDERS_DATASET_ID, holders_batch_id)

    stk_contract = load_stk_holdertrade_contract()
    stk_handed = propagate_disclosure_execution_contract("stk_holdertrade", stk_contract)
    stk_batch_id = f"stk_holdertrade:{STK_PARTITION}:reseal-recipe"
    land_stk_holdertrade_batch(
        conn,
        StkHoldertradeLandingBatch(
            batch_id=stk_batch_id,
            partition_value=STK_PARTITION,
            observed_at=STK_OBSERVED,
            available_at=STK_OBSERVED,
            rows=[_stk_row()],
            request={"api": "stk_holdertrade", "ann_date": STK_PARTITION},
            source=STK_SOURCE,
            contract_version=STK_CONTRACT_VERSION,
        ),
        stk_handed,
        handoff=stk_handed,
    )
    _assert_recompute_matches_stored(conn, STK_DATASET_ID, stk_batch_id)


# ---------------------------------------------------------------------------
# Family 1: stock_st (services.data_sources.security_day_partition family)
# ---------------------------------------------------------------------------


def test_stock_st_stale_landed_stamps_are_accepted(conn) -> None:
    """TARGET (was RED pre-fix): a LANDED batch whose frozen contract_hash/
    config_hash/source_name predate the 2026-09-01 restamp must still be
    ACCEPTED — those columns are a frozen seal, not a live-contract check. The
    pointer written at accept time carries the LIVE contract's hashes; the
    ingest_batch row keeps its frozen (stale) values untouched.

    PRE-FIX: accept_stock_st_batch returns REJECTED with rejection_code
    CONTRACT_DRIFT (security_day_partition.py's wiring_mismatch block compares
    ingest_batch.contract_hash/config_hash/source_name against the live contract).
    """

    contract = load_stock_st_contract()
    batch_id = "stock_st:stale-stamps:1"
    land_stock_st_batch(
        conn,
        SecurityDayLandingBatch(
            source=contract.source,
            batch_id=batch_id,
            partition_value=ST_PARTITION,
            observed_at=ST_OBSERVED,
            available_at=ST_OBSERVED,
            rows=_st_rows(),
            request={"api": "stock_st", "trade_date": ST_PARTITION},
        ),
        contract,
    )
    status_before = conn.execute(
        f"SELECT status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?", [batch_id]
    ).fetchone()[0]
    assert status_before == "LANDED"

    _make_batch_stamps_stale(conn, ST_DATASET_ID, batch_id)

    outcome = accept_stock_st_batch(conn, batch_id)
    assert outcome.status == "ACCEPTED", (
        outcome.status,
        getattr(outcome, "rejection_code", None),
    )

    pointer = conn.execute(
        f"""
        SELECT contract_hash, config_hash
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ? AND partition_value = ?
        """,
        [ST_DATASET_ID, ST_PARTITION],
    ).fetchone()
    assert tuple(pointer) == (contract.contract_hash, contract.config_hash)

    batch_row = conn.execute(
        f"""
        SELECT contract_hash, config_hash, source_name
          FROM {INGEST_BATCH_TABLE}
         WHERE batch_id = ?
        """,
        [batch_id],
    ).fetchone()
    assert tuple(batch_row) == (STALE_CONTRACT_HASH, STALE_CONFIG_HASH, STALE_SOURCE_NAME)


def test_stock_st_stale_contract_version_is_still_rejected(conn) -> None:
    """Control: contract_version drift must keep failing closed, before AND after
    the fix — contract_version is a declared identity, not a fingerprint."""

    contract = load_stock_st_contract()
    batch_id = "stock_st:stale-version:1"
    land_stock_st_batch(
        conn,
        SecurityDayLandingBatch(
            source=contract.source,
            batch_id=batch_id,
            partition_value=ST_PARTITION,
            observed_at=ST_OBSERVED,
            available_at=ST_OBSERVED,
            rows=_st_rows(),
            request={"api": "stock_st", "trade_date": ST_PARTITION},
        ),
        contract,
    )
    conn.execute(
        f"UPDATE {INGEST_BATCH_TABLE} SET contract_version = '99' WHERE batch_id = ?",
        [batch_id],
    )
    outcome = accept_stock_st_batch(conn, batch_id)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "CONTRACT_DRIFT"


def test_stock_st_stale_writer_id_is_still_rejected(conn) -> None:
    """Control: writer_id drift must keep failing closed, before AND after the
    fix — writer_id is a declared identity, not a fingerprint."""

    contract = load_stock_st_contract()
    batch_id = "stock_st:stale-writer:1"
    land_stock_st_batch(
        conn,
        SecurityDayLandingBatch(
            source=contract.source,
            batch_id=batch_id,
            partition_value=ST_PARTITION,
            observed_at=ST_OBSERVED,
            available_at=ST_OBSERVED,
            rows=_st_rows(),
            request={"api": "stock_st", "trade_date": ST_PARTITION},
        ),
        contract,
    )
    conn.execute(
        f"UPDATE {INGEST_BATCH_TABLE} SET writer_id = 'someone_else' WHERE batch_id = ?",
        [batch_id],
    )
    outcome = accept_stock_st_batch(conn, batch_id)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "CONTRACT_DRIFT"


# ---------------------------------------------------------------------------
# Family 2: holders_top10 (services.data_sources.holders_top10_acceptance)
# ---------------------------------------------------------------------------


def test_holders_stale_landed_stamps_are_accepted(conn) -> None:
    """TARGET (was RED pre-fix): same principle as the stock_st positive test,
    for the disclosure-event holders_top10 path.

    PRE-FIX: accept_holders_top10_batch raises HoldersTop10AcceptanceError with
    "landed contract_hash drift vs handoff" (holders_top10_acceptance.py compares
    the frozen ingest_batch.contract_hash/config_hash against the live handoff).
    """

    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    batch_id = f"holders_top10:{HOLDERS_PARTITION}:stale-stamps"
    rows = [
        _holders_row(holder_rank=1, holder_name="香港中央结算有限公司"),
        _holders_row(holder_rank=2, holder_name="中国证券金融股份有限公司"),
    ]
    land_holders_top10_batch(
        conn,
        HoldersTop10LandingBatch(
            batch_id=batch_id,
            partition_value=HOLDERS_PARTITION,
            observed_at=HOLDERS_OBSERVED,
            available_at=HOLDERS_OBSERVED,
            rows=rows,
            request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": HOLDERS_PARTITION},
        ),
        handed,
        handoff=handed,
    )
    status_before = conn.execute(
        f"SELECT status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?", [batch_id]
    ).fetchone()[0]
    assert status_before == "LANDED"

    _make_batch_stamps_stale(conn, HOLDERS_DATASET_ID, batch_id)

    outcome = accept_holders_top10_batch(conn, batch_id, handed, handoff=handed)
    assert outcome.status == "ACCEPTED", (
        outcome.status,
        getattr(outcome, "rejection_code", None),
    )

    pointer = conn.execute(
        f"""
        SELECT contract_hash, config_hash
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ? AND partition_value = ?
        """,
        [HOLDERS_DATASET_ID, HOLDERS_PARTITION],
    ).fetchone()
    assert tuple(pointer) == (contract.contract_hash, contract.config_hash)

    batch_row = conn.execute(
        f"""
        SELECT contract_hash, config_hash, source_name
          FROM {INGEST_BATCH_TABLE}
         WHERE batch_id = ?
        """,
        [batch_id],
    ).fetchone()
    assert tuple(batch_row) == (STALE_CONTRACT_HASH, STALE_CONFIG_HASH, STALE_SOURCE_NAME)


def test_holders_skip_reland_matches_on_pointer_not_frozen_batch_stamp(conn) -> None:
    """TARGET (was RED pre-fix): the skip-reland idempotency guard
    (holders_top10_skip_land.find_accepted_batch_with_same_payload) must match on
    the CURRENT accepted pointer for this partition, not on whether the
    ACCEPTED batch's frozen contract_hash/config_hash still equal today's live
    contract. Otherwise every restamp permanently disables idempotent skip-reland
    for every already-accepted partition landed before it, and a same-content
    re-land silently starts appending duplicate landing rows again.

    PRE-FIX: find_accepted_batch_with_same_payload's SQL filters
    ``ib.contract_hash = ? AND ib.config_hash = ?`` using the LIVE contract's
    hashes; once the ACCEPTED batch's stored hashes are stale they no longer
    match, so the join finds nothing, no skip happens, and land_holders_top10_batch
    re-lands under the NEW batch_id instead of returning the first one. We assert
    on the returned batch_id so that pre-fix failure is explicit (not just an
    incidental row-count difference).
    """

    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    rows = [
        _holders_row(holder_rank=1, holder_name="香港中央结算有限公司"),
        _holders_row(holder_rank=2, holder_name="中国证券金融股份有限公司"),
    ]
    request = {"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": HOLDERS_PARTITION}
    first_id = f"holders_top10:{HOLDERS_PARTITION}:skip-first"
    first = land_holders_top10_batch(
        conn,
        HoldersTop10LandingBatch(
            batch_id=first_id,
            partition_value=HOLDERS_PARTITION,
            observed_at=HOLDERS_OBSERVED,
            available_at=HOLDERS_OBSERVED,
            rows=rows,
            request=request,
        ),
        handed,
        handoff=handed,
    )
    assert first == first_id
    outcome = accept_holders_top10_batch(conn, first_id, handed, handoff=handed)
    assert outcome.status == "ACCEPTED"

    _make_batch_stamps_stale(conn, HOLDERS_DATASET_ID, first_id)

    landing_before = conn.execute(
        f"SELECT COUNT(*) FROM {HOLDERS_LANDING_TABLE}"
    ).fetchone()[0]

    steps: list[str] = []
    second_id = f"holders_top10:{HOLDERS_PARTITION}:skip-second"
    result = land_holders_top10_batch(
        conn,
        HoldersTop10LandingBatch(
            batch_id=second_id,
            partition_value=HOLDERS_PARTITION,
            observed_at=HOLDERS_OBSERVED,
            available_at=HOLDERS_OBSERVED,
            rows=rows,
            request=request,
        ),
        handed,
        handoff=handed,
        after_step=steps.append,
    )
    assert result == first_id

    assert "skip_accepted_same_payload" in steps
    assert (
        conn.execute(f"SELECT COUNT(*) FROM {HOLDERS_LANDING_TABLE}").fetchone()[0]
        == landing_before
    )


# ---------------------------------------------------------------------------
# Family 3: stk_holdertrade (services.data_sources.disclosure_event_partition
# family)
# ---------------------------------------------------------------------------


def test_stk_holdertrade_stale_landed_stamps_are_accepted(conn) -> None:
    """TARGET (was RED pre-fix): same principle again, for the shared
    disclosure_event_partition path stk_holdertrade rides on.

    PRE-FIX: accept_stk_holdertrade_batch raises StkHoldertradeAcceptanceError with
    "landed contract_hash drift vs handoff" (disclosure_event_partition.py's
    accept_disclosure_event_batch compares the frozen ingest_batch.contract_hash/
    config_hash against the live handoff).
    """

    contract = load_stk_holdertrade_contract()
    handed = propagate_disclosure_execution_contract("stk_holdertrade", contract)
    batch_id = f"stk_holdertrade:{STK_PARTITION}:stale-stamps"
    land_stk_holdertrade_batch(
        conn,
        StkHoldertradeLandingBatch(
            batch_id=batch_id,
            partition_value=STK_PARTITION,
            observed_at=STK_OBSERVED,
            available_at=STK_OBSERVED,
            rows=[_stk_row()],
            request={"api": "stk_holdertrade", "ann_date": STK_PARTITION},
            source=STK_SOURCE,
            contract_version=STK_CONTRACT_VERSION,
        ),
        handed,
        handoff=handed,
    )
    status_before = conn.execute(
        f"SELECT status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?", [batch_id]
    ).fetchone()[0]
    assert status_before == "LANDED"

    _make_batch_stamps_stale(conn, STK_DATASET_ID, batch_id)

    outcome = accept_stk_holdertrade_batch(conn, batch_id, handed, handoff=handed)
    assert outcome.status == "ACCEPTED", (
        outcome.status,
        getattr(outcome, "rejection_code", None),
    )

    pointer = conn.execute(
        f"""
        SELECT contract_hash, config_hash
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ? AND partition_value = ?
        """,
        [STK_DATASET_ID, STK_PARTITION],
    ).fetchone()
    assert tuple(pointer) == (contract.contract_hash, contract.config_hash)

    batch_row = conn.execute(
        f"""
        SELECT contract_hash, config_hash, source_name
          FROM {INGEST_BATCH_TABLE}
         WHERE batch_id = ?
        """,
        [batch_id],
    ).fetchone()
    assert tuple(batch_row) == (STALE_CONTRACT_HASH, STALE_CONFIG_HASH, STALE_SOURCE_NAME)
