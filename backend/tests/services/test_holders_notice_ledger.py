"""holders_notice_fetch_ledger 单测 (刀 B1; spec_holders_pagination.md §4.4)。

每条用例只违反一个门控条件, 其它条件全部满足 (mio/CLAUDE.md 测试纪律)。
真 tmp DuckDB, 不 monkeypatch 被测函数。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import re  # noqa: E402
from uuid import uuid4  # noqa: E402

import duckdb  # noqa: E402
import pytest  # noqa: E402

from services.holders_notice_ledger import (  # noqa: E402
    HoldersLedgerFloorError,
    LedgerRow,
    append_ledger,
    ensure_holders_notice_ledger,
    plan_due_notice_days,
    settle_stats,
    settled_notice_days,
)


def _conn():
    con = duckdb.connect(":memory:")
    ensure_holders_notice_ledger(con)
    return con


@pytest.fixture(params=["Asia/Shanghai", "UTC"])
def ledger_conn(request):
    """T4 (返修 blocking finding): settled / 到期判定用例在两种会话时区下各跑
    一遍, 断言结果与上海时区下一致——本机默认会话时区恰好就是 Asia/Shanghai,
    `AT TIME ZONE 'Asia/Shanghai'` 的显式转换与隐式 CAST 在这台机器上算出同一个
    日期, 去掉显式转换的回归 (M1) 只有在会话时区不是 Asia/Shanghai 时才会被
    抓到 (CI 也在这台上海时区机器上跑, 光靠默认会话跑不出这道守卫)。"""
    con = duckdb.connect(":memory:")
    con.execute(f"SET TimeZone='{request.param}'")
    ensure_holders_notice_ledger(con)
    return con


def _utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def _row(**overrides):
    base = dict(
        ledger_id=uuid4().hex, notice_date="20260820", fetched_at=_utc(2026, 8, 20, 12, 0),
        run_kind="daily", scope="day", outcome="complete", missing_rows=0,
    )
    base.update(overrides)
    return LedgerRow(**base)


# ── ensure_holders_notice_ledger / DDL shape ────────────────────────────


def test_ensure_creates_table_idempotently():
    con = _conn()
    ensure_holders_notice_ledger(con)  # 第二次调用不许炸
    cols = {r[0] for r in con.execute("DESCRIBE holders_notice_fetch_ledger").fetchall()}
    assert {"ledger_id", "notice_date", "fetched_at", "run_kind", "scope", "stock_code",
            "outcome", "reason", "missing_rows", "batch_ids"} <= cols


def test_module_source_has_no_update_or_delete_sql():
    """append-only: 源码不得含 UPDATE / DELETE SQL (B18 静态钉)。

    只扫真实 SQL 关键字用法(后随标识符), 不误判 docstring 里提到这两个词的
    散文 (例如本文件自己的 module docstring 就写了"不得含 UPDATE / DELETE")。
    """
    import services.holders_notice_ledger as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert re.search(r"\bUPDATE\s+[A-Za-z_{]", src) is None, "源码含 UPDATE SQL 语句"
    assert re.search(r"\bDELETE\s+FROM\b", src) is None, "源码含 DELETE SQL 语句"


def test_ddl_has_no_same_day_probe_column():
    """same_day_probe 不存在 (T7)."""
    con = _conn()
    cols = {r[0] for r in con.execute("DESCRIBE holders_notice_fetch_ledger").fetchall()}
    assert "same_day_probe" not in cols


# ── append_ledger: 每个校验条件各一条隔离用例 + 一次全满足对照 ───────────


def test_append_ledger_happy_path_writes_one_row():
    con = _conn()
    append_ledger(con, _row())
    n = con.execute("SELECT COUNT(*) FROM holders_notice_fetch_ledger").fetchone()[0]
    assert n == 1


def test_append_ledger_rejects_naive_datetime():
    """fetched_at 必须 timezone-aware (T4)."""
    con = _conn()
    naive = datetime(2026, 8, 20, 12, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        append_ledger(con, _row(fetched_at=naive))
    assert con.execute("SELECT COUNT(*) FROM holders_notice_fetch_ledger").fetchone()[0] == 0


def test_append_ledger_accepts_aware_datetime():
    """对照: aware 的正常写入 (B18e 后半)."""
    con = _conn()
    append_ledger(con, _row(fetched_at=_utc(2026, 8, 20, 12, 0)))
    assert con.execute("SELECT COUNT(*) FROM holders_notice_fetch_ledger").fetchone()[0] == 1


def test_append_ledger_rejects_bad_run_kind():
    con = _conn()
    with pytest.raises(ValueError, match="run_kind"):
        append_ledger(con, _row(run_kind="weekly"))


def test_append_ledger_rejects_bad_scope():
    con = _conn()
    with pytest.raises(ValueError, match="scope"):
        append_ledger(con, _row(scope="week"))


def test_append_ledger_rejects_bad_outcome():
    con = _conn()
    with pytest.raises(ValueError, match="outcome"):
        append_ledger(con, _row(outcome="partial"))


def test_append_ledger_scope_stock_requires_stock_code():
    con = _conn()
    with pytest.raises(ValueError, match="stock_code"):
        append_ledger(con, _row(scope="stock", stock_code=None))


def test_append_ledger_scope_day_forbids_stock_code():
    con = _conn()
    with pytest.raises(ValueError, match="stock_code"):
        append_ledger(con, _row(scope="day", stock_code="600000"))


def test_append_ledger_scope_stock_with_code_ok():
    """对照: scope='stock' 带 stock_code 正常写入."""
    con = _conn()
    append_ledger(con, _row(scope="stock", stock_code="600000", run_kind="reland"))
    assert con.execute("SELECT COUNT(*) FROM holders_notice_fetch_ledger").fetchone()[0] == 1


# ── settled_notice_days: B18 六条性质 + V1 ──────────────────────────────


def test_settled_requires_clean_fetch_after_settle_days(ledger_conn):
    """(ii)+(vi) 干净取数在 D+1 07:40 CST(=D 23:40 UTC)后 -> D settled。"""
    con = ledger_conn
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 20, 23, 40),
                             outcome="complete", missing_rows=0))
    assert "20260820" in settled_notice_days(con, settle_days=1)


def test_settled_before_settle_days_not_settled(ledger_conn):
    """按 UTC 日期判会误判 settled -> 变异靶点。"""
    con = ledger_conn
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 20, 12, 0),
                             outcome="complete", missing_rows=0))
    assert "20260820" not in settled_notice_days(con, settle_days=1)


def test_failed_never_settles_alone(ledger_conn):
    """(ii) failed 永不使一天 settled。"""
    con = ledger_conn
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 22, 0, 0),
                             outcome="failed", reason="count_drift", missing_rows=None))
    assert "20260820" not in settled_notice_days(con, settle_days=1)


def test_complete_with_missing_rows_not_settled(ledger_conn):
    """(iii) S1: complete 且 missing_rows>0 不 settle -- 那次正在接住迟到行。"""
    con = ledger_conn
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 22, 0, 0),
                             outcome="complete", missing_rows=3))
    assert "20260820" not in settled_notice_days(con, settle_days=1)
    # 对照: 同一天再补一条干净的 -> settled
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 23, 0, 0),
                             outcome="complete", missing_rows=0))
    assert "20260820" in settled_notice_days(con, settle_days=1)


def test_revised_surplus_moved_dup_do_not_block_settle(ledger_conn):
    """(iv) 其它计数非零不阻塞 settle (0731 这种带永久修订的分区要能 settle 掉)。"""
    con = ledger_conn
    append_ledger(con, _row(
        notice_date="20260731", fetched_at=_utc(2026, 8, 1, 23, 40), outcome="complete",
        missing_rows=0, revised_rows=4, surplus_rows=49, moved_rows=0, dup_rows=3,
    ))
    assert "20260731" in settled_notice_days(con, settle_days=1)


def test_stock_scope_rows_do_not_participate(ledger_conn):
    """(v) scope='stock' 的行不参与 settled 判定 (B7e)。"""
    con = ledger_conn
    append_ledger(con, _row(
        notice_date="20260820", scope="stock", stock_code="600000", run_kind="reland",
        fetched_at=_utc(2026, 8, 25, 0, 0), outcome="complete", missing_rows=0,
    ))
    assert "20260820" not in settled_notice_days(con, settle_days=1)


def test_settled_is_monotone_after_later_failed(ledger_conn):
    """(i) 单调: settled 之后再来一条 failed 不撤销 (B18d)。"""
    con = ledger_conn
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 21, 0, 0),
                             outcome="complete", missing_rows=0))
    assert "20260820" in settled_notice_days(con, settle_days=1)
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 25, 0, 0),
                             outcome="failed", reason="probe_error", missing_rows=None))
    assert "20260820" in settled_notice_days(con, settle_days=1)


def test_empty_with_local_rows_before_nonzero_does_not_settle(ledger_conn):
    """V1: empty 只在 local_rows_before=0 时算数 -- 本地已有行时供应商这次
    返回 9201 不代表这天真的没数据。"""
    con = ledger_conn
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 21, 0, 0),
                             outcome="empty", missing_rows=None, local_rows_before=5))
    assert "20260820" not in settled_notice_days(con, settle_days=1)


def test_empty_with_zero_local_rows_settles(ledger_conn):
    """对照: local_rows_before=0 (或 NULL) 时 empty 正常 settle。"""
    con = ledger_conn
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 21, 0, 0),
                             outcome="empty", missing_rows=None, local_rows_before=0))
    assert "20260820" in settled_notice_days(con, settle_days=1)


# ── plan_due_notice_days: 到期集合 + floor 守卫 ─────────────────────────


def test_first_run_due_is_calendar_minus_settled(monkeypatch, ledger_conn):
    """B24 (T3): 账本空, floor F, provider_max=F+50, 到期 = 从 F 起的 max_days
    个**日历日**(含周末)。"""
    con = ledger_conn
    due = plan_due_notice_days(
        con, provider_max="20260701", settle_days=1, floor="20260601", max_days=40,
    )
    assert due[0] == "20260601"
    assert len(due) == 31  # 06-01..07-01 闭区间共 31 天, 40 上限未触发
    assert due[-1] == "20260701"
    assert due == sorted(due)


def test_plan_due_caps_at_max_days(ledger_conn):
    con = ledger_conn
    due = plan_due_notice_days(
        con, provider_max="20260901", settle_days=1, floor="20260601", max_days=10,
    )
    assert len(due) == 10
    assert due[0] == "20260601"


def test_failed_day_stays_due_forever(ledger_conn):
    """B7b/R3: 失败的日子永远留在到期集合里, 不被后一天的成功越过。"""
    con = ledger_conn
    append_ledger(con, _row(notice_date="20260701", fetched_at=_utc(2026, 7, 3, 0, 0),
                             outcome="failed", reason="count_drift", missing_rows=None))
    append_ledger(con, _row(notice_date="20260702", fetched_at=_utc(2026, 7, 3, 0, 0),
                             outcome="complete", missing_rows=0))
    due = plan_due_notice_days(
        con, provider_max="20260702", settle_days=1, floor="20260701", max_days=40,
    )
    assert "20260701" in due
    assert "20260702" not in due


def test_floor_guard_raises_on_unsettled_predecessor(ledger_conn):
    """B23/S6: floor 之前存在未 settled 的 scope='day' 分区 -> 抛
    HoldersLedgerFloorError。"""
    con = ledger_conn
    append_ledger(con, _row(notice_date="20260630", fetched_at=_utc(2026, 7, 1, 0, 0),
                             outcome="failed", reason="blocked", missing_rows=None))
    with pytest.raises(HoldersLedgerFloorError):
        plan_due_notice_days(
            con, provider_max="20260710", settle_days=1, floor="20260701", max_days=40,
        )


def test_floor_guard_passes_once_predecessor_settled(ledger_conn):
    """对照: floor 之前的日期一旦 settled, 不再挡。"""
    con = ledger_conn
    append_ledger(con, _row(notice_date="20260630", fetched_at=_utc(2026, 7, 2, 0, 0),
                             outcome="complete", missing_rows=0))
    due = plan_due_notice_days(
        con, provider_max="20260702", settle_days=1, floor="20260701", max_days=40,
    )
    assert due == ["20260701", "20260702"]


# ── settle_stats: 零网络, 只报不判 ───────────────────────────────────────


def test_settle_stats_reports_lag_and_post_settle_missing():
    con = _conn()
    # D 当晚 (Beijing 20:00 = UTC 12:00, 仍是 D 当天本地日期) -- 还没到 settle_days。
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 20, 12, 0),
                             outcome="complete", missing_rows=10))
    # D+1 07:40 Beijing = D 23:40 UTC -- settling 那次, 干净。
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 20, 23, 40),
                             outcome="complete", missing_rows=0))
    # D+3 08:00 Beijing -- settled 之后又发现了迟到行。
    append_ledger(con, _row(notice_date="20260820", fetched_at=_utc(2026, 8, 22, 16, 0),
                             outcome="complete", missing_rows=2))
    stats = settle_stats(con, settle_days=1)
    assert len(stats) == 1
    assert stats[0]["notice_date"] == "20260820"
    assert stats[0]["post_settle_missing_fetches"] == 1
    assert isinstance(stats[0]["settle_lag_days"], int)


def test_settle_stats_empty_when_nothing_settled():
    con = _conn()
    assert settle_stats(con, settle_days=1) == []
