"""
qlib_full_engine.py — Qlib AI 多因子引擎

基于 pyqlib 0.9.7，使用 Alpha158/轻量 OHLCV + 自定义因子（财务 + 机构）+ LGBModel 标准训练管线。
查询 API（get_model_status/get_predictions/get_factor_importance）不依赖 pyqlib，
仅训练功能需要 pyqlib 安装。
"""

import json
import logging
import re
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from services.industry import industry_level_expr

logger = logging.getLogger("cm-api")

_QLIB_AVAILABLE = True
_QLIB_ERROR = None
try:
    import qlib
    from qlib.config import REG_CN
except ImportError as e:
    _QLIB_AVAILABLE = False
    _QLIB_ERROR = str(e)

_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "qlib_models"
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "qlib_data"
_RUNS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "qlib_runs"

_LEGACY_DEFAULT_TRAINING_SEGMENTS = {
    "train": ("2023-01-01", "2025-03-31"),
    "valid": ("2025-04-01", "2025-09-30"),
    "test": ("2025-10-01", "2026-01-31"),
}
_LIGHTWEIGHT_OHLCV_FEATURE_CONFIG = {
    "kbar": {},
    "price": {
        "windows": [0, 1],
        "feature": ["OPEN", "HIGH", "LOW", "VWAP"],
    },
    "volume": {
        "windows": [0, 1, 5],
    },
    "rolling": {
        "windows": [5, 10, 20],
        "include": ["ROC", "MA", "STD"],
    },
}
_LIGHTGBM_COLUMN_NAME_RE = re.compile(r"^Column_(\d+)$")
_QLIB_LABEL_CONFIG = (
    ["Ref($close, -2)/Ref($close, -1) - 1"],
    ["LABEL0"],
)
_QLIB_LEARN_PROCESSORS = [
    {"class": "DropnaLabel"},
    {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}},
]


def is_available() -> tuple[bool, Optional[str]]:
    return _QLIB_AVAILABLE, _QLIB_ERROR


def _safe_round(value, digits: int = 4):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except Exception:
        return None


_TDX_L1_TO_ETF_CATEGORY = [
    (("医药", "医疗", "生物"), "医疗健康"),
    (("半导体", "芯片"), "半导体"),
    (("电力设备", "新能源", "光伏", "风电", "电池"), "新能源"),
    (("食品饮料", "家用电器", "商贸零售", "社会服务", "美容护理", "纺织服饰", "轻工制造"), "消费"),
    (("银行", "非银金融", "金融"), "金融"),
    (("国防军工",), "军工"),
    (("房地产", "建筑装饰", "建筑材料"), "地产建筑"),
    (("基础化工", "石油石化", "有色金属", "钢铁", "煤炭", "环保", "农林牧渔"), "周期资源"),
    (("计算机", "通信", "传媒", "电子", "互联网"), "数字科技"),
    (("交通运输", "物流"), "交通物流"),
    (("公用事业",), "电力公用"),
    (("汽车",), "汽车"),
    (("机械设备",), "高端制造"),
]


def _map_tdx_l1_to_etf_category(tdx_l1: Optional[str]) -> Optional[str]:
    if not tdx_l1:
        return None
    text = str(tdx_l1).strip()
    if not text:
        return None
    for aliases, category in _TDX_L1_TO_ETF_CATEGORY:
        if any(alias in text for alias in aliases):
            return category
    return None


# ============================================================
# Schema
# ============================================================

def ensure_tables(conn):
    """创建完整 Qlib 专属表"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS qlib_model_state (
            model_id        TEXT PRIMARY KEY,
            status          TEXT NOT NULL DEFAULT 'idle',
            train_start     TEXT,
            train_end       TEXT,
            valid_start     TEXT,
            valid_end       TEXT,
            test_start      TEXT,
            test_end        TEXT,
            stock_count     INTEGER,
            factor_count    INTEGER,
            ic_mean         REAL,
            rank_ic_mean    REAL,
            test_top50_avg_return REAL,
            error           TEXT,
            model_path      TEXT,
            train_params_json TEXT,
            created_at      TEXT,
            finished_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS qlib_predictions (
            model_id        TEXT NOT NULL,
            stock_code      TEXT NOT NULL,
            stock_name      TEXT,
            predict_date    TEXT,
            qlib_score      REAL,
            qlib_rank       INTEGER,
            qlib_percentile REAL,
            PRIMARY KEY (model_id, stock_code)
        );
        CREATE INDEX IF NOT EXISTS idx_qp_rank ON qlib_predictions(model_id, qlib_rank);

        CREATE TABLE IF NOT EXISTS qlib_factor_importance (
            model_id        TEXT NOT NULL,
            factor_name     TEXT NOT NULL,
            importance      REAL,
            factor_group    TEXT,
            PRIMARY KEY (model_id, factor_name)
        );

        CREATE TABLE IF NOT EXISTS qlib_data_state (
            data_dir        TEXT PRIMARY KEY,
            last_dump_date  TEXT,
            stock_count     INTEGER,
            trading_days    INTEGER,
            min_date        TEXT,
            max_date        TEXT,
            format_version  TEXT DEFAULT 'v1',
            updated_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS qlib_backtest_result (
            model_id        TEXT NOT NULL,
            backtest_id     TEXT NOT NULL,
            strategy        TEXT,
            sharpe_ratio    REAL,
            calmar_ratio    REAL,
            max_drawdown    REAL,
            annual_return   REAL,
            turnover        REAL,
            detail_json     TEXT,
            created_at      TEXT,
            PRIMARY KEY (model_id, backtest_id)
        );
    """)
    for ddl in [
        "ALTER TABLE qlib_model_state ADD COLUMN train_params_json TEXT",
        "ALTER TABLE qlib_model_state ADD COLUMN backtest_status TEXT",
        "ALTER TABLE qlib_model_state ADD COLUMN backtest_benchmark TEXT",
        "ALTER TABLE qlib_model_state ADD COLUMN backtest_error TEXT",
        "ALTER TABLE qlib_model_state ADD COLUMN is_active INTEGER DEFAULT 0",
        "ALTER TABLE qlib_model_state ADD COLUMN performance_rank REAL",
    ]:
        try:
            conn.execute(ddl)
        except Exception:
            pass
    conn.commit()


# ============================================================
# 初始化
# ============================================================

def init_qlib(data_dir: str = None) -> bool:
    """初始化 pyqlib 环境"""
    if not _QLIB_AVAILABLE:
        logger.warning(f"[Qlib-Full] pyqlib 不可用: {_QLIB_ERROR}")
        return False

    data_path = data_dir or str(_DATA_DIR)
    try:
        qlib.init(provider_uri=data_path, region=REG_CN)
        logger.info(f"[Qlib-Full] 初始化成功: {data_path}")
        return True
    except Exception as e:
        logger.error(f"[Qlib-Full] 初始化失败: {e}")
        return False


# ============================================================
# 自定义因子（财务 + 机构）
# ============================================================

def _normalize_factor_date(value) -> Optional[pd.Timestamp]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    else:
        parsed = pd.to_datetime(text, errors="coerce")
    if parsed is None or pd.isna(parsed):
        return None
    return parsed.normalize()


def _date_text(value) -> Optional[str]:
    parsed = _normalize_factor_date(value)
    if parsed is None:
        return None
    return parsed.strftime("%Y-%m-%d")


def _next_date_text(value) -> Optional[str]:
    parsed = _normalize_factor_date(value)
    if parsed is None:
        return None
    return (parsed + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def _qlib_calendar_path(data_dir: str = None) -> Path:
    return Path(data_dir or str(_DATA_DIR)) / "calendars" / "day.txt"


def _load_trading_calendar(data_dir: str = None) -> list[pd.Timestamp]:
    calendar_path = _qlib_calendar_path(data_dir)
    if not calendar_path.exists():
        return []
    dates: list[pd.Timestamp] = []
    for raw_line in calendar_path.read_text(encoding="utf-8").splitlines():
        parsed = _normalize_factor_date(raw_line)
        if parsed is not None:
            dates.append(parsed)
    if not dates:
        return []
    return sorted(dict.fromkeys(dates))


def _resolve_default_training_segments(data_dir: str = None) -> dict[str, tuple[str, str]]:
    calendar_dates = _load_trading_calendar(data_dir)
    if len(calendar_dates) < 12:
        return dict(_LEGACY_DEFAULT_TRAINING_SEGMENTS)
    date_texts = [item.strftime("%Y-%m-%d") for item in calendar_dates]
    train_idx = max(int(len(date_texts) * 0.70), 1)
    valid_idx = max(int(len(date_texts) * 0.85), train_idx + 1)
    valid_idx = min(valid_idx, len(date_texts) - 2)
    return {
        "train": (date_texts[0], date_texts[train_idx - 1]),
        "valid": (date_texts[train_idx], date_texts[valid_idx - 1]),
        "test": (date_texts[valid_idx], date_texts[-1]),
    }


def _next_trading_day_text(value, data_dir: str = None) -> Optional[str]:
    parsed = _normalize_factor_date(value)
    if parsed is None:
        return None
    for item in _load_trading_calendar(data_dir):
        if item > parsed:
            return item.strftime("%Y-%m-%d")
    return _next_date_text(parsed)


def get_training_date_range(data_dir: str = None) -> dict:
    calendar_dates = _load_trading_calendar(data_dir)
    segments = _resolve_default_training_segments(data_dir)
    return {
        "calendar_start": calendar_dates[0].strftime("%Y-%m-%d") if calendar_dates else segments["train"][0],
        "calendar_end": calendar_dates[-1].strftime("%Y-%m-%d") if calendar_dates else segments["test"][1],
        "trading_days": len(calendar_dates),
        "source": "calendar" if calendar_dates else "fallback",
        "train_start": segments["train"][0],
        "train_end": segments["train"][1],
        "valid_start": segments["valid"][0],
        "valid_end": segments["valid"][1],
        "test_start": segments["test"][0],
        "test_end": segments["test"][1],
    }


def _instrument_from_stock_code(stock_code: str) -> str:
    code = str(stock_code or "").strip()
    if not code:
        return ""
    prefix = "SH" if code.startswith("6") else "SZ"
    return f"{prefix}{code}"


def _finalize_time_series_factor_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    factor_df = pd.DataFrame(rows)
    if factor_df.empty:
        return pd.DataFrame()
    factor_df["datetime"] = pd.to_datetime(factor_df["datetime"], errors="coerce")
    factor_df = factor_df.dropna(subset=["datetime", "instrument"])
    factor_df = factor_df.sort_values(["instrument", "datetime"])
    factor_df = factor_df.drop_duplicates(subset=["datetime", "instrument"], keep="last")
    return factor_df.set_index(["datetime", "instrument"]).sort_index()

def _load_financial_factors(smart_conn, codes: list) -> pd.DataFrame:
    """按公告可见日加载财务因子，避免 latest 快照向历史广播。"""
    if not codes:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in codes)
    rows = smart_conn.execute(
        f"SELECT f.stock_code, f.report_date, "
        f"       COALESCE(NULLIF(r.notice_date, ''), REPLACE(f.report_date, '-', '')) AS available_date, "
        f"       f.roe, f.debt_ratio, f.current_ratio, f.gross_margin, "
        f"       f.net_margin, f.revenue_yoy, f.profit_yoy, f.ocf_to_profit "
        f"FROM fact_financial_derived f "
        f"LEFT JOIN raw_gpcw_financial r "
        f"  ON r.stock_code = f.stock_code "
        f" AND r.report_date = REPLACE(f.report_date, '-', '') "
        f"WHERE f.stock_code IN ({placeholders}) "
        f"ORDER BY f.stock_code, available_date, f.report_date",
        codes
    ).fetchall()

    if not rows:
        return pd.DataFrame()

    data = []
    for r in rows:
        available_at = _normalize_factor_date(r["available_date"])
        instrument = _instrument_from_stock_code(r["stock_code"])
        if available_at is None or not instrument:
            continue
        data.append({
            "datetime": available_at,
            "instrument": instrument,
            "fin_roe": r["roe"],
            "fin_debt_ratio": r["debt_ratio"],
            "fin_current_ratio": r["current_ratio"],
            "fin_gross_margin": r["gross_margin"],
            "fin_net_margin": r["net_margin"],
            "fin_revenue_yoy": r["revenue_yoy"],
            "fin_profit_yoy": r["profit_yoy"],
            "fin_ocf_to_profit": r["ocf_to_profit"],
        })

    return _finalize_time_series_factor_frame(data)


