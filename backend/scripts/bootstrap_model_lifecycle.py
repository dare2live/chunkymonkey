"""一次性引导脚本: 从 mart_multidim_model 同步到 mart_model_lifecycle.

策略:
  - 默认保留已有 champion, 避免新 challenger 因 created_at 最新被误提升
  - 无 champion 时, 最新非 tdx_keep_challenger 模型 → champion
  - 其他 → retired
  - holdout_rank_ic → ic_holdout
  - 没有 walkforward 信息时 ic_walkforward_avg/std = NULL (后续 walkforward
    跑完会单独写)

执行后, Workbench Champion/Research 读模型会读取 mart_model_lifecycle。
"""
from __future__ import annotations

import json
import logging
import sys
import argparse
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bootstrap-ml")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset",
        action="store_true",
        help="危险操作: 删除并重建 lifecycle; 仍不会把 tdx_keep_challenger 设为 champion",
    )
    args = parser.parse_args()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT model_id, created_at, holdout_rank_ic, n_features,
                   best_params_json, label_name, feature_schema_version
              FROM mart_multidim_model
             ORDER BY created_at DESC
        """).fetchall()

        if not rows:
            log.warning("mart_multidim_model 是空的, 无可引导内容")
            return 1

        # 找 walkforward 平均 IC
        wf_avg = wf_std = None
        try:
            wf_row = conn.execute("""
                SELECT AVG(rank_ic), STDDEV(rank_ic), COUNT(*) FROM (
                    SELECT rank_ic FROM mart_model_walkforward_prediction LIMIT 1000
                )
            """).fetchone() if False else None
        except Exception:
            wf_row = None

        existing_champion = conn.execute(
            "SELECT model_id FROM mart_model_lifecycle WHERE status='champion' LIMIT 1"
        ).fetchone()
        existing_champion_id = existing_champion["model_id"] if existing_champion else None
        if args.reset:
            conn.execute("DELETE FROM mart_model_lifecycle")
            existing_champion_id = None

        champion_candidate = existing_champion_id
        if not champion_candidate:
            for r in rows:
                if not str(r["model_id"]).startswith("tdx_keep_challenger"):
                    champion_candidate = r["model_id"]
                    break

        for r in rows:
            model_id = r["model_id"]
            current = conn.execute(
                "SELECT status FROM mart_model_lifecycle WHERE model_id = ?",
                (model_id,),
            ).fetchone()
            if current and not args.reset:
                continue
            ic_holdout = r["holdout_rank_ic"]
            cfg = {
                "n_features": r["n_features"],
                "label": r["label_name"],
                "feature_schema_version": r["feature_schema_version"],
            }
            try:
                cfg["best_params"] = json.loads(r["best_params_json"]) if r["best_params_json"] else {}
            except Exception:
                cfg["best_params"] = {}
            status = "champion" if model_id == champion_candidate else (
                "challenger" if str(model_id).startswith("tdx_keep_challenger") else "retired"
            )
            deployed_at = r["created_at"] if status == "champion" else None
            retired_at = r["created_at"] if status == "retired" else None
            notes = "auto-bootstrap from mart_multidim_model" if status == "champion" else None

            conn.execute("""
                INSERT INTO mart_model_lifecycle (
                    model_id, status, deployed_at, retired_at,
                    ic_holdout, ic_walkforward_avg, ic_walkforward_std,
                    drift_score, deploy_decision_notes, training_config,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now(), now())
            """, (
                model_id, status, deployed_at, retired_at,
                ic_holdout, None, None,
                None, notes,
                json.dumps(cfg, ensure_ascii=False),
            ))
        conn.commit()
        log.info("bootstrapped/synced %d models — champion=%s", len(rows), champion_candidate)

        # 总结
        for r in conn.execute(
            "SELECT model_id, status, ic_holdout FROM mart_model_lifecycle ORDER BY status, ic_holdout DESC NULLS LAST"
        ).fetchall():
            log.info(f"  {r['status']:10s} {r['model_id']:50s} ic_holdout={r['ic_holdout']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
