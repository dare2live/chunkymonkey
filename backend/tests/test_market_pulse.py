"""market_pulse 单测 — quiet 连续天数 / RS 滚窗数值 / 两链隔离 / 炸板率边界 / 幂等增量 (证伪门)
+ v3: flow_regime 判定序 (6 标签手算 fixture + surge 优先级) / sw L2/L3 聚合数值 / cum_ratio /
level 列 / 增量 flow 列接续。

fixture 形态 = backend/tests/fixtures/domain_samples/*.json 真实字段契约 (禁抽象命名);
内存 DuckDB 用 CREATE SCHEMA tr 模拟生产 ATTACH tushare_raw AS tr (两部名同解析)。
单位契约 (与真实源一致): moneyflow_ind_dc.net_amount=元 / moneyflow.net_mf_amount=万元 /
moneyflow_dc.net_amount=万元 / dc_index.total_mv=万元 / daily_basic.circ_mv=万元。
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import market_pulse as mp
from conftest import duck_mem

# 测试用小窗 cfg (逻辑同构, 窗口缩小便于手算; 生产值走 yaml, 见 test_config_yaml_contract)
CFG = {
    "rs_window_4w": 2,
    "rs_window_12w": 5,
    "benchmark_code": "000300.SH",
    "quiet_px_band_pct": 1.0,
    "quiet_min_net_amount": 0,
    "top_n_sectors": 3,
    "data_start_dc": "20240102",
    "data_start_sw": "20190102",
    "data_start_market": "20230103",
    "dc_content_types": ["行业", "概念"],
    "sec_board_cutoff": "093059",
    "mkt_valuation_code": "000300.SH",
    "lookback_late_days": 2,   # 迟到列回补窗口 (R1 根因5); 测试缩小到 2 便于手算 (生产值走 yaml)
    # v3 flow_regime 小窗 (逻辑同构便于手算; 生产值走 yaml)
    "flow_z_surge": 2.0,
    "accum_min_streak": 2,
    "silent_px_band": 1.0,
    "zscore_window": 3,
    "cum_window": 3,
}

D = ["20240102", "20240103", "20240104", "20240105", "20240108"]

_DDL = """
CREATE SCHEMA tr;
CREATE TABLE tr.raw_tushare_moneyflow_ind_dc (
    trade_date TEXT, content_type TEXT, ts_code TEXT, name TEXT,
    pct_change DOUBLE, close DOUBLE, net_amount DOUBLE, buy_elg_amount DOUBLE,
    buy_sm_amount_stock TEXT);
CREATE TABLE tr.raw_tushare_dc_index (
    ts_code TEXT, trade_date TEXT, "leading" TEXT, leading_code TEXT, leading_pct DOUBLE,
    up_num BIGINT, down_num BIGINT, total_mv DOUBLE, level DOUBLE);
CREATE TABLE tr.raw_tushare_sw_daily (
    ts_code TEXT, trade_date TEXT, close DOUBLE, pct_change DOUBLE, amount DOUBLE);
CREATE TABLE tr.raw_tushare_index_daily (
    ts_code TEXT, trade_date TEXT, close DOUBLE);
CREATE TABLE tr.raw_tushare_index_member_all (
    l1_code TEXT, l1_name TEXT, l2_code TEXT, l2_name TEXT, l3_code TEXT, l3_name TEXT,
    ts_code TEXT, name TEXT, in_date TEXT, out_date TEXT, is_new TEXT);
CREATE TABLE tr.v_sw_industry_pit (
    stock_code TEXT, ts_code TEXT, l1_code TEXT, l1_name TEXT, l2_code TEXT, l2_name TEXT,
    l3_code TEXT, l3_name TEXT, in_date TEXT, out_date TEXT, is_new TEXT);
CREATE TABLE tr.raw_tushare_moneyflow (
    ts_code TEXT, trade_date TEXT, net_mf_amount DOUBLE);
CREATE TABLE tr.raw_tushare_daily_basic (
    ts_code TEXT, trade_date TEXT, circ_mv DOUBLE);
CREATE TABLE tr.raw_tushare_limit_list_d (
    ts_code TEXT, trade_date TEXT, "limit" TEXT, limit_times DOUBLE, fd_amount DOUBLE,
    open_times BIGINT, first_time TEXT);
CREATE TABLE tr.raw_tushare_daily (
    ts_code TEXT, trade_date TEXT, pct_chg DOUBLE);
CREATE TABLE tr.raw_tushare_moneyflow_mkt_dc (
    trade_date TEXT, net_amount DOUBLE);
CREATE TABLE tr.raw_tushare_dc_member (
    trade_date TEXT, ts_code TEXT, con_code TEXT, name TEXT);
CREATE TABLE tr.raw_tushare_moneyflow_dc (
    trade_date TEXT, ts_code TEXT, name TEXT, net_amount DOUBLE, pct_change DOUBLE);
CREATE TABLE tr.raw_tushare_margin (
    trade_date TEXT, exchange_id TEXT, rzrqye DOUBLE);
CREATE TABLE tr.raw_tushare_index_dailybasic (
    ts_code TEXT, trade_date TEXT, pe_ttm DOUBLE, turnover_rate_f DOUBLE);
CREATE TABLE tr.raw_tushare_top_list (
    trade_date TEXT, ts_code TEXT, name TEXT, reason TEXT);
CREATE TABLE tr.raw_tushare_top_inst (
    trade_date TEXT, ts_code TEXT, exalter TEXT, side TEXT, net_buy DOUBLE);
CREATE TABLE tr.raw_tushare_limit_cpt_list (
    trade_date TEXT, ts_code TEXT, name TEXT, days BIGINT, up_stat TEXT,
    cons_nums TEXT, up_nums BIGINT, pct_chg DOUBLE, "rank" BIGINT);
CREATE TABLE dim_stock_segment_daily (
    stock_code TEXT, trade_date TEXT, mktcap_seg TEXT, turnover_seg TEXT, sw_l1 TEXT);
CREATE TABLE fact_stock_form_daily (
    stock_code TEXT, trade_date TEXT, form_name TEXT, is_breakout_event BOOLEAN);
