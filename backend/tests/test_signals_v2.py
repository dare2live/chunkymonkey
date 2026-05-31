"""
signals_v2 单元测试

覆盖：
    - PolicyConfig 默认值 + gain_column
    - compute_ev_stats 空样本 / 单样本 / 多样本
    - _decide_from_history 三档决策分支
    - recommend_for_event 完整流程
    - 历史严格左切（no look-ahead）
"""

import json
from datetime import datetime, timedelta

import pytest

from conftest import duck_mem
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
    ensure_today_signal_cache,
    load_today_signal_cache,
    materialize_today_signal_cache,
    migrate_today_signal_cache_payload,
    ensure_defaults,
    cohort_recent_matured,
    institution_multi_horizon,
)


# ─── fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def memdb():
    """In-memory DB with minimal schema + sample events."""
    conn = duck_mem()
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
        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT PRIMARY KEY,
            tdx_l1 TEXT, tdx_l2 TEXT, tdx_l3 TEXT
        );
        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            name TEXT, display_name TEXT, type TEXT, enabled INTEGER DEFAULT 1
        );
        CREATE TABLE inst_holdings (
            institution_id TEXT, stock_code TEXT, report_date TEXT,
            holder_rank INTEGER, hold_ratio REAL, hold_amount REAL,
            PRIMARY KEY (institution_id, stock_code, report_date)
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
            INSERT OR REPLACE INTO dim_stock_tdx_industry (stock_code, tdx_l1)
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
    # hist_base 在 cohort 窗口（90-270 天）之外、短窗口（365 天）之内
    # → 300 天前，距离 cohort 事件 180 天，仍在短窗口
    hist_base = (datetime.now() - timedelta(days=300)).strftime("%Y%m%d")

    events = []
    # 机构 inst1 历史 15 条 buy 事件全 +15%（在短窗口内，cohort 窗口外）
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

    # cooldown_days=0 让历史在短时间跨度内仍可用
    cfg = PolicyConfig(min_sample=5, ev_threshold_pct=5.0,
                       prefer_same_industry_min_sample=5,
                       cooldown_days=0, short_min_sample=5,
                       short_window_days=365)
    r = cohort_recent_matured(memdb, lookback_days=180, config=cfg)
    # 应有 5 条 cohort 事件
    assert r["cohort_size"] == 5
    # 短+长窗口都足够且 EV > 阈值 → 全部 follow
    assert r["by_bucket"]["follow"]["n"] == 5
    # Follow 实际 EV 应等于 10.0（cohort 样本都是 +10%）
    assert r["by_bucket"]["follow"]["ev_pct"] == 10.0


def test_cohort_follow_quarterly_breakdown(memdb):
    """follow/watch 档应返回 quarterly 字段，揭示样本按季度分布（Phase A 后的诚实披露）"""
    from datetime import datetime, timedelta
    target_date = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
    hist_base = (datetime.now() - timedelta(days=300)).strftime("%Y%m%d")

    events = []
    for i in range(15):
        events.append((
            "inst1", f"HIST{i:03d}", "hist",
            f"{int(hist_base) + i}",
            "new_entry", 0.0, 15.0, "医药"
        ))
    # 5 个 cohort 事件都在同一日期范围，应聚到同一季度
    for i in range(5):
        events.append((
            "inst1", f"COHORT{i:03d}", "cohort",
            str(int(target_date) + i),
            "new_entry", 0.0, 10.0, "医药"
        ))
    _seed_events(memdb, events)

    cfg = PolicyConfig(min_sample=5, ev_threshold_pct=5.0,
                       prefer_same_industry_min_sample=5,
                       cooldown_days=0, short_min_sample=5,
                       short_window_days=365)
    r = cohort_recent_matured(memdb, lookback_days=180, config=cfg)
    follow = r["by_bucket"]["follow"]
    assert "quarterly" in follow
    # 5 个 cohort 事件全部相近日期 → 只有 1 个季度桶，n=5
    assert len(follow["quarterly"]) >= 1
    total_n = sum(q["n"] for q in follow["quarterly"])
    assert total_n == follow["n"]
    # skip 档不加 quarterly（没 alpha 可披露，保持简洁）
    assert "quarterly" not in r["by_bucket"]["skip"]


def test_cohort_dual_window_divergence(memdb):
    """短口径样本不足时应降档为 watch（不再是老的单一 follow 决策）"""
    from datetime import datetime, timedelta
    base_now = datetime.now()
    # 用真实日历算术生成 notice_date, 避免 int(YYYYMMDD)+i 跨月时产出非法日期 (如 20251232).
    # 非法日期会让 _shift_date 返回 None, 短窗口 fallback 为全历史, 测试逻辑被破坏.
    target_anchor = base_now - timedelta(days=120)
    hist_anchor = base_now - timedelta(days=720)  # 远超 365

    events = []
    for i in range(15):
        nd = (hist_anchor + timedelta(days=i)).strftime("%Y%m%d")
        events.append((
            "inst1", f"HIST{i:03d}", "hist",
            nd,
            "new_entry", 0.0, 15.0, "医药"
        ))
    for i in range(5):
        nd = (target_anchor + timedelta(days=i)).strftime("%Y%m%d")
        events.append((
            "inst1", f"COHORT{i:03d}", "cohort",
            nd,
            "new_entry", 0.0, 10.0, "医药"
        ))
    _seed_events(memdb, events)

    cfg = PolicyConfig(min_sample=5, ev_threshold_pct=5.0,
                       prefer_same_industry_min_sample=5,
                       cooldown_days=0, short_min_sample=5,
                       short_window_days=365)
    r = cohort_recent_matured(memdb, lookback_days=180, config=cfg)
    assert r["cohort_size"] == 5
    # 长窗口有数据（15 条历史）、短窗口空（历史太老）→ 降档 watch
    assert r["by_bucket"]["follow"]["n"] == 0
    assert r["by_bucket"]["watch"]["n"] == 5


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


def test_today_signal_cache_returns_last_materialized_result(memdb):
    today = datetime.now()
    initial_notice_iso = (today - timedelta(days=2)).strftime("%Y-%m-%d")
    newer_notice_iso = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    _seed_events(memdb, [
        ("inst1", "000001", "2023-Q4", initial_notice_iso, "new_entry", 0.0, 10.0, "医药"),
    ] + [
        ("inst1", f"0001{i}", "2023-Q1", f"2023-{i:02d}-15", "new_entry", 0.0, 10.0, "医药")
        for i in range(1, 11)
    ])
    cfg = PolicyConfig(min_sample=5, ev_threshold_pct=3.0,
                       prefer_same_industry_min_sample=5, signal_freshness_days=30)

    materialized = materialize_today_signal_cache(memdb, config=cfg, freshness_days=30)
    cached = load_today_signal_cache(memdb, config=cfg, freshness_days=30)
    cache_row = memdb.execute(
        """
        SELECT cache_key, signals_json, signal_count
          FROM mart_today_signal_cache
         LIMIT 1
        """
    ).fetchone()
    detail_count = memdb.execute(
        """
        SELECT COUNT(*) AS n
          FROM mart_today_signal_cache_signal
         WHERE cache_key = ?
        """,
        (cache_row["cache_key"],),
    ).fetchone()["n"]
    _seed_events(memdb, [
        ("inst1", "000099", "2023-Q4", newer_notice_iso, "new_entry", 0.0, 10.0, "医药"),
    ])
    stale_cached = load_today_signal_cache(memdb, config=cfg, freshness_days=30)

    assert materialized["summary"]["cache"]["status"] == "refreshed"
    assert cached["summary"]["cache"]["status"] == "hit"
    assert cached["summary"]["total"] == 1
    assert cached["signals"][0]["stock_code"] == "000001"
    assert cache_row["signals_json"] == "[]"
    assert detail_count == cache_row["signal_count"] == 1
    assert stale_cached["summary"]["cache"]["stale"] is True
    assert stale_cached["summary"]["total"] == 1
    assert stale_cached["signals"][0]["stock_code"] == "000001"


def test_today_signal_cache_migrates_legacy_payload_to_detail_table(memdb):
    cfg = PolicyConfig(min_sample=5, ev_threshold_pct=3.0)
    ensure_today_signal_cache(memdb)
    legacy_payload = [
        {"stock_code": "000001", "stock_name": "股票000001", "action": "follow"},
        {"stock_code": "000002", "stock_name": "股票000002", "action": "watch"},
    ]
    materialized = materialize_today_signal_cache(memdb, config=cfg, freshness_days=30)
    cache_key = materialized["cache"]["cache_key"]
    policy_hash = materialized["cache"]["policy_hash"]
    memdb.execute("DELETE FROM mart_today_signal_cache_signal WHERE cache_key = ?", (cache_key,))
    memdb.execute(
        """
        UPDATE mart_today_signal_cache
           SET summary_json = ?,
               signals_json = ?,
               signal_count = ?
         WHERE cache_key = ?
        """,
        (
            '{"total": 2}',
            json.dumps(legacy_payload, ensure_ascii=False, sort_keys=True),
            len(legacy_payload),
            cache_key,
        ),
    )

    dry_run = migrate_today_signal_cache_payload(memdb, execute=False)
    migrated = migrate_today_signal_cache_payload(memdb, execute=True)
    cached = load_today_signal_cache(memdb, config=cfg, freshness_days=30)
    cache_row = memdb.execute(
        """
        SELECT signals_json, signal_count
          FROM mart_today_signal_cache
         WHERE cache_key = ?
           AND policy_hash = ?
        """,
        (cache_key, policy_hash),
    ).fetchone()
    detail_count = memdb.execute(
        """
        SELECT COUNT(*) AS n
          FROM mart_today_signal_cache_signal
         WHERE cache_key = ?
        """,
        (cache_key,),
    ).fetchone()["n"]

    assert dry_run["execute"] is False
    assert dry_run["rows_to_migrate"] == 1
    assert dry_run["signals_to_migrate"] == 2
    assert migrated["execute"] is True
    assert migrated["payload_bytes_after"] == 2
    assert cache_row["signals_json"] == "[]"
    assert cache_row["signal_count"] == 2
    assert detail_count == 2
    assert [signal["stock_code"] for signal in cached["signals"]] == ["000001", "000002"]


def test_today_signal_cache_migration_dry_run_does_not_create_tables(memdb):
    result = migrate_today_signal_cache_payload(memdb, execute=False)
    tables = {
        row["table_name"]
        for row in memdb.execute(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_name LIKE 'mart_today_signal_cache%'
            """
        ).fetchall()
    }

    assert result == {
        "execute": False,
        "rows_scanned": 0,
        "rows_to_migrate": 0,
        "signals_to_migrate": 0,
        "payload_bytes_before": 0,
        "payload_bytes_after": 0,
    }
    assert tables == set()


def test_today_signals_exclude_future_notice_dates(memdb):
    today = datetime.now()
    today_iso = today.strftime("%Y-%m-%d")
    tomorrow_iso = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    _seed_events(memdb, [
        ("inst1", "000001", "2023-Q4", today_iso, "new_entry", 0.0, 10.0, "医药"),
        ("inst1", "000099", "2023-Q4", tomorrow_iso, "new_entry", 0.0, 10.0, "医药"),
    ] + [
        ("inst1", f"0001{i}", "2023-Q1", f"2023-{i:02d}-15", "new_entry", 0.0, 10.0, "医药")
        for i in range(1, 11)
    ])
    cfg = PolicyConfig(min_sample=5, ev_threshold_pct=3.0,
                       prefer_same_industry_min_sample=5, signal_freshness_days=30)

    signals = build_today_signals(memdb, config=cfg)

    assert [signal.stock_code for signal in signals] == ["000001"]


def test_today_signals_expose_notice_source_lineage(memdb):
    memdb.execute("ALTER TABLE fact_institution_event ADD COLUMN notice_date_source TEXT")
    memdb.execute("ALTER TABLE fact_institution_event ADD COLUMN source_notice_date TEXT")
    memdb.execute("ALTER TABLE fact_institution_event ADD COLUMN availability_deadline TEXT")
    today_iso = datetime.now().strftime("%Y-%m-%d")
    _seed_events(memdb, [
        ("inst1", "000001", "2023-Q4", today_iso, "new_entry", 0.0, 10.0, "医药"),
    ] + [
        ("inst1", f"0001{i}", "2023-Q1", f"2023-{i:02d}-15", "new_entry", 0.0, 10.0, "医药")
        for i in range(1, 11)
    ])
    memdb.execute(
        """
        UPDATE fact_institution_event
           SET notice_date_source = 'source_notice',
               source_notice_date = notice_date
         WHERE stock_code = '000001'
        """
    )
    cfg = PolicyConfig(min_sample=5, ev_threshold_pct=3.0,
                       prefer_same_industry_min_sample=5, signal_freshness_days=30)

    signals = build_today_signals(memdb, config=cfg)
    payload = signals[0].to_dict()

    assert payload["stock_code"] == "000001"
    assert payload["notice_date_source"] == "source_notice"
    assert payload["source_notice_date"] == today_iso
    assert payload["availability_deadline"] is None


# ─── rule_breakdown (Step 2.5) ───────────────────────────────────────

def test_build_rule_breakdown_seven_checks_default_shape():
    from services.signals_v2 import _build_rule_breakdown

    event = {}  # 所有原始值都缺失
    cfg = PolicyConfig()
    bd = _build_rule_breakdown(event, cfg, None)
    assert bd["triggered"] is None
    keys = [c["key"] for c in bd["checks"]]
    assert keys == [
        "inst_type", "premium_pct", "hold_ratio",
        "holder_count_yoy", "forecast_profit_yoy_mid", "future_unlock_ratio_180d",
        "survey_count_90d",
    ]
    # 全部原始值缺失时 status=unknown
    assert all(c["status"] == "unknown" for c in bd["checks"])


def test_build_rule_breakdown_pass_fail_unknown_states():
    from services.signals_v2 import _build_rule_breakdown

    cfg = PolicyConfig(
        max_premium_pct=15.0,
        min_hold_ratio=0.3,
        max_holder_yoy_pct=30.0,
        min_forecast_profit_yoy=20.0,
        max_unlock_ratio_180d=5.0,
        inst_type_blacklist="基金,国家队",
    )
    # 混合：部分通过、部分失败、部分缺失
    event = {
        "inst_type": "基金",              # fail (在黑名单)
        "premium_pct": 8.0,               # pass
        "hold_ratio": 0.1,                # fail (<0.3)
        "holder_count_yoy": 45.0,         # fail (>30)
        # forecast_profit_yoy_mid 缺失 → unknown
        "future_unlock_ratio_180d": 3.0,  # pass
    }
    bd = _build_rule_breakdown(event, cfg, "inst_type_blacklisted")
    checks_by_key = {c["key"]: c for c in bd["checks"]}
    assert bd["triggered"] == "inst_type_blacklisted"
    assert checks_by_key["inst_type"]["status"] == "fail"
    assert checks_by_key["premium_pct"]["status"] == "pass"
    assert checks_by_key["hold_ratio"]["status"] == "fail"
    assert checks_by_key["holder_count_yoy"]["status"] == "fail"
    assert checks_by_key["forecast_profit_yoy_mid"]["status"] == "unknown"
    assert checks_by_key["future_unlock_ratio_180d"]["status"] == "pass"
    # raw 值透传
    assert checks_by_key["premium_pct"]["raw"] == 8.0
    assert checks_by_key["holder_count_yoy"]["raw"] == 45.0
    # threshold_display 包含阈值字串
    assert "15.0" in checks_by_key["premium_pct"]["threshold_display"]
    assert "30.0" in checks_by_key["holder_count_yoy"]["threshold_display"]


def test_recommend_for_event_includes_rule_breakdown(memdb):
    _seed_events(memdb, [
        ("inst1", f"0001{i:02d}", "2023-Q1", f"2023-{(i%12)+1:02d}-15",
         "new_entry", 0.0, 10.0, "医药")
        for i in range(10)
    ])
    event = {
        "institution_id": "inst1",
        "stock_code": "000099",
        "stock_name": "股票99",
        "industry": "医药",
        "report_date": "2024-03-31",
        "notice_date": "2024-04-20",
        "event_type": "new_entry",
        "premium_pct": 3.0,
        "holder_count_yoy": 10.0,          # pass
        "forecast_profit_yoy_mid": 50.0,   # pass
    }
    cfg = PolicyConfig(min_sample=5, prefer_same_industry_min_sample=5)
    rec = recommend_for_event(memdb, event, config=cfg, as_of_date="2024-04-20")
    assert rec.rule_breakdown is not None
    assert rec.rule_breakdown["triggered"] is None  # 无硬规则命中
    assert len(rec.rule_breakdown["checks"]) == 7
    # to_dict 也把 rule_breakdown 透传
    d = rec.to_dict()
    assert "rule_breakdown" in d
    assert d["rule_breakdown"]["checks"][1]["key"] == "premium_pct"
    assert d["rule_breakdown"]["checks"][6]["key"] == "survey_count_90d"


# ─── D8 机构调研 ─────────────────────────────────────────────────────

def test_d8_apply_hard_rule_skip_when_enabled_and_below_threshold():
    from services.signals_v2 import _apply_hard_rules

    cfg = PolicyConfig(min_survey_count_90d=2)
    event = {"inst_type": "牛散", "premium_pct": 5.0, "survey_count_90d": 1}
    action, label = _apply_hard_rules(event, cfg)
    assert action == "skip"
    assert label == "survey_too_quiet"


def test_d8_passes_when_disabled():
    from services.signals_v2 import _apply_hard_rules

    cfg = PolicyConfig(min_survey_count_90d=0)
    event = {"inst_type": "牛散", "premium_pct": 5.0, "survey_count_90d": 0}
    action, label = _apply_hard_rules(event, cfg)
    assert action is None
    assert label is None


def test_d8_breakdown_displays_unavailable_when_disabled():
    from services.signals_v2 import _build_rule_breakdown

    cfg = PolicyConfig(min_survey_count_90d=0)
    event = {"survey_count_90d": 3}
    bd = _build_rule_breakdown(event, cfg, None)
    d8 = next(c for c in bd["checks"] if c["key"] == "survey_count_90d")
    assert d8["threshold_display"] == "未启用"
    assert d8["status"] == "pass"
    assert d8["raw"] == 3


def test_d8_count_surveys_as_of_respects_notice_date():
    """survey.notice_date 必须 <= event.notice_date，否则算 look-ahead。"""
    from services.signals_v2 import _count_surveys_as_of

    surveys = [
        ("2024-01-10", "2024-01-15"),  # ok: in window, disclosed before
        ("2024-02-01", "2024-02-05"),  # ok: in window
        ("2024-03-15", "2024-03-20"),  # future disclosure → excluded
        ("2023-10-01", "2023-10-05"),  # too old → excluded
    ]
    n = _count_surveys_as_of(surveys, "2024-03-01", window_days=90)
    assert n == 2


def test_d8_enrichment_integrates_with_recommend(memdb):
    memdb.executescript("""
        CREATE TABLE IF NOT EXISTS raw_institution_surveys (
            stock_code TEXT, survey_date TEXT, notice_date TEXT,
            inst_count INTEGER,
            PRIMARY KEY (stock_code, survey_date, notice_date)
        );
    """)
    memdb.executemany(
        "INSERT INTO raw_institution_surveys(stock_code, survey_date, notice_date, inst_count) VALUES (?,?,?,?)",
        [
            ("000088", "2024-03-01", "2024-03-05", 5),
            ("000088", "2024-03-20", "2024-03-25", 3),
        ],
    )
    memdb.commit()

    _seed_events(memdb, [
        ("inst1", f"0001{i:02d}", "2023-Q1", f"2023-{(i%12)+1:02d}-15",
         "new_entry", 0.0, 10.0, "医药")
        for i in range(10)
    ])
    event = {
        "institution_id": "inst1",
        "stock_code": "000088",
        "stock_name": "股票88",
        "industry": "医药",
        "report_date": "2024-03-31",
        "notice_date": "2024-04-20",
        "event_type": "new_entry",
        "premium_pct": 3.0,
    }

    from services.signals_v2 import _enrich_events_with_gpcw
    evs = [event]
    _enrich_events_with_gpcw(memdb, evs)
    assert evs[0]["survey_count_90d"] == 2  # 两条调研都在 90d 窗口内
