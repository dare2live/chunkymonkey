"""market_pulse 单测 — quiet 连续天数 / RS 滚窗数值 / 两链隔离 / 炸板率边界 / 幂等增量 (证伪门)。

fixture 形态 = backend/tests/fixtures/domain_samples/*.json 真实字段契约 (禁抽象命名);
内存 DuckDB 用 CREATE SCHEMA tr 模拟生产 ATTACH tushare_raw AS tr (两部名同解析)。
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
    up_num BIGINT, down_num BIGINT);
CREATE TABLE tr.raw_tushare_sw_daily (
    ts_code TEXT, trade_date TEXT, close DOUBLE, pct_change DOUBLE, amount DOUBLE);
CREATE TABLE tr.raw_tushare_index_daily (
    ts_code TEXT, trade_date TEXT, close DOUBLE);
CREATE TABLE tr.raw_tushare_index_member_all (
    l1_code TEXT, l1_name TEXT, ts_code TEXT, name TEXT, is_new TEXT);
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
    trade_date TEXT, ts_code TEXT, name TEXT, net_amount DOUBLE);
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
    c.executemany(
        "INSERT INTO tr.raw_tushare_dc_index VALUES "
        "('BK0001.DC', ?, '云煤能源', '600792.SH', 9.98, 10, 5)", [(d,) for d in D])
    # v2 inflow_breadth: BK0001 成分快照仅 D3/D4 (dc_member 2025+ 现实 → 其余日 NULL)
    #   D3: 成分 600001/600002/600003/600004, 个股流 +5/-3/+1/缺 → 宽度 = 2/3
    #   D4: 成分 600001/600002, 个股流 -1/0 (net=0 不算流入) → 宽度 = 0.0 (真 0 ≠ NULL)
    c.executemany("INSERT INTO tr.raw_tushare_dc_member VALUES (?, 'BK0001.DC', ?, ?)", [
        (D[3], "600001.SH", "甲"), (D[3], "600002.SZ", "乙"),
        (D[3], "600003.SZ", "丙"), (D[3], "600004.SZ", "丁"),
        (D[4], "600001.SH", "甲"), (D[4], "600002.SZ", "乙"),
    ])
    c.executemany("INSERT INTO tr.raw_tushare_moneyflow_dc VALUES (?, ?, ?, ?)", [
        (D[3], "600001.SH", "甲", 5.0), (D[3], "600002.SZ", "乙", -3.0),
        (D[3], "600003.SZ", "丙", 1.0),
        (D[4], "600001.SH", "甲", -1.0), (D[4], "600002.SZ", "乙", 0.0),
    ])
    # sw 链: 2 个 L1 + HS300 基准 (平盘 → rs = 板块自身滚窗收益)。成员行含 v2 下钻列
    # (ts_code/name/is_new; is_new='N' 历史行必须被 members 下钻排除)
    c.executemany("INSERT INTO tr.raw_tushare_index_member_all VALUES (?, ?, ?, ?, ?)",
                  [("801010.SI", "农林牧渔", "000592.SZ", "平潭发展", "Y"),
                   ("801010.SI", "农林牧渔", "002679.SZ", "福建金森", "Y"),
                   ("801010.SI", "农林牧渔", "600265.SH", "ST景谷", "N"),
                   ("801080.SI", "电子", "600100.SH", "同方股份", "Y")])
    closes_a = [100.0, 110.0, 121.0, 133.1, 146.41]     # 每日 +10%
    closes_b = [100.0, 100.0, 90.0, 81.0, 72.9]         # 平→连跌10%
    c.executemany("INSERT INTO tr.raw_tushare_sw_daily VALUES ('801010.SI', ?, ?, 10.0, 300.0)",
                  list(zip(D, closes_a)))
    c.executemany("INSERT INTO tr.raw_tushare_sw_daily VALUES ('801080.SI', ?, ?, -10.0, 100.0)",
                  list(zip(D, closes_b)))
    c.executemany("INSERT INTO tr.raw_tushare_index_daily VALUES ('000300.SH', ?, 100.0)",
                  [(d,) for d in D])
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
                "sec_board_cutoff", "mkt_valuation_code"):
        assert key in cfg, f"market_pulse.yaml missing key: {key}"
    assert isinstance(cfg["rs_window_4w"], int) and isinstance(cfg["rs_window_12w"], int)
    assert 0 < cfg["rs_window_4w"] < cfg["rs_window_12w"]
    assert float(cfg["quiet_px_band_pct"]) > 0
    assert isinstance(cfg["dc_content_types"], list) and cfg["dc_content_types"]
    # 秒板界: HHMMSS 6 位字符串 (lpad 归一后字典序可比)
    assert isinstance(cfg["sec_board_cutoff"], str) and len(cfg["sec_board_cutoff"]) == 6
    assert isinstance(cfg["mkt_valuation_code"], str) and cfg["mkt_valuation_code"]
    # 引擎 SQL 能用生产 cfg 生成 (阈值注入无语法炸点)
    assert "quiet_inflow_days" in mp._sector_sql(cfg)
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
    """两链隔离: dc 行 net_amount 非空/rs 恒 NULL; sw 行 net_amount 恒 NULL/quiet 恒 NULL; 禁跨链串码。"""
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
            AND (net_amount IS NOT NULL OR elg_amount IS NOT NULL OR rank_flow IS NOT NULL
                 OR quiet_inflow_days IS NOT NULL OR quiet_outflow_days IS NOT NULL
                 OR sector_code NOT LIKE '801%')""").fetchone()[0]
        assert bad_sw == 0
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
        c.executemany("INSERT INTO tr.raw_tushare_moneyflow_dc VALUES (?, ?, ?, ?)",
                      [(d6, "600001.SH", "甲", 5.0), (d6, "600002.SZ", "乙", -3.0)])
        c.execute("INSERT INTO tr.raw_tushare_sw_daily VALUES ('801010.SI', ?, 161.051, 10.0, 300.0)", [d6])
        c.execute("INSERT INTO tr.raw_tushare_sw_daily VALUES ('801080.SI', ?, 65.61, -10.0, 100.0)", [d6])
        c.execute("INSERT INTO tr.raw_tushare_index_daily VALUES ('000300.SH', ?, 100.0)", [d6])
        c.execute("INSERT INTO tr.raw_tushare_daily VALUES ('600001.SH', ?, 1.0)", [d6])
        out1 = mp.build_latest(conn=c, cfg=CFG)
        assert (out1["dc_added_days"], out1["sw_added_days"], out1["market_added_days"]) == (1, 1, 1)
        row = c.execute(f"""
            SELECT quiet_outflow_days FROM {mp.SECTOR_TABLE}
            WHERE sector_code = 'BK0001.DC' AND trade_date = ?""", [d6]).fetchone()
        assert row[0] == 2, "增量插入的 streak 必须接续历史 (非从 1 重数)"
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
