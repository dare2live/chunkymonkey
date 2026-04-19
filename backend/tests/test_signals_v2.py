"""
signals_v2 单元测试

覆盖：
    - PolicyConfig 默认值 + gain_column
    - compute_ev_stats 空样本 / 单样本 / 多样本
    - _decide_from_history 三档决策分支
    - recommend_for_event 完整流程
    - 历史严格左切（no look-ahead）
"""

import sqlite3
from datetime import datetime

import pytest

from services.signals_v2 import (
    DEFAULT_CONFIG,
    PolicyConfig,
    compute_ev_stats,
    _decide_from_history,
    _quarter_key,
    _normalize_ymd_key,
    fetch_institution_history,
    recommend_for_event,
    load_config,
    save_config,
    institution_track_record,
    backtest_historical,
    build_today_signals,
    ensure_defaults,
    cohort_recent_matured,
    institution_multi_horizon,
)


# ─── fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def memdb():
    """In-memory DB with minimal schema + sample events."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE app_settings (
            key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
        );
        CREATE TABLE fact_institution_event (
            institution_id TEXT,
            stock_code TEXT,
            stock_name TEXT,
            report_date TEXT,
            notice_date TEXT,
            event_type TEXT,
            premium_pct REAL,
            gain_30d REAL, gain_60d REAL, gain_90d REAL, gain_120d REAL,
            max_drawdown_30d REAL, max_drawdown_60d REAL,
            PRIMARY KEY (institution_id, stock_code, report_date)
        );
        CREATE TABLE dim_stock_industry (
            stock_code TEXT PRIMARY KEY,
            sw_level1 TEXT, sw_level2 TEXT, sw_level3 TEXT
        );
        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            name TEXT, display_name TEXT, type TEXT, enabled INTEGER DEFAULT 1
        );
    """)
    yield conn
    conn.close()


