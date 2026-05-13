"""
screening_engine.py — TDX 选股引擎

实现通达信选股公式 1/3/5。
结果存入 mart_stock_screening 单张表，前端和 scoring.py 只读此表。

公式 1: MA5 长期低于 MA90 后突破 MA145，流通市值 ≥ 30 亿
公式 3: 多级信号迭代买卖点 + 均线多头排列 + 回撤
公式 5: 连跌后首日上涨 + MACD 金叉（DIFF ≥ 0）
"""

import json
import logging
import math
from collections import defaultdict
from datetime import datetime
from typing import Optional

from services.market_db import get_canonical_kline_qfq_relation
from services.utils import latest_closed_or_raise as _latest_closed

logger = logging.getLogger("cm-api")
KLINE_DAILY_QFQ_RELATION = get_canonical_kline_qfq_relation()


# ============================================================
# Schema
# ============================================================

def ensure_tables(conn):
    """创建选股结果表"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mart_stock_screening (
            stock_code      TEXT PRIMARY KEY,
            stock_name      TEXT,
            screen_date     TEXT,
            f1_hit          INTEGER DEFAULT 0,
            f1_detail       TEXT,
            f3_hit          INTEGER DEFAULT 0,
            f3_detail       TEXT,
            f5_hit          INTEGER DEFAULT 0,
            f5_detail       TEXT,
            hit_count       INTEGER DEFAULT 0,
            float_market_cap REAL,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mss_hit ON mart_stock_screening(hit_count);
        CREATE INDEX IF NOT EXISTS idx_mss_date ON mart_stock_screening(screen_date);
    """)
    conn.commit()


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _column(rows: list[dict], name: str) -> list[Optional[float]]:
    return [_safe_float(row.get(name)) for row in rows]


def _rolling_mean(values: list[Optional[float]], window: int) -> list[Optional[float]]:
    out = []
    for idx in range(len(values)):
        chunk = values[idx - window + 1:idx + 1]
        valid = [value for value in chunk if value is not None]
        out.append(sum(valid) / window if len(valid) == window else None)
    return out


def _ema(values: list[Optional[float]], span: int) -> list[Optional[float]]:
    alpha = 2 / (span + 1)
    out = []
    ema_value = None
    valid_count = 0
    for value in values:
        if value is None:
            out.append(None)
            continue
        valid_count += 1
        ema_value = value if ema_value is None else alpha * value + (1 - alpha) * ema_value
        out.append(ema_value if valid_count >= span else None)
    return out


def _ref(values: list, n: int) -> list:
    return [None] * n + values[:-n] if n > 0 else list(values)


def _rolling_extreme(values: list[Optional[float]], window: int, *, fn) -> list[Optional[float]]:
    out = []
    for idx in range(len(values)):
        chunk = values[idx - window + 1:idx + 1]
        valid = [value for value in chunk if value is not None]
        out.append(fn(valid) if len(valid) == window else None)
    return out


def _rolling_sum(values: list[int], window: int) -> list[Optional[int]]:
    out = []
    for idx in range(len(values)):
        chunk = values[idx - window + 1:idx + 1]
        out.append(sum(chunk) if len(chunk) == window else None)
    return out


def _barslast(condition: list[bool]) -> list[Optional[int]]:
    out = []
    counter = None
    for value in condition:
        if value:
            counter = 0
        elif counter is not None:
            counter += 1
        out.append(counter)
    return out


def _barslastcount(condition: list[bool]) -> list[int]:
    out = []
    count = 0
    for value in condition:
        count = count + 1 if value else 0
        out.append(count)
    return out


def _cross(left: list[Optional[float]], right: list[Optional[float]]) -> list[bool]:
    out = [False]
    for idx in range(1, len(left)):
        cur_left = left[idx]
        cur_right = right[idx]
        prev_left = left[idx - 1]
        prev_right = right[idx - 1]
        out.append(
            cur_left is not None
            and cur_right is not None
            and prev_left is not None
            and prev_right is not None
            and cur_left > cur_right
            and prev_left <= prev_right
        )
    return out


