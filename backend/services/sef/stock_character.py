"""Layer 2A · Stock Idiosyncratic Beta (股性嵌入).

对每只股票学习它对若干"类事件"的价格弹性系数。

实现路径（简化版 Event Study + CAR）:
1. 对 fact_institution_event 按 event_type 做分组:
   - new_entry / increase → beta_inst_entry
   - decrease / exit → beta_holder_decline
2. 对 raw_institution_surveys 按调研热度 → beta_survey_surge
3. 对 fact_northbound_daily 净买入突增 → beta_northbound_in
4. noise_floor = 非事件日日收益标准差
5. info_lag_days = 事件后 CAR 峰值的滞后天数
6. elasticity_sector = 对所属 TDX L1 行业等权指数的 rolling beta

最终 embedding = [beta_* , noise, lag, sector] 14+ 维，用 PCA 压到 20 维（或截断）。

Event study window: [0, +5] 日 CAR。
超额收益 = 个股收益 - 同期沪深 300 或等权全市场
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from ._dates import to_iso

logger = logging.getLogger("cm-api.sef.stock_char")


# ---------------------------------------------------------------------------
# 1) 市场基准（等权全市场日收益）
# ---------------------------------------------------------------------------


def _load_market_returns(mkt_conn: sqlite3.Connection) -> dict[str, float]:
    """计算等权全市场日收益率。返回 {date: ret}."""
    rows = mkt_conn.execute(
        """
        WITH daily_ret AS (
            SELECT code, date,
                   close / LAG(close) OVER (PARTITION BY code ORDER BY date) - 1 AS ret
            FROM price_kline
            WHERE freq='daily' AND adjust='qfq'
        )
        SELECT date, AVG(ret) AS avg_ret FROM daily_ret WHERE ret IS NOT NULL
        GROUP BY date ORDER BY date
        """
    ).fetchall()
    return {to_iso(r[0]): float(r[1]) for r in rows if r[1] is not None}


def _load_stock_returns(mkt_conn: sqlite3.Connection, stock_code: str) -> list[tuple[str, float]]:
    rows = mkt_conn.execute(
        """
        SELECT date, close / LAG(close) OVER (ORDER BY date) - 1 AS ret
        FROM price_kline
        WHERE code=? AND freq='daily' AND adjust='qfq'
        ORDER BY date
        """,
        (stock_code,),
    ).fetchall()
    return [(to_iso(r[0]), float(r[1])) for r in rows if r[1] is not None]


# ---------------------------------------------------------------------------
# 2) Event Study · CAR 计算
# ---------------------------------------------------------------------------


def _car_around_event(
    stock_returns: list[tuple[str, float]],
    market_ret: dict[str, float],
    event_date: str,
    window_days: int = 5,
) -> Optional[float]:
    """事件后 window_days 的累计超额收益 (CAR)."""
    event_iso = to_iso(event_date)
    if not event_iso:
        return None
    # 找到事件日索引
    for i, (d, _r) in enumerate(stock_returns):
        if d >= event_iso:
            break
    else:
        return None
    # 取事件日 + window_days 天
    window = stock_returns[i : i + window_days]
    if len(window) < window_days // 2:
        return None
    car = 0.0
    for d, r in window:
        mkt = market_ret.get(d, 0.0)
        car += r - mkt
    return car


# ---------------------------------------------------------------------------
# 3) 逐股计算 beta 向量
# ---------------------------------------------------------------------------


def _compute_stock_betas(
    stock_code: str,
    mkt_conn: sqlite3.Connection,
    inst_events: list[dict],
    market_ret: dict[str, float],
) -> dict:
    """返回 {beta_inst_entry, beta_holder_decline, noise_floor, info_lag_days, ...}."""
    stock_returns = _load_stock_returns(mkt_conn, stock_code)
    if len(stock_returns) < 30:
        return {}

    cars_entry = []
    cars_exit = []
    event_dates = set()
    for e in inst_events:
        et = e.get("event_type")
        d = e.get("notice_date") or e.get("report_date")
        if not d:
            continue
        event_dates.add(to_iso(d))
        car = _car_around_event(stock_returns, market_ret, d, window_days=5)
        if car is None:
            continue
        if et in ("new_entry", "increase"):
            cars_entry.append(car)
        elif et in ("decrease", "exit"):
            cars_exit.append(car)

    result: dict = {}
    if cars_entry:
        result["beta_inst_entry"] = float(np.mean(cars_entry))
    if cars_exit:
        result["beta_holder_decline"] = float(np.mean(cars_exit))

    # noise_floor：非事件日日收益标准差
    non_event_rets = [r for d, r in stock_returns if d not in event_dates]
    if len(non_event_rets) >= 20:
        result["noise_floor"] = float(np.std(non_event_rets, ddof=1))

    # info_lag_days: 取入场事件后 CAR 达到最大时的天数
    if cars_entry:
        # 细粒度 per-day：只看第一个入场事件
        for e in inst_events:
            if e.get("event_type") not in ("new_entry", "increase"):
                continue
            ed = to_iso(e.get("notice_date") or e.get("report_date"))
            if not ed:
                continue
            for i, (d, _r) in enumerate(stock_returns):
                if d >= ed:
                    break
            else:
                continue
            window = stock_returns[i : i + 20]
            if len(window) < 5:
                continue
            cum = 0.0
            best_cum = 0.0
            best_lag = 0
            for k, (d, r) in enumerate(window):
                cum += r - market_ret.get(d, 0.0)
                if cum > best_cum:
                    best_cum = cum
                    best_lag = k
            result["info_lag_days"] = float(best_lag)
            break

    return result


# ---------------------------------------------------------------------------
# 4) 主入口
# ---------------------------------------------------------------------------


def build_stock_character(
    conn: sqlite3.Connection,
    mkt_conn: sqlite3.Connection,
    *,
    limit_stocks: Optional[int] = None,
    embedding_dim: int = 20,
) -> dict:
    """构建 fact_stock_character 表（每股一行，含 beta 向量 + embedding）."""

    logger.info("[SEF Layer 2A] 加载市场收益基准 ...")
    market_ret = _load_market_returns(mkt_conn)
    logger.info("[SEF Layer 2A] 市场基准 %d 天", len(market_ret))

    # 目标股票范围：active + 有事件的股票
    stock_rows = conn.execute(
        """
        SELECT DISTINCT d.stock_code FROM dim_active_a_stock d
        WHERE EXISTS (
            SELECT 1 FROM fact_institution_event e WHERE e.stock_code = d.stock_code
        )
        ORDER BY d.stock_code
        """
    ).fetchall()
    stock_codes = [r[0] for r in stock_rows]
    if limit_stocks:
        stock_codes = stock_codes[:limit_stocks]
    logger.info("[SEF Layer 2A] 目标股票 %d 只", len(stock_codes))

    # 幂等：清空再重建
    conn.execute("DELETE FROM fact_stock_character")

    now = datetime.utcnow().isoformat(timespec="seconds")
    written = 0
    skipped = 0

    feature_matrix: list[list[float]] = []
    feature_stocks: list[str] = []
    feature_names = [
        "beta_inst_entry", "beta_holder_decline", "noise_floor", "info_lag_days",
    ]

    for i, code in enumerate(stock_codes):
        events = [
            dict(r) for r in conn.execute(
                "SELECT event_type, notice_date, report_date FROM fact_institution_event "
                "WHERE stock_code=? ORDER BY notice_date",
                (code,),
            ).fetchall()
        ]
        if not events:
            skipped += 1
            continue
        betas = _compute_stock_betas(code, mkt_conn, events, market_ret)
        if not betas:
            skipped += 1
            continue

        feature_matrix.append([betas.get(name, 0.0) or 0.0 for name in feature_names])
        feature_stocks.append(code)

        conn.execute(
            """
            INSERT INTO fact_stock_character(
                stock_code, beta_inst_entry, beta_holder_decline,
                noise_floor, info_lag_days, last_updated
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                code,
                betas.get("beta_inst_entry"),
                betas.get("beta_holder_decline"),
                betas.get("noise_floor"),
                betas.get("info_lag_days"),
                now,
            ),
        )
        written += 1
        if (i + 1) % 200 == 0:
            conn.commit()
            logger.info("[SEF Layer 2A] 进度 %d / %d", i + 1, len(stock_codes))
    conn.commit()

    # PCA embedding
    pca_info = None
    if len(feature_matrix) >= embedding_dim:
        try:
            from sklearn.decomposition import PCA
            from sklearn.preprocessing import StandardScaler

            X = np.array(feature_matrix, dtype=float)
            X = StandardScaler().fit_transform(X)
            n_comp = min(embedding_dim, X.shape[1])
            pca = PCA(n_components=n_comp)
            emb = pca.fit_transform(X)
            pca_info = {
                "n_components": int(n_comp),
                "explained_variance_ratio": pca.explained_variance_ratio_.tolist(),
                "total_var_explained": float(pca.explained_variance_ratio_.sum()),
            }
            # 回写 embedding_json
            for code, vec in zip(feature_stocks, emb):
                conn.execute(
                    "UPDATE fact_stock_character SET embedding_json=? WHERE stock_code=?",
                    (json.dumps([float(x) for x in vec]), code),
                )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[SEF Layer 2A] PCA 失败: %s", e)

    report = {
        "target_stocks": len(stock_codes),
        "written": written,
        "skipped": skipped,
        "feature_matrix_rows": len(feature_matrix),
        "feature_names": feature_names,
        "pca": pca_info,
    }
    logger.info("[SEF Layer 2A] 完成: %s", {k: v for k, v in report.items() if k != "pca"})
    return report