"""


def _fixture_conn():
    """全量小 fixture: 5 交易日 × (dc 2板块 + sw 2 L1行业 + 2 个股 + 涨跌停 + 大盘流
    + v2: 成分/个股流(宽度) + 两融 + 大盘估值 + 龙虎榜 + 最强板块)。"""
    c = duck_mem()
    c.executescript(_DDL)
    # dc 链: BK0001 quiet 模式 (悄悄流入2天 → 涨幅破带断裂 → 再流入1天 → 转流出)
    quiet_pattern = [(D[0], 0.5, 10.0), (D[1], 0.3, 5.0), (D[2], 2.5, 7.0),
                     (D[3], 0.1, 1.0), (D[4], 0.2, -3.0)]
    c.executemany(
        "INSERT INTO tr.raw_tushare_moneyflow_ind_dc VALUES "
        "(?, '行业', 'BK0001.DC', '煤炭行业', ?, 100.0, ?, ?, '云煤能源')",
        [(d, p, n, n / 2) for d, p, n in quiet_pattern])
    # BK0002: 概念, 大额流入非 quiet (|pct|>=band)
    c.executemany(
        "INSERT INTO tr.raw_tushare_moneyflow_ind_dc VALUES "
        "(?, '概念', 'BK0002.DC', '低空经济', 3.0, 200.0, 100.0, 50.0, '万丰奥威')",
        [(d,) for d in D])
    # 地域板块: 不在 dc_content_types, 必须被过滤
    c.execute(
        "INSERT INTO tr.raw_tushare_moneyflow_ind_dc VALUES "
        "(?, '地域', 'BK0145.DC', '上海板块', 0.1, 50.0, 999.0, 1.0, '霍普股份')",
        [D[0]])
    # dc_index: total_mv=200 万元 → sector_mv 2e6 元 (cum_ratio 分母); level=1 → mart 'L1' 透出
    c.executemany(
        "INSERT INTO tr.raw_tushare_dc_index VALUES "
        "('BK0001.DC', ?, '云煤能源', '600792.SH', 9.98, 10, 5, 200.0, 1.0)", [(d,) for d in D])
    # v2 inflow_breadth: BK0001 成分快照仅 D3/D4 (dc_member 2025+ 现实 → 其余日 NULL)
    #   D3: 成分 600001/600002/600003/600004, 个股流 +5/-3/+1/缺 → 宽度 = 2/3
    #   D4: 成分 600001/600002, 个股流 -1/0 (net=0 不算流入) → 宽度 = 0.0 (真 0 ≠ NULL)
    c.executemany("INSERT INTO tr.raw_tushare_dc_member VALUES (?, 'BK0001.DC', ?, ?)", [
        (D[3], "600001.SH", "甲"), (D[3], "600002.SZ", "乙"),
        (D[3], "600003.SZ", "丙"), (D[3], "600004.SZ", "丁"),
        (D[4], "600001.SH", "甲"), (D[4], "600002.SZ", "乙"),
    ])
    c.executemany("INSERT INTO tr.raw_tushare_moneyflow_dc VALUES (?, ?, ?, ?, ?)", [
        (D[3], "600001.SH", "甲", 5.0, 2.0), (D[3], "600002.SZ", "乙", -3.0, -0.5),
        (D[3], "600003.SZ", "丙", 1.0, 0.3),
        (D[4], "600001.SH", "甲", -1.0, -1.0), (D[4], "600002.SZ", "乙", 0.0, 0.1),
    ])
    # sw 链: 2 个 L1 (+1 个 L2 行情码, v3) + HS300 基准 (平盘 → rs = 板块自身滚窗收益)。
    # 成员行含三级码 + PIT 区间 (v3 drill 叶子层; is_new='N'/out_date 已过历史行必须被排除)
    c.executemany("INSERT INTO tr.raw_tushare_index_member_all VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        ("801010.SI", "农林牧渔", "801011.SI", "种植业", "850111.SI", "粮食种植",
         "000592.SZ", "平潭发展", "20111010", None, "Y"),
        ("801010.SI", "农林牧渔", "801011.SI", "种植业", "850111.SI", "粮食种植",
         "002679.SZ", "福建金森", "20120105", None, "Y"),
        ("801010.SI", "农林牧渔", "801011.SI", "种植业", "850111.SI", "粮食种植",
         "600001.SH", "甲", "20111010", None, "Y"),
        ("801010.SI", "农林牧渔", "801011.SI", "种植业", "850111.SI", "粮食种植",
         "600265.SH", "ST景谷", "20111010", "20211213", "N"),
        ("801080.SI", "电子", "801081.SI", "半导体", "850811.SI", "数字芯片设计",
         "600100.SH", "同方股份", "20111010", None, "Y")])
    closes_a = [100.0, 110.0, 121.0, 133.1, 146.41]     # 每日 +10%
    closes_b = [100.0, 100.0, 90.0, 81.0, 72.9]         # 平→连跌10%
    closes_l2 = [100.0, 105.0, 110.25, 115.76, 121.55]  # L2 种植业 每日 +5%
    c.executemany("INSERT INTO tr.raw_tushare_sw_daily VALUES ('801010.SI', ?, ?, 10.0, 300.0)",
                  list(zip(D, closes_a)))
    c.executemany("INSERT INTO tr.raw_tushare_sw_daily VALUES ('801080.SI', ?, ?, -10.0, 100.0)",
                  list(zip(D, closes_b)))
    # v3: L2 行情码入 sw_daily → mart 出 L2 行 (amount=50 只进 L2 分区分母, L1 份额不受扰)
    c.executemany("INSERT INTO tr.raw_tushare_sw_daily VALUES ('801011.SI', ?, ?, 5.0, 50.0)",
                  list(zip(D, closes_l2)))
    c.executemany("INSERT INTO tr.raw_tushare_index_daily VALUES ('000300.SH', ?, 100.0)",
                  [(d,) for d in D])
    # v3 sw 链资金流底座: 个股全单净流 (万元) × as-of 归属 (v_sw_industry_pit)。
    #   600001 (801010/801011/850111): D0-D3 每日 2.0 万元 (D4 无行 → L2 当日 net NULL);
    #   600003 (801010/801012/850121, 同 L1 异 L2 分支): D0-D4 每日 3.0 万元
    #   → L1 801010 net: D0-D3 = 5e4 元, D4 = 3e4; L2 801011 net: D0-D3 = 2e4, D4 = NULL。
    c.executemany("INSERT INTO tr.v_sw_industry_pit VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        ("600001", "600001.SH", "801010.SI", "农林牧渔", "801011.SI", "种植业",
         "850111.SI", "粮食种植", "20111010", None, "Y"),
        ("600003", "600003.SZ", "801010.SI", "农林牧渔", "801012.SI", "渔业",
         "850121.SI", "水产养殖", "20111010", None, "Y")])
    c.executemany("INSERT INTO tr.raw_tushare_moneyflow VALUES ('600001.SH', ?, 2.0)",
                  [(d,) for d in D[:4]])
    c.executemany("INSERT INTO tr.raw_tushare_moneyflow VALUES ('600003.SZ', ?, 3.0)",
                  [(d,) for d in D])
    # 板块流通市值分母: circ_mv 万元 → 801010 = (100+300)万元 = 4e6 元; 801011 = 1e6 元
    for d in D:
        c.execute("INSERT INTO tr.raw_tushare_daily_basic VALUES ('600001.SH', ?, 100.0)", [d])
        c.execute("INSERT INTO tr.raw_tushare_daily_basic VALUES ('600003.SZ', ?, 300.0)", [d])
    # v3 drill 叶子层 form (as-of 取每股 <= date 最新行; D2 旧行必须被 D3 行覆盖)
    c.executemany("INSERT INTO fact_stock_form_daily VALUES (?, ?, ?, ?)", [
        ("600001", D[2], "上升通道", False),
        ("600001", D[3], "低位横盘", True)])
    # 个股日线 + B1 分层 (sw 链广度/涨跌停聚合桥)
    for d in D:
        c.execute("INSERT INTO tr.raw_tushare_daily VALUES ('600001.SH', ?, 1.0)", [d])
        c.execute("INSERT INTO tr.raw_tushare_daily VALUES ('600002.SZ', ?, -1.0)", [d])
        c.execute("INSERT INTO dim_stock_segment_daily VALUES ('600001', ?, 'large', 'low', '农林牧渔')", [d])
        c.execute("INSERT INTO dim_stock_segment_daily VALUES ('600002', ?, 'mid', 'high', '电子')", [d])
    # 涨跌停: 0102/0108 源整日缺失; 0104 只有 D (U+Z=0 炸板率边界); 0105 U+Z 各 1。
    # v2 列: (limit_times, fd_amount, open_times, first_time); first_time 无前导零陷阱
    # ('92500'=09:25:00 是秒板, '131757' 不是); D/Z 行 limit_times 缺失。
    c.executemany('INSERT INTO tr.raw_tushare_limit_list_d VALUES (?, ?, ?, ?, ?, ?, ?)', [
        ("600001.SH", D[1], "U", 1.0, 1000.0, 0, "131757"),
        ("600002.SZ", D[2], "D", None, None, None, None),
        ("600001.SH", D[3], "U", 2.0, 3000.0, 1, "92500"),
        ("600002.SZ", D[3], "Z", None, None, 2, "94000"),
    ])
    c.execute("INSERT INTO tr.raw_tushare_moneyflow_mkt_dc VALUES (?, -1000.0)", [D[0]])
    # v2 两融 (跨交易所直和 + 日增): D0=150, D1=170 (chg=+20), 其余日源缺 → NULL
    c.executemany("INSERT INTO tr.raw_tushare_margin VALUES (?, ?, ?)", [
        (D[0], "SSE", 100.0), (D[0], "SZSE", 50.0),
        (D[1], "SSE", 110.0), (D[1], "SZSE", 60.0),
    ])
    # v2 大盘估值/换手 (mkt_valuation_code 行; 000905 是干扰行必须被过滤)
    c.executemany("INSERT INTO tr.raw_tushare_index_dailybasic VALUES (?, ?, ?, ?)", [
        ("000300.SH", D[0], 14.42, 3.0), ("000905.SH", D[0], 25.0, 5.0),
    ])
    # v2 龙虎榜: 600001 两个上榜理由 (家数只算 1) + 600002 → lhb_count=2;
    # top_inst 同席位买/卖双榜重复行 (net_buy 同额) 去重后 100 + (-30) = 70
    c.executemany("INSERT INTO tr.raw_tushare_top_list VALUES (?, ?, ?, ?)", [
        (D[0], "600001.SH", "甲", "日涨幅偏离值达到7%"),
        (D[0], "600001.SH", "甲", "日振幅值达到15%"),
        (D[0], "600002.SZ", "乙", "日涨幅偏离值达到7%"),
    ])
    c.executemany("INSERT INTO tr.raw_tushare_top_inst VALUES (?, ?, ?, ?, ?)", [
        (D[0], "600001.SH", "席位甲", "0", 100.0),
        (D[0], "600001.SH", "席位甲", "1", 100.0),
        (D[0], "600001.SH", "席位乙", "0", -30.0),
    ])
    # v2 最强板块 (limit_cpt_list, TI 码独立卡): 仅 D0 有榜
    c.executemany("INSERT INTO tr.raw_tushare_limit_cpt_list VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        (D[0], "885700.TI", "军工", 1, "3天3板", "2", 13, 0.6693, 1),
        (D[0], "885571.TI", "核电", 1, "3天3板", "3", 11, 2.7006, 2),
    ])
    return c


def test_config_yaml_contract():
    """生产 yaml 契约: 全部键在场, 类型/序关系合法 (值本身是真相源, 不在测试里冻结)。"""
    cfg = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "config" / "market_pulse.yaml").read_text(encoding="utf-8"))
    for key in ("rs_window_4w", "rs_window_12w", "benchmark_code", "quiet_px_band_pct",
                "quiet_min_net_amount", "top_n_sectors", "data_start_dc", "data_start_sw",
                "data_start_market", "dc_content_types",
                "sec_board_cutoff", "mkt_valuation_code", "lookback_late_days",
                "flow_z_surge", "accum_min_streak", "silent_px_band",
                "zscore_window", "cum_window"):
        assert key in cfg, f"market_pulse.yaml missing key: {key}"
    assert isinstance(cfg["lookback_late_days"], int) and cfg["lookback_late_days"] >= 1
    assert isinstance(cfg["rs_window_4w"], int) and isinstance(cfg["rs_window_12w"], int)
    assert 0 < cfg["rs_window_4w"] < cfg["rs_window_12w"]
    assert float(cfg["quiet_px_band_pct"]) > 0
    assert isinstance(cfg["dc_content_types"], list) and cfg["dc_content_types"]
    # 秒板界: HHMMSS 6 位字符串 (lpad 归一后字典序可比)
    assert isinstance(cfg["sec_board_cutoff"], str) and len(cfg["sec_board_cutoff"]) == 6
    assert isinstance(cfg["mkt_valuation_code"], str) and cfg["mkt_valuation_code"]
    # v3 flow_regime 阈值: 类型/正性 (值本身是真相源不冻结)
    assert float(cfg["flow_z_surge"]) > 0 and float(cfg["silent_px_band"]) > 0
    assert isinstance(cfg["accum_min_streak"], int) and cfg["accum_min_streak"] >= 1
    assert isinstance(cfg["zscore_window"], int) and cfg["zscore_window"] >= 2
    assert isinstance(cfg["cum_window"], int) and cfg["cum_window"] >= 1
    # 引擎 SQL 能用生产 cfg 生成 (阈值注入无语法炸点)
    assert "quiet_inflow_days" in mp._sector_sql(cfg)
    assert "flow_regime" in mp._sector_sql(cfg)
    assert "limit_times_dist_json" in mp._market_sql(cfg)


def test_quiet_streak_break_resets():
    """quiet 连续天数: 递增 → 破带归零 → 重启; 流出镜像。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        rows = c.execute(f"""
            SELECT trade_date, quiet_inflow_days, quiet_outflow_days FROM {mp.SECTOR_TABLE}
            WHERE chain = '{mp.CHAIN_DC}' AND sector_code = 'BK0001.DC' ORDER BY trade_date""").fetchall()
        assert [r[1] for r in rows] == [1, 2, 0, 1, 0], "inflow streak: 断一天必须归零"
        assert [r[2] for r in rows] == [0, 0, 0, 0, 1], "outflow streak: net<0 且价稳才计"
        # 非 quiet 板块 (|pct|>=band) 恒 0
        z = c.execute(f"""
            SELECT DISTINCT quiet_inflow_days, quiet_outflow_days FROM {mp.SECTOR_TABLE}
            WHERE sector_code = 'BK0002.DC'""").fetchall()
        assert [(r[0], r[1]) for r in z] == [(0, 0)]
    finally:
        c.close()


