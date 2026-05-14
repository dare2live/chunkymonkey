"""LambdaMART pairwise ranker + walk-forward expanding_monthly (Codex 7-day plan Day 7).

跟 lightgbm_walkforward.py 同 walk-forward 结构, 区别:
- LGBMRanker (objective='lambdarank') vs LGBMRegressor
- 需要 per-signal_date group 信息 (group_sizes = train_df.groupby('signal_date').size())
- label 转 per-date integer relevance (0..n-1 rank within date) — 因 lambdarank 要离散 relevance

Codex Q2 推荐 LambdaMART 是 **架构诊断对照** — 验证 pointwise vs pairwise 差距, 不指望它独立救.
单 alpha 改善通常 < 0.005 RankIC, 主要 value 是验证 ranking objective 是否更稳定 (IC IR).

PIT 保证 (Rule 7): 同 lightgbm_walkforward — split_expanding_monthly 已强制时序.
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

log = logging.getLogger("ml_ranking.lambdamart_wf")


@dataclass(frozen=True)
class LambdaMARTWalkForwardConfig:
    """LambdaMART 训练超参 + walk-forward 配置."""
    num_leaves: int = 31
    learning_rate: float = 0.05
    n_estimators: int = 200
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 5
    min_child_samples: int = 20
    random_state: int = 42
    # Codex Q4 同 LightGBM extensions
    max_depth: int | None = None
    reg_alpha: float | None = None
    reg_lambda: float | None = None
    min_split_gain: float | None = None
    early_stopping_rounds: int | None = None
    # label_field 仍是 fwd_cost_after_*, 但内部转 per-date rank → integer relevance (0..n-1)
    label_field: str = "fwd_cost_after_10d"
    # lambdarank 特有: per-date 切 group, group_sizes 是 train 内 每 signal_date stocks 数量
    label_gain_max: int = 20  # relevance 上限 (lambdarank 内部 NDCG gain)
    # walk-forward
    min_train_months: int | None = None
    forward_months: int | None = None
    feature_columns: list[str] | None = None


@dataclass
class WalkForwardWindow:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    n_train: int
    n_test: int
    rank_ic: float
    rank_ic_ir: float
    test_predictions: list[dict] = field(default_factory=list)


@dataclass
class WalkForwardResult:
    config: LambdaMARTWalkForwardConfig
    windows: list[WalkForwardWindow]
    overall_rank_ic: RankICResult
    feature_columns: list[str]
    n_windows: int

    @property
    def passed_gate(self) -> bool:
        """LambdaMART Acceptance 同 LightGBM: stitched OOS RankIC ≥ 0.03."""
        return (
            self.overall_rank_ic.mean_rank_ic >= 0.03
            and self.overall_rank_ic.n_dates >= 30
        )


_META_FIELDS = {
    "stock_code", "signal_date", "entry_date",
    "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
    "unable_at_entry",
    "feature_version", "built_at",
    "industry_pit_confidence",  # v3 meta
    "score",
}


def _infer_feature_columns(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    return sorted([k for k in rows[0].keys() if k not in _META_FIELDS])


def _row_features_matrix(rows: list[dict], feature_columns: list[str]) -> np.ndarray:
    n, m = len(rows), len(feature_columns)
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


def _row_labels_raw(rows: list[dict], label_field: str) -> np.ndarray:
    y = np.full(len(rows), np.nan, dtype=float)
    for i, r in enumerate(rows):
        v = r.get(label_field)
        if v is not None:
            try:
                y[i] = float(v)
            except (TypeError, ValueError):
                pass
    return y


def _label_to_per_date_relevance(
    rows: list[dict],
    raw_labels: np.ndarray,
    label_gain_max: int,
) -> tuple[np.ndarray, np.ndarray]:
    """转 continuous label → per-signal_date integer relevance + return group_sizes.

    Returns:
        (relevance, group_sizes)
        relevance: same length as rows, integer 0..label_gain_max-1 per signal_date
        group_sizes: 数组, 每元素 = 该 signal_date 内 valid stocks 数量, 按 signal_date 升序
    """
    n = len(rows)
    relevance = np.zeros(n, dtype=np.int32)
    # group by signal_date 排序
    dates = [r.get("signal_date") for r in rows]
    # Build index per signal_date
    by_date: dict = {}
    for i, d in enumerate(dates):
        by_date.setdefault(str(d), []).append(i)
    # Sort dates 升序 (LightGBM lambdarank requires group order = sample order)
    sorted_dates = sorted(by_date.keys())

    # Re-order: 必须连续 per group
    # 但 rows order 应该已经按 signal_date 升序 (caller 用 split_expanding_monthly 保证).
    # 这里只 compute relevance 不重 排序.
    group_sizes_list = []
    for d in sorted_dates:
        idxs = by_date[d]
        # Get labels for valid (non-NaN) idxs
        valid_idxs = [i for i in idxs if np.isfinite(raw_labels[i])]
        if not valid_idxs:
            group_sizes_list.append(len(idxs))
            continue
        # Rank within date — top forward return → high relevance
        labels_per_date = raw_labels[valid_idxs]
        # ascending rank: smallest=0, largest=n-1
        order = np.argsort(np.argsort(labels_per_date))  # rank 0..n-1
        # Scale to [0, label_gain_max-1]
        if len(valid_idxs) > 1:
            relevance_per_date = (order * (label_gain_max - 1) // (len(valid_idxs) - 1)).astype(np.int32)
        else:
            relevance_per_date = np.array([label_gain_max // 2], dtype=np.int32)
        for k, idx in enumerate(valid_idxs):
            relevance[idx] = relevance_per_date[k]
        group_sizes_list.append(len(idxs))
    return relevance, np.array(group_sizes_list, dtype=np.int32)


def train_one_window(
    train_rows: list[dict],
    test_rows: list[dict],
    *,
    feature_columns: list[str],
    cfg: LambdaMARTWalkForwardConfig,
) -> tuple[np.ndarray, np.ndarray]:
    """单窗 LambdaMART fit + predict. 返回 (test_predictions, test_raw_labels).

    test_predictions 是 raw score (model.predict), 后续 compute_rank_ic 用 raw fwd return.
    """
    # Sort rows by signal_date ASC (lambdarank requires contiguous groups)
    train_rows_sorted = sorted(train_rows, key=lambda r: str(r.get("signal_date")))
    test_rows_sorted = sorted(test_rows, key=lambda r: str(r.get("signal_date")))

    X_train = _row_features_matrix(train_rows_sorted, feature_columns)
    y_train_raw = _row_labels_raw(train_rows_sorted, cfg.label_field)
    X_test = _row_features_matrix(test_rows_sorted, feature_columns)
    y_test_raw = _row_labels_raw(test_rows_sorted, cfg.label_field)

    train_mask = np.isfinite(y_train_raw)
    if train_mask.sum() < 100:
        log.warning(f"lambdamart train_mask only {train_mask.sum()} valid; skip window")
        return np.full(len(test_rows_sorted), np.nan), y_test_raw

    # Codex M1 (a163ca58) fix: NaN label filter 必须在 group_sizes 计算之前,
    # 否则 group counts 跟 LGBMRanker 接收的 sample 数不一致.
    valid_rows = [r for r, m in zip(train_rows_sorted, train_mask) if m]
    valid_labels = y_train_raw[train_mask]
    valid_X = X_train[train_mask]
    train_relevance, train_groups = _label_to_per_date_relevance(
        valid_rows, valid_labels, cfg.label_gain_max,
    )

    lgbm_params = dict(
        objective="lambdarank",
        metric="ndcg",
        num_leaves=cfg.num_leaves,
        learning_rate=cfg.learning_rate,
        n_estimators=cfg.n_estimators,
        feature_fraction=cfg.feature_fraction,
        bagging_fraction=cfg.bagging_fraction,
        bagging_freq=cfg.bagging_freq,
        min_child_samples=cfg.min_child_samples,
        random_state=cfg.random_state,
        label_gain=list(range(cfg.label_gain_max)),
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

    model = lgb.LGBMRanker(**lgbm_params)
    model.fit(valid_X, train_relevance, group=train_groups)  # all already NaN-filtered
    y_pred = model.predict(X_test)
    return y_pred, y_test_raw


def train_lambdamart_walkforward(
    rows: list[dict],
    cfg: LambdaMARTWalkForwardConfig | None = None,
) -> WalkForwardResult:
    """主入口: 跑 expanding_monthly walk-forward + 拼 OOS RankIC (LambdaMART pairwise)."""
    cfg = cfg or LambdaMARTWalkForwardConfig()
    if not rows:
        return WalkForwardResult(
            config=cfg, windows=[], n_windows=0,
            overall_rank_ic=RankICResult(float("nan"), float("nan"), 0, 0, []),
            feature_columns=[],
        )

    feature_columns = cfg.feature_columns or _infer_feature_columns(rows)
    log.info(f"lambdamart feature_columns ({len(feature_columns)}): {feature_columns[:10]}...")

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
    all_predictions: list[dict] = []
    for i, sp in enumerate(splits):
        log.info(f"lambdamart window {i+1}/{len(splits)}: train {sp.train_start}..{sp.train_end} ({len(sp.train)}) → test {sp.test_start}..{sp.test_end} ({len(sp.test)})")
        y_pred, y_test_raw = train_one_window(
            sp.train, sp.test,
            feature_columns=feature_columns,
            cfg=cfg,
        )
        # 使用 raw forward return 算 RankIC (不是 relevance integer)
        # 但 model.predict 输出 score 是 ranking score, 要跟 raw label 计 Spearman
        test_sorted = sorted(sp.test, key=lambda r: str(r.get("signal_date")))
        per_pred_rows = []
        for r, p, y in zip(test_sorted, y_pred, y_test_raw):
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
