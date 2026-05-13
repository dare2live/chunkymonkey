"""Phase ε+ §6.5.3 — GEPA 反思日志。

每次重训/大调参强制写: hypothesis - changed_params - reflection - next_hypothesis。
每 5 轮自动跑一次元反思 (聚合最近 5 条 log)。

强制规则:
  - reflection 为空或与上轮重复 → 标记 model 💤 DEAD
  - 第 5 / 10 / 15 / ... 轮自动元反思
"""
from __future__ import annotations

import json
import logging
import time
import uuid


log = logging.getLogger("research.reflection")


def write_reflection(
    conn,
    *,
    run_date: str,
    model_id_before: str | None,
    model_id_after: str | None,
    hypothesis: str,
    changed_params: dict,
    score_before: float | None,
    score_after: float | None,
    sharpe_before: float | None = None,
    sharpe_after: float | None = None,
    drawdown_before: float | None = None,
    drawdown_after: float | None = None,
    edge_flags: list[str] | None = None,
    reflection: str,
    next_hypothesis: str | None = None,
    force_meta: bool = False,
) -> dict:
    """写一条 reflection log。

    Args:
        force_meta: True 时强制 is_meta_reflection=True (run_meta_reflection 用)

    Returns:
        {log_id, cycle_number, is_meta_reflection}
    """
    if not hypothesis or not hypothesis.strip():
        raise ValueError("hypothesis 不能为空")
    if not reflection or not reflection.strip():
        raise ValueError("reflection 不能为空 (强制规则: 空 reflection → DEAD 标记)")

    # 反复 reflection 检测: 与上一条相同 → 警告 (但不抛错, 调用方决定是否触发 DEAD)
    prev = conn.execute(
        "SELECT reflection, cycle_number FROM mart_research_reflection_log ORDER BY built_at DESC LIMIT 1"
    ).fetchone()
    duplicate = bool(prev and prev[0] == reflection)
    cycle = (int(prev[1]) if prev and prev[1] is not None else 0) + 1
    is_meta = force_meta or (cycle % 5 == 0)

    log_id = uuid.uuid4().hex[:16]
    conn.execute(
        """INSERT INTO mart_research_reflection_log
           (log_id, cycle_number, run_date,
            model_id_before, model_id_after,
            hypothesis, changed_params,
            score_before, score_after,
            sharpe_before, sharpe_after,
            drawdown_before, drawdown_after,
            edge_flags_json, reflection, next_hypothesis,
            is_meta_reflection)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            log_id, cycle, run_date, model_id_before, model_id_after,
            hypothesis, json.dumps(changed_params, ensure_ascii=False),
            score_before, score_after, sharpe_before, sharpe_after,
            drawdown_before, drawdown_after,
            json.dumps(edge_flags or [], ensure_ascii=False),
            reflection, next_hypothesis, is_meta,
        ],
    )
    conn.commit()

    return {"log_id": log_id, "cycle_number": cycle, "is_meta_reflection": is_meta,
            "duplicate_reflection": duplicate}


def run_meta_reflection(conn, run_date: str) -> dict | None:
    """每 5 轮聚合最近 5 条 log → 识别盲点。"""
    rows = conn.execute(
        """
        SELECT cycle_number, hypothesis, changed_params, reflection, score_before, score_after
          FROM mart_research_reflection_log
         ORDER BY built_at DESC LIMIT 5
        """
    ).fetchall()
    if len(rows) < 5:
        return None

    # 简单盲点识别: 看 changed_params 关键词
    all_params = []
    for r in rows:
        try:
            p = json.loads(r[2]) if r[2] else {}
            all_params.extend(p.keys())
        except Exception:
            pass

    from collections import Counter
    top_params = Counter(all_params).most_common(3)
    blind_spots = []
    if top_params and top_params[0][1] >= 4:
        blind_spots.append(f"最近 5 轮反复调 {top_params[0][0]} ({top_params[0][1]} 次), 可能陷入局部")

    # 性能轨迹
    score_deltas = []
    for r in rows:
        if r[4] is not None and r[5] is not None:
            score_deltas.append(float(r[5]) - float(r[4]))
    avg_delta = sum(score_deltas) / len(score_deltas) if score_deltas else 0.0
    if avg_delta < 0.001 and avg_delta > -0.001:
        blind_spots.append(f"最近 5 轮平均 score_delta={avg_delta:.4f}, 改进停滞")

    if not blind_spots:
        blind_spots.append("无明显盲点")

    return write_reflection(
        conn,
        run_date=run_date,
        model_id_before="meta",
        model_id_after="meta",
        hypothesis="元反思: 最近 5 轮是否在同一方向调参",
        changed_params={"meta": "aggregate"},
        score_before=None, score_after=None,
        reflection=f"最常改的参数: {top_params}; 盲点: {'; '.join(blind_spots)}",
        next_hypothesis="尝试探索性方向 (换 feature 子集或换 horizon)",
        force_meta=True,
    )
