"""
stock_turtle_engine.py — 海龟执行特征中间层

把 Donchian 突破、ATR 风险、参考止损和参考加仓位统一沉成结构化特征，
供验证、多维模型特征注入、评分解释和后续交易仿真共享。

这一层只存“可观察的执行准备度”，不直接持有组合仓位状态。
真实持仓、加仓步数和交易日志由后续验证/回测层维护。
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from services.industry import load_industry_map
from services.ta_lib import compute_alpha_factors, hhv, llv
from services.utils import safe_float as _safe_float, clamp_score as _clamp_score

logger = logging.getLogger("cm-api")

TURTLE_FEATURE_SCHEMA_VERSION = 1


def _round_price(value) -> Optional[float]:
    number = _safe_float(value)
    return round(number, 4) if number is not None else None


def _round_pct(value) -> Optional[float]:
    number = _safe_float(value)
    return round(number, 2) if number is not None else None


def _dist_pct(close_price: Optional[float], level: Optional[float]) -> Optional[float]:
    if close_price in (None, 0) or level in (None, 0):
        return None
    return round((close_price / level - 1) * 100, 2)


def _load_price_history(mkt_conn, codes: list[str], since_days: int = 320) -> dict[str, list[dict]]:
    history = {code: [] for code in codes}
    chunk_size = 400
    cutoff = (date.today() - timedelta(days=since_days)).strftime("%Y-%m-%d")
    for idx in range(0, len(codes), chunk_size):
        chunk = codes[idx:idx + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = mkt_conn.execute(
            f"SELECT code, date, open, high, low, close, volume, amount "
            f"FROM price_kline "
            f"WHERE code IN ({placeholders}) AND freq='daily' AND adjust='qfq' "
            f"AND date >= ? ORDER BY code, date",
            (*chunk, cutoff),
        ).fetchall()
        for row in rows:
            history.setdefault(row["code"], []).append(dict(row))
    return history


def _preferred_system(
    entry_signal_20: int,
    entry_signal_55: int,
    breakout_dist_20_pct: Optional[float],
    breakout_dist_55_pct: Optional[float],
) -> str:
    if entry_signal_55:
        return "S2"
    if entry_signal_20:
        return "S1"
    if breakout_dist_20_pct is None and breakout_dist_55_pct is None:
        return "观察"
    if breakout_dist_20_pct is None:
        return "S2"
    if breakout_dist_55_pct is None:
        return "S1"
    return "S1" if abs(breakout_dist_20_pct) <= abs(breakout_dist_55_pct) else "S2"


def _build_setup_state(
    entry_signal_20: int,
    entry_signal_55: int,
    exit_signal_10: int,
    exit_signal_20: int,
    breakout_dist_20_pct: Optional[float],
    breakout_dist_55_pct: Optional[float],
) -> str:
    if exit_signal_20:
        return "20日退出触发"
    if exit_signal_10:
        return "10日退出触发"
    if entry_signal_55:
        return "S2突破触发"
    if entry_signal_20:
        return "S1突破触发"
    if breakout_dist_20_pct is not None and breakout_dist_20_pct >= -2.5:
        return "S1待突破"
    if breakout_dist_55_pct is not None and breakout_dist_55_pct >= -3.5:
        return "S2待突破"
    return "等待形态"


def _score_breakout(
    entry_signal_20: int,
    entry_signal_55: int,
    breakout_dist_20_pct: Optional[float],
    breakout_dist_55_pct: Optional[float],
    amount_ratio_20_120: Optional[float],
    path_state: Optional[str],
) -> tuple[float, list[str]]:
    score = 45.0
    reasons: list[str] = []

    if entry_signal_55:
        score += 24
        reasons.append("55日突破已触发")
    elif entry_signal_20:
        score += 16
        reasons.append("20日突破已触发")
    elif breakout_dist_20_pct is not None and breakout_dist_20_pct >= -2.5:
        score += 10
        reasons.append("接近20日突破位")
    elif breakout_dist_55_pct is not None and breakout_dist_55_pct >= -3.5:
        score += 8
        reasons.append("接近55日突破位")

    if amount_ratio_20_120 is not None:
        if amount_ratio_20_120 >= 1.5:
            score += 8
            reasons.append("量能明显放大")
        elif amount_ratio_20_120 >= 1.2:
            score += 4

    if path_state == "温和验证":
        score += 5
    elif path_state == "已充分演绎":
        score -= 8
    elif path_state == "失效破坏":
        score -= 18
        reasons.append("路径已失效")

    return _clamp_score(score), reasons


def _score_risk(
    atr_14_pct: Optional[float],
    amplitude_20d: Optional[float],
    _stock_gate: Optional[str],
    stage_score_v1: Optional[float],
) -> tuple[float, list[str]]:
    score = 55.0
    reasons: list[str] = []

    if atr_14_pct is not None:
        if atr_14_pct <= 2.5:
            score += 12
            reasons.append("ATR风险较低")
        elif atr_14_pct <= 4.0:
            score += 6
        elif atr_14_pct >= 7.0:
            score -= 15
            reasons.append("ATR波动偏大")
        elif atr_14_pct >= 5.5:
            score -= 8

    if amplitude_20d is not None:
        if amplitude_20d <= 18:
            score += 6
        elif amplitude_20d >= 35:
            score -= 12
            reasons.append("短期振幅偏大")
        elif amplitude_20d >= 28:
            score -= 6

    if stage_score_v1 is not None:
        if stage_score_v1 >= 75:
            score += 6
        elif stage_score_v1 < 45:
            score -= 10

    return _clamp_score(score), reasons


def build_stock_turtle_features(conn, mkt_conn, snapshot_date: Optional[str] = None) -> int:
    ensure_tables(conn)
    snapshot_date = snapshot_date or date.today().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()
    industry_map = load_industry_map(conn)

    stocks = conn.execute(
        """
        SELECT t.stock_code, t.stock_name, t.latest_notice_date, t.latest_report_date,
               s.path_state, s.stock_gate,
               s.amount_ratio_20_120, s.volatility_20d, s.amplitude_20d, s.stage_score_v1
        FROM mart_stock_trend t
        LEFT JOIN dim_stock_stage_latest s ON s.stock_code = t.stock_code
        WHERE t.stock_code IS NOT NULL
        ORDER BY t.stock_code
        """
    ).fetchall()

    conn.execute("DELETE FROM fact_stock_turtle_features WHERE snapshot_date = ?", (snapshot_date,))
    if not stocks:
        conn.execute("DELETE FROM dim_stock_turtle_latest")
        conn.commit()
        logger.info("[海龟特征] 无研究股票，跳过构建")
        return 0

    stock_rows = [dict(row) for row in stocks]
    for row in stock_rows:
        industry = industry_map.get(row["stock_code"]) or {}
        row["tdx_l1_name"] = industry.get("tdx_l1_name")
        row["tdx_l2_name"] = industry.get("tdx_l2_name")
    price_history = _load_price_history(mkt_conn, [row["stock_code"] for row in stock_rows])

    inserted = 0
    for row in stock_rows:
        code = row["stock_code"]
        history = price_history.get(code) or []
        latest_trade_date = history[-1]["date"] if history else None
        close_price = _round_price(history[-1].get("close") if history else None)

        atr_14 = None
        atr_14_pct = None
        entry_level_20 = None
        entry_level_55 = None
        exit_level_10 = None
        exit_level_20 = None
        breakout_dist_20_pct = None
        breakout_dist_55_pct = None
        exit_dist_10_pct = None
        exit_dist_20_pct = None
        stop_level_20_2n = None
        stop_level_55_2n = None
        add_level_20_1 = None
        add_level_20_2 = None
        add_level_20_3 = None
        add_level_55_1 = None
        add_level_55_2 = None
        add_level_55_3 = None
        entry_signal_20 = 0
        entry_signal_55 = 0
        exit_signal_10 = 0
        exit_signal_20 = 0

        if len(history) >= 21:
            df = pd.DataFrame(history)
            for col in ("open", "high", "low", "close", "volume", "amount"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            factors = compute_alpha_factors(df[["open", "high", "low", "close", "volume", "amount"]])
            atr_ratio = _safe_float(factors["ATR_14"].iloc[-1]) if "ATR_14" in factors.columns else None
            if atr_ratio is not None and close_price is not None:
                atr_14 = round(close_price * atr_ratio, 4)
                atr_14_pct = round(atr_ratio * 100, 2)

            entry_20_series = hhv(df["high"], 20).shift(1)
            exit_10_series = llv(df["low"], 10).shift(1)
            entry_level_20 = _round_price(entry_20_series.iloc[-1])
            exit_level_10 = _round_price(exit_10_series.iloc[-1])

            if len(history) >= 56:
                entry_55_series = hhv(df["high"], 55).shift(1)
                entry_level_55 = _round_price(entry_55_series.iloc[-1])
            if len(history) >= 21:
                exit_20_series = llv(df["low"], 20).shift(1)
                exit_level_20 = _round_price(exit_20_series.iloc[-1])

            breakout_dist_20_pct = _dist_pct(close_price, entry_level_20)
            breakout_dist_55_pct = _dist_pct(close_price, entry_level_55)
            exit_dist_10_pct = _dist_pct(close_price, exit_level_10)
            exit_dist_20_pct = _dist_pct(close_price, exit_level_20)

            entry_signal_20 = int(close_price is not None and entry_level_20 is not None and close_price >= entry_level_20)
            entry_signal_55 = int(close_price is not None and entry_level_55 is not None and close_price >= entry_level_55)
            exit_signal_10 = int(close_price is not None and exit_level_10 is not None and close_price <= exit_level_10)
            exit_signal_20 = int(close_price is not None and exit_level_20 is not None and close_price <= exit_level_20)

            if atr_14 is not None:
                if entry_level_20 is not None:
                    stop_level_20_2n = _round_price(entry_level_20 - 2 * atr_14)
                    add_level_20_1 = _round_price(entry_level_20 + 0.5 * atr_14)
                    add_level_20_2 = _round_price(entry_level_20 + 1.0 * atr_14)
                    add_level_20_3 = _round_price(entry_level_20 + 1.5 * atr_14)
                if entry_level_55 is not None:
                    stop_level_55_2n = _round_price(entry_level_55 - 2 * atr_14)
                    add_level_55_1 = _round_price(entry_level_55 + 0.5 * atr_14)
                    add_level_55_2 = _round_price(entry_level_55 + 1.0 * atr_14)
                    add_level_55_3 = _round_price(entry_level_55 + 1.5 * atr_14)

        preferred_system = _preferred_system(
            entry_signal_20,
            entry_signal_55,
            breakout_dist_20_pct,
            breakout_dist_55_pct,
        )
        turtle_setup_state = _build_setup_state(
            entry_signal_20,
            entry_signal_55,
            exit_signal_10,
            exit_signal_20,
            breakout_dist_20_pct,
            breakout_dist_55_pct,
        )

        amount_ratio_20_120 = _safe_float(row.get("amount_ratio_20_120"))
        volatility_20d = _safe_float(row.get("volatility_20d"))
        amplitude_20d = _safe_float(row.get("amplitude_20d"))
        stage_score_v1 = _safe_float(row.get("stage_score_v1"))

        breakout_score, breakout_reasons = _score_breakout(
            entry_signal_20,
            entry_signal_55,
            breakout_dist_20_pct,
            breakout_dist_55_pct,
            amount_ratio_20_120,
            row.get("path_state"),
        )
        risk_score, risk_reasons = _score_risk(
            atr_14_pct,
            amplitude_20d,
            row.get("stock_gate"),
            stage_score_v1,
        )
        turtle_execution_score_v1 = _clamp_score(
            breakout_score * 0.50
            + risk_score * 0.30
            + (stage_score_v1 if stage_score_v1 is not None else 50.0) * 0.20
        )

        reason_parts: list[str] = []
        for reason in breakout_reasons + risk_reasons:
            if reason and reason not in reason_parts:
                reason_parts.append(reason)
        if not reason_parts and latest_trade_date:
            reason_parts.append("海龟条件中性")
        if not latest_trade_date:
            reason_parts = ["缺少日线数据"]

        conn.execute(
            """
            INSERT OR REPLACE INTO fact_stock_turtle_features (
                snapshot_date, stock_code, stock_name, latest_trade_date,
                latest_notice_date, latest_report_date, tdx_l1_name, tdx_l2_name,
                path_state, stock_gate, close_price, atr_14, atr_14_pct,
                entry_level_20, entry_level_55, exit_level_10, exit_level_20,
                breakout_dist_20_pct, breakout_dist_55_pct, exit_dist_10_pct, exit_dist_20_pct,
                stop_level_20_2n, stop_level_55_2n,
                add_level_20_1, add_level_20_2, add_level_20_3,
                add_level_55_1, add_level_55_2, add_level_55_3,
                entry_signal_20, entry_signal_55, exit_signal_10, exit_signal_20,
                amount_ratio_20_120, volatility_20d, amplitude_20d,
                stage_score_v1,
                preferred_system, turtle_setup_state,
                turtle_breakout_score, turtle_risk_score, turtle_execution_score_v1,
                turtle_reason, schema_version, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_date,
                code,
                row.get("stock_name"),
                latest_trade_date,
                row.get("latest_notice_date"),
                row.get("latest_report_date"),
                row.get("tdx_l1_name"),
                row.get("tdx_l2_name"),
                row.get("path_state"),
                row.get("stock_gate"),
                close_price,
                atr_14,
                atr_14_pct,
                entry_level_20,
                entry_level_55,
                exit_level_10,
                exit_level_20,
                breakout_dist_20_pct,
                breakout_dist_55_pct,
                exit_dist_10_pct,
                exit_dist_20_pct,
                stop_level_20_2n,
                stop_level_55_2n,
                add_level_20_1,
                add_level_20_2,
                add_level_20_3,
                add_level_55_1,
                add_level_55_2,
                add_level_55_3,
                entry_signal_20,
                entry_signal_55,
                exit_signal_10,
                exit_signal_20,
                amount_ratio_20_120,
                volatility_20d,
                amplitude_20d,
                stage_score_v1,
                preferred_system,
                turtle_setup_state,
                breakout_score,
                risk_score,
                turtle_execution_score_v1,
                "；".join(reason_parts[:2]),
                TURTLE_FEATURE_SCHEMA_VERSION,
                now,
            ),
        )
        inserted += 1

    conn.execute("DELETE FROM dim_stock_turtle_latest")
    conn.execute(
        """
        INSERT INTO dim_stock_turtle_latest (
            stock_code, snapshot_date, stock_name, latest_trade_date,
            latest_notice_date, latest_report_date, tdx_l1_name, tdx_l2_name,
            path_state, stock_gate, close_price, atr_14, atr_14_pct,
            entry_level_20, entry_level_55, exit_level_10, exit_level_20,
            breakout_dist_20_pct, breakout_dist_55_pct, exit_dist_10_pct, exit_dist_20_pct,
            stop_level_20_2n, stop_level_55_2n,
            add_level_20_1, add_level_20_2, add_level_20_3,
            add_level_55_1, add_level_55_2, add_level_55_3,
            entry_signal_20, entry_signal_55, exit_signal_10, exit_signal_20,
            amount_ratio_20_120, volatility_20d, amplitude_20d,
            stage_score_v1,
            preferred_system, turtle_setup_state,
            turtle_breakout_score, turtle_risk_score, turtle_execution_score_v1,
            turtle_reason, schema_version, updated_at
        )
        SELECT stock_code, snapshot_date, stock_name, latest_trade_date,
               latest_notice_date, latest_report_date, tdx_l1_name, tdx_l2_name,
               path_state, stock_gate, close_price, atr_14, atr_14_pct,
               entry_level_20, entry_level_55, exit_level_10, exit_level_20,
               breakout_dist_20_pct, breakout_dist_55_pct, exit_dist_10_pct, exit_dist_20_pct,
               stop_level_20_2n, stop_level_55_2n,
               add_level_20_1, add_level_20_2, add_level_20_3,
               add_level_55_1, add_level_55_2, add_level_55_3,
               entry_signal_20, entry_signal_55, exit_signal_10, exit_signal_20,
               amount_ratio_20_120, volatility_20d, amplitude_20d,
               stage_score_v1,
               preferred_system, turtle_setup_state,
               turtle_breakout_score, turtle_risk_score, turtle_execution_score_v1,
               turtle_reason, schema_version, updated_at
        FROM fact_stock_turtle_features
        WHERE snapshot_date = ?
        """,
        (snapshot_date,),
    )
    conn.commit()
    logger.info(f"[海龟特征] 构建完成: {inserted} 只股票 · 快照 {snapshot_date}")
    return inserted