def _load_institution_factors(smart_conn, codes: list) -> pd.DataFrame:
    """按披露日聚合机构持仓因子，避免使用当前 mart 快照回填历史。"""
    if not codes:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in codes)
    rows = smart_conn.execute(
        f"SELECT stock_code, "
        f"       COALESCE(NULLIF(notice_date, ''), report_date) AS available_date, "
        f"       COUNT(DISTINCT holder_name) AS inst_count_t0, "
        f"       SUM(COALESCE(hold_ratio, 0)) AS inst_hold_ratio, "
        f"       SUM(COALESCE(hold_market_cap, 0)) AS inst_hold_market_cap "
        f"FROM market_raw_holdings "
        f"WHERE stock_code IN ({placeholders}) "
        f"  AND COALESCE(holder_name, '') != '' "
        f"GROUP BY stock_code, available_date "
        f"ORDER BY stock_code, available_date",
        codes
    ).fetchall()

    if not rows:
        return pd.DataFrame()

    history_df = pd.DataFrame([dict(r) for r in rows])
    history_df["datetime"] = history_df["available_date"].map(_normalize_factor_date)
    history_df = history_df.dropna(subset=["datetime"]).sort_values(["stock_code", "datetime"])
    history_df["inst_count_t1"] = history_df.groupby("stock_code")["inst_count_t0"].shift(1)
    history_df["inst_count_t2"] = history_df.groupby("stock_code")["inst_count_t0"].shift(2)
    history_df["inst_hold_ratio_t1"] = history_df.groupby("stock_code")["inst_hold_ratio"].shift(1)
    history_df["inst_hold_market_cap_t1"] = history_df.groupby("stock_code")["inst_hold_market_cap"].shift(1)

    data = []
    for row in history_df.to_dict("records"):
        current_count = row.get("inst_count_t0")
        previous_count = row.get("inst_count_t1")
        trend = 0
        if previous_count is not None and current_count is not None:
            if current_count > previous_count:
                trend = 1
            elif current_count < previous_count:
                trend = -1
        current_ratio = row.get("inst_hold_ratio")
        previous_ratio = row.get("inst_hold_ratio_t1")
        current_cap = row.get("inst_hold_market_cap")
        previous_cap = row.get("inst_hold_market_cap_t1")
        data.append({
            "datetime": row["datetime"],
            "instrument": _instrument_from_stock_code(row["stock_code"]),
            "inst_count_t0": current_count,
            "inst_count_t1": previous_count,
            "inst_count_t2": row.get("inst_count_t2"),
            "inst_trend": trend,
            "inst_hold_ratio": current_ratio,
            "inst_hold_ratio_change": (
                current_ratio - previous_ratio
                if current_ratio is not None and previous_ratio is not None
                else None
            ),
            "inst_hold_market_cap": current_cap,
            "inst_hold_market_cap_change": (
                current_cap - previous_cap
                if current_cap is not None and previous_cap is not None
                else None
            ),
        })

    return _finalize_time_series_factor_frame(data)


def _load_turtle_factors(smart_conn, codes: list) -> pd.DataFrame:
    """按 snapshot_date 加载海龟因子，避免 latest 快照向历史广播。"""
    if not codes:
        return pd.DataFrame()
    try:
        placeholders = ",".join("?" for _ in codes)
        rows = smart_conn.execute(
            f"SELECT snapshot_date, stock_code, atr_14_pct, breakout_dist_20_pct, breakout_dist_55_pct, "
            f"entry_signal_20, entry_signal_55, exit_signal_10, exit_signal_20, "
            f"turtle_breakout_score, turtle_risk_score, turtle_execution_score_v1, "
            f"preferred_system, turtle_setup_state "
            f"FROM fact_stock_turtle_features WHERE stock_code IN ({placeholders}) "
            f"ORDER BY stock_code, snapshot_date",
            codes,
        ).fetchall()
    except Exception:
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    breakout_states = {"S1突破触发", "S2突破触发"}
    watch_states = {"S1待突破", "S2待突破"}
    exit_states = {"10日退出触发", "20日退出触发"}
    data = []
    for r in rows:
        snapshot_at = _normalize_factor_date(r["snapshot_date"])
        instrument = _instrument_from_stock_code(r["stock_code"])
        if snapshot_at is None or not instrument:
            continue
        preferred_system = str(r["preferred_system"] or "").strip()
        turtle_state = str(r["turtle_setup_state"] or "").strip()
        data.append({
            "datetime": snapshot_at,
            "instrument": instrument,
            "turtle_atr_14_pct": r["atr_14_pct"],
            "turtle_breakout_dist_20_pct": r["breakout_dist_20_pct"],
            "turtle_breakout_dist_55_pct": r["breakout_dist_55_pct"],
            "turtle_entry_signal_20": r["entry_signal_20"],
            "turtle_entry_signal_55": r["entry_signal_55"],
            "turtle_exit_signal_10": r["exit_signal_10"],
            "turtle_exit_signal_20": r["exit_signal_20"],
            "turtle_breakout_score": r["turtle_breakout_score"],
            "turtle_risk_score": r["turtle_risk_score"],
            "turtle_execution_score": r["turtle_execution_score_v1"],
            "turtle_system_s1": 1 if preferred_system == "S1" else 0,
            "turtle_system_s2": 1 if preferred_system == "S2" else 0,
            "turtle_state_breakout": 1 if turtle_state in breakout_states else 0,
            "turtle_state_watch": 1 if turtle_state in watch_states else 0,
            "turtle_state_exit": 1 if turtle_state in exit_states else 0,
        })

    return _finalize_time_series_factor_frame(data)


def _load_quality_factors(smart_conn, codes: list) -> pd.DataFrame:
    """按 snapshot_date 加载质量因子，避免 latest 快照向历史广播。"""
    if not codes:
        return pd.DataFrame()
    try:
        placeholders = ",".join("?" for _ in codes)
        rows = smart_conn.execute(
            f"SELECT snapshot_date, stock_code, roe, roa_ak, gross_margin, ocf_to_profit, "
            f"       debt_ratio, current_ratio, contract_to_revenue, revenue_growth_yoy_ak, "
            f"       net_profit_growth_yoy_ak, quality_profit_raw, quality_cash_raw, "
            f"       quality_balance_raw, quality_margin_raw, quality_contract_raw, "
            f"       quality_freshness_raw, quality_capital_raw, quality_efficiency_raw, "
            f"       quality_growth_raw, quality_score_v1 "
            f"FROM fact_stock_quality_features WHERE stock_code IN ({placeholders}) "
            f"ORDER BY stock_code, snapshot_date",
            codes,
        ).fetchall()
    except Exception:
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    data = []
    for row in rows:
        snapshot_at = _normalize_factor_date(row["snapshot_date"])
        instrument = _instrument_from_stock_code(row["stock_code"])
        if snapshot_at is None or not instrument:
            continue
        data.append(
            {
                "datetime": snapshot_at,
                "instrument": instrument,
                "qual_roe": row["roe"],
                "qual_roa": row["roa_ak"],
                "qual_gross_margin": row["gross_margin"],
                "qual_ocf_to_profit": row["ocf_to_profit"],
                "qual_debt_ratio": row["debt_ratio"],
                "qual_current_ratio": row["current_ratio"],
                "qual_contract_to_revenue": row["contract_to_revenue"],
                "qual_revenue_growth_yoy": row["revenue_growth_yoy_ak"],
                "qual_profit_growth_yoy": row["net_profit_growth_yoy_ak"],
                "qual_profit_raw": row["quality_profit_raw"],
                "qual_cash_raw": row["quality_cash_raw"],
                "qual_balance_raw": row["quality_balance_raw"],
                "qual_margin_raw": row["quality_margin_raw"],
                "qual_contract_raw": row["quality_contract_raw"],
                "qual_freshness_raw": row["quality_freshness_raw"],
                "qual_capital_raw": row["quality_capital_raw"],
                "qual_efficiency_raw": row["quality_efficiency_raw"],
                "qual_growth_raw": row["quality_growth_raw"],
                "qual_score_v1": row["quality_score_v1"],
            }
        )

    return _finalize_time_series_factor_frame(data)


def _load_stage_factors(smart_conn, codes: list) -> pd.DataFrame:
    """按 snapshot_date 加载阶段因子，避免 latest 快照向历史广播。"""
    if not codes:
        return pd.DataFrame()
    try:
        placeholders = ",".join("?" for _ in codes)
        rows = smart_conn.execute(
            f"SELECT snapshot_date, stock_code, path_state, stock_gate, return_1m, return_3m, return_6m, "
            f"       return_12m, dist_ma120_pct, dist_ma250_pct, above_ma250, max_drawdown_60d, "
            f"       amount_ratio_20_120, volatility_20d, amplitude_20d, generic_stage_raw, "
            f"       stage_type_adjust_raw, stage_score_v1 "
            f"FROM fact_stock_stage_features WHERE stock_code IN ({placeholders}) "
            f"ORDER BY stock_code, snapshot_date",
            codes,
        ).fetchall()
    except Exception:
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    data = []
    for row in rows:
        snapshot_at = _normalize_factor_date(row["snapshot_date"])
        instrument = _instrument_from_stock_code(row["stock_code"])
        if snapshot_at is None or not instrument:
            continue
        path_state = str(row["path_state"] or "").strip()
        stock_gate = str(row["stock_gate"] or "").strip()
        data.append(
            {
                "datetime": snapshot_at,
                "instrument": instrument,
                "stage_return_1m": row["return_1m"],
                "stage_return_3m": row["return_3m"],
                "stage_return_6m": row["return_6m"],
                "stage_return_12m": row["return_12m"],
                "stage_dist_ma120_pct": row["dist_ma120_pct"],
                "stage_dist_ma250_pct": row["dist_ma250_pct"],
                "stage_above_ma250": row["above_ma250"],
                "stage_max_drawdown_60d": row["max_drawdown_60d"],
                "stage_amount_ratio_20_120": row["amount_ratio_20_120"],
                "stage_volatility_20d": row["volatility_20d"],
                "stage_amplitude_20d": row["amplitude_20d"],
                "stage_generic_raw": row["generic_stage_raw"],
                "stage_type_adjust_raw": row["stage_type_adjust_raw"],
                "stage_score_v1": row["stage_score_v1"],
                "stage_path_mild": 1 if path_state == "温和验证" else 0,
                "stage_path_exhausted": 1 if path_state == "已充分演绎" else 0,
                "stage_path_broken": 1 if path_state == "失效破坏" else 0,
                "stage_gate_follow": 1 if stock_gate == "follow" else 0,
                "stage_gate_watch": 1 if stock_gate == "watch" else 0,
                "stage_gate_avoid": 1 if stock_gate == "avoid" else 0,
            }
        )

    return _finalize_time_series_factor_frame(data)