def _macd(values: list[Optional[float]]) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    fast = _ema(values, 12)
    slow = _ema(values, 26)
    dif = [
        fast_value - slow_value if fast_value is not None and slow_value is not None else None
        for fast_value, slow_value in zip(fast, slow)
    ]
    dea = _ema(dif, 9)
    hist = [
        (dif_value - dea_value) * 2 if dif_value is not None and dea_value is not None else None
        for dif_value, dea_value in zip(dif, dea)
    ]
    return dif, dea, hist


def _gt(left, right) -> bool:
    return left is not None and right is not None and left > right


def _ge(left, right) -> bool:
    return left is not None and right is not None and left >= right


def _lt(left, right) -> bool:
    return left is not None and right is not None and left < right


def _le(left, right) -> bool:
    return left is not None and right is not None and left <= right


# ============================================================
# 公式 1: MA5 长期低于 MA90 → 突破 MA145
# ============================================================

def _formula_1(rows: list[dict], flt_mcap: float) -> dict:
    """
    MA5 长期(≥45天)低于 MA90，然后突破 MA145，
    且近 10 日中 MA5 上升 ≥ 7 天，流通市值 ≥ 30 亿。
    """
    if len(rows) < 150:
        return {"hit": False, "reason": "数据不足"}

    c = _column(rows, "close")
    ma5 = _rolling_mean(c, 5)
    ma90 = _rolling_mean(c, 90)
    ma145 = _rolling_mean(c, 145)
    top = [
        max(value for value in (ma90_value, ma145_value) if value is not None)
        if ma90_value is not None or ma145_value is not None
        else None
        for ma90_value, ma145_value in zip(ma90, ma145)
    ]

    # TJ1: MA5 低于 MA90 连续 ≥ 45 天
    ls = _barslast([_ge(ma5_value, ma90_value) for ma5_value, ma90_value in zip(ma5, ma90)])
    tj1 = [
        ls_value is not None and ls_value >= 45 and _lt(ma5_value, ma90_value)
        for ls_value, ma5_value, ma90_value in zip(ls, ma5, ma90)
    ]

    # TJ2: 近 10 日中 MA5 上升 ≥ 7 天
    ma5_up = [_gt(value, prev) for value, prev in zip(ma5, _ref(ma5, 1))]
    ma5_rising_cnt = _rolling_sum([1 if value else 0 for value in ma5_up], 10)
    tj2 = [value is not None and value >= 7 for value in ma5_rising_cnt]

    # TJ3: 近 11 日内突破 MA145 且当前在 MA145 之上
    cross_145 = _cross(c, ma145)
    b145 = _barslast(cross_145)
    cross_145_count = _rolling_sum([1 if value else 0 for value in cross_145], 11)
    tj3 = [
        count_value is not None and count_value >= 1
        and b145_value is not None and b145_value <= 10
        and _gt(close, ma145_value)
        for count_value, b145_value, close, ma145_value in zip(cross_145_count, b145, c, ma145)
    ]

    # TJ4: 自突破以来 CLOSE 始终 > MA145
    # 向量化：检查突破后没有任何一天 c < ma145——即 barslastcount(c < ma145) == 0
    # 并且必须在突破后（b145 有效）
    below_since_cross = _barslastcount([_lt(close, ma145_value) for close, ma145_value in zip(c, ma145)])
    tj4 = [
        b145_value is not None and b145_value >= 0 and below_count == 0
        for b145_value, below_count in zip(b145, below_since_cross)
    ]

    # TJ5: 近 45 日内 CLOSE > MA145 的天数 = b145+1
    close_above_145 = [1 if _gt(close, ma145_value) else 0 for close, ma145_value in zip(c, ma145)]
    cnt_above = _rolling_sum(close_above_145, 45)
    tj5 = [
        cnt_value is not None and b145_value is not None and cnt_value == b145_value + 1
        for cnt_value, b145_value in zip(cnt_above, b145)
    ]

    # TJ6: CLOSE ≤ TOP*1.06 且 CLOSE ≤ MA145*1.10
    tj6 = [
        _le(close, top_value * 1.06 if top_value is not None else None)
        and _le(close, ma145_value * 1.10 if ma145_value is not None else None)
        for close, top_value, ma145_value in zip(c, top, ma145)
    ]

    # TJ7: 近 45 日内 MA90/MA145 无交叉
    cross_90_145 = _cross(ma90, ma145)
    cross_145_90 = _cross(ma145, ma90)
    any_cross = [left or right for left, right in zip(cross_90_145, cross_145_90)]
    tj7_count = _rolling_sum([1 if value else 0 for value in any_cross], 45)
    tj7 = [value == 0 for value in tj7_count]

    # 流通市值 ≥ 30 亿
    float_mcap_ok = flt_mcap >= 3_000_000_000 if flt_mcap else False

    # 综合信号：取最后一根 K 线
    signal = [
        all(values)
        for values in zip(tj1, tj2, tj3, tj4, tj5, tj6, tj7)
    ]
    last = signal[-1] if signal else False
    hit = bool(last) and float_mcap_ok

    detail = {}
    if rows:
        idx = len(rows) - 1
        detail = {
            "ma5_below_ma90_days": int(ls[idx]) if ls[idx] is not None else 0,
            "ma5_rising_days_in_10": int(ma5_rising_cnt[idx]) if ma5_rising_cnt[idx] is not None else 0,
            "days_since_cross_145": int(b145[idx]) if b145[idx] is not None else -1,
            "float_market_cap": flt_mcap,
        }

    return {"hit": hit, "detail": detail}


