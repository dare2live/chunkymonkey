"""多策略 ensemble — P3.11 (2026-04-28).

核心思想:
- 项目核心是 "机构跟随" 单一 alpha (mart_stock_score)
- 但还有 4 个 alpha 维度未集成:
  * 估值分位 (mart_aif10_valuation_quantile, 越低分位越便宜)
  * 一致预期 (raw_aif10_forecast_consensus, COMPRE_RATING_NUM 越高越受认可)
  * 同行排名 (raw_aif10_peer_valuation, RANK 越低越优)
  * 风险因子 (fact_risk_factors, sharpe 高 + dd 小 + vol 中)
- ensemble = z-score 后加权 + (可选) 行业/市值中性化

实现:
- StrategyConfig dataclass: alpha 名 + weight + 方向 + 数据源
- compute_ensemble_score(snapshot_date, configs): 拉数据 → 标准化 → 加权
- 输出 mart_ensemble_signals (snapshot_date, stock_code, source, raw_score, ensemble_score, weight_config)

不重新训模型, 只做"信号融合 layer"在现有 alpha 之上.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date

from services.utils import latest_closed_or_raise as _latest_closed

logger = logging.getLogger("cm-api.strategy_ensemble")


@dataclass
class AlphaSource:
    """单一 alpha 来源配置."""
    name: str                          # 'institution' / 'valuation' / 'consensus' / etc.
    weight: float                      # 在 ensemble 中的权重 (会归一化)
    direction: int = 1                 # +1 越大越好, -1 越小越好
    sql: str = ""                      # 拉 (stock_code, raw_score) 的 SQL
    description: str = ""


# ===========================================================================
# 默认 alpha 配置
# ===========================================================================

DEFAULT_ALPHAS: list[AlphaSource] = [
    AlphaSource(
        name="institution_follow",
        weight=0.40,
        direction=1,
        sql="""
            SELECT stock_code, action_score AS raw_score
            FROM mart_stock_trend
            WHERE action_score IS NOT NULL
        """,
        description="机构跟随综合评分 (action_score, 项目主 alpha)",
    ),
    AlphaSource(
        name="valuation_pct_low",
        weight=0.20,
        direction=-1,  # 分位越低越便宜
        sql="""
            SELECT
                SUBSTR(secucode, 1, 6) AS stock_code,
                AVG(percentile_fifty) AS raw_score
            FROM raw_aif10_valuation_quantile
            WHERE statistics_cycle = '4' AND percentile_fifty IS NOT NULL
            GROUP BY secucode
        """,
        description="估值 10Y 分位 (越低越便宜)",
    ),
    AlphaSource(
        name="forecast_consensus",
        weight=0.15,
        direction=1,
        sql="""
            SELECT
                SUBSTR(secucode, 1, 6) AS stock_code,
                compre_rating_num AS raw_score
            FROM raw_aif10_forecast_consensus
            WHERE date_type_code = 4 AND compre_rating_num IS NOT NULL
        """,
        description="6 月内一致预期评分 (越高越被看好)",
    ),
    AlphaSource(
        name="momentum_120d",
        weight=0.10,
        direction=1,
        sql="""
            SELECT stock_code, mom_120d AS raw_score
            FROM fact_risk_factors
            WHERE mom_120d IS NOT NULL
        """,
        description="120 日动量",
    ),
    AlphaSource(
        name="risk_adjusted_sharpe",
        weight=0.15,
        direction=1,
        sql="""
            SELECT stock_code, sharpe_60d AS raw_score
            FROM fact_risk_factors
            WHERE sharpe_60d IS NOT NULL
        """,
        description="60 日夏普 (风险调整收益)",
    ),
]


def ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mart_ensemble_signals (
            snapshot_date    TEXT NOT NULL,
            stock_code       TEXT NOT NULL,
            ensemble_score   DOUBLE,
            n_alphas         INTEGER,
            alpha_breakdown  TEXT,
            weight_config    TEXT,
            built_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (snapshot_date, stock_code)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ens_date ON mart_ensemble_signals(snapshot_date DESC, ensemble_score DESC)")
    conn.commit()


def _zscore(values: dict[str, float]) -> dict[str, float]:
    """单 alpha 的 z-score 标准化."""
    if not values:
        return {}
    n = len(values)
    mean = sum(values.values()) / n
    var = sum((v - mean) ** 2 for v in values.values()) / (n - 1) if n > 1 else 0
    sd = var ** 0.5
    if sd == 0:
        return {k: 0.0 for k in values}
    return {k: (v - mean) / sd for k, v in values.items()}


def compute_ensemble(
    conn,
    *,
    alphas: list[AlphaSource] | None = None,
    snapshot_date: str | None = None,
) -> dict:
    """跑一次 ensemble: 拉每个 alpha → z-score → 加权合成 → upsert mart_ensemble_signals."""
    ensure_table(conn)
    if alphas is None:
        alphas = DEFAULT_ALPHAS
    if snapshot_date is None:
        snapshot_date = _latest_closed()  # Phase ψ.5: calendar-gated

    t0 = time.time()
    # 1. 拉每个 alpha 的 (stock_code, raw_score)
    alpha_data: dict[str, dict[str, float]] = {}
    for src in alphas:
        try:
            rows = conn.execute(src.sql).fetchall()
            alpha_data[src.name] = {
                str(r[0]): float(r[1])
                for r in rows
                if r[0] is not None and r[1] is not None
            }
            logger.debug(f"[ensemble] alpha {src.name}: {len(alpha_data[src.name])} 行")
        except Exception as exc:
            logger.warning(f"[ensemble] alpha {src.name} SQL 失败: {exc}")
            alpha_data[src.name] = {}

    # 2. z-score
    alpha_zscored = {name: _zscore(data) for name, data in alpha_data.items()}

    # 3. 加权合成: 取所有股票的并集, 按 weight 加和 (有 alpha 数据的才加, 缺失算 0)
    all_stocks = set()
    for data in alpha_zscored.values():
        all_stocks.update(data.keys())

    weight_total = sum(s.weight for s in alphas) or 1.0
    norm_weights = {s.name: s.direction * s.weight / weight_total for s in alphas}

    ensemble_scores: list[tuple[str, float, dict, int]] = []  # (stock, score, breakdown, n_alphas)
    for sc in all_stocks:
        score = 0.0
        breakdown = {}
        n_alphas = 0
        for src in alphas:
            z = alpha_zscored.get(src.name, {}).get(sc)
            if z is None:
                continue
            contribution = z * norm_weights[src.name]
            score += contribution
            breakdown[src.name] = round(z, 3)
            n_alphas += 1
        if n_alphas == 0:
            continue
        ensemble_scores.append((sc, score, breakdown, n_alphas))

    # 4. upsert
    weight_config_json = json.dumps({s.name: s.weight for s in alphas}, ensure_ascii=False)
    n_written = 0
    for sc, score, breakdown, n_alphas in ensemble_scores:
        conn.execute(
            """INSERT OR REPLACE INTO mart_ensemble_signals
               (snapshot_date, stock_code, ensemble_score, n_alphas, alpha_breakdown, weight_config)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                snapshot_date, sc, round(score, 4), n_alphas,
                json.dumps(breakdown, ensure_ascii=False), weight_config_json,
            ],
        )
        n_written += 1
    conn.commit()

    # P0.1 schema_version
    try:
        from services.schema_versions import record_actual_version
        record_actual_version(conn, "mart_ensemble_signals", "v1")
    except Exception:
        pass

    elapsed = time.time() - t0
    return {
        "status": "ok",
        "snapshot_date": snapshot_date,
        "alphas": [s.name for s in alphas],
        "weights": {s.name: s.weight for s in alphas},
        "n_stocks_per_alpha": {name: len(data) for name, data in alpha_data.items()},
        "n_written": n_written,
        "elapsed_s": round(elapsed, 2),
    }


def topk_ensemble(conn, snapshot_date: str | None = None, k: int = 50) -> list[dict]:
    """读 mart_ensemble_signals topK."""
    if snapshot_date is None:
        row = conn.execute("SELECT MAX(snapshot_date) FROM mart_ensemble_signals").fetchone()
        snapshot_date = row[0] if row and row[0] else None
    if not snapshot_date:
        return []
    rows = conn.execute(f"""
        SELECT stock_code, ensemble_score, n_alphas, alpha_breakdown
        FROM mart_ensemble_signals
        WHERE snapshot_date = ?
        ORDER BY ensemble_score DESC
        LIMIT ?
    """, [snapshot_date, k]).fetchall()
    return [
        {
            "stock_code": r[0],
            "ensemble_score": r[1],
            "n_alphas": r[2],
            "breakdown": json.loads(r[3]) if r[3] else {},
        }
        for r in rows
    ]
