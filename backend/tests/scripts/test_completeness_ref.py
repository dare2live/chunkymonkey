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


# ── 冻结域窗口截断 (2026-09-18, fable 设计, project cut_frozen_domain_verdicts) ──────────
#
# moneyflow/daily_basic 09-18 真实审计实测: execution_policy.mode=disabled 后域自己不会再
# 有新行, 而 ref_domain(daily) 天天在长 —— 旧判据拿两者逐日比对, 冻结之后的每一天都必然
# fail_row_count_mismatch, 而且永远不会好(这不是"待修的缺口", 是"域已经死了"): 18/44 观测
# 型域都是这个死法(observe_frozen_stale 先例), 但 completeness_ref 这道判据当时没读
# execution_policy_mode, 于是仍然逐日红。窗口上界改取 min(latest_expected, 本域 local_max):
# local_max **之前**是域自己曾经真实覆盖的历史, 出现不一致依旧是真损坏必须 FAIL; local_max
# **之后**是冻结后必然出现的空白, 不该被继续拿去跟一张还在长的基准表比, 改判 observe_frozen_window。

FROZEN_DAYS = ["20260105", "20260106", "20260107", "20260108", "20260109", "20260112"]
FROZEN_LOCAL_MAX = "20260108"  # mine 最后一次真实覆盖的交易日 (FROZEN_DAYS[3])


def _frozen_spec(**over):
    spec = _spec(execution_policy_mode="disabled", execution_policy_reason="tushare_sunset_freeze")
    spec.update(over)
    return spec


def test_frozen_domain_mismatch_inside_local_max_still_fails():
    """域已冻结, 但不一致落在 local_max **之前**(域自己真实覆盖过的区间) —— 那是真损坏,

    放宽只保护"冻结后必然出现的空白", 不许连带放过冻结前的真缺口。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    mine = dict(ref, **{"20260106": 9})   # 缺 1 行, 落在 local_max(20260108) 之前
    for d in ("20260109", "20260112"):
        mine.pop(d)                       # 冻结后的空白(local_max 之后), 不应影响本测试的判定
    conn = _fixture(mine, ref)

    got = cc.check_completeness_ref(conn, _frozen_spec(), FROZEN_DAYS, FROZEN_DAYS[-1])

    assert got["status"] == "fail_row_count_mismatch", got
    assert "20260106" in got["detail"], got["detail"]


def test_frozen_domain_mismatch_only_after_local_max_is_observed_not_failed():
    """域已冻结, 域自己覆盖过的区间(<=local_max)与基准完全一致, 冻结之后(>local_max)本域

    0 行而基准继续在长 —— 那是预期空白, 不是待补拉的缺口, 判 observe_frozen_window 而非 FAIL。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    mine = {d: 10 for d in FROZEN_DAYS if d <= FROZEN_LOCAL_MAX}   # 冻结后 0 行, 冻结前逐日一致

    conn = _fixture(mine, ref)
    got = cc.check_completeness_ref(conn, _frozen_spec(), FROZEN_DAYS, FROZEN_DAYS[-1])

    assert got["status"] == "observe_frozen_window", got
    assert f"local_max={FROZEN_LOCAL_MAX}" in got["detail"], got["detail"]
    assert got["fix_hint"], "冻结域放宽必须带出口说明, 不能只改状态不解释"