# ============================================================
# 公式 3: 多级信号迭代 + 均线多头 + 回撤
# ============================================================

def _formula_3(rows: list[dict]) -> dict:
    """
    多级买卖信号迭代系统。核心逻辑：
    1. 构建 X_3 (多均线平均) 和 X_4 (加权价格)
    2. 通过 10 轮迭代产生最终买卖信号 (GSB/GSS)
    3. 要求：当前处于卖出信号后 ≤3 天（INSELL）
    4. 90 日内快速反弹比率 ≥ 40%
    5. 均线多头排列 + 回撤条件
    """
    if len(rows) < 100:
        return {"hit": False, "reason": "数据不足"}

    c = _column(rows, "close")
    o = _column(rows, "open")
    h = _column(rows, "high")
    lows = _column(rows, "low")

    # X_1 ~ X_3: 多均线系统
    ma3 = _rolling_mean(c, 3)
    ma7 = _rolling_mean(c, 7)
    ma13 = _rolling_mean(c, 13)
    ma27 = _rolling_mean(c, 27)
    x1 = [
        sum(values) / 4 if all(value is not None for value in values) else None
        for values in zip(ma3, ma7, ma13, ma27)
    ]
    x2 = _ema(c, 5)
    x3 = [x1_value if x1_value is not None else x2_value for x1_value, x2_value in zip(x1, x2)]

    # X_4: 加权价格
    x4 = [
        (high + low + 2 * open_price + 6 * close) / 10
        if all(value is not None for value in (high, low, open_price, close))
        else None
        for high, low, open_price, close in zip(h, lows, o, c)
    ]

    # 卖出形态 / 买入形态
    prev_h = _ref(h, 1)
    prev_l = _ref(lows, 1)
    prev_c = _ref(c, 1)
    x5 = []
    x6 = []
    for close, open_price, high, low, prev_high, prev_low, prev_close in zip(c, o, h, lows, prev_h, prev_l, prev_c):
        close_ratio = close / prev_close if close is not None and prev_close not in (None, 0) else None
        x5.append(
            _lt(close, open_price)
            or (_lt(close, prev_high) and _gt(close, open_price))
            or (
                _ge(close, open_price)
                and high is not None and close is not None and open_price is not None
                and high - close >= close - open_price
                and _lt(close_ratio, 1.02)
            )
            or (
                close is not None and open_price is not None and close == open_price
                and high is not None and low is not None
                and high - close >= close - low
                and _lt(close_ratio, 1.05)
            )
        )
        x6.append(
            (_gt(close, open_price) and _gt(close_ratio, 0.94))
            or (_gt(close, prev_low) and _lt(close, open_price))
            or (
                _le(close, open_price)
                and close is not None and low is not None and open_price is not None
                and close - low >= open_price - close
                and _gt(close_ratio, 0.98)
            )
            or (
                close is not None and open_price is not None and close == open_price
                and high is not None and low is not None
                and close - low >= high - close
                and _gt(close_ratio, 0.95)
            )
        )

    # 10 轮迭代信号
    prev = x4.copy()
    for _ in range(10):
        cross_down = [crossed and flag for crossed, flag in zip(_cross(prev, x3), x5)]
        cross_up = [crossed and flag for crossed, flag in zip(_cross(x3, prev), x6)]
        nxt = prev.copy()
        for idx, crossed in enumerate(cross_down):
            if crossed and x3[idx] is not None:
                nxt[idx] = x3[idx] * 0.98
        for idx, crossed in enumerate(cross_up):
            if crossed and x3[idx] is not None:
                nxt[idx] = x3[idx] * 1.02
        prev = nxt

    x36 = prev

    # 买卖信号
    gsb = _cross(x36, x3)  # 买入信号
    gss = _cross(x3, x36)  # 卖出信号

    # INSELL: 处于卖出后 ≤ 3 天
    ls_sell = _barslast(gss)
    lb_buy = _barslast(gsb)
    insell = [
        sell is not None and buy is not None and sell < buy and sell <= 3
        for sell, buy in zip(ls_sell, lb_buy)
    ]

    # 90 日内快速反弹
    gss_bl = _barslast(gss)
    gsb_after_gss = [buy and bl is not None and bl > 0 for buy, bl in zip(gsb, gss_bl)]
    fast_b_cond = [flag and bl is not None and bl <= 3 for flag, bl in zip(gsb_after_gss, gss_bl)]
    fastb = _rolling_sum([1 if value else 0 for value in fast_b_cond], 90)
    totb = _rolling_sum([1 if value else 0 for value in gsb_after_gss], 90)
    rate = [
        fast * 100 / total if fast is not None and total not in (None, 0) else 0.0
        for fast, total in zip(fastb, totb)
    ]
    histok = [
        total is not None and total >= 1 and fast is not None and fast >= 1 and rate_value >= 40
        for total, fast, rate_value in zip(totb, fastb, rate)
    ]

    # 卖出状态分析
    gsb_bl = _barslast(gsb)
    sell_state = [
        sell is not None and buy is not None and sell < buy
        for sell, buy in zip(gss_bl, gsb_bl)
    ]
    sell_state_int = [1 if value else 0 for value in sell_state]
    max_run = _rolling_extreme([float(value) for value in _barslastcount(sell_state)], 45, fn=max)
    sell_pct_raw = _rolling_sum(sell_state_int, 45)
    sell_pct = [value * 100 / 45 if value is not None else None for value in sell_pct_raw]
    green_ok = [
        run is not None and pct is not None and run <= 8 and pct <= 60
        for run, pct in zip(max_run, sell_pct)
    ]

    # 卖出质量
    max_len_data = [float(bl) if buy and bl is not None else 0.0 for buy, bl in zip(gsb, gss_bl)]
    maxlen = _rolling_extreme(max_len_data, 90, fn=max)
    longb_cond = [buy and bl is not None and bl > 10 for buy, bl in zip(gsb, gss_bl)]
    longb = _rolling_sum([1 if value else 0 for value in longb_cond], 90)
    sell_qual = [
        max_len is not None and max_len <= 20 and long_count is not None and long_count <= 2
        for max_len, long_count in zip(maxlen, longb)
    ]

    # 均线多头排列
    m20 = _rolling_mean(c, 20)
    m60 = _rolling_mean(c, 60)
    m90 = _rolling_mean(c, 90)
    m60_ref20 = _ref(m60, 20)
    m90_ref20 = _ref(m90, 20)
    up = [
        _gt(m20_value, m60_value)
        and _gt(m60_value, m90_value)
        and _gt(m60_value, m60_ref)
        and _gt(m90_value, m90_ref)
        and _gt(close, m90_value)
        and _gt(close, m60_value * 0.97 if m60_value is not None else None)
        for m20_value, m60_value, m90_value, m60_ref, m90_ref, close
        in zip(m20, m60, m90, m60_ref20, m90_ref20, c)
    ]

    # 回撤条件
    hhv20 = _rolling_extreme(h, 20, fn=max)
    ret = [close / high if close is not None and high not in (None, 0) else None for close, high in zip(c, hhv20)]
    pull = [_le(value, 0.995) and _ge(value, 0.78) for value in ret]

    # 综合
    signal = [all(values) for values in zip(insell, histok, sell_qual, green_ok, up, pull)]
    last_hit = bool(signal[-1]) if signal else False

    detail = {}
    if rows:
        idx = len(rows) - 1
        detail = {
            "insell": bool(insell[idx]),
            "fast_bounce_rate": float(rate[idx]) if rate[idx] is not None else 0,
            "ma_bullish": bool(up[idx]),
            "pullback_ratio": float(ret[idx]) if ret[idx] is not None else 0,
        }

    return {"hit": last_hit, "detail": detail}