def test_rs_rolling_window_values():
    """RS 滚窗手算断言: 基准平盘 → rs_4w = 板块自身 w 日累计收益 (百分点), 尾对齐早期 NULL。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        rows = c.execute(f"""
            SELECT trade_date, rs_4w, rs_12w, rs_rank_4w, turnover_amt_share FROM {mp.SECTOR_TABLE}
            WHERE chain = '{mp.CHAIN_SW}' AND sector_code = '801010.SI' ORDER BY trade_date""").fetchall()
        # w4=2: 前 2 日历史不足 → NULL (PIT 尾对齐, 不外推)
        assert rows[0][1] is None and rows[1][1] is None
        # D3: (121/100-1)*100 - 0 = 21.0; D4: (133.1/110-1)*100 = 21.0
        assert rows[2][1] == pytest.approx(21.0)
        assert rows[3][1] == pytest.approx(21.0)
        # w12=5 > 样本长度 → 全 NULL
        assert all(r[2] is None for r in rows)
        # 排名: 801010 (+21) = 1, 801080 (-10) = 2
        assert rows[2][3] == 1
        weak = c.execute(f"""
            SELECT rs_4w, rs_rank_4w FROM {mp.SECTOR_TABLE}
            WHERE sector_code = '801080.SI' AND trade_date = ?""", [D[2]]).fetchone()
        assert weak[0] == pytest.approx(-10.0) and weak[1] == 2
        # 成交额占比: 300/(300+100)
        assert rows[0][4] == pytest.approx(0.75)
    finally:
        c.close()


def test_chain_isolation():
    """两链隔离: dc 行 net_amount 非空/rs 恒 NULL; sw 行 elg/rank_flow/quiet 恒 NULL; 禁跨链串码。
    v3 变更: sw 链 net_amount 不再恒 NULL (成分个股全单净流聚合, 与 dc 主力口径并列不可比) —
    但只允许来自 sw 自家聚合 (无 moneyflow/归属数据的 801080 必须仍 NULL, 不知道≠0)。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        bad_dc = c.execute(f"""
            SELECT COUNT(*) FROM {mp.SECTOR_TABLE} WHERE chain = '{mp.CHAIN_DC}'
            AND (net_amount IS NULL OR rs_4w IS NOT NULL OR rs_rank_4w IS NOT NULL
                 OR limit_up_n IS NOT NULL OR turnover_amt_share IS NOT NULL
                 OR sector_code NOT LIKE 'BK%')""").fetchone()[0]
        assert bad_dc == 0
        bad_sw = c.execute(f"""
            SELECT COUNT(*) FROM {mp.SECTOR_TABLE} WHERE chain = '{mp.CHAIN_SW}'
            AND (elg_amount IS NOT NULL OR rank_flow IS NOT NULL
                 OR quiet_inflow_days IS NOT NULL OR quiet_outflow_days IS NOT NULL
                 OR sector_code NOT LIKE '801%')""").fetchone()[0]
        assert bad_sw == 0
        # sw 无流数据板块: net/flow_regime 全 NULL (不知道≠0, 不伪造 neutral)
        no_flow = c.execute(f"""
            SELECT COUNT(*) FROM {mp.SECTOR_TABLE} WHERE sector_code = '801080.SI'
            AND (net_amount IS NOT NULL OR flow_regime IS NOT NULL)""").fetchone()[0]
        assert no_flow == 0
        # dc 广度来自 dc_index (vendor 自洽); sw 广度来自 daily×B1
        dc_b = c.execute(f"""
            SELECT up_num, down_num FROM {mp.SECTOR_TABLE}
            WHERE sector_code = 'BK0001.DC' AND trade_date = ?""", [D[0]]).fetchone()
        assert (dc_b[0], dc_b[1]) == (10, 5)
        sw_b = c.execute(f"""
            SELECT up_num, down_num FROM {mp.SECTOR_TABLE}
            WHERE sector_code = '801010.SI' AND trade_date = ?""", [D[0]]).fetchone()
        assert (sw_b[0], sw_b[1]) == (1, 0)
        # content_type 不在配置白名单 (地域) 不入链
        n_geo = c.execute(f"SELECT COUNT(*) FROM {mp.SECTOR_TABLE} WHERE sector_code = 'BK0145.DC'").fetchone()[0]
        assert n_geo == 0
    finally:
        c.close()


