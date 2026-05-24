"""
compute.py — MACD 选股后端计算引擎（多策略版）

职责:
  1. 历史回测指标（win_rate / avg_ret / calmar）缓存到 duckdb
  2. 当前 MACD 状态（刚金叉 / 持仓期 / 即将金叉 / 刚死叉 / 等待）
  3. 合并返回给前端，包括通达信三公式 f1/f3/f5 命中
  4. 支持多策略切换：内置参数、Optuna 最优参数、通达信参数
"""
from __future__ import annotations

from collections import OrderedDict
import csv
from datetime import date
import gzip
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import json

import duckdb
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from execution_model import EXECUTION_MODEL_VERSION, build_fixed_holding_trades, build_sell_rule_trades
from formula_engine import FORMULA_DEFINITIONS, compute_formula_signals
from settings import CACHE_DIR, CACHE_MAX_AGE, MARKET_DB, MAX_WARMUP_WORKERS, SCRIPTS_DIR, SMART_DB


# ---------------------------------------------------------------------------
# 路径与缓存
# ---------------------------------------------------------------------------
HOLDING_PERIODS = [5, 10, 15, 20, 30, 60]  # 多持股期回测天数
MIN_HISTORY_SIGNALS = 5
MIN_EFFECTIVENESS_TRADES = 6
MIN_EFFECTIVENESS_RECENT = 3
MAX_EFFECTIVENESS_RECENT = 5
MAX_EFFECTIVENESS_STALE_DAYS = 252
CHART_FALLBACK_BARS = 252
CHART_QUERY_BARS = 900
CURRENT_QUERY_BARS = 220
ANALYSIS_DIR = Path(__file__).resolve().parent / "analysis"
STOCK_FORMULA_BEST_CSV = ANALYSIS_DIR / "stock_formula_best.csv"
FORMULA_SELL_RULE_AUDIT_CSV = ANALYSIS_DIR / "formula_sell_rule_audit.csv"
UNIFIED_CACHE_SCHEMA = 13
UNIFIED_CACHE_FILE = CACHE_DIR / "cache_unified.json.gz"


# ---------------------------------------------------------------------------
# 业务常量
# ---------------------------------------------------------------------------
CROSS_WINDOW = 5
IMMINENT_DAYS = 5
IMMINENT_GAP = 0.012

S_JUST = "刚金叉"
S_HOLD = "持仓期"
S_IMMIN = "即将金叉"
S_DEATH = "刚死叉"
S_WAIT = "等待"

STATUS_ORDER = {S_JUST: 1, S_IMMIN: 2, S_HOLD: 3, S_DEATH: 4, S_WAIT: 5}
STATUS_COLOR = {
    S_JUST: "green",
    S_IMMIN: "yellow",
    S_HOLD: "blue",
    S_DEATH: "red",
    S_WAIT: "gray",
}


# ---------------------------------------------------------------------------
# 参数说明（高 / 低含义）
# ---------------------------------------------------------------------------
PARAM_DESCRIPTIONS = {
    "macd_fast": {
        "label": "快线周期",
        "desc": "EMA 快线周期，越小越接近现价，反应更快。",
        "low_hint": "更敏感，抓得更早，但噪音更明显。",
        "high_hint": "更平滑，信号更少但更稳定。",
    },
    "macd_slow": {
        "label": "慢线周期",
        "desc": "EMA 慢线周期，定义中期趋势。",
        "low_hint": "更快识别趋势反转，切入更积极。",
        "high_hint": "趋势定义更稳健，反应更慢。",
    },
    "macd_signal": {
        "label": "信号线周期",
        "desc": "DEA 平滑参数，直接决定金叉/死叉密度。",
        "low_hint": "交叉更密，容易出现短线震荡。",
        "high_hint": "交叉更稀，交易更集中。",
    },
    "holding_days": {
        "label": "持股天数",
        "desc": "买入后固定持有周期，控制兑现节奏。",
        "low_hint": "更快回收，降低回撤压力。",
        "high_hint": "更容易吃完整波段，但回撤扩张。",
    },
    "amt_ratio_min": {
        "label": "额比阈值",
        "desc": "金叉日成交额/20 日均额，用于判断主力资金参与度。",
        "low_hint": "入场机会更多，噪音样本上升。",
        "high_hint": "只留更强资金窗口，筛选更严。",
    },
    "price_pos_max": {
        "label": "价格位置上限",
        "desc": "金叉当日价格与近 60 日高点比例，越低说明越低位。",
        "low_hint": "偏向抄底附近，追高风险更小。",
        "high_hint": "临近高位区域，后续上行空间受限。",
    },
}


# ---------------------------------------------------------------------------
# 策略配置
# ---------------------------------------------------------------------------
DEFAULT_PROFILES = {
    "macd_10_22_8_h15": {
        "id": "macd_10_22_8_h15",
        "name": "基准策略 · EMA(10,22,8)",
        "source": "内置基准",
        "macd_fast": 10,
        "macd_slow": 22,
        "macd_signal": 8,
        "holding_days": 15,
        "amt_ratio_min": 1.42,
        "price_pos_max": 0.60,
        "min_signals": MIN_HISTORY_SIGNALS,
    },
}

MACD_COMBOS = {
    "S": (10, 22, 8),
    "M": (12, 26, 9),
    "L": (14, 30, 11),
}

FORMULA_PROFILE_RULES = {
    "f1_hit": {
        "name": "通达信公式一（F1）",
        "desc": "MA5 长期低于 MA90 后突破 MA145，且近 45 天 MA5 持续上行。",
        "low_hint": "放宽时命中更快，适合扩大候选池。",
        "high_hint": "提高严格度可显著减少噪音信号。",
        "rule": (
            "关键条件：MA5 在 MA90 下方 ≥45 天；"
            "最近 10 天 MA5 上涨天数≥7；"
            "10 天内突破 MA145 且价格持续在 MA145 上方。"
        ),
    },
    "f3_hit": {
        "name": "通达信公式三（F3）",
        "desc": "多级买卖信号迭代 + 均线多头排列 + 回撤约束。",
        "low_hint": "减少参数约束后可得到更多“刚形成”的中短线机会。",
        "high_hint": "加强约束可过滤高波动样本，降低假信号。",
        "rule": (
            "关键条件：近 45 天内处于卖出后 3 天以内；"
            "90 天内快速反弹率≥40%；均线多头排列，且回撤在可控区间。"
        ),
    },
    "f5_hit": {
        "name": "通达信公式五（F5）",
        "desc": "连跌后首日止跌回升且 MACD 金叉，DIFF ≥ 0。",
        "low_hint": "条件放宽可提高早期介入机会。",
        "high_hint": "要求严格可提升信号可靠度，但样本会变少。",
        "rule": (
            "关键条件：最近 4 日内由下跌转为上涨；"
            "MACD 发生金叉，且 DIFF 非负。"
        ),
    },
    "formula_any": {
        "name": "公式策略 · 任一命中",
        "desc": "至少一个公式（F1/F3/F5）命中后才保留。",
        "low_hint": "放宽后更容易命中，适合扩大样本。",
        "high_hint": "收紧后更偏向稳定样本。",
        "rule": "条件：F1、F3、F5 任一为真。",
    },
}

FORMULA_PROFILES = {
    "f1_only": {
        "id": "formula_f1",
        "name": "公式策略 · F1 命中",
        "source": "chunky-monkey/screening_engine.py",
        "formula_filter_mode": "single",
        "formula_keys": ("f1_hit",),
        "formula_min_hits": 1,
        "formula_rule_id": "f1_hit",
        "macd_fast": 10,
        "macd_slow": 22,
        "macd_signal": 8,
        "holding_days": 15,
        "amt_ratio_min": 1.0,
        "price_pos_max": 1.0,
        "min_signals": MIN_HISTORY_SIGNALS,
    },
    "f3_only": {
        "id": "formula_f3",
        "name": "公式策略 · F3 命中",
        "source": "chunky-monkey/screening_engine.py",
        "formula_filter_mode": "single",
        "formula_keys": ("f3_hit",),
        "formula_min_hits": 1,
        "formula_rule_id": "f3_hit",
        "macd_fast": 10,
        "macd_slow": 22,
        "macd_signal": 8,
        "holding_days": 15,
        "amt_ratio_min": 1.0,
        "price_pos_max": 1.0,
        "min_signals": MIN_HISTORY_SIGNALS,
    },
    "f5_only": {
        "id": "formula_f5",
        "name": "公式策略 · F5 命中",
        "source": "chunky-monkey/screening_engine.py",
        "formula_filter_mode": "single",
        "formula_keys": ("f5_hit",),
        "formula_min_hits": 1,
        "formula_rule_id": "f5_hit",
        "macd_fast": 10,
        "macd_slow": 22,
        "macd_signal": 8,
        "holding_days": 15,
        "amt_ratio_min": 1.0,
        "price_pos_max": 1.0,
        "min_signals": MIN_HISTORY_SIGNALS,
    },
    "f123_any": {
        "id": "formula_any",
        "name": "公式策略 · F1/F3/F5 任一命中",
        "source": "chunky-monkey/screening_engine.py",
        "formula_filter_mode": "any",
        "formula_keys": ("f1_hit", "f3_hit", "f5_hit"),
        "formula_min_hits": 1,
        "formula_rule_id": "formula_any",
        "macd_fast": 10,
        "macd_slow": 22,
        "macd_signal": 8,
        "holding_days": 15,
        "amt_ratio_min": 1.0,
        "price_pos_max": 1.0,
        "min_signals": MIN_HISTORY_SIGNALS,
    },
}

FORMULA_ENGINE_PROFILES = {
    "formula_gs_pullback_confirm": {
        "id": "formula_gs_pullback_confirm",
        "name": "GS回调确认",
        "source": "bestchoice/formula_engine.py",
        "strategy_type": "tdx_formula",
        "signal_source": "formula",
        "entry_rule": "formula_hit",
        "formula_id": "gs_pullback_confirm",
        "formula_rule_id": "gs_pullback_confirm",
        "macd_fast": 10,
        "macd_slow": 22,
        "macd_signal": 8,
        "holding_days": 15,
        "amt_ratio_min": 0.0,
        "vol_ratio_min": 0.0,
        "price_pos_max": 1.0,
        "min_signals": MIN_HISTORY_SIGNALS,
    },
    "formula_gs_raw_buy": {
        "id": "formula_gs_raw_buy",
        "name": "GS原始买点",
        "source": "bestchoice/formula_engine.py",
        "strategy_type": "tdx_formula",
        "signal_source": "formula",
        "entry_rule": "formula_hit",
        "formula_id": "gs_raw_buy",
        "formula_rule_id": "gs_raw_buy",
        "macd_fast": 10,
        "macd_slow": 22,
        "macd_signal": 8,
        "holding_days": 15,
        "amt_ratio_min": 0.0,
        "vol_ratio_min": 0.0,
        "price_pos_max": 1.0,
        "min_signals": MIN_HISTORY_SIGNALS,
    },
    "formula_ma_base_breakout": {
        "id": "formula_ma_base_breakout",
        "name": "均线筑底突破",
        "source": "bestchoice/formula_engine.py",
        "strategy_type": "tdx_formula",
        "signal_source": "formula",
        "entry_rule": "formula_hit",
        "formula_id": "ma_base_breakout",
        "formula_rule_id": "ma_base_breakout",
        "macd_fast": 10,
        "macd_slow": 22,
        "macd_signal": 8,
        "holding_days": 20,
        "amt_ratio_min": 0.0,
        "vol_ratio_min": 0.0,
        "price_pos_max": 1.0,
        "min_signals": MIN_HISTORY_SIGNALS,
    },
    "formula_activity_breakout": {
        "id": "formula_activity_breakout",
        "name": "活跃度大牛突破",
        "source": "bestchoice/formula_engine.py",
        "strategy_type": "tdx_formula",
        "signal_source": "formula",
        "entry_rule": "formula_hit",
        "formula_id": "activity_breakout",
        "formula_rule_id": "activity_breakout",
        "macd_fast": 10,
        "macd_slow": 22,
        "macd_signal": 8,
        "holding_days": 5,
        "amt_ratio_min": 0.0,
        "vol_ratio_min": 0.0,
        "price_pos_max": 1.0,
        "min_signals": MIN_HISTORY_SIGNALS,
    },
    "formula_volume_base_breakout": {
        "id": "formula_volume_base_breakout",
        "name": "巨量蓄势启动",
        "source": "bestchoice/formula_engine.py",
        "strategy_type": "tdx_formula",
        "signal_source": "formula",
        "entry_rule": "formula_hit",
        "formula_id": "volume_base_breakout",
        "formula_rule_id": "volume_base_breakout",
        "macd_fast": 10,
        "macd_slow": 22,
        "macd_signal": 8,
        "holding_days": 20,
        "amt_ratio_min": 0.0,
        "vol_ratio_min": 0.0,
        "price_pos_max": 1.0,
        "min_signals": MIN_HISTORY_SIGNALS,
    },
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def normalize_code(v: Any) -> str:
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).decode("utf-8", errors="ignore")
    if v is None:
        return ""
    return str(v)


