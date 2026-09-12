"""S5 -- K 线真相表重复实体史修正脚本 + 两条常驻审计的单测
(asof_identity_r1.md §5 / §9 S5)。

一个门控条件一个隔离用例, fixture 里其它条件全满足、只违反被测的那一个。全部用
内存 DuckDB (`conftest.duck_mem` / 裸 `duckdb.connect` + ATTACH), 不打开
`data/*.duckdb`。
"""
from __future__ import annotations

import json

import duckdb
import pytest

from conftest import duck_mem

from scripts.amend_kline_entity_duplicates import (
    AmendEventPlan,
    AmendMismatchError,
    AmendPlan,
    execute,
    format_plan,
    plan,
)
from services.data_audit import (
    _check_kline_code_succession,
    _check_kline_entity_duplicate,
)
from services.data_deletion import ensure_data_deletion_tables
from services.data_sources.accepted_schema import ACCEPTED_PARTITION_DDL
from services.data_sources.nominal_ohlcv_schema import (
    CANONICAL_TABLE,
    DATASET_ID,
    PROVIDER_FIELDS,
)
from services.data_sources.security_day_partition import canonical_content_hash
from services.security_identity import CodeChangeEvent, CodeChangeSet


def _ccs(*events: CodeChangeEvent) -> CodeChangeSet:
    return CodeChangeSet(
        events=tuple(events),
        by_new={e.new_code: e for e in events},
        by_old={e.old_code: e for e in events},
        sha256="test-sha256",
    )


_EVENT = CodeChangeEvent(
    old_code="300114.SZ",
    new_code="302132.SZ",
    effective_date="20250217",
    exchange="SZSE",
    kind="reorg_rename",
    source_kind="announcement",
    source_ref="cninfo 2025-02-15 公告编号 2025-028",
    checked_at="2026-09-12",
)


# --------------------------------------------------------------------- fixtures --

def _amend_conn() -> duckdb.DuckDBPyConnection:
    """裸 canonical K 线表 (grain: ts_code+trade_date, provider_fields 全列) +
    accepted_partition 指针表 + 删除记账表。amend 脚本直连一个数据库, 不需要 ATTACH。"""

    conn = duck_mem()
    conn.execute(f"""
        CREATE TABLE {CANONICAL_TABLE} (
            ts_code VARCHAR, trade_date DATE, open DOUBLE, high DOUBLE, low DOUBLE,
            close DOUBLE, pre_close DOUBLE, change DOUBLE, pct_chg DOUBLE,
            vol DOUBLE, amount DOUBLE
        )
    """)
    conn.execute(ACCEPTED_PARTITION_DDL)
    ensure_data_deletion_tables(conn)
    return conn


def _row(ts_code: str, d: str, **overrides) -> tuple:
    base = {
        "ts_code": ts_code, "trade_date": d, "open": 10.0, "high": 10.0, "low": 10.0,
        "close": 10.0, "pre_close": 10.0, "change": 0.0, "pct_chg": 0.0,
        "vol": 100.0, "amount": 1000.0,
    }
    base.update(overrides)
    return tuple(base[f] for f in PROVIDER_FIELDS)


def _insert_rows(conn, rows: list[tuple]) -> None:
    placeholders = ", ".join("?" for _ in PROVIDER_FIELDS)
    cols = ", ".join(PROVIDER_FIELDS)
    conn.executemany(
        f"INSERT INTO {CANONICAL_TABLE} ({cols}) VALUES ({placeholders})", rows
    )


