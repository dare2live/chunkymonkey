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
    conn.execute("create table canonical_nominal_ohlcv_daily (trade_date VARCHAR)")
    conn.execute("create table mine (trade_date VARCHAR)")
    for day, n in ref_per_day.items():
        for _ in range(n):
            conn.execute("insert into canonical_nominal_ohlcv_daily values (?)", [day])
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


# ── 标的集合差 (2026-08-22 实锤: 基准{A,B,C}/本域{A,B,X} 行数都=3, 互相抵消判 pass) ─────────
#
# 上面 9 个测试全部只造 (trade_date) 单列表, 不带标的列, 覆盖的是"行数比对"这条判据路径。
# 下面的测试需要能按标的对齐两张表, 所以另起一个带 ts_code 列的 fixture, 不改上面的 _fixture
# (它的表结构是那 9 个既有测试的契约, 改了就是改测试)。


def _fixture_with_codes(mine: dict[str, list[str]], ref: dict[str, list[str]]):
    """同 _fixture, 但两张表都带 ts_code 列, 用于标的集合差测试。"""
    conn = duckdb.connect(":memory:")
    conn.execute("create table canonical_nominal_ohlcv_daily (trade_date VARCHAR, ts_code VARCHAR)")
    conn.execute("create table mine (trade_date VARCHAR, ts_code VARCHAR)")
    for day, codes in ref.items():
        for c in codes:
            conn.execute("insert into canonical_nominal_ohlcv_daily values (?, ?)", [day, c])
    for day, codes in mine.items():
        for c in codes:
            conn.execute("insert into mine values (?, ?)", [day, c])
    return conn


def test_code_set_mismatch_with_equal_row_count_is_caught():
    """行数相同不代表标的相同 —— 少一只/多一只互相抵消, 纯行数比对必然漏检。"""
    ref = {d: ["A", "B", "C"] for d in DAYS}
    mine = {d: ["A", "B", "C"] for d in DAYS}
    mine["20260107"] = ["A", "B", "X"]   # 3 行 vs 3 行, 但 C 换成了 X
    conn = _fixture_with_codes(mine, ref)

    spec = _spec()
    spec["grain"] = ["ts_code", "trade_date"]
    got = cc.check_completeness_ref(conn, spec, DAYS, DAYS[-1])

    assert got["status"] == "fail_code_set_mismatch", got
    assert "C" in got["detail"], got["detail"]
    assert "X" in got["detail"], got["detail"]


def test_code_sets_matching_is_pass():
    ref = {d: ["A", "B", "C"] for d in DAYS}
    mine = {d: ["A", "B", "C"] for d in DAYS}
    conn = _fixture_with_codes(mine, ref)

    spec = _spec()
    spec["grain"] = ["ts_code", "trade_date"]
    got = cc.check_completeness_ref(conn, spec, DAYS, DAYS[-1])

    assert got["status"] == "pass", got


def test_row_count_mismatch_still_wins_over_code_set_mismatch():
    """行数不符时必须仍报 fail_row_count_mismatch —— 集合差不能抢走行数判据的判定。"""
    ref = {d: ["A", "B", "C"] for d in DAYS}
    mine = {d: ["A", "B", "C"] for d in DAYS}
    mine["20260107"] = ["A", "B"]   # 少一行, 同时标的集合也不同
    conn = _fixture_with_codes(mine, ref)

    spec = _spec()
    spec["grain"] = ["ts_code", "trade_date"]
    got = cc.check_completeness_ref(conn, spec, DAYS, DAYS[-1])

    assert got["status"] == "fail_row_count_mismatch", got


@pytest.mark.parametrize("bad_grain", [[], ["ts_code", "trade_date", "extra_col"]])
def test_unsupported_grain_falls_back_to_row_count_only(bad_grain):
    """grain 缺失, 或除日期列外不止一列 —— 不猜标的列, 回落成纯行数比对并注明。"""
    ref = {d: ["A", "B", "C"] for d in DAYS}
    mine = {d: ["A", "B", "C"] for d in DAYS}
    conn = _fixture_with_codes(mine, ref)

    spec = _spec()
    spec["grain"] = bad_grain
    got = cc.check_completeness_ref(conn, spec, DAYS, DAYS[-1])

    assert got["status"] == "pass", got
    assert "未做集合差" in got["detail"], got["detail"]


def test_day_absent_from_ref_entirely_is_not_reported_as_extra():
    """ref 该日本身 0 行 (基准表停更型) 不能把 mine 全部标的误判成"多出"。

    2026-08-22 真实数据实跑实锤: canonical_nominal_ohlcv_daily (completeness_ref 唯一基准表) 是
    legacy/停更表, 20260716 后再没写入, 而 daily_basic/moneyflow 之后仍持续新鲜。行数比对
    的 LEFT JOIN 锚在 ref 分组结果上, ref 当日 0 行时那天根本不出现在结果集里, 天然跳过；
    集合差查询若不复刻同一语义, 会把 ref 断更之后的每个交易日都判成 mine "多出 5541 个"
    ——那是基准断流, 不是本域真的多出标的。
    """
    ref = {d: ["A", "B", "C"] for d in DAYS if d != "20260107"}   # ref 该日 0 行 (未写入)
    mine = {d: ["A", "B", "C"] for d in DAYS}                      # mine 照常有数据
    conn = _fixture_with_codes(mine, ref)

    spec = _spec()
    spec["grain"] = ["ts_code", "trade_date"]
    got = cc.check_completeness_ref(conn, spec, DAYS, DAYS[-1])

    assert got["status"] == "pass", got
