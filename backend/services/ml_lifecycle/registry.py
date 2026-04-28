"""模型生命周期注册 + 部署闸门."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from services.db import get_conn

logger = logging.getLogger("cm-ml")


@dataclass
class DeployGateResult:
    pass_gate: bool
    reasons: list[str]
    metrics: dict


# ─────────────────────────────────────────────────────────────────────
# 配置 — 部署闸门阈值
# ─────────────────────────────────────────────────────────────────────

GATE_THRESHOLDS = {
    "min_ic_holdout": 0.02,             # holdout RankIC 至少 0.02
    "min_ic_walkforward_avg": 0.015,    # walkforward 平均 IC 至少 0.015
    "max_ic_walkforward_std": 0.03,     # walkforward IC 标准差不超过 0.03 (稳定性)
    "max_drift_score": 0.25,            # 平均 PSI 不超过 0.25
    "min_challenger_uplift": 0.005,     # challenger IC 至少比 champion 高 0.005
}


def get_champion() -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT * FROM mart_model_lifecycle
             WHERE status = 'champion'
             ORDER BY deployed_at DESC LIMIT 1
        """).fetchone()
        return dict(row) if row else None


def list_models(status_filter: Optional[str] = None) -> list[dict]:
    with get_conn() as conn:
        if status_filter:
            rows = conn.execute("""
                SELECT * FROM mart_model_lifecycle
                 WHERE status = ?
                 ORDER BY updated_at DESC
            """, (status_filter,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM mart_model_lifecycle
                 ORDER BY
                   CASE status
                     WHEN 'champion'  THEN 1
                     WHEN 'challenger' THEN 2
                     WHEN 'retired'   THEN 3
                     ELSE 9
                   END,
                   updated_at DESC
            """).fetchall()
        return [dict(r) for r in rows]


def get_model(model_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM mart_model_lifecycle WHERE model_id = ?",
            (model_id,),
        ).fetchone()
        return dict(row) if row else None


def propose_challenger(
    model_id: str,
    *,
    training_config: dict,
    ic_holdout: Optional[float] = None,
    ic_walkforward_avg: Optional[float] = None,
    ic_walkforward_std: Optional[float] = None,
) -> None:
    """注册一个新 challenger. 不影响 champion."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO mart_model_lifecycle (
                model_id, status, ic_holdout, ic_walkforward_avg, ic_walkforward_std,
                training_config, created_at, updated_at
            ) VALUES (?, 'challenger', ?, ?, ?, ?, now(), now())
            ON CONFLICT (model_id) DO UPDATE SET
                status = 'challenger',
                ic_holdout = EXCLUDED.ic_holdout,
                ic_walkforward_avg = EXCLUDED.ic_walkforward_avg,
                ic_walkforward_std = EXCLUDED.ic_walkforward_std,
                training_config = EXCLUDED.training_config,
                updated_at = now()
        """, (
            model_id, ic_holdout, ic_walkforward_avg, ic_walkforward_std,
            json.dumps(training_config, ensure_ascii=False),
        ))
        conn.commit()
    logger.info(f"[ml_lifecycle] challenger registered: {model_id}")


def promote(model_id: str, *, notes: str = "manual promotion") -> None:
    """把 challenger 提升为 champion. 旧 champion 自动 retired."""
    with get_conn() as conn:
        # 1) 旧 champion → retired
        old = conn.execute(
            "SELECT model_id FROM mart_model_lifecycle WHERE status = 'champion'"
        ).fetchone()
        old_id = old["model_id"] if old else None

        if old_id:
            conn.execute("""
                UPDATE mart_model_lifecycle
                   SET status = 'retired', retired_at = now(), updated_at = now()
                 WHERE model_id = ?
            """, (old_id,))

        # 2) 新模型 → champion
        conn.execute("""
            UPDATE mart_model_lifecycle
               SET status = 'champion', deployed_at = now(), promoted_from = ?,
                   deploy_decision_notes = ?, updated_at = now()
             WHERE model_id = ?
        """, (old_id, notes, model_id))
        conn.commit()
    logger.info(f"[ml_lifecycle] promoted {model_id} (old champion: {old_id})")


def retire(model_id: str) -> None:
    with get_conn() as conn:
        conn.execute("""
            UPDATE mart_model_lifecycle
               SET status = 'retired', retired_at = now(), updated_at = now()
             WHERE model_id = ?
        """, (model_id,))
        conn.commit()


def check_deploy_gate(model_id: str) -> DeployGateResult:
    """部署闸门: IC + drift 双重检查 + 跟 champion 比较 uplift."""
    m = get_model(model_id)
    if m is None:
        return DeployGateResult(False, [f"model {model_id} not found"], {})

    reasons = []
    metrics = {
        "ic_holdout": m.get("ic_holdout"),
        "ic_walkforward_avg": m.get("ic_walkforward_avg"),
        "ic_walkforward_std": m.get("ic_walkforward_std"),
        "drift_score": m.get("drift_score"),
    }

    # IC holdout
    if metrics["ic_holdout"] is None:
        reasons.append("ic_holdout 缺失")
    elif metrics["ic_holdout"] < GATE_THRESHOLDS["min_ic_holdout"]:
        reasons.append(f"ic_holdout={metrics['ic_holdout']:.4f} < min={GATE_THRESHOLDS['min_ic_holdout']:.4f}")

    # walkforward IC
    if metrics["ic_walkforward_avg"] is None:
        reasons.append("ic_walkforward_avg 缺失")
    elif metrics["ic_walkforward_avg"] < GATE_THRESHOLDS["min_ic_walkforward_avg"]:
        reasons.append(f"ic_walkforward_avg={metrics['ic_walkforward_avg']:.4f} < min={GATE_THRESHOLDS['min_ic_walkforward_avg']:.4f}")

    # walkforward 稳定性
    if metrics["ic_walkforward_std"] is not None and metrics["ic_walkforward_std"] > GATE_THRESHOLDS["max_ic_walkforward_std"]:
        reasons.append(f"ic_walkforward_std={metrics['ic_walkforward_std']:.4f} > max={GATE_THRESHOLDS['max_ic_walkforward_std']:.4f} (不稳定)")

    # drift
    if metrics["drift_score"] is not None and metrics["drift_score"] > GATE_THRESHOLDS["max_drift_score"]:
        reasons.append(f"drift_score={metrics['drift_score']:.4f} > max={GATE_THRESHOLDS['max_drift_score']:.4f}")

    # uplift vs champion
    champ = get_champion()
    if champ and champ["model_id"] != model_id:
        c_ic = champ.get("ic_walkforward_avg") or champ.get("ic_holdout")
        m_ic = metrics["ic_walkforward_avg"] or metrics["ic_holdout"]
        if c_ic is not None and m_ic is not None:
            uplift = m_ic - c_ic
            metrics["uplift_vs_champion"] = uplift
            if uplift < GATE_THRESHOLDS["min_challenger_uplift"]:
                reasons.append(
                    f"uplift={uplift:.4f} < min={GATE_THRESHOLDS['min_challenger_uplift']:.4f} "
                    f"(challenger 没显著超过 champion {champ['model_id']})"
                )

    return DeployGateResult(pass_gate=len(reasons) == 0, reasons=reasons, metrics=metrics)
