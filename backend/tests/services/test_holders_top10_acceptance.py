"""E0 tracer: holders_top10 land→validate→accept (fixture/memory; no mass fetch)."""
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
from services.data_sources.formal_execution import (
    FormalExecutionHandoffError,
    propagate_disclosure_execution_contract,
)
from services.data_sources.holders_top10_acceptance import (
    HoldersTop10AcceptanceError,
    HoldersTop10LandingBatch,
    accept_holders_top10_batch,
    land_holders_top10_batch,
    publish_accepted_holders_top10_partition,
    runtime_surface,
)
from services.data_sources.holders_top10_contract import load_holders_top10_contract
from services.data_sources.holders_top10_schema import (
    ACCEPTED_TABLE,
    CANONICAL_TABLE,
    DATASET_ID,
    INGEST_BATCH_TABLE,
    LANDING_TABLE,
)
from services.duck_adapter import connect

PARTITION = "20260429"
OBSERVED = datetime(2026, 4, 29, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)


def _row(**overrides):
    base = {
        "stock_code": "600519",
        "report_date": "20260331",
        "holder_set": "free",
        "holder_rank": 1,
        "row_seq": 1,
        "holder_name": "香港中央结算有限公司",
        # schema v3 / contract v4 (2026-09-07): 身份键上 canonical。
        # 默认值挑的就是那个最能说明问题的实体 —— 实测 code 10671586 在
        # 2018-12-31 起的数据里用过 9 种写法 (含繁体「結算」、(A股)/(沪股通) 后缀、
        # "中心"/"公司"), 共 51,499 行。只按 holder_name 聚合会把它拆成 9 个假实体。
        "holder_code": "10671586",
        "is_holder_org": True,
        "hold_ratio_float": 7.12,
        "notice_date": PARTITION,
        "is_exit_row": False,
    }
    base.update(overrides)
    return base


@pytest.fixture
def conn():
    database = connect(":memory:")
    yield database
    database.close()


def test_inventory_declares_holders_formal_writers_strangler() -> None:
    inventory = {item["domain"]: item for item in disclosure_inventory()}
    holders = inventory["holders_top10"]
    assert holders["landing_writer"] is not None
    assert holders["canonical_writer"] is not None
    assert holders["runtime_state"] == "formal_only"
    assert holders["conformity"] == "NONCONFORMING"
    # Compat plane DROPped — escape hatch retired (not test-escape).
    with pytest.raises(DisclosureBoundaryError, match="holders_compat_retired"):
        authorize_nonconforming_direct_write(
            "holders_top10",
            conformity="NONCONFORMING",
            allow_test_escape=True,
        )
    surface = runtime_surface()
    assert surface["legacy_mirror"] == "retired"
    assert surface["legacy_direct_write"] == "retired"
    # DatasetSnapshot freeze remains blocked without cutover_allowed.
    with pytest.raises(DisclosureBoundaryError, match="dataset_snapshot"):
        refuse_accepted_publication_claim("holders_top10", "DatasetSnapshot")
    report = attest_disclosure_research_surface()
    assert report.overall_status == "NONCONFORMING"
    assert report.cutover_allowed is False


def test_land_without_execution_handoff_fails_closed(conn) -> None:
    contract = load_holders_top10_contract()
    batch = HoldersTop10LandingBatch(
        batch_id=f"holders_top10:{PARTITION}:test",
        partition_value=PARTITION,
        observed_at=OBSERVED,
        available_at=OBSERVED,
        rows=[_row()],
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
    )
    with pytest.raises(HoldersTop10AcceptanceError, match="execution_handoff"):
        land_holders_top10_batch(conn, batch, contract)


def test_missing_available_at_fails_closed(conn) -> None:
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    batch = HoldersTop10LandingBatch(
        batch_id=f"holders_top10:{PARTITION}:missing-avail",
        partition_value=PARTITION,
        observed_at=OBSERVED,
        available_at=None,  # type: ignore[arg-type]
        rows=[_row()],
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
    )
    with pytest.raises(HoldersTop10AcceptanceError, match="available_at"):
        land_holders_top10_batch(conn, batch, handed, handoff=handed)