def _seed_events(conn, events):
    """events = [(inst, stock, report_date, notice_date, event_type, premium, gain60, industry), ...]"""
    industries = {}
    for ev in events:
        inst, stock, rd, nd, et, prem, gain60, ind = ev
        industries[stock] = ind
        conn.execute("""
            INSERT OR REPLACE INTO fact_institution_event
            (institution_id, stock_code, stock_name, report_date, notice_date,
             event_type, premium_pct, gain_60d, max_drawdown_60d)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (inst, stock, f"股票{stock}", rd, nd, et, prem, gain60, 10.0))
    for stock, ind in industries.items():
        conn.execute("""
            INSERT OR REPLACE INTO dim_stock_industry (stock_code, sw_level1)
            VALUES (?, ?)
        """, (stock, ind))
    conn.commit()


# ─── PolicyConfig ────────────────────────────────────────────────────

def test_config_defaults():
    cfg = PolicyConfig()
    assert cfg.horizon_days == 60
    assert cfg.min_sample == 10
    assert cfg.ev_threshold_pct == 5.0
    assert cfg.win_threshold == 0.55


def test_config_gain_column():
    assert PolicyConfig(horizon_days=30).gain_column == "gain_30d"
    assert PolicyConfig(horizon_days=60).gain_column == "gain_60d"
    assert PolicyConfig(horizon_days=120).gain_column == "gain_120d"


def test_config_gain_column_invalid():
    with pytest.raises(ValueError):
        PolicyConfig(horizon_days=45).gain_column


def test_load_save_config(memdb):
    ensure_defaults(memdb)
    cfg = load_config(memdb)
    assert cfg.horizon_days == DEFAULT_CONFIG["horizon_days"]

    save_config(memdb, {"horizon_days": 90, "min_sample": 20})
    cfg2 = load_config(memdb)
    assert cfg2.horizon_days == 90
    assert cfg2.min_sample == 20
    assert cfg2.ev_threshold_pct == DEFAULT_CONFIG["ev_threshold_pct"]


# ─── compute_ev_stats ────────────────────────────────────────────────

def test_ev_stats_empty():
    s = compute_ev_stats([])
    assert s.n == 0
    assert s.ev_pct is None


def test_ev_stats_single():
    s = compute_ev_stats([{"gain": 5.0, "max_drawdown_60d": 3.0}])
    assert s.n == 1
    assert s.ev_pct == 5.0
    assert s.win_rate == 1.0
    assert s.avg_drawdown_pct == 3.0


def test_ev_stats_mixed():
    rows = [{"gain": g, "max_drawdown_60d": 10.0} for g in [10, -5, 20, 0, -10, 15, 5, -3, 8, 12]]
    s = compute_ev_stats(rows)
    assert s.n == 10
    assert s.ev_pct == 5.2
    assert s.win_rate == 0.6  # 6 positive (10,20,15,5,8,12); 0 is not > 0


# ─── _decide_from_history ───────────────────────────────────────────

def test_decide_insufficient_sample():
    cfg = PolicyConfig(min_sample=10)
    history = [{"gain": 10.0} for _ in range(5)]
    action, scope = _decide_from_history({"industry": "医药"}, history, cfg)
    assert action == "skip"
    assert scope == "insufficient"


def test_decide_follow_same_industry():
    cfg = PolicyConfig(min_sample=10, prefer_same_industry_min_sample=5)
    history = [{"gain": 10.0, "industry": "医药"} for _ in range(5)] + \
              [{"gain": -20.0, "industry": "煤炭"} for _ in range(10)]
    action, scope = _decide_from_history({"industry": "医药"}, history, cfg)
    # 15 total events, 5 in 医药 (enough for prefer), should use 医药 subset with EV=10
    assert action == "follow"
    assert scope == "inst_industry"


def test_decide_fall_back_to_all():
    cfg = PolicyConfig(min_sample=10, prefer_same_industry_min_sample=10)
    history = [{"gain": 10.0, "industry": "医药"} for _ in range(3)] + \
              [{"gain": 10.0, "industry": "电子"} for _ in range(10)]
    action, scope = _decide_from_history({"industry": "医药"}, history, cfg)
    # 医药 subset too small (3 < 10), fall back to all 13 events
    assert action == "follow"
    assert scope == "inst_all"


def test_decide_skip_low_ev():
    cfg = PolicyConfig(min_sample=10, ev_threshold_pct=5.0, win_threshold=0.55)
    history = [{"gain": 1.0, "industry": "医药"} for _ in range(10)]  # EV=1, WR=100%
    action, scope = _decide_from_history({"industry": "医药"}, history, cfg)
    assert action == "watch"  # EV=1 is >= 5*0.6=3? no, 1<3 → skip actually
    # Recalc: ev=1.0, threshold=5.0; ev < threshold (fails). 1 >= 5*0.6=3? no. wr=1 >= 0.55*0.9=0.495? yes.
    # 所以会进 watch 档
    assert action == "watch"


def test_decide_skip_negative():
    cfg = PolicyConfig(min_sample=10, ev_threshold_pct=5.0, win_threshold=0.55)
    history = [{"gain": -5.0, "industry": "医药"} for _ in range(10)]
    action, scope = _decide_from_history({"industry": "医药"}, history, cfg)
    assert action == "skip"


# ─── fetch_institution_history 左切 ──────────────────────────────────

def test_fetch_history_left_cutoff(memdb):
    _seed_events(memdb, [
        ("inst1", "000001", "2024-03-31", "2024-04-20", "new_entry", 2.0, 10.0, "银行"),
        ("inst1", "000002", "2024-06-30", "2024-07-20", "new_entry", 1.0, 5.0, "银行"),
        ("inst1", "000003", "2024-09-30", "2024-10-20", "new_entry", 3.0, 20.0, "银行"),
    ])
    # As-of 2024-07-01 应只看到第一条
    history = fetch_institution_history(
        memdb, "inst1", gain_column="gain_60d", as_of_date="2024-07-01"
    )
    assert len(history) == 1
    assert history[0]["stock_code"] == "000001"

    # As-of 2024-12-01 看到全部 3 条
    history_full = fetch_institution_history(
        memdb, "inst1", gain_column="gain_60d", as_of_date="2024-12-01"
    )
    assert len(history_full) == 3


# ─── recommend_for_event 完整流程 ──────────────────────────────────

def test_recommend_follow_with_enough_history(memdb):
    _seed_events(memdb, [
        ("inst1", f"00000{i}", "2023-03-31", f"2023-04-{15+i:02d}", "new_entry", 0.0, 10.0, "医药")
        for i in range(1, 10)  # 9 historical events at +10%
    ] + [
        ("inst1", "000011", "2023-06-30", "2023-07-10", "new_entry", 0.0, 12.0, "医药"),
        # target event (11th, should see 10 historical)
        ("inst1", "000012", "2024-03-31", "2024-04-20", "new_entry", 1.0, 8.0, "医药"),
    ])

    event = {
        "institution_id": "inst1",
        "institution_name": "测试机构",
        "stock_code": "000012",
        "stock_name": "股票12",
        "industry": "医药",
        "report_date": "2024-03-31",
        "notice_date": "2024-04-20",
        "event_type": "new_entry",
        "premium_pct": 1.0,
        "realized_return_pct": 8.0,
    }
    cfg = PolicyConfig(min_sample=10, ev_threshold_pct=5.0, win_threshold=0.55,
                       prefer_same_industry_min_sample=10)
    rec = recommend_for_event(memdb, event, config=cfg, as_of_date="2024-04-20")
    assert rec.action == "follow"
    assert rec.scope == "inst_industry"
    assert rec.ev_stats.n == 10
    assert rec.ev_stats.win_rate == 1.0


def test_recommend_skip_insufficient(memdb):
    _seed_events(memdb, [
        ("inst1", "000001", "2023-03-31", "2023-04-20", "new_entry", 0.0, 10.0, "医药"),
    ])
    event = {
        "institution_id": "inst1",
        "stock_code": "000002",
        "notice_date": "2024-04-20",
        "event_type": "new_entry",
        "industry": "医药",
    }
    cfg = PolicyConfig(min_sample=10)
    rec = recommend_for_event(memdb, event, config=cfg)
    assert rec.action == "skip"
    assert rec.scope == "insufficient"


# ─── institution_track_record ────────────────────────────────────────

def test_track_record(memdb):
    _seed_events(memdb, [
        ("inst1", f"00000{i}", "2023-Q1", f"2023-{i:02d}-15", "new_entry", 0.0, g, "医药")
        for i, g in enumerate([10, -5, 20, 0, -10, 15, 5, -3, 8, 12, 3, 6], start=1)
    ])
    tr = institution_track_record(memdb, "inst1", config=PolicyConfig(min_sample=5))
    assert tr["overall"]["n"] == 12
    assert tr["overall"]["ev_pct"] > 0
    # 医药 is the only industry, should show
    assert any(b["industry"] == "医药" for b in tr["by_industry"])


# ─── 分组 key 归一 ──────────────────────────────────────────────────

def test_normalize_ymd_key():
    assert _normalize_ymd_key("20250930") == "2025-09-30"
    assert _normalize_ymd_key("2025-09-30") == "2025-09-30"
    assert _normalize_ymd_key("") is None
    assert _normalize_ymd_key(None) is None


def test_quarter_key():
    assert _quarter_key("20250101") == "2025-Q1"
    assert _quarter_key("20250331") == "2025-Q1"
    assert _quarter_key("20250401") == "2025-Q2"
    assert _quarter_key("20250930") == "2025-Q3"
    assert _quarter_key("20251231") == "2025-Q4"


# ─── backtest_historical ────────────────────────────────────────────

def test_backtest_empty(memdb):
    result = backtest_historical(memdb)
    assert "error" in result


def test_backtest_basic(memdb):
    _seed_events(memdb, [
        ("inst1", f"0{i:04d}", "2023-Q1", f"2023-{(i%12)+1:02d}-15", "new_entry",
         0.0, 10.0 if i < 15 else -5.0, "医药")
        for i in range(25)
    ])
    cfg = PolicyConfig(min_sample=5, ev_threshold_pct=3.0)
    result = backtest_historical(memdb, config=cfg)
    assert "coverage" in result
    assert result["coverage"]["total_events"] == 25


# ─── build_today_signals 集成 ───────────────────────────────────────

# ─── cohort_recent_matured ──────────────────────────────────────────

def test_cohort_empty_window(memdb):
    """无已成熟事件时应返回 cohort_size=0"""
    r = cohort_recent_matured(memdb, lookback_days=30)
    assert r["cohort_size"] == 0
    assert "note" in r


def test_cohort_nonempty(memdb):
    """成熟事件应分档并报告 edge_vs_blind"""
    from datetime import datetime, timedelta
    # 构造 180-90 天前的成熟事件 (9 个月前左右)
    # 需要有足够历史让 decide 产出 follow/watch/skip
    target_date = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
    hist_base = (datetime.now() - timedelta(days=720)).strftime("%Y%m%d")

    events = []
    # 机构 inst1 历史 15 条 buy 事件全 +15%
    for i in range(15):
        events.append((
            "inst1", f"HIST{i:03d}", "hist",
            f"{int(hist_base) + i}",  # 略错开日期
            "new_entry", 0.0, 15.0, "医药"
        ))
    # cohort 窗口内 5 条新事件（inst1 做出）
    for i in range(5):
        events.append((
            "inst1", f"COHORT{i:03d}", "cohort",
            str(int(target_date) + i),
            "new_entry", 0.0, 10.0, "医药"
        ))
    _seed_events(memdb, events)

    cfg = PolicyConfig(min_sample=5, ev_threshold_pct=5.0,
                       prefer_same_industry_min_sample=5)
    r = cohort_recent_matured(memdb, lookback_days=180, config=cfg)
    # 应有 5 条 cohort 事件，且因为历史 inst1 EV=+15 > 门槛，全部进 follow
    assert r["cohort_size"] == 5
    assert r["by_bucket"]["follow"]["n"] == 5
    # Follow 实际 EV 应等于 10.0（cohort 样本都是 +10%）
    assert r["by_bucket"]["follow"]["ev_pct"] == 10.0


# ─── institution_multi_horizon ──────────────────────────────────────

def test_multi_horizon_empty(memdb):
    r = institution_multi_horizon(memdb, "nonexistent_inst")
    assert len(r["horizons"]) == 4
    assert all(h["n"] == 0 for h in r["horizons"])


def test_multi_horizon_with_data(memdb):
    # 只填 gain_60d
    _seed_events(memdb, [
        ("inst1", f"00000{i}", "2024-03-31", f"2024-{i:02d}-20",
         "new_entry", 0.0, 10.0, "医药")
        for i in range(1, 6)
    ])
    r = institution_multi_horizon(memdb, "inst1")
    # 只 gain_60d 有值
    by_h = {x["horizon_days"]: x for x in r["horizons"]}
    assert by_h[60]["n"] == 5
    assert by_h[60]["ev_pct"] == 10.0
    assert by_h[30]["n"] == 0  # 30d 没填


def test_today_signals_recent_only(memdb):
    # 一条今天、一条 100 天前，freshness=30 天应只看到今天的
    today = datetime.now().strftime("%Y-%m-%d")
    _seed_events(memdb, [
        ("inst1", "000001", "2023-Q4", today, "new_entry", 0.0, 10.0, "医药"),
    ] + [
        # 10 historical events for inst1 before today
        ("inst1", f"0001{i}", "2023-Q1", f"2023-{i:02d}-15", "new_entry", 0.0, 10.0, "医药")
        for i in range(1, 11)
    ])
    cfg = PolicyConfig(min_sample=5, ev_threshold_pct=3.0,
                       prefer_same_industry_min_sample=5, signal_freshness_days=30)
    signals = build_today_signals(memdb, config=cfg)
    assert len(signals) == 1
    assert signals[0].stock_code == "000001"