def test_sw_limit_counts_zero_vs_unknown():
    """涨跌停聚合: 源当日在场缺组 = 真 0; 源整日缺失 = NULL (不知道≠0)。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        rows = {r[0]: (r[1], r[2], r[3]) for r in c.execute(f"""
            SELECT trade_date, limit_up_n, limit_down_n, zha_ban_n FROM {mp.SECTOR_TABLE}
            WHERE sector_code = '801010.SI' ORDER BY trade_date""").fetchall()}
        assert rows[D[0]] == (None, None, None)   # 0102 源整日缺失
        assert rows[D[1]] == (1, 0, 0)            # 0103 农林牧渔 1 涨停
        assert rows[D[2]] == (0, 0, 0)            # 0104 当日源在场, 本行业无 → 真 0
        assert rows[D[3]] == (1, 0, 0)
    finally:
        c.close()


def test_zha_ban_rate_boundary_and_market_fields():
    """炸板率 Z/(U+Z): U+Z=0 → NULL 不炸; 常规日 0.5; 大盘净流/涨跌比/涨跌停总数。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        m = {r[0]: r for r in c.execute(f"""
            SELECT trade_date, mkt_net_amount, limit_up_total, limit_down_total,
                   zha_ban_rate, adv_dec_ratio FROM {mp.MARKET_TABLE}""").fetchall()}
        assert len(m) == 5
        # 0102: 大盘流在场; limit 源整日缺失 → 总数/炸板率 NULL; 涨1跌1 → ratio 1.0
        assert m[D[0]][1] == pytest.approx(-1000.0)
        assert m[D[0]][2] is None and m[D[0]][4] is None
        assert m[D[0]][5] == pytest.approx(1.0)
        # 0104: 只有 D → U+Z=0, 不除零 → NULL; up_total=0 down_total=1
        assert m[D[2]][2] == 0 and m[D[2]][3] == 1 and m[D[2]][4] is None
        # 0105: U=1 Z=1 → 0.5
        assert m[D[3]][4] == pytest.approx(0.5)
        # 大盘流仅 0102 有源 → 其余 NULL (不外推)
        assert m[D[3]][1] is None
    finally:
        c.close()