def test_frozen_domain_grain_supported_empty_diff_is_observed_not_pass():
    """冻结域窗口截断 + grain 声明齐全(标的集合差分支)时同样必须挂 observe_frozen_window,

    不能因为集合差查询本身是空的(逐日行数一致、标的集合也一致, 没有 missing/extra)就
    退回成 pass —— 那是"grain 不支持"分支自己的出口(第一处 ok_status 返回, 见上面
    test_frozen_domain_mismatch_only_after_local_max_is_observed_not_failed, 它用的
    _fixture 没有 ts_code 列, 走的是回落成纯行数比对那条路); 本测试走的是"grain 支持"
    分支的出口(第二处 ok_status 返回, 在标的集合差 diff_rows 为空之后), 是两条独立的
    代码路径, 必须各自都认 truncated, 不能有一条漏判。

    生产实锤: moneyflow 的 sync_registry.yaml 声明 grain=[ts_code, trade_date] 且
    execution_policy.mode=disabled(2026-09-18 blocking finding 证据) —— moneyflow 走的
    正是这条"grain 支持"分支, 不是集成测试(见 test_check_continuity_integrity.py 的
    test_20260918_continuity_shapes_frozen_domains_observe_real_gap_still_fails)里为了
    避开这条分支而特意声明的 grain=[trade_date](无标的列, 强制回落)。"""
    ref = {d: ["A", "B", "C"] for d in FROZEN_DAYS}
    mine = {d: ["A", "B", "C"] for d in FROZEN_DAYS if d <= FROZEN_LOCAL_MAX}  # 冻结后 0 行
    conn = _fixture_with_codes(mine, ref)

    spec = _frozen_spec(grain=["ts_code", "trade_date"])
    got = cc.check_completeness_ref(conn, spec, FROZEN_DAYS, FROZEN_DAYS[-1])

    assert got["status"] == "observe_frozen_window", got
    assert f"local_max={FROZEN_LOCAL_MAX}" in got["detail"], got["detail"]
    assert got["fix_hint"], "冻结域放宽必须带出口说明, 不能只改状态不解释"
    assert "未做集合差" not in got["detail"], (
        "本测试要走 grain 支持分支(真的做了集合差), 不是回落分支", got["detail"])


def test_frozen_domain_local_max_equal_latest_expected_is_not_truncated():
    """边界 (2026-09-18 blocking finding 修复, project cut_frozen_domain_verdicts):

    冻结域现算 local_max **恰好等于** latest_expected(域刚好在最后一个交易日冻结, 且该日
    与基准完全一致) —— 窗口上界 min(latest_expected, local_max) 此时两者相等, 必须走
    "未截断"的正常 pass 路径, 不能被误判成 truncated 而挂上 observe_frozen_window/
    frozen_note/frozen_hint。隔离条件: frozen=True 且 window 非空且 local_max 存在,
    只有"local_max 是否严格早于 latest_expected"这一条为假 —— 钉住 truncated 判据必须用
    严格小于(``local_max < latest_expected``), 不能用 ``<=``(那会把"刚好追上"也当成
    "冻结边界前移"而挂假说明)。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    mine = {d: 10 for d in FROZEN_DAYS}   # 逐日与基准一致, 覆盖到 latest_expected 本身

    conn = _fixture(mine, ref)
    got = cc.check_completeness_ref(conn, _frozen_spec(), FROZEN_DAYS, FROZEN_DAYS[-1])

    assert got["status"] == "pass", got
    assert "window truncated" not in got["detail"], got["detail"]
    assert not got.get("fix_hint"), got.get("fix_hint")


def test_enabled_twin_with_same_gap_still_fails_not_observed():
    """同样的数据形状(冻结后即空白), 但域是 enabled —— 仍按原判据 FAIL。

    证明这条放宽只对 execution_policy.mode=disabled 生效, 不是把 completeness_ref 整体放绿。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    mine = {d: 10 for d in FROZEN_DAYS if d <= FROZEN_LOCAL_MAX}

    conn = _fixture(mine, ref)
    got = cc.check_completeness_ref(conn, _spec(), FROZEN_DAYS, FROZEN_DAYS[-1])

    assert got["status"] == "fail_row_count_mismatch", got


def test_frozen_domain_missing_table_is_still_skipped_missing_table():
    """表不存在时既有行为不能被冻结分支破坏 —— 冻结判定必须排在表存在性检查之后。"""
    conn = duckdb.connect(":memory:")
    conn.execute("create table canonical_nominal_ohlcv_daily (trade_date VARCHAR)")
    # 不建 mine 表

    got = cc.check_completeness_ref(conn, _frozen_spec(), FROZEN_DAYS, FROZEN_DAYS[-1])

    assert got["status"] == "skipped_missing_table", got