def _load_northbound_factors(smart_conn, codes: list) -> pd.DataFrame:
    """按 trade_date 加载北向持仓因子。"""
    if not codes:
        return pd.DataFrame()
    try:
        placeholders = ",".join("?" for _ in codes)
        rows = smart_conn.execute(
            f"SELECT stock_code, trade_date, hold_shares, hold_market_cap, hold_ratio, change_shares "
            f"FROM fact_northbound_daily WHERE stock_code IN ({placeholders}) "
            f"ORDER BY stock_code, trade_date",
            codes,
        ).fetchall()
    except Exception:
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    history_df = pd.DataFrame([dict(row) for row in rows])
    history_df["datetime"] = history_df["trade_date"].map(_normalize_factor_date)
    history_df = history_df.dropna(subset=["datetime"]).sort_values(["stock_code", "datetime"])
    history_df["hold_ratio_prev"] = history_df.groupby("stock_code")["hold_ratio"].shift(1)
    history_df["hold_shares_prev"] = history_df.groupby("stock_code")["hold_shares"].shift(1)
    history_df["hold_market_cap_prev"] = history_df.groupby("stock_code")["hold_market_cap"].shift(1)

    data = []
    for row in history_df.to_dict("records"):
        hold_ratio = row.get("hold_ratio")
        hold_shares = row.get("hold_shares")
        hold_market_cap = row.get("hold_market_cap")
        change_shares = row.get("change_shares")
        data.append(
            {
                "datetime": row["datetime"],
                "instrument": _instrument_from_stock_code(row["stock_code"]),
                "nb_hold_shares": hold_shares,
                "nb_hold_market_cap": hold_market_cap,
                "nb_hold_ratio": hold_ratio,
                "nb_change_shares": change_shares,
                "nb_hold_ratio_change": (
                    hold_ratio - row.get("hold_ratio_prev")
                    if hold_ratio is not None and row.get("hold_ratio_prev") is not None
                    else None
                ),
                "nb_hold_shares_change": (
                    hold_shares - row.get("hold_shares_prev")
                    if hold_shares is not None and row.get("hold_shares_prev") is not None
                    else None
                ),
                "nb_hold_market_cap_change": (
                    hold_market_cap - row.get("hold_market_cap_prev")
                    if hold_market_cap is not None and row.get("hold_market_cap_prev") is not None
                    else None
                ),
                "nb_net_inflow_flag": 1 if (change_shares or 0) > 0 else -1 if (change_shares or 0) < 0 else 0,
            }
        )

    return _finalize_time_series_factor_frame(data)


def _load_combined_custom_factors(
    smart_conn,
    codes: list[str],
    *,
    use_financial: bool,
    use_institution: bool,
    use_turtle: bool,
    use_quality: bool,
    use_stage: bool,
    use_northbound: bool,
) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame()
    factor_frames = []
    if use_financial:
        fin_factors = _load_financial_factors(smart_conn, codes)
        if not fin_factors.empty:
            factor_frames.append(fin_factors)
    if use_institution:
        inst_factors = _load_institution_factors(smart_conn, codes)
        if not inst_factors.empty:
            factor_frames.append(inst_factors)
    if use_turtle:
        turtle_factors = _load_turtle_factors(smart_conn, codes)
        if not turtle_factors.empty:
            factor_frames.append(turtle_factors)
    if use_quality:
        quality_factors = _load_quality_factors(smart_conn, codes)
        if not quality_factors.empty:
            factor_frames.append(quality_factors)
    if use_stage:
        stage_factors = _load_stage_factors(smart_conn, codes)
        if not stage_factors.empty:
            factor_frames.append(stage_factors)
    if use_northbound:
        northbound_factors = _load_northbound_factors(smart_conn, codes)
        if not northbound_factors.empty:
            factor_frames.append(northbound_factors)
    if not factor_frames:
        return pd.DataFrame()

    custom_factors = factor_frames[0]
    for frame in factor_frames[1:]:
        custom_factors = custom_factors.join(frame, how="outer")
    return custom_factors


def _inject_custom_factors_into_handler(handler, custom_factors: pd.DataFrame) -> int:
    custom_factor_count = len(custom_factors.columns) if not custom_factors.empty else 0
    if custom_factors.empty:
        return 0
    try:
        existing_df = _ensure_handler_feature_frame(handler)
        if existing_df is None or existing_df.empty:
            logger.warning("[Qlib-Full] Alpha158 handler._data 为空，跳过因子注入")
            return 0
        if not isinstance(existing_df.index, pd.MultiIndex):
            logger.warning("[Qlib-Full] Alpha158 handler._data 缺少 MultiIndex，跳过因子注入")
            return 0
        if isinstance(custom_factors.index, pd.MultiIndex):
            custom_matched = custom_factors.copy()
            if set(custom_matched.index.names) == set(existing_df.index.names):
                custom_matched = custom_matched.reorder_levels(existing_df.index.names)
            custom_matched = custom_matched.sort_index()
            aligned = pd.DataFrame(index=existing_df.index).join(custom_matched, how="left")
            if "instrument" in existing_df.index.names:
                aligned = aligned.groupby(level="instrument", sort=False).ffill()
        else:
            instruments_in_data = existing_df.index.get_level_values("instrument").unique()
            custom_matched = custom_factors.reindex(instruments_in_data)
            aligned = pd.DataFrame(index=existing_df.index)
            for col in custom_matched.columns:
                instrument_values = custom_matched[col]
                aligned[col] = existing_df.index.get_level_values("instrument").map(
                    instrument_values.to_dict()
                ).values
        use_feature_column_group = (
            isinstance(existing_df.columns, pd.MultiIndex)
            and existing_df.columns.nlevels >= 2
            and "feature" in existing_df.columns.get_level_values(0)
        )
        updated_df = existing_df.copy()
        for col in aligned.columns:
            target_col = ("feature", col) if use_feature_column_group else col
            updated_df[target_col] = aligned[col].values

        # Alpha158/DataHandlerLP 会缓存 raw/infer/learn 三套视图。
        # 仅修改 raw 会导致 fit() 继续使用旧 learn 缓存，而 predict() 读到新的 infer 视图，
        # 最终触发 LightGBM 的特征维度不一致错误。
        original_raw = getattr(handler, "_data", None)
        handler._data = updated_df
        process_data_fn = getattr(handler, "process_data", None)
        if callable(process_data_fn):
            try:
                process_data_fn(with_fit=False)
            except TypeError:
                process_data_fn()
        covered_rows = int(aligned.notna().any(axis=1).sum()) if not aligned.empty else 0
        logger.info(
            f"[Qlib-Full] 已注入 {custom_factor_count} 个自定义因子到 Alpha158 数据集，覆盖 {covered_rows} 条样本"
        )
    except Exception as e:
        if "original_raw" in locals():
            handler._data = original_raw
        logger.warning(f"[Qlib-Full] 自定义因子注入失败（回退到纯 Alpha158）: {e}")
        return 0
    return custom_factor_count


def _ensure_handler_feature_frame(handler) -> Optional[pd.DataFrame]:
    existing_df = getattr(handler, "_data", None)
    if isinstance(existing_df, pd.DataFrame) and not existing_df.empty:
        return existing_df

    fetched_df = None
    fetch_fn = getattr(handler, "fetch", None)
    if callable(fetch_fn):
        try:
            fetched_df = fetch_fn(col_set="feature")
        except TypeError:
            fetched_df = fetch_fn()
        except Exception:
            fetched_df = None
    if isinstance(fetched_df, pd.DataFrame) and not fetched_df.empty:
        handler._data = fetched_df.copy()
        return handler._data

    setup_fn = getattr(handler, "setup_data", None)
    if callable(setup_fn):
        try:
            setup_fn()
        except Exception:
            return None
        existing_df = getattr(handler, "_data", None)
        if isinstance(existing_df, pd.DataFrame) and not existing_df.empty:
            return existing_df
    return None


def _extract_handler_feature_names(handler) -> list[str]:
    feature_frame = _ensure_handler_feature_frame(handler)
    if feature_frame is None or feature_frame.empty:
        return []
    columns = feature_frame.columns
    if isinstance(columns, pd.MultiIndex):
        if "feature" in columns.get_level_values(0):
            return [str(column[-1]) for column in columns if column[0] == "feature"]
        return [str(column[-1]) for column in columns]
    return [str(column) for column in columns]


def _resolve_feature_name_aliases(feature_names: list[str], feature_name_candidates: Optional[list[str]] = None) -> list[str]:
    if not feature_names or not feature_name_candidates:
        return feature_names
    resolved = []
    for name in feature_names:
        match = _LIGHTGBM_COLUMN_NAME_RE.match(str(name))
        if not match:
            resolved.append(str(name))
            continue
        index = int(match.group(1))
        if 0 <= index < len(feature_name_candidates):
            resolved.append(str(feature_name_candidates[index]))
        else:
            resolved.append(str(name))
    return resolved


def _compute_model_performance_rank(model: dict, backtest_row: Optional[dict]) -> Optional[float]:
    components = []

    rank_ic_mean = _safe_round(model.get("rank_ic_mean"))
    if rank_ic_mean is not None:
        components.append(rank_ic_mean * 120.0)

    ic_mean = _safe_round(model.get("ic_mean"))
    if ic_mean is not None:
        components.append(ic_mean * 80.0)

    test_top50_avg_return = _safe_round(model.get("test_top50_avg_return"))
    if test_top50_avg_return is not None:
        components.append(test_top50_avg_return * 3.0)

    if backtest_row:
        sharpe_ratio = _safe_round(backtest_row.get("sharpe_ratio"))
        if sharpe_ratio is not None:
            components.append(sharpe_ratio * 8.0)

        annual_return = _safe_round(backtest_row.get("annual_return"))
        if annual_return is not None:
            components.append(annual_return * 2.0)

        max_drawdown = _safe_round(backtest_row.get("max_drawdown"))
        if max_drawdown is not None:
            components.append(-abs(max_drawdown) * 4.0)

    if not components:
        return None
    return round(sum(components), 4)


def _refresh_active_model(conn) -> Optional[str]:
    ensure_tables(conn)
    model_rows = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM qlib_model_state WHERE status = 'trained' ORDER BY created_at DESC"
        ).fetchall()
    ]
    if not model_rows:
        conn.execute("UPDATE qlib_model_state SET is_active = 0, performance_rank = NULL")
        conn.commit()
        return None

    latest_backtests = {}
    for row in conn.execute(
        "SELECT model_id, sharpe_ratio, annual_return, max_drawdown, created_at FROM qlib_backtest_result ORDER BY created_at DESC"
    ).fetchall():
        latest_backtests.setdefault(row["model_id"], dict(row))

    ranked_rows = []
    for model in model_rows:
        performance_rank = _compute_model_performance_rank(model, latest_backtests.get(model["model_id"]))
        ranked_rows.append((model, performance_rank))

    best_model, _ = max(
        ranked_rows,
        key=lambda item: (
            item[1] is not None,
            item[1] if item[1] is not None else float("-inf"),
            str(item[0].get("created_at") or ""),
        ),
    )
    best_model_id = best_model["model_id"]

    conn.execute("UPDATE qlib_model_state SET is_active = 0 WHERE is_active != 0")
    for model, performance_rank in ranked_rows:
        conn.execute(
            "UPDATE qlib_model_state SET is_active = ?, performance_rank = ? WHERE model_id = ?",
            (1 if model["model_id"] == best_model_id else 0, performance_rank, model["model_id"]),
        )
    conn.commit()
    return best_model_id


