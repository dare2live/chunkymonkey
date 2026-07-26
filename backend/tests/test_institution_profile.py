"""institution_profile 状态机单测 — 成本/收益正确性证伪门 (promote 纪律)。

覆盖: 新进→增持(加权平均成本)→减持(部分了结)→退出(清仓) 全链数值 + seeded/多轮 episode/无价跳过
+ 2026-07-03 审计修2: share_class 混流过滤 / 源重复键去重 (SQL 级) + 状态机三缺陷 (unit 级)。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services.institution_profile import (
    build_episodes,
    build_profiles,
    run_episode_state_machine,
)


def _row(holder="H", stock="600000", period="20240331", status="新进", is_exit=False,
         shares=None, chg=None, htype="QFII", notice="20240430", c1=10.0, c2=10.0, c3=10.0):
    return (holder, stock, period, status, is_exit, shares, chg, htype, notice, c1, c2, c3)


def test_full_lifecycle_weighted_cost_and_realized():
    """新进1000股@10 → 增持1000股@20 (成本加权→15) → 减持500股@30 (realized+7500) → 退出@40 (剩1500股×25)。"""
    rows = [
        _row(period="20240331", status="新进", shares=1000, c1=10.0, c2=10.0, c3=10.0),
        _row(period="20240630", status="增持", shares=2000, chg=1000, c1=20.0, c2=20.0, c3=20.0),
        _row(period="20240930", status="减持", shares=1500, chg=-500, c1=30.0, c2=30.0, c3=30.0),
        _row(period="20241231", status="退出", is_exit=True, shares=1500, chg=-1500, c1=40.0, c2=40.0, c3=40.0),
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats == {"opened": 1, "closed": 1, "seeded": 0, "no_price_skip": 0,
                     "unpriced_close": 0, "superseded": 0}
    ep = eps[0]
    assert ep["status"] == "closed"
    assert ep["cost_c1"] == pytest.approx(15.0)          # (1000×10 + 1000×20) / 2000
    assert ep["peak_shares"] == 2000
    # realized = 减持 500×(30−15)=7500 + 退出 1500×(40−15)=37500 = 45000
    assert ep["realized_c1"] == pytest.approx(45000.0)
    # 收益口径: realized/(cost×peak) = 45000/(15×2000) = 150%
    assert ep["realized_c1"] / (ep["cost_c1"] * ep["peak_shares"]) == pytest.approx(1.5)


def test_seeded_from_change_flagged():
    """首见即增持 (前期第11名进前十) → 按新进开仓且 seeded=True (画像排除)。"""
    rows = [_row(status="增持", shares=800, chg=300, c1=12.0)]
    eps, stats = run_episode_state_machine(rows)
    assert stats["seeded"] == 1
    assert eps[0]["seeded"] is True and eps[0]["status"] == "holding"
    assert eps[0]["cost_c1"] == 12.0 and eps[0]["shares"] == 800


def test_multi_round_episodes_same_holder_stock():
    """退出后再新进 = 独立新 episode (易方达×茅台 3 轮实证形态)。"""
    rows = [
        _row(period="20230331", status="新进", shares=100, c1=10.0),
        _row(period="20230630", status="退出", is_exit=True, shares=100, chg=-100, c1=12.0),
        _row(period="20240331", status="新进", shares=200, c1=8.0),
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats == {"opened": 2, "closed": 1, "seeded": 0, "no_price_skip": 0,
                     "unpriced_close": 0, "superseded": 0}
    closed = [e for e in eps if e["status"] == "closed"][0]
    holding = [e for e in eps if e["status"] == "holding"][0]
    assert closed["realized_c1"] == pytest.approx(100 * 2.0)
    assert holding["cost_c1"] == 8.0 and holding["shares"] == 200


def test_no_price_skips_event_not_episode():
    """窗口无K线 (c1=None) 的事件跳过, 不影响已开 episode。"""
    rows = [
        _row(period="20240331", status="新进", shares=100, c1=10.0),
        _row(period="20240630", status="增持", shares=200, chg=100, c1=None),  # 停牌窗口
        _row(period="20240930", status="退出", is_exit=True, shares=100, chg=-100, c1=20.0),
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats["no_price_skip"] == 1
    ep = eps[0]
    # 增持事件被跳过 → shares 仍 100, 成本仍 10, 退出 realized=100×10
    assert ep["shares"] == 100 and ep["cost_c1"] == 10.0
    assert ep["realized_c1"] == pytest.approx(1000.0)


def test_exit_without_open_is_noop():
    """无开仓的孤儿退出行 (数据起点截断) → 不产生 episode。"""
    eps, stats = run_episode_state_machine([_row(status="退出", is_exit=True, shares=100, chg=-100)])
    assert eps == [] and stats["closed"] == 0


# ── 2026-07-03 审计修2c: 状态机三缺陷证伪门 ─────────────────────────────────────


def test_exit_with_null_window_price_closes_unpriced():
    """退出行窗口无价 (c1=None) 也必须关闭 episode (修前被无价跳过吞掉 → 幽灵 holding)。
    status='unpriced_close': 最终腿 PnL 不可测不计 (不知道≠0), 已实现部分保留, 不进 'closed' 评级。"""
    rows = [
        _row(period="20240331", status="新进", shares=100, c1=10.0),
        _row(period="20240630", status="减持", shares=50, chg=-50, c1=20.0),  # realized 50×10=500
        _row(period="20240930", status="退出", is_exit=True, shares=50, chg=-50,
             c1=None, c2=None, c3=None),   # 停牌窗口无价
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats["unpriced_close"] == 1 and stats["closed"] == 0
    assert len(eps) == 1
    ep = eps[0]
    assert ep["status"] == "unpriced_close", "修前: 退出被吞, status 残留 holding"
    assert ep["close_date"] == "20240930"
    assert ep["realized_c1"] == pytest.approx(500.0)   # 只有已实现部分, 最终腿不估价


def test_new_entry_over_open_episode_supersedes_not_overwrites():
    """已开 episode 再见'新进' (中间退出披露缺失) → 旧 episode 按当期窗口价关闭
    (status='superseded', 不进评级) 再开新 — 修前 dict 直接覆盖 = 旧 episode 静默丢失。"""
    rows = [
        _row(period="20240331", status="新进", shares=100, c1=10.0),
        _row(period="20241231", status="新进", shares=200, c1=16.0),
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats == {"opened": 2, "closed": 0, "seeded": 0, "no_price_skip": 0,
                     "unpriced_close": 0, "superseded": 1}
    assert len(eps) == 2, "修前: 只剩 1 个 episode (旧的被覆盖丢失)"
    old = [e for e in eps if e["status"] == "superseded"][0]
    new = [e for e in eps if e["status"] == "holding"][0]
    assert old["close_date"] == "20241231"
    assert old["realized_c1"] == pytest.approx(100 * (16.0 - 10.0))  # 按当期窗口价关闭
    assert new["cost_c1"] == 16.0 and new["shares"] == 200 and new["seeded"] is False


# ── 2026-07-03 审计修2a/2b: build_episodes 源查询 (SQL 级证伪门) ─────────────────


def _sql_conn():
    """内存库模拟生产 ATTACH sm/tr (CREATE SCHEMA 两部名同解析) + 手造 period_windows。

    E0: rebuild reads canonical-only (holders fact retired 2026-07-26).
    """
    c = duck_mem()
    c.executescript("""
    CREATE SCHEMA sm; CREATE SCHEMA tr;
    CREATE TABLE sm.canonical_top10_float_holders_period (
        stock_code TEXT, report_date TEXT, holder_set TEXT, holder_rank INTEGER,
        row_seq INTEGER, holder_name TEXT, hold_ratio_float DOUBLE, notice_date TEXT,
        is_exit_row BOOLEAN, holder_name_norm TEXT, share_class TEXT,
        shares_approx BIGINT, change_status TEXT, hold_change_num DOUBLE,
        holder_type TEXT);
    CREATE TABLE period_windows (
        stock_code TEXT, report_date TEXT, prev_period TEXT, w_start TEXT, w_end TEXT,
        c1_vwap DOUBLE, c2_eod DOUBLE, c3_lhb DOUBLE, c3_eff DOUBLE);
    CREATE TABLE sm.fact_index_daily (
        trade_date TEXT, ts_code TEXT, close DOUBLE,
        available_at TIMESTAMPTZ, source_table TEXT, built_at TIMESTAMPTZ);
    CREATE TABLE tr.v_sw_industry_pit (stock_code TEXT, l1_name TEXT, in_date TEXT, out_date TEXT);
    """)
    c.executemany("INSERT INTO period_windows VALUES (?,?,?,?,?,?,?,?,?)", [
        ("600000", "20240331", None, "2024-01-01", "2024-03-31", 10.0, 10.0, None, 10.0),
        ("600000", "20240630", "20240331", "2024-03-31", "2024-06-30", 20.0, 20.0, None, 20.0),
    ])
    return c


_HOLDER_ROW_N = 15  # 列数与上方 canonical fixture DDL 一致


def test_build_episodes_filters_non_a_share_class():
    """share_class != 'A' (B/H 股行) 混入 A 股 qfq 价计价 = 价格错配 → 源查询硬滤。"""
    c = _sql_conn()
    try:
        c.executemany(f"INSERT INTO sm.canonical_top10_float_holders_period VALUES ({','.join('?' * _HOLDER_ROW_N)})", [
            ("600000", "20240331", "free", 1, 1, "基金一号", 1.0, "20240430", False, "基金一号", "A", 100, "新进", None, "基金"),
            ("600000", "20240630", "free", 1, 1, "基金一号", 1.0, "20240730", True, "基金一号", "A", 100, "退出", -100, "基金"),
            ("600000", "20240331", "free", 2, 1, "港资股东", 1.0, "20240430", False, "港资股东", "H", 50, "新进", None, "QFII"),
            ("600000", "20240331", "free", 3, 1, "B股东", 1.0, "20240430", False, "B股东", "B", 50, "新进", None, "法人"),
        ])
        build_episodes(c)
        holders = {r[0] for r in c.execute("SELECT DISTINCT holder FROM fact_inst_episode").fetchall()}
        assert holders == {"基金一号"}, f"B/H 行必须被滤 (修前混入): {holders}"
        ep = c.execute("SELECT status, realized_c1 FROM fact_inst_episode").fetchone()
        assert ep[0] == "closed" and ep[1] == pytest.approx(100 * (20.0 - 10.0))
    finally:
        c.close()


def test_build_episodes_dedups_source_duplicate_keys():
    """源 (holder, stock, report_date, is_exit_row) 双行 (实测 60 组) → QUALIFY 稳定序取 1 行;
    修前双行双计 → 修2c 语义下第二行还会 supersede 出 2 个 episode。"""
    c = _sql_conn()
    try:
        c.executemany(f"INSERT INTO sm.canonical_top10_float_holders_period VALUES ({','.join('?' * _HOLDER_ROW_N)})", [
            ("600000", "20240331", "free", 5, 1, "基金二号", 1.0, "20240430", False, "基金二号", "A", 100, "新进", None, "基金"),
            ("600000", "20240331", "free", 6, 1, "基金二号", 1.0, "20240430", False, "基金二号", "A", 999, "新进", None, "基金"),
        ])
        build_episodes(c)
        rows = c.execute("SELECT status, shares FROM fact_inst_episode WHERE holder = '基金二号'").fetchall()
        assert len(rows) == 1, f"重复键必须只计一次 (修前 2 个 episode): {rows}"
        assert rows[0][0] == "holding"
        assert rows[0][1] == pytest.approx(100.0), "稳定序: holder_rank 最小的主行胜出"
    finally:
        c.close()


# ── 2026-07-23 coverage lift: display profile for every episode holder ─────────


def test_build_profiles_includes_holding_only_and_keeps_metrics_null():
    """holding-only / passive / thin holders get display rows; alpha stays NULL."""
    c = duck_mem()
    try:
        c.execute("""
            CREATE TABLE fact_inst_episode (
                holder VARCHAR, stock VARCHAR, holder_type VARCHAR,
                open_date VARCHAR, close_date VARCHAR, status VARCHAR,
                seeded BOOLEAN, is_passive BOOLEAN,
                ret_c1 DOUBLE, alpha_c1 DOUBLE, sw_l1_at_open VARCHAR
            )
        """)
        c.executemany(
            "INSERT INTO fact_inst_episode VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                # rankable closed ×11 → ranked
                *[
                    ("牛散A", f"60000{i}", "个人", "20200101", "20200630",
                     "closed", False, False, 0.1, 0.05, "银行")
                    for i in range(11)
                ],
                # holding only → display, metrics NULL
                ("持有中B", "600100", "基金", "20240101", None,
                 "holding", False, False, None, None, "煤炭"),
                # passive only → display, metrics_status=passive_product
                ("被动ETF", "600200", "基金", "20240101", "20240630",
                 "closed", False, True, 0.2, 0.1, "电子"),
                # empty name → dropped
                ("", "600300", "个人", "20240101", None,
                 "holding", False, False, None, None, None),
                (None, "600301", "个人", "20240101", None,
                 "holding", False, False, None, None, None),
                # seeded closed only → no_closed_alpha (not rankable)
                ("种子C", "600400", "个人", "20200101", "20200630",
                 "closed", True, False, 0.3, 0.2, "医药"),
            ],
        )
        out = build_profiles(c)
        assert out["profiles"] == 4  # A/B/ETF/C — empty dropped
        rows = {
            r[0]: r
            for r in c.execute(
                "SELECT holder, n_closed, median_alpha, low_sample, "
                "n_episodes, is_passive_holder, metrics_status "
                "FROM mart_inst_profile"
            ).fetchall()
        }
        assert set(rows) == {"牛散A", "持有中B", "被动ETF", "种子C"}
        assert rows["牛散A"][1] == 11 and rows["牛散A"][2] == pytest.approx(0.05)
        assert rows["牛散A"][3] is False and rows["牛散A"][6] == "ranked"
        assert rows["持有中B"][1] == 0 and rows["持有中B"][2] is None
        assert rows["持有中B"][3] is True and rows["持有中B"][6] == "holding_only"
        assert rows["被动ETF"][1] == 0 and rows["被动ETF"][2] is None
        assert rows["被动ETF"][5] is True and rows["被动ETF"][6] == "passive_product"
        assert rows["种子C"][1] == 0 and rows["种子C"][2] is None
        assert rows["种子C"][6] == "no_closed_alpha"
        # dims stay rankable-only (牛散A only)
        dim_holders = {
            r[0]
            for r in c.execute("SELECT DISTINCT holder FROM mart_inst_profile_dim").fetchall()
        }
        assert dim_holders == {"牛散A"}
    finally:
        c.close()
