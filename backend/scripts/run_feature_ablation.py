#!/usr/bin/env python3
"""Feature-group ablation for the multidim model.

M6.1 (2026-04-25): default 改为复用 baseline best_params。
原始固定 PARAMS 保留为 `--params-source fixed`, 仅作向后兼容。
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb
import numpy as np

from services.db import get_conn
from services.model_feature_schema import (
    BASE_FEATURE_COLS,
    DEFAULT_LABEL_NAME,
    DENSE_V2_FEATURE_COLS,
    REGIME_FEATURE_COLS,
)


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


def _records_from_cursor(cursor: Any) -> list[dict[str, Any]]:
    names = [desc[0] for desc in (cursor.description or [])]
    return [
        {name: value for name, value in zip(names, row)}
        for row in cursor.fetchall()
    ]


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return value != value
    except Exception:
        return False


def _to_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _existing(cols: list[str], rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    panel_cols = set(rows[0].keys())
    return [c for c in cols if c in panel_cols]


def _rank_percentiles(values: list[float]) -> list[float]:
    n = len(values)
    if n == 0:
        return []
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * n
    pos = 0
    while pos < n:
        end = pos + 1
        while end < n and indexed[end][1] == indexed[pos][1]:
            end += 1
        avg_rank = ((pos + 1) + end) / 2.0
        percentile = avg_rank / n
        for idx in range(pos, end):
            ranks[indexed[idx][0]] = percentile
        pos = end
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n == 0 or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    denom_x = sum(value * value for value in dx)
    denom_y = sum(value * value for value in dy)
    if denom_x <= 0 or denom_y <= 0:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / ((denom_x * denom_y) ** 0.5)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    weight = pos - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _group_by_date(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get("date")), []).append(row)
    return groups


def load_panel_records(
    conn,
    start_date: str,
    end_date: str,
    *,
    label_name: str = DEFAULT_LABEL_NAME,
) -> list[dict[str, Any]]:
    duck = conn.raw if hasattr(conn, "raw") else conn
    panel_cols = {
        row[0]
        for row in duck.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'fact_feature_panel'"
        ).fetchall()
    }
    if label_name not in panel_cols:
        raise RuntimeError(f"fact_feature_panel 缺少 label 列: {label_name}")
    alpha158_cols: list[str] = []
    alpha158_join = ""
    alpha158_db = Path(__file__).resolve().parent.parent.parent / "data" / "alpha158.duckdb"
    if alpha158_db.exists():
        try:
            duck.execute(f"ATTACH IF NOT EXISTS '{alpha158_db}' AS a158 (READ_ONLY)")
            alpha158_cols = [
                row[0]
                for row in duck.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_catalog='a158'
                      AND table_name='fact_alpha158_panel'
                      AND column_name LIKE 'a158_%'
                    """
                ).fetchall()
            ]
            alpha158_join = (
                "LEFT JOIN a158.fact_alpha158_panel a "
                "ON a.stock_code = p.stock_code AND a.date = CAST(p.date AS DATE)"
            )
            logger.info("Alpha158 join 启用, 增补 %d 列", len(alpha158_cols))
        except Exception as exc:
            logger.warning("Alpha158 attach failed: %s", exc)

    feature_candidates = [
        *BASE_FEATURE_COLS,
        *DENSE_V2_FEATURE_COLS,
    ]
    select_features = []
    seen = set()
    for col in feature_candidates:
        if col in panel_cols and col not in seen:
            select_features.append(col)
            seen.add(col)
    select_cols = [
        "p.stock_code",
        "p.date",
        "p.regime_flag",
        f"p.{_quote_ident(label_name)} AS label_value",
        *[f"CAST(p.{_quote_ident(col)} AS DOUBLE) AS {_quote_ident(col)}" for col in select_features],
        *[f"CAST(a.{_quote_ident(col)} AS DOUBLE) AS {_quote_ident(col)}" for col in alpha158_cols],
    ]
    rows = _records_from_cursor(
        duck.execute(
            f"""
            SELECT {', '.join(select_cols)}
            FROM fact_feature_panel p
            {alpha158_join}
            WHERE p.date >= ? AND p.date <= ? AND p.{_quote_ident(label_name)} IS NOT NULL
            """,
            (start_date, end_date),
        )
    )
    for row in rows:
        regime = row.get("regime_flag")
        row["regime_up"] = 1 if regime == "up" else 0
        row["regime_flat"] = 1 if regime == "flat" else 0
        row["regime_down"] = 1 if regime == "down" else 0
    logger.info(
        "rows=%d codes=%d dates=%d total_cols=%d",
        len(rows),
        len({row.get("stock_code") for row in rows}),
        len({row.get("date") for row in rows}),
        len(rows[0]) if rows else 0,
    )
    return rows


