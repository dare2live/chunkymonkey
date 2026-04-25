#!/usr/bin/env python3
"""Feature-group ablation for the multidim model.

M6.1 (2026-04-25): default 改为复用 baseline best_params。
原始固定 PARAMS 保留为 `--params-source fixed`, 仅作向后兼容。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb
import pandas as pd

from services.db import get_conn
from services.model_feature_schema import (
    BASE_FEATURE_COLS,
    DEFAULT_LABEL_NAME,
    DENSE_V2_FEATURE_COLS,
    REGIME_FEATURE_COLS,
)
from scripts.train_multidim_model import compute_ic, decile_metrics, load_panel, split_time_series


logger = logging.getLogger("feature_ablation")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


# 仅在 ablation_run 表不存在时建; 已存在时尊重 M6 schema 迁移加的列
DDL = """
CREATE TABLE IF NOT EXISTS mart_model_ablation_run (
    run_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    label_name TEXT,
    n_features INTEGER,
    feature_cols_json TEXT,
    holdout_ic REAL,
    holdout_rank_ic REAL,
    holdout_top_decile_avg REAL,
    holdout_bottom_decile_avg REAL,
    holdout_long_short_spread REAL,
    holdout_winrate_top REAL,
    built_at TEXT,
    params_source TEXT,
    baseline_model_id TEXT,
    params_json TEXT,
    rank_ic_vs_base_pp REAL,
    num_round INTEGER,
    best_iteration INTEGER,
    PRIMARY KEY (run_id, group_name)
);
"""


# 旧版固定参数 (向后兼容, --params-source fixed 时使用)
LEGACY_FIXED_PARAMS = {
    "objective": "regression",
    "metric": "rmse",
    "learning_rate": 0.04,
    "num_leaves": 31,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l1": 0.01,
    "lambda_l2": 0.1,
    "max_depth": 6,
    "verbose": -1,
    "seed": 42,
}


def _existing(cols: list[str], df: pd.DataFrame) -> list[str]:
    return [c for c in cols if c in df.columns]


def load_baseline_params(conn, model_id: str | None) -> tuple[str, dict]:
    """从 mart_multidim_model 读 best_params_json。
    返回 (model_id, params_dict)。params 已补齐 LightGBM 必要字段。
    """
    if model_id:
        row = conn.execute(
            "SELECT model_id, best_params_json FROM mart_multidim_model WHERE model_id = ?",
            (model_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT model_id, best_params_json FROM mart_multidim_model "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not row or not row[1]:
        raise RuntimeError(
            f"未找到 model_id={model_id or 'latest'} 的 best_params_json; "
            "可改用 --params-source fixed 或先训练一个 baseline"
        )
    params = dict(LEGACY_FIXED_PARAMS)  # 用 legacy 做基础 (objective/metric/seed/verbose)
    params.update(json.loads(row[1]))
    params.update({"objective": "regression", "metric": "rmse", "verbose": -1, "seed": 42})
    return row[0], params


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--label-name", default=DEFAULT_LABEL_NAME)
    parser.add_argument(
        "--params-source",
        choices=["baseline_best", "fixed"],
        default="baseline_best",
        help="baseline_best: 复用最新或指定 model 的 Optuna best_params (默认); "
             "fixed: 用脚本里 LEGACY_FIXED_PARAMS (旧行为, 不推荐)",
    )
    parser.add_argument(
        "--baseline-model-id",
        default=None,
        help="--params-source baseline_best 时指定 model_id; 默认取 mart_multidim_model 最新",
    )
    parser.add_argument("--num-round", type=int, default=400, help="LightGBM 迭代轮数 (与 baseline 训练对齐)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        # === 解析参数源 ===
        if args.params_source == "baseline_best":
            baseline_model_id, params = load_baseline_params(conn, args.baseline_model_id)
            logger.info(
                "params_source=baseline_best, baseline_model_id=%s",
                baseline_model_id,
            )
            logger.info("LightGBM params: %s", {k: v for k, v in params.items() if k not in ("verbose",)})
        else:
            baseline_model_id = None
            params = dict(LEGACY_FIXED_PARAMS)
            logger.info("params_source=fixed, 使用 LEGACY_FIXED_PARAMS")

        df = load_panel(conn, args.start, args.end, label_name=args.label_name)
        train, valid, holdout = split_time_series(df)
        train_valid = pd.concat([train, valid], ignore_index=True)
        a158 = [c for c in df.columns if c.startswith("a158_")]
        groups = {
            "base": _existing(BASE_FEATURE_COLS, df),
            "base_dense_v2": _existing(BASE_FEATURE_COLS + DENSE_V2_FEATURE_COLS, df),
            "base_alpha158": _existing(BASE_FEATURE_COLS, df) + a158,
            "base_dense_v2_alpha158": _existing(BASE_FEATURE_COLS + DENSE_V2_FEATURE_COLS, df) + a158,
            "base_dense_v2_regime": _existing(
                BASE_FEATURE_COLS + DENSE_V2_FEATURE_COLS + REGIME_FEATURE_COLS, df
            ),
        }
        for name, cols in groups.items():
            logger.info("%s features=%d", name, len(cols))
        if args.dry_run:
            return

        conn.executescript(DDL)
        run_id = f"ablation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        built_at = datetime.utcnow().isoformat()

        # 第一遍跑 base 组, 拿 base RankIC 用作 vs_base 的参考
        results: dict[str, dict] = {}
        params_json = json.dumps(
            {k: v for k, v in params.items() if k not in ("objective", "metric", "verbose")},
            ensure_ascii=False,
            sort_keys=True,
        )
        for name, cols in groups.items():
            if not cols:
                continue
            logger.info("=== 训练 %s (n_feat=%d) ===", name, len(cols))
            # 与 baseline final fit 对齐: train+valid 合并 fit, num_round=400 固定, 不 early_stopping
            # 不能把 holdout 当 valid_sets, 否则有 lookahead bias 且 LightGBM 会在 holdout 上早停
            dt = lgb.Dataset(train_valid[cols].values, label=train_valid["label_value"].values, feature_name=cols)
            model = lgb.train(params, dt, num_boost_round=args.num_round)
            best_iter = args.num_round  # 固定轮数, 无 early_stopping
            pred = model.predict(holdout[cols].values)
            ic, rank_ic = compute_ic(
                holdout["label_value"].values, pred, holdout["date"].values
            )
            dec = decile_metrics(
                holdout["label_value"].values, pred, holdout["date"].values
            )
            results[name] = {
                "n_features": len(cols),
                "feature_cols": cols,
                "ic": ic,
                "rank_ic": rank_ic,
                "top_avg": dec["top_avg"],
                "bot_avg": dec["bot_avg"],
                "spread": dec["spread"],
                "winrate": dec["winrate_top"],
                "best_iteration": int(best_iter) if best_iter else None,
            }
            logger.info(
                "%s IC=%.4f RankIC=%.4f spread=%.4f winrate=%.3f best_iter=%s",
                name, ic, rank_ic, dec["spread"], dec["winrate_top"], results[name]["best_iteration"],
            )

        # 写库 + 计算 vs_base 增量
        base_rank_ic = results.get("base", {}).get("rank_ic")
        for name, r in results.items():
            vs_base_pp = (
                None if base_rank_ic is None or name == "base"
                else (r["rank_ic"] - base_rank_ic) * 100  # 百分点
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO mart_model_ablation_run
                (run_id, group_name, label_name, n_features, feature_cols_json,
                 holdout_ic, holdout_rank_ic, holdout_top_decile_avg,
                 holdout_bottom_decile_avg, holdout_long_short_spread,
                 holdout_winrate_top, built_at,
                 params_source, baseline_model_id, params_json,
                 rank_ic_vs_base_pp, num_round, best_iteration)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id, name, args.label_name, r["n_features"],
                    json.dumps(r["feature_cols"], ensure_ascii=False),
                    r["ic"], r["rank_ic"], r["top_avg"], r["bot_avg"],
                    r["spread"], r["winrate"], built_at,
                    args.params_source, baseline_model_id, params_json,
                    vs_base_pp, args.num_round, r["best_iteration"],
                ),
            )
        conn.commit()

        # 汇总打印
        logger.info("=" * 70)
        logger.info("Ablation 结果汇总 (run_id=%s, params_source=%s)", run_id, args.params_source)
        logger.info(
            f"  {'group':25s} {'n_feat':>6s} {'IC':>8s} {'RankIC':>8s} {'spread':>8s} {'winrate':>8s} {'vs_base':>8s}"
        )
        for name in groups.keys():
            if name not in results:
                continue
            r = results[name]
            vs = (r["rank_ic"] - base_rank_ic) * 100 if base_rank_ic is not None and name != "base" else 0.0
            logger.info(
                f"  {name:25s} {r['n_features']:>6d} {r['ic']:>8.4f} {r['rank_ic']:>8.4f} "
                f"{r['spread']:>8.4f} {r['winrate']:>8.3f} {vs:>+7.2f}pp"
            )

        # === Codex 验收三件套 ===
        logger.info("=" * 70)
        logger.info("Codex Decision 验收 (M6.1):")
        logger.info("  入围线 RankIC ≥ 0.030 + vs_base ≥ +0.30pp + 组合层改善 (M0 重跑后看)")
        candidates = [
            (n, r) for n, r in results.items()
            if n != "base"
            and r["rank_ic"] >= 0.030
            and base_rank_ic is not None
            and (r["rank_ic"] - base_rank_ic) * 100 >= 0.30
        ]
        if candidates:
            logger.info("  候选 (RankIC + vs_base 双过线):")
            for n, r in candidates:
                logger.info(
                    "    %s: RankIC %.4f, vs_base +%.2fpp", n, r["rank_ic"],
                    (r["rank_ic"] - base_rank_ic) * 100,
                )
            logger.info("  → 下一步: 在 M0 portfolio backtest 上重跑组合层验证")
        else:
            logger.info("  无候选过线 (RankIC<0.030 或 vs_base<+0.30pp)")
            logger.info("  → 接受奥卡姆剃刀, 当前 baseline 已是上限, 转去做 risk filter / 标签换 (剥 beta)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
