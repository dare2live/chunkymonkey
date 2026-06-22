"""预测 outcome tracker — P2.8 (2026-04-28).

核心问题:
- mart_daily_recommendation 每日落库 topK 预测 (snapshot_date / stock_code / model_id / pred_score)
- 但**预测后实际表现**没跟踪 → 无法监控模型衰减 → 无法知道哪个模型在 live 表现最好

P2.8 解决:
- 每日跑 calc_prediction_outcomes step
- 对所有近 60 天的 snapshot_date 预测, 算 T+5 / T+10 / T+30 后的实际涨幅
- 写到 mart_prediction_outcome
- 累积后可计算 hit_rate / avg_gain / IC, model_id 维度

不重新训模型 (留 P3 多策略 ensemble), 只跟踪 outcome.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta

logger = logging.getLogger("cm-api.prediction_outcome")

# model_id 白名单: 字母/数字/下划线/连字符/点 (项目模型命名约定, e.g.
# "lambdamart_v3.2", "ensemble_2026-05-01"). 拒绝引号/分号/空格/通配符等
# SQL 注入字符. 长度上限 128 防超长 payload.
_SAFE_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


def _is_safe_model_id(model_id: str) -> bool:
    """model_id 白名单校验 — 防 SQL 注入 (defense in depth, 配合参数化)."""
    return isinstance(model_id, str) and bool(_SAFE_MODEL_ID_RE.match(model_id))


def ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mart_prediction_outcome (
            snapshot_date  TEXT NOT NULL,
            stock_code     TEXT NOT NULL,
            model_id       TEXT NOT NULL,
            rank_in_date   INTEGER,
            pred_score     DOUBLE,
            entry_price    DOUBLE,
            ret_5d         DOUBLE,
            ret_10d        DOUBLE,
            ret_30d        DOUBLE,
            hit_5d         BOOLEAN,
            hit_30d        BOOLEAN,
            outcome_known_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (snapshot_date, stock_code, model_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_po_date ON mart_prediction_outcome(snapshot_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_po_model ON mart_prediction_outcome(model_id)")
    conn.commit()


def calc_outcomes(conn, *, lookback_days: int = 90) -> dict:
    """对近 lookback_days 内的每个 snapshot_date 预测, 算实际收益.

    需求:
    - mart_daily_recommendation: 预测来源
    - market.duckdb canonical K-line relation: actual prices

    优化:
    - 只算 outcome_known_at IS NULL 的 (未算过)
    - 每个 (snapshot_date, stock_code, model_id) 唯一 outcome
    """
    ensure_table(conn)
    t0 = time.time()
    today = date.today()   # rule-compliance: ok evidence=对账回溯窗口(选近lookback天预测评估outcome), 非PIT决策锚
    cutoff = (today - timedelta(days=lookback_days)).isoformat()

    # 拉近 90 天的预测
    try:
        preds = conn.execute("""
            SELECT
                r.snapshot_date, r.stock_code, r.model_id,
                r.rank_in_date, r.pred_score
            FROM mart_daily_recommendation r
            LEFT JOIN mart_prediction_outcome o
                ON r.snapshot_date = o.snapshot_date
               AND r.stock_code = o.stock_code
               AND r.model_id = o.model_id
            WHERE r.snapshot_date >= ? AND r.snapshot_date <= ?
              AND o.snapshot_date IS NULL
            ORDER BY r.snapshot_date, r.rank_in_date
        """, [cutoff, today.isoformat()]).fetchall()
    except Exception as exc:
        logger.warning(f"[prediction_outcome] 拉预测失败: {exc}")
        return {"status": "error", "error": str(exc)}

    if not preds:
        return {"status": "no_new_predictions", "n_processed": 0, "n_written": 0}

    # 拿价格 (一次性预加载: 所有相关 stock_code, 范围 cutoff → today + 35 天)
    from services.market_db import get_canonical_kline_qfq_relation, get_market_conn
    mc = get_market_conn()
    kline_relation = get_canonical_kline_qfq_relation()

    # 按股票预加载价格序列
    from collections import defaultdict
    code_dates: dict[str, dict[str, float]] = defaultdict(dict)  # code → date → close

    relevant_codes = list(set(p[1] for p in preds))
    if relevant_codes:
        # batch query
        placeholders = ",".join(["?"] * len(relevant_codes))
        try:
            rows = mc.execute(f"""
                SELECT code, date, close FROM {kline_relation}
                WHERE code IN ({placeholders})
                  AND date >= ?
                  AND freq = 'daily' AND adjust = 'qfq'
                  AND close IS NOT NULL
            """, relevant_codes + [cutoff]).fetchall()
            for r in rows:
                code_dates[r[0]][r[1][:10]] = float(r[2])
        except Exception as exc:
            logger.warning(f"[prediction_outcome] 拉价格失败: {exc}")
        finally:
            mc.close()

    # 对每个预测算 forward return
    n_written = 0
    n_skipped = 0
    today_iso = today.isoformat()
    for snap_date, stock_code, model_id, rank, pred_score in preds:
        snap_iso = str(snap_date)[:10]
        prices = code_dates.get(stock_code, {})
        if not prices:
            n_skipped += 1
            continue

        # 找 entry price (snap_iso 当天 / 之后第一个交易日)
        sorted_dates = sorted(prices.keys())
        entry_price = None
        entry_date = None
        for d in sorted_dates:
            if d >= snap_iso:
                entry_price = prices[d]
                entry_date = d
                break
        if entry_price is None or entry_price <= 0:
            n_skipped += 1
            continue

        def _ret_after(n_days: int) -> float | None:
            target = (datetime.fromisoformat(entry_date) + timedelta(days=n_days * 1.5)).date().isoformat()
            # 找 ≥ target 的第一个交易日 (近似 N 个交易日 = 1.5N 自然日)
            future = [d for d in sorted_dates if d >= target]
            if not future:
                return None
            future_price = prices[future[0]]
            if future_price <= 0:
                return None
            return future_price / entry_price - 1

        ret_5d = _ret_after(5)
        ret_10d = _ret_after(10)
        ret_30d = _ret_after(30)

        # 至少有一个 forward return 才落库 (T+5 没到时只有 ret_3d 也行)
        # 用 ret_after(2) 作 minimum
        if ret_5d is None and _ret_after(2) is None:
            n_skipped += 1
            continue

        conn.execute(
            """INSERT OR REPLACE INTO mart_prediction_outcome
               (snapshot_date, stock_code, model_id, rank_in_date, pred_score,
                entry_price, ret_5d, ret_10d, ret_30d, hit_5d, hit_30d)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                snap_iso, stock_code, model_id, rank, pred_score,
                entry_price, ret_5d, ret_10d, ret_30d,
                ret_5d > 0 if ret_5d is not None else None,
                ret_30d > 0 if ret_30d is not None else None,
            ],
        )
        n_written += 1

    conn.commit()
    elapsed = time.time() - t0
    logger.info(
        f"[prediction_outcome] {n_written} written / {n_skipped} skipped (no_price/no_future) / "
        f"{len(preds)} candidates / {elapsed:.1f}s"
    )

    # P0.1 schema_version
    try:
        from services.schema_versions import record_actual_version
        record_actual_version(conn, "mart_prediction_outcome", "v1")
    except Exception:
        pass

    return {
        "status": "ok",
        "n_candidates": len(preds),
        "n_written": n_written,
        "n_skipped": n_skipped,
        "elapsed_s": round(elapsed, 2),
    }


