"""
sector_momentum.py — 板块动量模块

计算通达信行业板块的技术状态（MACD/均线/趋势），
与机构事件叠加产生"双重确认"信号。

核心逻辑：
  机构行为(new_entry/increase) + 板块技术面启动(MACD金叉/底部反转)
  = 双重确认信号（含金量高于单维度信号）

数据来源：
  - 板块指数 K 线：成分股等权合成
  - 机构事件：fact_institution_event
  - 行业映射：dim_stock_tdx_industry (tdx_l1/tdx_l2, 以 tdx_l1_name 作板块名)

计算结果存入 mart_sector_momentum 表，被 scoring.py / screening_engine.py 读取。
单点计算、多处复用。
"""

import json
import logging
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from services.industry import (
    industry_join_clause,
    industry_level_value,
    industry_level_nonempty_condition,
    industry_level_select,
    load_industry_map,
)

logger = logging.getLogger("cm-api")

SECTOR_LEVEL = 1


def _sector_select(alias: str) -> str:
    return industry_level_select(SECTOR_LEVEL, alias=alias, result_alias="sector_name")


def _sector_nonempty_condition(alias: str) -> str:
    return industry_level_nonempty_condition(SECTOR_LEVEL, alias=alias)


# ============================================================
# Schema
# ============================================================

