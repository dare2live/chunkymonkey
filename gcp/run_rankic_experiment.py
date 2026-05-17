#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import optuna
import pandas as pd
from scipy.stats import spearmanr


TRIALS_DDL = """
CREATE TABLE IF NOT EXISTS mart_p1_optuna_trials (
    run_id TEXT NOT NULL,
    trial_number INTEGER NOT NULL,
    state TEXT,
    value DOUBLE,
    rank_ic_mean DOUBLE,
    rank_ic_std DOUBLE,
    n_windows INTEGER,
    params_json TEXT,
    duration_s DOUBLE,
    built_at TEXT,
    user_attrs_json TEXT,
    pruned_at_window INTEGER,
    PRIMARY KEY (run_id, trial_number)
);
"""


META_COLS = {
    "stock_code",
    "signal_date",
    "entry_date",
    "unable_at_entry",
    "month_start",
    "built_at",
    "feature_version",
    "label_version",
    "industry_pit_confidence",
    "industry_pit_l1_name",
    "industry_pit_l2_name",
    "sector_name",
    "holder_count_q_report_date",
    "fwd_cost_after_5d",
    "fwd_cost_after_10d",
    "fwd_cost_after_20d",
    "fwd_cost_after_60d",
}


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fail_if_pit_invalid(cfg: dict[str, Any]) -> None:
    pit = cfg.get("pit", {})
    if not pit.get("strict", True):
        raise ValueError("PIT strict mode is required for GCP jobs")
    if pit.get("reject_if_end_after_max_signal_date", True):
        end_date = pd.Timestamp(cfg["end_date"])
        max_signal_date = pd.Timestamp(pit["max_signal_date"])
        if end_date > max_signal_date:
            raise ValueError(f"end_date {end_date.date()} exceeds PIT max_signal_date {max_signal_date.date()}")


def compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in patterns]


def matches_any(name: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(name) for p in patterns)


def select_feature_columns(df: pd.DataFrame, cfg: dict[str, Any]) -> list[str]:
    forbidden = compile_patterns(cfg["pit"].get("forbidden_feature_patterns", []))
    group_patterns = {
        group: compile_patterns(patterns)
        for group, patterns in cfg.get("feature_groups", {}).items()
    }
    feature_set = cfg.get("feature_set", {})
    include_groups = set(feature_set.get("include_groups") or [])
    drop_groups = set(feature_set.get("drop_groups") or [])
    all_groups = set(group_patterns)

    numeric = [
        c for c in df.columns
        if c not in META_COLS
        and pd.api.types.is_numeric_dtype(df[c])
        and not matches_any(c, forbidden)
    ]

    if include_groups == all_groups:
        selected = list(numeric)
    else:
        include_patterns: list[re.Pattern[str]] = []
        for group in include_groups:
            include_patterns.extend(group_patterns.get(group, []))
        selected = [c for c in numeric if include_patterns and matches_any(c, include_patterns)]

    drop_patterns: list[re.Pattern[str]] = []
    for group in drop_groups:
        drop_patterns.extend(group_patterns.get(group, []))
    if drop_patterns:
        selected = [c for c in selected if not matches_any(c, drop_patterns)]

    if not selected:
        raise ValueError(f"No feature columns selected for feature_set={feature_set.get('name')}")
    return selected


def apply_universe(df: pd.DataFrame, universe: str) -> pd.DataFrame:
    if universe == "KEEP":
        return df
    cap_cols = [c for c in ["market_cap", "total_mv", "circ_mv", "float_market_cap"] if c in df.columns]
    if universe == "top-2000" and cap_cols:
        cap_col = cap_cols[0]
        ranked = df.sort_values(["signal_date", cap_col], ascending=[True, False])
        return ranked.groupby("signal_date", group_keys=False).head(2000).copy()
    return df


def load_panel(cfg: dict[str, Any], workdir: Path) -> pd.DataFrame:
    db_path = workdir / "data" / "smartmoney.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        label = cfg["label"]
        table = cfg["feature_panel"]
        query = f"""
            SELECT *
            FROM {table}
            WHERE signal_date >= ?
              AND signal_date <= ?
              AND {label} IS NOT NULL
            ORDER BY signal_date, stock_code
        """
        df = con.execute(query, [cfg["start_date"], cfg["end_date"]]).fetchdf()
    finally:
        con.close()
    if df.empty:
        raise ValueError("Feature panel query returned zero rows")
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    return apply_universe(df, cfg["universe"])


