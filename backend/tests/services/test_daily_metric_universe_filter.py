"""daily_metric_filter_sql() D4 (业主 2026-09-12 裁定): 两个日频指标只算项目股票池
内的证券 —— top_inst_seat_publish.daily_metric_filter_sql() 是单一计算点
(market_pulse.lhb_inst_net / institution_profile.c3_lhb 共用, 不各写一份判断)。

隔离原则: 每条用例只改动池前缀这一个轴, board_window/seat_kind 全部固定为
D2/D3 都放行的组合 (single_day/seat) —— 证明"其它条件全满足、只剩它为假"时
D4 依然生效。前缀白名单不 hardcode: 断言必须的地方直接复用
services.universe.sql_where_active_a_share(), 不在测试里再写一份前缀字面量。

只读, 内存 DuckDB, 不连 data/ 下任何库。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import top_inst_seat_publish as pub
from services import universe
from services.duck_adapter import connect as duck_connect


def _kept_codes(rows: list[tuple]) -> list:
    """rows: (ts_code, board_window, seat_kind). 返回通过 daily_metric_filter_sql() 的 ts_code。"""
    con = duck_connect(":memory:", read_only=False)
    try:
        con.execute("CREATE TABLE t (ts_code VARCHAR, board_window VARCHAR, seat_kind VARCHAR)")
        con.executemany("INSERT INTO t VALUES (?, ?, ?)", rows)
        got = con.execute(
            f"SELECT ts_code FROM t WHERE {pub.daily_metric_filter_sql()} ORDER BY ts_code"
        ).fetchall()
        return [r[0] for r in got]
    finally:
        con.close()


def test_pool_code_kept():
    """池内代码 (沪主板 60 前缀), D2/D3 都满足 → 必须被计入。"""
    assert _kept_codes([("600000.SH", "single_day", "seat")]) == ["600000.SH"]


def test_convertible_bond_excluded_isolated():
    """可转债 (113xxx.SH, 前缀 11 不在白名单) D2/D3 都满足, 仍须被池过滤排除。"""
    assert _kept_codes([("113050.SH", "single_day", "seat")]) == []


def test_bse_excluded_isolated():
    """北交所 (.BJ, 前缀 83 不在白名单) D2/D3 都满足, 仍须被池过滤排除。"""
    assert _kept_codes([("830001.BJ", "single_day", "seat")]) == []


def test_b_share_excluded_isolated():
    """B 股 (900xxx.SH, 前缀 90 不在白名单) D2/D3 都满足, 仍须被池过滤排除。"""
    assert _kept_codes([("900001.SH", "single_day", "seat")]) == []


def test_null_ts_code_not_counted_as_in_pool():
    """ts_code 为 NULL: SUBSTR(NULL,1,2) 是 NULL, `NULL IN (...)` 求值为 NULL 非 TRUE,
    WHERE 里等价假 —— 缺失只能传播为缺失(红线 3), 不当成"在池内"。"""
    assert _kept_codes([(None, "single_day", "seat")]) == []


def test_pool_and_non_pool_mixed_only_pool_kept():
    """混合行: 只有池内的那一行该留下, 池外三类各一行都必须被挡住。"""
    rows = [
        ("600000.SH", "single_day", "seat"),
        ("113050.SH", "single_day", "seat"),
        ("830001.BJ", "single_day", "seat"),
        ("900001.SH", "single_day", "seat"),
        (None, "single_day", "seat"),
    ]
    assert _kept_codes(rows) == ["600000.SH"]


_FAKE_SINGLE_PREFIX_POLICY_YAML = """
policy:
  id: "fake_single_prefix_policy"
  version: 1
include:
  board_prefixes: ["60"]
  exchange_ids: ["SSE"]
  venue_by_prefix:
    "60": {exchange_id: "SSE", ts_suffix: "SH"}
eligibility:
  rule: "traded_on_observation_date"
  calendar_exchange_id: "SSE"
exclude:
  excluded_boards:
    "9": "假排除板 (测试专用)"
limit_up_pct:
  "60": 0.10
truth_source:
  nominal_kline: "fake.source"
  st_membership: "fake.source"
  trading_calendar: "fake.source"
current_enumeration:
  identity_source: "fake.source"
  st_name_patterns: ["ST"]
  no_recent_kline_days: 5
"""


def test_whitelist_comes_from_policy_not_hardcode(monkeypatch):
    """构造一个只允许 '60' 前缀的假 policy (走真实 load_universe_policy, 不是 duck-typed
    stub), 断言过滤结果随之改变 —— 证明白名单读自 services.universe 的 policy, 不是
    top_inst_seat_publish.py 或本文件里的字面量。00 前缀在真实生产 policy 下在池内,
    换成假 policy 后必须被排除。
    """
    with tempfile.TemporaryDirectory() as d:
        fake_path = Path(d) / "fake_universe_rules.yaml"
        fake_path.write_text(_FAKE_SINGLE_PREFIX_POLICY_YAML, encoding="utf-8")
        fake_policy = universe.load_universe_policy(fake_path)
        assert fake_policy.allowed_board_prefixes == ("60",)

        # sql_where_active_a_share() 内部读的正是这个模块级派生值 —— 这就是
        # daily_metric_filter_sql() 取白名单的唯一路径 (universe.py 已有单一计算点)。
        monkeypatch.setattr(universe, "ACTIVE_A_SHARE_PREFIXES", fake_policy.allowed_board_prefixes)

        got = _kept_codes([
            ("600000.SH", "single_day", "seat"),
            ("000001.SZ", "single_day", "seat"),
        ])
        assert got == ["600000.SH"], (
            "假 policy 只放行 '60' 前缀, '00' 前缀的 000001.SZ 必须被排除; "
            f"实得 {got} —— 若这里仍是两行, 说明过滤条件绕过了 policy, 在别处写死了前缀。"
        )