def get_default_model_id(conn, model_id: Optional[str] = None) -> Optional[str]:
    ensure_tables(conn)
    if model_id:
        row = conn.execute(
            "SELECT model_id FROM qlib_model_state WHERE model_id = ? AND status = 'trained'",
            (model_id,),
        ).fetchone()
        return row["model_id"] if row else None

    row = conn.execute(
        "SELECT model_id FROM qlib_model_state WHERE status = 'trained' AND is_active = 1 ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row:
        return row["model_id"]

    active_model_id = _refresh_active_model(conn)
    if active_model_id:
        return active_model_id

    row = conn.execute(
        "SELECT model_id FROM qlib_model_state WHERE status = 'trained' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return row["model_id"] if row else None


def _resolve_training_stock_codes(
    smart_conn,
    *,
    universe_source: str = "active_a_stock",
    sample_stock_limit: int = 0,
) -> list[str]:
    source = str(universe_source or "active_a_stock").strip()
    if source == "current_trend":
        stock_sql = (
            "SELECT DISTINCT t.stock_code "
            "FROM mart_stock_trend t "
            "LEFT JOIN excluded_stocks e ON e.stock_code = t.stock_code "
            "WHERE COALESCE(t.stock_code, '') != '' AND e.stock_code IS NULL "
            "ORDER BY t.stock_code"
        )
    elif source == "active_a_stock":
        stock_sql = (
            "SELECT DISTINCT a.stock_code "
            "FROM dim_active_a_stock a "
            "LEFT JOIN excluded_stocks e ON e.stock_code = a.stock_code "
            "WHERE e.stock_code IS NULL "
            "ORDER BY a.stock_code"
        )
    else:
        raise ValueError(f"未知 Qlib 训练宇宙: {source}")

    if sample_stock_limit > 0:
        stock_sql += f" LIMIT {sample_stock_limit}"

    stock_rows = smart_conn.execute(stock_sql).fetchall()
    return [r["stock_code"] for r in stock_rows if r["stock_code"]]


def _normalize_predictions(pred) -> pd.DataFrame:
    """标准化 Qlib 预测输出为 (datetime, instrument, qlib_score) DataFrame"""
    if pred is None:
        return pd.DataFrame(columns=["datetime", "instrument", "qlib_score"])

    if isinstance(pred, pd.Series):
        pred_df = pred.to_frame("qlib_score")
    elif isinstance(pred, pd.DataFrame):
        pred_df = pred.copy()
        if "qlib_score" not in pred_df.columns:
            first_col = pred_df.columns[0]
            pred_df = pred_df.rename(columns={first_col: "qlib_score"})
    else:
        pred_df = pd.DataFrame(pred)
        if pred_df.empty:
            return pd.DataFrame(columns=["datetime", "instrument", "qlib_score"])
        if "qlib_score" not in pred_df.columns:
            first_col = pred_df.columns[0]
            pred_df = pred_df.rename(columns={first_col: "qlib_score"})

    if isinstance(pred_df.index, pd.MultiIndex):
        names = list(pred_df.index.names)
        if "datetime" in names and "instrument" in names:
            pred_df = pred_df.reset_index()[["datetime", "instrument", "qlib_score"]]
        else:
            pred_df = pred_df.reset_index()
            pred_df.columns = ["datetime", "instrument", "qlib_score"]
    else:
        pred_df = pred_df.reset_index()
        if len(pred_df.columns) >= 3:
            pred_df = pred_df.iloc[:, :3]
            pred_df.columns = ["datetime", "instrument", "qlib_score"]
        elif len(pred_df.columns) == 2:
            pred_df.columns = ["instrument", "qlib_score"]
            pred_df["datetime"] = None
        else:
            return pd.DataFrame(columns=["datetime", "instrument", "qlib_score"])

    pred_df["qlib_score"] = pd.to_numeric(pred_df["qlib_score"], errors="coerce")
    pred_df = pred_df.dropna(subset=["qlib_score"])
    return pred_df


def _stock_code_from_instrument(instrument: str) -> str:
    text = str(instrument or "")
    if len(text) >= 8 and text[:2].isalpha():
        return text[2:]
    return text


def _extract_metric(metrics: dict, *suffixes: str):
    if not metrics:
        return None
    for suffix in suffixes:
        for key, value in metrics.items():
            if str(key).endswith(suffix):
                return _safe_round(value)
    return None


def _calc_topk_avg_return(recorder, topk: int = 50) -> Optional[float]:
    try:
        pred = recorder.load_object("pred.pkl")
        label = recorder.load_object("label.pkl")
    except Exception:
        return None
    if pred is None or label is None:
        return None
    try:
        pred_df = pred.iloc[:, [0]].rename(columns={pred.columns[0]: "score"})
        label_df = label.iloc[:, [0]].rename(columns={label.columns[0]: "label"})
        joined = pred_df.join(label_df, how="inner").dropna(subset=["score", "label"])
        if joined.empty:
            return None
        mean_returns = []
        for _, group in joined.groupby(level="datetime"):
            top_group = group.sort_values("score", ascending=False).head(topk)
            if not top_group.empty:
                mean_returns.append(float(top_group["label"].mean()))
        if not mean_returns:
            return None
        return _safe_round(np.mean(mean_returns))
    except Exception:
        return None


def _resolve_backtest_benchmark(data_dir: str, requested: Optional[str]) -> Optional[str]:
    instruments_path = Path(data_dir) / "instruments" / "all.txt"
    if not instruments_path.exists():
        return requested
    try:
        with instruments_path.open() as handle:
            instruments = {line.strip().split("\t")[0] for line in handle if line.strip()}
    except Exception:
        return requested
    if requested and requested in instruments:
        return requested
    fallback_candidates = [
        "SZ159919",  # 沪深300ETF
        "SZ159915",  # 创业板ETF
        "SZ159949",  # 创业板50ETF
        "SZ159918",  # 中小板ETF
    ]
    for code in fallback_candidates:
        if code in instruments:
            return code
    return None


def _use_backtest_benchmark(params: Optional[dict]) -> bool:
    if not params:
        return True
    use_benchmark = params.get("use_benchmark")
    if use_benchmark is None:
        return True
    return bool(use_benchmark)


def _resolve_workflow_benchmark(data_dir: str, params: Optional[dict]) -> Optional[str]:
    if not _use_backtest_benchmark(params):
        return None
    requested = None
    if params:
        requested = params.get("benchmark")
    return _resolve_backtest_benchmark(data_dir, requested or "SH000300")


def _backtest_strategy_label(params: Optional[dict]) -> str:
    topk = int((params or {}).get("backtest_topk", 50) or 50)
    n_drop = int((params or {}).get("backtest_n_drop", 5) or 5)
    return f"TopkDropoutStrategy(topk={topk},n_drop={n_drop})"


def _build_backtest_config(params: Optional[dict], benchmark_code: Optional[str]) -> dict:
    params = dict(params or {})
    backtest_config = {
        "strategy": {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy",
            "kwargs": {
                "signal": "<PRED>",
                "topk": int(params.get("backtest_topk", 50)),
                "n_drop": int(params.get("backtest_n_drop", 5)),
            },
        },
        "backtest": {
            "start_time": params.get("test_start") or params.get("valid_end"),
            "end_time": params.get("test_end"),
            "account": float(params.get("backtest_account", 100000000)),
            "exchange_kwargs": {
                "limit_threshold": 0.095,
                "deal_price": "close",
                "open_cost": 0.0005,
                "close_cost": 0.0015,
                "min_cost": 5,
            },
        },
    }
    if benchmark_code:
        backtest_config["backtest"]["benchmark"] = benchmark_code
    return backtest_config


def _load_train_params(payload: Optional[str]) -> dict:
    try:
        return json.loads(payload or "{}")
    except Exception:
        return {}


def _resolve_training_segments(params: Optional[dict], data_dir: str = None) -> dict[str, tuple[str, str]]:
    params = dict(params or {})
    defaults = _resolve_default_training_segments(data_dir)
    train_start = _date_text(params.get("train_start") or defaults["train"][0])
    train_end = _date_text(params.get("train_end") or defaults["train"][1])
    valid_start = _date_text(params.get("valid_start")) or _next_trading_day_text(train_end, data_dir=data_dir) or defaults["valid"][0]
    valid_end = _date_text(params.get("valid_end") or defaults["valid"][1])
    test_start = _date_text(params.get("test_start")) or _next_trading_day_text(valid_end, data_dir=data_dir) or defaults["test"][0]
    test_end = _date_text(params.get("test_end") or defaults["test"][1])
    segments = {
        "train": (train_start, train_end),
        "valid": (valid_start, valid_end),
        "test": (test_start, test_end),
    }
    for name, (start_text, end_text) in segments.items():
        start_dt = _normalize_factor_date(start_text)
        end_dt = _normalize_factor_date(end_text)
        if start_dt is None or end_dt is None:
            raise ValueError(f"Qlib {name} 窗口日期无效: {start_text} ~ {end_text}")
        if start_dt > end_dt:
            raise ValueError(f"Qlib {name} 窗口起止顺序错误: {start_text} > {end_text}")
    return segments


def _build_handler_config(*, start_time: str, end_time: str, instruments, use_alpha158: bool = True) -> dict:
    return {
        "handler_kind": "alpha158" if use_alpha158 else "ohlcv_light",
        "start_time": start_time,
        "end_time": end_time,
        "instruments": instruments if instruments else "all",
    }


def _build_qlib_handler(handler_config: Optional[dict]):
    if not handler_config:
        raise ValueError("Qlib handler_config 不能为空")
    handler_kind = str(handler_config.get("handler_kind") or "alpha158").strip().lower()
    base_config = {
        key: value
        for key, value in handler_config.items()
        if key != "handler_kind"
    }
    if handler_kind == "alpha158":
        from qlib.contrib.data.handler import Alpha158

        return Alpha158(**base_config)
    if handler_kind == "ohlcv_light":
        from qlib.contrib.data.handler import Alpha158DL
        from qlib.data.dataset.handler import DataHandlerLP

        return DataHandlerLP(
            instruments=base_config.get("instruments"),
            start_time=base_config.get("start_time"),
            end_time=base_config.get("end_time"),
            data_loader={
                "class": "QlibDataLoader",
                "kwargs": {
                    "config": {
                        "feature": Alpha158DL.get_feature_config(_LIGHTWEIGHT_OHLCV_FEATURE_CONFIG),
                        "label": _QLIB_LABEL_CONFIG,
                    },
                    "freq": "day",
                },
            },
            infer_processors=[],
            learn_processors=_QLIB_LEARN_PROCESSORS,
            process_type=DataHandlerLP.PTYPE_A,
        )
    raise ValueError(f"未知 Qlib handler_kind: {handler_kind}")


def _requested_backtest_benchmark(params: Optional[dict]) -> Optional[str]:
    if not _use_backtest_benchmark(params):
        return None
    if not params:
        return "SH000300"
    return str(params.get("benchmark") or "SH000300")


def _candidate_mlruns_roots() -> list[Path]:
    repo_root = Path(__file__).resolve().parent.parent.parent
    candidates = [
        repo_root / "mlruns",
        repo_root / "backend" / "mlruns",
    ]
    seen = set()
    roots = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        roots.append(path)
    return roots


def _read_mlruns_experiment_name(meta_path: Path) -> str:
    try:
        for line in meta_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                return line.split(":", 1)[1].strip() or "Experiment"
    except Exception:
        pass
    return "Experiment"


def _find_saved_recorder(model_id: str) -> Optional[dict]:
    for mlruns_root in _candidate_mlruns_roots():
        if not mlruns_root.exists():
            continue
        for model_id_path in mlruns_root.rglob("params/model_id"):
            try:
                if model_id_path.read_text(encoding="utf-8").strip() != model_id:
                    continue
            except Exception:
                continue
            run_dir = model_id_path.parent.parent
            experiment_dir = run_dir.parent
            return {
                "uri": str(mlruns_root),
                "experiment_id": experiment_dir.name,
                "experiment_name": _read_mlruns_experiment_name(experiment_dir / "meta.yaml"),
                "recorder_id": run_dir.name,
            }
    return None


def _derive_backtest_status(model: dict, backtest_row) -> Optional[str]:
    status = str(model.get("backtest_status") or "").strip()
    if status:
        return status
    if backtest_row:
        return "success"
    model_status = str(model.get("status") or "").strip()
    if model_status == "training":
        return "pending"
    if model_status == "trained":
        return "missing"
    return None


def _candidate_model_paths(model_id: str, model_path: Optional[str]) -> list[Path]:
    candidates = []
    if model_path:
        candidates.append(Path(model_path))
    candidates.append(_MODEL_DIR / f"{model_id}.pkl")
    seen = set()
    result = []
    expected_name = f"{model_id}.pkl"
    for path in candidates:
        if path.name != expected_name:
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def delete_qlib_model_artifacts(conn, model_id: str, model_path: Optional[str] = None) -> dict:
    ensure_tables(conn)
    deleted_files = []
    for path in _candidate_model_paths(model_id, model_path):
        try:
            if path.exists():
                path.unlink()
                deleted_files.append(str(path))
        except Exception:
            logger.warning(f"[Qlib-Full] 删除模型文件失败: {path}")

    deleted_rows = {}
    for table in (
        "qlib_predictions",
        "qlib_factor_importance",
        "qlib_backtest_result",
        "qlib_model_state",
    ):
        cur = conn.execute(f"DELETE FROM {table} WHERE model_id = ?", (model_id,))
        deleted_rows[table] = cur.rowcount or 0
    return {
        "model_id": model_id,
        "deleted_rows": deleted_rows,
        "deleted_files": deleted_files,
    }


def cleanup_failed_qlib_models(conn, model_id: Optional[str] = None) -> list[dict]:
    ensure_tables(conn)
    params: tuple = ()
    where_clause = "WHERE status = 'failed'"
    if model_id:
        where_clause += " AND model_id = ?"
        params = (model_id,)
    rows = conn.execute(
        f"SELECT model_id, model_path FROM qlib_model_state {where_clause} ORDER BY created_at DESC",
        params,
    ).fetchall()
    if not rows:
        return []

    results = []
    for row in rows:
        results.append(
            delete_qlib_model_artifacts(
                conn,
                model_id=row["model_id"],
                model_path=row["model_path"],
            )
        )
    conn.commit()
    return results


def cleanup_stale_training_qlib_models(conn, stale_before: str) -> list[dict]:
    ensure_tables(conn)
    rows = conn.execute(
        """
        SELECT s.model_id, s.model_path
        FROM qlib_model_state s
        WHERE s.status = 'training'
          AND COALESCE(s.finished_at, '') = ''
          AND COALESCE(s.created_at, '') != ''
          AND s.created_at < ?
          AND NOT EXISTS (
              SELECT 1 FROM qlib_predictions p WHERE p.model_id = s.model_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM qlib_factor_importance f WHERE f.model_id = s.model_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM qlib_backtest_result b WHERE b.model_id = s.model_id
          )
        ORDER BY s.created_at DESC
        """,
        (stale_before,),
    ).fetchall()
    if not rows:
        return []

    results = []
    for row in rows:
        results.append(
            delete_qlib_model_artifacts(
                conn,
                model_id=row["model_id"],
                model_path=row["model_path"],
            )
        )
    conn.commit()
    return results


def backfill_qlib_backtest_state(conn, model_id: Optional[str] = None) -> int:
    ensure_tables(conn)
    deleted_failed = cleanup_failed_qlib_models(conn, model_id=model_id)
    params: tuple = ()
    where_clause = ""
    if model_id:
        where_clause = "WHERE model_id = ?"
        params = (model_id,)

    model_rows = conn.execute(
        f"SELECT * FROM qlib_model_state {where_clause} ORDER BY created_at DESC",
        params,
    ).fetchall()
    if not model_rows:
        return 0

    backtest_rows = conn.execute(
        f"""
        SELECT model_id, backtest_id, strategy, sharpe_ratio, calmar_ratio,
               max_drawdown, annual_return, turnover, created_at
        FROM qlib_backtest_result
        {where_clause}
        ORDER BY created_at DESC
        """,
        params,
    ).fetchall()
    latest_backtest_map: dict[str, dict] = {}
    for row in backtest_rows:
        latest_backtest_map.setdefault(row["model_id"], dict(row))

    updated = 0
    for row in model_rows:
        model = dict(row)
        train_params = _load_train_params(model.get("train_params_json"))
        latest_backtest = latest_backtest_map.get(model["model_id"])
        resolved_status = _derive_backtest_status(model, latest_backtest)
        resolved_benchmark = model.get("backtest_benchmark")
        if resolved_benchmark is None or str(resolved_benchmark).strip() == "":
            resolved_benchmark = _requested_backtest_benchmark(train_params)
        resolved_error = model.get("backtest_error") or None

        if (
            resolved_status == (model.get("backtest_status") or None)
            and resolved_benchmark == (model.get("backtest_benchmark") or None)
            and resolved_error == (model.get("backtest_error") or None)
        ):
            continue

        conn.execute(
            """
            UPDATE qlib_model_state
            SET backtest_status = ?,
                backtest_benchmark = ?,
                backtest_error = ?
            WHERE model_id = ?
            """,
            (resolved_status, resolved_benchmark, resolved_error, model["model_id"]),
        )
        updated += 1

    if updated:
        conn.commit()
    return updated + len(deleted_failed)


def _extract_feature_meta(lgb_model, feature_name_candidates: Optional[list[str]] = None) -> tuple[list[str], list[float]]:
    model = getattr(lgb_model, "model", None)
    if model is None:
        return [], []
    feature_names = []
    importances = []
    try:
        feature_names = list(model.feature_name())
        importances = list(model.feature_importance(importance_type="gain"))
    except Exception:
        try:
            booster = getattr(model, "booster_", None)
            if booster is not None:
                feature_names = list(booster.feature_name())
                importances = list(booster.feature_importance(importance_type="gain"))
        except Exception:
            logger.warning("[Qlib-Full] 无法提取特征重要性，跳过写入")
    feature_names = _resolve_feature_name_aliases(feature_names, feature_name_candidates)
    return feature_names, importances


def _load_saved_model_replay_bundle(smart_conn, model_row: dict, params: dict):
    from qlib.contrib.data.handler import Alpha158
    from qlib.data.dataset import DatasetH
    import pickle

    model_id = model_row["model_id"]
    candidate_paths = _candidate_model_paths(model_id, model_row.get("model_path"))
    existing_model_path = next((path for path in candidate_paths if path.exists()), None)
    if existing_model_path is None:
        raise FileNotFoundError(f"未找到模型文件: {model_id}")

    with existing_model_path.open("rb") as handle:
        bundle = pickle.load(handle)
    lgb_model = bundle.get("model") if isinstance(bundle, dict) else bundle
    if lgb_model is None:
        raise RuntimeError(f"模型文件缺少可重放的模型对象: {model_id}")

    segment_params = {
        "train_start": model_row.get("train_start") or params.get("train_start"),
        "train_end": model_row.get("train_end") or params.get("train_end"),
        "valid_start": model_row.get("valid_start") or params.get("valid_start"),
        "valid_end": model_row.get("valid_end") or params.get("valid_end"),
        "test_start": model_row.get("test_start") or params.get("test_start"),
        "test_end": model_row.get("test_end") or params.get("test_end"),
    }
    segments = _resolve_training_segments(segment_params)

    handler_config = bundle.get("handler_config") if isinstance(bundle, dict) else None
    if not handler_config:
        all_codes = _resolve_training_stock_codes(
            smart_conn,
            universe_source=str(params.get("universe_source") or "active_a_stock"),
            sample_stock_limit=int(params.get("sample_stock_limit", 0) or 0),
        )
        handler_config = _build_handler_config(
            start_time=segments["train"][0],
            end_time=segments["test"][1],
            instruments=[
                _instrument_from_stock_code(code)
                for code in all_codes
            ],
            use_alpha158=bool(params.get("use_alpha158", True)),
        )

    handler = _build_qlib_handler(handler_config)
    dataset = DatasetH(
        handler=handler,
        segments=segments,
    )

    instruments = handler_config.get("instruments") if isinstance(handler_config, dict) else None
    if isinstance(instruments, (list, tuple, set)):
        all_codes = [_stock_code_from_instrument(item) for item in instruments if item]
    else:
        all_codes = _resolve_training_stock_codes(
            smart_conn,
            universe_source=str(params.get("universe_source") or "active_a_stock"),
            sample_stock_limit=int(params.get("sample_stock_limit", 0) or 0),
        )

    custom_factors = _load_combined_custom_factors(
        smart_conn,
        all_codes,
        use_financial=bool(params.get("use_financial", True)),
        use_institution=bool(params.get("use_institution", True)),
        use_turtle=bool(params.get("use_turtle", True)),
        use_quality=bool(params.get("use_quality", False)),
        use_stage=bool(params.get("use_stage", False)),
        use_northbound=bool(params.get("use_northbound", False)),
    )
    custom_factor_count = _inject_custom_factors_into_handler(handler, custom_factors)
    logger.info(
        f"[Qlib-Full] 已加载模型回测重放上下文: {model_id}, 自定义因子 {custom_factor_count}"
    )
    return lgb_model, dataset


def _persist_training_outputs(smart_conn, *, model_id: str, params: dict,
                              model_path: str, pred, lgb_model,
                              feature_name_candidates: Optional[list[str]] = None) -> dict:
    pred_df = _normalize_predictions(pred)
    if pred_df.empty:
        raise RuntimeError("Qlib 预测结果为空，无法写回排名")

    if pred_df["datetime"].notna().any():
        latest_dt = pred_df["datetime"].dropna().max()
        latest_df = pred_df[pred_df["datetime"] == latest_dt].copy()
    else:
        latest_dt = datetime.now().strftime("%Y-%m-%d")
        latest_df = pred_df.copy()

    latest_df["stock_code"] = latest_df["instrument"].map(_stock_code_from_instrument)
    latest_df = latest_df.sort_values("qlib_score", ascending=False).reset_index(drop=True)
    latest_df["qlib_rank"] = latest_df.index + 1
    total = len(latest_df)
    latest_df["qlib_percentile"] = latest_df["qlib_rank"].map(
        lambda rank: round((1 - ((rank - 1) / total)) * 100, 2) if total else None
    )

    info_rows = smart_conn.execute("""
        SELECT stock_code, stock_name
        FROM dim_active_a_stock
    """).fetchall()
    info_map = {row["stock_code"]: dict(row) for row in info_rows}

    smart_conn.execute("DELETE FROM qlib_predictions WHERE model_id = ?", (model_id,))
    for row in latest_df.itertuples(index=False):
        meta = info_map.get(row.stock_code, {})
        smart_conn.execute("""
            INSERT OR REPLACE INTO qlib_predictions
            (model_id, stock_code, stock_name, predict_date, qlib_score, qlib_rank, qlib_percentile)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            model_id,
            row.stock_code,
            meta.get("stock_name") or row.stock_code,
            str(latest_dt)[:10],
            float(row.qlib_score),
            int(row.qlib_rank),
            float(row.qlib_percentile),
        ))

    feature_names, importances = _extract_feature_meta(lgb_model, feature_name_candidates)
    smart_conn.execute("DELETE FROM qlib_factor_importance WHERE model_id = ?", (model_id,))
    for factor_name, importance in zip(feature_names, importances):
        if importance is None:
            continue
        if str(factor_name).startswith("inst_"):
            factor_group = "institution"
        elif str(factor_name).startswith("turtle_"):
            factor_group = "turtle"
        elif str(factor_name).startswith("qual_"):
            factor_group = "quality"
        elif str(factor_name).startswith("stage_"):
            factor_group = "stage"
        elif str(factor_name).startswith("nb_"):
            factor_group = "northbound"
        elif str(factor_name).startswith("fin_"):
            factor_group = "financial"
        else:
            factor_group = "alpha158"
        smart_conn.execute("""
            INSERT OR REPLACE INTO qlib_factor_importance
            (model_id, factor_name, importance, factor_group)
            VALUES (?, ?, ?, ?)
        """, (model_id, str(factor_name), float(importance), factor_group))

    smart_conn.execute("""
        UPDATE qlib_model_state
        SET status = 'trained',
            stock_count = ?,
            factor_count = ?,
            error = NULL,
            model_path = ?,
            train_params_json = ?,
            finished_at = ?
        WHERE model_id = ?
    """, (
        len(latest_df),
        len(feature_names),
        model_path,
        json.dumps(params, ensure_ascii=False),
        datetime.now().isoformat(),
        model_id,
    ))
    smart_conn.commit()
    return {
        "predictions_count": len(latest_df),
        "factor_count": len(feature_names),
        "predict_date": str(latest_dt)[:10],
    }


def _persist_workflow_records(smart_conn, *, model_id: str, dataset, model, params: dict) -> dict:
    from qlib.workflow import R
    from qlib.workflow.record_temp import PortAnaRecord, SigAnaRecord, SignalRecord

    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    experiment_name = "chunky_monkey_qlib"
    record_summary = {
        "ic_mean": None,
        "rank_ic_mean": None,
        "test_top50_avg_return": None,
        "backtest_id": None,
    }
    benchmark_code = _resolve_workflow_benchmark(str(_DATA_DIR), params)
    smart_conn.execute(
        """
        UPDATE qlib_model_state
        SET backtest_status = ?,
            backtest_benchmark = ?,
            backtest_error = NULL
        WHERE model_id = ?
        """,
        ("running", benchmark_code, model_id),
    )
    smart_conn.commit()
    backtest_config = _build_backtest_config(params, benchmark_code)

    recorder = None
    started_new_exp = False
    try:
        recorder = R.get_recorder()
    except Exception:
        recorder = None

    record_ctx = nullcontext(recorder)
    if recorder is None:
        started_new_exp = True
        record_ctx = R.start(
            experiment_name=experiment_name,
            recorder_name=model_id,
            uri=str(_RUNS_DIR),
        )

    with record_ctx:
        if recorder is None:
            recorder = R.get_recorder()
        recorder.log_params(
            model_id=model_id,
            train_start=params.get("train_start"),
            train_end=params.get("train_end"),
            valid_start=params.get("valid_start"),
            valid_end=params.get("valid_end"),
            test_start=params.get("test_start"),
            test_end=params.get("test_end"),
            benchmark=benchmark_code,
            use_benchmark=_use_backtest_benchmark(params),
            use_alpha158=params.get("use_alpha158", True),
            use_financial=params.get("use_financial", True),
            use_institution=params.get("use_institution", True),
            use_turtle=params.get("use_turtle", True),
            use_quality=params.get("use_quality", False),
            use_stage=params.get("use_stage", False),
            use_northbound=params.get("use_northbound", False),
        )
        SignalRecord(model=model, dataset=dataset, recorder=recorder).generate()
        SigAnaRecord(recorder, ana_long_short=True).generate()

        metrics = recorder.list_metrics() or {}
        ic_mean = _safe_round(metrics.get("IC"))
        rank_ic_mean = _safe_round(metrics.get("Rank IC"))
        test_top50_avg_return = _calc_topk_avg_return(
            recorder, topk=int(params.get("backtest_topk", 50))
        )
        annual_return = _extract_metric(metrics, "excess_return_with_cost.annualized_return")
        sharpe_ratio = _extract_metric(metrics, "excess_return_with_cost.information_ratio")
        max_drawdown = _extract_metric(metrics, "excess_return_with_cost.max_drawdown")
        turnover = _extract_metric(metrics, "turnover")
        calmar_ratio = None
        if annual_return is not None and max_drawdown not in (None, 0):
            try:
                calmar_ratio = _safe_round(float(annual_return) / abs(float(max_drawdown)))
            except Exception:
                calmar_ratio = None

        smart_conn.execute(
            """
            UPDATE qlib_model_state
            SET ic_mean = ?, rank_ic_mean = ?, test_top50_avg_return = ?
            WHERE model_id = ?
            """,
            (ic_mean, rank_ic_mean, test_top50_avg_return, model_id),
        )

        backtest_id = None
        try:
            PortAnaRecord(
                recorder,
                config=backtest_config,
                risk_analysis_freq="day",
                indicator_analysis_freq="day",
            ).generate()
            metrics = recorder.list_metrics() or metrics
            annual_return = _extract_metric(metrics, "excess_return_with_cost.annualized_return")
            sharpe_ratio = _extract_metric(metrics, "excess_return_with_cost.information_ratio")
            max_drawdown = _extract_metric(metrics, "excess_return_with_cost.max_drawdown")
            turnover = _extract_metric(metrics, "turnover")
            calmar_ratio = None
            if annual_return is not None and max_drawdown not in (None, 0):
                try:
                    calmar_ratio = _safe_round(float(annual_return) / abs(float(max_drawdown)))
                except Exception:
                    calmar_ratio = None
            backtest_id = f"{model_id}_default_day"
            smart_conn.execute(
                """
                INSERT OR REPLACE INTO qlib_backtest_result
                (model_id, backtest_id, strategy, sharpe_ratio, calmar_ratio, max_drawdown, annual_return, turnover, detail_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    backtest_id,
                    _backtest_strategy_label(params),
                    sharpe_ratio,
                    calmar_ratio,
                    max_drawdown,
                    annual_return,
                    turnover,
                    json.dumps(metrics, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
            smart_conn.execute(
                """
                UPDATE qlib_model_state
                SET backtest_status = ?,
                    backtest_benchmark = ?,
                    backtest_error = NULL
                WHERE model_id = ?
                """,
                ("success", benchmark_code, model_id),
            )
        except Exception as exc:
            record_summary["backtest_error"] = str(exc)
            smart_conn.execute(
                """
                UPDATE qlib_model_state
                SET backtest_status = ?,
                    backtest_benchmark = ?,
                    backtest_error = ?
                WHERE model_id = ?
                """,
                ("failed", benchmark_code, str(exc), model_id),
            )
            logger.warning(f"[Qlib-Full] PortAnaRecord 回测失败，保留信号分析结果继续写回: {exc}")
        smart_conn.commit()
        record_summary.update(
            {
                "ic_mean": ic_mean,
                "rank_ic_mean": rank_ic_mean,
                "test_top50_avg_return": test_top50_avg_return,
                "backtest_id": backtest_id,
                "backtest_annual_return": annual_return,
                "backtest_sharpe_ratio": sharpe_ratio,
                "backtest_max_drawdown": max_drawdown,
            }
        )
    if not started_new_exp:
        try:
            R.end_exp()
        except Exception:
            pass
    return record_summary


def _persist_backtest_from_saved_recorder(smart_conn, *, model_id: str, params: dict, data_dir: str) -> Optional[dict]:
    from qlib.workflow.exp import MLflowExperiment
    from qlib.workflow.record_temp import PortAnaRecord

    recorder_ref = _find_saved_recorder(model_id)
    if not recorder_ref:
        return None

    experiment = MLflowExperiment(
        recorder_ref["experiment_id"],
        recorder_ref["experiment_name"],
        recorder_ref["uri"],
    )
    recorder = experiment.get_recorder(recorder_ref["recorder_id"])
    try:
        recorder.load_object("pred.pkl")
    except Exception:
        return None

    record_summary = {
        "ic_mean": _safe_round((recorder.list_metrics() or {}).get("IC")),
        "rank_ic_mean": _safe_round((recorder.list_metrics() or {}).get("Rank IC")),
        "test_top50_avg_return": _calc_topk_avg_return(
            recorder,
            topk=int(params.get("backtest_topk", 50)),
        ),
        "backtest_id": None,
    }
    benchmark_code = _resolve_workflow_benchmark(data_dir, params)
    smart_conn.execute(
        """
        UPDATE qlib_model_state
        SET backtest_status = ?,
            backtest_benchmark = ?,
            backtest_error = NULL
        WHERE model_id = ?
        """,
        ("running", benchmark_code, model_id),
    )
    smart_conn.commit()

    try:
        PortAnaRecord(
            recorder,
            config=_build_backtest_config(params, benchmark_code),
            risk_analysis_freq="day",
            indicator_analysis_freq="day",
        ).generate()
        metrics = recorder.list_metrics() or {}
        annual_return = _extract_metric(metrics, "excess_return_with_cost.annualized_return")
        sharpe_ratio = _extract_metric(metrics, "excess_return_with_cost.information_ratio")
        max_drawdown = _extract_metric(metrics, "excess_return_with_cost.max_drawdown")
        turnover = _extract_metric(metrics, "turnover")
        calmar_ratio = None
        if annual_return is not None and max_drawdown not in (None, 0):
            try:
                calmar_ratio = _safe_round(float(annual_return) / abs(float(max_drawdown)))
            except Exception:
                calmar_ratio = None
        backtest_id = f"{model_id}_default_day"
        smart_conn.execute(
            """
            INSERT OR REPLACE INTO qlib_backtest_result
            (model_id, backtest_id, strategy, sharpe_ratio, calmar_ratio, max_drawdown, annual_return, turnover, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_id,
                backtest_id,
                _backtest_strategy_label(params),
                sharpe_ratio,
                calmar_ratio,
                max_drawdown,
                annual_return,
                turnover,
                json.dumps(metrics, ensure_ascii=False),
                datetime.now().isoformat(),
            ),
        )
        smart_conn.execute(
            """
            UPDATE qlib_model_state
            SET ic_mean = COALESCE(ic_mean, ?),
                rank_ic_mean = COALESCE(rank_ic_mean, ?),
                test_top50_avg_return = COALESCE(test_top50_avg_return, ?),
                backtest_status = ?,
                backtest_benchmark = ?,
                backtest_error = NULL
            WHERE model_id = ?
            """,
            (
                record_summary["ic_mean"],
                record_summary["rank_ic_mean"],
                record_summary["test_top50_avg_return"],
                "success",
                benchmark_code,
                model_id,
            ),
        )
        smart_conn.commit()
        record_summary.update(
            {
                "backtest_id": backtest_id,
                "backtest_annual_return": annual_return,
                "backtest_sharpe_ratio": sharpe_ratio,
                "backtest_max_drawdown": max_drawdown,
            }
        )
        return record_summary
    except Exception as exc:
        smart_conn.execute(
            """
            UPDATE qlib_model_state
            SET backtest_status = ?,
                backtest_benchmark = ?,
                backtest_error = ?
            WHERE model_id = ?
            """,
            ("failed", benchmark_code, str(exc), model_id),
        )
        smart_conn.commit()
        return {
            **record_summary,
            "backtest_error": str(exc),
        }


def sync_latest_predictions_to_stock_trend(smart_conn, model_id: Optional[str] = None) -> int:
    ensure_tables(smart_conn)
    for ddl in [
        "ALTER TABLE mart_stock_trend ADD COLUMN qlib_score REAL",
        "ALTER TABLE mart_stock_trend ADD COLUMN qlib_percentile REAL",
        "ALTER TABLE mart_stock_trend ADD COLUMN qlib_rank INTEGER",
    ]:
        try:
            smart_conn.execute(ddl)
        except Exception:
            pass
    model_id = get_default_model_id(smart_conn, model_id=model_id)
    if not model_id:
        return 0

    rows = smart_conn.execute(
        "SELECT stock_code, qlib_rank, qlib_score, qlib_percentile FROM qlib_predictions WHERE model_id = ?",
        (model_id,)
    ).fetchall()
    smart_conn.execute(
        "UPDATE mart_stock_trend SET qlib_rank = NULL, qlib_score = NULL, qlib_percentile = NULL"
    )
    updated = 0
    for row in rows:
        cur = smart_conn.execute("""
            UPDATE mart_stock_trend
            SET qlib_rank = ?, qlib_score = ?, qlib_percentile = ?
            WHERE stock_code = ?
        """, (row["qlib_rank"], row["qlib_score"], row["qlib_percentile"], row["stock_code"]))
        updated += cur.rowcount or 0
    smart_conn.commit()
    return updated


# ============================================================
# 训练
# ============================================================

def train_full_model(smart_conn, data_dir: str = None, *, params: Optional[dict] = None) -> dict:
    """完整 Qlib 训练管线：Alpha158 + 自定义因子 + LGBModel"""
    if not _QLIB_AVAILABLE:
        raise RuntimeError(f"pyqlib 不可用: {_QLIB_ERROR}")

    params = dict(params or {})
    data_path = data_dir or str(_DATA_DIR)
    segments = _resolve_training_segments(params, data_dir=data_path)
    train_start, train_end = segments["train"]
    valid_start, valid_end = segments["valid"]
    test_start, test_end = segments["test"]
    params["train_start"] = train_start
    params["train_end"] = train_end
    params["valid_start"] = valid_start
    params["valid_end"] = valid_end
    params["test_start"] = test_start
    params["test_end"] = test_end
    use_financial = params.get("use_financial", True)
    use_institution = params.get("use_institution", True)
    use_turtle = params.get("use_turtle", True)
    use_quality = params.get("use_quality", True)
    use_stage = params.get("use_stage", True)
    use_northbound = params.get("use_northbound", False)
    universe_source = str(params.get("universe_source") or "active_a_stock").strip()
    sample_stock_limit = int(params.get("sample_stock_limit", 0) or 0)

    from services.qlib_data_handler import dump_bin_from_db, get_qlib_data_status
    qlib_data_status = get_qlib_data_status(data_path)
    if not qlib_data_status.get("available"):
        logger.info("[Qlib-Full] 检测到 qlib_data 不可用，开始从 market_data.db 自动构建")
        from services.market_db import get_market_conn

        mkt_conn = get_market_conn()
        try:
            dump_result = dump_bin_from_db(mkt_conn, data_dir=data_path)
            logger.info(f"[Qlib-Full] 已自动构建 qlib_data: {dump_result}")
        finally:
            try:
                mkt_conn.close()
            except Exception:
                pass

    if not init_qlib(data_path):
        raise RuntimeError("Qlib 初始化失败")

    from qlib.config import C
    qlib_joblib_backend = params.get("qlib_joblib_backend") or "threading"
    qlib_kernels = int(params.get("qlib_kernels", 1) or 1)
    C["joblib_backend"] = qlib_joblib_backend
    C["kernels"] = max(1, qlib_kernels)
    logger.info(
        f"[Qlib-Full] 使用安全执行模式 backend={C['joblib_backend']} kernels={C['kernels']}"
    )

    ensure_tables(smart_conn)
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)

    model_id = f"lgb_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info(f"[Qlib-Full] 开始训练: {model_id}")
    smart_conn.execute("""
        INSERT OR REPLACE INTO qlib_model_state
        (model_id, status, train_start, train_end, valid_start, valid_end, test_start, test_end,
         error, model_path, train_params_json, created_at, finished_at)
        VALUES (?, 'training', ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL)
    """, (
        model_id,
        train_start,
        train_end,
        valid_start,
        valid_end,
        test_start,
        test_end,
        json.dumps(params, ensure_ascii=False),
        datetime.now().isoformat(),
    ))
    smart_conn.commit()

    try:
        from qlib.contrib.model.gbdt import LGBModel
        from qlib.data.dataset import DatasetH

        all_codes = _resolve_training_stock_codes(
            smart_conn,
            universe_source=universe_source,
            sample_stock_limit=sample_stock_limit,
        )
        logger.info(
            f"[Qlib-Full] 训练宇宙: {universe_source}, 股票数 {len(all_codes)}"
        )
        qlib_instruments = [_instrument_from_stock_code(code) for code in all_codes]

        handler_config = _build_handler_config(
            start_time=train_start,
            end_time=test_end,
            instruments=qlib_instruments,
            use_alpha158=bool(params.get("use_alpha158", True)),
        )
        logger.info(
            "[Qlib-Full] 基础特征栈: %s",
            "Alpha158" if params.get("use_alpha158", True) else "轻量 OHLCV",
        )

        handler = _build_qlib_handler(handler_config)
        dataset = DatasetH(
            handler=handler,
            segments=segments
        )

        # ============================================================
        # 注入自定义因子（财务 + 机构）
        # ============================================================
        custom_factors = _load_combined_custom_factors(
            smart_conn,
            all_codes,
            use_financial=bool(use_financial),
            use_institution=bool(use_institution),
            use_turtle=bool(use_turtle),
            use_quality=bool(use_quality),
            use_stage=bool(use_stage),
            use_northbound=bool(use_northbound),
        )
        custom_factor_count = len(custom_factors.columns) if not custom_factors.empty else 0
        logger.info(f"[Qlib-Full] 自定义因子: {custom_factor_count} 个, 覆盖 {len(custom_factors)} 只股票")

        # 如果有自定义因子，注入到 dataset 中
        # Qlib DatasetH 内部用 handler.fetch() 获取特征 DataFrame
        # 我们在 handler._data 层面直接 concat（社区通用做法）
        _inject_custom_factors_into_handler(handler, custom_factors)
        feature_name_candidates = _extract_handler_feature_names(handler)

        # LGBModel
        lgb_model = LGBModel(
            loss="mse",
            num_boost_round=int(params.get("num_boost_round", 500)),
            early_stopping_rounds=int(params.get("early_stopping_rounds", 50)),
            num_leaves=int(params.get("num_leaves", 64)),
            learning_rate=float(params.get("learning_rate", 0.05)),
            subsample=float(params.get("subsample", 0.8)),
            colsample_bytree=float(params.get("colsample_bytree", 0.8)),
        )

        lgb_model.fit(dataset)

        # 预测
        pred = lgb_model.predict(dataset)
        logger.info(f"[Qlib-Full] 预测完成: {len(pred)} 条")

        # 保存模型
        import pickle
        model_path = str(_MODEL_DIR / f"{model_id}.pkl")
        with open(model_path, "wb") as f:
            pickle.dump({"model": lgb_model, "handler_config": handler_config,
                         "custom_factor_count": custom_factor_count}, f)

        persisted = _persist_training_outputs(
            smart_conn,
            model_id=model_id,
            params=params,
            model_path=model_path,
            pred=pred,
            lgb_model=lgb_model,
            feature_name_candidates=feature_name_candidates,
        )
        workflow_summary = {}
        try:
            workflow_summary = _persist_workflow_records(
                smart_conn,
                model_id=model_id,
                dataset=dataset,
                model=lgb_model,
                params=params,
            )
            logger.info(
                "[Qlib-Full] Workflow records 完成: "
                f"IC={workflow_summary.get('ic_mean')} "
                f"RankIC={workflow_summary.get('rank_ic_mean')} "
                f"Backtest={workflow_summary.get('backtest_id')}"
            )
        except Exception as exc:
            workflow_summary = {
                "workflow_error": str(exc),
                "ic_mean": None,
                "rank_ic_mean": None,
                "test_top50_avg_return": None,
                "backtest_id": None,
            }
            logger.warning(f"[Qlib-Full] Workflow records 生成失败，保留训练结果继续回流: {exc}")
        active_model_id = _refresh_active_model(smart_conn) or model_id
        synced_stock_count = sync_latest_predictions_to_stock_trend(smart_conn, model_id=active_model_id)

        result = {
            "model_id": model_id,
            "status": "trained",
            "is_active": active_model_id == model_id,
            "active_model_id": active_model_id,
            "model_path": model_path,
            "predictions_count": persisted["predictions_count"],
            "custom_factors": custom_factor_count,
            "factor_count": persisted["factor_count"],
            "predict_date": persisted["predict_date"],
            "synced_stock_count": synced_stock_count,
            "ic_mean": workflow_summary.get("ic_mean"),
            "rank_ic_mean": workflow_summary.get("rank_ic_mean"),
            "test_top50_avg_return": workflow_summary.get("test_top50_avg_return"),
            "backtest_id": workflow_summary.get("backtest_id"),
            "workflow_error": workflow_summary.get("workflow_error") or workflow_summary.get("backtest_error"),
        }
        logger.info(f"[Qlib-Full] 训练完成: {result}")
        return result

    except Exception as e:
        smart_conn.execute(
            "UPDATE qlib_model_state SET status = 'failed', error = ?, finished_at = ? WHERE model_id = ?",
            (str(e), datetime.now().isoformat(), model_id),
        )
        cleanup_failed_qlib_models(smart_conn, model_id=model_id)
        logger.error(f"[Qlib-Full] 训练失败: {e}")
        raise


def run_backtest(smart_conn, model_id: str, data_dir: str = None) -> dict:
    """返回最新 Qlib 回测摘要；训练主流程已自动生成标准回测记录。"""
    ensure_tables(smart_conn)
    row = smart_conn.execute(
        """
        SELECT model_id, backtest_id, strategy, sharpe_ratio, calmar_ratio,
               max_drawdown, annual_return, turnover, created_at
        FROM qlib_backtest_result
        WHERE model_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (model_id,),
    ).fetchone()
    if not row:
        return {"model_id": model_id, "status": "not_ready"}
    return {
        "model_id": row["model_id"],
        "status": "ok",
        "backtest_id": row["backtest_id"],
        "strategy": row["strategy"],
        "sharpe_ratio": _safe_round(row["sharpe_ratio"]),
        "calmar_ratio": _safe_round(row["calmar_ratio"]),
        "max_drawdown": _safe_round(row["max_drawdown"]),
        "annual_return": _safe_round(row["annual_return"]),
        "turnover": _safe_round(row["turnover"]),
        "created_at": row["created_at"],
    }


def rebuild_model_backtest(smart_conn, model_id: Optional[str] = None, data_dir: str = None) -> dict:
    if not _QLIB_AVAILABLE:
        raise RuntimeError(f"pyqlib 不可用: {_QLIB_ERROR}")

    ensure_tables(smart_conn)
    model_id = get_default_model_id(smart_conn, model_id=model_id)
    if not model_id:
        raise ValueError("当前没有可补跑回测的已训练模型")

    model_row = smart_conn.execute(
        "SELECT * FROM qlib_model_state WHERE model_id = ?",
        (model_id,),
    ).fetchone()
    if not model_row:
        raise ValueError(f"模型不存在: {model_id}")
    model = dict(model_row)
    if str(model.get("status") or "") != "trained":
        raise ValueError(f"仅支持对已训练模型补跑回测: {model_id}")

    params = _load_train_params(model.get("train_params_json"))
    data_path = data_dir or str(_DATA_DIR)
    if not init_qlib(data_path):
        raise RuntimeError("Qlib 初始化失败")

    from qlib.config import C

    qlib_joblib_backend = params.get("qlib_joblib_backend") or "threading"
    qlib_kernels = int(params.get("qlib_kernels", 1) or 1)
    C["joblib_backend"] = qlib_joblib_backend
    C["kernels"] = max(1, qlib_kernels)

    workflow_summary = _persist_backtest_from_saved_recorder(
        smart_conn,
        model_id=model_id,
        params=params,
        data_dir=data_path,
    )
    if workflow_summary is None:
        lgb_model, dataset = _load_saved_model_replay_bundle(smart_conn, model, params)
        workflow_summary = _persist_workflow_records(
            smart_conn,
            model_id=model_id,
            dataset=dataset,
            model=lgb_model,
            params=params,
        )
    backfill_qlib_backtest_state(smart_conn, model_id=model_id)
    _refresh_active_model(smart_conn)
    latest_backtest = run_backtest(smart_conn, model_id)
    return {
        "model_id": model_id,
        "backtest_status": latest_backtest.get("status"),
        "backtest_benchmark": _resolve_workflow_benchmark(data_path, params),
        "backtest_id": workflow_summary.get("backtest_id"),
        "workflow_error": workflow_summary.get("workflow_error") or workflow_summary.get("backtest_error"),
        "latest_backtest": latest_backtest,
    }


# ============================================================
# 查询 API（不依赖 pyqlib，只读 SQLite）
# ============================================================

def get_model_status(conn) -> Optional[dict]:
    """返回最新模型状态"""
    ensure_tables(conn)
    backfill_qlib_backtest_state(conn)
    _refresh_active_model(conn)
    latest_row = conn.execute(
        "SELECT * FROM qlib_model_state ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    row = latest_row
    if latest_row and str(latest_row["status"] or "") != "training":
        default_model_id = get_default_model_id(conn)
        if default_model_id:
            row = conn.execute(
                "SELECT * FROM qlib_model_state WHERE model_id = ?",
                (default_model_id,),
            ).fetchone()
    if not row:
        return None
    model = dict(row)
    params = _load_train_params(model.get("train_params_json"))
    backtest_row = conn.execute(
        """
        SELECT backtest_id, strategy, sharpe_ratio, calmar_ratio, max_drawdown, annual_return, turnover, created_at
        FROM qlib_backtest_result
        WHERE model_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (model["model_id"],),
    ).fetchone()
    model["train_params"] = params
    model["backtest_status"] = _derive_backtest_status(model, backtest_row)
    model["backtest_benchmark"] = model.get("backtest_benchmark") or _requested_backtest_benchmark(params)
    model["backtest_error"] = model.get("backtest_error") or None
    model["latest_backtest"] = (
        {
            "backtest_id": backtest_row["backtest_id"],
            "strategy": backtest_row["strategy"],
            "sharpe_ratio": _safe_round(backtest_row["sharpe_ratio"]),
            "calmar_ratio": _safe_round(backtest_row["calmar_ratio"]),
            "max_drawdown": _safe_round(backtest_row["max_drawdown"]),
            "annual_return": _safe_round(backtest_row["annual_return"]),
            "turnover": _safe_round(backtest_row["turnover"]),
            "created_at": backtest_row["created_at"],
        }
        if backtest_row else None
    )
    return model


def get_factor_importance(conn, model_id: Optional[str] = None) -> list:
    """返回因子重要性"""
    ensure_tables(conn)
    model_id = get_default_model_id(conn, model_id=model_id)
    if not model_id:
        return []

    rows = conn.execute(
        "SELECT * FROM qlib_factor_importance WHERE model_id = ? ORDER BY importance DESC",
        (model_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_model_summary(conn, model_id: Optional[str] = None) -> dict:
    """返回最新训练模型的摘要，供评分卡/验证页直接复用。"""
    ensure_tables(conn)
    backfill_qlib_backtest_state(conn, model_id=model_id)
    model_id = get_default_model_id(conn, model_id=model_id)
    if not model_id:
        return {}

    model_row = conn.execute(
        "SELECT * FROM qlib_model_state WHERE model_id = ?",
        (model_id,),
    ).fetchone()
    if not model_row:
        return {}
    model = dict(model_row)

    predict_row = conn.execute(
        """
        SELECT COUNT(*) AS prediction_count,
               MAX(predict_date) AS predict_date,
               AVG(qlib_percentile) AS avg_percentile
        FROM qlib_predictions
        WHERE model_id = ?
        """,
        (model_id,),
    ).fetchone()

    group_rows = conn.execute(
        """
        SELECT COALESCE(factor_group, 'unknown') AS factor_group,
               COUNT(*) AS factor_count,
               SUM(importance) AS total_importance,
               AVG(importance) AS avg_importance
        FROM qlib_factor_importance
        WHERE model_id = ?
        GROUP BY COALESCE(factor_group, 'unknown')
        ORDER BY SUM(importance) DESC, factor_group
        """,
        (model_id,),
    ).fetchall()
    top_factor_rows = conn.execute(
        """
        SELECT factor_name, importance, factor_group
        FROM qlib_factor_importance
        WHERE model_id = ?
        ORDER BY importance DESC, factor_name
        LIMIT 5
        """,
        (model_id,),
    ).fetchall()
    backtest_row = conn.execute(
        """
        SELECT backtest_id, strategy, sharpe_ratio, calmar_ratio, max_drawdown, annual_return, turnover, created_at
        FROM qlib_backtest_result
        WHERE model_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (model_id,),
    ).fetchone()

    params = _load_train_params(model.get("train_params_json"))
    backtest_status = _derive_backtest_status(model, backtest_row)
    backtest_benchmark = model.get("backtest_benchmark") or _requested_backtest_benchmark(params)

    return {
        "model_id": model_id,
        "status": model.get("status"),
        "is_active": bool(model.get("is_active")),
        "performance_rank": _safe_round(model.get("performance_rank"), 4),
        "train_start": model.get("train_start"),
        "train_end": model.get("train_end"),
        "valid_start": model.get("valid_start"),
        "valid_end": model.get("valid_end"),
        "test_start": model.get("test_start"),
        "test_end": model.get("test_end"),
        "stock_count": int(model.get("stock_count") or 0),
        "factor_count": int(model.get("factor_count") or 0),
        "prediction_count": int((predict_row["prediction_count"] if predict_row else 0) or 0),
        "predict_date": predict_row["predict_date"] if predict_row else None,
        "avg_percentile": _safe_round(predict_row["avg_percentile"] if predict_row else None, 2),
        "ic_mean": _safe_round(model.get("ic_mean")),
        "rank_ic_mean": _safe_round(model.get("rank_ic_mean")),
        "test_top50_avg_return": _safe_round(model.get("test_top50_avg_return")),
        "created_at": model.get("created_at"),
        "finished_at": model.get("finished_at"),
        "backtest_status": backtest_status,
        "backtest_benchmark": backtest_benchmark,
        "backtest_error": model.get("backtest_error") or None,
        "train_params": {
            "use_alpha158": bool(params.get("use_alpha158", True)),
            "use_financial": bool(params.get("use_financial", True)),
            "use_institution": bool(params.get("use_institution", True)),
            "use_turtle": bool(params.get("use_turtle", True)),
            "use_quality": bool(params.get("use_quality", False)),
            "use_stage": bool(params.get("use_stage", False)),
            "use_northbound": bool(params.get("use_northbound", False)),
            "use_benchmark": _use_backtest_benchmark(params),
            "benchmark": _requested_backtest_benchmark(params),
            "universe_source": str(params.get("universe_source") or "active_a_stock"),
        },
        "factor_groups": [
            {
                "factor_group": row["factor_group"],
                "factor_count": int(row["factor_count"] or 0),
                "total_importance": _safe_round(row["total_importance"], 2),
                "avg_importance": _safe_round(row["avg_importance"], 2),
            }
            for row in group_rows
        ],
        "top_factors": [
            {
                "factor_name": row["factor_name"],
                "factor_group": row["factor_group"],
                "importance": _safe_round(row["importance"], 2),
            }
            for row in top_factor_rows
        ],
        "latest_backtest": (
            {
                "backtest_id": backtest_row["backtest_id"],
                "strategy": backtest_row["strategy"],
                "benchmark": backtest_benchmark,
                "sharpe_ratio": _safe_round(backtest_row["sharpe_ratio"]),
                "calmar_ratio": _safe_round(backtest_row["calmar_ratio"]),
                "max_drawdown": _safe_round(backtest_row["max_drawdown"]),
                "annual_return": _safe_round(backtest_row["annual_return"]),
                "turnover": _safe_round(backtest_row["turnover"]),
                "created_at": backtest_row["created_at"],
            }
            if backtest_row else None
        ),
    }


def get_qlib_etf_consensus(conn, model_id: Optional[str] = None, topk: int = 50) -> dict:
    """聚合 Qlib 股票预测，产出 ETF 分类可消费的共识摘要。"""
    ensure_tables(conn)
    summary = get_model_summary(conn, model_id=model_id)
    if not summary:
        return {
            "available": False,
            "model_id": None,
            "model_status": "none",
            "signal_date": None,
            "topk": int(topk),
            "categories": [],
            "category_signal_map": {},
            "factor_consensus": {},
            "leading_factor_group": None,
        }

    model_id = summary.get("model_id")
    model_status = summary.get("status") or "unknown"
    industry_level1_expr = industry_level_expr(1, alias="ctx")
    rows = conn.execute(
        f"""
        SELECT p.stock_code, p.stock_name, p.qlib_score, p.qlib_rank, p.qlib_percentile,
               ctx.tdx_l1
        FROM qlib_predictions p
        LEFT JOIN dim_stock_industry_context_latest ctx ON ctx.stock_code = p.stock_code
        WHERE p.model_id = ?
        ORDER BY p.qlib_rank ASC, p.stock_code ASC
        LIMIT ?
        """,
        (model_id, int(topk)),
    ).fetchall()

    factor_groups = summary.get("factor_groups") or []
    total_importance = sum(float(item.get("total_importance") or 0.0) for item in factor_groups)
    factor_consensus = {}
    for item in factor_groups:
        group = item.get("factor_group") or "unknown"
        importance = float(item.get("total_importance") or 0.0)
        factor_consensus[group] = _safe_round(importance / total_importance, 4) if total_importance > 0 else None
    leading_factor_group = factor_groups[0].get("factor_group") if factor_groups else None

    grouped: dict[str, list[dict]] = {}
    mapped_stock_count = 0
    for row in rows:
        item = dict(row)
        category = _map_tdx_l1_to_etf_category(item.get("tdx_l1"))
        if not category:
            continue
        mapped_stock_count += 1
        grouped.setdefault(category, []).append(item)

    categories = []
    for category, items in grouped.items():
        percentiles = [float(item.get("qlib_percentile") or 0.0) for item in items]
        scores = [float(item.get("qlib_score") or 0.0) for item in items]
        ranks = [int(item.get("qlib_rank") or 0) for item in items if item.get("qlib_rank") is not None]
        stock_count = len(items)
        high_conviction_count = sum(1 for item in items if float(item.get("qlib_percentile") or 0.0) >= 80.0)
        avg_percentile = sum(percentiles) / stock_count if stock_count else 0.0
        avg_score = sum(scores) / stock_count if stock_count else 0.0
        median_rank = int(np.median(ranks)) if ranks else None
        conviction_ratio = high_conviction_count / stock_count if stock_count else 0.0
        consensus_score = _safe_round(min(max(avg_percentile * 0.8 + conviction_ratio * 20.0, 0.0), 100.0), 1)
        categories.append({
            "category": category,
            "consensus_score": consensus_score,
            "avg_score": _safe_round(avg_score, 4),
            "avg_percentile": _safe_round(avg_percentile, 2),
            "median_rank": median_rank,
            "stock_count": stock_count,
            "high_conviction_count": high_conviction_count,
            "top_components": [
                {
                    "stock_code": item.get("stock_code"),
                    "stock_name": item.get("stock_name"),
                    "qlib_rank": item.get("qlib_rank"),
                    "qlib_percentile": _safe_round(item.get("qlib_percentile"), 2),
                }
                for item in items[:3]
            ],
            "leading_factor_group": leading_factor_group,
            "model_status": model_status,
            "test_top50_avg_return": summary.get("test_top50_avg_return"),
        })

    categories.sort(
        key=lambda item: (
            -(item.get("consensus_score") or 0.0),
            -(item.get("avg_percentile") or 0.0),
            -(item.get("stock_count") or 0),
            item.get("category") or "",
        )
    )
    category_signal_map = {
        item["category"]: {
            "consensus_score": item.get("consensus_score"),
            "avg_percentile": item.get("avg_percentile"),
            "avg_score": item.get("avg_score"),
            "median_rank": item.get("median_rank"),
            "stock_count": item.get("stock_count"),
            "high_conviction_count": item.get("high_conviction_count"),
            "leading_factor_group": item.get("leading_factor_group"),
            "model_status": item.get("model_status"),
            "test_top50_avg_return": item.get("test_top50_avg_return"),
            "top_components": item.get("top_components") or [],
        }
        for item in categories
    }
    return {
        "available": bool(categories),
        "model_id": model_id,
        "model_status": model_status,
        "signal_date": summary.get("predict_date") or summary.get("finished_at") or summary.get("created_at"),
        "topk": int(topk),
        "source_prediction_count": int(summary.get("prediction_count") or 0),
        "mapped_stock_count": mapped_stock_count,
        "factor_consensus": factor_consensus,
        "leading_factor_group": leading_factor_group,
        "test_top50_avg_return": summary.get("test_top50_avg_return"),
        "categories": categories,
        "category_signal_map": category_signal_map,
    }
