"""全历史行数塌陷扫描的判据测试。

现有 check_cross_section 只看近 60 交易日, 历史异常一旦滑出窗口就永久失查;
_dip_scan.scan_full_history 补上全历史扫描能力, 这里验证它的核心判据:
邻域中位数比值判塌陷 + known_empty 排除实测真空日, 避免重报永不收敛。
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from _dip_scan import scan_full_history  # noqa: E402


def _dates(n: int) -> list[str]:
    return [f"202601{d:02d}" for d in range(1, n + 1)]


def _table(conn, name: str, per_day: dict[str, int]) -> None:
    conn.execute(f'create table "{name}" (trade_date VARCHAR)')
    for day, n in per_day.items():
        for _ in range(n):
            conn.execute(f'insert into "{name}" values (?)', [day])


def test_a_collapsed_day_is_reported():
    conn = duckdb.connect(":memory:")
    days = {d: 100 for d in _dates(21)}
    collapsed_day = _dates(21)[10]  # 第 11 天
    days[collapsed_day] = 3
    _table(conn, "t", days)

    got = scan_full_history(conn, "t", "trade_date")

    assert len(got) == 1, got
    assert got[0]["date"] == collapsed_day, got


def test_known_empty_day_is_excluded():
    conn = duckdb.connect(":memory:")
    days = {d: 100 for d in _dates(21)}
    collapsed_day = _dates(21)[10]
    days[collapsed_day] = 3
    _table(conn, "t", days)

    got = scan_full_history(conn, "t", "trade_date", known_empty={collapsed_day})

    assert got == []


def test_no_collapse_returns_empty():
    conn = duckdb.connect(":memory:")
    days = {d: 100 for d in _dates(21)}
    _table(conn, "t", days)

    got = scan_full_history(conn, "t", "trade_date")

    assert got == []


def test_empty_table_returns_empty_not_raises():
    conn = duckdb.connect(":memory:")
    conn.execute('create table "t" (trade_date VARCHAR)')

    got = scan_full_history(conn, "t", "trade_date")

    assert got == []


def test_missing_table_returns_empty_not_raises():
    conn = duckdb.connect(":memory:")

    got = scan_full_history(conn, "no_such_table", "trade_date")

    assert got == []


def test_result_dict_has_required_keys():
    conn = duckdb.connect(":memory:")
    days = {d: 100 for d in _dates(21)}
    collapsed_day = _dates(21)[10]
    days[collapsed_day] = 3
    _table(conn, "t", days)

    got = scan_full_history(conn, "t", "trade_date")

    assert len(got) == 1
    assert set(got[0].keys()) == {"date", "rows", "neighbor_median", "ratio"}


def test_multiple_collapsed_days_returned_ascending():
    conn = duckdb.connect(":memory:")
    all_days = _dates(31)
    days = {d: 100 for d in all_days}
    first_collapse = all_days[10]
    second_collapse = all_days[20]
    days[first_collapse] = 3
    days[second_collapse] = 5
    _table(conn, "t", days)

    got = scan_full_history(conn, "t", "trade_date")

    assert [r["date"] for r in got] == [first_collapse, second_collapse], got