def split_time_series_records(
    rows: list[dict[str, Any]],
    train_ratio: float = 0.7,
    valid_ratio: float = 0.15,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    dates = sorted({row["date"] for row in rows})
    n = len(dates)
    t_end = dates[int(n * train_ratio)]
    v_end = dates[int(n * (train_ratio + valid_ratio))]
    train = [row for row in rows if row["date"] < t_end]
    valid = [row for row in rows if t_end <= row["date"] < v_end]
    holdout = [row for row in rows if row["date"] >= v_end]
    logger.info(
        "split: train %s ~ %s (%d)  valid %s ~ %s (%d)  holdout %s ~ %s (%d)",
        min((row["date"] for row in train), default=None),
        max((row["date"] for row in train), default=None),
        len(train),
        min((row["date"] for row in valid), default=None),
        max((row["date"] for row in valid), default=None),
        len(valid),
        min((row["date"] for row in holdout), default=None),
        max((row["date"] for row in holdout), default=None),
        len(holdout),
    )
    return train, valid, holdout


def _matrix(rows: list[dict[str, Any]], cols: list[str]) -> np.ndarray:
    matrix = np.empty((len(rows), len(cols)), dtype=np.float32)
    for row_idx, row in enumerate(rows):
        for col_idx, col in enumerate(cols):
            matrix[row_idx, col_idx] = _to_float(row.get(col)) or 0.0
    return matrix


def _values(rows: list[dict[str, Any]], col: str) -> list[float]:
    return [_to_float(row.get(col)) or 0.0 for row in rows]


def _dates(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("date")) for row in rows]


def compute_ic(y_true: list[float], y_pred, dates: list[str]) -> tuple[float, float]:
    pearson_values = []
    spearman_values = []
    grouped: dict[str, list[tuple[float, float]]] = {}
    for y, pred, date in zip(y_true, y_pred, dates):
        grouped.setdefault(str(date), []).append((float(y), float(pred)))
    for pairs in grouped.values():
        if len(pairs) < 2:
            continue
        ys = [y for y, _ in pairs]
        preds = [pred for _, pred in pairs]
        if len(set(ys)) < 2 or len(set(preds)) < 2:
            continue
        pearson = _pearson(ys, preds)
        spearman = _pearson(_rank_percentiles(ys), _rank_percentiles(preds))
        if pearson is not None:
            pearson_values.append(float(pearson))
        if spearman is not None:
            spearman_values.append(float(spearman))
    return _mean(pearson_values), _mean(spearman_values)


def decile_metrics(y_true: list[float], y_pred, dates: list[str]) -> dict[str, float]:
    top_avgs = []
    bottom_avgs = []
    top_winrates = []
    grouped: dict[str, list[tuple[float, float]]] = {}
    for y, pred, date in zip(y_true, y_pred, dates):
        grouped.setdefault(str(date), []).append((float(y), float(pred)))
    for pairs in grouped.values():
        if len(pairs) < 10:
            continue
        preds = [pred for _, pred in pairs]
        q10 = _quantile(preds, 0.1)
        q90 = _quantile(preds, 0.9)
        if q10 is None or q90 is None:
            continue
        top = [y for y, pred in pairs if pred >= q90]
        bottom = [y for y, pred in pairs if pred <= q10]
        if top and bottom:
            top_avgs.append(sum(top) / len(top))
            bottom_avgs.append(sum(bottom) / len(bottom))
            top_winrates.append(sum(1 for value in top if value > 0) / len(top))
    top_avg = _mean(top_avgs)
    bot_avg = _mean(bottom_avgs)
    return {
        "top_avg": top_avg,
        "bot_avg": bot_avg,
        "spread": top_avg - bot_avg,
        "winrate_top": _mean(top_winrates),
    }


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

        records = load_panel_records(conn, args.start, args.end, label_name=args.label_name)
        if not records:
            raise RuntimeError("fact_feature_panel 空或无 label; 先跑 build_feature_panel_duck.py")
        train, valid, holdout = split_time_series_records(records)
        train_valid = [*train, *valid]
        panel_cols = set(records[0].keys())
        a158 = [c for c in panel_cols if c.startswith("a158_")]
        groups = {
            "base": _existing(BASE_FEATURE_COLS, records),
            "base_dense_v2": _existing(BASE_FEATURE_COLS + DENSE_V2_FEATURE_COLS, records),
            "base_alpha158": _existing(BASE_FEATURE_COLS, records) + a158,
            "base_dense_v2_alpha158": _existing(BASE_FEATURE_COLS + DENSE_V2_FEATURE_COLS, records) + a158,
            "base_dense_v2_regime": _existing(
                BASE_FEATURE_COLS + DENSE_V2_FEATURE_COLS + REGIME_FEATURE_COLS, records
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
            dt = lgb.Dataset(
                _matrix(train_valid, cols),
                label=_values(train_valid, "label_value"),
                feature_name=cols,
            )
            model = lgb.train(params, dt, num_boost_round=args.num_round)
            best_iter = args.num_round  # 固定轮数, 无 early_stopping
            pred = model.predict(_matrix(holdout, cols))
            ic, rank_ic = compute_ic(
                _values(holdout, "label_value"), pred, _dates(holdout)
            )
            dec = decile_metrics(
                _values(holdout, "label_value"), pred, _dates(holdout)
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