def test_top_sectors_json_snapshot():
    """两链 top/bottom 快照: dc 按 rank_flow (资金流), sw 按 rs_rank_4w; JSON 可解析。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        raw = c.execute(f"SELECT top_sectors_json FROM {mp.MARKET_TABLE} WHERE trade_date = ?",
                        [D[3]]).fetchone()[0]
        snap = json.loads(raw)
        assert snap["dc_top"][0]["sector_code"] == "BK0002.DC"      # net 100 > 1
        assert snap["dc_bottom"][0]["sector_code"] == "BK0001.DC"
        assert snap["sw_top"][0]["sector_code"] == "801010.SI"
        assert snap["sw_bottom"][0]["sector_code"] == "801080.SI"
        # rs 未成窗的早期日: sw 侧无可排名行 → null (不伪造)
        early = json.loads(c.execute(
            f"SELECT top_sectors_json FROM {mp.MARKET_TABLE} WHERE trade_date = ?", [D[0]]).fetchone()[0])
        assert early["sw_top"] is None
    finally:
        c.close()


def test_build_latest_incremental_idempotent():
    """幂等增量: 无新日 no-op; 新日插入后 streak/RS 跨增量边界与全量一致 (窗口在全史上算)。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        out0 = mp.build_latest(conn=c, cfg=CFG)
        assert (out0["dc_added_days"], out0["sw_added_days"], out0["market_added_days"]) == (0, 0, 0)
        # 追加 D6: dc BK0001 续流出 (0.3, -5) → outflow 应从 1 → 2 (证明增量读了历史)
        d6 = "20240109"
        c.execute("INSERT INTO tr.raw_tushare_moneyflow_ind_dc VALUES "
                  "(?, '行业', 'BK0001.DC', '煤炭行业', 0.3, 100.0, -5.0, -2.0, '云煤能源')", [d6])
        # d6 成分+个股流: 2 成分 1 流入 → 宽度 1/2 (验证 dc_day_where 下推不丢增量日)
        c.executemany("INSERT INTO tr.raw_tushare_dc_member VALUES (?, 'BK0001.DC', ?, ?)",
                      [(d6, "600001.SH", "甲"), (d6, "600002.SZ", "乙")])
        c.executemany("INSERT INTO tr.raw_tushare_moneyflow_dc VALUES (?, ?, ?, ?, ?)",
                      [(d6, "600001.SH", "甲", 5.0, 1.2), (d6, "600002.SZ", "乙", -3.0, -0.4)])
        c.execute("INSERT INTO tr.raw_tushare_sw_daily VALUES ('801010.SI', ?, 161.051, 10.0, 300.0)", [d6])
        c.execute("INSERT INTO tr.raw_tushare_sw_daily VALUES ('801080.SI', ?, 65.61, -10.0, 100.0)", [d6])
        c.execute("INSERT INTO tr.raw_tushare_index_daily VALUES ('000300.SH', ?, 100.0)", [d6])
        c.execute("INSERT INTO tr.raw_tushare_daily VALUES ('600001.SH', ?, 1.0)", [d6])
        # v3: sw 流增量 (600003 补 d6 → 801010 net=3e4; 601 无行)
        c.execute("INSERT INTO tr.raw_tushare_moneyflow VALUES ('600003.SZ', ?, 3.0)", [d6])
        c.execute("INSERT INTO tr.raw_tushare_daily_basic VALUES ('600003.SZ', ?, 300.0)", [d6])
        out1 = mp.build_latest(conn=c, cfg=CFG)
        assert (out1["dc_added_days"], out1["sw_added_days"], out1["market_added_days"]) == (1, 1, 1)
        row = c.execute(f"""
            SELECT quiet_outflow_days FROM {mp.SECTOR_TABLE}
            WHERE sector_code = 'BK0001.DC' AND trade_date = ?""", [d6]).fetchone()
        assert row[0] == 2, "增量插入的 streak 必须接续历史 (非从 1 重数)"
        # v3 flow 列跨增量: dc 流出 streak 接续 (D4 -3, d6 -5 → -2), 价稳 → 横盘累积流出;
        # sw 净流聚合 + streak 接续 (D0-D4 全流入 5 天, d6 第 6 天)
        v3dc = c.execute(f"""
            SELECT flow_streak, flow_regime FROM {mp.SECTOR_TABLE}
            WHERE sector_code = 'BK0001.DC' AND trade_date = ?""", [d6]).fetchone()
        assert v3dc[0] == -2 and v3dc[1] == "accum_out_silent"
        v3sw = c.execute(f"""
            SELECT net_amount, flow_streak FROM {mp.SECTOR_TABLE}
            WHERE sector_code = '801010.SI' AND trade_date = ?""", [d6]).fetchone()
        assert v3sw[0] == pytest.approx(30000.0) and v3sw[1] == 6
        # v2 列跨增量: content_type/龙头透传; 宽度日期下推 (d6 有成分+流 → 手算 1/2)
        v2 = c.execute(f"""
            SELECT content_type, flow_leader_stock, inflow_breadth FROM {mp.SECTOR_TABLE}
            WHERE sector_code = 'BK0001.DC' AND trade_date = ?""", [d6]).fetchone()
        assert v2[0] == "行业" and v2[1] == "云煤能源"
        assert v2[2] == pytest.approx(0.5)
        rs = c.execute(f"""
            SELECT rs_4w FROM {mp.SECTOR_TABLE}
            WHERE sector_code = '801010.SI' AND trade_date = ?""", [d6]).fetchone()
        assert rs[0] == pytest.approx(21.0)
        # 再跑 = no-op, 且无重复行
        out2 = mp.build_latest(conn=c, cfg=CFG)
        assert (out2["dc_added_days"], out2["sw_added_days"], out2["market_added_days"]) == (0, 0, 0)
        dup = c.execute(f"""
            SELECT COUNT(*) FROM (SELECT chain, sector_code, trade_date, COUNT(*) AS n
                                  FROM {mp.SECTOR_TABLE} GROUP BY 1,2,3 HAVING n > 1)""").fetchone()[0]
        assert dup == 0
    finally:
        c.close()


def test_build_latest_backfills_late_columns():
    """迟到列回补 (R1 根因5, 2026-07-03): margin 是 t+1 披露源 — 早跑日行 rzrqye 以 NULL 入库
    后定格。build_latest 对最近 lookback_late_days 个已存在日 DELETE+重插 → 迟到列治愈;
    幂等 (行数不变仅列值更新), 无新源日时 added_days 仍为 0。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        before_m = c.execute(f"SELECT COUNT(*) FROM {mp.MARKET_TABLE}").fetchone()[0]
        before_s = c.execute(f"SELECT COUNT(*) FROM {mp.SECTOR_TABLE}").fetchone()[0]
        # 最近源日 D4: rebuild 时 margin 未披露 (fixture 仅 D0/D1 有) → 行已带 NULL 定格
        frozen = c.execute(f"SELECT rzrqye, rzrqye_chg FROM {mp.MARKET_TABLE} "
                           "WHERE trade_date = ?", [D[4]]).fetchone()
        assert frozen[0] is None and frozen[1] is None
        # margin t+1 迟到补披露 D4 (SSE+SZSE 直和 180; 前一有数日 D1=170 → chg=+10)
        c.executemany("INSERT INTO tr.raw_tushare_margin VALUES (?, ?, ?)", [
            (D[4], "SSE", 120.0), (D[4], "SZSE", 60.0)])
        out = mp.build_latest(conn=c, cfg=CFG)
        assert (out["dc_added_days"], out["sw_added_days"], out["market_added_days"]) == (0, 0, 0)
        assert out["late_refreshed_days"]["market"] == 2   # lookback=2 → D3/D4 重插
        healed = c.execute(f"SELECT rzrqye, rzrqye_chg FROM {mp.MARKET_TABLE} "
                           "WHERE trade_date = ?", [D[4]]).fetchone()
        assert healed[0] == pytest.approx(180.0), "迟到行必须被治愈 (NULL → 实值)"
        assert healed[1] == pytest.approx(10.0)   # 180 - 170 (LAG 跨窗口边界读全史)
        # 窗口外日不受影响; 窗口内无源日 (D3) 仍 NULL (不知道≠0, 不伪造)
        assert c.execute(f"SELECT rzrqye FROM {mp.MARKET_TABLE} WHERE trade_date = ?",
                         [D[3]]).fetchone()[0] is None
        assert c.execute(f"SELECT rzrqye FROM {mp.MARKET_TABLE} WHERE trade_date = ?",
                         [D[1]]).fetchone()[0] == pytest.approx(170.0)
        # 幂等: 两表行数不变, 无重复行; 再跑一次值稳定
        assert c.execute(f"SELECT COUNT(*) FROM {mp.MARKET_TABLE}").fetchone()[0] == before_m
        assert c.execute(f"SELECT COUNT(*) FROM {mp.SECTOR_TABLE}").fetchone()[0] == before_s
        mp.build_latest(conn=c, cfg=CFG)
        assert c.execute(f"SELECT COUNT(*) FROM {mp.MARKET_TABLE}").fetchone()[0] == before_m
        assert c.execute(f"SELECT rzrqye FROM {mp.MARKET_TABLE} WHERE trade_date = ?",
                         [D[4]]).fetchone()[0] == pytest.approx(180.0)
        dup = c.execute(f"""
            SELECT COUNT(*) FROM (SELECT trade_date, COUNT(*) AS n
                                  FROM {mp.MARKET_TABLE} GROUP BY 1 HAVING n > 1)""").fetchone()[0]
        assert dup == 0
    finally:
        c.close()


def test_build_latest_bootstraps_missing_tables():
    """表不存在 → build_latest 走全量重建 (首跑/重置后自举)。"""
    c = _fixture_conn()
    try:
        out = mp.build_latest(conn=c, cfg=CFG)
        assert out.get("mode") == "rebuild" and out["sector_rows"] > 0
    finally:
        c.close()


def test_build_latest_rebuilds_on_v1_schema():
    """schema 升级守卫: 表在但缺 v2 哨兵列 (v1 残留) → 自动全量重建, 不做列数不齐的 INSERT。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        c.execute("DROP INDEX IF EXISTS idx_pulse_sector")  # 索引挡 ALTER; 重建会重造
        c.execute(f"ALTER TABLE {mp.SECTOR_TABLE} DROP COLUMN content_type")  # 模拟 v1 残留
        out = mp.build_latest(conn=c, cfg=CFG)
        assert out.get("mode") == "rebuild"
        n = c.execute(f"""
            SELECT COUNT(*) FROM {mp.SECTOR_TABLE}
            WHERE content_type IS NOT NULL""").fetchone()[0]
        assert n > 0, "重建后 v2 列必须回来且有值"
    finally:
        c.close()


