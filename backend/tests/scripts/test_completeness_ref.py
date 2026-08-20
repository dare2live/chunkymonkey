"""同日行数对账门的语义测试。

存在理由(2026-08-18 实测): min_rows_per_batch 只能检出"明显残缺", 逻辑上不可能证明完整 ——
它回答不了"应该有多少行"。daily_basic 底线 3,000 而真值 5,197: 底线丢 42% 才报, 对账丢 1 行就报。
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_continuity_integrity as cc  # noqa: E402


DAYS = ["20260105", "20260106", "20260107", "20260108"]


def _fixture(mine_per_day: dict[str, int], ref_per_day: dict[str, int]):
    conn = duckdb.connect(":memory:")
    conn.execute("create table raw_tushare_daily (trade_date VARCHAR)")
    conn.execute("create table mine (trade_date VARCHAR)")
    for day, n in ref_per_day.items():
        for _ in range(n):
            conn.execute("insert into raw_tushare_daily values (?)", [day])
    for day, n in mine_per_day.items():
        for _ in range(n):
            conn.execute("insert into mine values (?)", [day])
    return conn


def _spec(**over):
    spec = {
        "domain": "mine", "db": "tushare_raw", "table": "mine",
        "freshness_date_column": "trade_date", "date_param": None,
        "completeness_ref": {
            "kind": "same_day_row_count", "ref_domain": "daily",
            "tolerance": 0, "verified_since": "20260101", "evidence": "test",
        },
    }
    spec.update(over)
    return spec


def test_matching_row_counts_pass():
    counts = {d: 10 for d in DAYS}
    conn = _fixture(counts, counts)

    got = cc.check_completeness_ref(conn, _spec(), DAYS, DAYS[-1])

    assert got["status"] == "pass", got


def test_one_missing_row_is_caught():
    """丢 1 行就报 —— 这正是对账相对行数下界的价值。"""
    ref = {d: 10 for d in DAYS}
    mine = dict(ref, **{"20260107": 9})
    conn = _fixture(mine, ref)

    got = cc.check_completeness_ref(conn, _spec(), DAYS, DAYS[-1])

    assert got["status"] == "fail_row_count_mismatch", got
    assert "20260107" in got["detail"], got["detail"]


def test_a_day_absent_from_our_table_is_caught_not_skipped():
    """整天缺失也必须报: left join 后本域计 0, 不能因为没有行就跳过这天。"""
    ref = {d: 10 for d in DAYS}
    mine = {d: 10 for d in DAYS if d != "20260106"}
    conn = _fixture(mine, ref)

    got = cc.check_completeness_ref(conn, _spec(), DAYS, DAYS[-1])

    assert got["status"] == "fail_row_count_mismatch", got
    assert "20260106" in got["detail"], got["detail"]


def test_differences_before_verified_since_are_not_reported():
    """verified_since 之前的差额是 vendor 历史覆盖差异, 强制它会制造幻影缺口。

    实测依据: moneyflow 与 daily 在 2020-2024 恒 0 率为 0(差额达 -23), 2026 年才 100% 一致。
    按"必须为 0"无差别设门会天天报红, 而那不是我们的缺口。
    """
    ref = {d: 10 for d in DAYS}
    mine = dict(ref, **{"20260105": 3})          # 差异落在生效起点之前
    conn = _fixture(mine, ref)

    spec = _spec()
    spec["completeness_ref"] = {**spec["completeness_ref"], "verified_since": "20260106"}
    got = cc.check_completeness_ref(conn, spec, DAYS, DAYS[-1])

    assert got["status"] == "pass", got


def test_declaration_without_verified_since_is_refused():
    """缺 verified_since 不能"宽容放行" —— 未核证区间的对账会把供应商差异误判成缺陷。

    2026-08-18 实证: 我据"dc_member 板块数应等于 dc_index"判定某日缺 342 个板块,
    向 vendor 核实后发现是 vendor 三个接口历史覆盖本就不同, 我们拉到的是全部。
    """
    counts = {d: 10 for d in DAYS}
    conn = _fixture(counts, counts)

    spec = _spec()
    spec["completeness_ref"] = {k: v for k, v in spec["completeness_ref"].items()
                                if k != "verified_since"}
    got = cc.check_completeness_ref(conn, spec, DAYS, DAYS[-1])

    assert got["status"] == "fail_bad_declaration", got


def test_unknown_ref_domain_is_refused_not_silently_skipped():
    """基准域解析不了要报错, 不能静默跳过 —— 静默跳过就是一道永远绿的门。"""
    counts = {d: 10 for d in DAYS}
    conn = _fixture(counts, counts)

    spec = _spec()
    spec["completeness_ref"] = {**spec["completeness_ref"], "ref_domain": "not_a_domain"}
    got = cc.check_completeness_ref(conn, spec, DAYS, DAYS[-1])

    assert got["status"] == "fail_bad_declaration", got


def test_domains_without_declaration_are_skipped_explicitly():
    """未声明对账的域明确标 skipped, 而不是伪装成 pass。"""
    counts = {d: 10 for d in DAYS}
    conn = _fixture(counts, counts)

    got = cc.check_completeness_ref(conn, _spec(completeness_ref=None), DAYS, DAYS[-1])

    assert got["status"] == "skipped_not_declared", got


@pytest.mark.parametrize("tolerance,expected", [(0, "fail_row_count_mismatch"), (1, "pass")])
def test_tolerance_is_honoured(tolerance, expected):
    ref = {d: 10 for d in DAYS}
    mine = dict(ref, **{"20260107": 9})
    conn = _fixture(mine, ref)

    spec = _spec()
    spec["completeness_ref"] = {**spec["completeness_ref"], "tolerance": tolerance}
    got = cc.check_completeness_ref(conn, spec, DAYS, DAYS[-1])

    assert got["status"] == expected, got