def make_windows(df: pd.DataFrame, cfg: dict[str, Any]) -> list[tuple[np.ndarray, np.ndarray, dict[str, str]]]:
    min_train = int(cfg["walk_forward"]["min_train_months"])
    forward = int(cfg["walk_forward"]["forward_months"])
    horizon = cfg["horizon"]
    embargo_days = int(cfg["pit"]["label_embargo_days_by_horizon"][horizon])

    months = sorted(df["signal_date"].dt.to_period("M").unique())
    windows = []
    dates = df["signal_date"]
    for i in range(min_train, len(months) - forward + 1):
        test_months = months[i:i + forward]
        test_start = test_months[0].to_timestamp()
        test_end = (test_months[-1].to_timestamp() + pd.offsets.MonthEnd(0)).normalize()
        train_cutoff = test_start - pd.Timedelta(days=embargo_days)
        train_mask = dates <= train_cutoff
        test_mask = dates.dt.to_period("M").isin(test_months)
        train_idx = np.flatnonzero(train_mask.to_numpy())
        test_idx = np.flatnonzero(test_mask.to_numpy())
        if len(train_idx) and len(test_idx):
            windows.append((
                train_idx,
                test_idx,
                {
                    "train_start": str(dates.iloc[train_idx].min().date()),
                    "train_end": str(dates.iloc[train_idx].max().date()),
                    "test_start": str(test_start.date()),
                    "test_end": str(test_end.date()),
                    "embargo_days": str(embargo_days),
                },
            ))
    if not windows:
        raise ValueError("No walk-forward windows after PIT embargo")
    return windows


def relevance_by_date(df_part: pd.DataFrame, label: str, max_gain: int = 20) -> np.ndarray:
    rel = np.zeros(len(df_part), dtype=np.int32)
    for _, idx in df_part.groupby("signal_date", sort=False).groups.items():
        y = df_part.loc[idx, label]
        pct = y.rank(pct=True, method="first").to_numpy()
        rel[df_part.index.get_indexer(idx)] = np.clip(np.floor(pct * max_gain), 0, max_gain).astype(np.int32)
    return rel


def build_model(model_name: str, trial: optuna.Trial, seed: int, full: bool):
    if model_name == "lightgbm":
        from lightgbm import LGBMRegressor

        max_depth = trial.suggest_int("max_depth", 3, 8)
        num_leaves_high = min(127, max(2, 2 ** max_depth - 1))
        return LGBMRegressor(
            objective="regression",
            n_estimators=2000 if full else 300,
            max_depth=max_depth,
            num_leaves=trial.suggest_int("num_leaves", 2, num_leaves_high, log=True),
            learning_rate=trial.suggest_float("learning_rate", 0.01, 0.08, log=True),
            min_child_samples=trial.suggest_int("min_child_samples", 20, 300, log=True),
            feature_fraction=trial.suggest_float("feature_fraction", 0.55, 0.95),
            bagging_fraction=trial.suggest_float("bagging_fraction", 0.60, 1.00),
            bagging_freq=trial.suggest_int("bagging_freq", 1, 5),
            reg_alpha=trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
            reg_lambda=trial.suggest_float("reg_lambda", 1e-8, 50.0, log=True),
            random_state=seed,
            verbose=-1,
            n_jobs=-1,
        )
    if model_name == "lambdamart":
        from lightgbm import LGBMRanker

        return LGBMRanker(
            objective="lambdarank",
            metric="ndcg",
            n_estimators=1200 if full else 300,
            learning_rate=trial.suggest_float("learning_rate", 0.01, 0.08, log=True),
            num_leaves=trial.suggest_int("num_leaves", 15, 127, log=True),
            min_child_samples=trial.suggest_int("min_child_samples", 20, 300, log=True),
            random_state=seed,
            verbose=-1,
            n_jobs=-1,
        )
    if model_name == "xgboost":
        from xgboost import XGBRegressor

        return XGBRegressor(
            objective="reg:squarederror",
            n_estimators=1200 if full else 300,
            max_depth=trial.suggest_int("max_depth", 3, 8),
            learning_rate=trial.suggest_float("learning_rate", 0.01, 0.08, log=True),
            subsample=trial.suggest_float("subsample", 0.60, 1.00),
            colsample_bytree=trial.suggest_float("colsample_bytree", 0.55, 0.95),
            reg_alpha=trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
            reg_lambda=trial.suggest_float("reg_lambda", 1e-8, 50.0, log=True),
            random_state=seed,
            tree_method="hist",
            n_jobs=-1,
        )
    if model_name == "catboost":
        from catboost import CatBoostRegressor

        return CatBoostRegressor(
            loss_function="RMSE",
            iterations=1200 if full else 300,
            depth=trial.suggest_int("depth", 3, 8),
            learning_rate=trial.suggest_float("learning_rate", 0.01, 0.08, log=True),
            l2_leaf_reg=trial.suggest_float("l2_leaf_reg", 1.0, 50.0, log=True),
            random_seed=seed,
            verbose=False,
            thread_count=-1,
        )
    raise ValueError(f"Unsupported model: {model_name}")