# ============================================================
# 公式 5: 连跌后首日上涨 + MACD 金叉
# ============================================================

def _formula_5(rows: list[dict]) -> dict:
    """
    GS十三-上涨1 + MACD 金叉 + DIFF >= 0
    条件：最后一根 K 线，首日上涨（之前连续下跌），MACD 金叉且 DIFF ≥ 0。
    """
    if len(rows) < 30:
        return {"hit": False, "reason": "数据不足"}

    c = _column(rows, "close")

    # A1: 连续上涨天数 = 1（即今日刚从下跌转为上涨）
    a1 = [_gt(close, prev) for close, prev in zip(c, _ref(c, 4))]
    nt = _barslastcount(a1)

    # MACD
    dif, dea, _ = _macd(c)

    # 金叉
    macd_cross = _cross(dif, dea)

    # 综合：最后一根 + 首日上涨 + MACD 金叉 + DIFF >= 0
    signal = [
        idx == len(c) - 1 and a1_value and nt_value == 1 and cross_value and _ge(dif_value, 0)
        for idx, (a1_value, nt_value, cross_value, dif_value)
        in enumerate(zip(a1, nt, macd_cross, dif))
    ]

    last_hit = bool(signal[-1]) if signal else False

    detail = {}
    if rows:
        idx = len(rows) - 1
        detail = {
            "up_days": int(nt[idx]),
            "dif": float(dif[idx]) if dif[idx] is not None else 0,
            "dea": float(dea[idx]) if dea[idx] is not None else 0,
            "macd_cross": bool(macd_cross[idx]),
        }

    return {"hit": last_hit, "detail": detail}