def test_get_helpers_asof():
    """as-of 查询: 周末回退最近入库日; chain 过滤; 未知 chain 报错。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        rows = mp.get_sector_pulse("20240106", chain=mp.CHAIN_SW, conn=c)  # 周六 → 回退 0105
        assert rows and all(r["trade_date"] == D[3] and r["chain"] == mp.CHAIN_SW for r in rows)
        allrows = mp.get_sector_pulse("20240106", conn=c)
        assert {r["chain"] for r in allrows} == {mp.CHAIN_DC, mp.CHAIN_SW}
        mkt = mp.get_market_pulse("20240106", conn=c)
        assert mkt is not None and mkt["trade_date"] == D[3]
        with pytest.raises(ValueError):
            mp.get_sector_pulse("20240106", chain="ths_industry", conn=c)
    finally:
        c.close()


def test_get_helpers_asof_semantics():
    """get_sector_pulse / get_market_pulse: as-of 回退到 <= as_of 最近入库日; 分链独立回退。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        # 周末 as_of (20240106) 回退到 20240105; D[4]=20240108 之后的 as_of 取 20240108
        rows = mp.get_sector_pulse("20240106", conn=c)
        assert rows and all(r["trade_date"] == "20240105" for r in rows)
        dc_only = mp.get_sector_pulse("20240108", chain=mp.CHAIN_DC, conn=c)
        assert dc_only and all(r["chain"] == mp.CHAIN_DC for r in dc_only)
        assert all(r["net_amount"] is not None for r in dc_only)  # dc 链 net_amount 有值
        with pytest.raises(ValueError):
            mp.get_sector_pulse("20240108", chain="ths_industry", conn=c)
        m = mp.get_market_pulse("20240106", conn=c)
        assert m is not None and m["trade_date"] == "20240105"
        assert mp.get_market_pulse("20000101", conn=c) is None  # 早于全部数据
    finally:
        c.close()


# ── v2 第一批 (2026-07-02): content_type / 龙头 / 宽度 / 情绪周期 / 水位 / 龙虎榜 / 最强板块 ──


def test_v2_content_type_and_leaders_per_chain():
    """content_type 分链: dc 透出源值 (行业/概念), sw 恒 '申万L1'; 龙头三件套 + 资金龙头只在 dc。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        r1 = c.execute(f"""
            SELECT content_type, "leading", leading_code, leading_pct, flow_leader_stock
            FROM {mp.SECTOR_TABLE} WHERE sector_code = 'BK0001.DC' AND trade_date = ?""",
            [D[0]]).fetchone()
        assert tuple(r1) == ("行业", "云煤能源", "600792.SH", pytest.approx(9.98), "云煤能源")
        # BK0002 无 dc_index 行 → 涨幅龙头 NULL, 资金龙头仍有 (来自 moneyflow_ind_dc)
        r2 = c.execute(f"""
            SELECT content_type, "leading", flow_leader_stock FROM {mp.SECTOR_TABLE}
            WHERE sector_code = 'BK0002.DC' AND trade_date = ?""", [D[0]]).fetchone()
        assert tuple(r2) == ("概念", None, "万丰奥威")
        # sw 链: content_type 恒 '申万L1', 龙头族恒 NULL (vendor 隔离)
        bad_sw = c.execute(f"""
            SELECT COUNT(*) FROM {mp.SECTOR_TABLE} WHERE chain = '{mp.CHAIN_SW}'
            AND (content_type != '{mp.CONTENT_SW}' OR "leading" IS NOT NULL
                 OR leading_code IS NOT NULL OR leading_pct IS NOT NULL
                 OR flow_leader_stock IS NOT NULL OR inflow_breadth IS NOT NULL)""").fetchone()[0]
        assert bad_sw == 0
    finally:
        c.close()


def test_v2_inflow_breadth_manual():
    """宽度手算: D3 成分 4 只中 3 只有流数据, 2 只 net>0 → 2/3; D4 全非流入 → 真 0.0;
    无成分快照日 → NULL (不知道≠0)。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        rows = {r[0]: r[1] for r in c.execute(f"""
            SELECT trade_date, inflow_breadth FROM {mp.SECTOR_TABLE}
            WHERE sector_code = 'BK0001.DC' ORDER BY trade_date""").fetchall()}
        assert rows[D[0]] is None and rows[D[1]] is None and rows[D[2]] is None
        assert rows[D[3]] == pytest.approx(2.0 / 3.0)
        assert rows[D[4]] == pytest.approx(0.0)
        # BK0002 无成分快照 → 全 NULL
        b2 = c.execute(f"""
            SELECT DISTINCT inflow_breadth FROM {mp.SECTOR_TABLE}
            WHERE sector_code = 'BK0002.DC'""").fetchall()
        assert [r[0] for r in b2] == [None]
    finally:
        c.close()


