#!/usr/bin/env python3
"""Phase 1.6: Cross-model best params extraction from Optuna study DBs.

Per goal.md MASTER_SYNTHESIS Phase 1.6: 提取 v7/v8/v9b best params consensus zone.
No retrain needed — uses existing data/reports/optuna/*.best.json + study.db.

Output: data/reports/best_params_consensus.json
Identifies stable hyperparam ranges that work across model variants,
informs future retrain seed params (when needed).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_best_json(path: Path) -> dict | None:
    try:
        return json.load(path.open())
    except Exception:
        return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-json", default=str(REPO_ROOT / "data" / "reports" / "best_params_consensus.json"))
    args = p.parse_args()

    optuna_dir = REPO_ROOT / "data" / "reports" / "optuna"
    models = {
        "v7": "lgbm_phase5_v7_20260523T010000Z.best.json",
        "v8": "lgbm_phase5_v8_20260523T020000Z.best.json",
        "v9b": "lgbm_phase5_v9b_20260523T083000Z.best.json",
    }

    cross_model = {}
    for name, fname in models.items():
        path = optuna_dir / fname
        data = load_best_json(path)
        if data:
            cross_model[name] = {
                "best_value": data.get("best_value"),
                "best_params": data.get("best_params"),
                "best_trial": data.get("best_trial_number"),
            }
        else:
            cross_model[name] = {"error": f"{path.name} not found"}

    # Param consensus per hyperparam
    param_keys = set()
    for m in cross_model.values():
        if "best_params" in m and m["best_params"]:
            param_keys.update(m["best_params"].keys())

    consensus = {}
    for param in sorted(param_keys):
        values = []
        for m in cross_model.values():
            if "best_params" in m and m["best_params"] and param in m["best_params"]:
                values.append(m["best_params"][param])
        if not values:
            continue
        try:
            consensus[param] = {
                "values": values,
                "min": min(values),
                "max": max(values),
                "mean": statistics.mean(values) if all(isinstance(v, (int, float)) for v in values) else None,
                "median": statistics.median(values) if all(isinstance(v, (int, float)) for v in values) else None,
            }
        except Exception:
            consensus[param] = {"values": values, "note": "non-numeric"}

    out = {
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),  # rule-compliance: ok evidence=runtime date stamp
        "rationale": "Cross-model best params extraction from v7/v8/v9b Optuna DBs",
        "models_compared": list(cross_model.keys()),
        "best_value_comparison": {
            name: m.get("best_value") for name, m in cross_model.items() if "best_value" in m
        },
        "per_model_best_params": cross_model,
        "consensus_zones": consensus,
        "interpretation": {
            "stable_params": "hyperparams with low (max-min)/mean variance across models = stable consensus",
            "divergent_params": "high variance = model-class specific (e.g. v9b stronger penalty drove different reg_alpha)",
            "use_case": "future retrain seed: use median values as starting Optuna search center",
        },
    }

    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))

    print(f"Cross-model best params extracted: {out_path}")
    print(f"Models: {list(cross_model.keys())}")
    print(f"\nbest_value comparison:")
    for name, val in out["best_value_comparison"].items():
        print(f"  {name}: {val}")
    print(f"\nConsensus zones (top 5 params):")
    for i, (k, v) in enumerate(consensus.items()):
        if i >= 5:
            break
        if v.get("median") is not None:
            print(f"  {k}: median={v['median']:.4g} range=[{v['min']:.4g}, {v['max']:.4g}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
