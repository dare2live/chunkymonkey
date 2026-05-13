"""Phase ε+ §3.4 — 规则类 dim_* 写死的 seed 数据。

数据全部按 A 股 2026 监管规则写死, 不依赖外部 API。
"""
from __future__ import annotations

import logging
import time


log = logging.getLogger("primitives.seed")


def seed_price_limit_rules(conn) -> int:
    """A 股涨跌停规则 (2026)。"""
    rows = [
        # 主板正常股
        ("main_normal", "main", False, False, None, 0.10, -0.10, "2020-08-24", None, "主板正常股 ±10%"),
        # 主板 ST
        ("main_st", "main", True, False, None, 0.05, -0.05, "2020-08-24", None, "主板 ST ±5%"),
        # 创业板正常股 (2020-08-24 后 ±20%)
        ("chinext_normal", "chinext", False, False, None, 0.20, -0.20, "2020-08-24", None, "创业板正常股 ±20%"),
        # 创业板 ST
        ("chinext_st", "chinext", True, False, None, 0.20, -0.20, "2020-08-24", None, "创业板 ST 仍 ±20%"),
        # 科创板正常股
        ("star_normal", "star", False, False, None, 0.20, -0.20, "2019-07-22", None, "科创板正常股 ±20%"),
        # 北交所
        ("bj_normal", "bj", False, False, None, 0.30, -0.30, "2021-11-15", None, "北交所 ±30%"),
        # 主板新股前 N 日不设限 (无穷大用 0.44 近似)
        ("main_new", "main", False, True, 5, 0.44, -0.36, "2020-08-24", None, "主板新股 5 日内不设限 (上市首日 ±44%)"),
        # 创业板/科创板/北交所新股 5 日内不设限
        ("chinext_new", "chinext", False, True, 5, 1.00, -1.00, "2020-08-24", None, "创业板新股 5 日内不设限"),
        ("star_new", "star", False, True, 5, 1.00, -1.00, "2019-07-22", None, "科创板新股 5 日内不设限"),
        ("bj_new", "bj", False, True, 5, 1.00, -1.00, "2021-11-15", None, "北交所新股 5 日内不设限"),
    ]
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM dim_price_limit_rules")
        conn.executemany(
            """INSERT INTO dim_price_limit_rules
               (rule_id, market_segment, is_st, is_new_listing, days_after_ipo,
                limit_up_pct, limit_down_pct, effective_from, effective_to, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        try: conn.execute("ROLLBACK")
        except Exception: pass
        raise
    return len(rows)


def seed_market_segments(conn) -> int:
    """A 股代码前缀规则。"""
    rows = [
        ("main_sh", "上海主板", "600,601,603,605", "^(600|601|603|605)", "上海主板"),
        ("main_sz", "深圳主板", "000,001,002", "^(000|001|002)", "深圳主板 (含中小板)"),
        ("chinext", "创业板", "300,301", "^(300|301)", "创业板"),
        ("star", "科创板", "688", "^(688)", "科创板"),
        ("bj", "北交所", "8,4,920", "^(83|87|92|43)", "北交所"),
    ]
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM dim_market_segment")
        conn.executemany(
            "INSERT INTO dim_market_segment (segment_id, segment_name, code_prefix, code_pattern_re, notes) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        try: conn.execute("ROLLBACK")
        except Exception: pass
        raise
    return len(rows)


def seed_trading_rules(conn) -> int:
    rows = [
        ("main_t1",     "main",     "T+1", 100, 0.01, "2020-08-24"),
        ("chinext_t1",  "chinext",  "T+1", 100, 0.01, "2020-08-24"),
        ("star_t1",     "star",     "T+1", 200, 0.01, "2019-07-22"),  # 科创板 200 股起
        ("bj_t1",       "bj",       "T+1", 100, 0.01, "2021-11-15"),
    ]
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM dim_trading_rule")
        conn.executemany(
            "INSERT INTO dim_trading_rule (rule_id, market_segment, settlement_cycle, min_lot_size, price_tick, effective_from) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        try: conn.execute("ROLLBACK")
        except Exception: pass
        raise
    return len(rows)


def seed_fee_schedule(conn) -> int:
    """常规 A 股费用: 千五佣金 (双边) + 万一印花税 (卖) + 万分之0.2 过户费 (双边沪市)。"""
    rows = [
        ("commission_default", "commission", 0.00025, 5.0, "both", None, "2023-08-28", "佣金 万 2.5, 最低 5 元"),
        ("stamp_tax_sell",     "stamp_tax", 0.0005, None, "sell", None, "2023-08-28", "印花税 万 5 卖出"),
        ("transfer_sh",        "transfer",  0.000001, None, "both", "main_sh", "2022-04-27", "过户费 (沪市) 万 0.01"),
    ]
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM dim_fee_schedule")
        conn.executemany(
            "INSERT INTO dim_fee_schedule (fee_id, fee_type, rate_pct, min_amount, side, market_segment, effective_from, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        try: conn.execute("ROLLBACK")
        except Exception: pass
        raise
    return len(rows)


def seed_trading_sessions(conn) -> int:
    rows = [
        ("open_call",  "集合竞价开盘", "09:15", "09:25", True,  True,  False),
        ("am_cont",    "连续竞价 上午", "09:30", "11:30", True,  False, False),
        ("pm_cont",    "连续竞价 下午", "13:00", "14:57", True,  False, False),
        ("close_call", "集合竞价收盘", "14:57", "15:00", True,  False, True),
    ]
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM dim_trading_session")
        conn.executemany(
            "INSERT INTO dim_trading_session (session_id, session_name, start_time, end_time, allow_match, is_open_session, is_close_session) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        try: conn.execute("ROLLBACK")
        except Exception: pass
        raise
    return len(rows)


def seed_liquidity_thresholds(conn) -> int:
    """流动性阈值: 主板 ≥ 5000 万 / 创业板 ≥ 3000 万 / 科创板 ≥ 2000 万 / 北交所 ≥ 500 万 20 日均成交额。"""
    rows = [
        ("main_5k_w",    "main",    50_000_000.0, 0.005, "2024-01-01", "主板 ≥ 5000 万 20d 均额"),
        ("chinext_3k_w", "chinext", 30_000_000.0, 0.005, "2024-01-01", "创业板 ≥ 3000 万"),
        ("star_2k_w",    "star",    20_000_000.0, 0.003, "2024-01-01", "科创板 ≥ 2000 万"),
        ("bj_0_5k_w",    "bj",       5_000_000.0, 0.003, "2024-01-01", "北交所 ≥ 500 万"),
    ]
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM dim_liquidity_threshold")
        conn.executemany(
            "INSERT INTO dim_liquidity_threshold (threshold_id, market_segment, min_amount_20d, min_turnover_pct, effective_from, notes) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        try: conn.execute("ROLLBACK")
        except Exception: pass
        raise
    return len(rows)


def seed_style_factors(conn) -> int:
    rows = [
        ("size",       "市值",     "log(float_market_cap)", "规模因子"),
        ("value",      "价值",     "1 / pe_ttm, 1 / pb",     "估值因子"),
        ("momentum",   "动量",     "ret_60d - ret_5d",        "12-1 月动量"),
        ("quality",    "质量",     "roe + cash_flow / net_profit", "质量因子"),
        ("volatility", "波动",     "std(daily_ret, 20)",      "20 日波动"),
        ("liquidity",  "流动性",   "log(amount_20d_mean)",    "成交量"),
    ]
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM dim_style_factor")
        conn.executemany(
            "INSERT INTO dim_style_factor (factor_id, factor_name, formula_text, notes) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        try: conn.execute("ROLLBACK")
        except Exception: pass
        raise
    return len(rows)


def seed_all_primitives(conn) -> dict:
    """一键 seed 全部 7 张规则/枚举类 dim 表。"""
    t0 = time.time()
    result = {
        "dim_price_limit_rules": seed_price_limit_rules(conn),
        "dim_market_segment": seed_market_segments(conn),
        "dim_trading_rule": seed_trading_rules(conn),
        "dim_fee_schedule": seed_fee_schedule(conn),
        "dim_trading_session": seed_trading_sessions(conn),
        "dim_liquidity_threshold": seed_liquidity_thresholds(conn),
        "dim_style_factor": seed_style_factors(conn),
    }
    log.info(f"完成: {sum(result.values())} 行 (耗时 {time.time()-t0:.2f}s)")
    return result
