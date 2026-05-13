"""Phase ε+ — daily-topk × formula_weight × sign-flip 闭环融合。

输入:
  - mart_daily_recommendation (ML 模型 pred_score, snapshot_date)
  - fact_technical_trigger (公式触发, signal_date = snapshot_date 前最近)
  - mart_formula_weight_history (反馈环权重, latest snapshot)
  - mart_signal_ic (用于 sign-flip 判定: 负 IC 公式 strength 取负)

输出:
  - mart_daily_blended_recommendation (重排后 topk + 解释字段)
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict


log = logging.getLogger("blended_recommendation")


BLENDED_DDL = """
CREATE TABLE IF NOT EXISTS mart_daily_blended_recommendation (
    snapshot_date         TEXT NOT NULL,
    stock_code            TEXT NOT NULL,
    model_id              TEXT NOT NULL DEFAULT 'blended_v1',
    base_pred_score       REAL,                  -- 原 ML pred_score
    formula_bonus         REAL,                  -- Σ weight × sign × strength
    blended_score         REAL,                  -- base × (1 + formula_bonus) [sign-flip 已应用]
    rank_in_date          INTEGER,
    base_rank_in_date     INTEGER,               -- 原 ML rank (用于对比)
    formula_breakdown_json TEXT,                 -- list[{formula_id, weight, strength, sign}]
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, stock_code, model_id)
);
CREATE INDEX IF NOT EXISTS idx_mdbr_date ON mart_daily_blended_recommendation(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_mdbr_rank ON mart_daily_blended_recommendation(snapshot_date, rank_in_date);
"""


def ensure_blended_table(conn) -> None:
    conn.executescript(BLENDED_DDL)
    conn.commit()


def build_blended_for_date(conn, snapshot_date: str, top_k: int = 100,
                            model_id: str = "blended_v1") -> int:
    """对单一 snapshot_date 算 blended_score 并重排, 写库。

    sign 决定: 公式 rolling_ic_60d > 0 取 +1; ≤ 0 取 -1 (sign-flip)。
    """
    t0 = time.time()
    ensure_blended_table(conn)

    # 1. base recommendations
    base_rows = conn.execute(
        """
        SELECT stock_code, rank_in_date, pred_score
          FROM mart_daily_recommendation
         WHERE snapshot_date = ?
        """,
        [snapshot_date],
    ).fetchall()
    if not base_rows:
        log.warning(f"  无 base recommendation for {snapshot_date}")
        return 0
    base_by_code = {r[0]: {"rank": int(r[1]) if r[1] is not None else None,
                            "pred_score": float(r[2] or 0.0)} for r in base_rows}

    # 2. formula triggers (用 snapshot_date 前最近一天的信号; 避 lookahead)
    prev_sig = conn.execute(
        "SELECT MAX(date) FROM fact_technical_trigger WHERE date < ?",
        [snapshot_date],
    ).fetchone()
    signal_date = prev_sig[0] if prev_sig and prev_sig[0] else snapshot_date
    sig_rows = conn.execute(
        """
        SELECT stock_code, formula_id, formula_variant, strength
          FROM fact_technical_trigger
         WHERE date = ?
        """,
        [signal_date],
    ).fetchall()
    sigs_by_code: dict[str, list[dict]] = defaultdict(list)
    for sc, fid, fvar, strength in sig_rows:
        sigs_by_code[sc].append({
            "formula_id": fid,
            "formula_variant": fvar or fid,
            "strength": float(strength or 0.0),
        })

    # 3. formula weights (latest)
    latest_w = conn.execute(
        "SELECT MAX(snapshot_date) FROM mart_formula_weight_history"
    ).fetchone()
    weights_by_formula = {}
    sign_by_formula = {}
    if latest_w and latest_w[0]:
        w_rows = conn.execute(
            """
            SELECT formula_id, formula_variant, weight, rolling_ic_60d
              FROM mart_formula_weight_history
             WHERE snapshot_date = ?
            """,
            [latest_w[0]],
        ).fetchall()
        for fid, fvar, w, ic in w_rows:
            key = (fid, fvar or fid)
            weights_by_formula[key] = float(w or 0.0)
            # sign-flip: IC ≤ 0 → -1 (信号反向有效); IC > 0 → +1
            sign_by_formula[key] = -1.0 if (ic is None or ic <= 0) else 1.0
    log.info(f"  base picks {len(base_by_code)} / signals {len(sig_rows)} / formulas {len(weights_by_formula)}")

    # 4. 合成 blended_score
    out_rows = []
    for code, base in base_by_code.items():
        bonus = 0.0
        breakdown = []
        for sig in sigs_by_code.get(code, []):
            key = (sig["formula_id"], sig["formula_variant"])
            w = weights_by_formula.get(key, 0.0)
            sign = sign_by_formula.get(key, 1.0)
            contrib = w * sign * sig["strength"]
            bonus += contrib
            breakdown.append({
                "formula_id": sig["formula_id"],
                "weight": w, "sign": sign,
                "strength": sig["strength"],
                "contrib": contrib,
            })
        blended = base["pred_score"] * (1 + bonus)
        out_rows.append({
            "stock_code": code,
            "base_pred_score": base["pred_score"],
            "formula_bonus": bonus,
            "blended_score": blended,
            "base_rank": base["rank"],
            "breakdown": breakdown,
        })

    # 5. 重排 + 截 top_k
    out_rows.sort(key=lambda r: r["blended_score"], reverse=True)
    final = out_rows[:top_k]
    for i, r in enumerate(final):
        r["new_rank"] = i + 1

    # 6. 写库 (atomic)
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "DELETE FROM mart_daily_blended_recommendation WHERE snapshot_date = ? AND model_id = ?",
            [snapshot_date, model_id],
        )
        conn.executemany(
            """INSERT INTO mart_daily_blended_recommendation
               (snapshot_date, stock_code, model_id,
                base_pred_score, formula_bonus, blended_score,
                rank_in_date, base_rank_in_date, formula_breakdown_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(snapshot_date, r["stock_code"], model_id,
              r["base_pred_score"], r["formula_bonus"], r["blended_score"],
              r["new_rank"], r["base_rank"],
              json.dumps(r["breakdown"], ensure_ascii=False) if r["breakdown"] else None)
             for r in final],
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    log.info(f"完成: {len(final)} 行 (耗时 {time.time()-t0:.2f}s)")
    return len(final)