# ============================================================
# 主入口：批量运行全部选股公式
# ============================================================

def run_all_screens(smart_conn, mkt_conn) -> int:
    """遍历所有股票，执行全部选股公式，写入 mart_stock_screening。
    技术指标使用 records-native helper 计算后传入各公式。
    """
    ensure_tables(smart_conn)

    # 1. 获取活跃股票列表
    # 真相源：dim_active_a_stock（security_master 维护的当前可交易 A 股清单）
    # 旧 dim_stock 表已退役（曾全表为空导致 calc_screening 静默跳过）
    stock_rows = smart_conn.execute(
        "SELECT a.stock_code, a.stock_name "
        "FROM dim_active_a_stock a "
        "LEFT JOIN excluded_stocks e ON e.stock_code = a.stock_code "
        "WHERE e.stock_code IS NULL"
    ).fetchall()
    stock_map = {r["stock_code"]: r["stock_name"] for r in stock_rows}

    if not stock_map:
        logger.warning("[选股] dim_active_a_stock 为空，请先跑「数据获取 → 同步十大股东」让 security_master 拉取主数据")
        return 0

    # 2. 批量加载财务数据（流通股本）
    fin_map = {}
    try:
        fin_rows = smart_conn.execute(
            "SELECT stock_code, float_shares FROM dim_financial_latest"
        ).fetchall()
        fin_map = {r["stock_code"]: r["float_shares"] for r in fin_rows}
    except Exception as e:
        logger.warning(f"[选股] 加载财务数据失败（公式 1 流通市值将缺失）: {e}")

    # 3. 批量加载 K 线数据
    logger.info(f"[选股] 开始筛选 {len(stock_map)} 只股票")
    codes = list(stock_map.keys())
    placeholders = ",".join("?" for _ in codes)
    kline_rows = mkt_conn.execute(
        f"SELECT code, date, open, high, low, close, volume, amount "
        f"FROM {KLINE_DAILY_QFQ_RELATION} "
        f"WHERE code IN ({placeholders}) AND freq='daily' AND adjust='qfq' "
        f"ORDER BY code, date",
        codes
    ).fetchall()

    if not kline_rows:
        logger.warning("[选股] 无 K 线数据")
        return 0

    now = datetime.now().isoformat()
    screen_date = _latest_closed()  # Phase ψ.5: calendar-gated
    results = []
    stock_groups: dict[str, list[dict]] = defaultdict(list)
    for row in kline_rows:
        item = dict(row)
        stock_groups[str(item.get("code"))].append(item)
    total_stocks = len(stock_groups)

    # 4. 逐股运行公式
    for stock_idx, (code, group) in enumerate(stock_groups.items()):
        # C-3: 进度日志
        if stock_idx > 0 and stock_idx % 500 == 0:
            logger.info(f"[选股] 进度: {stock_idx}/{total_stocks} ({stock_idx*100//total_stocks}%)")
        rows = sorted(group, key=lambda item: str(item.get("date") or ""))

        if len(rows) < 30:
            continue

        # 流通市值（NaN 防御：last_close 为 NaN 时归零，避免静默跳过 F1）
        float_shares = fin_map.get(code, 0) or 0
        last_close = _safe_float(rows[-1].get("close")) or 0
        flt_mcap = float_shares * last_close if float_shares else 0

        # 运行三个公式
        r1 = _formula_1(rows, flt_mcap)
        r3 = _formula_3(rows)
        r5 = _formula_5(rows)

        r1_hit = bool(r1["hit"])
        r3_hit = bool(r3["hit"])
        r5_hit = bool(r5["hit"])
        hit_count = int(sum([r1_hit, r3_hit, r5_hit]))

        results.append((
            str(code), stock_map.get(code, ""), screen_date,
            int(r1_hit), json.dumps(r1.get("detail", {}), ensure_ascii=False),
            int(r3_hit), json.dumps(r3.get("detail", {}), ensure_ascii=False),
            int(r5_hit), json.dumps(r5.get("detail", {}), ensure_ascii=False),
            hit_count, float(flt_mcap), now,
        ))

    # 5. 写入结果
    smart_conn.execute("DELETE FROM mart_stock_screening")
    smart_conn.executemany("""
        INSERT INTO mart_stock_screening
        (stock_code, stock_name, screen_date, f1_hit, f1_detail, f3_hit, f3_detail,
         f5_hit, f5_detail, hit_count, float_market_cap, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, results)
    smart_conn.commit()

    hit_total = sum(1 for r in results if r[9] > 0)  # hit_count > 0
    f1_count = sum(1 for r in results if r[3] == 1)
    f3_count = sum(1 for r in results if r[5] == 1)
    f5_count = sum(1 for r in results if r[7] == 1)

    logger.info(
        f"[选股] 完成: {len(results)} 只股票, "
        f"F1={f1_count} F3={f3_count} F5={f5_count}, 总命中={hit_total}"
    )
    return len(results)