def ensure_tables(conn):
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS fact_stock_turtle_features (
            snapshot_date                 TEXT NOT NULL,
            stock_code                    TEXT NOT NULL,
            stock_name                    TEXT,
            latest_trade_date             TEXT,
            latest_notice_date            TEXT,
            latest_report_date            TEXT,
            tdx_l1_name                   TEXT,
            tdx_l2_name                   TEXT,
            path_state                    TEXT,
            stock_gate                    TEXT,
            close_price                   REAL,
            atr_14                        REAL,
            atr_14_pct                    REAL,
            entry_level_20                REAL,
            entry_level_55                REAL,
            exit_level_10                 REAL,
            exit_level_20                 REAL,
            breakout_dist_20_pct          REAL,
            breakout_dist_55_pct          REAL,
            exit_dist_10_pct              REAL,
            exit_dist_20_pct              REAL,
            stop_level_20_2n              REAL,
            stop_level_55_2n              REAL,
            add_level_20_1                REAL,
            add_level_20_2                REAL,
            add_level_20_3                REAL,
            add_level_55_1                REAL,
            add_level_55_2                REAL,
            add_level_55_3                REAL,
            entry_signal_20               INTEGER DEFAULT 0,
            entry_signal_55               INTEGER DEFAULT 0,
            exit_signal_10                INTEGER DEFAULT 0,
            exit_signal_20                INTEGER DEFAULT 0,
            amount_ratio_20_120           REAL,
            volatility_20d                REAL,
            amplitude_20d                 REAL,
            stage_score_v1                REAL,
            preferred_system              TEXT,
            turtle_setup_state            TEXT,
            turtle_breakout_score         REAL,
            turtle_risk_score             REAL,
            turtle_execution_score_v1     REAL,
            turtle_reason                 TEXT,
            schema_version                INTEGER NOT NULL DEFAULT {TURTLE_FEATURE_SCHEMA_VERSION},
            updated_at                    TEXT,
            PRIMARY KEY (snapshot_date, stock_code)
        );
        CREATE INDEX IF NOT EXISTS idx_fstf_stock ON fact_stock_turtle_features(stock_code);
        CREATE INDEX IF NOT EXISTS idx_fstf_state ON fact_stock_turtle_features(turtle_setup_state);
        CREATE INDEX IF NOT EXISTS idx_fstf_score ON fact_stock_turtle_features(turtle_execution_score_v1 DESC);

        CREATE TABLE IF NOT EXISTS dim_stock_turtle_latest (
            stock_code                    TEXT PRIMARY KEY,
            snapshot_date                 TEXT,
            stock_name                    TEXT,
            latest_trade_date             TEXT,
            latest_notice_date            TEXT,
            latest_report_date            TEXT,
            tdx_l1_name                   TEXT,
            tdx_l2_name                   TEXT,
            path_state                    TEXT,
            stock_gate                    TEXT,
            close_price                   REAL,
            atr_14                        REAL,
            atr_14_pct                    REAL,
            entry_level_20                REAL,
            entry_level_55                REAL,
            exit_level_10                 REAL,
            exit_level_20                 REAL,
            breakout_dist_20_pct          REAL,
            breakout_dist_55_pct          REAL,
            exit_dist_10_pct              REAL,
            exit_dist_20_pct              REAL,
            stop_level_20_2n              REAL,
            stop_level_55_2n              REAL,
            add_level_20_1                REAL,
            add_level_20_2                REAL,
            add_level_20_3                REAL,
            add_level_55_1                REAL,
            add_level_55_2                REAL,
            add_level_55_3                REAL,
            entry_signal_20               INTEGER DEFAULT 0,
            entry_signal_55               INTEGER DEFAULT 0,
            exit_signal_10                INTEGER DEFAULT 0,
            exit_signal_20                INTEGER DEFAULT 0,
            amount_ratio_20_120           REAL,
            volatility_20d                REAL,
            amplitude_20d                 REAL,
            stage_score_v1                REAL,
            preferred_system              TEXT,
            turtle_setup_state            TEXT,
            turtle_breakout_score         REAL,
            turtle_risk_score             REAL,
            turtle_execution_score_v1     REAL,
            turtle_reason                 TEXT,
            schema_version                INTEGER NOT NULL DEFAULT {TURTLE_FEATURE_SCHEMA_VERSION},
            updated_at                    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dstl_state ON dim_stock_turtle_latest(turtle_setup_state);
        CREATE INDEX IF NOT EXISTS idx_dstl_score ON dim_stock_turtle_latest(turtle_execution_score_v1 DESC);
        """
    )
    conn.commit()
    logger.info("[海龟特征] 已确保 fact_stock_turtle_features / dim_stock_turtle_latest 表结构")