def test_forged_available_at_before_notice_date_fails_closed(conn) -> None:
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    forged = datetime(2026, 4, 28, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    batch = HoldersTop10LandingBatch(
        batch_id=f"holders_top10:{PARTITION}:forged",
        partition_value=PARTITION,
        observed_at=forged,
        available_at=forged,
        rows=[_row()],
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
    )
    land_holders_top10_batch(conn, batch, handed, handoff=handed)
    outcome = accept_holders_top10_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "FORGED_AVAILABLE_AT"


def test_missing_notice_date_on_row_fails_closed(conn) -> None:
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    batch = HoldersTop10LandingBatch(
        batch_id=f"holders_top10:{PARTITION}:null-notice",
        partition_value=PARTITION,
        observed_at=OBSERVED,
        available_at=OBSERVED,
        rows=[_row(notice_date=None)],
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
    )
    land_holders_top10_batch(conn, batch, handed, handoff=handed)
    outcome = accept_holders_top10_batch(conn, batch.batch_id, handed, handoff=handed)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "MISSING_NOTICE_DATE"


def test_publish_land_accept_roundtrip_fixture(conn) -> None:
    contract = load_holders_top10_contract()
    rows = [
        _row(holder_rank=1, holder_name="香港中央结算有限公司"),
        _row(holder_rank=2, holder_name="中国证券金融股份有限公司"),
    ]
    outcome = publish_accepted_holders_top10_partition(
        conn,
        HoldersTop10LandingBatch(
            batch_id=f"holders_top10:{PARTITION}:ok",
            partition_value=PARTITION,
            observed_at=OBSERVED,
            available_at=OBSERVED,
            rows=rows,
            request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
        ),
        contract,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == 2
    assert outcome.batch_id.startswith("holders_top10:")
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


def test_accepted_same_payload_hash_skips_reland(conn) -> None:
    """Knife 2: ACCEPTED + same payload_hash → no new landing rows."""

    from services.data_sources.disclosure_transport import (
        land_then_accept_disclosure_partition,
    )

    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    rows = [
        _row(holder_rank=1, holder_name="香港中央结算有限公司"),
        _row(holder_rank=2, holder_name="中国证券金融股份有限公司"),
    ]
    request = {"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION}
    first_id = f"holders_top10:{PARTITION}:first"
    first = land_holders_top10_batch(
        conn,
        HoldersTop10LandingBatch(
            batch_id=first_id,
            partition_value=PARTITION,
            observed_at=OBSERVED,
            available_at=OBSERVED,
            rows=rows,
            request=request,
        ),
        handed,
        handoff=handed,
    )
    assert first == first_id
    outcome = accept_holders_top10_batch(conn, first_id, handed, handoff=handed)
    assert outcome.status == "ACCEPTED"

    landing_before = conn.execute(f"SELECT COUNT(*) FROM {LANDING_TABLE}").fetchone()[0]
    batches_before = conn.execute(
        f"SELECT COUNT(*) FROM {INGEST_BATCH_TABLE}"
    ).fetchone()[0]
    canonical_before = conn.execute(
        f"SELECT COUNT(*) FROM {CANONICAL_TABLE}"
    ).fetchone()[0]

    steps: list[str] = []
    skipped = land_holders_top10_batch(
        conn,
        HoldersTop10LandingBatch(
            batch_id=f"holders_top10:{PARTITION}:storm-uuid",
            partition_value=PARTITION,
            observed_at=OBSERVED,
            available_at=OBSERVED,
            rows=rows,
            request=request,
        ),
        handed,
        handoff=handed,
        after_step=steps.append,
    )
    assert skipped == first_id
    assert "skip_accepted_same_payload" in steps
    assert conn.execute(f"SELECT COUNT(*) FROM {LANDING_TABLE}").fetchone()[0] == (
        landing_before
    )
    assert conn.execute(f"SELECT COUNT(*) FROM {INGEST_BATCH_TABLE}").fetchone()[
        0
    ] == batches_before

    # Transport fuse must accept via the skipped batch_id (not the unused uuid).
    fused = land_then_accept_disclosure_partition(
        "holders_top10",
        conn,
        partition=PARTITION,
        rows=rows,
        observed_at=OBSERVED,
        available_at=OBSERVED,
        batch_id=f"holders_top10:{PARTITION}:another-uuid",
        request=request,
        bootstrap=False,
    )
    assert fused.status == "ACCEPTED"
    assert fused.batch_id == first_id
    assert conn.execute(f"SELECT COUNT(*) FROM {LANDING_TABLE}").fetchone()[0] == (
        landing_before
    )
    assert conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0] == (
        canonical_before
    )


def test_accepted_same_rows_skip_when_fetch_clock_differs(conn) -> None:
    """Skip-land identity is row content, not wall-clock on the fetch envelope."""

    from datetime import timedelta

    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    rows = [
        _row(holder_rank=1, holder_name="香港中央结算有限公司"),
        _row(holder_rank=2, holder_name="中国证券金融股份有限公司"),
    ]
    request = {"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION}
    first_id = f"holders_top10:{PARTITION}:clock-first"
    land_holders_top10_batch(
        conn,
        HoldersTop10LandingBatch(
            batch_id=first_id,
            partition_value=PARTITION,
            observed_at=OBSERVED,
            available_at=OBSERVED,
            rows=rows,
            request=request,
        ),
        handed,
        handoff=handed,
    )
    accept_holders_top10_batch(conn, first_id, handed, handoff=handed)
    later = OBSERVED + timedelta(hours=1)
    landing_before = conn.execute(f"SELECT COUNT(*) FROM {LANDING_TABLE}").fetchone()[0]
    skipped = land_holders_top10_batch(
        conn,
        HoldersTop10LandingBatch(
            batch_id=f"holders_top10:{PARTITION}:clock-later",
            partition_value=PARTITION,
            observed_at=later,
            available_at=later,
            rows=rows,
            request=request,
        ),
        handed,
        handoff=handed,
    )
    assert skipped == first_id
    assert conn.execute(f"SELECT COUNT(*) FROM {LANDING_TABLE}").fetchone()[0] == (
        landing_before
    )


def test_new_payload_still_appends_landing(conn) -> None:
    """Different content must keep append-only landing (no silent overwrite)."""

    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    request = {"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION}
    first_id = f"holders_top10:{PARTITION}:v1"
    land_holders_top10_batch(
        conn,
        HoldersTop10LandingBatch(
            batch_id=first_id,
            partition_value=PARTITION,
            observed_at=OBSERVED,
            available_at=OBSERVED,
            rows=[_row(holder_rank=1, holder_name="A")],
            request=request,
        ),
        handed,
        handoff=handed,
    )
    assert (
        accept_holders_top10_batch(conn, first_id, handed, handoff=handed).status
        == "ACCEPTED"
    )
    second_id = f"holders_top10:{PARTITION}:v2"
    landed = land_holders_top10_batch(
        conn,
        HoldersTop10LandingBatch(
            batch_id=second_id,
            partition_value=PARTITION,
            observed_at=OBSERVED,
            available_at=OBSERVED,
            rows=[_row(holder_rank=1, holder_name="B")],
            request=request,
        ),
        handed,
        handoff=handed,
    )
    assert landed == second_id
    assert conn.execute(f"SELECT COUNT(*) FROM {LANDING_TABLE}").fetchone()[0] == 2
    assert (
        accept_holders_top10_batch(conn, second_id, handed, handoff=handed).status
        == "ACCEPTED"
    )


def test_disclosure_handoff_rejects_wrong_contract_for_other_domain() -> None:
    contract = load_holders_top10_contract()
    with pytest.raises(
        FormalExecutionHandoffError, match="OrgHoldingContract|mismatched"
    ):
        propagate_disclosure_execution_contract("org_holding", contract)
    with pytest.raises(
        FormalExecutionHandoffError, match="no disclosure execution consumer"
    ):
        propagate_disclosure_execution_contract("not_a_disclosure_domain", contract)


# ── delete_scope: 一个分区由多个按股批次拼成时的替换范围 ──────────────────────


def _land_and_accept(conn, handed, *, batch_id, rows, scope="partition"):
    """落一批并接受, 返回 outcome。测试内小工具, 不改生产路径。"""
    batch = HoldersTop10LandingBatch(
        batch_id=batch_id,
        partition_value=PARTITION,
        observed_at=OBSERVED,
        available_at=OBSERVED,
        rows=rows,
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
    )
    land_holders_top10_batch(conn, batch, handed, handoff=handed)
    return accept_holders_top10_batch(
        conn, batch_id, handed, handoff=handed, delete_scope=scope
    )


def test_stocks_in_batch_scope_keeps_other_stocks_on_same_notice_date(conn) -> None:
    """按股回填: 两个互斥股票集先后 accept, 两组行都要在。

    守的属性是「一个批次只替换它自己覆盖的股票」。默认的 partition 范围做不到这一点 ——
    它靠上游先把整个分区读出来拼进批次来保证不丢, 代价是写放大 (2026-09-07 实测: 全量
    历史回填目标 1,449,322 行, 按 partition 范围要实际写 854,850,658 行, 590 倍)。
    本用例直接落两批**互不重叠**的股票, 不走上游那段合并 —— 只有 delete_scope
    收窄到本批股票时两组才能共存。
    """
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)

    _land_and_accept(
        conn, handed, batch_id=f"holders_top10:{PARTITION}:stock-a",
        rows=[_row(stock_code="600519")], scope="stocks_in_batch",
    )
    _land_and_accept(
        conn, handed, batch_id=f"holders_top10:{PARTITION}:stock-b",
        rows=[_row(stock_code="000001")], scope="stocks_in_batch",
    )

    got = {
        r[0]
        for r in conn.execute(
            f"SELECT DISTINCT stock_code FROM {CANONICAL_TABLE} WHERE notice_date = ?",
            [PARTITION],
        ).fetchall()
    }
    assert got == {"600519", "000001"}

    # accepted 指针必须描述**合并后的整个分区**, 不是最后一批。
    row_count = conn.execute(
        f"SELECT row_count FROM {ACCEPTED_TABLE} "
        f"WHERE dataset_id = ? AND partition_value = ?",
        [DATASET_ID, PARTITION],
    ).fetchone()[0]
    assert row_count == 2


def test_stocks_in_batch_scope_replaces_only_that_stock_on_second_accept(conn) -> None:
    """同一只股二次 accept 只留后一批; 同分区其他股不受影响。"""
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)

    _land_and_accept(
        conn, handed, batch_id=f"holders_top10:{PARTITION}:other",
        rows=[_row(stock_code="000001", holder_name="中央汇金")], scope="stocks_in_batch",
    )
    _land_and_accept(
        conn, handed, batch_id=f"holders_top10:{PARTITION}:v1",
        rows=[_row(stock_code="600519", holder_name="旧名")], scope="stocks_in_batch",
    )
    _land_and_accept(
        conn, handed, batch_id=f"holders_top10:{PARTITION}:v2",
        rows=[_row(stock_code="600519", holder_name="新名")], scope="stocks_in_batch",
    )

    rows = [
        tuple(r)
        for r in conn.execute(
            f"SELECT stock_code, holder_name FROM {CANONICAL_TABLE} "
            f"WHERE notice_date = ? ORDER BY stock_code",
            [PARTITION],
        ).fetchall()
    ]
    assert rows == [("000001", "中央汇金"), ("600519", "新名")]


def test_partition_scope_is_still_the_default(conn) -> None:
    """默认仍是整分区替换 —— 日更按公告日全市场拉, 一个批次就是那天的全部内容,
    某只股从重拉结果里消失时它的旧行应当一并消失。改默认会静默改变日更语义。"""
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)

    _land_and_accept(
        conn, handed, batch_id=f"holders_top10:{PARTITION}:day-v1",
        rows=[_row(stock_code="600519"), _row(stock_code="000001", row_seq=2)],
    )
    _land_and_accept(
        conn, handed, batch_id=f"holders_top10:{PARTITION}:day-v2",
        rows=[_row(stock_code="600519")],
    )

    got = {
        r[0]
        for r in conn.execute(
            f"SELECT DISTINCT stock_code FROM {CANONICAL_TABLE} WHERE notice_date = ?",
            [PARTITION],
        ).fetchall()
    }
    assert got == {"600519"}


def test_unknown_delete_scope_fails_closed(conn) -> None:
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    with pytest.raises(HoldersTop10AcceptanceError, match="unknown delete_scope"):
        _land_and_accept(
            conn, handed, batch_id=f"holders_top10:{PARTITION}:bad",
            rows=[_row()], scope="whatever",
        )


# ── 身份键三态 (2026-09-07, schema v3 / contract v4) ─────────────────────────
#
# is_holder_org 在 DDL 上可空, 只是为了不因为加一列去 drop 生产表 (610,414 行 v2 遗留,
# 其中 6,554 行连 staging 快照都没有)。真正的约束在这条 accept 路径上 —— 下面三个测试
# 就是「可空但安全」这句话的全部依据: v3 起写进来的行必然三态可判。


def _land_accept(conn, row, suffix):
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    batch = HoldersTop10LandingBatch(
        batch_id=f"holders_top10:{PARTITION}:{suffix}",
        partition_value=PARTITION,
        observed_at=OBSERVED,
        available_at=OBSERVED,
        rows=[row],
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
    )
    land_holders_top10_batch(conn, batch, handed, handoff=handed)
    return accept_holders_top10_batch(conn, batch.batch_id, handed, handoff=handed)


def test_null_is_holder_org_is_rejected_not_defaulted(conn) -> None:
    """v3 起不许再写入身份未判的行 —— 这是 DDL 可空之所以安全的唯一原因。"""
    outcome = _land_accept(conn, _row(is_holder_org=None), "null-org")
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "INVALID_HOLDER_ORG_FLAG"


def test_org_without_holder_code_is_rejected(conn) -> None:
    """机构却没有 code = 供应商行为变了。

    实测边界: IS_HOLDORG=1 的 829,249 行里 holder_code 空 0 行。静默放行会让身份键
    悄悄退化回名字 —— 而名字在 35.2% 的行上不是身份, 正是加这一列要解决的问题。
    """
    outcome = _land_accept(conn, _row(is_holder_org=True, holder_code=None), "org-nocode")
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "MISSING_ORG_HOLDER_CODE"


def test_natural_person_without_holder_code_is_accepted(conn) -> None:
    """个人无编码是**正常**的, 不是缺失 —— 供应商只给机构发码 (实测 620,073 行全空)。

    这条与上一条成对: 少了它, 上一条的严格会被误读成「holder_code 必填」,
    下一个人就会去给个人编一个假 code。
    """
    outcome = _land_accept(
        conn,
        _row(holder_name="张三", is_holder_org=False, holder_code=None),
        "person-nocode",
    )
    assert outcome.status == "ACCEPTED", outcome.rejection_code
    stored = conn.execute(
        "SELECT holder_code, is_holder_org FROM canonical_top10_float_holders_period "
        "WHERE holder_name = '张三'"
    ).fetchone()
    assert tuple(stored) == (None, False), stored


# ── 同粒度多版本共存 (2026-09-08, notice_date 进 GRAIN) ──────────────────────


def test_ensure_schema_refuses_pk_that_is_not_grain(conn) -> None:
    """漂移检查必须比主键, 不只比列集合。

    2026-09-08: 此前它只问「列对不对」。GRAIN 变了而列一列没变, 于是生产表带着旧 6 列 PK
    检查照样全绿, 直到某天两版同粒度撞车才炸 —— 「门问的问题 ≠ 它想守的东西」。
    """
    from services.data_sources.holders_top10_acceptance import (
        ensure_holders_top10_acceptance_schema,
    )
    from services.data_sources.holders_top10_schema import SCHEMA_CONTRACT

    ensure_holders_top10_acceptance_schema(conn)
    conn.execute(f"DROP TABLE {CANONICAL_TABLE}")
    # 手建一张列相同、但主键是旧 6 列(缺 notice_date)的表
    old_pk = [c for c in SCHEMA_CONTRACT["primary_key"] if c != "notice_date"]
    cols = ",\n".join(
        f"{f['name']} {f['duckdb_type']}" for f in SCHEMA_CONTRACT["fields"]
    )
    conn.execute(
        f"CREATE TABLE {CANONICAL_TABLE} ({cols}, PRIMARY KEY ({', '.join(old_pk)}))"
    )
    with pytest.raises(HoldersTop10AcceptanceError, match="primary key drift"):
        ensure_holders_top10_acceptance_schema(conn)


def test_same_grain_republished_on_later_notice_date_coexists(conn) -> None:
    """供应商把同一报告期改挂到新公告日时, 两版必须能共存。

    这是 2026-09-08 之前 5 只股 Duplicate key、以及日更两次整天丢失的直接原因:
    notice_date 是分区键却不在 GRAIN 里, 于是 (股, 报告期) 全表只能存在于一个分区,
    新分区写入时撞上旧分区那行, 而分区内 DELETE 够不到它。

    旧版**不删** —— 它是「当时可知」不是「记错」: 供应商是 SCD-1(只留最新态),
    我们的 landing 是唯一一份「那天那个榜单长什么样」的记录, 删掉 = 历史消失(红线 1)。
    """
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)

    def land_accept(partition, notice, suffix, observed=OBSERVED):
        batch = HoldersTop10LandingBatch(
            batch_id=f"holders_top10:{partition}:{suffix}",
            partition_value=partition,
            observed_at=observed,
            available_at=observed,
            rows=[_row(notice_date=notice)],
            request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": partition},
        )
        land_holders_top10_batch(conn, batch, handed, handoff=handed)
        return accept_holders_top10_batch(conn, batch.batch_id, handed, handoff=handed)

    first = land_accept(PARTITION, PARTITION, "v1")
    assert first.status == "ACCEPTED", first.rejection_code

    # 同一 (股, 报告期, rank), 供应商改挂到更晚的公告日
    later = "20260618"
    # 每个分区有自己的 notice cutoff, available_at 早于它会被判 FORGED_AVAILABLE_AT ——
    # 那道闸是对的(不许伪造"我在公告前就知道了"), 所以新版要用它自己那天的时点。
    later_observed = datetime(2026, 6, 18, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    second = land_accept(later, later, "v2", observed=later_observed)
    assert second.status == "ACCEPTED", second.rejection_code

    n = conn.execute(
        f"SELECT COUNT(*) FROM {CANONICAL_TABLE} "
        "WHERE stock_code = '600519' AND report_date = '20260331'"
    ).fetchone()[0]
    assert n == 2, f"两版没共存, 只有 {n} 行"

    # 旧版还在, 且它自己的分区指针没被后来那版改掉
    old_ptr = conn.execute(
        f"SELECT row_count FROM {ACCEPTED_TABLE} "
        "WHERE dataset_id = ? AND partition_value = ?",
        [DATASET_ID, PARTITION],
    ).fetchone()
    assert tuple(old_ptr) == (1,), old_ptr
    new_ptr = conn.execute(
        f"SELECT row_count FROM {ACCEPTED_TABLE} "
        "WHERE dataset_id = ? AND partition_value = ?",
        [DATASET_ID, later],
    ).fetchone()
    assert tuple(new_ptr) == (1,), new_ptr


# ── delete_scope="merge_new_grains" (刀 B1; spec_holders_pagination.md §4.3) ──
#
# 与上面所有既有用例共用同一批 land/accept 原语, 只是 delete_scope 换了一个
# 值 —— "partition"/"stocks_in_batch" 两条既有代码路径一字不动 (B30 静态钉住
# 契约常量不变已经证明改动没有碰到 _HASH_FIELDS / GRAIN)。


def _land_accept_merge(conn, rows, *, partition, batch_id, observed=OBSERVED):
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    batch = HoldersTop10LandingBatch(
        batch_id=batch_id,
        partition_value=partition,
        observed_at=observed,
        available_at=observed,
        rows=rows,
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": partition},
    )
    land_holders_top10_batch(conn, batch, handed, handoff=handed)
    return accept_holders_top10_batch(
        conn, batch.batch_id, handed, handoff=handed, delete_scope="merge_new_grains"
    )


def test_delete_scope_closed_set_rejects_unknown_value(conn) -> None:
    """delete_scope 是闭合集合 {"partition","stocks_in_batch","merge_new_grains"} ——
    未登记的值 fail-closed, 不静默当某个已知值处理。"""
    contract = load_holders_top10_contract()
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    batch = HoldersTop10LandingBatch(
        batch_id=f"holders_top10:{PARTITION}:bogus-scope",
        partition_value=PARTITION,
        observed_at=OBSERVED,
        available_at=OBSERVED,
        rows=[_row()],
        request={"api": "RPT_F10_EH_FREEHOLDERS", "notice_date": PARTITION},
    )
    land_holders_top10_batch(conn, batch, handed, handoff=handed)
    with pytest.raises(HoldersTop10AcceptanceError, match="delete_scope"):
        accept_holders_top10_batch(
            conn, batch.batch_id, handed, handoff=handed, delete_scope="bogus"
        )


def test_merge_new_grains_inserts_new_and_holds_existing(conn) -> None:
    first = _land_accept_merge(
        conn, [_row(holder_rank=1, holder_name="甲")],
        partition=PARTITION, batch_id=f"holders_top10:{PARTITION}:m1",
    )
    assert first.status == "ACCEPTED"
    assert first.inserted_rows == 1
    assert first.held_rows == 0

    later_observed = datetime(2026, 4, 29, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    second = _land_accept_merge(
        conn,
        [_row(holder_rank=1, holder_name="甲"), _row(holder_rank=2, holder_name="乙")],
        partition=PARTITION, batch_id=f"holders_top10:{PARTITION}:m2",
        observed=later_observed,
    )
    assert second.status == "ACCEPTED"
    assert second.inserted_rows == 1  # 只有「乙」是新 GRAIN
    assert second.held_rows == 1      # 「甲」已存在, 持有不写

    first_batch_id_after = conn.execute(
        f"SELECT ingest_batch_id FROM {CANONICAL_TABLE} "
        "WHERE stock_code='600519' AND holder_name='甲'"
    ).fetchone()[0]
    assert first_batch_id_after == first.batch_id  # 「甲」的 batch 归属没被第二批改掉


def test_merge_new_grains_row_seq_collision_rejects(conn) -> None:
    """同 GRAIN (含 row_seq) 已存在但 holder_name 不同 -> ROW_SEQ_COLLISION,
    canonical 不变 (B25b)。"""
    _land_accept_merge(
        conn, [_row(holder_rank=1, holder_name="甲")],
        partition=PARTITION, batch_id=f"holders_top10:{PARTITION}:c1",
    )
    outcome = _land_accept_merge(
        conn, [_row(holder_rank=1, holder_name="乙")],
        partition=PARTITION, batch_id=f"holders_top10:{PARTITION}:c2",
    )
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "ROW_SEQ_COLLISION"
    names = {
        r[0] for r in conn.execute(
            f"SELECT holder_name FROM {CANONICAL_TABLE} WHERE stock_code='600519'"
        ).fetchall()
    }
    assert names == {"甲"}


def test_merge_new_grains_exit_group_not_touched_rejects(conn) -> None:
    """批内退出行的组没有任何 GRAIN 不存在的观测行 -> EXIT_GROUP_NOT_TOUCHED
    (B27): 写方只该对有新观测的组派生。"""
    _land_accept_merge(
        conn,
        [_row(holder_rank=1, holder_name="甲"), _row(holder_rank=2, holder_name="乙")],
        partition=PARTITION, batch_id=f"holders_top10:{PARTITION}:e1",
    )
    later_observed = datetime(2026, 4, 29, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    exit_row = _row(holder_rank=2, holder_name="乙", is_exit_row=True)
    outcome = _land_accept_merge(
        conn, [exit_row], partition=PARTITION, batch_id=f"holders_top10:{PARTITION}:e2",
        observed=later_observed,
    )
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "EXIT_GROUP_NOT_TOUCHED"


def test_merge_new_grains_touched_group_allows_exit_replacement(conn) -> None:
    """对照: 组里确有新观测行时, 批内退出行正常通过并整组替换 (B26)。"""
    _land_accept_merge(
        conn,
        [_row(holder_rank=1, holder_name="甲"), _row(holder_rank=2, holder_name="乙")],
        partition=PARTITION, batch_id=f"holders_top10:{PARTITION}:g1",
    )
    later_observed = datetime(2026, 4, 29, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    outcome = _land_accept_merge(
        conn,
        [_row(holder_rank=3, holder_name="丙"),
         _row(holder_rank=2, holder_name="乙", is_exit_row=True)],
        partition=PARTITION, batch_id=f"holders_top10:{PARTITION}:g2",
        observed=later_observed,
    )
    assert outcome.status == "ACCEPTED", outcome.rejection_code
    assert outcome.inserted_rows == 1
    assert outcome.exit_rows_replaced == 0  # 之前没有旧退出行, 这次是新插入


def test_merge_new_grains_pointer_matches_canonical(conn) -> None:
    """B28: 指针整分区现算, 与 partition_pointer_stats 复算的行数一致
    (db_invariants.holders_pointer_rowcount_matches_canonical 的判据)。"""
    _land_accept_merge(
        conn, [_row(holder_rank=1, holder_name="甲")],
        partition=PARTITION, batch_id=f"holders_top10:{PARTITION}:p1",
    )
    later_observed = datetime(2026, 4, 29, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    )
    _land_accept_merge(
        conn, [_row(holder_rank=2, holder_name="乙")],
        partition=PARTITION, batch_id=f"holders_top10:{PARTITION}:p2",
        observed=later_observed,
    )
    actual = conn.execute(
        f"SELECT COUNT(*) FROM {CANONICAL_TABLE} WHERE notice_date=?", [PARTITION]
    ).fetchone()[0]
    pointer = conn.execute(
        f"SELECT row_count FROM {ACCEPTED_TABLE} WHERE partition_value=?", [PARTITION]
    ).fetchone()[0]
    assert pointer == actual == 2
