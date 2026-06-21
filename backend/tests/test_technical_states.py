"""technical_states 模块单测 (2026-06-21) — config 加载 / PIT 正确性 / 状态语义 / resample / 多TF。"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from services.technical_states import compute, load_config, resample  # noqa: E402
from services.technical_states.classifier import classify_bar, classify_stock  # noqa: E402


def _series(n, start="2020-01-02"):
    import datetime
    d0 = datetime.date.fromisoformat(start)
    dates, dd = [], d0
    while len(dates) < n:
        if dd.weekday() < 5:
            dates.append(dd.isoformat())
        dd += datetime.timedelta(days=1)
    return dates


def test_config_loads():
    cfg = load_config()
    assert set(cfg["状态"]) == {"低位横盘", "放量突破", "上升通道", "缩量上涨", "中继平台",
                                "高位滞涨", "下跌通道", "放量下跌", "缩量回踩"}   # D1: +中继平台 +放量下跌
    assert "daily" in cfg["timeframes"] and "monthly" in cfg["timeframes"]
    assert cfg["timeframes"]["monthly"]["windows"]["er"] < cfg["timeframes"]["daily"]["windows"]["er"]  # 月线窗更短
    # 声明式: 状态由人话条件列表定义 (公式结构进 config, J1)
    up = cfg["状态"]["上升通道"]["条件"]
    assert all({"指标", "判断", "阈值", "锐度"} <= set(c) for c in up)
    assert any(c["指标"] == "均线斜率" and c["判断"] == "高于" for c in up)


def test_resample_weekly():
    dates = _series(20)
    o = h = l = c = [10.0 + i for i in range(20)]
    v = [100] * 20
    wd, wo, wh, wl, wc, wv = resample(dates, o, h, l, c, v, "W")
    assert len(wd) < len(dates)                       # 周 bar 少于日 bar
    assert wv[0] >= 100                               # 量是周内 sum


def test_pit_no_lookahead():
    """PIT 铁律: 加未来 bar 不改变历史 bar 的特征 (特征只用 ≤i)。"""
    n = 200
    dates = _series(n + 60)
    c = [10.0 * (1.005 ** i) for i in range(n + 60)]
    o = h = l = c
    v = [100000.0] * (n + 60)
    f_short = compute(dates[:n], o[:n], h[:n], l[:n], c[:n], v[:n])
    f_long = compute(dates, o, h, l, c, v)
    common = set(f_short) & set(f_long)
    assert len(common) > 20
    for d in list(common)[:30]:
        for k in ("ma_slope", "mom20", "er", "pctile"):
            a, b = f_short[d][k], f_long[d][k]
            if a == a and b == b:                     # 非 NaN
                assert abs(a - b) < 1e-9, f"{d} {k} 受未来影响 = look-ahead"


def test_classify_uptrend():
    """强单调上涨 → 主态=上升通道 (语义)。"""
    n = 200
    dates = _series(n)
    c = [10.0 * (1.012 ** i) for i in range(n)]       # 1.2%/日稳升
    o = h = l = c
    v = [100000.0] * n
    f = compute(dates, o, h, l, c, v)
    doms = [classify_bar(ff)["dominant"] for ff in list(f.values())[-30:]]
    assert doms.count("上升通道") >= 15, f"强升却没识别上升通道: {set(doms)}"


def test_classify_downtrend():
    n = 200
    dates = _series(n)
    c = [10.0 * (0.99 ** i) for i in range(n)]         # 1%/日跌
    o = h = l = c
    v = [100000.0] * n
    f = compute(dates, o, h, l, c, v)
    doms = [classify_bar(ff)["dominant"] for ff in list(f.values())[-30:]]
    assert doms.count("下跌通道") >= 15, f"持续跌却没识别下跌通道: {set(doms)}"


def test_declarative_evaluator_semantics():
    """声明式 evaluator: 强升 bar 上升通道分应高于下跌通道 (公式结构从 config 解释正确)。"""
    from services.technical_states.classifier import state_scores
    cfg = load_config()
    n = 200
    dates = _series(n)
    c = [10.0 * (1.012 ** i) for i in range(n)]
    o = h = l = c
    v = [100000.0] * n
    f = compute(dates, o, h, l, c, v)
    last = list(f.values())[-1]
    sc = state_scores(last, cfg)
    assert sc["上升通道"] > sc["下跌通道"], f"强升 evaluator 出错: {sc}"


def test_coupling_mirror_and_override():
    """J2 边界耦合: 调上升通道均线斜率 → 下跌通道镜像同步; with_overrides 改的是阈值不动代码。"""
    from services.technical_states.coupling import apply_coupling, list_tunables, with_overrides
    cfg = load_config()
    synced, notes = apply_coupling({"上升通道.均线斜率": 7.0}, cfg)
    assert synced["下跌通道.均线斜率"] == -7.0, f"互补对称未镜像: {synced}"
    assert any("镜像" in n for n in notes)
    eff = with_overrides(cfg, synced)
    up = [c for c in eff["状态"]["上升通道"]["条件"] if c["指标"] == "均线斜率"][0]
    assert up["阈值"] == 7.0 and cfg["状态"]["上升通道"]["条件"][0]["阈值"] != 7.0  # 原 cfg 不被改
    tun = list_tunables(cfg)
    assert any(t["param"] == "上升通道.均线斜率" and t["耦合"] for t in tun)  # 可枚举且标耦合


def test_substate_config_driven():
    """D2: 子态全 config 驱动 (子态规则), 放量下跌按价格分位位置消歧 (改config阈值即变, 不动代码)。"""
    from services.technical_states.classifier import _sub_state
    cfg = load_config()
    assert "子态规则" in cfg and "放量下跌" in cfg["子态规则"]
    hi = {"pctile": 0.8, "maxdd": 0.1, "er": 0.1, "accel": 0.0, "zvol": 0.0}
    lo = {"pctile": 0.1, "maxdd": 0.1, "er": 0.1, "accel": 0.0, "zvol": 0.0}
    mid = {"pctile": 0.45, "maxdd": 0.1, "er": 0.1, "accel": 0.0, "zvol": 0.0}
    assert _sub_state("放量下跌", hi, cfg) == "高位放量下跌"   # 位置消歧
    assert _sub_state("放量下跌", lo, cfg) == "低位放量下跌"
    assert _sub_state("放量下跌", mid, cfg) == "中位放量下跌"
    assert _sub_state("上升通道", {"accel": 0.01, "er": 0.0, "maxdd": 0.5}, cfg) == "加速上涨"  # 加速门


def test_classify_stock_multi_tf_keys():
    """多TF API 返回结构正确。"""
    n = 400
    dates = _series(n)
    c = [10.0 + (i % 50) * 0.1 for i in range(n)]
    o = h = l = c
    v = [100000.0] * n
    mtf = classify_stock(dates, o, h, l, c, v)
    assert len(mtf) > 0
    r = next(iter(mtf.values()))
    assert set(r) >= {"daily", "daily_sub", "weekly", "monthly", "mtf_aligned", "entropy"}


def test_limits_flags_and_enrich():
    """D3 A股涨停: 涨停标志识别 + enrich 修正 vol_ratio(封板=最大需求, 防放量突破误判)。"""
    from services.technical_states.limits import code_to_ts_code, compute_limit_flags, enrich_features
    assert code_to_ts_code("000513") == "000513.SZ" and code_to_ts_code("600519") == "600519.SH"
    dates = ["2023-01-03", "2023-01-04"]
    # bar0 一字涨停(开高低收=11.0, up_limit=11.0); bar1 普通
    o = [11.0, 10.5]; h = [11.0, 10.8]; l = [11.0, 10.2]; c = [11.0, 10.6]
    ul = [11.0, 11.66]; dl = [9.0, 9.54]
    flags = compute_limit_flags(dates, o, h, l, c, ul, dl)
    assert flags["2023-01-03"]["is_up_limit"] and flags["2023-01-03"]["is_one_word"]
    assert not flags["2023-01-04"]["is_up_limit"]
    feats = {"2023-01-03": {"vol_ratio": 0.3}, "2023-01-04": {"vol_ratio": 1.2}}  # 涨停日量缩0.3
    enrich_features(feats, flags, {"涨停": {"需求proxy量比": 3.0}})
    assert feats["2023-01-03"]["vol_ratio"] == 3.0       # 封板缩量→proxy(防误判无量假突破)
    assert feats["2023-01-03"]["is_up_limit"] == 1.0
    assert feats["2023-01-04"]["vol_ratio"] == 1.2       # 非涨停不改


def test_context_pit_no_lookahead():
    """D4 上下文层 PIT: 加未来 bar 不改历史 bar 的 context_state/prior_trend (前序只用 ≤t-1)。"""
    from services.technical_states import classify_series
    from services.technical_states.context import apply_context
    cfg = load_config()
    n = 220
    dates = _series(n + 40)
    c = [10.0 * (1.01 ** i) for i in range(n + 40)]    # 稳升
    o = h = l = c
    v = [100000.0] * (n + 40)
    f_all = compute(dates, o, h, l, c, v)
    cls_short = apply_context(classify_series({d: f_all[d] for d in list(f_all)[:n]}, cfg),
                              {d: f_all[d] for d in list(f_all)[:n]}, cfg)
    cls_long = apply_context(classify_series(f_all, cfg), f_all, cfg)
    common = list(set(cls_short) & set(cls_long))[20:50]
    for d in common:
        assert cls_short[d]["prior_trend"] == cls_long[d]["prior_trend"], f"{d} prior_trend 受未来影响"
        assert cls_short[d]["context_state"] == cls_long[d]["context_state"], f"{d} context_state 受未来影响"


def test_context_revives_pullback():
    """D4: 上下文层用前序升势复活缩量回踩 (确定性: 前序态=上升通道 + 当前 mild 回调 → 缩量回踩)。"""
    from services.technical_states.context import apply_context
    cfg = load_config()
    dates = [f"2020-01-{i:02d}" for i in range(1, 13)]
    cls = {d: {"dominant": "上升通道", "covered": True} for d in dates[:11]}   # 前11根升势
    cls[dates[11]] = {"dominant": "中继平台", "covered": True}                  # 当前瞬时态非缩量回踩
    feats = {d: {"mom20": 0.05, "vol_ratio": 1.1, "ma_dist": 0.04} for d in dates[:11]}
    feats[dates[11]] = {"mom20": -0.03, "vol_ratio": 0.7, "ma_dist": -0.02}     # 当前: 小幅缩量回调
    apply_context(cls, feats, cfg)
    assert cls[dates[11]]["context_state"] == "缩量回踩"                         # 前序升+当前mild→复活
    assert cls[dates[11]]["refined_dominant"] == "缩量回踩"
    assert cls[dates[11]]["prior_trend"] == "升"


def test_candle_patterns():
    """D5 单日K线: 几何识别 + 位置消歧(锤子vs上吊同形) + A股一字板特判。"""
    from services.technical_states.candles import candle_pattern
    cfg = load_config()
    # 十字星 (实体极小)
    assert candle_pattern(10.0, 10.5, 9.5, 10.02, cfg=cfg) == "十字星"
    # 大阳线 (光头光脚长实体)
    assert candle_pattern(10.0, 11.0, 10.0, 11.0, cfg=cfg) == "大阳线"
    # 锤子/上吊 同形, 位置消歧 (长下影 + 实体非doji: body0.2/range1.5=0.13>0.1)
    assert candle_pattern(10.3, 10.5, 9.0, 10.5, prior_trend="跌", cfg=cfg) == "锤子线"   # 下跌末=看多
    assert candle_pattern(10.3, 10.5, 9.0, 10.5, prior_trend="升", cfg=cfg) == "上吊线"   # 上涨末=看空
    # A股一字板特判 (不判十字星)
    assert candle_pattern(11.0, 11.0, 11.0, 11.0, is_up_limit=True, is_one_word=True, cfg=cfg) == "一字板涨停"


def test_named_patterns_pit_no_backfill():
    """D5b 命名形态: 态序列模板在**完成bar命中, 不回贴历史bar** (PIT三时点); 老鸭头序列匹配。"""
    from services.technical_states.patterns import match_named_patterns
    cfg = load_config()
    # 构造老鸭头序列: 上升通道→缩量回踩→中继平台→放量突破(出水=完成bar)
    seq = ([("d%02d" % i, "上升通道") for i in range(5)]
           + [("d%02d" % i, "缩量回踩") for i in range(5, 8)]
           + [("d%02d" % i, "中继平台") for i in range(8, 12)]
           + [("d12", "放量突破")])
    named = match_named_patterns(seq, cfg)
    assert "d12" in named and any(p["名称"] == "老鸭头" for p in named["d12"])  # 命中在出水bar
    # PIT: 不回贴鸭头/吸筹段 (d00-d11 不应有老鸭头标签)
    for d in [s[0] for s in seq[:-1]]:
        assert not any(p["名称"] == "老鸭头" for p in named.get(d, [])), f"{d} 回贴老鸭头=前瞻泄漏"
    # provenance 标主观性
    assert "主观" in [p["provenance"] for p in named["d12"] if p["名称"] == "老鸭头"][0]


def test_relative_strength():
    """RS 相对强度 (评审HIGH盲点): 个股跑赢基准→强于大盘; PIT (RS只用≤t)。"""
    from services.technical_states.rs import relative_strength
    dates = [f"2020-01-{i:02d}" for i in range(1, 41)]
    # 个股翻倍, 基准平 → RS 持续上升 = 强于大盘
    stock = [10.0 * (1.02 ** i) for i in range(40)]
    bench = {d: 100.0 for d in dates}
    rs = relative_strength(dates, stock, bench, window=20)
    last = rs[dates[-1]]
    assert last["rs_state"] == "强于大盘" and last["rs_slope"] > 0
    # 个股弱于基准 (基准涨股票平)
    bench2 = {dates[i]: 100.0 * (1.03 ** i) for i in range(40)}
    rs2 = relative_strength(dates, stock, bench2, window=20)
    assert rs2[dates[-1]]["rs_state"] == "弱于大盘"


def test_capital_and_chip_signals():
    """维度③资金④筹码: 主力净流入/换手 + 获利盘/集中度/价位 (config阈值)。"""
    from services.technical_states.capital import capital_signals
    from services.technical_states.chips import chip_signals
    cfg = load_config()
    dates = [f"2024-01-{i:02d}" for i in range(1, 31)]
    money = {d: {"buy_elg": 1000.0, "buy_lg": 500.0, "sell_elg": 200.0, "sell_lg": 100.0} for d in dates}  # elg+lg净>0
    turn = {d: 5.0 for d in dates}
    cap = capital_signals(dates, money, turn, window=20, cfg=cfg)
    assert cap[dates[-1]]["capital_state"] == "主力净流入"     # 走 mainforce_net(elg+lg) 非 net_mf
    # 筹码: 高获利盘+价在均成本上=获利
    cyq = {d: {"winner_rate": 90.0, "cost_5pct": 9.0, "cost_50pct": 10.0, "cost_95pct": 11.0, "weight_avg": 10.0} for d in dates}
    close = {d: 12.0 for d in dates}
    chip = chip_signals(dates, cyq, close, window=20, cfg=cfg)
    last = chip[dates[-1]]
    assert "派发压力" in last["chip_state"] and last["价位状态"] == "获利"   # 高获利盘+价在成本上
    assert last["集中状态"] == "单峰集中"                                    # (11-9)/10=0.2<0.5


def test_chip_distribution_warn():
    """筹码精细化① (长江分盈亏 + goal CYQ鱼尾出货): 套牢盘/成本偏度/集中度变化/派发预警。"""
    from services.technical_states.chips import chip_signals
    cfg = load_config()
    dates = [f"2024-01-{i:02d}" for i in range(1, 31)]
    cyq = {}
    for i, d in enumerate(dates):   # 前20日单峰集中(conc=0.1) → 后期转分散(conc=0.5)+高获利盘+价位获利 = 派发
        cyq[d] = ({"winner_rate": 90.0, "cost_5pct": 9.5, "cost_50pct": 10.0, "cost_95pct": 10.5, "weight_avg": 10.0} if i < 20
                  else {"winner_rate": 92.0, "cost_5pct": 8.0, "cost_50pct": 10.0, "cost_95pct": 13.0, "weight_avg": 10.5})
    close = {d: 12.0 for d in dates}
    last = chip_signals(dates, cyq, close, window=20, cfg=cfg)[dates[-1]]
    assert last["套牢盘"] == 8.0                       # 100-92 = 亏损筹码(论文预测力更强)
    assert last["集中度20日变化"] > 0.1                # 0.5-0.1 单峰转多峰
    assert last["派发预警"] is True                    # 高获利盘+集中度松动+价位获利 = 鱼尾出货
    assert abs(last["成本偏度"] - 0.2) < 0.01          # (10.5-10)/((13-8)/2)=0.2 右偏(上方套牢)


def test_capital_intent():
    """主力意图 + 量价背离 (暗盘伪维度已砍, 改 明盘×价格 量价背离代理)。
    evidence: sandbox/mingan_redesign — 东财桶零和+同花顺L2暗盘任何日度口径不可近似(净额54%/gross排序0.283)。"""
    from services.technical_states.capital import capital_intent
    cfg = load_config()
    dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
    money = {d: {"net_amount": -20000.0, "net_amount_rate": 3.0, "pct_change": 2.5} for d in dates}  # 明出价涨(背离)
    out = capital_intent(dates, money, cfg=cfg)
    last = out[dates[-1]]
    assert last["主力意图"] == "诱空吸筹建仓"        # net<0(主力流出) + 价涨 = 隐蔽承接
    assert last["量价背离"] == "隐性承接"
    assert "主力净额" in last and "三日主力净额" in last and "净额占比" in last   # 明盘数值独立保留(三因子分离)


def test_zhuli_intent_minga_price():
    """主力意图 6象限 (明盘方向 × 价格方向; 量价背离代理替伪暗盘)。
    evidence: 同花顺暗盘追踪26条语义 + sandbox/mingan_redesign 标定 (净额/gross 均不可近似L2暗盘)。"""
    from services.technical_states.capital import zhuli_intent
    cfg = load_config()
    def it(net, pct, rate): return zhuli_intent(net, pct, rate, cfg)["主力意图"]   # 主力大单净(亿), 涨跌%, 净额占比%
    # 主力清淡 (|rate|<1.5)
    assert it(0.1, 2.0, 1.0) == "洗盘低吸"        # 主力清淡+价涨 = 隐蔽承接
    assert it(0.1, -2.0, 1.0) == "缩量阴跌"       # 主力清淡+价跌
    # 背离 (明盘强 rate>=1.5)
    assert it(-2.0, 2.0, 3.0) == "诱空吸筹建仓"   # 明出+价涨 (恩捷股份型)
    assert it(2.0, -2.0, 3.0) == "拉高派发诱多"   # 明入+价跌
    # 量价一致
    assert it(2.0, 2.0, 3.0) == "主力推升看多"    # 明入+价涨 (晶方/万通型)
    assert it(-2.0, -2.0, 3.0) == "主力做空出逃"  # 明出+价跌 (赛力斯型)
    # 价平 → 分歧
    assert it(2.0, 0.0, 3.0) == "资金分歧"
    # 量价背离标签 (三因子分离: 背离独立于意图)
    assert zhuli_intent(-2.0, 2.0, 3.0, cfg)["量价背离"] == "隐性承接"
    assert zhuli_intent(2.0, -2.0, 3.0, cfg)["量价背离"] == "隐性派发"


def test_mainforce_net_dongcai():
    """主力大单净 (东财单一源): mainforce_net=东财net_amount; capital_signals 主力净流入判定同源。"""
    from services.technical_states.capital import capital_signals, mainforce_net
    assert mainforce_net({"net_amount": 1200}) == 1200                # 东财 net_amount 直接
    assert mainforce_net({"buy_elg": 1000, "buy_lg": 500}) == 1500    # 无net_amount → elg+lg净 fallback
    dates = [f"2024-01-{i:02d}" for i in range(1, 26)]
    money = {d: {"net_amount": 1200.0} for d in dates}
    cap = capital_signals(dates, money, {d: 5.0 for d in dates}, window=20)
    assert cap[dates[-1]]["capital_state"] == "主力净流入"            # 东财net_amount>0