def fit_predict(model_name: str, model, df: pd.DataFrame, feature_cols: list[str], label: str, train_idx: np.ndarray, test_idx: np.ndarray) -> np.ndarray:
    x_train = df.iloc[train_idx][feature_cols].fillna(0).to_numpy(np.float32)
    x_test = df.iloc[test_idx][feature_cols].fillna(0).to_numpy(np.float32)
    if model_name == "lambdamart":
        train_df = df.iloc[train_idx].copy()
        rel = relevance_by_date(train_df.reset_index(drop=True), label)
        group = train_df.groupby("signal_date", sort=False).size().to_list()
        model.fit(x_train, rel, group=group)
    else:
        y_train = df.iloc[train_idx][label].to_numpy(np.float32)
        model.fit(x_train, y_train)
    return np.asarray(model.predict(x_test), dtype=np.float64)


def rank_ic(pred: np.ndarray, y: np.ndarray) -> float | None:
    if len(pred) < 3 or np.nanstd(pred) == 0 or np.nanstd(y) == 0:
        return None
    rho, _ = spearmanr(pred, y)
    if rho is None or math.isnan(float(rho)):
        return None
    return float(rho)


def portfolio_metric(pred_df: pd.DataFrame, label: str, top_k: int, sizer: str) -> dict[str, Any]:
    daily = []
    for _, group in pred_df.groupby("signal_date"):
        top = group.sort_values("score", ascending=False).head(top_k)
        if top.empty:
            continue
        if sizer == "score_rank_diff":
            ranks = np.arange(len(top), 0, -1, dtype=np.float64)
            weights = ranks / ranks.sum()
        elif sizer in {"kelly", "wilson_kelly"}:
            score = top["score"].to_numpy(np.float64)
            score = score - np.nanmin(score)
            weights = score / score.sum() if score.sum() > 0 else np.repeat(1.0 / len(top), len(top))
        else:
            weights = np.repeat(1.0 / len(top), len(top))
        daily.append(float(np.dot(weights, top[label].to_numpy(np.float64))))
    if not daily:
        return {"n_dates": 0, "mean_daily_ret": None, "std_daily_ret": None}
    arr = np.asarray(daily, dtype=np.float64)
    return {
        "n_dates": int(len(arr)),
        "mean_daily_ret": float(np.nanmean(arr)),
        "std_daily_ret": float(np.nanstd(arr, ddof=1)) if len(arr) > 1 else 0.0,
    }


