"""institution_profile 状态机单测 — 成本/收益正确性证伪门 (promote 纪律)。

覆盖: 新进→增持(加权平均成本)→减持(部分了结)→退出(清仓) 全链数值 + seeded/多轮 episode/无价跳过
+ 2026-07-03 审计修2: share_class 混流过滤 / 源重复键去重 (SQL 级) + 状态机三缺陷 (unit 级)。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services.institution_profile import build_episodes, run_episode_state_machine


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

    E0: rebuild reads canonical spine + legacy enrichment projection; fixture
    keeps canonical empty so legacy_only path supplies episode rows.
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
    CREATE TABLE sm.fact_top10_holder_period (
        stock_code TEXT, report_date TEXT, holder_set TEXT, holder_name TEXT,
        holder_name_norm TEXT, change_status TEXT, is_exit_row BOOLEAN,
        shares_approx DOUBLE, hold_change_num DOUBLE, holder_type TEXT,
        notice_date TEXT, share_class TEXT, holder_rank INTEGER, row_seq INTEGER,
        raw_hash TEXT, source TEXT);
    CREATE TABLE period_windows (
        stock_code TEXT, report_date TEXT, prev_period TEXT, w_start TEXT, w_end TEXT,
        c1_vwap DOUBLE, c2_eod DOUBLE, c3_lhb DOUBLE, c3_eff DOUBLE);
    CREATE TABLE tr.raw_tushare_index_daily (ts_code TEXT, trade_date TEXT, close DOUBLE);
    CREATE TABLE tr.v_sw_industry_pit (stock_code TEXT, l1_name TEXT, in_date TEXT, out_date TEXT);
    """)
    c.executemany("INSERT INTO period_windows VALUES (?,?,?,?,?,?,?,?,?)", [
        ("600000", "20240331", None, "2024-01-01", "2024-03-31", 10.0, 10.0, None, 10.0),
        ("600000", "20240630", "20240331", "2024-03-31", "2024-06-30", 20.0, 20.0, None, 20.0),
    ])
    return c


_HOLDER_ROW_N = 16  # 列数与上方 legacy fixture DDL 一致


def test_build_episodes_filters_non_a_share_class():
    """share_class != 'A' (B/H 股行) 混入 A 股 qfq 价计价 = 价格错配 → 源查询硬滤。"""
    c = _sql_conn()
    try:
        c.executemany(f"INSERT INTO sm.fact_top10_holder_period VALUES ({','.join('?' * _HOLDER_ROW_N)})", [
            ("600000", "20240331", "free", "基金一号", "基金一号", "新进", False, 100, None, "基金", "20240430", "A", 1, 1, "h1", "miaoxiang"),
            ("600000", "20240630", "free", "基金一号", "基金一号", "退出", True, 100, -100, "基金", "20240730", "A", 1, 1, "h2", "miaoxiang"),
            ("600000", "20240331", "free", "港资股东", "港资股东", "新进", False, 50, None, "QFII", "20240430", "H", 2, 1, "h3", "miaoxiang"),
            ("600000", "20240331", "free", "B股东", "B股东", "新进", False, 50, None, "法人", "20240430", "B", 3, 1, "h4", "miaoxiang"),
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
        c.executemany(f"INSERT INTO sm.fact_top10_holder_period VALUES ({','.join('?' * _HOLDER_ROW_N)})", [
            ("600000", "20240331", "free", "基金二号", "基金二号", "新进", False, 100, None, "基金", "20240430", "A", 5, 1, "h5", "miaoxiang"),
            ("600000", "20240331", "free", "基金二号", "基金二号", "新进", False, 999, None, "基金", "20240430", "A", 6, 1, "h6", "miaoxiang"),
        ])
        build_episodes(c)
        rows = c.execute("SELECT status, shares FROM fact_inst_episode WHERE holder = '基金二号'").fetchall()
        assert len(rows) == 1, f"重复键必须只计一次 (修前 2 个 episode): {rows}"
        assert rows[0][0] == "holding"
        assert rows[0][1] == pytest.approx(100.0), "稳定序: holder_rank 最小的主行胜出"
    finally:
        c.close()