def test_frozen_domain_with_empty_table_still_fails_not_silently_observed():
    """域已冻结但 local_max 取不出(表存在但 0 行, 从未真正来过数据) —— 不能被当成

    "已经截断到 local_max"而放行, 必须落回正常判据照样 FAIL(真损坏不能因为"表是空的
    所以特殊对待"被放过)。隔离条件: frozen 与 window 非空都满足, 只有 local_max 缺失。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    conn = duckdb.connect(":memory:")
    conn.execute("create table canonical_nominal_ohlcv_daily (trade_date VARCHAR)")
    conn.execute("create table mine (trade_date VARCHAR)")   # 建表但不插入任何行
    for day, n in ref.items():
        for _ in range(n):
            conn.execute("insert into canonical_nominal_ohlcv_daily values (?)", [day])

    got = cc.check_completeness_ref(conn, _frozen_spec(), FROZEN_DAYS, FROZEN_DAYS[-1])

    assert got["status"] == "fail_row_count_mismatch", got


def test_frozen_domain_local_max_before_verified_since_has_no_window_to_reconcile():
    """local_max 早于 verified_since —— 该判据在这个域身上从未在窗口内生效过,

    没有"永远绿"的空子: 只在 (a) 域冻结 且 (b) local_max 真实存在 且 (c) 窗口为空 三者同时成立时
    才走这条分支, 不是缺省行为。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    mine = {FROZEN_DAYS[0]: 10}   # 只在最早一天有数据, 远早于下面设的 verified_since
    conn = _fixture(mine, ref)

    spec = _frozen_spec()
    spec["completeness_ref"] = {**spec["completeness_ref"], "verified_since": FROZEN_DAYS[3]}
    got = cc.check_completeness_ref(conn, spec, FROZEN_DAYS, FROZEN_DAYS[-1])

    assert got["status"] == "observe_frozen_window", got
    assert "无窗口内可对账区间" in got["detail"], got["detail"]


# ── 冻结锚点回退防线 (2026-09-18, blocking finding 修复, 同一个 cut) ──────────────────────
#
# 上面的"冻结域窗口截断"用 local_max(本域自己那张表现算的 MAX) 当窗口上界——但 local_max
# 本身没有一次性核证过的冻结时刻锚点: 若这张表在冻结之后被静默削尾(误删/去重/并发写坏,
# CLAUDE.md 红线6), local_max 会跟着一起缩水, 窗口跟着收缩, 被削掉的那天直接被排除出
# 对账范围, 判定仍是 observe_frozen_window —— 真损坏被静默重新解读成"冻结边界又前移了一天"。
# 本节验证的修法: mart_data_source_watermark.last_data_date 是独立锚点(sync_runner 只增不退
# 写入, 冻结后没有代码路径会再移动它), 锚点晚于现算 local_max 时判 fail_frozen_regression。

_WATERMARK_DDL = """
CREATE TABLE mart_data_source_watermark (
    data_domain          TEXT NOT NULL,
    source_name          TEXT NOT NULL,
    source_tier          SMALLINT NOT NULL,
    last_data_date       TEXT,
    PRIMARY KEY (data_domain, source_name, source_tier)
);
"""


def _anchor_conn(rows: dict[str, str | None]):
    """独立的锚点连接 (真实 mart_data_source_watermark 列子集): {sync:domain -> last_data_date}。"""
    conn = duckdb.connect(":memory:")
    conn.execute(_WATERMARK_DDL)
    for i, (domain_key, last_data_date) in enumerate(rows.items()):
        conn.execute(
            "INSERT INTO mart_data_source_watermark "
            "(data_domain, source_name, source_tier, last_data_date) VALUES (?, ?, ?, ?)",
            [domain_key, "tushare", 2, last_data_date],
        )
    return conn


