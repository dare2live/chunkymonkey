"""主升浪 ground-truth 证伪门单测 (owner=docs/strategy_validation_contract.md §8.1)。

1. 边界压线: 合成序列 gain=0.599 拒 / 0.601 收 (定义压线, 真 yaml 阈值非测试复制品);
2. holdout red-green: data_end 越界 raise (入口第一行, 不碰库) / train 窗内落库 + 右删失 embargo 剔除;
3. 负样本 purge: 同股正样本 bottom ±max_forward_days 根内 pivot 不取;
4. 列契约: outcome (bull_aligned 等) 做 X 即 raise;
5. universe: 北交所前缀端到端拦截 + 硬门 (前缀过滤被绕过时 assert_universe_clean 兜底 raise,
   证明闸真会触发 — 死闸反例防御)。

合成 K 线 fixture: 长底 130 根 (贴底带内) + 唯一 pivot 低点 + 单调拉升 (∃日多头排列/平滑) + 缓落尾。
所有阈值从 backend/config/rally_gt.yaml 读 (不 hardcode 第二真相源); 日历/K线走内存 catalog (mk/ref/raw)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import duck_mem
from services import rally_detect as rd
from services import rally_gt
from services.gt_label_contract import assert_no_outcome_leakage, entry_anchor, label_column
from services.holdout_guard import HoldoutBoundaryViolation, load_policy, training_cutoff_before_holdout
from services.universe import UniverseContaminationError

CFG = rally_gt.load_config()
EP = CFG["episode"]


# ── 合成 K 线 ────────────────────────────────────────────────────────────────────

N_BASE = 130          # 长底段长度 (> base_lookback_days=120, 覆盖回看窗)
RISE = 80             # 拉升段 (>= min_duration_days=20, 单调 → ∃日多头排列 + max_dd=0)
BOTTOM_LOW = 10.0     # 底价 (gain 分母)


def make_rally_bars(gain: float, n_total: int = 400):
    """单股合成 K 线: 长底 + 底 (唯一最低) + 单调拉升到 BOTTOM_LOW*(1+gain) + 缓落尾。

    返回 (highs, lows, closes) ndarray; bottom_idx=N_BASE, peak_idx=N_BASE+RISE。
    """
    peak_px = BOTTOM_LOW * (1.0 + gain)
    closes, highs, lows = [], [], []
    for k in range(n_total):
        if k < N_BASE:                       # 长底: 贴底带内严格缓降 (防并列最低造伪 pivot), lows 不低于底
            c = 10.30 - 0.001 * k
            closes.append(c); highs.append(c + 0.05); lows.append(c)
        elif k == N_BASE:                    # 波段底 (±pivot_low_window 内唯一最低)
            closes.append(10.02); highs.append(10.1); lows.append(BOTTOM_LOW)
        elif k <= N_BASE + RISE:             # 单调拉升 → 峰
            frac = (k - N_BASE) / RISE
            c = 10.02 + (peak_px - 10.02) * frac
            closes.append(c); highs.append(c); lows.append(c - 0.01)
        else:                                # 缓落尾 (峰保持全局最高)
            c = peak_px * (0.998 ** (k - N_BASE - RISE))
            closes.append(c); highs.append(c); lows.append(c - 0.01)
    return np.array(highs), np.array(lows), np.array(closes)


def make_dates(n: int) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2019-01-01", periods=n)]


def detect(gain: float, n_total: int = 400):
    highs, lows, closes = make_rally_bars(gain, n_total)
    dates = np.array(make_dates(n_total))
    return rally_gt.detect_episodes("600001", dates, highs, lows, closes, {}, CFG)


# ── 1. 边界压线 (定义证伪门) ─────────────────────────────────────────────────────

def test_gain_boundary_0601_accepted():
    eps = detect(0.601)
    assert len(eps) == 1
    e = eps[0]
    assert e["gain_to_peak_pct"] >= float(EP["gain_min"])
    assert e["peak_offset_days"] >= int(EP["min_duration_days"])
    assert e["base_days"] >= int(EP["base_min_days"])
    assert e["bull_aligned"] is True
    assert e["path_max_dd_pct"] > float(EP["dd_floor"])


def test_gain_boundary_0599_rejected():
    assert detect(0.599) == []


def test_gain_exact_060_accepted():
    # 压线含边界: 定义是 >= 0.60 (v1.5 parquet 实测 min(gain)=0.6000)
    eps = detect(0.60)
    assert len(eps) == 1


# ── 2. holdout red-green + 右删失 embargo ───────────────────────────────────────

def _mk_env(kline_rows, calendar_dates, symbols, st_rows=(), member_rows=(),
            segment_rows=(), form_rows=()):
    """内存 feature_store conn (ATTACH mk/ref/sm) + 独立 raw conn (默认 catalog = tushare_raw 表)。"""
    conn = duck_mem()
    conn.execute("ATTACH ':memory:' AS mk")
    conn.execute("ATTACH ':memory:' AS ref")
    conn.execute("ATTACH ':memory:' AS sm")
    conn.execute("CREATE TABLE mk.price_kline_qfq_tushare "
                 "(code VARCHAR, date VARCHAR, high DOUBLE, low DOUBLE, close DOUBLE)")
    conn.executemany("INSERT INTO mk.price_kline_qfq_tushare VALUES (?,?,?,?,?)", kline_rows)
    conn.execute("CREATE TABLE ref.dim_trading_calendar (trade_date VARCHAR, is_trading BIGINT)")
    conn.executemany("INSERT INTO ref.dim_trading_calendar VALUES (?, 1)", [(d,) for d in calendar_dates])
    # B1/B2 特征面 (trade_date = YYYYMMDD VARCHAR; 生产表 join 所读列子集)
    conn.execute("CREATE TABLE sm.dim_stock_segment_daily (stock_code VARCHAR, trade_date VARCHAR, "
                 "mktcap_seg VARCHAR, turnover_seg VARCHAR, vol_regime VARCHAR)")
    if segment_rows:
        conn.executemany("INSERT INTO sm.dim_stock_segment_daily VALUES (?,?,?,?,?)", segment_rows)
    conn.execute("CREATE TABLE sm.fact_stock_form_daily (stock_code VARCHAR, trade_date VARCHAR, "
                 "axis_pos VARCHAR, axis_purity VARCHAR)")
    if form_rows:
        conn.executemany("INSERT INTO sm.fact_stock_form_daily VALUES (?,?,?,?)", form_rows)
    # S7: identity publication = dim_active_a_stock (not raw stock_basic)
    conn.execute(
        "CREATE TABLE ref.dim_active_a_stock "
        "(stock_code VARCHAR, stock_name VARCHAR, market VARCHAR)"
    )
    conn.executemany(
        "INSERT INTO ref.dim_active_a_stock VALUES (?,?,?)",
        [(s, s, "SH") for s in symbols],
    )
    raw = duck_mem()
    raw.execute("CREATE TABLE raw_tushare_stock_st (ts_code VARCHAR, trade_date VARCHAR)")
    if st_rows:
        raw.executemany("INSERT INTO raw_tushare_stock_st VALUES (?,?)", st_rows)
    raw.execute("CREATE TABLE raw_tushare_index_member_all (l1_code VARCHAR, l1_name VARCHAR, "
                "l2_code VARCHAR, l2_name VARCHAR, ts_code VARCHAR, in_date VARCHAR, out_date INTEGER)")
    if member_rows:
        raw.executemany("INSERT INTO raw_tushare_index_member_all VALUES (?,?,?,?,?,?,?)", member_rows)
    return conn, raw


def _kline_rows(code: str, gain: float = 0.65, n_total: int = 400, dates=None):
    highs, lows, closes = make_rally_bars(gain, n_total)
    dates = dates or make_dates(n_total)
    return [(code, dates[k], float(highs[k]), float(lows[k]), float(closes[k]))
            for k in range(n_total)]


def test_holdout_red_data_end_beyond_boundary_raises():
    hs = str(load_policy()["holdout_start"])          # 20250601 (yaml 唯一真相源)
    beyond = str(int(hs) + 100)                       # 越界一个月+
    with pytest.raises(HoldoutBoundaryViolation):
        rally_gt.rebuild(conn=None, data_end=beyond)  # 入口第一行守门, 不碰任何库


def test_holdout_start_itself_is_rejected_and_default_is_strictly_before():
    hs = str(load_policy()["holdout_start"])
    with pytest.raises(HoldoutBoundaryViolation):
        rally_gt.rebuild(conn=None, data_end=hs)
    assert training_cutoff_before_holdout() < hs


def test_holdout_green_and_embargo_censoring():
    """train 窗内: 早 rally (forward 窗完整) 落库; 晚 rally (bottom+250td 跨 data_end) 右删失剔除。"""
    all_dates = make_dates(700)
    # 早 rally 全程有 K线到数据末 (非退市): 底@130, 130+250=380 <= 699 → 完整
    early = _kline_rows("600001", 0.65, 700, dates=all_dates)
    # 晚 rally: 同形态但整体右移 350 根 → 底@480, 480+250=730 > 699 → censored
    late = _kline_rows("600002", 0.65, 350, dates=all_dates[350:700])
    data_end = all_dates[699].replace("-", "")
    bottom_ymd = all_dates[N_BASE].replace("-", "")
    prev_ymd = all_dates[N_BASE - 1].replace("-", "")
    conn, raw = _mk_env(
        early + late, all_dates, ["600001", "600002"],
        member_rows=[("801080.SI", "电子", "801081.SI", "半导体", "600001.SH", "20180101", None)],
        # B1: 底前一日 + 底日两行 → ASOF 必须取底日行 (as-of 最近, 非任意/最早)
        segment_rows=[("600001", prev_ymd, "mid", "low", "low_vol"),
                      ("600001", bottom_ymd, "small", "high", "high_vol")],
        # B2: 底日精确行
        form_rows=[("600001", bottom_ymd, "bottom_zone", "clean")])
    try:
        stats = rally_gt.rebuild(conn=conn, data_end=data_end, raw_conn=raw)
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM fact_rally_ground_truth").fetchall()]
        assert codes == ["600001"]                     # green: 完整 episode 落库
        assert stats["funnel"]["E_embargo_censored"] >= 1   # red: 跨界 episode 被 embargo 剔除
        assert "600002" not in codes
        # 落库字段与列契约同步 (landing 断言已核, 这里抽查锚/label)
        row = conn.execute("SELECT bottom_date, is_true_rally, taxonomy_version "
                           "FROM fact_rally_ground_truth").fetchone()
        assert str(row[0]) == all_dates[N_BASE]
        assert row[1] is True
        assert row[2] == str(CFG["taxonomy_version"])
        # strata 1:1 + 申万 as-of 接上 + B1 ASOF 取底日行(非前日) + B2 底日精确对照
        srow = conn.execute(
            "SELECT sw_l1_name, base_bucket, mktcap_seg, turnover_seg, vol_regime, "
            "axis_pos, axis_purity FROM fact_rally_strata").fetchone()
        assert srow[0] == "电子"
        assert srow[1] is not None
        assert (srow[2], srow[3], srow[4]) == ("small", "high", "high_vol")  # 底日行, 非 prev 的 mid/low
        assert (srow[5], srow[6]) == ("bottom_zone", "clean")
    finally:
        conn.close(); raw.close()


def test_strata_b1_join_break_raises_and_structural_null_passes():
    """B1/B2 landing 门 red-green: 源表有行而 strata NULL = join 失灵 raise;
    源表无行 (warmup 前结构性 NULL) = 放行。"""
    all_dates = make_dates(700)
    rows = _kline_rows("600001", 0.65, 700, dates=all_dates)
    data_end = all_dates[699].replace("-", "")
    bottom_ymd = all_dates[N_BASE].replace("-", "")
    conn, raw = _mk_env(
        rows, all_dates, ["600001"],
        segment_rows=[("600001", bottom_ymd, "small", "high", "high_vol")],
        form_rows=[])   # B2 无行 → axis 结构性 NULL, 门放行 (green 面)
    try:
        stats = rally_gt.rebuild(conn=conn, data_end=data_end, raw_conn=raw)
        assert stats["n_pos"] == 1
        got = conn.execute("SELECT mktcap_seg, axis_pos FROM fact_rally_strata").fetchone()
        assert (got[0], got[1]) == ("small", None)   # B1 接上, B2 结构性 NULL
        # red 面: 人为掐断 B1 值 → 源表有 as-of 行而 strata NULL, 门必 raise
        conn.execute("UPDATE fact_rally_strata SET mktcap_seg = NULL")
        with pytest.raises(rally_gt.RallyGTLandingError, match="B1 join"):
            rally_gt._landing_assertions(conn, CFG, rally_gt._to_iso(data_end),
                                         all_dates, all_dates[699])
    finally:
        conn.close(); raw.close()


# ── 3. 负样本 purge ±max_forward_days ───────────────────────────────────────────

def _flat_bars_with_dips(n: int, dip_idx: list[int]):
    """近平底序列 (lows 严格缓降 → 仅 dip 是 pivot) + 指定下标挖唯一低点; 前瞻涨幅恒 << gain_min。"""
    closes = np.full(n, 10.0)
    lows = np.array([10.5 - 0.0005 * k for k in range(n)])
    highs = closes + 0.02
    for j in dip_idx:
        lows[j] = 9.5 - 0.001 * j   # 各 dip 唯一最低 (低于任何缓降 base low)
    return highs, lows, closes


def test_negative_purge_and_gap():
    n = 800
    maxfwd = int(EP["max_forward_days"])
    highs, lows, closes = _flat_bars_with_dips(n, [200, 300])
    dates = np.array(make_dates(n))
    trading_days = make_dates(n)
    pos_idx = [500]     # 假想同股正样本 bottom @500
    negs = rally_gt.detect_negatives("600001", dates, highs, lows, closes, pos_idx,
                                     trading_days, trading_days[-1], CFG)
    got_idx = [make_dates(n).index(d) for _, d, _ in negs]
    assert 200 in got_idx                         # |200-500|=300 >= 250 → 保留
    assert 300 not in got_idx                     # |300-500|=200 < 250 → purge
    for _, d, base in negs:
        assert base >= int(EP["base_min_days"])   # 与正样本同 PIT setup
    # 未涨定义: 全部 forward gain < gain_min
    for _, d, _ in negs:
        i = make_dates(n).index(d)
        assert rd.forward_max_gain(highs, lows, i, maxfwd) < float(EP["gain_min"])


def test_purge_set_includes_embargo_censored_bottoms():
    """修3 (2026-07-03 审计): 被右删失 embargo 剔出 train 的 L4-真 bottom 仍须进负样本 purge 集 —
    其 ±max_forward_days 根内 pivot 是紧邻(删失)主升浪底的污染负样本, 不得落 fact_rally_negative
    (修前: kept_bottoms 在 embargo continue 之后收集 → censored bottom 不进 purge, 该 pivot 入库)。"""
    L = 700
    maxfwd = int(EP["max_forward_days"])
    dates = make_dates(L)
    # 600001: 早 rally 完整落库 (保证 GT 非空, landing 断言可过)
    rows_a = _kline_rows("600001", 0.65, L, dates=dates)
    # 600002: 平底 (lows 严格缓降, 无伪 pivot) + 负样本候选 pivot p=379 + censored rally 底 b=619
    #   b+250 > 699 → embargo 删失; |p-b|=240 < 250 → p 必须被 purge;
    #   p 自身: forward 窗 [380..629] 完整, 窗内最高 ≈ 拉升前 10 根 (~10.8) → gain ~16% < gain_min。
    p_idx, b_idx, rise = 379, 619, 80
    peak_px = 16.5                              # 16.5/9.2-1 = 79% >= gain_min (b 的 rally)
    closes = np.full(L, 10.0)
    lows = np.array([10.5 - 0.0005 * k for k in range(L)])
    highs = closes + 0.02
    lows[p_idx] = 9.3                           # 负样本候选 pivot (base: closes 10.0 贴 9.3 带内)
    closes[b_idx], lows[b_idx], highs[b_idx] = 10.02, 9.2, 10.1
    for k in range(b_idx + 1, L):               # b 后单调拉升 80 根到峰 (bull_align/平滑天然满足)
        frac = (k - b_idx) / rise
        cpx = 10.02 + (peak_px - 10.02) * frac
        closes[k], highs[k], lows[k] = cpx, cpx, cpx - 0.01
    rows_b = [("600002", dates[k], float(highs[k]), float(lows[k]), float(closes[k]))
              for k in range(L)]
    conn, raw = _mk_env(rows_a + rows_b, dates, ["600001", "600002"])
    try:
        stats = rally_gt.rebuild(conn=conn, data_end=dates[-1].replace("-", ""), raw_conn=raw)
        assert stats["funnel"]["E_embargo_censored"] >= 1, "前提: b 必须真被 embargo 删失"
        gt_codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM fact_rally_ground_truth").fetchall()]
        assert gt_codes == ["600001"], "censored episode 不落 GT (embargo 语义不变)"
        neg_dates = [r[0] for r in conn.execute(
            "SELECT CAST(entry_signal_date AS VARCHAR) FROM fact_rally_negative "
            "WHERE stock_code = '600002'").fetchall()]
        assert dates[p_idx] not in neg_dates, (
            f"purge 违规: censored bottom {dates[b_idx]} 的 ±{maxfwd} 根内 pivot "
            f"{dates[p_idx]} 落进负样本 (修前行为)")
    finally:
        conn.close(); raw.close()


def test_negative_rejects_incomplete_forward_window():
    n = 400
    highs, lows, closes = _flat_bars_with_dips(n, [200])
    dates = np.array(make_dates(n))
    trading_days = make_dates(n)
    # 数据边缘 = 底后仅 100 交易日 → forward 窗不完整 → 不当负样本
    negs = rally_gt.detect_negatives("600001", dates[:301], highs[:301], lows[:301], closes[:301],
                                     [], trading_days[:301], trading_days[300], CFG)
    assert negs == []


# ── 4. 列契约执法 (修正#4: outcome 做 X 即 raise) ────────────────────────────────

def test_contract_outcome_as_x_raises():
    with pytest.raises(ValueError, match="bull_aligned"):
        assert_no_outcome_leakage(rally_gt.CONTRACT, ["base_days", "bull_aligned"])
    with pytest.raises(ValueError, match="gain_to_peak_pct"):
        assert_no_outcome_leakage(rally_gt.CONTRACT, ["gain_to_peak_pct"])


def test_contract_clean_pit_features_pass():
    assert_no_outcome_leakage(rally_gt.CONTRACT, ["base_days"])   # 不 raise
    assert entry_anchor(rally_gt.CONTRACT) == "bottom_date"
    assert label_column(rally_gt.CONTRACT) == "is_true_rally"


# ── 5. universe 硬门 ─────────────────────────────────────────────────────────────

def test_universe_bj_prefix_filtered_end_to_end():
    """北交所 83 前缀同形态股: 端到端不进 GT (L1 前缀门)。"""
    n = 400
    dates = make_dates(n)
    rows = _kline_rows("600001", 0.65, n, dates=dates) + _kline_rows("830001", 0.65, n, dates=dates)
    conn, raw = _mk_env(rows, dates, ["600001", "830001"])
    try:
        rally_gt.rebuild(conn=conn, data_end=dates[-1].replace("-", ""), raw_conn=raw)
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM fact_rally_ground_truth").fetchall()]
        assert codes == ["600001"]
    finally:
        conn.close(); raw.close()


def test_universe_hard_gate_fires_when_prefix_filter_bypassed(monkeypatch):
    """死闸防御: 即使上游前缀过滤被绕过 (内联白名单/bug), assert_universe_clean 兜底 raise。"""
    n = 400
    dates = make_dates(n)
    rows = _kline_rows("830001", 0.65, n, dates=dates)
    conn, raw = _mk_env(rows, dates, ["830001"])
    monkeypatch.setattr(rally_gt, "is_active_a_share", lambda c: True)
    try:
        with pytest.raises(UniverseContaminationError):
            rally_gt.rebuild(conn=conn, data_end=dates[-1].replace("-", ""), raw_conn=raw)
    finally:
        conn.close(); raw.close()


def test_universe_st_episode_excluded():
    """拉升期内 PIT ST 标记 → L1 剔除 (ST 是时变量, 用 ST 日历非当前名)。"""
    n = 400
    dates = make_dates(n)
    rows = _kline_rows("600003", 0.65, n, dates=dates) + _kline_rows("600001", 0.65, n, dates=dates)
    st_day = dates[N_BASE + 10].replace("-", "")      # 拉升期第 10 根 (st_sample_step=10 必采样到)
    conn, raw = _mk_env(rows, dates, ["600001", "600003"],
                        st_rows=[("600003.SH", st_day)])
    try:
        rally_gt.rebuild(conn=conn, data_end=dates[-1].replace("-", ""), raw_conn=raw)
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM fact_rally_ground_truth").fetchall()]
        assert codes == ["600001"]
    finally:
        conn.close(); raw.close()