def test_v2_limit_ladder_promotion_secboard():
    """情绪周期族: 天梯 JSON / 晋级率 (含昨日 0 板不除零边界) / 最高板 / 秒板 (first_time
    无前导零 lpad 归一) / 封单均额 / 炸板总次数; 源整日缺失 → 全 NULL。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        cols = ["trade_date", "max_limit_times", "limit_times_dist_json", "promotion_rate",
                "sec_board_n", "avg_fd_amount", "open_times_total"]
        m = {r[0]: dict(zip(cols, r)) for r in c.execute(f"""
            SELECT {', '.join(cols)} FROM {mp.MARKET_TABLE}""").fetchall()}
        # D0/D4: limit 源整日缺失 → 全 NULL (不知道≠0)
        for d in (D[0], D[4]):
            assert m[d]["max_limit_times"] is None and m[d]["limit_times_dist_json"] is None
            assert m[d]["promotion_rate"] is None and m[d]["sec_board_n"] is None
            assert m[d]["avg_fd_amount"] is None and m[d]["open_times_total"] is None
        # D1: 1 只 1 板; 首个源日无昨日 → 晋级率 NULL; '131757' 非秒板
        d1 = m[D[1]]
        assert d1["max_limit_times"] == 1 and json.loads(d1["limit_times_dist_json"]) == {"1": 1}
        assert d1["promotion_rate"] is None and d1["sec_board_n"] == 0
        assert d1["avg_fd_amount"] == pytest.approx(1000.0) and d1["open_times_total"] == 0
        # D2: 源在场但 0 涨停 → 真 0 / '{}'; 晋级率 = 0 (今 0 只 >=2 板 ÷ 昨 1 板)
        d2 = m[D[2]]
        assert d2["max_limit_times"] == 0 and json.loads(d2["limit_times_dist_json"]) == {}
        assert d2["promotion_rate"] == pytest.approx(0.0)
        assert d2["avg_fd_amount"] is None and d2["open_times_total"] == 0
        # D3: 2 板 1 只 + Z 1 只; 昨日 (D2) 0 板 → 晋级率 NULL 不除零 (契约边界);
        # '92500' lpad → '092500' <= '093059' 秒板 1; 炸板总次数 = U(1) + Z(2) = 3
        d3 = m[D[3]]
        assert d3["max_limit_times"] == 2 and json.loads(d3["limit_times_dist_json"]) == {"2": 1}
        assert d3["promotion_rate"] is None, "昨日 0 板必须 NULL, 不除零"
        assert d3["sec_board_n"] == 1 and d3["avg_fd_amount"] == pytest.approx(3000.0)
        assert d3["open_times_total"] == 3
    finally:
        c.close()


# ── v3 (2026-07-03): flow_regime 分类学 / sw L2/L3 聚合 / level / cum_ratio ──


# 6 标签 + neutral + 双向 surge 优先级 全覆盖的手算序列 (net 元, pct 百分数):
# 判定用 CFG 小窗 zw=3 / cw=3 / z_surge=2.0 / min_streak=2 / band=1.0
_REGIME_DAYS = [f"202402{i:02d}" for i in range(1, 11)]
_REGIME_SERIES = [
    # (net, pct, expect_regime, why)
    (1.0, 0.1, "neutral",          "streak=1<2, z 窗未满"),
    (2.0, 0.1, "accum_in_silent",  "streak=2, px_cum=1.001^2-1=0.2%<1"),
    (1.0, 0.1, "accum_in_silent",  "streak=3, px_cum=0.3%"),
    (10.0, 0.1, "surge_in",        "z=(10-4/3)/0.5774=15.01>=2 且 net>0; streak=4 也满足 accum → surge 优先"),
    (1.0, 0.1, "accum_in_silent",  "z=(1-13/3)/4.933=-0.68 无脉冲; streak=5, px=0.5%"),
    (1.0, 2.0, "accum_in_driving", "streak=6, px_cum=1.001^5*1.02-1=2.5%>=1 上行累积"),
    (-2.0, -0.1, "neutral",        "转向首日 streak=-1"),
    (-1.0, -0.1, "accum_out_silent", "streak=-2, px_cum=-0.2%"),
    (-1.0, -3.0, "accum_out_driving", "streak=-3, px_cum=0.999^2*0.97-1=-3.09%<=-1 下行累积"),
    (-20.0, -0.1, "surge_out",     "z=(-20+4/3)/0.5774=-32.3<=-2 且 net<0; streak=-4 px=-3.2% 也满足 accum_out_driving → surge 优先"),
]


def _regime_conn():
    """独立小 fixture: 单 dc 板块 10 日脚本化序列 (不与主 fixture 日期域互扰)。"""
    c = duck_mem()
    c.executescript(_DDL)
    c.executemany(
        "INSERT INTO tr.raw_tushare_moneyflow_ind_dc VALUES "
        "(?, '行业', 'BK0009.DC', '贵金属', ?, 100.0, ?, ?, '赤峰黄金')",
        [(d, p, n, n / 2) for d, (n, p, *_) in zip(_REGIME_DAYS, _REGIME_SERIES)])
    return c


def test_v3_flow_regime_taxonomy_and_priority():
    """flow_regime 判定序: 6 标签逐日手算 + surge 双向优先级 (surge_in 压 accum_in_silent /
    surge_out 压 accum_out_driving) + z 的 net 同号 guard (由 surge_in 日 net>0 隐式覆盖)。"""
    c = _regime_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        rows = c.execute(f"""
            SELECT trade_date, flow_streak, flow_z, flow_regime FROM {mp.SECTOR_TABLE}
            WHERE sector_code = 'BK0009.DC' ORDER BY trade_date""").fetchall()
        assert len(rows) == len(_REGIME_SERIES)
        got = [r[3] for r in rows]
        want = [x[2] for x in _REGIME_SERIES]
        assert got == want, f"判定序错位: {list(zip(_REGIME_DAYS, got, want))}"
        # streak 带符号递推
        assert [r[1] for r in rows] == [1, 2, 3, 4, 5, 6, -1, -2, -3, -4]
        # flow_z 手算锚点: 尾对齐 (前 3 日窗未满 → NULL); surge 两日
        assert rows[0][2] is None and rows[1][2] is None and rows[2][2] is None
        assert rows[3][2] == pytest.approx((10 - 4 / 3) / 0.57735, rel=1e-3)
        assert rows[9][2] == pytest.approx((-20 + 4 / 3) / 0.57735, rel=1e-3)
        # 无 dc_index 行 → level/cum_ratio NULL (分母缺, 不知道≠0)
        nulls = c.execute(f"""
            SELECT COUNT(*) FROM {mp.SECTOR_TABLE} WHERE sector_code = 'BK0009.DC'
            AND (level IS NOT NULL OR cum_ratio_20d IS NOT NULL)""").fetchone()[0]
        assert nulls == 0
    finally:
        c.close()


def test_v3_sw_level_rows_and_flow_aggregation():
    """sw 链 L2 聚合数值手算 + level 列 + 同级分区不串: L1 801010 net = 600001(2万)+600003(3万)
    ×1e4 (万元→元; 600003 在同 L1 异 L2 分支); L2 801011 net 只含 600001; 850111 不在 sw_daily
    → 无行 (面板基底=sw_daily)。turnover_amt_share/rs_rank 同级分区, L1 数值与 v2 逐 bit 一致。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        lv = {r[0]: r[1] for r in c.execute(f"""
            SELECT sector_code, level FROM {mp.SECTOR_TABLE}
            WHERE chain = '{mp.CHAIN_SW}' AND trade_date = ?""", [D[0]]).fetchall()}
        assert lv == {"801010.SI": "L1", "801080.SI": "L1", "801011.SI": "L2"}
        n_l3 = c.execute(f"SELECT COUNT(*) FROM {mp.SECTOR_TABLE} WHERE sector_code = '850111.SI'").fetchone()[0]
        assert n_l3 == 0, "无行情码不出行 (面板基底=sw_daily)"
        l1 = {r[0]: r[1] for r in c.execute(f"""
            SELECT trade_date, net_amount FROM {mp.SECTOR_TABLE}
            WHERE sector_code = '801010.SI' ORDER BY trade_date""").fetchall()}
        assert l1[D[0]] == pytest.approx(50000.0)   # (2+3) 万元 → 元
        assert l1[D[3]] == pytest.approx(50000.0)
        assert l1[D[4]] == pytest.approx(30000.0)   # 600001 D4 无流行 → 只剩 600003
        l2 = {r[0]: r[1] for r in c.execute(f"""
            SELECT trade_date, net_amount FROM {mp.SECTOR_TABLE}
            WHERE sector_code = '801011.SI' ORDER BY trade_date""").fetchall()}
        assert l2[D[0]] == pytest.approx(20000.0)
        assert l2[D[4]] is None, "成员当日无流数据 → NULL (不知道≠0)"
        # 同级分区: L1 turnover share 与 v2 一致 (300/400), L2 独占自己分区 = 1.0
        sh = c.execute(f"""
            SELECT turnover_amt_share FROM {mp.SECTOR_TABLE}
            WHERE sector_code = '801011.SI' AND trade_date = ?""", [D[0]]).fetchone()[0]
        assert sh == pytest.approx(1.0)
        # rs_rank 同级: L2 801011 独行 rank=1 (不与 31 L1 混排)
        rk = c.execute(f"""
            SELECT rs_rank_4w FROM {mp.SECTOR_TABLE}
            WHERE sector_code = '801011.SI' AND trade_date = ?""", [D[2]]).fetchone()[0]
        assert rk == 1
    finally:
        c.close()