def test_frozen_regression_fires_when_anchor_ahead_of_live_max():
    """复现 blocking finding 证据: 冻结表最新一天的行被静默删掉, local_max 现算值倒退到

    前一天, 而 mart_data_source_watermark 里冻结前记录的水位(FROZEN_LOCAL_MAX 的下一个
    交易日, 模拟"域冻结时其实已经追到过更晚一天")还留着更晚的日期 —— 必须 fail_frozen_regression,
    不能被现算窗口截断悄悄吸收成 observe。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    mine = {d: 10 for d in FROZEN_DAYS if d <= FROZEN_LOCAL_MAX}   # 现算 local_max=FROZEN_LOCAL_MAX
    conn = _fixture(mine, ref)
    anchor = _anchor_conn({"sync:mine": "20260112"})   # 锚点晚于现算 local_max(20260108)

    spec = _frozen_spec(watermark_table="mart_data_source_watermark")
    got = cc.check_completeness_ref(conn, spec, FROZEN_DAYS, FROZEN_DAYS[-1], anchor_conn=anchor)

    assert got["status"] == "fail_frozen_regression", got
    assert FROZEN_LOCAL_MAX in got["detail"] and "20260112" in got["detail"], got["detail"]
    assert got["fix_hint"], "回退判据必须带出口说明, 不能只改状态不解释"


def test_frozen_regression_not_triggered_when_anchor_equals_live_max():
    """隔离: 锚点与现算 local_max **相等**(没有回退)不得误判 —— 只有 anchor > local_max

    (严格大于)才是回退, 相等是健康的冻结稳态。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    mine = {d: 10 for d in FROZEN_DAYS if d <= FROZEN_LOCAL_MAX}
    conn = _fixture(mine, ref)
    anchor = _anchor_conn({"sync:mine": FROZEN_LOCAL_MAX})   # 与 local_max 完全一致

    spec = _frozen_spec(watermark_table="mart_data_source_watermark")
    got = cc.check_completeness_ref(conn, spec, FROZEN_DAYS, FROZEN_DAYS[-1], anchor_conn=anchor)

    assert got["status"] == "observe_frozen_window", got


def test_frozen_regression_not_triggered_when_anchor_behind_live_max():
    """隔离: 锚点落后于现算 local_max (域冻结后又核实补过历史/水位未及时刷新) 不是回退证据,

    不得误判 —— 只有锚点**领先于**现算值才说明现算值退步了。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    mine = {d: 10 for d in FROZEN_DAYS if d <= FROZEN_LOCAL_MAX}
    conn = _fixture(mine, ref)
    anchor = _anchor_conn({"sync:mine": FROZEN_DAYS[0]})   # 锚点早于 local_max

    spec = _frozen_spec(watermark_table="mart_data_source_watermark")
    got = cc.check_completeness_ref(conn, spec, FROZEN_DAYS, FROZEN_DAYS[-1], anchor_conn=anchor)

    assert got["status"] == "observe_frozen_window", got


def test_frozen_window_falls_back_when_watermark_table_not_declared():
    """隔离: registry 没声明 watermark_table (既有域此前的常态, spec 缺该键) —— 必须原样回落

    到旧判据, 不能因为多加了这条回退防线就要求所有域都补声明才能跑。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    mine = {d: 10 for d in FROZEN_DAYS if d <= FROZEN_LOCAL_MAX}
    conn = _fixture(mine, ref)
    anchor = _anchor_conn({"sync:mine": "20260112"})   # 就算锚点库里有更晚的水位

    spec = _frozen_spec()   # 不带 watermark_table
    got = cc.check_completeness_ref(conn, spec, FROZEN_DAYS, FROZEN_DAYS[-1], anchor_conn=anchor)

    assert got["status"] == "observe_frozen_window", got