def _seed_pointer(conn, partition_value: str, row_count: int, *, content_hash: str = "seed") -> None:
    conn.execute(
        """
        INSERT INTO accepted_partition (
            dataset_id, partition_value, batch_id, contract_version, contract_hash,
            config_hash, row_count, content_hash, observed_at, available_at, accepted_at
        ) VALUES (?, ?, ?, '1', 'c', 'c', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [DATASET_ID, partition_value, f"batch-{partition_value}", row_count, content_hash],
    )


def _canonical_count(conn, *, ts_code: str | None = None) -> int:
    if ts_code is None:
        return int(conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0])
    return int(
        conn.execute(
            f"SELECT COUNT(*) FROM {CANONICAL_TABLE} WHERE ts_code = ?", [ts_code]
        ).fetchone()[0]
    )


def _pointer(conn, partition_value: str) -> tuple[int, str]:
    row = conn.execute(
        "SELECT row_count, content_hash FROM accepted_partition "
        "WHERE dataset_id = ? AND partition_value = ?",
        [DATASET_ID, partition_value],
    ).fetchone()
    assert row is not None, f"no pointer for {partition_value}"
    return int(row[0]), str(row[1])


def _ledger_rows(conn) -> list[tuple]:
    return conn.execute(
        "SELECT deleted_rows, verification_json FROM mart_data_deletion_record"
    ).fetchall()


def _seed_two_pre_effective_dates(conn) -> None:
    """new_code=302132.SZ 在 effective_date(20250217) 前两天各有一行, old_code=300114.SZ
    同期也有行 (twin, 两份历史并存); effective_date 当天及之后各留一行, 用来验证"当天及
    之后不许被删"。每天恰好 2 行 (old+new), 方便 accepted_partition.row_count 对账。"""

    _insert_rows(conn, [
        _row("302132.SZ", "2025-02-10"),
        _row("300114.SZ", "2025-02-10"),
        _row("302132.SZ", "2025-02-11"),
        _row("300114.SZ", "2025-02-11"),
        _row("302132.SZ", "2025-02-17"),  # effective_date 当天, 不许被删
        _row("302132.SZ", "2025-02-18"),  # effective_date 之后, 不许被删
    ])
    _seed_pointer(conn, "20250210", 2)
    _seed_pointer(conn, "20250211", 2)
    _seed_pointer(conn, "20250217", 1)
    _seed_pointer(conn, "20250218", 1)


# ------------------------------------------------------------------------- plan --

def test_plan_reports_dates_shared_old_code_and_total():
    conn = _amend_conn()
    _seed_two_pre_effective_dates(conn)
    p = plan(conn, _ccs(_EVENT))
    assert len(p.events) == 1
    ep = p.events[0]
    assert ep.new_code == "302132.SZ"
    assert ep.old_code == "300114.SZ"
    assert ep.dates == ("20250210", "20250211")
    assert ep.old_code_shared_dates == ("20250210", "20250211")
    assert p.total_rows == 2

    report = format_plan(p)
    assert "302132.SZ" in report
    assert "300114.SZ" in report
    assert "20250210" in report and "20250211" in report
    assert "两份历史并存" in report
    assert "合计: 2 行" in report


def test_dry_run_writes_nothing():
    """隔离用例: dry-run (只调 plan(), 不调 execute()) 不改表行数, 不写记账表。"""
    conn = _amend_conn()
    _seed_two_pre_effective_dates(conn)
    before_total = _canonical_count(conn)
    before_pointer_210 = _pointer(conn, "20250210")

    plan(conn, _ccs(_EVENT))

    assert _canonical_count(conn) == before_total
    assert _pointer(conn, "20250210") == before_pointer_210
    assert _ledger_rows(conn) == []


# ---------------------------------------------------------------------- execute --

def test_execute_deletes_correct_rows_updates_pointers_and_records_ledger():
    conn = _amend_conn()
    _seed_two_pre_effective_dates(conn)
    p = plan(conn, _ccs(_EVENT))

    result = execute(conn, p, run_id="run1")

    assert result.total_rows == 2
    # 删除后 new_code 在 effective_date 之前必须 0 行; 当天(0217)及之后(0218)两行保留。
    assert _canonical_count(conn, ts_code="302132.SZ") == 2
    remaining_dates = {
        str(r[0]) for r in conn.execute(
            f"SELECT trade_date FROM {CANONICAL_TABLE} WHERE ts_code = '302132.SZ'"
        ).fetchall()
    }
    assert "2025-02-10" not in remaining_dates
    assert "2025-02-11" not in remaining_dates

    for d in ("20250210", "20250211"):
        row_count, content_hash = _pointer(conn, d)
        assert row_count == 1  # 原 2 行减去被删的 new_code 那 1 行
        expected_rows = [
            dict(zip(PROVIDER_FIELDS, r))
            for r in conn.execute(
                f"SELECT {', '.join(PROVIDER_FIELDS)} FROM {CANONICAL_TABLE} "
                "WHERE trade_date = ?",
                [f"{d[:4]}-{d[4:6]}-{d[6:]}"],
            ).fetchall()
        ]
        assert canonical_content_hash(expected_rows, PROVIDER_FIELDS) == content_hash

    ledger = _ledger_rows(conn)
    assert len(ledger) == 1
    deleted_rows, verification_json = ledger[0]
    assert int(deleted_rows) == 2
    verification = json.loads(verification_json)
    assert verification["source_ref"] == _EVENT.source_ref
    assert verification["new_code"] == "302132.SZ"
    assert verification["old_code"] == "300114.SZ"


def test_effective_date_and_after_rows_survive():
    """隔离用例: effective_date 当天及之后的行不许被删 (变异钉子: 把 < 改成 <=
    会让 20250217 那行也被删, 本用例必须变红)。"""
    conn = _amend_conn()
    _seed_two_pre_effective_dates(conn)
    p = plan(conn, _ccs(_EVENT))
    execute(conn, p, run_id="run1")

    remaining_dates = {
        str(r[0]) for r in conn.execute(
            f"SELECT trade_date FROM {CANONICAL_TABLE} WHERE ts_code = '302132.SZ'"
        ).fetchall()
    }
    assert "2025-02-17" in remaining_dates
    assert "2025-02-18" in remaining_dates
    # effective_date 当天的 accepted_partition 指针不该被这次修正碰过。
    assert _pointer(conn, "20250217") == (1, "seed")


def test_old_code_rows_survive():
    """隔离用例: 旧码同期行不许被删。"""
    conn = _amend_conn()
    _seed_two_pre_effective_dates(conn)
    before = _canonical_count(conn, ts_code="300114.SZ")
    p = plan(conn, _ccs(_EVENT))
    execute(conn, p, run_id="run1")
    assert _canonical_count(conn, ts_code="300114.SZ") == before == 2


def test_no_op_when_event_table_empty():
    """隔离用例: 事件表为空时脚本是 no-op 而不是报错。"""
    conn = _amend_conn()
    _seed_two_pre_effective_dates(conn)
    before_total = _canonical_count(conn)

    empty_ccs = _ccs()
    p = plan(conn, empty_ccs)
    assert p.events == ()
    assert p.total_rows == 0
    assert "nothing to do" in format_plan(p)

    result = execute(conn, p, run_id="run-empty")
    assert result.total_rows == 0
    assert _canonical_count(conn) == before_total
    assert _ledger_rows(conn) == []


def test_no_op_when_event_has_zero_matching_rows():
    """事件已登记但当前表里 new_code 在 effective_date 前已经 0 行 (例如已清理过):
    该事件整体 no-op, 不写记账, 不报错。"""
    conn = _amend_conn()
    # 只插 effective_date 当天及之后的行, 不插任何 pre-effective 行。
    _insert_rows(conn, [_row("302132.SZ", "2025-02-17")])
    _seed_pointer(conn, "20250217", 1)

    p = plan(conn, _ccs(_EVENT))
    assert p.events[0].dates == ()
    result = execute(conn, p, run_id="run-noop")
    assert result.total_rows == 0
    assert _ledger_rows(conn) == []


def test_mismatch_between_plan_and_actual_rolls_back_and_raises():
    """隔离用例: 删除行数与预告不等时 ROLLBACK 并抛 (写库前断言)。用一个手工构造、
    比实际少报一天的 AmendPlan 模拟"计划与执行之间数据发生了变化"; 断言消息里的
    关键词只属于这条前置断言 (不是"删除后仍有行早于"或"指针与内容脱节"那两条),
    确保这条断言真的被删掉时本用例会变红, 而不是被别的断言顶替蒙混过关。"""
    conn = _amend_conn()
    _seed_two_pre_effective_dates(conn)
    real_plan = plan(conn, _ccs(_EVENT))
    real_ep = real_plan.events[0]
    assert real_ep.dates == ("20250210", "20250211")

    wrong_ep = AmendEventPlan(
        old_code=real_ep.old_code,
        new_code=real_ep.new_code,
        effective_date=real_ep.effective_date,
        source_ref=real_ep.source_ref,
        dates=("20250210",),  # 实际有 2 天, 这里只报 1 天
        old_code_shared_dates=("20250210",),
    )
    wrong_plan = AmendPlan(events=(wrong_ep,))

    with pytest.raises(AmendMismatchError, match="写库时实测"):
        execute(conn, wrong_plan, run_id="run-mismatch")

    # 断言未发生任何写: canonical/pointer/ledger 全部保持原状。
    assert _canonical_count(conn, ts_code="302132.SZ") == 4
    assert _pointer(conn, "20250210") == (2, "seed")
    assert _pointer(conn, "20250211") == (2, "seed")
    assert _ledger_rows(conn) == []


def test_mid_event_second_date_failure_rolls_back_first_date_too():
    """隔离用例 (事务边界): 同一事件里第 1 天正常处理完 (删除+更新指针, 尚未提交),
    第 2 天因 accepted_partition 指针与实际内容对不上而抛错——整个事务必须回滚,
    第 1 天的删除/指针更新也要被撤销 (变异钉子: 删掉 BEGIN/COMMIT/ROLLBACK 包裹后,
    DuckDB 逐语句自动提交, 第 1 天的改动会永久生效, 本用例必须变红)。"""
    conn = _amend_conn()
    _seed_two_pre_effective_dates(conn)
    p = plan(conn, _ccs(_EVENT))
    assert p.events[0].dates == ("20250210", "20250211")

    # 把第 2 天的指针改成一个荒谬值, 让 old_row_count-1 != 删除后剩余行数。
    conn.execute(
        "UPDATE accepted_partition SET row_count = 999 "
        "WHERE dataset_id = ? AND partition_value = '20250211'",
        [DATASET_ID],
    )

    with pytest.raises(AmendMismatchError, match="指针与内容脱节"):
        execute(conn, p, run_id="run-mid-fail")

    # 第 1 天 (20250210) 的删除必须也被回滚: new_code 那行还在, 指针还是原值。
    remaining = {
        str(r[0]) for r in conn.execute(
            f"SELECT trade_date FROM {CANONICAL_TABLE} WHERE ts_code = '302132.SZ'"
        ).fetchall()
    }
    assert "2025-02-10" in remaining
    assert _pointer(conn, "20250210") == (2, "seed")
    assert _ledger_rows(conn) == []


def test_missing_accepted_partition_pointer_raises_and_rolls_back():
    """隔离用例: 某受影响日在 accepted_partition 里根本没有指针 (数据状态本身不一致)
    时必须 fail-closed, 不能悄悄跳过不更新。"""
    conn = _amend_conn()
    _insert_rows(conn, [
        _row("302132.SZ", "2025-02-10"),
        _row("300114.SZ", "2025-02-10"),
    ])
    # 故意不建 20250210 的 accepted_partition 指针。
    p = plan(conn, _ccs(_EVENT))
    with pytest.raises(AmendMismatchError, match="no accepted_partition pointer"):
        execute(conn, p, run_id="run-no-pointer")
    assert _canonical_count(conn, ts_code="302132.SZ") == 1


# ------------------------------------------------------------- kline_entity_duplicate --

def _audit_conn() -> duckdb.DuckDBPyConnection:
    """裸 duckdb 连接 + ATTACH 出的 tushare_raw 别名, 镜像 data_audit._open_conn 的
    连接形状 (两条新审计的 SQL 都以 tushare_raw.<kline_table> 为源)。"""

    conn = duckdb.connect()
    conn.execute("ATTACH ':memory:' AS tushare_raw")
    conn.execute(
        "CREATE TABLE tushare_raw.canonical_nominal_ohlcv_daily "
        "(ts_code VARCHAR, trade_date DATE, close DOUBLE, pre_close DOUBLE, "
        "vol DOUBLE, amount DOUBLE)"
    )
    return conn


def test_entity_duplicate_unregistered_pair_fails():
    """A1: 一对未登记全同 1 天 -> FAIL。"""
    conn = _audit_conn()
    conn.execute(
        "INSERT INTO tushare_raw.canonical_nominal_ohlcv_daily VALUES "
        "('111111.SZ', DATE '2024-01-02', 10.0, 9.9, 100.0, 1000.0), "
        "('222222.SZ', DATE '2024-01-02', 10.0, 9.9, 100.0, 1000.0)"
    )
    result = _check_kline_entity_duplicate(conn, ccs=_ccs())
    assert result.status == "FAIL", result.detail
    assert "unregistered" in result.detail


def test_entity_duplicate_registered_valid_interval_passes():
    """A2: 已登记且区间合法 -> PASS."""
    conn = _audit_conn()
    conn.execute(
        "INSERT INTO tushare_raw.canonical_nominal_ohlcv_daily VALUES "
        "('111111.SZ', DATE '2024-01-02', 10.0, 9.9, 100.0, 1000.0), "
        "('222222.SZ', DATE '2024-01-02', 10.0, 9.9, 100.0, 1000.0)"
    )
    event = CodeChangeEvent(
        old_code="111111.SZ", new_code="222222.SZ", effective_date="20240103",
        exchange="SZSE", kind="reorg_rename", source_kind="kline_succession_observed",
        source_ref="test", checked_at="2026-09-12",
    )
    result = _check_kline_entity_duplicate(conn, ccs=_ccs(event))
    assert result.status == "PASS", result.detail


def test_entity_duplicate_registered_but_interval_overruns_effective_date_fails():
    """已登记但重复区间越过 effective_date -> 仍 FAIL (前提锁不因"有登记"就放行
    任意区间)。"""
    conn = _audit_conn()
    conn.execute(
        "INSERT INTO tushare_raw.canonical_nominal_ohlcv_daily VALUES "
        "('111111.SZ', DATE '2024-01-02', 10.0, 9.9, 100.0, 1000.0), "
        "('222222.SZ', DATE '2024-01-02', 10.0, 9.9, 100.0, 1000.0)"
    )
    event = CodeChangeEvent(
        old_code="111111.SZ", new_code="222222.SZ", effective_date="20240101",  # 早于重复区间
        exchange="SZSE", kind="reorg_rename", source_kind="kline_succession_observed",
        source_ref="test", checked_at="2026-09-12",
    )
    result = _check_kline_entity_duplicate(conn, ccs=_ccs(event))
    assert result.status == "FAIL", result.detail


# ------------------------------------------------------------- kline_code_succession --

def test_code_succession_unregistered_candidate_fails():
    """A3: 后继候选未登记 -> FAIL."""
    conn = _audit_conn()
    conn.execute(
        "INSERT INTO tushare_raw.canonical_nominal_ohlcv_daily VALUES "
        "('333333.SZ', DATE '2024-01-05', 10.0, 9.9, 100.0, 1000.0), "
        "('444444.SZ', DATE '2024-01-08', 11.0, 10.0, 100.0, 1000.0)"
    )
    result = _check_kline_code_succession(conn, ccs=_ccs())
    assert result.status == "FAIL", result.detail
    assert "333333.SZ->444444.SZ" in result.detail


def test_code_succession_registered_candidate_passes():
    """A4: 后继候选已登记 -> PASS."""
    conn = _audit_conn()
    conn.execute(
        "INSERT INTO tushare_raw.canonical_nominal_ohlcv_daily VALUES "
        "('333333.SZ', DATE '2024-01-05', 10.0, 9.9, 100.0, 1000.0), "
        "('444444.SZ', DATE '2024-01-08', 11.0, 10.0, 100.0, 1000.0)"
    )
    event = CodeChangeEvent(
        old_code="333333.SZ", new_code="444444.SZ", effective_date="20240108",
        exchange="SZSE", kind="reorg_rename", source_kind="kline_succession_observed",
        source_ref="test", checked_at="2026-09-12",
    )
    result = _check_kline_code_succession(conn, ccs=_ccs(event))
    assert result.status == "PASS", result.detail


def test_code_succession_price_not_adjacent_is_not_a_candidate():
    """A5: 价格不邻接 (差 0.02 > tolerance 0.01) -> 根本不算候选, PASS (即便未登记)。"""
    conn = _audit_conn()
    conn.execute(
        "INSERT INTO tushare_raw.canonical_nominal_ohlcv_daily VALUES "
        "('333333.SZ', DATE '2024-01-05', 10.0, 9.9, 100.0, 1000.0), "
        "('444444.SZ', DATE '2024-01-08', 11.0, 10.02, 100.0, 1000.0)"
    )
    result = _check_kline_code_succession(conn, ccs=_ccs())
    assert result.status == "PASS", result.detail
