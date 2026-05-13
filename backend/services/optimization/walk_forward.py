"""Phase ψ — Optuna 时序切分器 (单一职责, Anti-leakage, Config-driven).

⚠ 任何 Optuna 调参**必须**走此处切 train / test, 不许直接喂整段 signals 进 study.optimize.
⚠ Rule 5 + Rule 6 + Rule 7 联合应用 (CLAUDE.md):
    - 根因修复 in-sample fit / look-ahead bias
    - 入库的 metric 必须是 OOS 实测, 不是 in-sample 估算
    - 切分参数走 backend/config/optuna_config.yaml, 不 hardcode

业界做法 (Lopez de Prado, Marcos. "Advances in Financial Machine Learning" Ch. 7):
  - 严禁 train / test 在时间上重叠
  - 严禁 random k-fold (会把未来 leak 给过去)
  - 推荐 walk-forward / expanding window / purged k-fold

本模块提供 4 种切分模式:
  - none                整段 in-sample (旧默认, 仅供调试 / 描述, governance 拒入业务表)
  - holdout             前 N% train / 后 (1-N)% OOS (最简单, 推荐起步)
  - expanding           按 n_windows 切 (大样本)
  - expanding_monthly   R1 标准: 每月底切, 前 N 月 train, 当月 OOS, OOS metrics 拼

输入: list[dict] {stock_code, signal_date} ('YYYY-MM-DD' 字符串)
输出: list[WalkForwardSplit] (holdout/none 单元素, expanding/expanding_monthly 多元素)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from typing import Literal

from services.optimization.config import OptunaConfig, get_optuna_config

WalkForwardMode = Literal["none", "holdout", "expanding", "expanding_monthly"]


@dataclass(frozen=True)
class WalkForwardSplit:
    """单次切分结果 (1 train + 1 test 集)."""
    train: list[dict]
    test: list[dict]
    train_start: str          # 'YYYY-MM-DD'
    train_end: str
    test_start: str
    test_end: str
    mode: WalkForwardMode

    @property
    def n_train(self) -> int:
        return len(self.train)

    @property
    def n_test(self) -> int:
        return len(self.test)


def _sort_by_date(signals: list[dict]) -> list[dict]:
    return sorted(signals, key=lambda s: s["signal_date"])


def _ym(date_str: str) -> tuple[int, int]:
    """'YYYY-MM-DD' → (year, month)."""
    return int(date_str[:4]), int(date_str[5:7])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Mode 1: holdout (简单 70/30)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def split_holdout(
    signals: list[dict],
    train_ratio: float | None = None,
    min_train: int | None = None,
    min_test: int | None = None,
    cfg: OptunaConfig | None = None,
) -> WalkForwardSplit | None:
    """简单时序 holdout. 默认从 config 读 train_ratio.

    Args:
        signals:     按 signal_date 升序 (内部会再排一次保险)
        train_ratio: 默认走 config.walk_forward.holdout.train_ratio
        min_train:   默认走 config
        min_test:    默认走 config

    Returns:
        WalkForwardSplit 或 None (样本不足)
    """
    cfg = cfg or get_optuna_config()
    tr = train_ratio if train_ratio is not None else cfg.walk_forward.holdout.train_ratio
    mt = min_train if min_train is not None else cfg.walk_forward.holdout.min_train_signals
    ms = min_test if min_test is not None else cfg.walk_forward.holdout.min_test_signals

    if not (0.0 < tr < 1.0):
        raise ValueError(f"train_ratio 必须 (0, 1), 给的 {tr}")
    sigs = _sort_by_date(signals)
    n = len(sigs)
    if n < mt + ms:
        return None
    split_idx = max(mt, int(n * tr))
    split_idx = min(split_idx, n - ms)
    train = sigs[:split_idx]
    test = sigs[split_idx:]
    if len(train) < mt or len(test) < ms:
        return None
    return WalkForwardSplit(
        train=train, test=test,
        train_start=train[0]["signal_date"], train_end=train[-1]["signal_date"],
        test_start=test[0]["signal_date"], test_end=test[-1]["signal_date"],
        mode="holdout",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Mode 2: expanding (按 n_windows 切)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def split_expanding(
    signals: list[dict],
    n_windows: int | None = None,
    min_train: int | None = None,
    min_test: int | None = None,
    cfg: OptunaConfig | None = None,
) -> list[WalkForwardSplit]:
    """按 n_windows 等切. 默认从 config 读."""
    cfg = cfg or get_optuna_config()
    nw = n_windows if n_windows is not None else cfg.walk_forward.expanding.n_windows
    mt = min_train if min_train is not None else cfg.walk_forward.expanding.min_train_signals
    ms = min_test if min_test is not None else cfg.walk_forward.expanding.min_test_signals

    if nw < 2:
        raise ValueError(f"n_windows 必须 ≥ 2, 给的 {nw}")
    sigs = _sort_by_date(signals)
    n = len(sigs)
    if n < mt + ms * nw:
        return []
    step = n // (nw + 1)
    if step < ms:
        return []
    splits: list[WalkForwardSplit] = []
    for k in range(1, nw + 1):
        train_end_idx = step * k
        test_end_idx = step * (k + 1) if k < nw else n
        if train_end_idx < mt:
            continue
        train = sigs[:train_end_idx]
        test = sigs[train_end_idx:test_end_idx]
        if len(train) < mt or len(test) < ms:
            continue
        splits.append(WalkForwardSplit(
            train=train, test=test,
            train_start=train[0]["signal_date"], train_end=train[-1]["signal_date"],
            test_start=test[0]["signal_date"], test_end=test[-1]["signal_date"],
            mode="expanding",
        ))
    return splits


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Mode 3: expanding_monthly (R1 标准, 用户指定)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def split_expanding_monthly(
    signals: list[dict],
    min_train_months: int | None = None,
    forward_months: int | None = None,
    min_test: int | None = None,
    cfg: OptunaConfig | None = None,
) -> list[WalkForwardSplit]:
    """R1 标准: 每月底切一次 walk-forward.

    例 signals 跨 2023-01 → 2026-05, min_train_months=6, forward_months=1:
      window 1: train 2023-01 → 2023-06   test 2023-07
      window 2: train 2023-01 → 2023-07   test 2023-08
      ...
      window N: train 2023-01 → 2026-04   test 2026-05

    业务代码拿到 list[Split] 后, 用 best params 在每个 test 上跑一遍, 把 OOS metrics
    全部拼起来 (avg OOS sharpe / 拼 OOS NAV / 全部 OOS trades 聚合算 win_rate).
    入库的 sharpe = 拼起来的 OOS sharpe, 真实可信.

    Args:
        signals:           按 signal_date 升序
        min_train_months:  默认走 config.walk_forward.expanding_monthly.min_train_months (6)
        forward_months:    默认走 config (1) — 每窗向前 N 个月 OOS
        min_test:          每窗 OOS 至少几笔 (默认走 governance.min_test_signals)

    Returns:
        list[WalkForwardSplit]. 空表示样本不够 (< min_total_months 个月).
    """
    cfg = cfg or get_optuna_config()
    em = cfg.walk_forward.expanding_monthly
    mt_months = min_train_months if min_train_months is not None else em.min_train_months
    fwd_months = forward_months if forward_months is not None else em.forward_months
    ms = min_test if min_test is not None else cfg.governance.min_test_signals
    min_total = em.min_total_months

    if mt_months < 1 or fwd_months < 1:
        raise ValueError(f"min_train_months {mt_months} 或 forward_months {fwd_months} < 1")

    sigs = _sort_by_date(signals)
    if not sigs:
        return []

    # 收集所有 unique (year, month) 升序
    months = []
    seen = set()
    for s in sigs:
        ym = _ym(s["signal_date"])
        if ym not in seen:
            seen.add(ym)
            months.append(ym)
    if len(months) < min_total:
        return []

    splits: list[WalkForwardSplit] = []
    # 第一个 OOS 月 = month[min_train_months] (前 mt_months 月当 train base)
    # 每窗 train = 累积到该月 - forward_months 之前
    # test = 该月 .. 该月 + forward_months - 1
    for k in range(mt_months, len(months), fwd_months):
        # train 包含 month[0] .. month[k-1]
        train_months = set(months[:k])
        # test = month[k] .. month[k + forward_months - 1]
        test_month_indices = list(range(k, min(k + fwd_months, len(months))))
        if not test_month_indices:
            break
        test_months = set(months[i] for i in test_month_indices)
        train = [s for s in sigs if _ym(s["signal_date"]) in train_months]
        test = [s for s in sigs if _ym(s["signal_date"]) in test_months]
        if len(test) < ms:
            continue   # 这个月太少, 跳
        splits.append(WalkForwardSplit(
            train=train, test=test,
            train_start=train[0]["signal_date"] if train else "",
            train_end=train[-1]["signal_date"] if train else "",
            test_start=test[0]["signal_date"],
            test_end=test[-1]["signal_date"],
            mode="expanding_monthly",
        ))
    return splits


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Mode 4: none (旧 in-sample, governance 拒入业务表)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _split_none(signals: list[dict]) -> list[WalkForwardSplit]:
    sigs = _sort_by_date(signals)
    if not sigs:
        return []
    return [WalkForwardSplit(
        train=sigs, test=[],
        train_start=sigs[0]["signal_date"], train_end=sigs[-1]["signal_date"],
        test_start="", test_end="",
        mode="none",
    )]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 统一 dispatch (业务代码入口)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def split_dispatch(
    signals: list[dict],
    mode: WalkForwardMode | None = None,
    cfg: OptunaConfig | None = None,
    **kwargs,
) -> list[WalkForwardSplit]:
    """统一入口. mode 默认走 config.walk_forward.default_mode.

    Returns:
        list[WalkForwardSplit]. holdout/none 单元素, expanding/expanding_monthly 多元素.

    kwargs 透传给具体 split_* 函数 (例如 holdout 的 train_ratio).

    用法:
        cfg = get_optuna_config()
        splits = split_dispatch(signals)   # 走 cfg.walk_forward.default_mode
        for split in splits:
            assert_no_temporal_leak(split)
            # train Optuna, test OOS ...
    """
    cfg = cfg or get_optuna_config()
    m = mode or cfg.walk_forward.default_mode

    if m == "none":
        return _split_none(signals)
    if m == "holdout":
        s = split_holdout(signals, cfg=cfg, **kwargs)
        return [s] if s is not None else []
    if m == "expanding":
        return split_expanding(signals, cfg=cfg, **kwargs)
    if m == "expanding_monthly":
        # R1 严格模式: 月度滚动 OOS. 但 A 股信号稀疏, 多数 (stock × variant × stage)
        # 组合不到 12 月跨度 — 直接 reject 会丢 99% 任务.
        # 数据驱动 fallback: expanding_monthly 失败 → 退到 holdout. 两层都是 OOS,
        # 业务表能拿到尽量多的覆盖, 但 walk_forward_mode 字段如实标 (governance 可按此筛).
        splits = split_expanding_monthly(signals, cfg=cfg, **kwargs)
        if splits:
            return splits
        s = split_holdout(signals, cfg=cfg)
        return [s] if s is not None else []
    raise ValueError(f"未知 walk_forward_mode: {m}")


def assert_no_temporal_leak(split: WalkForwardSplit) -> None:
    """防御性 assert: train.signal_date 都 < test.signal_date.

    任何业务代码用 walk-forward 前 / 入库前**必须**调一次, 防止默默 leak.
    """
    if split.mode == "none":
        return
    if not split.train or not split.test:
        return
    last_train = max(s["signal_date"] for s in split.train)
    first_test = min(s["signal_date"] for s in split.test)
    if last_train >= first_test:
        raise AssertionError(
            f"Temporal leak detected: last_train_date={last_train} >= "
            f"first_test_date={first_test} (mode={split.mode})"
        )