def test_frozen_window_falls_back_when_anchor_row_absent():
    """隔离: 锚点库可达但该域从未同步过(没有匹配的 data_domain 行) —— 必须回落, 不能把

    「查不到」误当成「查到了没有回退」, 也不能崩溃。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    mine = {d: 10 for d in FROZEN_DAYS if d <= FROZEN_LOCAL_MAX}
    conn = _fixture(mine, ref)
    anchor = _anchor_conn({"sync:some_other_domain": "20260112"})   # 键不匹配

    spec = _frozen_spec(watermark_table="mart_data_source_watermark")
    got = cc.check_completeness_ref(conn, spec, FROZEN_DAYS, FROZEN_DAYS[-1], anchor_conn=anchor)

    assert got["status"] == "observe_frozen_window", got


def test_frozen_window_falls_back_when_anchor_explicitly_disabled():
    """anchor_conn=None(不传时的默认值, 生产路径下由 run_checks 经 conn_for("smartmoney")

    注入; 本函数自身不再现开连接, 见 _frozen_watermark_anchor_max docstring)必须走回退
    分支而非报错 —— 调用方(测试)拒绝锚点查找时不能崩溃。"""
    ref = {d: 10 for d in FROZEN_DAYS}
    mine = {d: 10 for d in FROZEN_DAYS if d <= FROZEN_LOCAL_MAX}
    conn = _fixture(mine, ref)

    spec = _frozen_spec(watermark_table="mart_data_source_watermark")
    got = cc.check_completeness_ref(conn, spec, FROZEN_DAYS, FROZEN_DAYS[-1], anchor_conn=None)

    assert got["status"] == "observe_frozen_window", got


def test_frozen_watermark_anchor_max_reads_real_schema_and_normalizes():
    """直接单测 _frozen_watermark_anchor_max: 对真实 mart_data_source_watermark 列子集取

    MAX(last_data_date) 并按 domain_key='sync:{domain}' 过滤、经 _norm_day 归一。"""
    anchor = _anchor_conn({"sync:moneyflow": "2026-08-28"})   # dashed 存储也要能归一
    assert cc._frozen_watermark_anchor_max("moneyflow", "mart_data_source_watermark", anchor) \
        == "20260828"


def test_frozen_watermark_anchor_max_none_without_watermark_table():
    """隔离: watermark_table 参数为 None/空串 直接短路返回 None, 不发起任何查询

    (连锚点连接都不看一眼 —— 用一个记录调用次数的假连接证明它真的没被碰; 不能靠"execute
    抛异常"来证明, 因为生产代码本身会 except Exception 兜底, 抛异常也会被悄悄吞掉返回
    None, 那样测试就算 mutant 把短路守卫删掉了也照样通过)。"""
    class _BoomConn:
        def __init__(self):
            self.called = False

        def execute(self, *a, **kw):
            self.called = True
            raise RuntimeError("watermark_table 缺失时不该发起任何查询")

    c1, c2 = _BoomConn(), _BoomConn()
    assert cc._frozen_watermark_anchor_max("moneyflow", None, c1) is None
    assert cc._frozen_watermark_anchor_max("moneyflow", "", c2) is None
    assert c1.called is False, "watermark_table=None 时不该发起任何查询"
    assert c2.called is False, "watermark_table='' 时不该发起任何查询"


def test_frozen_watermark_anchor_max_none_when_anchor_conn_none():
    """隔离: anchor_conn 显式传 None (与不传/UNSET 不同) 直接短路返回 None。"""
    assert cc._frozen_watermark_anchor_max(
        "moneyflow", "mart_data_source_watermark", None) is None


def test_frozen_watermark_anchor_max_none_when_query_raises_real_exception():
    """隔离(其它全满足, 只违反"查询本身能成功执行"这一条): watermark_table 非空、
    anchor_conn 非 None、确实发起了查询, 但那张表在这条连接里根本不存在 —— 触发真实
    duckdb CatalogException(不是构造出来的假异常), 必须被 except Exception 兜底退化成
    "锚点不可用"返回 None, 不能让审计门自己崩溃(CLAUDE.md 红线3)。

    与上面 test_frozen_watermark_anchor_max_none_without_watermark_table 的区别: 那条测的
    是参数短路(assert 从未调用 execute); 本条测的是短路条件都不成立、execute 真的被调用、
    调用本身失败——覆盖 blocking finding 证据里点名的"另一处兜底"(把 except Exception
    收窄成 except ValueError 时该测例必须变红, 见本文件同名变异记录)。"""
    anchor = duckdb.connect(":memory:")   # 空库, 连 mart_data_source_watermark 表都没建
    got = cc._frozen_watermark_anchor_max("moneyflow", "mart_data_source_watermark", anchor)
    assert got is None, got
