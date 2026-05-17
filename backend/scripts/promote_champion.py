#!/usr/bin/env python3
"""P4c champion promote CLI — 把 P3 PASS 的 model 注册成新冠军.

PLAN_V3 v3.2 P4c: paper_sim KPI → mart_walkforward_eval (已有), champion → mart_champion_model (本 CLI 写入).

用法 (在 P3 final holdout PASS 后):
    PYTHONPATH=backend python backend/scripts/promote_champion.py \
        --p3-run-id p3_20260514T201500_abc123 \
        --reason "lgbm baseline first champion"

CLI 流程:
1. 读 mart_p3_acceptance_result by run_id → 取 model_id + KPI
2. 读 mart_p2_composite_result 同 model_id 最近 composite_score
3. 构造 ChampionRecord → validate_champion_kpi_completeness
4. 注册 register_champion(promote=True)
5. 报告新 / 旧 champion 对比 (compare_challenger)

注意: 当 P3 not passed → 拒绝注册 (除非 --force).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.portfolio.champion import (
    CHAMPION_DDL,
    ChampionRecord,
    compare_challenger,
    get_current_champion,
    register_champion,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("promote_champion")


def _load_p3_record(conn, run_id: str) -> dict | None:
    cur = conn.execute(
        "SELECT * FROM mart_p3_acceptance_result WHERE run_id = ? LIMIT 1",
        [run_id],
    )
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip(cols, row))


def _load_p2_composite(conn, model_id: str) -> float | None:
    """从 mart_p2_composite_result 取此 model_id 最高 composite score."""
    try:
        r = conn.execute(
            "SELECT MAX(composite_score) FROM mart_p2_composite_result WHERE model_id = ?",
            [model_id],
        ).fetchone()
        return r[0] if r and r[0] is not None else None
    except Exception as e:
        log.warning(f"P2 composite lookup failed: {e}")
        return None


def _load_p0b_rank_ic(conn, model_id: str, feature_version: str | None) -> float | None:
    """从 mart_p0b_walkforward_eval 取此 model_id 平均 rank_ic.
    用 OOS test windows 平均 (filter is_final_holdout=true if 存在, 否则全 windows)."""
    try:
        # 优先 final holdout
        r = conn.execute(
            "SELECT AVG(rank_ic) FROM mart_p0b_walkforward_eval "
            "WHERE model_id = ? AND is_final_holdout = TRUE",
            [model_id],
        ).fetchone()
        if r and r[0] is not None:
            return r[0]
        # fallback: 全 OOS windows
        r = conn.execute(
            "SELECT AVG(rank_ic) FROM mart_p0b_walkforward_eval WHERE model_id = ?",
            [model_id],
        ).fetchone()
        return r[0] if r and r[0] is not None else None
    except Exception as e:
        log.warning(f"P0b rank_ic lookup failed: {e}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="P4c promote champion CLI")
    parser.add_argument("--p3-run-id", required=True)
    parser.add_argument("--champion-id", default=None,
                        help="自定义 champion_id (default 用 P3 run_id + timestamp)")
    parser.add_argument("--reason", default="P3 PASS — auto promote")
    parser.add_argument("--force", action="store_true",
                        help="即使 P3 FAIL 也强制 promote (Rule 9 不推荐)")
    args = parser.parse_args()

    conn = duck_connect(str(DB_PATH))
    try:
        conn.execute(CHAMPION_DDL)

        p3 = _load_p3_record(conn, args.p3_run_id)
        if not p3:
            log.error(f"P3 run not found: {args.p3_run_id}")
            return 1
        log.info(f"P3 run loaded: model_id={p3['model_id']}, passed={p3['passed']}")
        if not p3["passed"] and not args.force:
            log.error("P3 FAIL — 拒绝 promote (用 --force 强制).")
            return 1

        composite = _load_p2_composite(conn, p3["model_id"])
        log.info(f"P2 composite score (best): {composite}")

        # Codex CRITICAL fix: 用 mart_p0b_walkforward_eval real rank_ic, 不接受 ann_ret*0.1 占位
        feature_version = p3.get("feature_version")
        rank_ic_real = _load_p0b_rank_ic(conn, p3["model_id"], feature_version)
        log.info(f"P0b walkforward rank_ic (avg): {rank_ic_real}")

        champion_id = args.champion_id or f"{p3['model_id']}_{p3['run_id']}"
        rec = ChampionRecord(
            champion_id=champion_id,
            model_id=p3["model_id"],
            model_version=p3.get("model_version") or "unknown",
            feature_version=p3.get("feature_version") or "p0a_v1",
            label_version=p3.get("label_version") or "p0a_v1",
            seed=p3.get("seed") or 42,
            rank_ic=rank_ic_real,  # 真实 OOS rank_ic from mart_p0b_walkforward_eval (Codex fix)
            ann_ret=p3["ann_ret"], max_dd=p3["max_dd"],
            monthly_win_rate=p3["monthly_win_rate"],
            excess_vs_hs300=p3["excess_vs_hs300"],
            turnover=0.0,  # TODO 接 mart_paper_sim_kpi
            tx_cost_pct=0.0,
            capacity_concentration=0.20,
            final_period_start=p3.get("final_period_start"),
            final_period_end=p3.get("final_period_end"),
            n_oos_months=p3.get("n_oos_months") or 0,
            composite_score=composite,
            p3_passed=p3["passed"],
            p3_failures=json.loads(p3.get("failures_json") or "[]"),
        )

        # Compare with current champion
        comp = compare_challenger(conn, rec)
        if comp["verdict"] == "no_champion_yet":
            log.info("No current champion; this will be the first.")
        else:
            log.info(f"Challenger vs current champion ({comp['current_champion_id']}):")
            for k in ("rank_ic", "ann_ret", "max_dd", "monthly_win_rate", "excess_vs_hs300"):
                cv = comp.get(f"{k}_champion")
                hv = comp.get(f"{k}_challenger")
                dv = comp.get(f"{k}_delta")
                log.info(f"  {k}: champion={cv}, challenger={hv}, Δ={dv}")

        # Codex CRITICAL fix: 删除 rank_ic = ann_ret * 0.1 占位 (污染 champion register).
        # 现在只接受 mart_p0b_walkforward_eval 的真实 OOS rank_ic.
        # 如果 rank_ic 为 None → 拒 promote (除非 --force, 然后明确写 NULL 不伪填).
        if rec.rank_ic is None:
            if not args.force:
                log.error(
                    "rank_ic not available from mart_p0b_walkforward_eval for "
                    f"model_id={p3['model_id']}. 拒绝 promote (用 --force 跳过但 rank_ic 入库为 NULL)."
                )
                return 1
            log.warning("--force 启用, rank_ic 入库为 NULL (champion register KPI 不完整).")

        ok = register_champion(conn, rec, promote=True, reason=args.reason)
        if not ok:
            log.error("Promote 失败 (KPI 不完整或 register 拒)")
            return 1
        log.info(f"✓ Champion promoted: {champion_id}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
