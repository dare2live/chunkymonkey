"""Phase 4 性能优化 — PreparedPanel (LightGBM df.to_dict 优化).

按 Codex brief 第四阶段:
- DuckDB fetchdf() → pandas DataFrame → df.to_dict("records") → list[dict] → numpy
  是低效的 (dict records 内存大, Python 逐行取字段慢)
- 改: 一次 fetchdf → cast to float32 ndarray + precomputed window indices, trial 复用

API:
    from services.perf.prepared_panel import PreparedPanel, build_panel

    panel = build_panel(conn, panel_name="mart_p0a_feature_label_panel_v3",
                       label="fwd_cost_after_20d",
                       universe_filter_view=None,
                       feature_columns=None)

    # Optuna trial 内 only:
    X_train = panel.X[panel.window_indices[i]["train_idx"]]
    y_train = panel.y[panel.window_indices[i]["train_idx"]]
    # fit / predict / rank_ic
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

log = logging.getLogger("perf.prepared_panel")


@dataclass
class PreparedPanel:
    """Columnar feature/label panel — 一次构造, trial 内复用.

    所有 X / y 是 float32 ndarray (节省 4x memory vs float64).
    """
    X: np.ndarray                                  # shape (N, F), float32 — features
    y: np.ndarray                                  # shape (N,), float32 — primary label
    y_5d: Optional[np.ndarray] = None              # shape (N,), float32 — alt label
    y_10d: Optional[np.ndarray] = None             # shape (N,), float32
    y_20d: Optional[np.ndarray] = None             # shape (N,), float32
    date_codes: Optional[np.ndarray] = None        # shape (N,), int32 — month_start encoded
    stock_codes: Optional[np.ndarray] = None       # shape (N,), object — for diagnostics
    feature_columns: list[str] = field(default_factory=list)
    window_indices: list[dict] = field(default_factory=list)  # [{train_idx: ndarray, test_idx: ndarray}, ...]

    def __post_init__(self):
        if self.X.dtype != np.float32:
            raise ValueError(f"X must be float32 (memory efficiency), got {self.X.dtype}")
        if self.y.dtype != np.float32:
            raise ValueError(f"y must be float32, got {self.y.dtype}")
        if self.X.shape[0] != self.y.shape[0]:
            raise ValueError(f"X/y row count mismatch: {self.X.shape[0]} vs {self.y.shape[0]}")
        if self.feature_columns and len(self.feature_columns) != self.X.shape[1]:
            raise ValueError(f"feature_columns count mismatch: {len(self.feature_columns)} vs {self.X.shape[1]}")

    def __len__(self) -> int:
        return self.X.shape[0]

    @property
    def n_features(self) -> int:
        return self.X.shape[1]

    @property
    def n_windows(self) -> int:
        return len(self.window_indices)

    def get_window(self, i: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Returns (X_train, y_train, X_test, y_test) for window i."""
        if i >= len(self.window_indices):
            raise IndexError(f"window {i} out of range, total {len(self.window_indices)}")
        w = self.window_indices[i]
        return (
            self.X[w["train_idx"]], self.y[w["train_idx"]],
            self.X[w["test_idx"]], self.y[w["test_idx"]],
        )


def is_count_feature(feature_name: str) -> bool:
    """Return True when zero is a real missing-value default for this feature."""
    name = feature_name.lower()
    return "count" in name or "cnt" in name


def _is_categorical_series(series) -> bool:
    import pandas as pd

    return (
        isinstance(series.dtype, pd.CategoricalDtype)
        or pd.api.types.is_object_dtype(series)
        or pd.api.types.is_string_dtype(series)
    )


def _feature_group(series, feature_name: str) -> str:
    if is_count_feature(feature_name):
        return "count"
    if _is_categorical_series(series):
        return "categorical"
    return "numeric"


def feature_fillna_audit_rows(df, feature_cols: list[str]) -> list[dict]:
    """Build fillna-policy audit rows for logging and tests."""
    rows = []
    total = len(df)
    for col in feature_cols:
        if col not in df.columns:
            continue
        series = df[col]
        group = _feature_group(series, col)
        rows.append({
            "feature": col,
            "feature_group": group,
            "non_null_pct": float(series.notna().mean()) if total else 0.0,
            "distinct_count": int(series.nunique(dropna=True)),
            "zero_meaning_flag": group == "count",
        })
    return rows


