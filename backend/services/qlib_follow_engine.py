"""
qlib_follow_engine.py — Qlib 作为跟随信号的"第二意见"模型

与 qlib_full_engine.py 的差异：
  qlib_full_engine:  label = Ref(close,-2)/Ref(close,-1)-1 (日内短线)
                     用途：股票维度的 cross-sectional ranking
  qlib_follow_engine: label = gain_60d (事件跟随收益)
                     用途：对每个机构事件，预测"如果现在跟，60 天能赚多少"

本模块是 signals_v2 KNN 的"第二意见"——不合成主评分，并排展示让用户看两条独立证据链。

设计：
  - 不动 qlib_full_engine（兼容老链路）
  - 训练数据：fact_institution_event 里所有 matured buy 事件
    (gain_60d IS NOT NULL)
  - 特征：
    * 事件级：premium_pct / peer_count / institution_industry_hit_rate
    * 股票价格：Alpha158 的 "KMID/KLEN/KUP/KLOW" 等 20 个精选因子
    * 股票财务：ROE / debt_ratio / gross_margin / revenue_yoy 等 GPCW
  - 滚动训练：每次训练只用 past 36 个月，老样本丢弃（regime drift 对抗）
  - 输出表：qlib_follow_predictions (event_id, predicted_return, confidence, top_contributors_json)

当前版本是骨架+训练流水线，实际训练要等下一步（需要 qlib 环境预处理）。
"""

from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("cm-api")

_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "qlib_follow_models"
_MODEL_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FollowTrainConfig:
    """Qlib follow-return 训练配置。"""
    horizon_days: int = 60                 # 必须与 signals_v2 一致
    rolling_window_months: int = 36        # 训练集滚动窗口（对抗 regime drift）
    min_train_samples: int = 500           # 低于此值不训练
    valid_months: int = 3                  # 验证集占最后几个月
    num_boost_round: int = 300
    learning_rate: float = 0.05
    num_leaves: int = 48
    early_stopping_rounds: int = 30


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────

def ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS qlib_follow_model_state (
            model_id        TEXT PRIMARY KEY,
            status          TEXT NOT NULL DEFAULT 'idle',
            train_window_start TEXT,
            train_window_end   TEXT,
            valid_start        TEXT,
            valid_end          TEXT,
            n_samples          INTEGER,
            n_features         INTEGER,
            valid_r2           REAL,
            valid_mae          REAL,
            valid_ic           REAL,
            model_path         TEXT,
            train_params_json  TEXT,
            feature_importance_json TEXT,
            created_at         TEXT,
            finished_at        TEXT,
            error              TEXT
        );

        CREATE TABLE IF NOT EXISTS qlib_follow_predictions (
            model_id        TEXT NOT NULL,
            institution_id  TEXT NOT NULL,
            stock_code      TEXT NOT NULL,
            report_date     TEXT NOT NULL,
            predicted_return_pct REAL,
            confidence_pct  REAL,
            top_contributors_json TEXT,
            created_at      TEXT,
            PRIMARY KEY (model_id, institution_id, stock_code, report_date)
        );
        CREATE INDEX IF NOT EXISTS idx_qfp_event
            ON qlib_follow_predictions(institution_id, stock_code, report_date);
    """)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────
# 特征抽取（从数据库 join 构造训练矩阵，不依赖 qlib 运行时）
# ─────────────────────────────────────────────────────────────────────

# TDX 一级行业 one-hot (与 qlib_full_engine 保持一致：T01..T13)
_TDX_L1_ONEHOT_CODES = tuple(f"T{i:02d}" for i in range(1, 14))
_TDX_L1_ONEHOT_FEATURES = tuple(f"ind_t{i:02d}" for i in range(1, 14))


FEATURE_COLUMNS = [
    # 事件级
    "premium_pct",
    "peer_count_same_quarter",
    "institution_industry_hit_rate",
    "event_type_is_new_entry",
    "days_since_industry_latest_high",
    # 股票财务（from raw_gpcw_detail / fact_financial_derived）
    "roe",
    "debt_ratio",
    "gross_margin",
    "revenue_yoy",
    "profit_yoy",
    "ocf_to_profit",
    "contract_to_revenue",
    # D1-D8 挖过的 alpha 维度
    "holder_count_yoy",              # D1
    "contract_liabilities_yoy",      # D2
    "forecast_profit_yoy_mid",       # D3
    "future_unlock_ratio_180d",      # D5
    "inst_recent_ev_60d",            # D7
    "survey_count_90d",              # D8
    # 行业内 z-score (Phase 4c 方案 B):
    # YoY 类特征跨行业基数差异大 (银行 +30% vs 科技 +300% 语义不同),
    # 按 (tdx_l1, report_date) 分组归一化让树模型捕捉同期相对强度
    "holder_count_yoy_z",
    "contract_liabilities_yoy_z",
    "forecast_profit_yoy_mid_z",
    # 价格动量（from price_kline，窗口聚合）
    "return_20d_before",
    "return_60d_before",
    "volatility_60d",
    "dist_from_120d_high",
    # TDX 一级行业 one-hot (13 维)
    *_TDX_L1_ONEHOT_FEATURES,
]

LABEL_COLUMN = "gain_60d"


def _iso_date(d) -> Optional[str]:
    """把 YYYYMMDD 或 YYYY-MM-DD 统一成 YYYY-MM-DD，None/空→None。"""
    if d is None:
        return None
    s = str(d)
    if not s:
        return None
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _shift_days(iso: str, days: int) -> str:
    dt = datetime.strptime(iso, "%Y-%m-%d") + timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def _prev_year_quarter(report_date: str) -> Optional[str]:
    """'2025-09-30' → '2024-09-30'. 失败返回 None。"""
    try:
        dt = datetime.strptime(report_date, "%Y-%m-%d")
        return dt.replace(year=dt.year - 1).strftime("%Y-%m-%d")
    except Exception:
        return None


def _yoy(curr, prev) -> Optional[float]:
    """YoY 增长率 %。prev<=0 或为 None 返回 None（避免基数异常）。"""
    try:
        if curr is None or prev is None:
            return None
        c = float(curr); p = float(prev)
        if p <= 0:
            return None
        return (c - p) / p * 100.0
    except Exception:
        return None


def extract_training_matrix(
    conn,
    mkt_conn,
    *,
    window_start: str,
    window_end: str,
) -> tuple[list[dict], list[str]]:
    """
    从数据库抽取训练样本。
    返回 (rows, feature_names)，每 row 是 dict: {feature1: val, ..., label: gain_60d, meta: {...}}

    注意：本函数当前只实现骨架 + 前 10 个特征（依赖已有字段）。后续迭代补全。
    """
    rows = conn.execute("""
        SELECT
            e.institution_id, e.stock_code, e.report_date, e.notice_date,
            e.event_type, e.premium_pct, e.gain_60d,
            i.tdx_l1 AS industry
        FROM fact_institution_event e
        LEFT JOIN dim_stock_tdx_industry i ON i.stock_code = e.stock_code
        WHERE e.event_type IN ('new_entry', 'increase')
          AND e.gain_60d IS NOT NULL
          AND e.notice_date >= ?
          AND e.notice_date <= ?
        ORDER BY e.notice_date ASC
    """, (window_start, window_end)).fetchall()

    # 同期 peer_count 预聚合（同 stock_code + report_date 的事件数）
    peer_rows = conn.execute("""
        SELECT stock_code, report_date, COUNT(*) AS n
        FROM fact_institution_event
        WHERE event_type IN ('new_entry', 'increase') AND gain_60d IS NOT NULL
        GROUP BY stock_code, report_date
    """).fetchall()
    peer_map = {(r["stock_code"], r["report_date"]): int(r["n"]) for r in peer_rows}

    # 机构×行业历史胜率预聚合（简化版：全时段胜率，严谨版应 as-of）
    inst_ind_rows = conn.execute("""
        SELECT e.institution_id, i.tdx_l1 AS industry,
               AVG(CASE WHEN e.gain_60d > 0 THEN 1.0 ELSE 0.0 END) AS hit_rate,
               COUNT(*) AS n
        FROM fact_institution_event e
        LEFT JOIN dim_stock_tdx_industry i ON i.stock_code = e.stock_code
        WHERE e.event_type IN ('new_entry', 'increase') AND e.gain_60d IS NOT NULL
        GROUP BY e.institution_id, i.tdx_l1
    """).fetchall()
    ind_hit_map = {
        (r["institution_id"], r["industry"]): float(r["hit_rate"] or 0)
        for r in inst_ind_rows if (r["n"] or 0) >= 5
    }

    out = []
    for r in rows:
        peer = peer_map.get((r["stock_code"], r["report_date"]), 1) - 1  # 不含自己
        ind_hit = ind_hit_map.get((r["institution_id"], r["industry"]))

        industry_code = r["industry"]
        sample = {
            "institution_id": r["institution_id"],
            "stock_code": r["stock_code"],
            "report_date": r["report_date"],
            "notice_date": r["notice_date"],
            "industry": industry_code,
            # 事件级特征
            "premium_pct": _safe(r["premium_pct"]),
            "peer_count_same_quarter": peer,
            "institution_industry_hit_rate": ind_hit,
            "event_type_is_new_entry": 1 if r["event_type"] == "new_entry" else 0,
            # 占位：其它特征从 fact_financial_derived / price_kline 填补
            "roe": None,
            "debt_ratio": None,
            "gross_margin": None,
            "revenue_yoy": None,
            "profit_yoy": None,
            "ocf_to_profit": None,
            "contract_to_revenue": None,
            "holder_count_yoy": None,
            "contract_liabilities_yoy": None,
            "forecast_profit_yoy_mid": None,
            "future_unlock_ratio_180d": None,
            "inst_recent_ev_60d": None,
            "survey_count_90d": None,
            "holder_count_yoy_z": None,
            "contract_liabilities_yoy_z": None,
            "forecast_profit_yoy_mid_z": None,
            "return_20d_before": None,
            "return_60d_before": None,
            "volatility_60d": None,
            "dist_from_120d_high": None,
            "days_since_industry_latest_high": None,
            # TDX L1 one-hot (13 维)
            **{feat: (1 if industry_code == code else 0)
               for code, feat in zip(_TDX_L1_ONEHOT_CODES, _TDX_L1_ONEHOT_FEATURES)},
            # Label
            "gain_60d": _safe(r["gain_60d"]),
        }
        out.append(sample)

    # 补：财务特征（join dim_financial_latest if 存在）
    try:
        fin_rows = conn.execute("""
            SELECT stock_code, roe, debt_ratio, gross_margin,
                   revenue_yoy, profit_yoy, ocf_to_profit, contract_to_revenue
            FROM dim_financial_latest
        """).fetchall()
        fin_map = {fr["stock_code"]: dict(fr) for fr in fin_rows}
        for s in out:
            f = fin_map.get(s["stock_code"]) or {}
            for col in ("roe", "debt_ratio", "gross_margin",
                        "revenue_yoy", "profit_yoy", "ocf_to_profit",
                        "contract_to_revenue"):
                s[col] = _safe(f.get(col))
    except Exception as exc:
        logger.warning(f"[qlib_follow] 财务特征补全失败（可能表不存在）: {exc}")

    # 补：D1 holder_count_yoy / D2 contract_liabilities_yoy / D3 forecast_profit_yoy_mid
    # 匹配 (stock_code, report_date)，再 join 去年同季度做 YoY
    # 注意：fact_institution_event.report_date 是 YYYYMMDD，
    # raw_gpcw_detail.report_date 是 YYYY-MM-DD，需要归一化。
    try:
        gpcw_rows = conn.execute("""
            SELECT stock_code, report_date, holder_count, contract_liabilities_wan,
                   forecast_profit_yoy_low, forecast_profit_yoy_high
            FROM raw_gpcw_detail
        """).fetchall()
        gpcw_map = {(g["stock_code"], _iso_date(g["report_date"])): dict(g) for g in gpcw_rows}
        for s in out:
            rd_iso = _iso_date(s["report_date"])
            if not rd_iso:
                continue
            curr = gpcw_map.get((s["stock_code"], rd_iso))
            if not curr:
                continue
            # D3: 预告 YoY 中值 (low+high)/2 — 直接读当季预告值
            lo = _safe(curr.get("forecast_profit_yoy_low"))
            hi = _safe(curr.get("forecast_profit_yoy_high"))
            if lo is not None and hi is not None:
                s["forecast_profit_yoy_mid"] = (lo + hi) / 2.0
            elif lo is not None:
                s["forecast_profit_yoy_mid"] = lo
            elif hi is not None:
                s["forecast_profit_yoy_mid"] = hi
            # D1/D2: YoY 需要去年同季度
            prev_rd = _prev_year_quarter(rd_iso)
            if not prev_rd:
                continue
            prev = gpcw_map.get((s["stock_code"], prev_rd))
            if not prev:
                continue
            s["holder_count_yoy"] = _yoy(curr.get("holder_count"), prev.get("holder_count"))
            s["contract_liabilities_yoy"] = _yoy(
                curr.get("contract_liabilities_wan"),
                prev.get("contract_liabilities_wan"),
            )
    except Exception as exc:
        logger.warning(f"[qlib_follow] GPCW 衍生特征补全失败: {exc}")

    # Phase 4c · 方案 B: D1/D2/D3 行业内 z-score
    # 按 (tdx_l1, report_date) 分组, 组内 ≥5 样本才计算 z, 否则保留 None
    for raw_col, z_col in (
        ("holder_count_yoy", "holder_count_yoy_z"),
        ("contract_liabilities_yoy", "contract_liabilities_yoy_z"),
        ("forecast_profit_yoy_mid", "forecast_profit_yoy_mid_z"),
    ):
        groups: dict = {}
        for s in out:
            key = (s.get("industry"), s.get("report_date"))
            if s.get(raw_col) is None or key[0] is None:
                continue
            groups.setdefault(key, []).append(s[raw_col])
        group_stats: dict = {}
        for key, vals in groups.items():
            if len(vals) < 5:
                continue
            m = sum(vals) / len(vals)
            var = sum((v - m) ** 2 for v in vals) / len(vals)
            std = var ** 0.5
            if std > 0:
                group_stats[key] = (m, std)
        for s in out:
            v = s.get(raw_col)
            if v is None:
                continue
            stats = group_stats.get((s.get("industry"), s.get("report_date")))
            if stats:
                m, std = stats
                s[z_col] = (v - m) / std

    # 补：D5 future_unlock_ratio_180d — 按 stock_code 聚合未来 180d 解禁占流通市值比
    try:
        unlock_rows = conn.execute("""
            SELECT stock_code, unlock_date, unlock_ratio_float_mkt
            FROM raw_capital_unlock
            WHERE unlock_ratio_float_mkt IS NOT NULL
        """).fetchall()
        unlock_by_stock: dict = {}
        for u in unlock_rows:
            iso = _iso_date(u["unlock_date"])
            if not iso:
                continue
            unlock_by_stock.setdefault(u["stock_code"], []).append(
                (iso, float(u["unlock_ratio_float_mkt"] or 0.0))
            )
        for s in out:
            anchor = _iso_date(s["notice_date"])
            if not anchor:
                continue
            end = _shift_days(anchor, 180)
            total = sum(
                ratio for d, ratio in unlock_by_stock.get(s["stock_code"], [])
                if anchor < d <= end
            )
            s["future_unlock_ratio_180d"] = total if total > 0 else 0.0
    except Exception as exc:
        logger.warning(f"[qlib_follow] D5 解禁特征补全失败: {exc}")

    # 补：D7 inst_recent_ev_60d — 机构在此事件前已成熟 (gain_60d 已观察) 的所有事件平均收益
    #     as-of 严谨：排除 notice_date >= 当前事件 notice_date - 60d 的事件 (gain_60d 未观察)
    try:
        all_inst_rows = conn.execute("""
            SELECT institution_id, notice_date, gain_60d
            FROM fact_institution_event
            WHERE event_type IN ('new_entry', 'increase')
              AND gain_60d IS NOT NULL
              AND notice_date IS NOT NULL
            ORDER BY institution_id, notice_date ASC
        """).fetchall()
        inst_history: dict = {}
        for ev in all_inst_rows:
            iso = _iso_date(ev["notice_date"])
            if not iso:
                continue
            inst_history.setdefault(ev["institution_id"], []).append(
                (iso, float(ev["gain_60d"]))
            )
        for s in out:
            anchor = _iso_date(s["notice_date"])
            if not anchor:
                continue
            cutoff = _shift_days(anchor, -60)
            past = [g for d, g in inst_history.get(s["institution_id"], []) if d < cutoff]
            if len(past) >= 3:
                s["inst_recent_ev_60d"] = sum(past) / len(past)
    except Exception as exc:
        logger.warning(f"[qlib_follow] D7 机构近期 EV 补全失败: {exc}")

    # 补：D8 survey_count_90d — 事件 notice_date 前 90d 内该股的调研次数
    try:
        survey_rows = conn.execute("""
            SELECT stock_code, survey_date
            FROM raw_institution_surveys
            WHERE survey_date IS NOT NULL
        """).fetchall()
        surveys_by_stock: dict = {}
        for sv in survey_rows:
            iso = _iso_date(sv["survey_date"])
            if not iso:
                continue
            surveys_by_stock.setdefault(sv["stock_code"], []).append(iso)
        for s in out:
            anchor = _iso_date(s["notice_date"])
            if not anchor:
                continue
            start = _shift_days(anchor, -90)
            cnt = sum(1 for d in surveys_by_stock.get(s["stock_code"], []) if start <= d < anchor)
            s["survey_count_90d"] = cnt
    except Exception as exc:
        logger.warning(f"[qlib_follow] D8 调研特征补全失败: {exc}")

    # 补：价格特征（从 market_data.db）
    try:
        for s in out:
            anchor = s["notice_date"]
            s.update(_compute_price_features(mkt_conn, s["stock_code"], anchor))
    except Exception as exc:
        logger.warning(f"[qlib_follow] 价格特征补全失败: {exc}")

    return out, FEATURE_COLUMNS


def _compute_price_features(mkt_conn, stock_code: str, anchor_date: str) -> dict:
    """计算股票在 anchor_date 之前的价格动量/波动率特征。"""
    # normalize date
    if len(str(anchor_date)) == 8:
        anchor_iso = f"{anchor_date[:4]}-{anchor_date[4:6]}-{anchor_date[6:8]}"
    else:
        anchor_iso = str(anchor_date)

    rows = mkt_conn.execute("""
        SELECT date, close
        FROM price_kline
        WHERE code = ? AND freq='daily' AND adjust='qfq'
          AND date < ?
        ORDER BY date DESC
        LIMIT 120
    """, (stock_code, anchor_iso)).fetchall()

    if len(rows) < 20:
        return {"return_20d_before": None, "return_60d_before": None,
                "volatility_60d": None, "dist_from_120d_high": None}

    closes = [float(r["close"]) for r in rows if r["close"]]
    latest = closes[0]
    r20 = (latest / closes[min(20, len(closes)-1)] - 1) * 100 if len(closes) > 20 else None
    r60 = (latest / closes[min(60, len(closes)-1)] - 1) * 100 if len(closes) > 60 else None
    # 波动率：60d 日收益率标准差
    vol = None
    if len(closes) > 60:
        daily_rets = [(closes[i-1] / closes[i] - 1) for i in range(1, 61)]
        mean = sum(daily_rets) / len(daily_rets)
        var = sum((x - mean) ** 2 for x in daily_rets) / len(daily_rets)
        vol = (var ** 0.5) * 100
    # 距离 120d 高点
    high_120 = max(closes[:min(120, len(closes))])
    dist_high = (latest / high_120 - 1) * 100 if high_120 > 0 else None

    return {
        "return_20d_before": round(r20, 2) if r20 is not None else None,
        "return_60d_before": round(r60, 2) if r60 is not None else None,
        "volatility_60d": round(vol, 2) if vol is not None else None,
        "dist_from_120d_high": round(dist_high, 2) if dist_high is not None else None,
    }


def _safe(v):
    if v is None:
        return None
    try:
        f = float(v)
        import math
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# 训练入口（骨架，实际跑需要 lightgbm）
# ─────────────────────────────────────────────────────────────────────

def train_follow_model(
    conn,
    mkt_conn,
    *,
    config: Optional[FollowTrainConfig] = None,
    train_end_date: Optional[str] = None,
) -> dict:
    """
    训练一次"事件→跟随收益"模型。

    实现策略（滚动窗口对抗 regime drift）：
      - 训练窗口：[train_end - rolling_window_months, train_end - valid_months]
      - 验证窗口：[train_end - valid_months, train_end]
      - 测试：调用方在 train_end 之后的样本做 OOS 预测

    返回：{model_id, status, n_samples, valid_metrics, feature_importance}
    """
    cfg = config or FollowTrainConfig()
    ensure_tables(conn)

    if train_end_date is None:
        train_end_date = datetime.now().strftime("%Y%m%d")

    # 计算窗口
    end_dt = datetime.strptime(train_end_date, "%Y%m%d")
    valid_start_dt = end_dt - timedelta(days=cfg.valid_months * 30)
    train_start_dt = valid_start_dt - timedelta(days=cfg.rolling_window_months * 30)
    train_start = train_start_dt.strftime("%Y%m%d")
    valid_start = valid_start_dt.strftime("%Y%m%d")

    logger.info(
        f"[qlib_follow] 训练窗口 {train_start}~{valid_start}, "
        f"验证 {valid_start}~{train_end_date}"
    )

    # 抽特征
    samples, feature_names = extract_training_matrix(
        conn, mkt_conn,
        window_start=train_start,
        window_end=train_end_date,
    )

    if len(samples) < cfg.min_train_samples:
        return {
            "status": "skipped",
            "reason": f"样本不足 ({len(samples)} < {cfg.min_train_samples})",
            "train_window": (train_start, train_end_date),
        }

    # 切训练/验证
    train = [s for s in samples if s["notice_date"] < valid_start]
    valid = [s for s in samples if s["notice_date"] >= valid_start]

    model_id = f"qlib_follow_{end_dt.strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat()

    try:
        import lightgbm as lgb  # noqa
        import numpy as np
    except ImportError as exc:
        # 无 lightgbm 环境：记录骨架信息不训练
        logger.warning(f"[qlib_follow] lightgbm 未装，仅记录训练计划: {exc}")
        conn.execute("""
            INSERT OR REPLACE INTO qlib_follow_model_state
            (model_id, status, train_window_start, train_window_end,
             valid_start, valid_end, n_samples, n_features,
             train_params_json, created_at, error)
            VALUES (?, 'planned', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            model_id, train_start, valid_start,
            valid_start, train_end_date,
            len(train), len(feature_names),
            json.dumps(asdict(cfg)), now,
            "lightgbm not installed",
        ))
        conn.commit()
        return {
            "model_id": model_id,
            "status": "planned",
            "n_train": len(train),
            "n_valid": len(valid),
            "features": feature_names,
            "note": "lightgbm 未安装，仅规划未训练",
        }

    # 真实训练
    def _to_matrix(rows):
        X, y = [], []
        for r in rows:
            x = [r.get(col) for col in feature_names]
            # 跳过所有特征都缺失的样本
            if all(v is None for v in x):
                continue
            label = r.get(LABEL_COLUMN)
            if label is None:
                continue
            X.append(x)
            y.append(label)
        return np.array(X, dtype=float), np.array(y, dtype=float)

    X_train, y_train = _to_matrix(train)
    X_valid, y_valid = _to_matrix(valid)

    if len(X_train) < cfg.min_train_samples:
        return {"status": "skipped", "reason": f"有效训练样本 {len(X_train)} 不足"}

    train_ds = lgb.Dataset(X_train, label=y_train, feature_name=feature_names)
    valid_ds = lgb.Dataset(X_valid, label=y_valid, feature_name=feature_names, reference=train_ds)

    params = {
        "objective": "regression",
        "metric": ["l2", "l1"],
        "learning_rate": cfg.learning_rate,
        "num_leaves": cfg.num_leaves,
        "min_data_in_leaf": 20,
        "verbose": -1,
    }
    model = lgb.train(
        params, train_ds,
        num_boost_round=cfg.num_boost_round,
        valid_sets=[valid_ds],
        callbacks=[lgb.early_stopping(cfg.early_stopping_rounds, verbose=False)],
    )

    # 验证指标
    y_pred = model.predict(X_valid)
    mae = float(np.mean(np.abs(y_pred - y_valid))) if len(y_valid) else None
    ss_res = float(np.sum((y_valid - y_pred) ** 2)) if len(y_valid) else None
    ss_tot = float(np.sum((y_valid - np.mean(y_valid)) ** 2)) if len(y_valid) else 0
    r2 = (1 - ss_res / ss_tot) if ss_tot else None
    # IC（Information Coefficient，相关系数）
    ic = None
    if len(y_valid) > 5:
        ic = float(np.corrcoef(y_pred, y_valid)[0, 1])

    # 持久化模型
    model_path = str(_MODEL_DIR / f"{model_id}.pkl")
    with open(model_path, "wb") as f:
        pickle.dump({"model": model, "feature_names": feature_names}, f)

    # 特征重要性
    importance = dict(zip(feature_names, model.feature_importance().tolist()))

    conn.execute("""
        INSERT OR REPLACE INTO qlib_follow_model_state
        (model_id, status, train_window_start, train_window_end,
         valid_start, valid_end, n_samples, n_features,
         valid_r2, valid_mae, valid_ic,
         model_path, train_params_json, feature_importance_json,
         created_at, finished_at)
        VALUES (?, 'trained', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        model_id, train_start, valid_start,
        valid_start, train_end_date,
        len(X_train), len(feature_names),
        r2, mae, ic,
        model_path, json.dumps(asdict(cfg)),
        json.dumps(importance),
        now, datetime.now().isoformat(),
    ))
    conn.commit()

    r2_str = f"{r2:.3f}" if r2 is not None else "NA"
    ic_str = f"{ic:.3f}" if ic is not None else "NA"
    logger.info(
        f"[qlib_follow] 训练完成 {model_id}: "
        f"n_train={len(X_train)}, R2={r2_str}, IC={ic_str}"
    )
    return {
        "model_id": model_id,
        "status": "trained",
        "n_train": len(X_train),
        "n_valid": len(X_valid),
        "valid_r2": r2,
        "valid_mae": mae,
        "valid_ic": ic,
        "feature_importance": importance,
    }


# ─────────────────────────────────────────────────────────────────────
# 预测入口
# ─────────────────────────────────────────────────────────────────────

def predict_for_event(
    conn,
    mkt_conn,
    *,
    institution_id: str,
    stock_code: str,
    report_date: str,
    notice_date: str,
    event_type: str,
    premium_pct: Optional[float],
    model_id: Optional[str] = None,
) -> Optional[dict]:
    """
    对一个事件用最新训练模型预测跟随收益。

    返回：
        {predicted_return_pct, confidence_pct, top_contributors: [(feat, value, shap), ...]}
        如无模型返回 None
    """
    ensure_tables(conn)
    if model_id is None:
        row = conn.execute(
            "SELECT model_id FROM qlib_follow_model_state WHERE status='trained' "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        model_id = row["model_id"]

    state_row = conn.execute(
        "SELECT model_path FROM qlib_follow_model_state WHERE model_id = ?",
        (model_id,),
    ).fetchone()
    if not state_row or not state_row["model_path"]:
        return None

    try:
        with open(state_row["model_path"], "rb") as f:
            bundle = pickle.load(f)
    except Exception as exc:
        logger.warning(f"[qlib_follow] 加载模型失败 {model_id}: {exc}")
        return None

    import numpy as np
    model = bundle["model"]
    feature_names = bundle["feature_names"]

    # 构造特征向量（复用 extract 里的行逻辑）
    samples, _ = extract_training_matrix(
        conn, mkt_conn,
        window_start=notice_date,
        window_end=notice_date,
    )
    # 找对应事件
    target = None
    for s in samples:
        if (s["institution_id"] == institution_id and
                s["stock_code"] == stock_code and
                s["report_date"] == report_date):
            target = s
            break
    if target is None:
        return None

    X = np.array([[target.get(col) for col in feature_names]], dtype=float)
    y_pred = float(model.predict(X)[0])

    # Top 3 贡献因子（简化：看特征重要性）
    importance = model.feature_importance().tolist()
    contribs = sorted(
        zip(feature_names, [target.get(c) for c in feature_names], importance),
        key=lambda x: -x[2],
    )[:3]

    return {
        "model_id": model_id,
        "predicted_return_pct": round(y_pred, 2),
        "confidence_pct": None,  # 需额外逻辑；留待后续
        "top_contributors": [
            {"feature": c[0], "value": c[1], "importance": c[2]} for c in contribs
        ],
    }