def ensure_tables(conn):
    """创建板块动量表"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mart_sector_momentum (
            sector_name     TEXT PRIMARY KEY,
            sector_code     TEXT,
            sector_level    TEXT DEFAULT 'L1',
            calc_date       TEXT,
            close           REAL,
            ma20            REAL,
            ma60            REAL,
            macd_dif        REAL,
            macd_dea        REAL,
            macd_hist       REAL,
            macd_cross      INTEGER DEFAULT 0,
            macd_cross_days INTEGER,
            trend_state     TEXT,
            price_vs_ma20   REAL,
            price_vs_ma60   REAL,
            pullback_from_high REAL,
            rally_from_low  REAL,
            return_1m       REAL,
            return_3m       REAL,
            return_6m       REAL,
            return_12m      REAL,
            excess_1m       REAL,
            excess_3m       REAL,
            excess_6m       REAL,
            excess_12m      REAL,
            rotation_score  REAL,
            rotation_rank   INTEGER,
            rotation_rank_1m INTEGER,
            rotation_rank_3m INTEGER,
            rotation_bucket TEXT,
            rotation_blacklisted INTEGER DEFAULT 0,
            momentum_score  REAL,
            detail_json     TEXT,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_msm_score ON mart_sector_momentum(momentum_score);

        CREATE TABLE IF NOT EXISTS mart_dual_confirm (
            stock_code      TEXT NOT NULL,
            institution_id  TEXT NOT NULL,
            event_type      TEXT,
            report_date     TEXT,
            sector_name     TEXT,
            sector_momentum_score REAL,
            sector_trend_state TEXT,
            sector_macd_cross INTEGER,
            dual_confirm    INTEGER DEFAULT 0,
            confirm_detail  TEXT,
            updated_at      TEXT,
            PRIMARY KEY (stock_code, institution_id, report_date)
        );
        CREATE INDEX IF NOT EXISTS idx_mdc_dual ON mart_dual_confirm(dual_confirm);
        CREATE INDEX IF NOT EXISTS idx_mdc_stock ON mart_dual_confirm(stock_code);
    """)
    for col in [
        "return_1m REAL", "return_3m REAL", "return_6m REAL", "return_12m REAL",
        "excess_1m REAL", "excess_3m REAL", "excess_6m REAL", "excess_12m REAL",
        "rotation_score REAL", "rotation_rank INTEGER", "rotation_rank_1m INTEGER",
        "rotation_rank_3m INTEGER", "rotation_bucket TEXT", "rotation_blacklisted INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(f"ALTER TABLE mart_sector_momentum ADD COLUMN {col}")
        except Exception:
            pass
    conn.commit()


# ============================================================
# 板块技术状态计算
# ============================================================

def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _rolling_mean(values: list[Optional[float]], window: int) -> list[Optional[float]]:
    out = []
    for idx in range(len(values)):
        chunk = values[idx - window + 1:idx + 1]
        valid = [value for value in chunk if value is not None]
        out.append(_mean(valid) if len(valid) == window else None)
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


def _macd(
    values: list[Optional[float]],
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    fast = _ema(values, 12)
    slow = _ema(values, 26)
    dif = [
        (fast_value - slow_value) if fast_value is not None and slow_value is not None else None
        for fast_value, slow_value in zip(fast, slow)
    ]
    dea = _ema(dif, 9)
    hist = [
        (dif_value - dea_value) * 2 if dif_value is not None and dea_value is not None else None
        for dif_value, dea_value in zip(dif, dea)
    ]
    return dif, dea, hist


def _cross(left: list[Optional[float]], right: list[Optional[float]]) -> list[bool]:
    out = [False]
    for idx in range(1, len(left)):
        prev_left = left[idx - 1]
        prev_right = right[idx - 1]
        cur_left = left[idx]
        cur_right = right[idx]
        out.append(
            cur_left is not None
            and cur_right is not None
            and prev_left is not None
            and prev_right is not None
            and cur_left > cur_right
            and prev_left <= prev_right
        )
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


def _rolling_extreme(values: list[Optional[float]], window: int, *, fn) -> list[Optional[float]]:
    out = []
    for idx in range(len(values)):
        chunk = values[idx - window + 1:idx + 1]
        valid = [value for value in chunk if value is not None]
        out.append(fn(valid) if len(valid) == window else None)
    return out


def _calc_sector_state(rows: list[dict]) -> dict:
    """计算单个板块的技术状态。
    输入：按日期排序的 OHLCV records。
    """
    if len(rows) < 60:
        return {"trend_state": "unknown", "momentum_score": 0}

    closes = [_safe_float(row.get("close")) for row in rows]
    highs = [_safe_float(row.get("high")) for row in rows]
    lows = [_safe_float(row.get("low")) for row in rows]

    ma20 = _rolling_mean(closes, 20)
    ma60 = _rolling_mean(closes, 60)
    dif, dea, hist = _macd(closes)
    macd_golden = _cross(dif, dea)
    macd_cross_bl = _barslast(macd_golden)

    # 趋势状态判定
    last = len(rows) - 1
    last_close = closes[last] or 0.0
    last_ma20 = ma20[last] if ma20[last] is not None else last_close
    last_ma60 = ma60[last] if ma60[last] is not None else last_close
    last_dif = dif[last] if dif[last] is not None else 0.0
    last_dea = dea[last] if dea[last] is not None else 0.0
    last_hist = hist[last] if hist[last] is not None else 0.0
    last_macd_cross = bool(macd_golden[last]) if last < len(macd_golden) else False
    cross_days = int(macd_cross_bl[last]) if macd_cross_bl[last] is not None else -1

    # 高低点
    hhv60 = _rolling_extreme(highs, 60, fn=max)
    llv60 = _rolling_extreme(lows, 60, fn=min)
    high_60 = hhv60[last] if hhv60[last] is not None else last_close
    low_60 = llv60[last] if llv60[last] is not None else last_close

    pullback = (high_60 - last_close) / high_60 if high_60 > 0 else 0
    rally = (last_close - low_60) / low_60 if low_60 > 0 else 0

    price_vs_ma20 = (last_close / last_ma20 - 1) if last_ma20 > 0 else 0
    price_vs_ma60 = (last_close / last_ma60 - 1) if last_ma60 > 0 else 0

    # 趋势判定
    if last_close > last_ma20 > last_ma60 and last_dif > last_dea:
        trend = "bullish"
    elif last_close < last_ma20 < last_ma60 and last_dif < last_dea:
        trend = "bearish"
    elif last_dif > last_dea and last_close > last_ma60:
        trend = "recovering"
    elif last_dif < last_dea and last_close < last_ma60:
        trend = "weakening"
    else:
        trend = "neutral"

    # 动量评分 (0-100)
    # MACD 方向（30分）+ 均线位置（30分）+ 趋势力度（20分）+ 金叉新鲜度（20分）
    score = 0

    # MACD 方向
    if last_dif > 0 and last_dif > last_dea:
        score += 30
    elif last_dif > last_dea:
        score += 20
    elif last_dif > 0:
        score += 10

    # 均线位置
    if last_close > last_ma20 > last_ma60:
        score += 30
    elif last_close > last_ma60:
        score += 20
    elif last_close > last_ma20:
        score += 10

    # 趋势力度（DIF 与 DEA 的距离）
    dif_spread = abs(last_dif - last_dea) / last_close * 100 if last_close > 0 else 0
    if dif_spread > 2:
        score += 20
    elif dif_spread > 1:
        score += 15
    elif dif_spread > 0.5:
        score += 10

    # 金叉新鲜度
    if 0 <= cross_days <= 3:
        score += 20
    elif 0 <= cross_days <= 10:
        score += 10

    return {
        "close": last_close,
        "ma20": last_ma20,
        "ma60": last_ma60,
        "macd_dif": round(last_dif, 4),
        "macd_dea": round(last_dea, 4),
        "macd_hist": round(last_hist, 4),
        "macd_cross": 1 if last_macd_cross else 0,
        "macd_cross_days": cross_days,
        "trend_state": trend,
        "price_vs_ma20": round(price_vs_ma20, 4),
        "price_vs_ma60": round(price_vs_ma60, 4),
        "pullback_from_high": round(pullback, 4),
        "rally_from_low": round(rally, 4),
        "momentum_score": min(score, 100),
    }


def _calc_window_return(values: list[float], window: int) -> float:
    if not values or len(values) <= window:
        return 0.0
    last = float(values[-1])
    prev = float(values[-window - 1])
    if prev == 0:
        return 0.0
    return round((last / prev - 1) * 100, 2)


def _equal_weight_index(rows, value_col: str) -> list[dict]:
    by_code: dict[str, list[dict]] = defaultdict(list)
    all_dates = set()
    for raw in rows:
        row = dict(raw)
        value = _safe_float(row.get(value_col))
        if value is None:
            continue
        date_value = str(row.get("date") or "").strip()[:10]
        code = str(row.get("code") or "").strip()
        if not date_value or not code:
            continue
        by_code[code].append({"date": date_value, "value": value})
        all_dates.add(date_value)

    returns_by_date: dict[str, list[float]] = defaultdict(list)
    for code_rows in by_code.values():
        code_rows.sort(key=lambda item: item["date"])
        prev = None
        for item in code_rows:
            if prev not in (None, 0):
                returns_by_date[item["date"]].append(item["value"] / prev - 1)
            prev = item["value"]

    index_value = 1000.0
    series = []
    for date_value in sorted(all_dates):
        daily_ret = _mean(returns_by_date[date_value]) if returns_by_date[date_value] else 0.0
        index_value *= (1 + daily_ret)
        series.append({"date": date_value, "value": index_value})
    return series


def _series_map(series: list[dict]) -> dict[str, float]:
    return {row["date"]: row["value"] for row in series}


def _sector_index_rows(kline_rows) -> list[dict]:
    close_series = _equal_weight_index(kline_rows, "close")
    high_series = _equal_weight_index(kline_rows, "high")
    low_series = _equal_weight_index(kline_rows, "low")
    close_map = _series_map(close_series)
    high_map = _series_map(high_series)
    low_map = _series_map(low_series)
    dates = sorted(set(close_map) & set(high_map) & set(low_map))
    return [
        {
            "date": date_value,
            "close": close_map[date_value],
            "high": high_map[date_value],
            "low": low_map[date_value],
        }
        for date_value in dates
    ]


def _rank_sector_rotation(rows: list[dict]) -> list[dict]:
    if not rows:
        return []

    total = len(rows)
    edge_n = 3 if total >= 9 else 2 if total >= 5 else 1

    short_sorted = sorted(
        rows,
        key=lambda item: (
            -(item.get("excess_1m") or 0.0),
            -(item.get("momentum_score") or 0.0),
            item.get("sector_name") or "",
        ),
    )
    long_sorted = sorted(
        rows,
        key=lambda item: (
            -(item.get("excess_3m") or 0.0),
            -(item.get("momentum_score") or 0.0),
            item.get("sector_name") or "",
        ),
    )
    short_rank = {item["sector_name"]: idx + 1 for idx, item in enumerate(short_sorted)}
    long_rank = {item["sector_name"]: idx + 1 for idx, item in enumerate(long_sorted)}

    combined = []
    for item in rows:
        ex1 = float(item.get("excess_1m") or 0.0)
        ex3 = float(item.get("excess_3m") or 0.0)
        momentum = float(item.get("momentum_score") or 0.0)
        combined_score = ex1 * 0.55 + ex3 * 0.45 + (momentum - 50.0) * 0.06
        combined.append({
            **item,
            "_rotation_combined": combined_score,
        })

    combined_sorted = sorted(
        combined,
        key=lambda item: (
            -(item.get("_rotation_combined") or 0.0),
            short_rank.get(item["sector_name"], total),
            long_rank.get(item["sector_name"], total),
            item.get("sector_name") or "",
        ),
    )

    ranked = []
    for idx, item in enumerate(combined_sorted, start=1):
        if total <= 1:
            rotation_score = 50.0
        else:
            rotation_score = round(100.0 - ((idx - 1) / (total - 1)) * 100.0, 1)
        bucket = "neutral"
        if idx <= edge_n:
            bucket = "leader"
        elif idx > total - edge_n:
            bucket = "blacklist"
        ranked.append({
            **item,
            "rotation_rank": idx,
            "rotation_rank_1m": short_rank.get(item["sector_name"]),
            "rotation_rank_3m": long_rank.get(item["sector_name"]),
            "rotation_score": rotation_score,
            "rotation_bucket": bucket,
            "rotation_blacklisted": 1 if bucket == "blacklist" else 0,
        })
    return ranked


# ============================================================
# 主计算入口
# ============================================================

def calc_sector_momentum(smart_conn, mkt_conn) -> int:
    """计算所有板块的动量状态，写入 mart_sector_momentum。

    板块指数数据来源：用板块内成分股 K 线合成等权指数。
    如果有真正的板块指数 K 线（指数类型），优先使用。
    """
    ensure_tables(smart_conn)

    # 获取行业-股票映射 (按中文名聚合，板块名 = tdx_l1_name)
    industry_stocks = {}
    for row in smart_conn.execute(
        "SELECT stock_code, tdx_l1_name FROM dim_stock_tdx_industry WHERE tdx_l1_name IS NOT NULL AND tdx_l1_name != ''"
    ).fetchall():
        industry_stocks.setdefault(row["tdx_l1_name"], []).append(row["stock_code"])

    industries = [{"tdx_l1_name": sector_name} for sector_name in sorted(industry_stocks)]
    if not industries:
        logger.info("[板块动量] 无行业分类数据")
        return 0

    # 全市场等权基线：作为行业强弱的相对参照
    benchmark_close = None
    all_codes = sorted({code for codes in industry_stocks.values() for code in codes})
    if all_codes:
        try:
            benchmark_cutoff = (date.today() - timedelta(days=420)).strftime("%Y-%m-%d")
            placeholders = ",".join("?" for _ in all_codes)
            benchmark_rows = mkt_conn.execute(
                f"SELECT code, date, close FROM price_kline "
                f"WHERE code IN ({placeholders}) AND freq='daily' AND adjust='qfq' "
                f"AND date >= ? ORDER BY date",
                (*all_codes, benchmark_cutoff)
            ).fetchall()
            if benchmark_rows:
                benchmark_close = _equal_weight_index(benchmark_rows, "close")
                if len(benchmark_close) < 60:
                    benchmark_close = None
        except Exception as e:
            logger.warning(f"[板块动量] 市场等权基线计算失败: {e}")
            benchmark_close = None

    now = datetime.now().isoformat()
    calc_date = datetime.now().strftime("%Y-%m-%d")
    count = 0
    total_sectors = len(industries)
    sector_rotation_rows = []

    for sec_idx, ind_row in enumerate(industries):
        sector = ind_row["tdx_l1_name"]
        codes = industry_stocks.get(sector, [])
        if len(codes) < 5:
            continue

        try:
            # 加载成分股 K 线，合成等权指数
            placeholders = ",".join("?" for _ in codes)
            kline_rows = mkt_conn.execute(
                f"SELECT code, date, close, high, low FROM price_kline "
                f"WHERE code IN ({placeholders}) AND freq='daily' AND adjust='qfq' "
                f"ORDER BY date",
                codes
            ).fetchall()

            if not kline_rows:
                continue

            sector_rows = _sector_index_rows(kline_rows)
            if len(sector_rows) < 60:
                continue

            sector_close_values = [row["close"] for row in sector_rows]
            sector_dates = [row["date"] for row in sector_rows]
            state = _calc_sector_state(sector_rows)
            return_1m = _calc_window_return(sector_close_values, 20)
            return_3m = _calc_window_return(sector_close_values, 60)
            return_6m = _calc_window_return(sector_close_values, 120)
            return_12m = _calc_window_return(sector_close_values, 240)
            excess_1m = excess_3m = excess_6m = excess_12m = 0.0
            if benchmark_close is not None:
                benchmark_map = _series_map(benchmark_close)
                aligned_bench = [
                    benchmark_map[date_value]
                    for date_value in sector_dates
                    if date_value in benchmark_map
                ]
                if len(aligned_bench) >= 60:
                    bench_1m = _calc_window_return(aligned_bench, 20)
                    bench_3m = _calc_window_return(aligned_bench, 60)
                    bench_6m = _calc_window_return(aligned_bench, 120)
                    bench_12m = _calc_window_return(aligned_bench, 240)
                    excess_1m = round(return_1m - bench_1m, 2)
                    excess_3m = round(return_3m - bench_3m, 2)
                    excess_6m = round(return_6m - bench_6m, 2)
                    excess_12m = round(return_12m - bench_12m, 2)

            state.update({
                "return_1m": return_1m,
                "return_3m": return_3m,
                "return_6m": return_6m,
                "return_12m": return_12m,
                "excess_1m": excess_1m,
                "excess_3m": excess_3m,
                "excess_6m": excess_6m,
                "excess_12m": excess_12m,
            })
            sector_rotation_rows.append({
                "sector_name": sector,
                "excess_1m": excess_1m,
                "excess_3m": excess_3m,
                "momentum_score": state.get("momentum_score"),
            })

            smart_conn.execute("""
                INSERT OR REPLACE INTO mart_sector_momentum
                (sector_name, sector_level, calc_date, close, ma20, ma60,
                 macd_dif, macd_dea, macd_hist, macd_cross, macd_cross_days,
                 trend_state, price_vs_ma20, price_vs_ma60,
                 pullback_from_high, rally_from_low,
                 return_1m, return_3m, return_6m, return_12m,
                 excess_1m, excess_3m, excess_6m, excess_12m,
                 rotation_score, rotation_rank, rotation_rank_1m, rotation_rank_3m,
                 rotation_bucket, rotation_blacklisted, momentum_score, detail_json, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                sector, "L1", calc_date, state["close"], state["ma20"], state["ma60"],
                state["macd_dif"], state["macd_dea"], state["macd_hist"],
                state["macd_cross"], state["macd_cross_days"],
                state["trend_state"], state["price_vs_ma20"], state["price_vs_ma60"],
                state["pullback_from_high"], state["rally_from_low"],
                state["return_1m"], state["return_3m"], state["return_6m"], state["return_12m"],
                state["excess_1m"], state["excess_3m"], state["excess_6m"], state["excess_12m"],
                None, None, None, None, None, 0, state["momentum_score"],
                json.dumps(state, ensure_ascii=False), now,
            ))
            count += 1
        except Exception as e:
            logger.warning(f"[板块动量] {sector} 计算失败: {e}")
            continue

    for item in _rank_sector_rotation(sector_rotation_rows):
        smart_conn.execute("""
            UPDATE mart_sector_momentum
            SET rotation_score = ?,
                rotation_rank = ?,
                rotation_rank_1m = ?,
                rotation_rank_3m = ?,
                rotation_bucket = ?,
                rotation_blacklisted = ?
            WHERE sector_name = ? AND calc_date = ?
        """, (
            item.get("rotation_score"),
            item.get("rotation_rank"),
            item.get("rotation_rank_1m"),
            item.get("rotation_rank_3m"),
            item.get("rotation_bucket"),
            item.get("rotation_blacklisted"),
            item.get("sector_name"),
            calc_date,
        ))

    smart_conn.commit()
    logger.info(f"[板块动量] 完成: {count}/{total_sectors} 个行业板块")
    return count


# ============================================================
# 双重确认信号
# ============================================================

def calc_dual_confirm(smart_conn) -> int:
    """为最近的机构事件叠加板块动量，产生双重确认信号。

    逻辑：
    - 机构 new_entry/increase 事件
    - 所属板块 momentum_score ≥ 60 或 trend_state in (bullish, recovering) 且 macd_cross_days ≤ 10
    = dual_confirm = 1（双重确认）
    """
    ensure_tables(smart_conn)

    # 获取板块动量
    sector_rows = smart_conn.execute(
        "SELECT sector_name, momentum_score, trend_state, macd_cross FROM mart_sector_momentum"
    ).fetchall()
    sector_map = {r["sector_name"]: dict(r) for r in sector_rows}

    if not sector_map:
        logger.info("[双重确认] 无板块动量数据")
        return 0

    # 获取最近的机构 new_entry/increase 事件
    event_cutoff = (date.today() - timedelta(days=183)).strftime("%Y-%m-%d")
    events = smart_conn.execute(f"""
        SELECT e.stock_code, e.institution_id, e.event_type, e.report_date,
               si.tdx_l1_name as sector_name
        FROM fact_institution_event e
        LEFT JOIN dim_stock_tdx_industry si ON e.stock_code = si.stock_code
        WHERE e.event_type IN ('new_entry', 'increase')
          AND e.report_date >= ?
          AND si.tdx_l1_name IS NOT NULL
        ORDER BY e.report_date DESC
        """, (event_cutoff,)).fetchall()

    if not events:
        logger.info("[双重确认] 无符合条件的事件")
        return 0

    now = datetime.now().isoformat()
    count = 0

    for evt in events:
        sector = evt["sector_name"]
        sm = sector_map.get(sector)
        if not sm:
            continue

        score = sm.get("momentum_score", 0) or 0
        trend = sm.get("trend_state", "")
        mc = sm.get("macd_cross", 0)

        # 双重确认条件
        dual = 0
        reasons = []
        if score >= 60:
            dual = 1
            reasons.append(f"板块动量评分{score}")
        if trend in ("bullish", "recovering") and mc:
            dual = 1
            reasons.append(f"板块{trend}+MACD金叉")

        smart_conn.execute("""
            INSERT OR REPLACE INTO mart_dual_confirm
            (stock_code, institution_id, event_type, report_date,
             sector_name, sector_momentum_score, sector_trend_state,
             sector_macd_cross, dual_confirm, confirm_detail, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            evt["stock_code"], evt["institution_id"], evt["event_type"],
            evt["report_date"], sector, score, trend, mc, dual,
            json.dumps({"reasons": reasons}, ensure_ascii=False) if reasons else None,
            now,
        ))
        count += 1

    smart_conn.commit()
    dual_count = smart_conn.execute(
        "SELECT COUNT(*) FROM mart_dual_confirm WHERE dual_confirm = 1"
    ).fetchone()[0]
    logger.info(f"[双重确认] 完成: {count} 条事件, {dual_count} 条双重确认")
    return count