def log_feature_fillna_audit(df, feature_cols: list[str]) -> None:
    """Log per-feature fillna policy audit metrics."""
    for row in feature_fillna_audit_rows(df, feature_cols):
        log.info(
            "fillna_policy feature=%s group=%s non_null_pct=%.4f distinct_count=%s zero_meaning_flag=%s",
            row["feature"],
            row["feature_group"],
            row["non_null_pct"],
            row["distinct_count"],
            row["zero_meaning_flag"],
        )


def apply_feature_fillna_policy(df, feature_cols: list[str], *, audit: bool = True):
    """Apply PIT-safe feature missing-value policy.

    - count/cnt features: NaN -> 0 because zero is a real count meaning.
    - numeric features: preserve NaN for LightGBM native missing handling.
    - categorical features: NaN -> "MISSING".
    """
    if audit:
        log_feature_fillna_audit(df, feature_cols)

    out = df.copy()
    for col in feature_cols:
        if col not in out.columns:
            continue
        group = _feature_group(out[col], col)
        if group == "count":
            out[col] = out[col].fillna(0)
        elif group == "categorical":
            out[col] = out[col].astype("object").where(out[col].notna(), "MISSING")
    return out


def _feature_frame_to_float32(df, feature_cols: list[str]):
    """Convert selected features to a float32 frame without zero-filling NaNs."""
    import pandas as pd

    out = df[feature_cols].copy()
    for col in feature_cols:
        if _is_categorical_series(out[col]):
            out[col] = pd.Categorical(out[col]).codes.astype(np.float32)
    return out.astype(np.float32)


def build_panel_from_df(
    df,
    label_col: str = "fwd_cost_after_20d",
    feature_cols: Optional[list[str]] = None,
    meta_cols: Optional[set] = None,
) -> PreparedPanel:
    """pandas DataFrame → PreparedPanel (columnar float32 ndarray).

    Args:
        df: pandas DataFrame with stock_code / signal_date / label / features.
        label_col: primary label column.
        feature_cols: explicit feature list; if None, auto exclude meta_cols.
        meta_cols: meta col set to exclude (default standard meta).

    Returns: PreparedPanel ready for Optuna fast trial loop.
    """
    import pandas as pd

    if meta_cols is None:
        meta_cols = {"stock_code", "signal_date", "month_start", "built_at",
                     "feature_version", "label_version",
                     "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
                     "industry_pit_confidence", "industry_pit_l1_name", "industry_pit_l2_name"}

    if feature_cols is None:
        feature_cols = [
            c for c in df.columns
            if c not in meta_cols and pd.api.types.is_numeric_dtype(df[c])
        ]

    # Drop rows where label is NaN (training filter)
    df_filtered = df[df[label_col].notna()].copy()

    df_features = apply_feature_fillna_policy(df_filtered, feature_cols)
    X = _feature_frame_to_float32(df_features, feature_cols).values
    y = df_filtered[label_col].values.astype(np.float32)

    # Optional alt labels (5d / 10d / 20d) if exist
    y_5d = df_filtered["fwd_cost_after_5d"].values.astype(np.float32) if "fwd_cost_after_5d" in df_filtered.columns else None
    y_10d = df_filtered["fwd_cost_after_10d"].values.astype(np.float32) if "fwd_cost_after_10d" in df_filtered.columns else None
    y_20d = df_filtered["fwd_cost_after_20d"].values.astype(np.float32) if "fwd_cost_after_20d" in df_filtered.columns else None

    # Month codes (for walk-forward indexing)
    month_codes = None
    if "signal_date" in df_filtered.columns:
        ms = pd.to_datetime(df_filtered["signal_date"]).dt.to_period("M").dt.to_timestamp()
        # Encode month_start as int (years × 12 + month) for compact storage
        month_codes = (ms.dt.year * 12 + ms.dt.month).values.astype(np.int32)

    stock_codes = df_filtered["stock_code"].values if "stock_code" in df_filtered.columns else None

    return PreparedPanel(
        X=X,
        y=y,
        y_5d=y_5d,
        y_10d=y_10d,
        y_20d=y_20d,
        date_codes=month_codes,
        stock_codes=stock_codes,
        feature_columns=feature_cols,
        window_indices=[],
    )