def model_performance_summary(conn, model_id: str | None = None, lookback_days: int = 90) -> dict:
    """汇总 outcome: hit_rate / avg_gain / IC by model_id.

    安全: model_id 来自公开 HTTP query 参数 (routers/data_sources.py
    prediction_outcomes_summary), 必须参数化 + 白名单校验, 不能 f-string 拼 SQL
    (注入面). cutoff 也参数化 (虽内部派生, 统一走 placeholder, 不留 f-string SQL).
    """
    # 白名单校验: model_id 只允许字母/数字/下划线/连字符/点 (模型命名约定),
    # 拒绝引号/分号/空格等注入字符. 不合法 → 当作无该模型, 返回空汇总.
    if model_id is not None and not _is_safe_model_id(model_id):
        logger.warning("[prediction_outcome] 拒绝非法 model_id (疑似注入): %r", model_id)
        return {"lookback_days": lookback_days, "summaries": []}

    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()  # rule-compliance: ok evidence=对账回溯窗口非PIT锚
    where = "WHERE snapshot_date >= ?"
    params: list = [cutoff]
    if model_id:
        where += " AND model_id = ?"
        params.append(model_id)

    rows = conn.execute(
        f"""
        SELECT
            model_id,
            COUNT(*) AS n,
            AVG(CASE WHEN hit_5d THEN 1.0 ELSE 0.0 END) AS hit_rate_5d,
            AVG(CASE WHEN hit_30d THEN 1.0 ELSE 0.0 END) AS hit_rate_30d,
            AVG(ret_5d) AS avg_ret_5d,
            AVG(ret_30d) AS avg_ret_30d,
            CORR(pred_score, ret_5d) AS ic_5d,
            CORR(pred_score, ret_30d) AS ic_30d
        FROM mart_prediction_outcome
        {where}
        GROUP BY model_id
        ORDER BY model_id
    """,
        params,
    ).fetchall()

    return {
        "lookback_days": lookback_days,
        "summaries": [
            {
                "model_id": r[0],
                "n_predictions": r[1],
                "hit_rate_5d": round(r[2] or 0, 3),
                "hit_rate_30d": round(r[3] or 0, 3),
                "avg_ret_5d": round(r[4] or 0, 4) if r[4] else None,
                "avg_ret_30d": round(r[5] or 0, 4) if r[5] else None,
                "ic_5d": round(r[6] or 0, 4) if r[6] else None,
                "ic_30d": round(r[7] or 0, 4) if r[7] else None,
            }
            for r in rows
        ],
    }