def test_v3_cum_ratio_and_dc_level():
    """cum_ratio_20d 手算 (cw=3): sw 801010 D2 = 15e4/4e6*100 = 3.75%; 窗未满 (D0/D1) → NULL。
    dc BK0001 D2 = (10+5+7)/2e6*100 = 0.0011%; dc level 透出 (dc_index.level=1 → 'L1',
    无 dc_index 行的 BK0002 → NULL)。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        sw = {r[0]: r[1] for r in c.execute(f"""
            SELECT trade_date, cum_ratio_20d FROM {mp.SECTOR_TABLE}
            WHERE sector_code = '801010.SI' ORDER BY trade_date""").fetchall()}
        assert sw[D[0]] is None and sw[D[1]] is None
        assert sw[D[2]] == pytest.approx(3.75)
        assert sw[D[4]] == pytest.approx((5 + 5 + 3) * 1e4 / 4e6 * 100)
        dc = {r[0]: (r[1], r[2]) for r in c.execute(f"""
            SELECT trade_date, cum_ratio_20d, level FROM {mp.SECTOR_TABLE}
            WHERE sector_code = 'BK0001.DC' ORDER BY trade_date""").fetchall()}
        assert dc[D[0]][0] is None
        assert dc[D[2]][0] == pytest.approx(22.0 / 2e6 * 100)
        assert all(v[1] == "L1" for v in dc.values())
        lvl2 = c.execute(f"""
            SELECT DISTINCT level FROM {mp.SECTOR_TABLE} WHERE sector_code = 'BK0002.DC'""").fetchall()
        assert [r[0] for r in lvl2] == [None]
    finally:
        c.close()


def test_v2_margin_coverage_gate_sse_only_day():
    """覆盖门 (2026-07-03 审计修1): 仅 SSE 1 行的日 rzrqye 必须 NULL (直和腰斩 ≠ 真值,
    不知道≠0); rzrqye_chg 在 qualifying 序列上 LAG — 前后 qualifying 日的 chg 跨过 SSE-only 日
    相减, 不产生 ±腰斩幅度的假摆动, 也不撞 NULL。"""
    c = _fixture_conn()
    try:
        # fixture 已有 D0=150 (SSE+SZSE) / D1=170 (SSE+SZSE); 追加 D2 SSE-only / D3 双所 190
        c.execute("INSERT INTO tr.raw_tushare_margin VALUES (?, 'SSE', 200.0)", [D[2]])
        c.executemany("INSERT INTO tr.raw_tushare_margin VALUES (?, ?, ?)", [
            (D[3], "SSE", 120.0), (D[3], "SZSE", 70.0)])
        mp.rebuild_all(conn=c, cfg=CFG)
        m = {r[0]: (r[1], r[2]) for r in c.execute(f"""
            SELECT trade_date, rzrqye, rzrqye_chg FROM {mp.MARKET_TABLE}""").fetchall()}
        assert m[D[1]] == (pytest.approx(170.0), pytest.approx(20.0))
        # SSE-only 日: 覆盖门 → 值/chg 双 NULL (修前: rzrqye=200 直和腰斩, chg=+30 假摆动)
        assert m[D[2]] == (None, None), "SSE-only 日必须 NULL, 不出腰斩直和"
        # 下一 qualifying 日: chg 跨过 SSE-only 日 = 190-170 (修前: 190-200=-10 假摆动)
        assert m[D[3]] == (pytest.approx(190.0), pytest.approx(20.0))
    finally:
        c.close()


def test_v2_margin_valuation_lhb_strongest():
    """水位/龙虎榜/最强板块: 两融跨所直和+日增 / 估值行过滤干扰指数 / 上榜家数 DISTINCT /
    席位净买双榜去重 / strongest JSON rank 序。"""
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        cols = ["trade_date", "rzrqye", "rzrqye_chg", "mkt_pe", "mkt_turnover",
                "lhb_count", "lhb_inst_net", "strongest_sectors_json"]
        m = {r[0]: dict(zip(cols, r)) for r in c.execute(f"""
            SELECT {', '.join(cols)} FROM {mp.MARKET_TABLE}""").fetchall()}
        d0 = m[D[0]]
        assert d0["rzrqye"] == pytest.approx(150.0)      # SSE 100 + SZSE 50
        assert d0["rzrqye_chg"] is None                  # 首个源日无昨日
        assert d0["mkt_pe"] == pytest.approx(14.42)      # 000300 行, 000905 干扰行被滤
        assert d0["mkt_turnover"] == pytest.approx(3.0)
        assert d0["lhb_count"] == 2                      # 600001 两理由算 1 家 + 600002
        assert d0["lhb_inst_net"] == pytest.approx(70.0)  # 席位甲双榜去重 100 + 席位乙 -30
        snap = json.loads(d0["strongest_sectors_json"])
        assert [s["ts_code"] for s in snap] == ["885700.TI", "885571.TI"]  # rank 升序
        assert snap[0]["up_stat"] == "3天3板" and snap[0]["up_nums"] == 13
        assert snap[0]["rank"] == 1 and snap[0]["cons_nums"] == "2"
        d1 = m[D[1]]
        assert d1["rzrqye"] == pytest.approx(170.0)
        assert d1["rzrqye_chg"] == pytest.approx(20.0)
        # 源缺日全 NULL (不知道≠0)
        d3 = m[D[3]]
        assert d3["rzrqye"] is None and d3["mkt_pe"] is None
        assert d3["lhb_count"] is None and d3["strongest_sectors_json"] is None
    finally:
        c.close()