def make_lambdarank_groups(
    panel,
    signal_dates,
    *,
    label_col: str = "fwd_cost_after_20d",
    feature_cols: Optional[list[str]] = None,
    meta_cols: Optional[set] = None,
    date_col: str = "signal_date",
    fill_value: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build LightGBM LambdaRank arrays grouped by ``signal_date``.

    Returns:
        (X, y_relevance, groups_array)

    ``y_relevance`` is encoded within each ``signal_date`` group with
    ``pd.qcut(..., 5, labels=[0, 1, 2, 3, 4])`` semantics: the weakest
    forward label maps to 0 and the strongest maps to 4.
    """
    import pandas as pd

    if date_col not in panel.columns:
        raise ValueError(f"{date_col} column required for LambdaRank grouping")
    if label_col not in panel.columns:
        raise ValueError(f"{label_col} column required for LambdaRank relevance")

    if meta_cols is None:
        meta_cols = {
            "stock_code", "signal_date", "entry_date", "unable_at_entry",
            "month_start", "built_at",
            "feature_version", "label_version",
            "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
            "industry_pit_confidence", "industry_pit_l1_name", "industry_pit_l2_name",
            "industry_pit_l1_code", "industry_pit_l2_code",
            "sector_name",
        }
    meta_cols = set(meta_cols)
    meta_cols.add(label_col)

    if feature_cols is None:
        feature_cols = [
            c for c in panel.columns
            if c not in meta_cols and pd.api.types.is_numeric_dtype(panel[c])
        ]

    if signal_dates is None:
        date_mask = pd.Series(True, index=panel.index)
    else:
        wanted_dates = pd.to_datetime(list(signal_dates))
        date_values = pd.to_datetime(panel[date_col])
        date_mask = date_values.isin(wanted_dates)

    sort_cols = [date_col]
    if "stock_code" in panel.columns:
        sort_cols.append("stock_code")
    df = (
        panel.loc[date_mask & panel[label_col].notna()]
        .sort_values(sort_cols)
        .reset_index(drop=True)
        .copy()
    )
    if df.empty:
        return (
            np.empty((0, len(feature_cols)), dtype=np.float32),
            np.empty((0,), dtype=np.int32),
            np.empty((0,), dtype=np.int32),
        )

    relevance = np.zeros(len(df), dtype=np.int32)
    for _, group_idx in df.groupby(date_col, sort=False).groups.items():
        values = df.loc[group_idx, label_col].astype(float)
        positions = np.asarray(group_idx, dtype=np.int64)
        if len(values) == 1:
            relevance[positions] = 4
            continue

        try:
            encoded = pd.qcut(
                values.rank(method="first"),
                5,
                labels=[0, 1, 2, 3, 4],
            ).astype("int32")
        except ValueError:
            pct_rank = values.rank(method="first", pct=True)
            encoded = np.ceil(pct_rank * 5.0).astype("int32") - 1
            encoded = np.clip(encoded, 0, 4)
        relevance[positions] = np.asarray(encoded, dtype=np.int32)

    feature_df = df.copy()
    feature_df[feature_cols] = feature_df[feature_cols].replace([np.inf, -np.inf], np.nan)
    X_df = _feature_frame_to_float32(apply_feature_fillna_policy(feature_df, feature_cols), feature_cols)
    if fill_value is not None:
        X_df = X_df.fillna(fill_value)
    X = X_df.to_numpy(dtype=np.float32, copy=True)
    groups = df.groupby(date_col, sort=False).size().to_numpy(dtype=np.int32)
    if int(groups.sum()) != len(df):
        raise AssertionError(f"LambdaRank group sum mismatch: {groups.sum()} != {len(df)}")

    return X, relevance, groups


def compute_walk_forward_windows(
    panel: PreparedPanel,
    min_train_months: int = 6,
    forward_months: int = 1,
) -> PreparedPanel:
    """计算 expanding monthly walk-forward windows + 填入 panel.window_indices.

    Returns: panel (modified in place).

    Optuna trial 内 only loop window_indices, 不再 cut DataFrame.
    """
    if panel.date_codes is None:
        raise ValueError("panel.date_codes required for walk-forward (build with signal_date column)")

    unique_months = np.unique(panel.date_codes)
    unique_months.sort()
    windows = []
    for i in range(min_train_months, len(unique_months) - forward_months + 1):
        train_months = unique_months[:i]
        test_months = unique_months[i:i + forward_months]

        train_mask = np.isin(panel.date_codes, train_months)
        test_mask = np.isin(panel.date_codes, test_months)
        train_idx = np.where(train_mask)[0].astype(np.int32)
        test_idx = np.where(test_mask)[0].astype(np.int32)

        if len(train_idx) > 0 and len(test_idx) > 0:
            windows.append({
                "train_idx": train_idx,
                "test_idx": test_idx,
                "train_month_start": int(train_months[0]),
                "train_month_end": int(train_months[-1]),
                "test_month_start": int(test_months[0]),
                "test_month_end": int(test_months[-1]),
            })

    panel.window_indices = windows
    return panel
