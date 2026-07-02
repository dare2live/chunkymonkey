"""technical_states (B2 形态识别重建) 单测 — 设计 §6 全 6 组 (证伪门写法)。

硬门 (契约):
  1. PIT 截断不变性 (加未来 bar 全特征+全轴+标签逐位 0 diff);
  2. 决策日 live=batch 一致性 (随机截断点 weekly/monthly 100% 一致 — 旧实现 23%/38% 不一致的证伪门)。
其余: 一字跌停 (H3) / 零成交量 not covered / pctile tie / 几何均量纲不变量 (H2) /
突破检测器 C1 证伪门 / config 契约 (cell 全覆盖无静默 fallback)。
builder 级测试全用内存 DuckDB (CREATE SCHEMA mkt/tr/ref 模拟生产 ATTACH), 不碰真库。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import technical_states as ts                       # noqa: E402
from services.technical_states import axes, breakout, candles, labeler, limits, patterns  # noqa: E402
from services.technical_states.features import FEATURE_KEYS, compute, resample  # noqa: E402
from conftest import duck_mem                                     # noqa: E402

CFG = ts.load_config()
LAB = labeler.Labeler(CFG)
_DAILY = CFG["timeframes"]["daily"]
_ALL_KEYS = list(FEATURE_KEYS) + ["vol_ratio_eff", "zvol_eff"]


# ---------------------------------------------------------------- fixtures
def _weekdays(n: int, start: str = "2019-01-02") -> list[str]:
    import datetime
    d = datetime.date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def _walk(n: int, seed: int, base: float = 10.0):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0005, 0.02, n)
    c = base * np.cumprod(1 + ret)
    v = np.exp(rng.normal(13.0, 0.5, n))
    return [round(float(x), 2) for x in c], [float(x) for x in v]


def _bars(c):
    o = [round(x * 0.998, 4) for x in c]
    h = [round(x * 1.012, 4) for x in c]
    l = [round(x * 0.988, 4) for x in c]
    return o, h, l


def _classify(dates, c, v, cal, **kw):
    o, h, l = _bars(c)
    return ts.classify_stock(dates, o, h, l, c, v, trading_days=cal, cfg=CFG, lab=LAB, **kw)


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return (a != a and b != b) or a == b
    return a == b


# ---------------------------------------------------------------- 6) config 契约
def test_config_contract_thresholds_and_units():
    """轴判据可加载 + 阈值取整 (<=2 位小数, H6 理论锚定) + 未知指标/判断词 fail loud。"""
    for axis, spec in CFG["轴"].items():
        for val, conds in (spec.get("取值") or {}).items():
            for cond in conds:
                assert cond["指标"] in CFG["指标"], f"{axis}/{val} 引用未登记指标"
                thr = float(cond["阈值"])
                assert abs(thr * 100 - round(thr * 100)) < 1e-9, \
                    f"{axis}/{val} 阈值 {thr} 非取整值 (H6: 理论锚定取整, 禁伪精度)"
                assert float(cond["过渡带"]) > 0
    bad = copy.deepcopy(CFG)
    bad["轴"]["位置"]["取值"]["low"][0]["指标"] = "不存在的指标"
    with pytest.raises(KeyError):
        axes.build_engine(bad)
    bad2 = copy.deepcopy(CFG)
    bad2["轴"]["位置"]["取值"]["low"][0]["判断"] = "约等于"
    with pytest.raises(ValueError):
        axes.build_engine(bad2)


def test_cell_mapping_full_coverage_no_silent_fallback():
    """cell 映射 54 组合全覆盖; 删一条规则 → load 抛错 (无静默 fallback); 规则值拼错 → 抛错。"""
    rules = labeler.load_cell_rules(CFG, LAB.engine)
    assert rules, "cell映射 为空"
    broken = copy.deepcopy(CFG)
    removed = broken["cell映射"].pop()          # 删掉最后一条 → 某些 cell 失配
    with pytest.raises(ValueError, match="未覆盖组合"):
        labeler.Labeler(broken)
    assert removed["标签"]
    typo = copy.deepcopy(CFG)
    typo["cell映射"][0]["位置"] = "LOW_TYPO"
    with pytest.raises(ValueError, match="非法"):
        labeler.Labeler(typo)


def test_output_has_no_direction_semantics():
    """C2 CRITICAL: 输出无 mtf_aligned / bull / bear / side 字段 — 多 TF 只有各框描述标签。"""
    n = 320
    dates = _weekdays(n)
    cal = _weekdays(n + 30)
    c, v = _walk(n, seed=7)
    out = _classify(dates, c, v, cal)
    assert out
    row = next(iter(out.values()))
    expected = {"axis_pos", "axis_trend", "axis_purity", "axis_vol", "axis_volregime",
                "axis_pos_memb", "axis_trend_memb", "axis_purity_memb", "axis_vol_memb",
                "axis_volregime_memb", "form_name", "form_sub", "weekly_name", "monthly_name",
                "is_breakout_event", "base_days", "buyable", "sellable", "is_one_word"}
    assert set(row) == expected
    for forbidden in ("mtf_aligned", "bull", "bear", "side", "direction"):
        assert forbidden not in row


# ---------------------------------------------------------------- 1) PIT 截断不变性 (硬门)
def test_pit_truncation_invariance_features_axes_labels():
    """加未来 60 bar, 全 15+2 特征 + 全轴 + 标签在 common date 逐位 0 diff (红线: 不改历史输出)。"""
    n, fut = 400, 60
    dates = _weekdays(n + fut)
    cal = _weekdays(n + fut + 30)
    c, v = _walk(n + fut, seed=11)
    f_short = compute(dates[:n], *_bars(c[:n]), c[:n], v[:n],
                      windows=_DAILY["windows"], warmup=_DAILY["warmup"])
    f_long = compute(dates, *_bars(c), c, v,
                     windows=_DAILY["windows"], warmup=_DAILY["warmup"])
    common = sorted(set(f_short) & set(f_long))
    assert len(common) > 100, "有效 bar 太少, 测试空转"
    for d in common:
        for k in _ALL_KEYS:
            a, b = f_short[d][k], f_long[d][k]
            assert _same(a, b), f"{d} {k}: {a} != {b} — 加未来 bar 改了历史特征"
    out_short = _classify(dates[:n], c[:n], v[:n], cal)
    out_long = _classify(dates, c, v, cal)
    for d in sorted(set(out_short) & set(out_long)):
        ra, rb = out_short[d], out_long[d]
        for k in ra:
            assert _same(ra[k], rb[k]), f"{d} {k}: {ra[k]} != {rb[k]} — 加未来 bar 改了历史输出"


# ---------------------------------------------------------------- 2) 决策日 live=batch (硬门, H1 证伪门)
def test_live_equals_batch_on_decision_day():
    """随机截断点: live=classify(数据到t) 在 t 日的 daily/weekly/monthly 标签与全量批算 100% 一致。

    旧实现在此 23%(weekly)/38%(monthly) 不一致 (resample flush 未闭合尾 bar) — 本测试即其证伪门:
    把 resample 的 bar 键改回"周期内最后数据日"即红。
    """
    n = 780
    dates = _weekdays(n)
    cal = _weekdays(n + 40)
    c, v = _walk(n, seed=23)
    batch = _classify(dates, c, v, cal)
    cut_points = [300, 340, 401, 452, 500, 555, 601, 660, 690, 710, 745, 779]
    n_weekly = n_monthly = 0
    for t in cut_points:
        d = dates[t]
        live = _classify(dates[:t + 1], c[:t + 1], v[:t + 1], cal)
        assert d in live and d in batch, f"决策日 {d} 缺输出"
        for field in ("form_name", "form_sub", "weekly_name", "monthly_name",
                      "axis_pos", "axis_trend", "axis_purity", "axis_vol"):
            assert _same(live[d][field], batch[d][field]), \
                f"t={d} {field}: live={live[d][field]!r} vs batch={batch[d][field]!r} — 决策日被未来改写 (H1)"
        n_weekly += live[d]["weekly_name"] is not None
        n_monthly += live[d]["monthly_name"] is not None
    assert n_weekly >= 6 and n_monthly >= 3, \
        f"weekly/monthly 非空样本不足 ({n_weekly}/{n_monthly}) — 测试空转"


def test_resample_emits_only_calendar_closed_keys():
    """resample bar 键 = 周期末日历交易日; 数据止于周三 → 尾 bar 键为该周五 (>数据末日 = as-of 不可见)。"""
    cal = _weekdays(30, start="2024-01-01")      # 2024-01-01 周一
    dates = cal[:13]                              # 止于第 3 周周三 (2024-01-17)
    c = [10.0 + i * 0.1 for i in range(13)]
    v = [100.0] * 13
    o, h, l = _bars(c)
    keys, _o, _h, _l, _c, vv = resample(dates, o, h, l, c, v, "W", cal)
    assert keys[0] == "2024-01-05" and keys[1] == "2024-01-12"    # 已闭合周键=周五
    assert keys[2] == "2024-01-19" > dates[-1]                     # 开放周键=未来周五 → as-of 跳过
    assert vv[0] == pytest.approx(500.0)                           # 闭合周量 = 整周 sum
    # ISO 跨年周: 2019-12-30(周一)~2020-01-03(周五) 同一 ISO 周 → 单 bar
    cal2 = _weekdays(10, start="2019-12-30")
    k2, *_rest = resample(cal2[:5], [1] * 5, [1] * 5, [1] * 5, [1.0] * 5, [1] * 5, "W", cal2)
    assert len(k2) == 1 and k2[0] == "2020-01-03"
    # 日历不覆盖 → fail loud
    with pytest.raises(ValueError, match="交易日历未覆盖"):
        resample(["2030-01-02"], [1], [1], [1], [1.0], [1], "W", cal)


# ---------------------------------------------------------------- 3) 一字跌停 / 零成交量 / pctile tie
def test_one_word_down_limit_h3():
    """H3: 一字跌停 (o=h=l=c=down_limit) 必须 is_one_word=True + candles '一字板跌停' 可达。"""
    flags = limits.compute_limit_flags(
        ["2024-01-05"], [9.0], [9.0], [9.0], [9.0],
        raw_close=[9.0], up_limit=[11.0], down_limit=[9.0], price_tol=0.005)
    f = flags["2024-01-05"]
    assert f["is_down_limit"] is True and f["is_up_limit"] is False and f["is_one_word"] is True
    assert candles.candle_pattern(9.0, 9.0, 9.0, 9.0, is_down_limit=True, is_one_word=True,
                                  cfg=CFG) == "一字板跌停"
    # 一字涨停侧不回归
    up = limits.compute_limit_flags(["2024-01-05"], [11.0], [11.0], [11.0], [11.0],
                                    raw_close=[11.0], up_limit=[11.0], down_limit=[9.0],
                                    price_tol=0.005)["2024-01-05"]
    assert up["is_up_limit"] is True and up["is_one_word"] is True
    assert candles.candle_pattern(11.0, 11.0, 11.0, 11.0, is_up_limit=True, is_one_word=True,
                                  cfg=CFG) == "一字板涨停"


def test_limit_flags_exact_raw_price_and_unknown():
    """medium 修复: 原始价空间半 tick 精确比 — 尾盘炸板 (10.97 vs 板 11.00) 不再误判封板;
    无 stk_limit 数据 → None (不知道 != False, H4 诚实语义)。"""
    zb = limits.compute_limit_flags(["2024-01-05"], [10.5], [11.0], [10.4], [10.97],
                                    raw_close=[10.97], up_limit=[11.0], down_limit=[9.0],
                                    price_tol=0.005)["2024-01-05"]
    assert zb["is_up_limit"] is False
    unk = limits.compute_limit_flags(["2024-01-05"], [10.0], [10.5], [9.9], [10.2],
                                     raw_close=[10.2], up_limit=[None], down_limit=[None],
                                     price_tol=0.005)["2024-01-05"]
    assert unk["is_up_limit"] is None and unk["is_down_limit"] is None and unk["is_one_word"] is None


def test_enrich_eff_view_keeps_measured():
    """涨跌停修正写 eff 视图, 实测 vol_ratio/zvol 不改写 (medium); 跌停侧对称 proxy (medium)。"""
    feats = {"2024-01-05": {"vol_ratio": 0.05, "vol_ratio_eff": 0.05, "zvol": -2.0, "zvol_eff": -2.0}}
    flags = {"2024-01-05": {"is_up_limit": False, "is_down_limit": True, "is_one_word": True}}
    limits.enrich_features(feats, flags, CFG)
    f = feats["2024-01-05"]
    assert f["vol_ratio"] == 0.05 and f["zvol"] == -2.0          # measured 不污染
    assert f["vol_ratio_eff"] == CFG["涨跌停"]["供给proxy量比"]
    assert f["zvol_eff"] == CFG["涨跌停"]["量能zproxy"]
    assert f["is_down_limit"] == 1.0 and f["is_one_word"] == 1.0


def test_zero_volume_bar_not_covered():
    """零成交量 bar → vol_ratio/zvol NaN → 量能轴 None → form_name None (不再伪装量能正常)。"""
    n = 320
    dates = _weekdays(n)
    cal = _weekdays(n + 30)
    c, v = _walk(n, seed=5)
    v[300] = 0.0
    out = _classify(dates, c, v, cal)
    row = out[dates[300]]
    assert row["axis_vol"] is None and row["form_name"] is None
    assert out[dates[299]]["axis_vol"] is not None               # 邻近正常 bar 不受牵连


def test_pctile_strict_no_tie_inflation():
    """medium 修复: 死平序列严格分位 = 0.0 (旧 <= 口径虚高到 1.0 → 误判高位滞涨)。"""
    n = 320
    dates = _weekdays(n)
    c = [10.0] * n
    v = [100000.0] * n
    feats = compute(dates, *_bars(c), c, v, windows=_DAILY["windows"], warmup=_DAILY["warmup"])
    assert feats, "平序列无有效 bar"
    last = feats[sorted(feats)[-1]]
    assert last["pctile"] == 0.0
    assert last["r2"] == 0.0                                      # 零方差窗不再伪装完美趋势
    ax = LAB.engine.classify(last)
    assert ax["位置"]["value"] != "high", "死平股仍被判高位 = tie 虚高回归"


# ---------------------------------------------------------------- 4) 几何均量纲不变量 (H2)
def test_geometric_mean_condition_count_invariance():
    """H2 证伪门: 3 条件与 5 条件取值在每门同满足度下归一分相等 (旧连乘 0.125 vs 0.031 即红)。"""
    cfg = {
        "soft": {"温度": 0.25, "过渡带锐度常数": 6.0},
        "指标": {k: {"key": k, "单位": "比例", "值域": [-1000.0, 1000.0]} for k in "abcde"},
        "轴": {"测试": {"列": "x", "取值": {
            "v3": [{"指标": k, "判断": "高于", "阈值": 0.0, "过渡带": 1.0} for k in "abc"],
            "v5": [{"指标": k, "判断": "高于", "阈值": 0.0, "过渡带": 1.0} for k in "abcde"],
        }}},
    }
    eng = axes.build_engine(cfg)
    feats = {k: 0.5 for k in "abcde"}                # 每门满足度 = sigmoid(6*0.5) 相同
    s3 = eng.value_score("测试", "v3", feats)
    s5 = eng.value_score("测试", "v5", feats)
    assert s3 == pytest.approx(s5, abs=1e-9), f"条件数改变分数量纲: {s3} vs {s5} (H2 回归)"
    assert s3 > 0.9
    # 对照: 旧 raw 连乘在同满足度下随条件数塌缩 (被本设计消除的病根)
    g = 1.0 / (1.0 + np.exp(-3.0))
    assert abs(g ** 3 - g ** 5) > 0.05


def test_axis_normalization_bounded_by_one():
    """平缓门 cap 校正: 完美满足 (斜率=0) 的 flat 归一分 = 1.0 (旧 max_score<1 的量纲病)。"""
    feats = {"ma_slope": 0.0, "ma_align": 0.5, "pctile": 0.5, "pth": 0.5, "vol_ratio_eff": 1.0,
             "zvol_eff": 0.0, "er": 0.3, "r2": 0.5, "ma_dist": 0.0, "mom20": 0.0, "rv_pctile": 0.5}
    s = LAB.engine.value_score("趋势方向", "flat", feats)
    assert s == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------- 5) 突破检测器 (C1 证伪门)
def _breakout_fixture(pos_value: str, dry_vr: float = 0.8, rv_prev: float = 0.3,
                      trigger_vr: float = 3.0):
    n = 100
    dates = _weekdays(n, start="2024-01-01")
    c = [10.0] * n
    h = [10.05] * n
    trigger = n - 1
    c[trigger] = 12.0
    h[trigger] = 12.1
    feats = {}
    rows = {}
    for i, d in enumerate(dates):
        feats[d] = {"vol_ratio": dry_vr, "vol_ratio_eff": dry_vr, "rv_pctile": rv_prev}
        rows[d] = {"axes": {"位置": {"value": pos_value}}}
    feats[dates[trigger]] = {"vol_ratio": trigger_vr, "vol_ratio_eff": trigger_vr, "rv_pctile": 0.6}
    return dates, h, c, feats, rows


def test_breakout_requires_base_context():
    """C1: 有底盘 (低位 streak+收缩+枯量) 的破位放量 → 事件; 高位无底盘同样破位放量 → 不触发。"""
    dates, h, c, feats, rows = _breakout_fixture("low")
    ev = breakout.detect(dates, h, c, feats, rows, {}, CFG)
    assert dates[-1] in ev
    e = ev[dates[-1]]
    assert e["base_days"] >= CFG["突破"]["底盘最少天数"]
    assert e["trigger_strength"] == pytest.approx(3.0)
    assert e["tradable"] is None and e["buyable"] is None        # 无涨跌停数据 → 不知道 != True

    dates, h, c, feats, rows = _breakout_fixture("high")         # C1 证伪门: 高位裸突破
    assert breakout.detect(dates, h, c, feats, rows, {}, CFG) == {}


def test_breakout_gates_dry_volume_and_contraction():
    """底盘枯量门与波动收缩门缺一不触发 (VCP/O'Neil 三层结构)。"""
    dates, h, c, feats, rows = _breakout_fixture("low", dry_vr=1.5)      # 未枯量
    assert breakout.detect(dates, h, c, feats, rows, {}, CFG) == {}
    dates, h, c, feats, rows = _breakout_fixture("low", rv_prev=0.8)    # 波动未收缩
    assert breakout.detect(dates, h, c, feats, rows, {}, CFG) == {}
    dates, h, c, feats, rows = _breakout_fixture("low", trigger_vr=1.2)  # 量比不足
    assert breakout.detect(dates, h, c, feats, rows, {}, CFG) == {}


def test_breakout_one_word_gate():
    """一字板触发日 → tradable=False / 收盘封板 → buyable=False (H4 可成交闸)。"""
    dates, h, c, feats, rows = _breakout_fixture("low")
    flags = {dates[-1]: {"is_up_limit": True, "is_down_limit": False, "is_one_word": True}}
    ev = breakout.detect(dates, h, c, feats, rows, flags, CFG)
    e = ev[dates[-1]]
    assert e["tradable"] is False and e["buyable"] is False


# ---------------------------------------------------------------- context 两遍 (前序依赖态)
def test_context_pullback_and_relay_platform():
    """缩量回踩/中继平台 = pass-2 前序依赖 (前序趋势轴 up 多数派), 非瞬时态 (H7)。"""
    dates = [f"2020-01-{i:02d}" for i in range(1, 13)]

    def mk_rows(last_axes, last_label, last_sub):
        rows = {d: {"axes": {"趋势方向": {"value": "up"}, "位置": {"value": "mid"}},
                    "label": "上升通道", "sub": "温和上涨"} for d in dates[:11]}
        rows[dates[11]] = {"axes": last_axes, "label": last_label, "sub": last_sub}
        return rows

    feats = {d: {"mom20": 0.05, "vol_ratio_eff": 1.1, "ma_dist": 0.04} for d in dates}
    # 缩量回踩: 前序 up + 当前 mild 缩量回调
    rows = mk_rows({"趋势方向": {"value": "flat"}, "位置": {"value": "mid"}}, "中位盘整", "温和盘整")
    feats[dates[11]] = {"mom20": -0.03, "vol_ratio_eff": 0.7, "ma_dist": -0.02}
    LAB.apply_context(rows, feats)
    assert rows[dates[11]]["label"] == "缩量回踩" and rows[dates[11]]["sub"] == "升势回踩"
    # 中继平台: 前序 up + 当前 flat/mid 但不满足回踩当前条件 (量未缩)
    rows = mk_rows({"趋势方向": {"value": "flat"}, "位置": {"value": "mid"}}, "中位盘整", "温和盘整")
    feats[dates[11]] = {"mom20": 0.005, "vol_ratio_eff": 1.2, "ma_dist": 0.01}
    LAB.apply_context(rows, feats)
    assert rows[dates[11]]["label"] == "中继平台"
    # 前序非 up → 不 refine
    rows = mk_rows({"趋势方向": {"value": "flat"}, "位置": {"value": "mid"}}, "中位盘整", "温和盘整")
    for d in dates[:11]:
        rows[d]["axes"]["趋势方向"]["value"] = "down"
    feats[dates[11]] = {"mom20": -0.03, "vol_ratio_eff": 0.7, "ma_dist": -0.02}
    LAB.apply_context(rows, feats)
    assert rows[dates[11]]["label"] == "中位盘整"


# ---------------------------------------------------------------- candles / patterns (沿用+修正)
def test_candle_neutral_without_prior_trend():
    """low 修复: 无前序趋势的长影线 → 几何中性名, 不隐性押注空头。"""
    assert candles.candle_pattern(10.3, 10.5, 9.0, 10.5, prior_trend=None, cfg=CFG) == "长下影线"
    assert candles.candle_pattern(10.3, 10.5, 9.0, 10.5, prior_trend="跌", cfg=CFG) == "锤子线"
    assert candles.candle_pattern(10.3, 10.5, 9.0, 10.5, prior_trend="升", cfg=CFG) == "上吊线"
    assert candles.candle_pattern(10.0, 11.0, 10.0, 11.0, cfg=CFG) == "大阳线"
    with pytest.raises(KeyError):
        candles.candle_pattern(10.0, 11.0, 10.0, 11.0, cfg={})   # cfg 必传, 无内置默认


def test_named_patterns_breakout_event_completion_no_backfill():
    """老鸭头末元素 = 突破事件 (C1 词表适配); 命中只写完成 bar, 不回贴历史 (PIT 三时点 keep)。"""
    seq = ([(f"d{i:02d}", "上升通道", False) for i in range(5)]
           + [(f"d{i:02d}", "缩量回踩", False) for i in range(5, 8)]
           + [(f"d{i:02d}", "中继平台", False) for i in range(8, 12)]
           + [("d12", "上升通道", True)])                        # 出水日: 标签任意 + 突破事件
    named = patterns.match_named_patterns(seq, CFG)
    assert "d12" in named and any(p["名称"] == "老鸭头" for p in named["d12"])
    for d, _s, _e in seq[:-1]:
        assert not any(p["名称"] == "老鸭头" for p in named.get(d, [])), f"{d} 回贴 = 前瞻泄漏"


# ---------------------------------------------------------------- builder (内存 DuckDB)
_FIX_DDL = """
CREATE SCHEMA mkt;
CREATE SCHEMA tr;
CREATE SCHEMA ref;
CREATE TABLE mkt.price_kline_qfq_tushare (
    code TEXT, date TEXT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    volume DOUBLE, amount DOUBLE);
CREATE TABLE tr.raw_tushare_daily (ts_code TEXT, trade_date TEXT, close DOUBLE);
CREATE TABLE tr.raw_tushare_stk_limit (ts_code TEXT, trade_date TEXT, up_limit DOUBLE, down_limit DOUBLE);
CREATE TABLE tr.raw_tushare_index_daily (ts_code TEXT, trade_date TEXT, close DOUBLE);
CREATE TABLE ref.dim_trading_calendar (trade_date TEXT, is_trading INTEGER);
CREATE TABLE dim_stock_segment_daily (
    stock_code TEXT, trade_date TEXT, mktcap_seg TEXT, turnover_seg TEXT, sw_l1 TEXT,
    circ_mv DOUBLE, turnover_rate DOUBLE, rv_pctile DOUBLE, vol_regime TEXT);
"""

_N_FIX = 800


def _gen_fixture_data(n: int = _N_FIX):
    """确定性合成市场: 2 股 x n 交易日 + 日历 + 涨跌停 + B1 分层 + 基准。"""
    cal = _weekdays(n + 30)
    days = cal[:n]
    data = {"cal": cal, "days": days, "kline": [], "raw": [], "limit": [], "seg": [], "bench": []}
    one_up, one_down = n - 5, n - 3                     # 倒数第5日一字涨停 / 倒数第3日一字跌停
    for code, ts_code, seed in (("600001", "600001.SH", 1), ("000002", "000002.SZ", 2)):
        c, v = _walk(n, seed=seed)
        for i, d in enumerate(days):
            compact = d.replace("-", "")
            if i in (one_up, one_down):
                o = h = l = px = c[i]
            else:
                o, h, l, px = round(c[i] * 0.998, 2), round(c[i] * 1.012, 2), round(c[i] * 0.988, 2), c[i]
            ul = px if i == one_up else round(px * 1.1, 2)
            dl = px if i == one_down else round(px * 0.9, 2)
            data["kline"].append((code, d, o, h, l, px, v[i], px * v[i]))
            data["raw"].append((ts_code, compact, px))
            data["limit"].append((ts_code, compact, ul, dl))
            data["seg"].append((code, compact, "mid", "low", "行业X", 1.0, 1.0,
                                0.3 if i % 2 == 0 else 0.7,
                                "low_vol" if i % 2 == 0 else "high_vol"))
    for i, d in enumerate(days):
        data["bench"].append(("000300.SH", d.replace("-", ""), 3000.0 + i))
    return data


def _load_fixture(data, upto_day: str | None = None):
    c = duck_mem()
    c.executescript(_FIX_DDL)
    c.executemany("INSERT INTO mkt.price_kline_qfq_tushare VALUES (?,?,?,?,?,?,?,?)",
                  data["kline"] if upto_day is None else
                  [r for r in data["kline"] if r[1] <= upto_day])
    c.executemany("INSERT INTO tr.raw_tushare_daily VALUES (?,?,?)",
                  data["raw"] if upto_day is None else
                  [r for r in data["raw"] if r[1] <= upto_day.replace("-", "")])
    c.executemany("INSERT INTO tr.raw_tushare_stk_limit VALUES (?,?,?,?)",
                  data["limit"] if upto_day is None else
                  [r for r in data["limit"] if r[1] <= upto_day.replace("-", "")])
    c.executemany("INSERT INTO tr.raw_tushare_index_daily VALUES (?,?,?)",
                  data["bench"] if upto_day is None else
                  [r for r in data["bench"] if r[1] <= upto_day.replace("-", "")])
    c.executemany("INSERT INTO dim_stock_segment_daily VALUES (?,?,?,?,?,?,?,?,?)",
                  data["seg"] if upto_day is None else
                  [r for r in data["seg"] if r[1] <= upto_day.replace("-", "")])
    c.executemany("INSERT INTO ref.dim_trading_calendar VALUES (?, 1)", [(d,) for d in data["cal"]])
    return c


def _rows_for_day(con, compact_day):
    # built_at 是第 22 列 (时间戳), 比对取前 21 列
    rows = con.execute(f"SELECT * FROM {ts.TABLE} WHERE trade_date = ? ORDER BY stock_code",
                       [compact_day]).fetchall()
    return [tuple(list(r)[:21]) for r in rows]


@pytest.fixture(scope="module")
def fix_data():
    return _gen_fixture_data()


def test_rebuild_all_end_to_end(fix_data):
    """全量构建: 产物表 §5 全字段落地 — E 轴消费 B1 / weekly+monthly 非空 / 可成交标注可见 (H4)。"""
    con = _load_fixture(fix_data)
    try:
        out = ts.rebuild_all(conn=con, cfg=CFG)
        assert out["rows"] > 0 and out["codes"] == 2
        last = fix_data["days"][-1].replace("-", "")
        rows = con.execute(f"""
            SELECT stock_code, axis_pos, axis_trend, axis_vol, axis_volregime, axis_volregime_memb,
                   form_name, weekly_name, monthly_name, buyable, sellable, is_one_word
            FROM {ts.TABLE} WHERE trade_date = ? ORDER BY stock_code""", [last]).fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r[1] in ("low", "mid", "high") and r[2] in ("up", "flat", "down")
            assert r[4] in ("low_vol", "high_vol")                       # E 轴来自 B1
            assert r[5] == pytest.approx(0.4, abs=1e-9)                  # |rv_pctile-0.5|/0.5
            assert r[6] is not None and r[7] is not None and r[8] is not None
            assert r[9] is True and r[10] is True and r[11] is False     # 常规日可买可卖
        one_up = fix_data["days"][_N_FIX - 5].replace("-", "")
        r = con.execute(f"""
            SELECT buyable, sellable, is_one_word FROM {ts.TABLE}
            WHERE trade_date = ? AND stock_code = '600001'""", [one_up]).fetchone()
        assert (r[0], r[1], r[2]) == (False, True, True)                 # 一字涨停: 买不进卖得出
        one_down = fix_data["days"][_N_FIX - 3].replace("-", "")
        r = con.execute(f"""
            SELECT buyable, sellable, is_one_word FROM {ts.TABLE}
            WHERE trade_date = ? AND stock_code = '600001'""", [one_down]).fetchone()
        assert (r[0], r[1], r[2]) == (True, False, True)                 # 一字跌停: 卖不出 (H3+H4)
    finally:
        con.close()


def test_build_latest_incremental_equals_rebuild(fix_data):
    """增量=全量逐 bit 一致 (切片含前序需求): 缺最后一日 → build_latest 补齐, 行与全量重建相同。"""
    last_day = fix_data["days"][-1]
    prev_day = fix_data["days"][-2]
    con_full = _load_fixture(fix_data)
    con_inc = _load_fixture(fix_data, upto_day=prev_day)
    try:
        ts.rebuild_all(conn=con_full, cfg=CFG)
        want = _rows_for_day(con_full, last_day.replace("-", ""))
        assert len(want) == 2

        ts.rebuild_all(conn=con_inc, cfg=CFG)
        assert _rows_for_day(con_inc, last_day.replace("-", "")) == []
        # 追加最后一日源数据 → 增量
        compact = last_day.replace("-", "")
        con_inc.executemany("INSERT INTO mkt.price_kline_qfq_tushare VALUES (?,?,?,?,?,?,?,?)",
                            [r for r in fix_data["kline"] if r[1] == last_day])
        con_inc.executemany("INSERT INTO tr.raw_tushare_daily VALUES (?,?,?)",
                            [r for r in fix_data["raw"] if r[1] == compact])
        con_inc.executemany("INSERT INTO tr.raw_tushare_stk_limit VALUES (?,?,?,?)",
                            [r for r in fix_data["limit"] if r[1] == compact])
        con_inc.executemany("INSERT INTO tr.raw_tushare_index_daily VALUES (?,?,?)",
                            [r for r in fix_data["bench"] if r[1] == compact])
        con_inc.executemany("INSERT INTO dim_stock_segment_daily VALUES (?,?,?,?,?,?,?,?,?)",
                            [r for r in fix_data["seg"] if r[1] == compact])
        out = ts.build_latest(conn=con_inc, cfg=CFG)
        assert out["added_days"] == 1 and out["rows"] == 2
        got = _rows_for_day(con_inc, compact)
        assert got == want, "增量行与全量重建不一致 — 切片/前序需求破坏确定性"
        # 幂等: 再跑 no-op, 无重复行
        out2 = ts.build_latest(conn=con_inc, cfg=CFG)
        assert out2 == {"added_days": 0, "rows": 0}
        dup = con_inc.execute(f"""
            SELECT COUNT(*) FROM (SELECT stock_code, trade_date, COUNT(*) AS n
                                  FROM {ts.TABLE} GROUP BY 1, 2 HAVING n > 1)""").fetchone()[0]
        assert dup == 0
    finally:
        con_full.close()
        con_inc.close()


def test_build_latest_bootstraps_missing_table(fix_data):
    """表不存在 → build_latest 走全量重建 (首跑/重置后自举)。"""
    con = _load_fixture(fix_data, upto_day=fix_data["days"][320])
    try:
        out = ts.build_latest(conn=con, cfg=CFG)
        assert out.get("mode") == "rebuild" and out["rows"] > 0
    finally:
        con.close()


def test_rebuild_fails_loud_without_b1_columns(fix_data):
    """B1 缺 rv_pctile/vol_regime 列 → fail loud (E 轴单一计算点前置, 不静默降级)。"""
    con = _load_fixture(fix_data, upto_day=fix_data["days"][300])
    try:
        con.execute("ALTER TABLE dim_stock_segment_daily DROP COLUMN rv_pctile")
        con.execute("ALTER TABLE dim_stock_segment_daily DROP COLUMN vol_regime")
        with pytest.raises(RuntimeError, match="rv_pctile"):
            ts.rebuild_all(conn=con, cfg=CFG)
    finally:
        con.close()
