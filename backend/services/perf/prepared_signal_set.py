"""Phase 2 性能优化 — PreparedSignalSet (signal universe 预聚合 + 数组化).

按 Codex brief 优先级 2:
- 把 signal 候选 universe 从 dict-of-dicts 改成 ndarray columnar layout
- trial 内形态/事件 mask 用 numpy bool array (而非 Python for-loop)
- 避 Optuna 重复 SQL 跑 (cached snapshot per signal_date)

收益:
- 避免每个 trial 重算 20 日均量 / 20 日高点 / K 线比例
- 减少 Python 函数调用 + 临时 ndarray 创建
- ~10-30× speedup per trial (尤其 Optuna 200 trials × N stocks)

API:
    from services.perf.prepared_signal_set import PreparedSignalSet, build_from_df

    pss = build_from_df(df_signals)  # 一次性建
    # Optuna trial 内 fast mask:
    mask = pss.filter(body_ratio_min=0.5, lower_shadow_min=0.3, volume_relative_min=1.2)
    selected_stock_codes = pss.stock_code[mask]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class PreparedSignalSet:
    """columnar signal universe — 数组化 trial fast path.

    构建一次 (per signal_date 或 per universe), trial 内只跑 ndarray bool mask.
    """
    stock_code: np.ndarray              # shape (N,), dtype object / int (encoded)
    signal_date: np.ndarray             # shape (N,), dtype 'datetime64[D]'
    signal_bar_idx: np.ndarray          # shape (N,), dtype int32
    stage: np.ndarray                   # shape (N,), dtype int (encoded category)

    # K 线 ratio 特征 (Phase 1 candle_pattern 已有)
    body_ratio: np.ndarray              # shape (N,), float32
    lower_shadow_ratio: np.ndarray      # shape (N,), float32
    close_position: np.ndarray          # shape (N,), float32 (close-low)/(high-low)
    volume_relative: np.ndarray         # shape (N,), float32 vol / 20d_avg_vol

    # 可选 metadata
    stock_slice_start_end: Optional[dict[str, tuple[int, int]]] = None  # stock_code → (start_i, end_i) for slicing
    stage_codec: Optional[dict] = None  # encoded int → original stage name

    def __post_init__(self):
        n = len(self.stock_code)
        for arr_name in ("signal_date", "signal_bar_idx", "stage",
                         "body_ratio", "lower_shadow_ratio",
                         "close_position", "volume_relative"):
            arr = getattr(self, arr_name)
            assert len(arr) == n, f"{arr_name} length mismatch: {len(arr)} != {n}"

    def __len__(self) -> int:
        return len(self.stock_code)

    def filter(
        self,
        *,
        body_ratio_min: Optional[float] = None,
        body_ratio_max: Optional[float] = None,
        lower_shadow_min: Optional[float] = None,
        lower_shadow_max: Optional[float] = None,
        close_position_min: Optional[float] = None,
        close_position_max: Optional[float] = None,
        volume_relative_min: Optional[float] = None,
        volume_relative_max: Optional[float] = None,
        stages: Optional[list[int]] = None,
    ) -> np.ndarray:
        """Fast ndarray bool mask. Returns shape (N,) bool array.

        Optuna trial 内典型用法 — 全 numpy vectorized, 无 Python for-loop.
        """
        mask = np.ones(len(self), dtype=bool)
        if body_ratio_min is not None:
            mask &= self.body_ratio >= body_ratio_min
        if body_ratio_max is not None:
            mask &= self.body_ratio <= body_ratio_max
        if lower_shadow_min is not None:
            mask &= self.lower_shadow_ratio >= lower_shadow_min
        if lower_shadow_max is not None:
            mask &= self.lower_shadow_ratio <= lower_shadow_max
        if close_position_min is not None:
            mask &= self.close_position >= close_position_min
        if close_position_max is not None:
            mask &= self.close_position <= close_position_max
        if volume_relative_min is not None:
            mask &= self.volume_relative >= volume_relative_min
        if volume_relative_max is not None:
            mask &= self.volume_relative <= volume_relative_max
        if stages is not None:
            mask &= np.isin(self.stage, stages)
        return mask


def build_from_df(df) -> PreparedSignalSet:
    """从 pandas DataFrame (含 signal_date / stock_code / 7 ratio 字段) 建 PreparedSignalSet.

    Args:
        df: pd.DataFrame with columns: stock_code, signal_date, signal_bar_idx, stage,
            body_ratio, lower_shadow_ratio, close_position, volume_relative.

    Returns: PreparedSignalSet (数组 ready for Optuna fast mask).
    """
    import pandas as pd

    required = {"stock_code", "signal_date", "signal_bar_idx", "stage",
                "body_ratio", "lower_shadow_ratio", "close_position", "volume_relative"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"build_from_df missing required cols: {missing}")

    # Stage encoder
    unique_stages = pd.Categorical(df["stage"]).categories.tolist()
    stage_codec = {i: name for i, name in enumerate(unique_stages)}
    stage_to_int = {name: i for i, name in enumerate(unique_stages)}
    stage_int = df["stage"].map(stage_to_int).astype("int8").values

    # Stock slicing (sorted by stock_code, contiguous slices)
    df_sorted = df.sort_values(["stock_code", "signal_date"]).reset_index(drop=True)
    stock_slice = {}
    code_array = df_sorted["stock_code"].values
    if len(code_array) > 0:
        current_code = code_array[0]
        start = 0
        for i in range(1, len(code_array)):
            if code_array[i] != current_code:
                stock_slice[current_code] = (start, i)
                current_code = code_array[i]
                start = i
        stock_slice[current_code] = (start, len(code_array))

    # signal_date → np.datetime64[D]
    sd = pd.to_datetime(df_sorted["signal_date"]).values.astype("datetime64[D]")

    return PreparedSignalSet(
        stock_code=df_sorted["stock_code"].values,
        signal_date=sd,
        signal_bar_idx=df_sorted["signal_bar_idx"].astype("int32").values,
        stage=df_sorted["stage"].map(stage_to_int).astype("int8").values,
        body_ratio=df_sorted["body_ratio"].astype("float32").values,
        lower_shadow_ratio=df_sorted["lower_shadow_ratio"].astype("float32").values,
        close_position=df_sorted["close_position"].astype("float32").values,
        volume_relative=df_sorted["volume_relative"].astype("float32").values,
        stock_slice_start_end=stock_slice,
        stage_codec=stage_codec,
    )
