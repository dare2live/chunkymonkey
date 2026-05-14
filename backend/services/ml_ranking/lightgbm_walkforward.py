"""LightGBM pointwise ranker + walk-forward expanding_monthly.

PLAN_V3 v3.2 P0b:
- LightGBM pointwise 首跑 (regression on fwd_cost_after, score 用做 ranking)
- 验证: walk_forward.expanding_monthly, 每月重训, 下月 OOS
- Go metric: stitched OOS RankIC ≥ 0.03; cost-after ann > +3.78%; mdd ≥ -30.1%
- 阈值依据: RankIC 0.03 = 可交易横截面模型下限 (Lopez de Prado)

输入: list[dict] from mart_p0a_feature_label_panel (含 stock_code, signal_date,
fwd_cost_after_5d/10d/20d, 65 alpha158 + 6 risk + 4 financial + 4 event).
切分: services.optimization.walk_forward.split_expanding_monthly (R1 标准).
输出: OOS predictions + RankIC per window + stitched 总 RankIC.

PIT 保证 (Rule 7):
- model fit 只用 train (signal_date ≤ train_end)
- predict 只在 test (test_start > train_end)
- 不允许 random k-fold (split_expanding_monthly 已强制时序)
- feature panel 本身已 PIT-safe (services/labels/feature_join.py)

Codex review (TODO 后续 P0b commit 前):
- LambdaMART pairwise ablation 同特征 (P0b 第二步)
- IC IR 阈值是否合理 (建议 ≥ 0.5 业界惯例)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np

from services.ml_ranking.rank_ic import RankICResult, compute_rank_ic
from services.optimization.walk_forward import (
    WalkForwardSplit,
    split_expanding_monthly,
)

log = logging.getLogger("ml_ranking.lightgbm_wf")


@dataclass(frozen=True)
class LightGBMWalkForwardConfig:
    """LightGBM 训练超参 + walk-forward 配置.

    用法: 默认值是 P0b 首跑值, 后续 P1 用 Optuna 搜.

    Codex Q4 search space (200-trial Optuna 推荐), 新字段都 Optional (None = LGBM default):
    - max_depth: 3-8, 限制树深度防过拟合
    - reg_alpha (lambda_l1): 1e-8 - 10.0 log
    - reg_lambda (lambda_l2): 1e-8 - 50.0 log
    - min_split_gain (min_gain_to_split): 0.0 - 0.2
    - early_stopping_rounds: 100 (n_estimators=2000 时配合 early stop)
    """
    # LightGBM 核心超参 (业界默认 + 用户偏中庸)
    num_leaves: int = 31
    learning_rate: float = 0.05
    n_estimators: int = 200
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 5
    min_child_samples: int = 20
    random_state: int = 42
    # Codex Q4 新加 (default None = backward compatible)
    max_depth: int | None = None
    reg_alpha: float | None = None
    reg_lambda: float | None = None
    min_split_gain: float | None = None
    early_stopping_rounds: int | None = None
    # 用哪个 horizon 作 target
    label_field: str = "fwd_cost_after_10d"
    # walk-forward 参数 (默认从 optuna_config.yaml 取)
    min_train_months: int | None = None
    forward_months: int | None = None
    # 特征列名 (None = 自动从 row dict 取所有非元数据列)
    feature_columns: list[str] | None = None


@dataclass
class WalkForwardWindow:
    """单 walk-forward 窗口的训练 + 预测结果."""
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    n_train: int
    n_test: int
    rank_ic: float           # 该窗 stitched (within-window mean) RankIC
    rank_ic_ir: float
    test_predictions: list[dict] = field(default_factory=list)  # {stock_code, signal_date, score, label}


@dataclass
class WalkForwardResult:
    """全 walk-forward 输出."""
    config: LightGBMWalkForwardConfig
    windows: list[WalkForwardWindow]
    overall_rank_ic: RankICResult  # 全 OOS predictions stitched
    feature_columns: list[str]
    n_windows: int

    @property
    def passed_gate(self) -> bool:
        """P0b Acceptance: stitched OOS RankIC ≥ 0.03."""
        return (
            self.overall_rank_ic.mean_rank_ic >= 0.03
            and self.overall_rank_ic.n_dates >= 30  # 至少 30 个 OOS signal_dates
        )


# 元数据字段 (不进 feature)
_META_FIELDS = {
    "stock_code", "signal_date", "entry_date",
    "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
    "unable_at_entry",
    "feature_version", "built_at",
    "score",  # output 字段, 不进 feature
}


def _infer_feature_columns(rows: list[dict], excluded: set[str] = _META_FIELDS) -> list[str]:
    """从 row dict 推断 feature 列名 (排除元数据 + label)."""
    if not rows:
        return []
    return sorted([k for k in rows[0].keys() if k not in excluded])


def _row_features_matrix(rows: list[dict], feature_columns: list[str]) -> np.ndarray:
    """从 list[dict] 转 X (n_rows × n_features), 缺失用 np.nan (LightGBM 原生支持)."""
    n = len(rows)
    m = len(feature_columns)
    X = np.full((n, m), np.nan, dtype=float)
    for i, r in enumerate(rows):
        for j, c in enumerate(feature_columns):
            v = r.get(c)
            if v is None:
                continue
            try:
                X[i, j] = float(v)
            except (TypeError, ValueError):
                continue
    return X


def _row_labels(rows: list[dict], label_field: str) -> np.ndarray:
    y = np.full(len(rows), np.nan, dtype=float)
    for i, r in enumerate(rows):
        v = r.get(label_field)
        if v is not None:
            try:
                y[i] = float(v)
            except (TypeError, ValueError):
                pass
    return y


def train_one_window(
    train_rows: list[dict],
    test_rows: list[dict],
    *,
    feature_columns: list[str],
    cfg: LightGBMWalkForwardConfig,
) -> tuple[np.ndarray, np.ndarray]:
    """单窗 fit + predict. 返回 (test_predictions, test_labels)."""
    X_train = _row_features_matrix(train_rows, feature_columns)
    y_train = _row_labels(train_rows, cfg.label_field)
    X_test = _row_features_matrix(test_rows, feature_columns)
    y_test = _row_labels(test_rows, cfg.label_field)

    # Filter rows with NaN label (mask=True 的 unable_at_entry)
    train_mask = np.isfinite(y_train)
    if train_mask.sum() < 100:
        log.warning(f"train_mask only {train_mask.sum()} valid; skip window")
        return np.full(len(test_rows), np.nan), y_test

    lgbm_params = dict(
        num_leaves=cfg.num_leaves,
        learning_rate=cfg.learning_rate,
        n_estimators=cfg.n_estimators,
        feature_fraction=cfg.feature_fraction,
        bagging_fraction=cfg.bagging_fraction,
        bagging_freq=cfg.bagging_freq,
        min_child_samples=cfg.min_child_samples,
        random_state=cfg.random_state,
        verbose=-1,
    )
    if cfg.max_depth is not None:
        lgbm_params["max_depth"] = cfg.max_depth
    if cfg.reg_alpha is not None:
        lgbm_params["reg_alpha"] = cfg.reg_alpha
    if cfg.reg_lambda is not None:
        lgbm_params["reg_lambda"] = cfg.reg_lambda
    if cfg.min_split_gain is not None:
        lgbm_params["min_split_gain"] = cfg.min_split_gain
    model = lgb.LGBMRegressor(**lgbm_params)

    fit_kwargs: dict[str, Any] = {}
    if cfg.early_stopping_rounds is not None and cfg.early_stopping_rounds > 0:
        # Use last 10% of train as eval set for early stopping (Codex Q4: n_est=2000 + early_stop=100)
        n_train_valid = int(train_mask.sum())
        split_idx = int(n_train_valid * 0.9)
        idx_train_valid = np.where(train_mask)[0]
        cut = idx_train_valid[split_idx]
        early_train_mask = train_mask.copy()
        early_train_mask[cut:] = False  # train on first 90%
        eval_mask = train_mask.copy()
        eval_mask[:cut] = False  # eval on last 10%
        if eval_mask.sum() >= 50:
            model.fit(
                X_train[early_train_mask], y_train[early_train_mask],
                eval_set=[(X_train[eval_mask], y_train[eval_mask])],
                callbacks=[lgb.early_stopping(cfg.early_stopping_rounds, verbose=False)],
            )
        else:
            model.fit(X_train[train_mask], y_train[train_mask])
    else:
        model.fit(X_train[train_mask], y_train[train_mask])
    y_pred = model.predict(X_test)
    return y_pred, y_test


def train_lightgbm_walkforward(
    rows: list[dict],
    cfg: LightGBMWalkForwardConfig | None = None,
) -> WalkForwardResult:
    """主入口: 跑 expanding_monthly walk-forward + 拼 OOS RankIC.

    Args:
        rows: from mart_p0a_feature_label_panel (含 stock_code/signal_date/features/labels).
        cfg: 训练超参. 默认 LightGBMWalkForwardConfig().

    Returns:
        WalkForwardResult — 每窗 + overall stitched RankIC.
    """
    cfg = cfg or LightGBMWalkForwardConfig()
    if not rows:
        return WalkForwardResult(
            config=cfg, windows=[], n_windows=0,
            overall_rank_ic=RankICResult(float("nan"), float("nan"), 0, 0, []),
            feature_columns=[],
        )

    feature_columns = cfg.feature_columns or _infer_feature_columns(rows)
    log.info(f"feature_columns ({len(feature_columns)}): {feature_columns[:10]}...")

    splits: list[WalkForwardSplit] = split_expanding_monthly(
        rows,
        min_train_months=cfg.min_train_months,
        forward_months=cfg.forward_months,
    )
    if not splits:
        return WalkForwardResult(
            config=cfg, windows=[], n_windows=0,
            overall_rank_ic=RankICResult(float("nan"), float("nan"), 0, 0, []),
            feature_columns=feature_columns,
        )

    windows: list[WalkForwardWindow] = []
    all_predictions: list[dict] = []  # stitched OOS predictions
    for i, sp in enumerate(splits):
        log.info(f"window {i+1}/{len(splits)}: train {sp.train_start}..{sp.train_end} ({len(sp.train)}) → test {sp.test_start}..{sp.test_end} ({len(sp.test)})")
        y_pred, y_test = train_one_window(
            sp.train, sp.test,
            feature_columns=feature_columns,
            cfg=cfg,
        )
        # 单窗 RankIC + 拼接全局 predictions
        per_pred_rows = []
        for r, p, y in zip(sp.test, y_pred, y_test):
            d = {
                "stock_code": r.get("stock_code"),
                "signal_date": r.get("signal_date"),
                "score": float(p) if np.isfinite(p) else None,
                cfg.label_field: float(y) if np.isfinite(y) else None,
            }
            per_pred_rows.append(d)
        window_ic = compute_rank_ic(per_pred_rows, label_field=cfg.label_field)
        windows.append(WalkForwardWindow(
            train_start=sp.train_start, train_end=sp.train_end,
            test_start=sp.test_start, test_end=sp.test_end,
            n_train=sp.n_train, n_test=sp.n_test,
            rank_ic=window_ic.mean_rank_ic,
            rank_ic_ir=window_ic.ic_ir,
            test_predictions=per_pred_rows,
        ))
        all_predictions.extend(per_pred_rows)

    overall_ic = compute_rank_ic(all_predictions, label_field=cfg.label_field)
    return WalkForwardResult(
        config=cfg, windows=windows, n_windows=len(windows),
        overall_rank_ic=overall_ic,
        feature_columns=feature_columns,
    )