def insert_trials(db_path: Path, table: str, rows: list[dict[str, Any]]) -> None:
    con = duckdb.connect(str(db_path))
    try:
        con.execute(TRIALS_DDL)
        for row in rows:
            con.execute(
                f"""INSERT OR REPLACE INTO {table}
                   (run_id, trial_number, state, value, rank_ic_mean, rank_ic_std,
                    n_windows, params_json, duration_s, built_at, user_attrs_json, pruned_at_window)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    row["run_id"],
                    row["trial_number"],
                    row["state"],
                    row["value"],
                    row["rank_ic_mean"],
                    row["rank_ic_std"],
                    row["n_windows"],
                    row["params_json"],
                    row["duration_s"],
                    row["built_at"],
                    row["user_attrs_json"],
                    row.get("pruned_at_window"),
                ],
            )
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one strict-PIT RankIC experiment.")
    parser.add_argument("--job-config", required=True)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    cfg = load_config(Path(args.job_config))
    workdir = Path(args.workdir)
    result_dir = workdir / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    fail_if_pit_invalid(cfg)

    df = load_panel(cfg, workdir)
    feature_cols = select_feature_columns(df, cfg)
    windows = make_windows(df, cfg)
    label = cfg["label"]
    model_name = cfg["model"]
    seed = int(cfg["seed"])
    full = bool(cfg.get("full", False))
    run_id = cfg["run_id"]
    table = cfg["output"]["trials_table"]
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    x_meta = {
        "n_rows": int(len(df)),
        "n_features": int(len(feature_cols)),
        "n_windows": int(len(windows)),
        "feature_columns": feature_cols,
        "windows": [w[2] for w in windows],
    }
    (result_dir / "panel_meta.json").write_text(json.dumps(x_meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    trial_rows: list[dict[str, Any]] = []

    def objective(trial: optuna.Trial) -> float:
        start = time.time()
        ics: list[float] = []
        for train_idx, test_idx, _ in windows:
            model = build_model(model_name, trial, seed, full)
            pred = fit_predict(model_name, model, df, feature_cols, label, train_idx, test_idx)
            y_test = df.iloc[test_idx][label].to_numpy(np.float64)
            ic = rank_ic(pred, y_test)
            if ic is not None:
                ics.append(ic)
        if not ics:
            value = -10.0
            mean_ic = None
            std_ic = None
        else:
            mean_ic = float(np.mean(ics))
            std_ic = float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
            value = mean_ic - 0.5 * std_ic
        trial.set_user_attr("rank_ic_mean", mean_ic)
        trial.set_user_attr("rank_ic_std", std_ic)
        trial.set_user_attr("n_windows", len(ics))
        trial.set_user_attr("duration_s", time.time() - start)
        return float(value)

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=seed),
        study_name=run_id,
    )
    study.optimize(objective, n_trials=int(cfg["n_trials"]), gc_after_trial=True)

    for t in study.trials:
        user_attrs = dict(t.user_attrs)
        trial_rows.append({
            "batch_id": cfg["batch_id"],
            "experiment_id": cfg["experiment_id"],
            "run_id": run_id,
            "trial_number": int(t.number),
            "state": t.state.name,
            "value": None if t.value is None else float(t.value),
            "rank_ic_mean": user_attrs.get("rank_ic_mean"),
            "rank_ic_std": user_attrs.get("rank_ic_std"),
            "n_windows": user_attrs.get("n_windows"),
            "params_json": json.dumps(t.params, sort_keys=True),
            "duration_s": user_attrs.get("duration_s"),
            "built_at": built_at,
            "user_attrs_json": json.dumps(user_attrs, sort_keys=True),
            "pruned_at_window": None,
            "model": model_name,
            "horizon": cfg["horizon"],
            "sizer": cfg["sizer"],
            "universe": cfg["universe"],
            "feature_set": cfg["feature_set"]["name"],
        })

    best = study.best_trial
    pred_rows = []
    for train_idx, test_idx, info in windows:
        model = build_model(model_name, optuna.trial.FixedTrial(best.params), seed, full)
        pred = fit_predict(model_name, model, df, feature_cols, label, train_idx, test_idx)
        part = df.iloc[test_idx][["stock_code", "signal_date", label]].copy()
        part["score"] = pred
        part["window_test_start"] = info["test_start"]
        part["window_test_end"] = info["test_end"]
        pred_rows.append(part)
    pred_df = pd.concat(pred_rows, ignore_index=True) if pred_rows else pd.DataFrame()

    summary = {
        "batch_id": cfg["batch_id"],
        "experiment_id": cfg["experiment_id"],
        "run_id": run_id,
        "model": model_name,
        "horizon": cfg["horizon"],
        "sizer": cfg["sizer"],
        "universe": cfg["universe"],
        "feature_set": cfg["feature_set"],
        "best_trial_number": int(best.number),
        "best_value": float(study.best_value),
        "best_params": best.params,
        "portfolio_metric": portfolio_metric(pred_df, label, int(cfg["top_k"]), cfg["sizer"]),
        "pit": cfg["pit"],
        "built_at": built_at,
    }

    trials_path = result_dir / "trials.jsonl"
    with trials_path.open("w", encoding="utf-8") as f:
        for row in trial_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    pd.DataFrame(trial_rows).to_parquet(result_dir / "trials.parquet", index=False)
    if not pred_df.empty:
        pred_df.to_parquet(result_dir / "predictions.parquet", index=False)
    (result_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    insert_trials(workdir / "data" / "smartmoney.duckdb", table, trial_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
