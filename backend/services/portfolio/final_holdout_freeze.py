"""Codex round 17 Q8.8 FIX: final-holdout window freeze + access guard.

按 PLAN_V3 v3.2 §72: final holdout 只读一次 (P3 acceptance 时), 之前阶段 ablation/threshold tuning
绝对禁止读. Codex Q8.8: "Freeze final window before P3, log access, and ensure no ablation/threshold
tuning reads it."

实施:
- mart_p3_holdout_freeze 表: 记录 final 6 months window 的 (start, end, frozen_at, model_id)
- assert_no_holdout_leak(): training / Optuna / paper_sim 阶段 raise 如果 signal_date ∈ frozen window
- record_holdout_access(): P3 acceptance 阶段记录访问 (audit trail)

用法:
    from services.portfolio.final_holdout_freeze import freeze_window, assert_no_holdout_leak
    freeze_window(conn, model_id="lgbm_governance_v1", start="2025-11-01", end="2026-04-30")
    assert_no_holdout_leak(conn, signal_dates_used_in_training, phase="P0b_optuna")
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

UTC = timezone.utc

log = logging.getLogger("portfolio.final_holdout_freeze")


HOLDOUT_FREEZE_DDL = """
CREATE TABLE IF NOT EXISTS mart_p3_holdout_freeze (
    model_id TEXT NOT NULL,
    final_period_start TEXT NOT NULL,
    final_period_end TEXT NOT NULL,
    frozen_at TEXT NOT NULL,
    freeze_reason TEXT,
    access_log TEXT,
    PRIMARY KEY (model_id)
);
"""


def init_freeze_ddl(conn) -> None:
    conn.execute(HOLDOUT_FREEZE_DDL)


def freeze_window(conn, model_id: str, start: str, end: str, reason: str = "P3 acceptance prep") -> None:
    """Freeze final holdout window before P3.

    Args:
        conn: DuckDB connection
        model_id: governance v1 model_id (e.g. lgbm_20260517_governance_v1_20d)
        start: YYYY-MM-DD final period start (inclusive)
        end: YYYY-MM-DD final period end (inclusive)
        reason: 冻结原因 (e.g. "P3 acceptance prep" / "champion promotion")
    """
    init_freeze_ddl(conn)
    frozen_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR REPLACE INTO mart_p3_holdout_freeze "
        "(model_id, final_period_start, final_period_end, frozen_at, freeze_reason, access_log) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [model_id, start, end, frozen_at, reason, "[]"],
    )
    log.info(f"[holdout_freeze] model={model_id} window={start}~{end} frozen_at={frozen_at}")


def assert_no_holdout_leak(conn, signal_dates: list[str], phase: str, model_id: str | None = None) -> None:
    """Raise if any signal_date falls into frozen holdout window.

    Codex Q8.8: training / Optuna / paper_sim 阶段必须 invoke 防 leak.

    Args:
        conn: DuckDB connection
        signal_dates: list of YYYY-MM-DD strings (training/Optuna/paper_sim 用的 dates)
        phase: 当前阶段名 (e.g. "P0b_optuna" / "paper_sim" / "P2_composite")
        model_id: 可选, 限定 check 哪个 model 的 frozen window (None = check 所有)
    """
    init_freeze_ddl(conn)
    if not signal_dates:
        return
    sql = (
        "SELECT model_id, final_period_start, final_period_end FROM mart_p3_holdout_freeze"
    )
    params: list = []
    if model_id:
        sql += " WHERE model_id = ?"
        params.append(model_id)
    frozen = conn.execute(sql, params).fetchall()
    if not frozen:
        return
    for f_model_id, start, end in frozen:
        leaked = [d for d in signal_dates if start <= d <= end]
        if leaked:
            sample = leaked[:3]
            raise RuntimeError(
                f"governance v1 holdout leak: phase={phase} model_id={f_model_id} "
                f"frozen_window={start}~{end} leaked_dates={sample}+{len(leaked)-3}more "
                f"— Codex Q8.8 holdout 冻结期间不可读"
            )


def record_holdout_access(conn, model_id: str, accessor: str, purpose: str) -> None:
    """P3 acceptance 阶段记录 holdout 访问 (audit trail).

    只允许 P3 acceptance 时调用. 其他阶段调用 = governance violation (caller responsibility).
    """
    init_freeze_ddl(conn)
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    entry = f"{timestamp} accessor={accessor} purpose={purpose}"
    # Append to access_log (JSON list as TEXT)
    conn.execute(
        "UPDATE mart_p3_holdout_freeze "
        "SET access_log = COALESCE(access_log, '[]') || ' | ' || ? "
        "WHERE model_id = ?",
        [entry, model_id],
    )
    log.info(f"[holdout_access] {entry}")