# Cache the latest data date so we don't re-query on every computation
_LATEST_DATA_DATE: Optional[str] = None
_DATA_FRESHNESS: Optional[dict[str, Any]] = None
_DATA_FRESHNESS_TS: float = 0.0
_LATEST_DATA_DATE_LOCK = threading.Lock()
DATA_FRESHNESS_TTL_SECONDS = 30.0
_CACHE_LOCKS: dict[str, threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()
_CURRENT_RAW_CACHE: dict[int, tuple[float, str, dict[str, np.ndarray]]] = {}
_CURRENT_RAW_CACHE_LOCK = threading.Lock()
CURRENT_RAW_CACHE_TTL_SECONDS = 120.0


def _cache_lock(profile_id: str) -> threading.Lock:
    with _CACHE_LOCKS_GUARD:
        lock = _CACHE_LOCKS.get(profile_id)
        if lock is None:
            lock = threading.Lock()
            _CACHE_LOCKS[profile_id] = lock
        return lock


def _require_source_dbs() -> None:
    missing = [str(p) for p in (MARKET_DB, SMART_DB) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "缺少行情数据文件: "
            + ", ".join(missing)
            + "。可通过 BESTCHOICE_CHUNKY_DIR / BESTCHOICE_MARKET_DB / BESTCHOICE_SMART_DB 配置路径。"
        )


def _file_fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    st = path.stat()
    if path.suffix == ".duckdb":
        # DuckDB files can update metadata timestamps during read-only access on
        # some local setups. Data freshness is already covered by
        # latest_data_date, so avoid invalidating strategy caches on mtime churn.
        return {
            "path": str(path),
            "exists": True,
            "size": st.st_size,
        }
    return {
        "path": str(path),
        "exists": True,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }


def _profile_cache_payload(profile: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "strategy_type",
        "macd_fast",
        "macd_slow",
        "macd_signal",
        "holding_days",
        "vol_ratio_min",
        "amt_ratio_min",
        "price_pos_max",
        "dif_positive",
        "min_signals",
        "signal_source",
        "entry_rule",
        "formula_filter_mode",
        "formula_keys",
        "formula_min_hits",
        "formula_rule_id",
        "formula_id",
        "formula_params",
        "execution_model_version",
    )
    return {k: profile.get(k) for k in keys if k in profile}


def _cache_signature(profile: dict[str, Any]) -> str:
    payload = {
        "schema": 13,
        "profile": _profile_cache_payload(profile),
        "holding_periods": HOLDING_PERIODS,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "market_db": _file_fingerprint(MARKET_DB),
        "smart_db": _file_fingerprint(SMART_DB),
        "latest_data_date": get_latest_data_date(),
        "optuna_csv": _file_fingerprint(SCRIPTS_DIR / "macd_optuna_top10.csv"),
        "golden_csv": _file_fingerprint(SCRIPTS_DIR / "macd_gcross_holding_period_summary.csv"),
        "stock_formula_best": _file_fingerprint(STOCK_FORMULA_BEST_CSV)
        if profile.get("signal_source") == "formula"
        else None,
        "formula_sell_rule_audit": _file_fingerprint(FORMULA_SELL_RULE_AUDIT_CSV)
        if profile.get("signal_source") == "formula"
        else None,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _unified_cache_signature(profile_ids: list[str], profiles: dict[str, dict[str, Any]]) -> str:
    payload = {
        "schema": UNIFIED_CACHE_SCHEMA,
        "profile_ids": profile_ids,
        "profile_signatures": {
            pid: _cache_signature(profiles[pid])
            for pid in profile_ids
            if pid in profiles
        },
        "latest_data_date": get_latest_data_date(),
        "freshness": get_data_freshness(),
        "stock_formula_best": _file_fingerprint(STOCK_FORMULA_BEST_CSV),
        "formula_sell_rule_audit": _file_fingerprint(FORMULA_SELL_RULE_AUDIT_CSV),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_unified_cache(
    profile_ids: list[str],
    profiles: dict[str, dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if not UNIFIED_CACHE_FILE.exists():
        return None
    if (time.time() - UNIFIED_CACHE_FILE.stat().st_mtime) >= CACHE_MAX_AGE:
        return None
    try:
        expected = _unified_cache_signature(profile_ids, profiles)
        with gzip.open(UNIFIED_CACHE_FILE, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("signature") != expected:
            return None
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("ready"):
            return None
        return data
    except Exception:
        return None


def _save_unified_cache(
    data: dict[str, Any],
    profile_ids: list[str],
    profiles: dict[str, dict[str, Any]],
) -> None:
    try:
        tmp = UNIFIED_CACHE_FILE.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp.gz")
        payload = {
            "signature": _unified_cache_signature(profile_ids, profiles),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "data": data,
        }
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, UNIFIED_CACHE_FILE)
    except Exception:
        try:
            if "tmp" in locals() and tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def get_data_freshness(force: bool = False) -> dict[str, Any]:
    global _DATA_FRESHNESS, _DATA_FRESHNESS_TS, _LATEST_DATA_DATE
    now = time.time()
    if (
        not force
        and _DATA_FRESHNESS is not None
        and now - _DATA_FRESHNESS_TS < DATA_FRESHNESS_TTL_SECONDS
    ):
        return _DATA_FRESHNESS
    with _LATEST_DATA_DATE_LOCK:
        now = time.time()
        if (
            not force
            and _DATA_FRESHNESS is not None
            and now - _DATA_FRESHNESS_TS < DATA_FRESHNESS_TTL_SECONDS
        ):
            return _DATA_FRESHNESS
        try:
            _require_source_dbs()
            con = duckdb.connect(str(MARKET_DB), read_only=True)
            rows = con.execute(
                """
                WITH daily AS (
                    SELECT date, COUNT(*) AS n
                    FROM v_price_kline_qfq
                    GROUP BY date
                ),
                stats AS (
                    SELECT MAX(n) AS max_n FROM daily
                )
                SELECT d.date, d.n, d.n::DOUBLE / NULLIF(s.max_n, 0) AS coverage
                FROM daily d
                CROSS JOIN stats s
                ORDER BY d.date DESC
                LIMIT 10
                """
            ).fetchall()
            con.close()
            latest = rows[0] if rows else None
            coverage_row = next((r for r in rows if float(r[2] or 0) >= 0.95), latest)
            _DATA_FRESHNESS = {
                "latest_data_date": str(coverage_row[0]) if coverage_row else "未知",
                "global_latest_data_date": str(latest[0]) if latest else "未知",
                "global_latest_stock_count": int(latest[1]) if latest else 0,
                "coverage_latest_stock_count": int(coverage_row[1]) if coverage_row else 0,
                "coverage_latest_ratio": round(float(coverage_row[2] or 0), 4) if coverage_row else 0.0,
            }
            _DATA_FRESHNESS_TS = now
            _LATEST_DATA_DATE = _DATA_FRESHNESS["latest_data_date"]
        except Exception as e:
            if _DATA_FRESHNESS is not None:
                _DATA_FRESHNESS = {
                    **_DATA_FRESHNESS,
                    "freshness_error": str(e),
                }
            else:
                _DATA_FRESHNESS = {
                    "latest_data_date": "未知",
                    "global_latest_data_date": "未知",
                    "global_latest_stock_count": 0,
                    "coverage_latest_stock_count": 0,
                    "coverage_latest_ratio": 0.0,
                    "freshness_error": str(e),
                }
                _LATEST_DATA_DATE = "未知"
            _DATA_FRESHNESS_TS = now
    return _DATA_FRESHNESS


def invalidate_data_freshness() -> None:
    global _DATA_FRESHNESS, _DATA_FRESHNESS_TS, _LATEST_DATA_DATE
    with _LATEST_DATA_DATE_LOCK:
        _DATA_FRESHNESS = None
        _DATA_FRESHNESS_TS = 0.0
        _LATEST_DATA_DATE = None


def get_latest_data_date() -> str:
    """Return the latest market date with broad stock coverage."""
    global _LATEST_DATA_DATE
    if _LATEST_DATA_DATE is not None:
        return _LATEST_DATA_DATE
    _LATEST_DATA_DATE = get_data_freshness()["latest_data_date"]
    return _LATEST_DATA_DATE


def _attach_smart_db(con) -> None:
    """Attach smartmoney.duckdb as 'sm'. Safe to call from multiple threads —
    DuckDB in-process connections share the catalog, so 'sm' might already
    be attached by another thread."""
    try:
        con.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")
    except Exception as e:
        if "already exists" not in str(e).lower():
            raise  # real error, propagate


def _to_float(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _to_int(v: Any, default: int = 0) -> int:
    x = _to_float(v)
    return default if x is None else int(x)


def _to_bool(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return bool(int(v))
    s = str(v).strip().lower()
    return s in {"1", "true", "t", "y", "yes", "是", "命中"}


def ema_np(arr: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    c = 1.0 - alpha
    out = np.empty(len(arr), dtype=np.float64)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + c * out[i - 1]
    return out


def sma_np(arr: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(arr), np.nan, dtype=np.float64)
    if len(arr) >= window:
        kernel = np.ones(window, dtype=np.float64) / window
        out[window - 1 :] = np.convolve(arr, kernel, mode="valid")
    return out


def rolling_max_np(arr: np.ndarray, window: int) -> np.ndarray:
    padded = np.pad(arr.astype(np.float64), (window - 1, 0), mode="edge")
    return sliding_window_view(padded, window).max(axis=1)


# ---------------------------------------------------------------------------
# 当前 MACD 状态
# ---------------------------------------------------------------------------
def current_status(dif: np.ndarray, dea: np.ndarray, close: np.ndarray) -> tuple[str, Optional[int], float]:
    n = len(dif)
    if n < 3:
        return S_WAIT, None, 0.0

    gap_arr = dif - dea
    gap_now = float(gap_arr[-1])

    if gap_now > 0:
        for i in range(1, min(n, 30)):
            if gap_arr[-(i + 1)] <= 0:
                if i <= CROSS_WINDOW:
                    return S_JUST, i, gap_now
                return S_HOLD, i, gap_now
        return S_HOLD, None, gap_now

    for i in range(1, min(n, 10)):
        if gap_arr[-(i + 1)] >= 0:
            if i <= CROSS_WINDOW:
                return S_DEATH, i, gap_now
            break

    if n >= 3:
        rate = float(gap_arr[-1] - gap_arr[-2])
        rate2 = float(gap_arr[-2] - gap_arr[-3])
        converging = rate > 0 and rate2 > 0
        gap_ratio = abs(gap_now) / max(abs(float(close[-1])), 0.001)
        if converging and gap_ratio < IMMINENT_GAP:
            days_est = int(-gap_now / rate) if rate > 1e-8 else 99
            if days_est <= IMMINENT_DAYS:
                return S_IMMIN, days_est, gap_now

    return S_WAIT, None, gap_now


# ---------------------------------------------------------------------------
# 策略加载
# ---------------------------------------------------------------------------
def _parse_optuna_profile() -> Optional[dict[str, Any]]:
    params_path = SCRIPTS_DIR / "macd_optuna_best_params.json"
    if params_path.exists():
        try:
            params = json.loads(params_path.read_text(encoding="utf-8"))
            combo_key = str(params.get("macd_combo", "S")).strip().upper() or "S"
            fast = _to_int(params.get("macd_fast"), MACD_COMBOS.get(combo_key, (10, 22, 8))[0])
            slow = _to_int(params.get("macd_slow"), MACD_COMBOS.get(combo_key, (10, 22, 8))[1])
            signal = _to_int(params.get("macd_signal"), MACD_COMBOS.get(combo_key, (10, 22, 8))[2])
            return {
                "id": "optuna_best",
                "name": f"Optuna 最优 · EMA({fast}/{slow}/{signal})",
                "source": "scripts/macd_optuna_best_params.json",
                "strategy_type": "macd_optuna",
                "macd_fast": fast,
                "macd_slow": slow,
                "macd_signal": signal,
                "holding_days": _to_int(params.get("holding_days"), 15),
                "vol_ratio_min": _to_float(params.get("vol_ratio_min")) or 1.0,
                "amt_ratio_min": _to_float(params.get("amt_ratio_min")) or 1.5,
                "price_pos_max": _to_float(params.get("price_pos_max")) or 0.60,
                "dif_positive": bool(params.get("dif_positive", False)),
                "min_signals": MIN_HISTORY_SIGNALS,
                "optuna_score": _to_float(params.get("score")) or 0.0,
                "optuna_n": _to_int(params.get("signal_count"), 0),
                "formula_rule_id": "optuna_macd",
            }
        except Exception:
            pass

    csv_path = SCRIPTS_DIR / "macd_optuna_top10.csv"
    if not csv_path.exists():
        return None

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None

    def score_fn(r: dict[str, str]) -> float:
        calmar = _to_float(r.get("calmar"))
        score = _to_float(r.get("score"))
        if score is not None:
            return score
        if calmar is not None:
            return calmar
        return 0.0

    row = max(rows, key=score_fn)
    combo_key = str(row.get("macd_combo", "S")).strip().upper() or "S"
    fast, slow, signal = MACD_COMBOS.get(combo_key, (10, 22, 8))

    return {
        "id": "optuna_best",
        "name": f"Optuna 最优 · EMA({fast}/{slow}/{signal})",
        "source": "chunkymonkey/macd_optuna_top10.csv",
        "strategy_type": "macd_optuna",
        "macd_fast": fast,
        "macd_slow": slow,
        "macd_signal": signal,
        "holding_days": _to_int(row.get("holding_days"), 15),
        "vol_ratio_min": _to_float(row.get("avg_vol_r20")) or 1.0,
        "amt_ratio_min": _to_float(row.get("avg_amt_r20")) or 1.5,
        "price_pos_max": _to_float(row.get("avg_price60")) or 0.60,
        "dif_positive": False,
        "min_signals": MIN_HISTORY_SIGNALS,
        "optuna_score": _to_float(row.get("score")) or 0.0,
        "optuna_n": _to_int(row.get("n"), 0),
        "formula_rule_id": "optuna_macd",
    }


def _parse_golden_profile() -> Optional[dict[str, Any]]:
    summary = SCRIPTS_DIR / "macd_gcross_holding_period_summary.csv"
    if not summary.exists():
        return None

    best_holding = 15
    best_calmar = None

    with summary.open("r", encoding="utf-8", newline="") as f:
        rows = csv.DictReader(f)
        for r in rows:
            cal = _to_float(r.get("median_calmar"))
            if cal is None:
                continue
            if best_calmar is None or cal > best_calmar:
                best_calmar = cal
                best_holding = _to_int(r.get("holding_days"), best_holding)

    if best_calmar is None:
        return None

    return {
        "id": "tdx_12_26_9",
        "name": "通达信参数 · EMA(12,26,9)",
        "source": "chunkymonkey/macd_gcross_holding_period_summary.csv",
        "strategy_type": "macd_golden",
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "holding_days": best_holding,
        "amt_ratio_min": 1.0,
        "price_pos_max": 0.70,
        "min_signals": MIN_HISTORY_SIGNALS,
        "formula_rule_id": "golden_cross",
        "best_calmar": best_calmar,
    }


def _passes_formula_filter(hits: dict[str, bool], profile: dict[str, Any]) -> bool:
    mode = profile.get("formula_filter_mode")
    if not mode:
        return True

    keys = tuple(profile.get("formula_keys") or ())
    if not keys:
        return True

    values = [bool(hits.get(k, False)) for k in keys]
    if mode == "single":
        # keys 通常只有一个
        return any(values)

    if mode == "all":
        return all(values)

    if mode == "count":
        min_hits = int(profile.get("formula_min_hits", 1))
        return sum(values) >= min_hits

    # any
    return any(values)


def get_strategy_profiles() -> dict[str, dict[str, Any]]:
    profiles: OrderedDict[str, dict[str, Any]] = OrderedDict()

    # 先放置基准 + MACD 三套常用参数
    profiles.update({k: dict(v) for k, v in DEFAULT_PROFILES.items()})

    for name, (f, s, sig) in MACD_COMBOS.items():
        pid = f"macd_{f}_{s}_{sig}"
        if pid not in profiles:
            profiles[pid] = {
                "id": pid,
                "name": f"参数组 {name} · EMA({f},{s},{sig})",
                "source": "chunkymonkey 常规参数",
                "macd_fast": f,
                "macd_slow": s,
                "macd_signal": sig,
                "holding_days": 15,
                "amt_ratio_min": 1.42,
                "price_pos_max": 0.60,
                "min_signals": MIN_HISTORY_SIGNALS,
            }

    for k, v in FORMULA_PROFILES.items():
        if v["id"] not in profiles:
            profiles[v["id"]] = dict(v)

    for k, v in FORMULA_ENGINE_PROFILES.items():
        if v["id"] not in profiles:
            profiles[v["id"]] = dict(v)

    # 再读取 Optuna 与通达信参数，用于“更好的买入时机/持股周期”探索
    opt = _parse_optuna_profile()
    if opt is not None:
        profiles[opt["id"]] = opt

    gdx = _parse_golden_profile()
    if gdx is not None:
        profiles[gdx["id"]] = gdx

    return dict(profiles)


def get_default_profile_id(profiles: dict[str, dict[str, Any]]) -> str:
    if "tdx_12_26_9" in profiles:
        return "tdx_12_26_9"
    if "optuna_best" in profiles:
        return "optuna_best"
    if "macd_10_22_8_h15" in profiles:
        return "macd_10_22_8_h15"
    return next(iter(profiles.keys()))


def _safe_cache_path(profile_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in {"_", "-"} else "_" for c in profile_id)
    return CACHE_DIR / f"cache_{safe}.duckdb"


def _cache_fresh(profile_id: str, profile: dict[str, Any]) -> bool:
    p = _safe_cache_path(profile_id)
    if not p.exists():
        return False
    if (time.time() - p.stat().st_mtime) >= CACHE_MAX_AGE:
        return False
    # Invalidate cache if it predates the multi-horizon schema
    try:
        con = duckdb.connect(str(p), read_only=True)
        info = con.execute("PRAGMA table_info(hist_metrics)").fetchall()
        col_names = {r[1] for r in info}
        required_cols = {"horizons_json", "effectiveness_json", "execution_json", "trade_series_json"}
        if not required_cols.issubset(col_names):
            con.close()
            return False
        try:
            row = con.execute("SELECT value FROM cache_manifest WHERE key = 'signature'").fetchone()
        except Exception:
            con.close()
            return False
        con.close()
        return bool(row and row[0] == _cache_signature(profile))
    except Exception:
        return False


def _load_cache(profile_id: str, include_trade_series: bool = True) -> dict[str, dict[str, Any]]:
    db = _safe_cache_path(profile_id)
    con = duckdb.connect(str(db), read_only=True)
    try:
        info = con.execute("PRAGMA table_info(hist_metrics)").fetchall()
    except Exception:
        con.close()
        return {}
    col_names = {r[1] for r in info}
    has_status   = "history_status"   in col_names
    has_best_hp  = "best_holding_days" in col_names
    has_horizons = "horizons_json"     in col_names
    has_effect   = "effectiveness_json" in col_names
    has_exec     = "execution_json"      in col_names
    has_series   = "trade_series_json"  in col_names

    cols = "code, signal_count, win_rate, avg_ret, avg_dd, calmar"
    if has_status:   cols += ", history_status"
    if has_best_hp:  cols += ", best_holding_days"
    if has_horizons: cols += ", horizons_json"
    if has_effect:   cols += ", effectiveness_json"
    if has_exec:     cols += ", execution_json"
    if has_series and include_trade_series:
        cols += ", trade_series_json"

    rows = con.execute(f"SELECT {cols} FROM hist_metrics").fetchall()
    con.close()

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        i = 0
        code = normalize_code(row[i]); i += 1
        sc   = row[i];                 i += 1
        wr   = row[i];                 i += 1
        ar   = row[i];                 i += 1
        ad   = row[i];                 i += 1
        cal  = row[i];                 i += 1
        status   = row[i] if has_status   else "ok"; i += 1 if has_status   else 0
        best_hp  = row[i] if has_best_hp  else None; i += 1 if has_best_hp  else 0
        hjson    = row[i] if has_horizons else "{}"; i += 1 if has_horizons else 0
        ejson    = row[i] if has_effect   else "{}"; i += 1 if has_effect else 0
        xjson = "{}"
        sjson = "[]"
        if has_exec:
            xjson = row[i]
            i += 1
        if has_series and include_trade_series:
            sjson = row[i]
            i += 1
        try:
            horizons = {int(k): v for k, v in json.loads(hjson or "{}").items()}
        except Exception:
            horizons = {}
        try:
            effectiveness = json.loads(ejson or "{}")
        except Exception:
            effectiveness = {}
        try:
            execution = json.loads(xjson or "{}")
        except Exception:
            execution = {}
        if include_trade_series:
            try:
                trade_series = json.loads(sjson or "[]")
            except Exception:
                trade_series = []
        else:
            trade_series = []
        out[code] = {
            "signal_count":     int(sc),
            "win_rate":         _to_float(wr),
            "avg_ret":          _to_float(ar),
            "avg_dd":           _to_float(ad),
            "calmar":           _to_float(cal),
            "history_status":   status or "ok",
            "best_holding_days": int(best_hp) if best_hp is not None else None,
            "horizons":         horizons,
            "effectiveness":     effectiveness,
            "execution":         execution,
            "trade_series":      trade_series,
        }
    return out


def _save_cache(profile_id: str, profile: dict[str, Any], metrics: dict[str, dict[str, Any]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    db = _safe_cache_path(profile_id)
    tmp = db.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp.duckdb")
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    con.execute("DROP TABLE IF EXISTS hist_metrics")
    con.execute(
        """
        CREATE TABLE hist_metrics (
            code              VARCHAR PRIMARY KEY,
            signal_count      INTEGER,
            win_rate          DOUBLE,
            avg_ret           DOUBLE,
            avg_dd            DOUBLE,
            calmar            DOUBLE,
            history_status    VARCHAR,
            best_holding_days INTEGER,
            horizons_json     TEXT,
            effectiveness_json TEXT,
            execution_json TEXT,
            trade_series_json TEXT
        )
        """
    )
    rows = []
    for code, v in metrics.items():
        h_raw = v.get("horizons") or {}
        rows.append((
            code,
            int(v.get("signal_count", 0)),
            _to_float(v.get("win_rate")),
            _to_float(v.get("avg_ret")),
            _to_float(v.get("avg_dd")),
            _to_float(v.get("calmar")),
            str(v.get("history_status", "ok") or "ok"),
            int(v.get("best_holding_days") or 15),
            json.dumps({str(k): val for k, val in h_raw.items()}),
            json.dumps(v.get("effectiveness") or {}, ensure_ascii=False),
            json.dumps(v.get("execution") or {}, ensure_ascii=False),
            json.dumps(v.get("trade_series") or [], ensure_ascii=False),
        ))
    if rows:
        con.executemany("INSERT INTO hist_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("DROP TABLE IF EXISTS cache_manifest")
    con.execute("CREATE TABLE cache_manifest (key VARCHAR PRIMARY KEY, value TEXT)")
    con.executemany(
        "INSERT INTO cache_manifest VALUES (?, ?)",
        [
            ("signature", _cache_signature(profile)),
            ("created_at", time.strftime("%Y-%m-%d %H:%M:%S")),
            ("profile_id", profile["id"]),
        ],
    )
    con.close()
    os.replace(tmp, db)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _slope(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    x = np.arange(len(values), dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    return float(np.polyfit(x, y, 1)[0])


def _trade_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    rets = np.asarray([float(t["ret"]) for t in rows], dtype=np.float64)
    dds = np.asarray([float(t["max_dd"]) for t in rows], dtype=np.float64)
    avg_ret = float(np.mean(rets))
    avg_dd = float(np.mean(dds))
    return {
        "n": len(rows),
        "avg_ret": avg_ret,
        "win_rate": float(np.mean(rets > 0)),
        "avg_dd": avg_dd,
        "calmar": avg_ret / max(abs(avg_dd), 0.005),
    }


def _effectiveness_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    trades = sorted(trades, key=lambda t: t.get("buy_date") or "")
    total_n = len(trades)
    last_buy_date = trades[-1].get("buy_date") if trades else None
    if total_n < MIN_EFFECTIVENESS_TRADES:
        return {
            "score": None,
            "label": "样本不足",
            "total_n": total_n,
            "recent_n": 0,
            "last_buy_date": last_buy_date,
        }

    recent_n = min(MAX_EFFECTIVENESS_RECENT, max(MIN_EFFECTIVENESS_RECENT, total_n // 3))
    recent = trades[-recent_n:]
    prior = trades[:-recent_n]
    if len(prior) < MIN_EFFECTIVENESS_RECENT:
        return {
            "score": None,
            "label": "样本不足",
            "total_n": total_n,
            "recent_n": recent_n,
            "last_buy_date": last_buy_date,
        }

    prior_s = _trade_stats(prior)
    recent_s = _trade_stats(recent)
    prior_avg_ret = float(prior_s["avg_ret"])
    prior_win_rate = float(prior_s["win_rate"])
    prior_avg_dd = float(prior_s["avg_dd"])
    prior_calmar = float(prior_s["calmar"])
    recent_avg_ret = float(recent_s["avg_ret"])
    recent_win_rate = float(recent_s["win_rate"])
    recent_avg_dd = float(recent_s["avg_dd"])
    recent_calmar = float(recent_s["calmar"])

    recent_rets = [float(t["ret"]) for t in recent]
    trimmed_recent = list(recent_rets)
    trimmed_recent.remove(max(trimmed_recent))
    trimmed_recent_avg = float(np.mean(trimmed_recent)) if trimmed_recent else None
    trimmed_recent_win = float(np.mean(np.asarray(trimmed_recent) > 0)) if trimmed_recent else None
    robust = bool(
        trimmed_recent_avg is not None
        and trimmed_recent_avg > prior_avg_ret
        and (trimmed_recent_win or 0.0) >= max(0.5, prior_win_rate - 0.05)
    )

    positive_sum = sum(v for v in recent_rets if v > 0)
    outlier_share = max(recent_rets) / positive_sum if positive_sum > 0 else 1.0
    ret_delta = recent_avg_ret - prior_avg_ret
    win_delta = recent_win_rate - prior_win_rate
    dd_delta = recent_avg_dd - prior_avg_dd
    calmar_delta = recent_calmar - prior_calmar
    ret_slope = _slope([float(t["ret"]) for t in trades])
    dd_slope = _slope([float(t["max_dd"]) for t in trades])

    score = (
        30 * _clamp01((ret_delta + 0.02) / 0.10)
        + 20 * _clamp01((win_delta + 0.05) / 0.30)
        + 20 * _clamp01((dd_delta + 0.02) / 0.08)
        + 20 * _clamp01((calmar_delta + 0.30) / 2.30)
        + 10 * _clamp01((ret_slope + 0.005) / 0.025)
    )
    if outlier_share > 0.72:
        score -= 12
    if not robust:
        score -= 10
    score = round(max(0.0, min(100.0, score)), 1)

    if score >= 70 and robust and ret_delta > 0 and win_delta >= -0.01 and dd_delta >= -0.01:
        label = "增强中"
    elif score >= 58 and ret_delta > 0 and dd_delta >= -0.02:
        label = "改善观察"
    elif ret_delta < -0.02 or dd_delta < -0.03:
        label = "退化中"
    else:
        label = "稳定/不明显"

    stale_days = None
    latest_data_date = get_latest_data_date()
    try:
        if latest_data_date and last_buy_date:
            stale_days = (date.fromisoformat(str(latest_data_date)) - date.fromisoformat(str(last_buy_date))).days
    except Exception:
        stale_days = None
    is_stale = stale_days is not None and stale_days > MAX_EFFECTIVENESS_STALE_DAYS
    if is_stale:
        label = "样本陈旧"

    return {
        "score": score,
        "label": label,
        "total_n": total_n,
        "recent_n": recent_n,
        "last_buy_date": last_buy_date,
        "stale_days": stale_days,
        "is_stale": is_stale,
        "recent_avg_ret": round(recent_avg_ret, 4),
        "recent_win_rate": round(recent_win_rate, 4),
        "recent_avg_dd": round(recent_avg_dd, 4),
        "recent_calmar": round(recent_calmar, 4),
        "prior_avg_ret": round(prior_avg_ret, 4),
        "prior_win_rate": round(prior_win_rate, 4),
        "prior_avg_dd": round(prior_avg_dd, 4),
        "prior_calmar": round(prior_calmar, 4),
        "ret_delta": round(ret_delta, 4),
        "win_delta": round(win_delta, 4),
        "dd_delta": round(dd_delta, 4),
        "calmar_delta": round(calmar_delta, 4),
        "ret_slope": round(ret_slope, 4),
        "dd_slope": round(dd_slope, 4),
        "trimmed_recent_avg_ret": round(trimmed_recent_avg, 4) if trimmed_recent_avg is not None else None,
        "outlier_share": round(outlier_share, 4),
        "robust_after_drop_best": robust,
    }


def _execution_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(trades)
    if total == 0:
        return {
            "total_signals": 0,
            "completed_trades": 0,
            "skipped_buys": 0,
            "pending_buys": 0,
            "delayed_buys": 0,
            "delayed_sells": 0,
            "untradable_events": 0,
            "completion_rate": None,
            "skipped_buy_rate": None,
            "untradable_rate": None,
        }
    completed = sum(1 for t in trades if t.get("ret") is not None)
    skipped_buys = sum(1 for t in trades if t.get("skipped") or (t.get("buy_block_reason") and not t.get("buy_date")))
    pending_buys = sum(1 for t in trades if t.get("pending_buy"))
    delayed_buys = sum(1 for t in trades if int(t.get("delay_buy_days") or 0) > 0 and t.get("buy_date"))
    delayed_sells = sum(1 for t in trades if int(t.get("delay_sell_days") or 0) > 0)
    untradable_events = skipped_buys + delayed_buys + delayed_sells
    return {
        "total_signals": total,
        "completed_trades": completed,
        "skipped_buys": skipped_buys,
        "pending_buys": pending_buys,
        "delayed_buys": delayed_buys,
        "delayed_sells": delayed_sells,
        "untradable_events": untradable_events,
        "completion_rate": round(completed / total, 4),
        "skipped_buy_rate": round(skipped_buys / total, 4),
        "untradable_rate": round(untradable_events / total, 4),
    }


# ---------------------------------------------------------------------------
# 历史回测（带缓存）
# ---------------------------------------------------------------------------
def compute_historical(profile: dict[str, Any], progress_cb=None) -> dict[str, dict[str, Any]]:
    profile_id = profile["id"]
    cache_pid = profile_id
    lock = _cache_lock(cache_pid)
    with lock:
        if _cache_fresh(cache_pid, profile):
            return _load_cache(cache_pid, include_trade_series=False)

        _require_source_dbs()
        mkt = duckdb.connect(str(MARKET_DB), read_only=True)
        try:
            try:
                _attach_smart_db(mkt)
                raw = mkt.execute(
                    """
                    SELECT k.code, k.date, k.open, k.high, k.low, k.close, k.volume, k.amount
                    FROM v_price_kline_qfq k
                    INNER JOIN sm.dim_active_a_stock s ON k.code = s.stock_code AND s.stock_name NOT LIKE 'ST%' AND s.stock_name NOT LIKE '*ST%' -- rule-compliance: ok evidence=bc_absorbed Phase 2.2 universe wire ST filter
                    ORDER BY k.code, k.date
                    """
                ).fetchnumpy()
            except duckdb.IOException:
                raw = mkt.execute(
                    """
                    SELECT code, date, open, high, low, close, volume, amount
                    FROM v_price_kline_qfq
                    ORDER BY code, date
                    """
                ).fetchnumpy()
        finally:
            mkt.close()

    if len(raw["code"]) == 0:
        _save_cache(cache_pid, profile, {})
        return {}

    codes   = raw["code"]
    dates_all = raw["date"]
    opens   = raw["open"].astype(np.float64)
    highs   = raw["high"].astype(np.float64)
    closes  = raw["close"].astype(np.float64)
    lows    = raw["low"].astype(np.float64)
    volumes = raw["volume"].astype(np.float64)
    amounts = raw["amount"].astype(np.float64)

    unique_codes, counts = np.unique(codes, return_counts=True)
    n_total = len(unique_codes)

    fast = int(profile["macd_fast"])
    slow = int(profile["macd_slow"])
    sig  = int(profile["macd_signal"])
    hp   = int(profile["holding_days"])
    min_signals = int(profile.get("min_signals", 1))
    formula_id = profile.get("formula_id") if profile.get("signal_source") == "formula" else None
    stock_formula_best = _load_stock_formula_best() if formula_id else {}

    all_periods = sorted(set(HOLDING_PERIODS + [hp]))
    warmup = slow + sig + max(all_periods) + 2
    if formula_id:
        warmup = max(warmup, 220)
    metrics: dict[str, dict[str, Any]] = {}

    idx = 0
    for ci, (code_raw, cnt) in enumerate(zip(unique_codes, counts)):
        code = normalize_code(code_raw)
        sl   = slice(idx, idx + cnt)
        cls  = closes[sl]
        dates = dates_all[sl]
        op   = opens[sl]
        hi   = highs[sl]
        lo   = lows[sl]
        vol  = volumes[sl]
        amt  = amounts[sl]
        n    = len(cls)
        stock_best = stock_formula_best.get((code, str(formula_id))) if formula_id else None
        if formula_id and stock_best is None:
            metrics[code] = {
                "signal_count": 0,
                "win_rate": None,
                "avg_ret": None,
                "avg_dd": None,
                "calmar": None,
                "history_status": "missing_optimized_result",
                "history_source": "formula_parameter_search",
                "optimization_missing_reason": "缺少每股公式参数寻优结果，未使用默认参数回退",
                "best_holding_days": None,
                "horizons": {},
                "effectiveness": {"score": None, "label": "缺少寻优结果", "total_n": 0, "recent_n": 0},
                "execution": _execution_metrics([]),
                "trade_series": [],
            }
            idx += cnt
            continue
        stock_hp = int((stock_best or {}).get("holding_days") or hp)
        stock_periods = sorted(set(HOLDING_PERIODS + [stock_hp]))

        if n < warmup:
            metrics[code] = {
                "signal_count": 0, "win_rate": None, "avg_ret": None,
                "avg_dd": None, "calmar": None,
                "history_status": "insufficient_history",
                "best_holding_days": stock_hp, "horizons": {},
                "effectiveness": {"score": None, "label": "样本不足", "total_n": 0, "recent_n": 0},
                "execution": _execution_metrics([]),
                "trade_series": [],
            }
            idx += cnt
            continue

        amt_ma20 = sma_np(amt, 20)
        vol_ma20 = sma_np(vol, 20)
        max60_arr = rolling_max_np(cls, 60)

        if formula_id:
            try:
                formula_params = dict((stock_best or {}).get("params") or profile.get("formula_params") or {})
                formula_out = compute_formula_signals(
                    str(formula_id),
                    open_=op,
                    high=hi,
                    low=lo,
                    close=cls,
                    volume=vol,
                    amount=amt,
                    params=formula_params,
                )
                sig_idxs = np.where(formula_out["entry"])[0]
                exit_signals = np.asarray(formula_out.get("exit", np.zeros(n, dtype=bool)), dtype=bool)
            except Exception:
                sig_idxs = np.array([], dtype=np.int64)
                exit_signals = np.zeros(n, dtype=bool)
        else:
            dif = ema_np(cls, fast) - ema_np(cls, slow)
            dea = ema_np(dif, sig)
            cross = (dif[:-1] < dea[:-1]) & (dif[1:] > dea[1:])
            sig_idxs = np.where(cross)[0] + 1
            exit_signals = np.zeros(n, dtype=bool)

        h_rets: dict[int, list] = {h: [] for h in stock_periods}
        h_dds:  dict[int, list] = {h: [] for h in stock_periods}
        h_trades: dict[int, list[dict[str, Any]]] = {h: [] for h in stock_periods}
        raw_h_rets: dict[int, list] = {h: [] for h in stock_periods}
        raw_h_dds: dict[int, list] = {h: [] for h in stock_periods}
        raw_h_trades: dict[int, list[dict[str, Any]]] = {h: [] for h in stock_periods}
        raw_h_execution: dict[int, dict[str, Any]] = {h: _execution_metrics([]) for h in stock_periods}

        stock_sell_rule = (stock_best or {}).get("sell_rule") or f"fixed_{stock_hp}"
        if formula_id:
            raw_trade_map = {
                stock_hp: build_sell_rule_trades(
                    code=code,
                    dates=dates,
                    opens=op,
                    highs=hi,
                    lows=lo,
                    closes=cls,
                    volumes=vol,
                    amounts=amt,
                    signal_indices=sig_idxs,
                    sell_rule=str(stock_sell_rule),
                    exit_signals=exit_signals,
                    include_open=False,
                )
            }
        else:
            raw_trade_map = build_fixed_holding_trades(
                code=code,
                dates=dates,
                opens=op,
                highs=hi,
                lows=lo,
                closes=cls,
                volumes=vol,
                amounts=amt,
                signal_indices=sig_idxs,
                holding_periods=stock_periods,
                include_open=False,
            )
        raw_h_execution = {h: _execution_metrics(trades) for h, trades in raw_trade_map.items()}
        for h, trades in raw_trade_map.items():
            for t in trades:
                if t.get("ret") is None:
                    continue
                raw_h_rets[h].append(float(t["ret"]))
                raw_h_dds[h].append(float(t.get("max_dd") or 0.0))
                raw_h_trades[h].append(t)

        filtered_sig_idxs = []
        if formula_id:
            filtered_sig_idxs = [int(si) for si in sig_idxs]
        else:
            for si in sig_idxs:
                if (
                    vol_ma20[si] <= 0
                    or np.isnan(vol_ma20[si])
                    or amt_ma20[si] <= 0
                    or np.isnan(amt_ma20[si])
                    or max60_arr[si] <= 0
                ):
                    continue
                vol_r20 = float(vol[si] / vol_ma20[si])
                amt_r20 = float(amt[si] / amt_ma20[si])
                price60 = float(cls[si] / max60_arr[si])
                if (
                    vol_r20 < float(profile.get("vol_ratio_min", 1.0))
                    or amt_r20 < float(profile.get("amt_ratio_min", 1.0))
                    or price60 > float(profile.get("price_pos_max", 1.0))
                    or (profile.get("dif_positive") and float(dif[si]) <= 0)
                ):
                    continue
                filtered_sig_idxs.append(int(si))

        if formula_id:
            trade_map = {
                stock_hp: build_sell_rule_trades(
                    code=code,
                    dates=dates,
                    opens=op,
                    highs=hi,
                    lows=lo,
                    closes=cls,
                    volumes=vol,
                    amounts=amt,
                    signal_indices=filtered_sig_idxs,
                    sell_rule=str(stock_sell_rule),
                    exit_signals=exit_signals,
                    include_open=False,
                )
            }
        else:
            trade_map = build_fixed_holding_trades(
                code=code,
                dates=dates,
                opens=op,
                highs=hi,
                lows=lo,
                closes=cls,
                volumes=vol,
                amounts=amt,
                signal_indices=filtered_sig_idxs,
                holding_periods=stock_periods,
                include_open=False,
            )
        h_execution: dict[int, dict[str, Any]] = {h: _execution_metrics(trades) for h, trades in trade_map.items()}
        for h, trades in trade_map.items():
            for t in trades:
                if t.get("ret") is None:
                    continue
                h_rets[h].append(float(t["ret"]))
                h_dds[h].append(float(t.get("max_dd") or 0.0))
                h_trades[h].append(t)

        # Per-horizon summary (only for HOLDING_PERIODS, not the extra hp if different)
        horizons: dict[int, dict] = {}
        for h in HOLDING_PERIODS:
            rr = h_rets.get(h, [])
            if rr:
                wr_  = float(np.mean([r > 0 for r in rr]))
                ar_  = float(np.mean(rr))
                ad_  = float(np.mean(h_dds[h]))
                cal_ = ar_ / max(abs(ad_), 0.005)
                horizons[h] = {
                    "win_rate": round(wr_, 4), "avg_ret": round(ar_, 4),
                    "avg_dd":   round(ad_, 4), "calmar":  round(cal_, 4),
                    "n": len(rr),
                    "execution": h_execution.get(h, _execution_metrics([])),
                }

        raw_horizons: dict[int, dict] = {}
        for h in HOLDING_PERIODS:
            rr = raw_h_rets.get(h, [])
            if rr:
                wr_ = float(np.mean([r > 0 for r in rr]))
                ar_ = float(np.mean(rr))
                ad_ = float(np.mean(raw_h_dds[h]))
                cal_ = ar_ / max(abs(ad_), 0.005)
                raw_horizons[h] = {
                    "win_rate": round(wr_, 4), "avg_ret": round(ar_, 4),
                    "avg_dd": round(ad_, 4), "calmar": round(cal_, 4),
                    "n": len(rr),
                    "execution": raw_h_execution.get(h, _execution_metrics([])),
                }

        # Best holding period (highest calmar)
        best_hp  = hp
        best_cal = None
        for h, hm in horizons.items():
            if best_cal is None or hm["calmar"] > best_cal:
                best_cal, best_hp = hm["calmar"], h
        if formula_id and stock_best:
            best_hp = stock_hp
        metric_horizons = horizons
        metric_rets_by_h = h_rets
        metric_dds_by_h = h_dds
        metric_trades_by_h = h_trades
        metric_execution_by_h = h_execution
        history_source = "formula_signal" if formula_id else "strategy_filter"
        if (not formula_id) and (not horizons and raw_horizons):
            metric_horizons = raw_horizons
            metric_rets_by_h = raw_h_rets
            metric_dds_by_h = raw_h_dds
            metric_trades_by_h = raw_h_trades
            metric_execution_by_h = raw_h_execution
            history_source = "all_macd_cross"
            best_hp = hp
            best_cal = None
            for h, hm in raw_horizons.items():
                if best_cal is None or hm["calmar"] > best_cal:
                    best_cal, best_hp = hm["calmar"], h

        # Main table metrics stay on the profile holding period for strategy-level comparability.
        # Per-stock effectiveness and chart intervals use the stock's best historical holding period.
        metric_hp = stock_hp if formula_id and stock_best else hp
        rets = metric_rets_by_h.get(metric_hp, [])
        dds  = metric_dds_by_h.get(metric_hp, [])
        trade_series = metric_trades_by_h.get(best_hp, [])
        effectiveness = _effectiveness_metrics(trade_series)
        execution = metric_execution_by_h.get(best_hp) or _execution_metrics([])

        if len(rets) >= min_signals:
            win_rate = float(np.mean([r > 0 for r in rets]))
            avg_ret  = float(np.mean(rets))
            avg_dd   = float(np.mean(dds))
            calmar   = avg_ret / max(abs(avg_dd), 0.005)
            metrics[code] = {
                "signal_count": len(rets),
                "win_rate": win_rate, "avg_ret": avg_ret,
                "avg_dd": avg_dd,     "calmar": calmar,
                "history_status": "ok" if history_source in {"strategy_filter", "formula_signal"} else "all_macd_cross",
                "history_source": history_source,
                "best_holding_days": best_hp, "horizons": metric_horizons,
                "effectiveness": effectiveness,
                "execution": execution,
                "trade_series": trade_series,
            }
        else:
            metrics[code] = {
                "signal_count": int(len(rets)),
                "win_rate":  float(np.mean([r > 0 for r in rets])) if rets else None,
                "avg_ret":   float(np.mean(rets)) if rets else None,
                "avg_dd":    float(np.mean(dds))  if dds  else None,
                "calmar":    None,
                "history_status": (
                    "all_macd_cross"
                    if history_source == "all_macd_cross" and rets
                    else ("too_few_signals" if rets else "no_signal")
                ),
                "history_source": history_source,
                "best_holding_days": best_hp if metric_horizons else hp,
                "horizons":  metric_horizons,
                "effectiveness": effectiveness,
                "execution": execution,
                "trade_series": trade_series,
            }

        if progress_cb and (ci + 1) % 200 == 0:
            progress_cb(ci + 1, n_total)

        idx += cnt

    _save_cache(cache_pid, profile, metrics)
    return metrics


# ---------------------------------------------------------------------------
# 当前状态（全量）
# ---------------------------------------------------------------------------
def _load_formula_hits() -> dict[str, dict[str, bool]]:
    out: dict[str, dict[str, bool]] = {}
    try:
        con = duckdb.connect(str(SMART_DB), read_only=True)
        rows = con.execute(
            """
            SELECT stock_code, f1_hit, f3_hit, f5_hit
            FROM (
                SELECT
                    stock_code,
                    f1_hit,
                    f3_hit,
                    f5_hit,
                    ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY screen_date DESC) AS rn
                FROM mart_stock_screening
            )
            WHERE rn = 1
            """
        ).fetchall()
        con.close()
    except Exception:
        return out

    for stock_code, f1, f3, f5 in rows:
        code = normalize_code(stock_code)
        out[code] = {
            "f1_hit": _to_bool(f1),
            "f3_hit": _to_bool(f3),
            "f5_hit": _to_bool(f5),
        }
    return out


def _load_formula_best_sell_rules() -> dict[str, dict[str, Any]]:
    if not FORMULA_SELL_RULE_AUDIT_CSV.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        with FORMULA_SELL_RULE_AUDIT_CSV.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                formula_id = str(row.get("formula_id") or "")
                if not formula_id:
                    continue
                score = _to_float(row.get("score")) or 0.0
                if formula_id not in out or score > (out[formula_id].get("sell_rule_score") or 0.0):
                    out[formula_id] = {
                        "sell_rule": row.get("sell_rule"),
                        "sell_rule_score": score,
                        "sell_rule_win_rate": _to_float(row.get("win_rate")),
                        "sell_rule_avg_ret": _to_float(row.get("avg_ret")),
                        "sell_rule_avg_dd": _to_float(row.get("avg_dd")),
                        "sell_rule_calmar": _to_float(row.get("calmar")),
                    }
    except Exception:
        return {}
    return out


def _load_stock_formula_best() -> dict[tuple[str, str], dict[str, Any]]:
    if not STOCK_FORMULA_BEST_CSV.exists():
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        with STOCK_FORMULA_BEST_CSV.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                code = normalize_code(row.get("stock_code"))
                formula_id = str(row.get("formula_id") or "")
                if not code or not formula_id:
                    continue
                params_raw = row.get("params") or "{}"
                try:
                    params = json.loads(params_raw) if params_raw else {}
                except Exception:
                    params = {}
                holding_days = _to_int(row.get("holding_days"), 0)
                sell_rule = row.get("best_sell_rule") or row.get("sell_rule") or (
                    f"fixed_{holding_days}" if holding_days > 0 else None
                )
                out[(code, formula_id)] = {
                    "variant_id": row.get("variant_id"),
                    "holding_days": holding_days,
                    "sell_rule": sell_rule,
                    "sell_rule_score": _to_float(row.get("best_sell_rule_score") or row.get("sell_rule_score"))
                    or _to_float(row.get("score")),
                    "sell_rule_win_rate": _to_float(row.get("win_rate")),
                    "sell_rule_avg_ret": _to_float(row.get("avg_ret")),
                    "sell_rule_avg_dd": _to_float(row.get("avg_dd")),
                    "sell_rule_calmar": _to_float(row.get("calmar")),
                    "signal_count": _to_int(row.get("signal_count"), 0),
                    "win_rate": _to_float(row.get("win_rate")),
                    "avg_ret": _to_float(row.get("avg_ret")),
                    "avg_dd": _to_float(row.get("avg_dd")),
                    "calmar": _to_float(row.get("calmar")),
                    "score": _to_float(row.get("score")),
                    "params": params,
                }
    except Exception:
        return {}
    return out


def _formula_current_bars(formula_id: str | None) -> int:
    if formula_id == "volume_base_breakout":
        return 150
    if formula_id == "activity_breakout":
        return 90
    return CURRENT_QUERY_BARS


def _load_current_market_raw(query_bars: int) -> dict[str, np.ndarray]:
    latest = get_latest_data_date()
    now = time.time()
    with _CURRENT_RAW_CACHE_LOCK:
        cached = _CURRENT_RAW_CACHE.get(query_bars)
        if cached and cached[1] == latest and now - cached[0] < CURRENT_RAW_CACHE_TTL_SECONDS:
            return cached[2]

    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    try:
        try:
            _attach_smart_db(mkt)
            raw = mkt.execute(
                """
                WITH ranked AS (
                    SELECT k.code, k.date, k.open, k.high, k.low, k.close, k.volume, k.amount,
                           ROW_NUMBER() OVER (PARTITION BY k.code ORDER BY k.date DESC) AS rn
                    FROM v_price_kline_qfq k
                    INNER JOIN sm.dim_active_a_stock s ON k.code = s.stock_code AND s.stock_name NOT LIKE 'ST%' AND s.stock_name NOT LIKE '*ST%' -- rule-compliance: ok evidence=bc_absorbed Phase 2.2 universe wire ST filter
                )
                SELECT code, date, open, high, low, close, volume, amount
                FROM ranked
                WHERE rn <= ?
                ORDER BY code, date
                """,
                [query_bars],
            ).fetchnumpy()
        except duckdb.IOException:
            raw = mkt.execute(
                """
                WITH ranked AS (
                    SELECT code, date, open, high, low, close, volume, amount,
                           ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
                    FROM v_price_kline_qfq
                )
                SELECT code, date, open, high, low, close, volume, amount
                FROM ranked
                WHERE rn <= ?
                ORDER BY code, date
                """,
                [query_bars],
            ).fetchnumpy()
    finally:
        mkt.close()

    out = {
        "code": raw["code"],
        "date": raw["date"],
        "open": raw["open"].astype(np.float64),
        "high": raw["high"].astype(np.float64),
        "low": raw["low"].astype(np.float64),
        "close": raw["close"].astype(np.float64),
        "volume": raw["volume"].astype(np.float64),
        "amount": raw["amount"].astype(np.float64),
    }
    with _CURRENT_RAW_CACHE_LOCK:
        _CURRENT_RAW_CACHE[query_bars] = (time.time(), latest, out)
    return out


def compute_current(meta: dict[str, tuple], profile: dict[str, Any], formula_hits: dict[str, dict[str, bool]]) -> list[dict[str, Any]]:
    formula_id = profile.get("formula_id") if profile.get("signal_source") == "formula" else None
    query_bars = _formula_current_bars(str(formula_id) if formula_id else None)
    raw = _load_current_market_raw(query_bars)

    if len(raw["code"]) == 0:
        return []

    codes = raw["code"]
    dates = raw["date"]
    opens = raw["open"]
    highs = raw["high"]
    lows = raw["low"]
    closes = raw["close"]
    volumes = raw["volume"]
    amounts = raw["amount"]

    unique_codes, counts = np.unique(codes, return_counts=True)
    fast = int(profile["macd_fast"])
    slow = int(profile["macd_slow"])
    sig = int(profile["macd_signal"])
    default_holding_days = int(profile["holding_days"])
    hist_cache: dict[str, dict[str, Any]] = {}
    try:
        if _cache_fresh(profile["id"], profile):
            hist_cache = _load_cache(profile["id"], include_trade_series=False)
    except Exception:
        hist_cache = {}
    stock_formula_best = _load_stock_formula_best() if formula_id else {}

    idx = 0
    results: list[dict[str, Any]] = []

    for code_raw, cnt in zip(unique_codes, counts):
        code = normalize_code(code_raw)
        holding_days = int((hist_cache.get(code) or {}).get("best_holding_days") or default_holding_days)
        formula_params = dict(profile.get("formula_params") or {})
        sell_rule = f"fixed_{holding_days}"
        if formula_id:
            best = stock_formula_best.get((code, str(formula_id)))
            if best is None:
                idx += cnt
                continue
            holding_days = int(best.get("holding_days") or holding_days)
            formula_params = dict(best.get("params") or formula_params)
            sell_rule = str(best.get("sell_rule") or f"fixed_{holding_days}")
        sl = slice(idx, idx + cnt)
        date_arr = dates[sl]
        op = opens[sl]
        hi = highs[sl]
        lo = lows[sl]
        cls = closes[sl]
        vol = volumes[sl]
        amt = amounts[sl]
        n = len(cls)

        if n < slow + sig + 2:
            idx += cnt
            continue

        dif = ema_np(cls, fast) - ema_np(cls, slow)
        dea = ema_np(dif, sig)

        status, days_ev, gap = current_status(dif, dea, cls)

        amt_ma20 = sma_np(amt, 20)
        vol_ma20 = sma_np(vol, 20)
        max60_arr = rolling_max_np(cls, 60)
        cur_vol_r20 = float(vol[-1] / vol_ma20[-1]) if (vol_ma20[-1] > 0 and not np.isnan(vol_ma20[-1])) else 0.0
        cur_amt_r20 = float(amt[-1] / amt_ma20[-1]) if (amt_ma20[-1] > 0 and not np.isnan(amt_ma20[-1])) else 0.0
        cur_price60 = float(cls[-1] / max60_arr[-1]) if max60_arr[-1] > 0 else 1.0
        dif_positive_now = float(dif[-1]) > 0

        last_gc_date = None
        sell_hint = None
        latest_trade_horizons: dict[int, dict[str, Any]] = {}
        latest_trade_base: dict[str, Any] = {}
        if formula_id:
            try:
                formula_out = compute_formula_signals(
                    str(formula_id),
                    open_=op,
                    high=hi,
                    low=lo,
                    close=cls,
                    volume=vol,
                    amount=amt,
                    params=formula_params,
                )
                formula_entries = np.where(formula_out["entry"])[0]
                formula_exits = np.asarray(formula_out.get("exit", np.zeros(n, dtype=bool)), dtype=bool)
            except Exception:
                formula_entries = np.array([], dtype=np.int64)
                formula_exits = np.zeros(n, dtype=bool)
            if len(formula_entries) == 0:
                idx += cnt
                continue
            signal_i = int(formula_entries[-1])
            days_held = n - 1 - signal_i
            if days_held > max(holding_days, CROSS_WINDOW):
                idx += cnt
                continue
            last_gc_date = str(date_arr[signal_i])
            status = S_JUST if days_held <= CROSS_WINDOW else S_HOLD
            days_ev = int(days_held)
            gap = 0.0
            remain = holding_days - days_held
            if remain > 0:
                sell_hint = f"建议再持 {remain} 天"
            elif remain == 0:
                sell_hint = "今日为建议卖出日"
            else:
                sell_hint = f"已超持股期 {abs(remain)} 天"
            latest_trade_base = {
                "signal_date": str(date_arr[signal_i]),
                "price_mode": EXECUTION_MODEL_VERSION,
            }
            latest_trades = build_sell_rule_trades(
                code=code,
                dates=date_arr,
                opens=op,
                highs=hi,
                lows=lo,
                closes=cls,
                volumes=vol,
                amounts=amt,
                signal_indices=[signal_i],
                sell_rule=sell_rule,
                exit_signals=formula_exits,
                include_open=True,
            )
            sample_trade = None
            for hp0 in [holding_days]:
                trade = (latest_trades or [{}])[0]
                if sample_trade is None and trade:
                    sample_trade = trade
                reached = bool(trade.get("sell_date"))
                latest_trade_horizons[hp0] = {
                    "holding_days": hp0,
                    "sell_rule": sell_rule,
                    "target_sell_date": trade.get("sell_date") if reached else None,
                    "target_sell_price": trade.get("sell_price") if reached else None,
                    "eval_date": trade.get("sell_date") or trade.get("latest_date") or str(date_arr[-1]),
                    "eval_price": trade.get("sell_price") or trade.get("latest_price") or round(float(cls[-1]), 3),
                    "ret": trade.get("ret") if reached else trade.get("latest_ret"),
                    "max_dd": trade.get("max_dd"),
                    "reached_target": reached,
                    "remaining_days": max(0, int(trade.get("remaining_days") or 0)),
                    "buy_date": trade.get("buy_date"),
                    "buy_price": trade.get("buy_price"),
                    "buy_price_method": trade.get("buy_price_method"),
                    "sell_price_method": trade.get("sell_price_method"),
                    "delay_buy_days": trade.get("delay_buy_days"),
                    "delay_sell_days": trade.get("delay_sell_days"),
                }
            if sample_trade and sample_trade.get("buy_date"):
                latest_trade_base.update(
                    {
                        "buy_date": sample_trade.get("buy_date"),
                        "buy_price": sample_trade.get("buy_price"),
                        "buy_price_method": sample_trade.get("buy_price_method"),
                        "latest_date": sample_trade.get("latest_date") or str(date_arr[-1]),
                        "latest_price": sample_trade.get("latest_price") or round(float(cls[-1]), 3),
                        "elapsed_trading_days": int(sample_trade.get("holding_days_actual") or 0),
                        "latest_ret": sample_trade.get("latest_ret")
                        if sample_trade.get("latest_ret") is not None
                        else sample_trade.get("ret"),
                        "delay_buy_days": sample_trade.get("delay_buy_days"),
                    }
                )
            else:
                latest_trade_base.update(
                    {
                        "buy_date": None,
                        "buy_price": None,
                        "buy_price_method": None,
                        "latest_date": str(date_arr[-1]),
                        "latest_price": round(float(cls[-1]), 3),
                        "elapsed_trading_days": 0,
                        "latest_ret": None,
                        "pending_buy": True,
                        "pending_reason": (sample_trade or {}).get("buy_block_reason") or "waiting_next_bar",
                    }
                )

        if not formula_id:
            for i in range(1, min(n, 60)):
                if dif[-i] > dea[-i] and (i + 1 <= n) and dif[-(i + 1)] <= dea[-(i + 1)]:
                    last_gc_date = str(date_arr[-i])
                    days_held = i - 1
                    remain = holding_days - days_held
                    if remain > 0:
                        sell_hint = f"建议再持 {remain} 天"
                    elif remain == 0:
                        sell_hint = "今日为建议卖出日"
                    else:
                        sell_hint = f"已超持股期 {abs(remain)} 天"

                    signal_i = n - i
                    latest_trade_base = {
                        "signal_date": str(date_arr[signal_i]),
                        "price_mode": EXECUTION_MODEL_VERSION,
                    }
                    latest_trade_map = build_fixed_holding_trades(
                        code=code,
                        dates=date_arr,
                        opens=op,
                        highs=hi,
                        lows=lo,
                        closes=cls,
                        volumes=vol,
                        amounts=amt,
                        signal_indices=[signal_i],
                        holding_periods=[holding_days],
                        include_open=True,
                    )
                    sample_trade = None
                    for hp0 in [holding_days]:
                        trade = (latest_trade_map.get(hp0) or [{}])[0]
                        if sample_trade is None and trade:
                            sample_trade = trade
                        reached = bool(trade.get("sell_date"))
                        latest_trade_horizons[hp0] = {
                            "holding_days": hp0,
                            "target_sell_date": trade.get("sell_date") if reached else None,
                            "target_sell_price": trade.get("sell_price") if reached else None,
                            "eval_date": trade.get("sell_date") or trade.get("latest_date") or str(date_arr[-1]),
                            "eval_price": trade.get("sell_price") or trade.get("latest_price") or round(float(cls[-1]), 3),
                            "ret": trade.get("ret") if reached else trade.get("latest_ret"),
                            "max_dd": trade.get("max_dd"),
                            "reached_target": reached,
                            "remaining_days": max(0, int(trade.get("remaining_days") or 0)),
                            "buy_date": trade.get("buy_date"),
                            "buy_price": trade.get("buy_price"),
                            "buy_price_method": trade.get("buy_price_method"),
                            "sell_price_method": trade.get("sell_price_method"),
                            "delay_buy_days": trade.get("delay_buy_days"),
                            "delay_sell_days": trade.get("delay_sell_days"),
                        }
                    if sample_trade and sample_trade.get("buy_date"):
                        latest_trade_base.update(
                            {
                                "buy_date": sample_trade.get("buy_date"),
                                "buy_price": sample_trade.get("buy_price"),
                                "buy_price_method": sample_trade.get("buy_price_method"),
                                "latest_date": sample_trade.get("latest_date") or str(date_arr[-1]),
                                "latest_price": sample_trade.get("latest_price") or round(float(cls[-1]), 3),
                                "elapsed_trading_days": int(sample_trade.get("holding_days_actual") or 0),
                                "latest_ret": sample_trade.get("latest_ret")
                                if sample_trade.get("latest_ret") is not None
                                else sample_trade.get("ret"),
                                "delay_buy_days": sample_trade.get("delay_buy_days"),
                            }
                        )
                    else:
                        sell_hint = "等待下一交易日确认买入"
                        latest_trade_base.update(
                            {
                                "buy_date": None,
                                "buy_price": None,
                                "buy_price_method": None,
                                "latest_date": str(date_arr[-1]),
                                "latest_price": round(float(cls[-1]), 3),
                                "elapsed_trading_days": 0,
                                "latest_ret": None,
                                "pending_buy": True,
                                "pending_reason": (sample_trade or {}).get("buy_block_reason") or "waiting_next_bar",
                            }
                        )
                    break

        hits = formula_hits.get(code, {"f1_hit": False, "f3_hit": False, "f5_hit": False})
        if not _passes_formula_filter(hits, profile):
            idx += cnt
            continue
        meta_val = meta.get(code, ("", "未知", "未知", 0.0))

        results.append(
            {
                "code": code,
                "name": meta_val[0],
                "industry": meta_val[1],
                "archetype": meta_val[2],
                "holder_chg": float(meta_val[3]) if meta_val[3] else 0.0,
                "status": status,
                "status_order": STATUS_ORDER.get(status, 9),
                "status_color": STATUS_COLOR.get(status, "gray"),
                "days_event": days_ev,
                "gap": round(gap, 6),
                "cur_dif": round(float(dif[-1]), 6),
                "cur_dea": round(float(dea[-1]), 6),
                "cur_close": round(float(cls[-1]), 2),
                "cur_date": str(date_arr[-1]),
                "dif_positive": dif_positive_now,
                "cur_vol_r20": round(cur_vol_r20, 2),
                "cur_amt_r20": round(cur_amt_r20, 2),
                "cur_price60": round(cur_price60, 3),
                "filter_pass": (
                    cur_vol_r20 >= float(profile.get("vol_ratio_min", 1.0))
                    and cur_amt_r20 >= float(profile.get("amt_ratio_min", 1.0))
                    and cur_price60 <= float(profile.get("price_pos_max", 1.0))
                    and (not profile.get("dif_positive") or dif_positive_now)
                ),
                "last_gc_date": last_gc_date,
                "sell_hint": sell_hint,
                "latest_trade": latest_trade_base or None,
                "latest_trade_horizons": latest_trade_horizons,
                "f1_hit": hits.get("f1_hit", False),
                "f3_hit": hits.get("f3_hit", False),
                "f5_hit": hits.get("f5_hit", False),
                "formula_hit_count": int(hits.get("f1_hit", False))
                + int(hits.get("f3_hit", False))
                + int(hits.get("f5_hit", False)),
                "history_status": "pending",
            }
        )

        idx += cnt

    return results


# ---------------------------------------------------------------------------
# 图表数据
# ---------------------------------------------------------------------------
def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _chart_start_month(latest: Any) -> date | None:
    d = _parse_date(latest)
    if not d:
        return None
    return date(d.year - 1, d.month, 1)


def _series_values(arr: np.ndarray, start: int, digits: int = 6) -> list[float | None]:
    out: list[float | None] = []
    for v in arr[start:]:
        fv = float(v)
        out.append(round(fv, digits) if np.isfinite(fv) else None)
    return out


def _constant_series(value: float, length: int, digits: int = 6) -> list[float]:
    return [round(float(value), digits)] * length


def _extend_sparse_indicator(arr: np.ndarray, span: int = 20) -> np.ndarray:
    out = np.full(len(arr), np.nan, dtype=np.float64)
    finite_idx = np.where(np.isfinite(arr))[0]
    for i in finite_idx:
        end = min(len(arr), int(i) + max(1, span))
        out[int(i) : end] = float(arr[int(i)])
    return out


def _indicator_chart_payload(
    profile: dict[str, Any],
    formula_id: str | None,
    formula_out: dict[str, Any] | None,
    formula_params: dict[str, Any],
    start: int,
    n_visible: int,
    dif: np.ndarray,
    dea: np.ndarray,
    bar: np.ndarray,
) -> dict[str, Any]:
    if not formula_id or not formula_out:
        return {
            "title": "MACD · DIF / DEA",
            "kind": "macd",
            "datasets": [
                {"type": "line", "label": "DIF", "data": _series_values(dif, start), "role": "dif"},
                {"type": "line", "label": "DEA", "data": _series_values(dea, start), "role": "dea"},
                {"type": "bar", "label": "MACD", "data": _series_values(bar, start), "role": "histogram"},
            ],
        }

    indicators = formula_out.get("indicators") or {}
    if formula_id in {"gs_raw_buy", "gs_pullback_confirm"}:
        datasets = [
            {"type": "line", "label": "X3", "data": _series_values(indicators.get("x3", np.array([])), start), "role": "dif"},
            {"type": "line", "label": "X36", "data": _series_values(indicators.get("x36", np.array([])), start), "role": "dea"},
        ]
        if "rate" in indicators:
            datasets.append({"type": "line", "label": "历史快买率", "data": _series_values(indicators["rate"], start, 3), "role": "accent"})
        return {"title": "GS · X3 / X36", "kind": "gs", "datasets": datasets}

    if formula_id == "ma_base_breakout":
        return {
            "title": "均线筑底 · MA5 / MA90 / MA145",
            "kind": "ma_base",
            "datasets": [
                {"type": "line", "label": "短均线", "data": _series_values(indicators.get("ma_short", np.array([])), start), "role": "dif"},
                {"type": "line", "label": "中均线", "data": _series_values(indicators.get("ma_mid", np.array([])), start), "role": "dea"},
                {"type": "line", "label": "长均线", "data": _series_values(indicators.get("ma_long", np.array([])), start), "role": "accent"},
            ],
        }

    if formula_id == "activity_breakout":
        big = float(formula_params.get("big_bull_line", formula_params.get("大牛线", 6.0)))
        strong = float(formula_params.get("strong_line", 3.0))
        return {
            "title": "活跃度 · X15 / 强势线 / 大牛线",
            "kind": "activity",
            "datasets": [
                {"type": "line", "label": "X15", "data": _series_values(indicators.get("x15", np.array([])), start), "role": "dif"},
                {"type": "line", "label": "强势线", "data": _constant_series(strong, n_visible), "role": "dea"},
                {"type": "line", "label": "大牛线", "data": _constant_series(big, n_visible), "role": "accent"},
            ],
        }

    if formula_id == "volume_base_breakout":
        platform_low = _extend_sparse_indicator(indicators.get("platform_low", np.array([])), 20)
        platform_high = _extend_sparse_indicator(indicators.get("platform_high", np.array([])), 20)
        return {
            "title": "巨量蓄势 · 平台低 / 平台高",
            "kind": "volume_base",
            "datasets": [
                {"type": "line", "label": "平台低", "data": _series_values(platform_low, start), "role": "neg"},
                {"type": "line", "label": "平台高", "data": _series_values(platform_high, start), "role": "pos"},
            ],
        }

    return {
        "title": f"{profile.get('name', '策略')} · 指标",
        "kind": "formula",
        "datasets": [],
    }


def get_chart_data(code: str, profile: dict[str, Any]) -> dict[str, Any]:
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    raw = mkt.execute(
        """
        SELECT date, open, high, low, close, volume, amount
        FROM v_price_kline_qfq
        WHERE code = ?
        ORDER BY date DESC
        LIMIT ?
        """,
        [normalize_code(code), CHART_QUERY_BARS],
    ).fetchnumpy()
    mkt.close()

    if len(raw["date"]) == 0:
        return {}

    dates = raw["date"][::-1]
    opens = raw["open"][::-1].astype(np.float64)
    highs = raw["high"][::-1].astype(np.float64)
    lows = raw["low"][::-1].astype(np.float64)
    closes = raw["close"][::-1].astype(np.float64)
    volumes = raw["volume"][::-1].astype(np.float64)
    amounts = raw["amount"][::-1].astype(np.float64)
    n = len(closes)

    fast = int(profile["macd_fast"])
    slow = int(profile["macd_slow"])
    sig = int(profile["macd_signal"])
    cached_hist_row: dict[str, Any] = {}
    if _cache_fresh(profile["id"], profile):
        cached_hist_row = _load_cache(profile["id"]).get(normalize_code(code), {}) or {}
    holding_days = int(cached_hist_row.get("best_holding_days") or profile["holding_days"])
    formula_id = profile.get("formula_id") if profile.get("signal_source") == "formula" else None
    formula_params = profile.get("formula_params") or {}
    formula_best = None
    sell_rule = f"fixed_{holding_days}"
    if formula_id:
        formula_best = _load_stock_formula_best().get((normalize_code(code), str(formula_id)))
        if formula_best:
            holding_days = int(formula_best.get("holding_days") or holding_days)
            formula_params = formula_best.get("params") or formula_params
            sell_rule = str(formula_best.get("sell_rule") or f"fixed_{holding_days}")
    missing_formula_optimization = bool(formula_id and formula_best is None)

    dif = ema_np(closes, fast) - ema_np(closes, slow)
    dea = ema_np(dif, sig)
    bar = (dif - dea) * 2
    amt_ma20 = sma_np(amounts, 20)
    vol_ma20 = sma_np(volumes, 20)
    max60_arr = rolling_max_np(closes, 60)

    crosses = []
    chart_signal_idxs = []
    chart_exit_idxs = []
    for i in range(1, n):
        if dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:
            crosses.append({"idx": i, "type": "golden", "date": str(dates[i]), "close": round(float(closes[i]), 2)})
        elif dif[i] < dea[i] and dif[i - 1] >= dea[i - 1]:
            crosses.append({"idx": i, "type": "death", "date": str(dates[i]), "close": round(float(closes[i]), 2)})

    formula_out = None
    if formula_id and not missing_formula_optimization:
        try:
            formula_out = compute_formula_signals(
                str(formula_id),
                open_=opens,
                high=highs,
                low=lows,
                close=closes,
                volume=volumes,
                amount=amounts,
                params=formula_params,
            )
            chart_signal_idxs = [int(i) for i in np.where(formula_out["entry"])[0]]
            chart_exit_idxs = [int(i) for i in np.where(formula_out.get("exit", np.zeros(n, dtype=bool)))[0]]
        except Exception:
            formula_out = None
            chart_signal_idxs = []
            chart_exit_idxs = []
    else:
        for i in range(1, n):
            if dif[i] > dea[i] and dif[i - 1] < dea[i - 1]:
                if (
                    vol_ma20[i] <= 0
                    or np.isnan(vol_ma20[i])
                    or amt_ma20[i] <= 0
                    or np.isnan(amt_ma20[i])
                    or max60_arr[i] <= 0
                ):
                    continue
                vol_r20 = float(volumes[i] / vol_ma20[i])
                amt_r20 = float(amounts[i] / amt_ma20[i])
                price60 = float(closes[i] / max60_arr[i])
                if (
                    vol_r20 < float(profile.get("vol_ratio_min", 1.0))
                    or amt_r20 < float(profile.get("amt_ratio_min", 1.0))
                    or price60 > float(profile.get("price_pos_max", 1.0))
                    or (profile.get("dif_positive") and float(dif[i]) <= 0)
                ):
                    continue
                chart_signal_idxs.append(i)

    if formula_id and not missing_formula_optimization:
        backtest_trades = build_sell_rule_trades(
            code=normalize_code(code),
            dates=dates,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            amounts=amounts,
            signal_indices=chart_signal_idxs,
            sell_rule=sell_rule,
            exit_signals=np.asarray((formula_out or {}).get("exit", np.zeros(n, dtype=bool)), dtype=bool),
            include_open=True,
        )
    else:
        backtest_trades = build_fixed_holding_trades(
            code=normalize_code(code),
            dates=dates,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            amounts=amounts,
            signal_indices=chart_signal_idxs,
            holding_periods=[holding_days],
            include_open=True,
        ).get(holding_days, [])

    chart_start_month = _chart_start_month(dates[-1])
    if chart_start_month:
        start = next(
            (i for i, d in enumerate(dates) if (_parse_date(d) or date.min) >= chart_start_month),
            max(0, n - CHART_FALLBACK_BARS),
        )
    else:
        start = max(0, n - CHART_FALLBACK_BARS)
    status, days_ev, gap = current_status(dif, dea, closes)
    if missing_formula_optimization:
        status = "缺少寻优结果"
        days_ev = None
        gap = 0.0
    cached_trade_series = cached_hist_row.get("trade_series") or []
    if formula_best:
        cached_trade_series = backtest_trades

    visible_dates = [str(d) for d in dates[start:]]
    indicator_chart = _indicator_chart_payload(
        profile,
        str(formula_id) if formula_id else None,
        formula_out,
        formula_params,
        start,
        len(visible_dates),
        dif,
        dea,
        bar,
    )
    date_to_visible_idx = {d: i for i, d in enumerate(visible_dates)}
    holding_intervals = []
    for t in cached_trade_series:
        buy_date = str(t.get("buy_date") or "")
        sell_date = str(t.get("sell_date") or "")
        if not buy_date or not sell_date:
            continue
        buy_idx = date_to_visible_idx.get(buy_date)
        sell_idx = date_to_visible_idx.get(sell_date)
        if buy_idx is None and sell_idx is None:
            continue
        holding_intervals.append(
            {
                "buy_idx": buy_idx if buy_idx is not None else 0,
                "sell_idx": sell_idx if sell_idx is not None else len(visible_dates) - 1,
                "buy_date": buy_date,
                "sell_date": sell_date,
                "holding_days": t.get("holding_days"),
                "ret": t.get("ret"),
                "max_dd": t.get("max_dd"),
            }
        )

    return {
        "dates": visible_dates,
        "open": [round(float(v), 2) for v in opens[start:]],
        "high": [round(float(v), 2) for v in highs[start:]],
        "low": [round(float(v), 2) for v in lows[start:]],
        "close": [round(float(v), 2) for v in closes[start:]],
        "volume": [round(float(v) / 100, 0) for v in volumes[start:]],
        "dif": [round(float(v), 6) for v in dif[start:]],
        "dea": [round(float(v), 6) for v in dea[start:]],
        "bar": [round(float(v), 6) for v in bar[start:]],
        "crosses": [
            {**c, "idx": c["idx"] - start}
            for c in crosses
            if c["idx"] >= start
        ],
        "signal_points": [
            {
                "idx": i - start,
                "type": "entry",
                "date": str(dates[i]),
                "close": round(float(closes[i]), 2),
            }
            for i in chart_signal_idxs
            if i >= start
        ] + [
            {
                "idx": i - start,
                "type": "exit",
                "date": str(dates[i]),
                "close": round(float(closes[i]), 2),
            }
            for i in chart_exit_idxs
            if i >= start
        ],
        "indicator_chart": indicator_chart,
        "backtest_trades": [
            {
                **t,
                "signal_idx": t["signal_idx"] - start,
                "buy_idx": t["buy_idx"] - start if t.get("buy_idx") is not None else None,
                "sell_idx": t["sell_idx"] - start if t.get("sell_idx") is not None else None,
                "pending_buy_idx": t["pending_buy_idx"] - start if t.get("pending_buy_idx") is not None else None,
            }
            for t in backtest_trades
            if (
                (t.get("buy_idx") is not None and t["buy_idx"] >= start)
                or (t.get("pending_buy_idx") is not None and t["pending_buy_idx"] >= start)
                or (t.get("sell_idx") is not None and t["sell_idx"] >= start)
            )
        ],
        "trade_series": [
            {
                "buy_date": t.get("buy_date"),
                "sell_date": t.get("sell_date"),
                "holding_days": t.get("holding_days"),
                "ret": t.get("ret"),
                "max_dd": t.get("max_dd"),
            }
            for t in cached_trade_series
            if t.get("buy_date") and t.get("sell_date")
        ],
        "holding_intervals": holding_intervals,
        "status": status,
        "days_event": days_ev,
        "gap": round(float(gap), 6),
        "chart_start_date": visible_dates[0] if visible_dates else None,
        "chart_end_date": visible_dates[-1] if visible_dates else None,
        "chart_period_label": "近一年",
        "profile_id": profile["id"],
        "optimization_missing": missing_formula_optimization,
        "optimization_missing_reason": "缺少每股公式参数寻优结果，未使用默认参数回退"
        if missing_formula_optimization
        else None,
        "optimized_variant_id": formula_best.get("variant_id") if formula_best else None,
        "optimized_sell_rule": formula_best.get("sell_rule") if formula_best else None,
        "optimized_sell_rule_score": formula_best.get("sell_rule_score") if formula_best else None,
        "optimized_params": formula_params if formula_best else None,
    }


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------
class ComputeEngine:
    def __init__(self):
        self._lock = threading.Lock()
        self._ready = False
        self._started = False
        self._message = "等待启动"
        # Multi-profile cache: each profile's computed result stored separately
        self._data_cache: dict[str, dict[str, Any]] = {}
        self._unified_cache: Optional[dict[str, Any]] = None
        self._unified_cache_profile_ids: tuple[str, ...] = ()
        # Track which profiles are currently being computed
        self._computing: set[str] = set()

        self._profiles = get_strategy_profiles()
        self._default_profile_id = get_default_profile_id(self._profiles)
        start_profile = os.environ.get("BESTCHOICE_START_PROFILE")
        self._active_profile_id = (
            start_profile if start_profile in self._profiles else self._default_profile_id
        )

    def profiles(self) -> dict[str, dict[str, Any]]:
        return self._profiles

    def active_profile_id(self) -> str:
        with self._lock:
            return self._active_profile_id

    def default_profile_id(self) -> str:
        return self._default_profile_id

    def active_profile(self) -> dict[str, Any]:
        return self._profiles[self.active_profile_id()]

    def ensure_profile(self, profile_id: str, force: bool = False) -> dict[str, Any]:
        if profile_id not in self._profiles:
            raise KeyError(profile_id)

        with self._lock:
            already_done = profile_id in self._data_cache
            already_computing = profile_id in self._computing

        if force or (not already_done and not already_computing):
            threading.Thread(
                target=self.start,
                args=(profile_id,),
                kwargs={"force": force},
                daemon=True,
            ).start()

        return self._profiles[profile_id]

    def set_profile(self, profile_id: str) -> dict[str, Any]:
        if profile_id not in self._profiles:
            raise KeyError(profile_id)

        with self._lock:
            self._active_profile_id = profile_id
            already_done = profile_id in self._data_cache

        if already_done:
            # Serve from cache instantly — no recompute needed
            with self._lock:
                self._ready = True
                self._message = f"就绪（{self._profiles[profile_id]['name']}）"
        else:
            # Not in cache yet (either computing or not started at all)
            with self._lock:
                self._ready = False
                self._message = f"计算策略（{self._profiles[profile_id]['name']}）..."
            self.ensure_profile(profile_id, force=True)

        return self._profiles[profile_id]

    def _build_profile_payload(self, profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": profile["id"],
            "name": profile["name"],
            "source": profile.get("source", "内置"),
            "macd": f"EMA({profile['macd_fast']}/{profile['macd_slow']}/{profile['macd_signal']})",
            "holding_days": profile["holding_days"],
            "vol_ratio_min": profile.get("vol_ratio_min", 1.0),
            "amt_ratio_min": profile["amt_ratio_min"],
            "price_pos_max": profile["price_pos_max"],
            "min_signals": profile.get("min_signals", 1),
            "fast": profile["macd_fast"],
            "slow": profile["macd_slow"],
            "signal": profile["macd_signal"],
            "dif_positive": profile.get("dif_positive", False),
            "strategy_type": profile.get("strategy_type", "macd"),
            "formula_filter_mode": profile.get("formula_filter_mode"),
            "formula_rule_id": profile.get("formula_rule_id"),
            "formula_id": profile.get("formula_id"),
            "signal_source": profile.get("signal_source", "macd"),
        }

    def unified_profile_ids(self) -> list[str]:
        ids = [self._default_profile_id]
        for pid in ("macd_10_22_8", "macd_12_26_9", "macd_14_30_11", "optuna_best"):
            if pid in self._profiles and pid not in ids:
                ids.append(pid)
        for pid, profile in self._profiles.items():
            if profile.get("signal_source") == "formula" and pid not in ids:
                ids.append(pid)
        return ids

    def ensure_unified(self) -> list[str]:
        ids = self.unified_profile_ids()
        try:
            max_compute = max(1, int(os.environ.get("BESTCHOICE_UNIFIED_MAX_COMPUTE", "2")))
        except Exception:
            max_compute = 2
        max_compute = min(max_compute, 2)
        with self._lock:
            active_unified_computing = [pid for pid in ids if pid in self._computing]
            pending = [pid for pid in ids if pid not in self._data_cache and pid not in self._computing]
        slots = max(0, max_compute - len(active_unified_computing))
        for pid in pending[:slots]:
            self.ensure_profile(pid)
        return ids

    def _strategy_signal_from_row(
        self,
        profile: dict[str, Any],
        row: dict[str, Any] | None,
        hist: dict[str, Any] | None,
        formula_best: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        hist = hist or {}
        row = row or {}
        has_current = bool(row)
        score = 0.0
        if has_current:
            if row.get("status") == S_JUST:
                score += 35
            elif row.get("status") == S_HOLD:
                score += 20
            elif row.get("status") == S_IMMIN:
                score += 10
            if row.get("trade_buy_date"):
                score += 10
        optimized = formula_best if profile.get("signal_source") == "formula" else None
        eff_score = hist.get("effectiveness", {}).get("score") if hist else None
        if eff_score is not None:
            score += min(float(eff_score) * 0.25, 25)
        win_rate = optimized.get("win_rate") if optimized and optimized.get("win_rate") is not None else hist.get("win_rate")
        avg_ret = optimized.get("avg_ret") if optimized and optimized.get("avg_ret") is not None else hist.get("avg_ret")
        avg_dd = optimized.get("avg_dd") if optimized and optimized.get("avg_dd") is not None else hist.get("avg_dd")
        calmar = optimized.get("calmar") if optimized and optimized.get("calmar") is not None else hist.get("calmar")
        if win_rate is not None:
            score += min(float(win_rate) * 15, 15)
        if avg_ret is not None and float(avg_ret) > 0:
            score += 8
        if calmar is not None:
            score += min(max(float(calmar), 0.0) * 2, 7)
        execution = hist.get("execution") or {}
        untradable_rate = execution.get("untradable_rate")
        if untradable_rate is not None:
            score -= min(float(untradable_rate) * 25, 12)

        status = row.get("status") if has_current else "未触发"
        if not has_current and int(hist.get("signal_count") or 0) > 0:
            status = "历史有效未触发"

        optimized_score = optimized.get("score") if optimized else None
        optimized_holding = optimized.get("holding_days") if optimized else None
        optimized_params = optimized.get("params") if optimized else None

        return {
            "strategy_id": profile["id"],
            "strategy_name": profile["name"],
            "signal_source": profile.get("signal_source", "macd"),
            "formula_id": profile.get("formula_id"),
            "signal_family": profile.get("formula_id") if profile.get("signal_source") == "formula" else "macd",
            "status": status,
            "signal_date": row.get("trade_signal_date") or row.get("last_gc_date"),
            "buy_date": row.get("trade_buy_date"),
            "buy_price": row.get("trade_buy_price"),
            "buy_price_method": row.get("trade_buy_price_method"),
            "latest_ret": row.get("trade_latest_ret"),
            "target_sell_date": row.get("trade_target_sell_date"),
            "target_sell_price": row.get("trade_target_sell_price"),
            "eval_date": row.get("trade_eval_date"),
            "eval_price": row.get("trade_eval_price"),
            "ref_ret": row.get("trade_ref_ret"),
            "ref_max_dd": row.get("trade_ref_max_dd"),
            "reached_target": row.get("trade_reached_target"),
            "remaining_days": row.get("trade_remaining_days"),
            "pending_buy": row.get("trade_pending_buy"),
            "pending_reason": row.get("trade_pending_reason"),
            "best_holding_days": optimized_holding or hist.get("best_holding_days") or row.get("best_holding_days"),
            "optimized_variant_id": optimized.get("variant_id") if optimized else None,
            "optimized_sell_rule": optimized.get("sell_rule") if optimized else None,
            "optimized_sell_rule_score": optimized.get("sell_rule_score") if optimized else None,
            "optimized_score": optimized_score,
            "optimized_signal_count": optimized.get("signal_count") if optimized else None,
            "optimized_params": optimized_params,
            "optimized_param_count": len(optimized_params or {}),
            "win_rate": win_rate,
            "avg_ret": avg_ret,
            "avg_dd": avg_dd,
            "calmar": calmar,
            "signal_count": optimized.get("signal_count") if optimized else hist.get("signal_count", 0),
            "effectiveness_score": eff_score,
            "effectiveness_label": hist.get("effectiveness", {}).get("label") if hist else None,
            "execution": execution,
            "untradable_rate": untradable_rate,
            "skipped_buy_rate": execution.get("skipped_buy_rate"),
            "completion_rate": execution.get("completion_rate"),
            "is_current": has_current,
            "is_buy_window": bool(has_current and row.get("status") == S_JUST and (row.get("days_event") or 99) <= 5),
            "is_today_candidate": bool(row.get("is_today_candidate")),
            "is_strong_pick": bool(row.get("is_strong_pick")),
            "score": round(score, 1),
        }

    def unified_data(self) -> Optional[dict[str, Any]]:
        profile_ids = self.unified_profile_ids()
        with self._lock:
            if (
                self._unified_cache is not None
                and self._unified_cache_profile_ids == tuple(profile_ids)
            ):
                return self._unified_cache
        cached_unified = _load_unified_cache(profile_ids, self._profiles)
        if cached_unified is not None:
            with self._lock:
                self._unified_cache = cached_unified
                self._unified_cache_profile_ids = tuple(profile_ids)
            return cached_unified

        self.ensure_unified()
        with self._lock:
            missing = [pid for pid in profile_ids if pid not in self._data_cache]
            computing = list(self._computing)
        if missing:
            return {
                "ready": False,
                "missing_profiles": missing,
                "computing_profiles": computing,
                "profile_ids": profile_ids,
            }

        profile_data = {pid: self._data_cache[pid] for pid in profile_ids}
        base_data = profile_data[self._default_profile_id]
        base_rows = {r["code"]: dict(r) for r in base_data.get("stocks", [])}

        hist_by_profile: dict[str, dict[str, dict[str, Any]]] = {}
        current_by_profile: dict[str, dict[str, dict[str, Any]]] = {}
        formula_best_by_key = _load_stock_formula_best()
        for pid in profile_ids:
            profile = self._profiles[pid]
            current_by_profile[pid] = {r["code"]: r for r in profile_data[pid].get("stocks", [])}
            try:
                hist_by_profile[pid] = _load_cache(pid, include_trade_series=False) if _cache_fresh(pid, profile) else {}
            except Exception:
                hist_by_profile[pid] = {}
            for code in hist_by_profile[pid]:
                if code not in base_rows:
                    base_rows[code] = {
                        "code": code,
                        "name": code,
                        "industry": "未知",
                        "archetype": "未知",
                        "cur_close": None,
                        "cur_date": None,
                        "status": S_WAIT,
                        "status_order": STATUS_ORDER[S_WAIT],
                    }

        stocks = []
        for code, base in base_rows.items():
            signals = []
            for pid in profile_ids:
                profile = self._profiles[pid]
                hist = hist_by_profile.get(pid, {}).get(code)
                row = current_by_profile.get(pid, {}).get(code)
                if row or (hist and int(hist.get("signal_count") or 0) > 0):
                    formula_best = None
                    formula_id = profile.get("formula_id")
                    if formula_id:
                        formula_best = formula_best_by_key.get((code, str(formula_id)))
                    signals.append(self._strategy_signal_from_row(profile, row, hist, formula_best))
            current_signals = [s for s in signals if s.get("is_current")]
            buy_window_signals = [s for s in signals if s.get("is_buy_window")]
            best_signal = max(signals, key=lambda s: s.get("score") or 0.0) if signals else None
            confluence_families = {
                s.get("signal_family") or s.get("strategy_id")
                for s in current_signals
                if s.get("score", 0) >= 30
            }
            confluence_score = len(confluence_families)
            quality_score = max((s.get("score") or 0.0 for s in signals), default=0.0)
            qualified_buy_signals = [
                s
                for s in buy_window_signals
                if (s.get("win_rate") or 0) >= 0.55
                and (s.get("avg_ret") or 0) > 0
                and (s.get("effectiveness_score") or 0) >= 50
                and (s.get("untradable_rate") is None or float(s.get("untradable_rate") or 0) <= 0.20)
            ]
            qualified_buy_families = {
                s.get("signal_family") or s.get("strategy_id")
                for s in qualified_buy_signals
            }
            top_qualified_score = max((s.get("score") or 0.0 for s in qualified_buy_signals), default=0.0)
            today_recommended = bool(
                qualified_buy_signals
                and (
                    (len(qualified_buy_families) >= 2 and quality_score >= 80)
                    or top_qualified_score >= 92
                )
            )
            if today_recommended:
                reason = "买入窗口内，历史表现达标"
                if len(qualified_buy_families) >= 2:
                    reason = f"{len(qualified_buy_families)} 个达标策略共振，且处于买入窗口"
            elif not buy_window_signals:
                reason = "当前未处于最佳买入窗口"
            elif quality_score < 55:
                reason = "历史有效性或综合评分不足"
            else:
                reason = "缺少胜率/收益达标的回测支持"

            slim_base = {
                k: base.get(k)
                for k in (
                    "code",
                    "name",
                    "industry",
                    "archetype",
                    "holder_chg",
                    "cur_close",
                    "cur_date",
                    "cur_dif",
                    "cur_dea",
                    "cur_vol_r20",
                    "cur_amt_r20",
                    "cur_price60",
                    "dif_positive",
                )
                if k in base
            }
            out = {
                **slim_base,
                "status": best_signal.get("status") if best_signal else base.get("status", S_WAIT),
                "status_order": STATUS_ORDER.get(best_signal.get("status") if best_signal else base.get("status"), 9),
                "strategy_signals": signals,
                "best_signal": best_signal,
                "best_signal_name": best_signal.get("strategy_name") if best_signal else None,
                "best_signal_score": best_signal.get("score") if best_signal else None,
                "current_signal_count": len(current_signals),
                "buy_window_signal_count": len(buy_window_signals),
                "confluence_score": confluence_score,
                "qualified_buy_signal_count": len(qualified_buy_signals),
                "qualified_buy_family_count": len(qualified_buy_families),
                "is_today_recommended": today_recommended,
                "today_recommend_reason": reason,
            }
            stocks.append(out)

        stocks.sort(
            key=lambda r: (
                not r.get("is_today_recommended"),
                -(r.get("qualified_buy_family_count") or 0),
                -(r.get("confluence_score") or 0),
                -(r.get("best_signal_score") or 0),
                r.get("code") or "",
            )
        )
        summary = {
            "total": len(stocks),
            "today_recommended": sum(1 for r in stocks if r.get("is_today_recommended")),
            "buy_window": sum(1 for r in stocks if r.get("buy_window_signal_count")),
            "current_signal": sum(1 for r in stocks if r.get("current_signal_count")),
            "multi_signal": sum(1 for r in stocks if (r.get("qualified_buy_family_count") or 0) >= 2),
            "multi_family": sum(1 for r in stocks if (r.get("qualified_buy_family_count") or 0) >= 2),
            "current_multi_family": sum(1 for r in stocks if (r.get("confluence_score") or 0) >= 2),
            "profiles": len(profile_ids),
        }
        unified = {
            "ready": True,
            "stocks": stocks,
            "summary": summary,
            "profile_ids": profile_ids,
            "profiles": {pid: self._build_profile_payload(self._profiles[pid]) for pid in profile_ids},
            "computed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            **get_data_freshness(),
        }
        with self._lock:
            self._unified_cache = unified
            self._unified_cache_profile_ids = tuple(profile_ids)
        _save_unified_cache(unified, profile_ids, self._profiles)
        return unified

    def _build_param_desc_payload(self, profile: dict[str, Any]) -> dict[str, Any]:
        out = {
            "macd_fast": {
                **PARAM_DESCRIPTIONS["macd_fast"],
                "value": profile["macd_fast"],
            },
            "macd_slow": {
                **PARAM_DESCRIPTIONS["macd_slow"],
                "value": profile["macd_slow"],
            },
            "macd_signal": {
                **PARAM_DESCRIPTIONS["macd_signal"],
                "value": profile["macd_signal"],
            },
            "holding_days": {
                **PARAM_DESCRIPTIONS["holding_days"],
                "value": profile["holding_days"],
            },
            "amt_ratio_min": {
                **PARAM_DESCRIPTIONS["amt_ratio_min"],
                "value": profile["amt_ratio_min"],
            },
            "price_pos_max": {
                **PARAM_DESCRIPTIONS["price_pos_max"],
                "value": profile["price_pos_max"],
            },
            "min_signals": {
                "label": "历史信号下限",
                "desc": "至少保留多少个历史有效金叉才作为有效样本。",
                "low_hint": "调低更容易拿到结果，适合先做候选广度分析。",
                "high_hint": "调高可增强历史样本稳定性，但会出现更多空缺。",
                "value": int(profile.get("min_signals", 1)),
            },
        }

        rule_id = str(profile.get("formula_rule_id", ""))
        rule_meta = FORMULA_PROFILE_RULES.get(rule_id)
        if rule_meta:
            out.update(
                {
                    "formula_rule": {
                        "label": rule_meta.get("name", "公式规则"),
                        "desc": rule_meta.get("desc", ""),
                        "low_hint": rule_meta.get("low_hint", ""),
                        "high_hint": rule_meta.get("high_hint", ""),
                        "value": rule_meta.get("rule", ""),
                    },
                    "formula_rule_raw": {
                        "label": "命中模式",
                        "desc": "命中逻辑与筛选组合方式，可理解为策略对样本保守程度的控制。",
                        "low_hint": "放宽模式（任意命中）可显著扩大样本。",
                        "high_hint": "收紧模式（单公式/交集）可减少噪音。",
                        "value": profile.get("formula_filter_mode", "none"),
                    },
                }
            )

        if profile.get("strategy_type") == "macd_optuna":
            score = profile.get("optuna_score")
            out["optuna_n"] = {
                "label": "Optuna 样本量",
                "desc": "在候选参数下满足过滤条件的总信号样本。",
                "low_hint": "样本更少时，排名更容易受极端行情影响。",
                "high_hint": "样本更多通常更稳健，但可能包含更多弱信号。",
                "value": int(profile.get("optuna_n", 0)),
            }
            if score is not None:
                out["optuna_score"] = {
                    "label": "Optuna 目标分数",
                    "desc": "按 Calmar × 胜率定义的综合得分。",
                    "low_hint": "分数高并不代表样本无偏，但整体更均衡。",
                    "high_hint": "分数更高可作为更优买入与持仓参数的参考。",
                    "value": score,
                }

            if "best_calmar" in profile:
                out["best_calmar"] = {
                    "label": "最佳全局中位 Calmar",
                    "desc": "通达信参数扫描选出的全市场中位 Calmar。",
                    "low_hint": "值较低时，持仓期可能更偏快进快出。",
                    "high_hint": "值较高时，持仓窗口更有历史有效性。",
                    "value": profile["best_calmar"],
                }

        return out

    def warmup_all(self) -> None:
        """After default profile is ready, pre-warm only unified-stock-pool profiles."""
        profile_ids = [pid for pid in self.unified_profile_ids() if pid != self._default_profile_id]

        def _worker() -> None:
            while True:
                with self._lock:
                    pending = [
                        pid for pid in profile_ids
                        if pid not in self._data_cache and pid not in self._computing
                    ]
                if not pending:
                    return
                self.start(pending[0], force=False)

        for _ in range(min(MAX_WARMUP_WORKERS, len(profile_ids))):
            threading.Thread(target=_worker, daemon=True).start()

    def start(self, profile_id: str | None = None, force: bool = False, clear_cache: bool = False) -> None:
        pid = profile_id or self.active_profile_id()

        with self._lock:
            if pid not in self._profiles:
                raise KeyError(pid)
            # Prevent duplicate computation
            if pid in self._computing:
                return
            if pid in self._data_cache and not force:
                # Already computed; if this is now the active profile, mark ready
                if pid == self._active_profile_id:
                    self._ready = True
                    self._message = f"就绪（{self._profiles[pid]['name']}）"
                return
            self._computing.add(pid)
            self._started = True  # legacy flag

        profile = self._profiles[pid]
        is_active = pid == self.active_profile_id()
        if is_active:
            with self._lock:
                self._ready = False
                self._message = f"准备计算（{profile['name']}）"
        t0 = time.time()

        if clear_cache:
            invalidate_data_freshness()
            cache_file = _safe_cache_path(pid)
            if cache_file.exists():
                cache_file.unlink()
            if UNIFIED_CACHE_FILE.exists():
                UNIFIED_CACHE_FILE.unlink()

        def _msg(m: str) -> None:
            """Only update status message when computing the active profile."""
            with self._lock:
                if pid == self._active_profile_id:
                    self._message = m

        try:
            _msg("读取元数据...")
            _require_source_dbs()
            mkt = duckdb.connect(str(MARKET_DB), read_only=True)
            try:
                try:
                    _attach_smart_db(mkt)
                    meta_rows = mkt.execute(
                        """
                        SELECT s.stock_code, s.stock_name,
                               COALESCE(a.tdx_l1_name, '未知') AS industry,
                               COALESCE(a.stock_archetype, '未知') AS archetype,
                               COALESCE(f.holder_count_change_pct, 0.0) AS holder_chg_pct
                        FROM sm.dim_active_a_stock s -- rule-compliance: ok evidence=bc_absorbed universe + ST filter inline
                        LEFT JOIN sm.dim_stock_archetype_latest a ON s.stock_code = a.stock_code
                        LEFT JOIN sm.dim_financial_latest f ON s.stock_code = f.stock_code
                        WHERE s.stock_name NOT LIKE 'ST%' AND s.stock_name NOT LIKE '*ST%'
                        """
                    ).fetchall()
                except duckdb.IOException:
                    meta_rows = mkt.execute(
                        """
                        SELECT DISTINCT code, code AS stock_name,
                               '未知' AS industry,
                               '未知' AS archetype,
                               0.0 AS holder_chg_pct
                        FROM v_price_kline_qfq
                        """
                    ).fetchall()
            finally:
                mkt.close()

            meta = {normalize_code(code): tuple(row) for code, *row in meta_rows}

            formula_hits = {}
            if profile.get("formula_filter_mode"):
                _msg("加载选股公式命中字段...")
                formula_hits = _load_formula_hits()

            _msg("计算历史回测指标...")

            def prog(done, total):
                _msg(f"历史回测 {done}/{total} ({done*100//max(total,1)}%)")

            hist = compute_historical(profile, progress_cb=prog)

            _msg("计算当前 MACD 状态...")
            current = compute_current(meta, profile, formula_hits)

            _msg("合并历史与当前数据...")
            for row in current:
                h = hist.get(row["code"])
                if h and int(h.get("signal_count") or 0) > 0:
                    row.update(h)
                    row["has_history"] = True
                else:
                    row["has_history"] = False
                    row["signal_count"] = 0
                    row["win_rate"] = None
                    row["avg_ret"] = None
                    row["avg_dd"] = None
                    row["calmar"] = None
                if h:
                    row["history_status"]   = h.get("history_status") or "pending"
                    row["horizons"]         = h.get("horizons") or {}
                    row["best_holding_days"] = h.get("best_holding_days")
                    eff = h.get("effectiveness") or {}
                    row["effectiveness"] = eff
                    row["effectiveness_score"] = eff.get("score")
                    row["effectiveness_label"] = eff.get("label") or "样本不足"
                    row["effectiveness_total_n"] = eff.get("total_n")
                    row["effectiveness_recent_n"] = eff.get("recent_n")
                    exe = h.get("execution") or {}
                    row["execution"] = exe
                    row["untradable_rate"] = exe.get("untradable_rate")
                    row["skipped_buy_rate"] = exe.get("skipped_buy_rate")
                    row["completion_rate"] = exe.get("completion_rate")
                else:
                    row["history_status"]   = row.get("history_status") or "none"
                    row["horizons"]         = {}
                    row["best_holding_days"] = None
                    row["effectiveness"] = {}
                    row["effectiveness_score"] = None
                    row["effectiveness_label"] = "样本不足"
                    row["effectiveness_total_n"] = 0
                    row["effectiveness_recent_n"] = 0
                    row["execution"] = {}
                    row["untradable_rate"] = None
                    row["skipped_buy_rate"] = None
                    row["completion_rate"] = None

                ref_hp = int(row.get("best_holding_days") or profile["holding_days"])
                latest_trade = row.get("latest_trade") or {}
                latest_horizons = row.get("latest_trade_horizons") or {}
                ref_trade = latest_horizons.get(ref_hp) or latest_horizons.get(str(ref_hp)) or {}
                row["trade_ref_holding_days"] = ref_hp
                row["trade_signal_date"] = latest_trade.get("signal_date")
                row["trade_buy_date"] = latest_trade.get("buy_date")
                row["trade_buy_price"] = latest_trade.get("buy_price")
                row["trade_latest_date"] = latest_trade.get("latest_date")
                row["trade_latest_price"] = latest_trade.get("latest_price")
                row["trade_elapsed_days"] = latest_trade.get("elapsed_trading_days")
                row["trade_latest_ret"] = latest_trade.get("latest_ret")
                row["trade_pending_buy"] = bool(latest_trade.get("pending_buy"))
                row["trade_pending_reason"] = latest_trade.get("pending_reason")
                row["trade_target_sell_date"] = ref_trade.get("target_sell_date")
                row["trade_target_sell_price"] = ref_trade.get("target_sell_price")
                row["trade_eval_date"] = ref_trade.get("eval_date")
                row["trade_eval_price"] = ref_trade.get("eval_price")
                row["trade_ref_ret"] = ref_trade.get("ret")
                row["trade_ref_max_dd"] = ref_trade.get("max_dd")
                row["trade_reached_target"] = ref_trade.get("reached_target")
                row["trade_remaining_days"] = ref_trade.get("remaining_days")

                # Compute buy-point signal and composite score after merging hist data
                is_just  = row["status"] == S_JUST
                days_ev  = row.get("days_event") or 99
                fp       = row.get("filter_pass", False)
                wr       = row.get("win_rate") or 0.0
                cal      = row.get("calmar")   or 0.0
                avg_ret  = row.get("avg_ret") or 0.0
                eff_score = row.get("effectiveness_score")
                eff_label = row.get("effectiveness_label") or ""
                has_hist = row["has_history"]
                hist_positive = bool(has_hist and wr >= 0.50 and avg_ret > 0)
                eff_ok = bool(
                    eff_score is not None
                    and float(eff_score) >= 50
                    and eff_label not in {"退化中", "样本陈旧"}
                )
                untradable_rate = row.get("untradable_rate")
                execution_ok = untradable_rate is None or float(untradable_rate) <= 0.20

                row["is_today_candidate"] = bool(is_just and days_ev <= 3 and fp and hist_positive and eff_ok and execution_ok)
                row["is_strong_pick"] = bool(
                    row["is_today_candidate"] and has_hist and wr >= 0.48 and cal >= 0.5
                )
                row["is_buy_point"] = row["is_strong_pick"]

                # buy_score: 0-100 composite for ranking today's picks
                score = 0.0
                if is_just:
                    score += 40 if days_ev <= 1 else (30 if days_ev <= 2 else 18)
                elif row["status"] == S_IMMIN:
                    score += 8
                if fp:
                    score += 15   # bonus for passing volume/position filter
                if has_hist:
                    score += min(wr * 25, 25)
                    score += min(cal * 4, 10)
                    if (row.get("avg_ret") or 0) > 0:
                        score += 5
                    if (row.get("avg_dd") or -1) > -0.08:
                        score += 5
                row["buy_score"] = round(score, 1)

            current.sort(
                key=lambda x: (
                    x["status_order"],
                    -(x["calmar"] if x["calmar"] is not None else -999),
                    x["code"],
                )
            )

            industries = sorted({r["industry"] for r in current if r["industry"] != "未知"})
            archetypes = sorted({r["archetype"] for r in current if r["archetype"] != "未知"})

            summary = {
                "total": len(current),
                "just_cross": sum(1 for r in current if r["status"] == S_JUST),
                "imminent": sum(1 for r in current if r["status"] == S_IMMIN),
                "holding": sum(1 for r in current if r["status"] == S_HOLD),
                "death": sum(1 for r in current if r["status"] == S_DEATH),
                "waiting": sum(1 for r in current if r["status"] == S_WAIT),
                "with_history": sum(1 for r in current if r["has_history"]),
                "today_candidates": sum(1 for r in current if r.get("is_today_candidate")),
                "strong_picks": sum(1 for r in current if r.get("is_strong_pick")),
                "f1_hits": sum(1 for r in current if r["f1_hit"]),
                "f3_hits": sum(1 for r in current if r["f3_hit"]),
                "f5_hits": sum(1 for r in current if r["f5_hit"]),
                "elapsed": round(time.time() - t0, 1),
            }

            data = {
                "stocks": current,
                "summary": summary,
                "industries": industries,
                "archetypes": archetypes,
                "params": {
                    "macd": f"EMA({profile['macd_fast']}/{profile['macd_slow']}/{profile['macd_signal']})",
                    "holding_days": profile["holding_days"],
                    "vol_min": profile.get("vol_ratio_min", 1.0),
                    "amt_min": profile["amt_ratio_min"],
                    "price_max": profile["price_pos_max"],
                    "min_signals": profile.get("min_signals", 1),
                    "source": profile.get("source", "内置"),
                },
                "param_descriptions": self._build_param_desc_payload(profile),
                "profile": self._build_profile_payload(profile),
                "profile_id": profile["id"],
                "computed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                **get_data_freshness(),
            }

            with self._lock:
                self._data_cache[pid] = data
                self._unified_cache = None
                self._unified_cache_profile_ids = ()
                self._computing.discard(pid)
                self._started = False
                if pid == self._active_profile_id:
                    self._ready = True
                    self._message = f"就绪（{profile['name']}）耗时 {summary['elapsed']} 秒"

            # After the default profile finishes, warm up all others in background
            if pid == self._default_profile_id and os.environ.get("BESTCHOICE_SKIP_WARMUP") != "1":
                self.warmup_all()

        except Exception as e:  # pragma: no cover
            with self._lock:
                if pid == self._active_profile_id:
                    self._message = f"计算出错: {e}"
                self._ready = False
                self._computing.discard(pid)
                self._started = False
            raise

    def restart(self, profile_id: str | None = None, clear_cache: bool = False, activate: bool = True) -> None:
        pid = profile_id or self.active_profile_id()
        if pid not in self._profiles:
            raise KeyError(pid)

        # Evict stale in-memory cache for this profile so it recomputes
        with self._lock:
            if activate:
                self._active_profile_id = pid
            self._data_cache.pop(pid, None)
            self._unified_cache = None
            self._unified_cache_profile_ids = ()
            if clear_cache and UNIFIED_CACHE_FILE.exists():
                UNIFIED_CACHE_FILE.unlink()
            if pid == self._active_profile_id:
                self._ready = False
        threading.Thread(target=self.start, args=(pid,), kwargs={"force": True, "clear_cache": clear_cache}, daemon=True).start()

    def status(self, profile_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            computing = list(self._computing)
            active = self._active_profile_id
            ready = self._ready
            message = self._message

            if profile_id is not None:
                if profile_id not in self._profiles:
                    raise KeyError(profile_id)
                ready = profile_id in self._data_cache
                if ready:
                    message = f"就绪（{self._profiles[profile_id]['name']}）"
                elif profile_id in self._computing:
                    message = f"计算策略（{self._profiles[profile_id]['name']}）..."
                else:
                    message = f"等待计算（{self._profiles[profile_id]['name']}）"

        return {
            "ready": ready,
            "message": message,
            "active_profile_id": active,
            "default_profile_id": self._default_profile_id,
            "profile_id": profile_id or active,
            "computing_profiles": computing,
        }

    def data(self) -> Optional[dict[str, Any]]:
        return self.data_for_profile()

    def data_for_profile(self, profile_id: str | None = None) -> Optional[dict[str, Any]]:
        pid = profile_id or self.active_profile_id()
        return self._data_cache.get(pid)
