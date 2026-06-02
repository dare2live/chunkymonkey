"""Phase ψ.α — 短期反转因子 (A 股最强 alpha 之一).

学术 + 业界证据 (CLAUDE.md Rule 6, 全网调研):
  - 1 月反转因子在中证全指 RankIC = 9.15% (最强单因子之一)
  - 创业板 / 小市值 > 沪深主板, 但项目里靠 liquidity filter + paper_sim 5 只集中持仓自然限制
  - 中国散户"追涨杀跌"导致反转效应远强于动量 (谭小芬等)
  - 反转最强区间: 3 个月内, 之后开始变弱

反例 (项目原有 3 公式踩过的坑):
  - MACD金叉 / 海龟突破 / 动态MA迭代 = 全是**动量类**指标
  - A 股 OOS sharpe 实测全部为负 (-0.02 ~ -0.63, 12/12 全负)
  - 用错方向: A 股短期反转 > 动量, 我们却用动量

公式设计 (基于学术经验 + Rule 6 数据驱动):
  - 触发条件 (1 月反转, 多 variant, 由 backend/config/formula_reversal_short_term.yaml 管理):
    1. 收益率: closes[t] / closes[t-20] - 1 ∈ [-0.30, -0.04] (跌 4-30%)
       - 跌 < 4% 不算"反转候选"
       - 跌 > 30% 是崩盘 / 退市风险 (跳)
    2. 低波动过滤: 60 日 close 日 std / mean ≤ 0.06 (相对波动率 6%)
       - 高波动 = 噪音多 反转不稳定
    3. 量比正常: 0.6 < vol_today / vol_ma20 < 2.0
       - 太低 = 没人接 / 太高 = 恐慌抛售末期
  - strength: 跌幅 × 低波 score, 跌 15% 左右 + 波动低 strength 最大

  - variant:
    1. reversal_1m_mild    — 跌 1-15% (温和反转, RankIC 适中)
    2. reversal_1m_deep    — 跌 4-30% (深度反转, RankIC 高但样本少)
    3. reversal_1w         — 1 周反转 (5 日, 信号高频但换手大)

⚠ 不调单股阈值 (留给 Phase ψ Optuna search_space 后续扩展). 当前公式阈值
  由 config 统一管理, 跑出 OOS sharpe 数据后再决定是否参数化.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from services.formula_engine.base import (
    FormulaMetadata,
    FormulaSignal,
    register_formula,
    sma,
)


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "formula_reversal_short_term.yaml"
DEFAULT_CONFIG: dict[str, dict[str, float | int]] = {
    "reversal_1m_mild": {
        "lookback_days": 20,
        "pct_change_lo": -0.15,
        "pct_change_hi": -0.01,
        "rel_std_max": 0.08,
        "vol_ratio_lo": 0.6,
        "vol_ratio_hi": 2.0,
    },
    "reversal_1m_deep": {
        "lookback_days": 20,
        "pct_change_lo": -0.30,
        "pct_change_hi": -0.04,
        "rel_std_max": 0.10,
        "vol_ratio_lo": 0.6,
        "vol_ratio_hi": 2.0,
    },
    "reversal_1w": {
        "lookback_days": 5,
        "pct_change_lo": -0.10,
        "pct_change_hi": -0.01,
        "rel_std_max": 0.07,
        "vol_ratio_lo": 0.6,
        "vol_ratio_hi": 2.0,
    },
}


def _load_yaml(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


def _load_config(path: Path | None = None) -> dict[str, dict[str, float | int]]:
    raw_path = path or CONFIG_PATH
    try:
        raw = _load_yaml(raw_path)
    except FileNotFoundError:
        return {name: values.copy() for name, values in DEFAULT_CONFIG.items()}
    try:
        loaded: dict[str, dict[str, float | int]] = {}
        for variant_name, defaults in DEFAULT_CONFIG.items():
            variant_raw = raw.get(variant_name, {})
            if not isinstance(variant_raw, dict):
                raise ValueError(f"{raw_path.name}: {variant_name} must be a mapping")
            loaded[variant_name] = {
                "lookback_days": int(variant_raw.get("lookback_days", defaults["lookback_days"])),
                "pct_change_lo": float(variant_raw.get("pct_change_lo", defaults["pct_change_lo"])),
                "pct_change_hi": float(variant_raw.get("pct_change_hi", defaults["pct_change_hi"])),
                "rel_std_max": float(variant_raw.get("rel_std_max", defaults["rel_std_max"])),
                "vol_ratio_lo": float(variant_raw.get("vol_ratio_lo", defaults["vol_ratio_lo"])),
                "vol_ratio_hi": float(variant_raw.get("vol_ratio_hi", defaults["vol_ratio_hi"])),
            }
        return loaded
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{raw_path.name}: reversal thresholds must be numeric mappings") from exc


REVERSAL_CONFIG = _load_config()
MILD_CONFIG = REVERSAL_CONFIG["reversal_1m_mild"]
DEEP_CONFIG = REVERSAL_CONFIG["reversal_1m_deep"]
W1_CONFIG = REVERSAL_CONFIG["reversal_1w"]


# ── 共用 helper ─────────────────────────────────────────────


def _trailing_pct_change(closes: np.ndarray, window: int) -> np.ndarray:
    """对每个 i 返回 (closes[i] / closes[i-window] - 1). 前 window 行 nan."""
    n = len(closes)
    out = np.full(n, np.nan)
    if n <= window:
        return out
    out[window:] = closes[window:] / closes[:-window] - 1.0
    return out


def _trailing_rel_std(closes: np.ndarray, window: int = 60) -> np.ndarray:
    """对每个 i 返回 close 日 std / mean (相对波动率) 在前 N 日 (不含当日).

    前 window 行 nan.
    """
    n = len(closes)
    out = np.full(n, np.nan)
    if n <= window:
        return out
    for i in range(window, n):
        seg = closes[i - window:i]
        m = seg.mean()
        if m > 0:
            out[i] = seg.std() / m
    return out


# ── 公式实现 (3 variant) ─────────────────────────────────────


@dataclass(frozen=True)
class _ReversalBase:
    """共用 compute_signals 实现, 子类只指定 metadata + 阈值."""
    metadata: FormulaMetadata
    lookback_days: int                # 1 月 = 20, 1 周 = 5
    pct_change_lo: float              # 跌幅下界 (负数, 例 -0.30)
    pct_change_hi: float              # 跌幅上界 (例 -0.04)
    rel_std_max: float                # 相对波动率上限 (例 0.06)
    vol_ratio_lo: float = 0.6         # 量比下限
    vol_ratio_hi: float = 2.0         # 量比上限

    def compute_signals(
        self,
        code: str,
        dates: np.ndarray,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        amounts: np.ndarray,
    ) -> list[FormulaSignal]:
        n = len(closes)
        if n < max(self.lookback_days, 60) + 1:
            return []

        # 1. 1 月 / 1 周反转幅度
        pct_change = _trailing_pct_change(closes, self.lookback_days)
        in_reversal_zone = (
            (pct_change >= self.pct_change_lo)
            & (pct_change <= self.pct_change_hi)
            & ~np.isnan(pct_change)
        )

        # 2. 60 日相对波动率过滤 (反转的可靠性)
        rel_std = _trailing_rel_std(closes, window=60)
        low_vol_ok = (rel_std <= self.rel_std_max) & ~np.isnan(rel_std)

        # 3. 量比正常 (避免崩盘末期 + 无人问津)
        vol_ma20 = sma(volumes, 20)
        vol_ratio = np.where((vol_ma20 > 0) & ~np.isnan(vol_ma20),
                             volumes / vol_ma20, np.nan)
        vol_ok = (
            (vol_ratio >= self.vol_ratio_lo)
            & (vol_ratio <= self.vol_ratio_hi)
            & ~np.isnan(vol_ratio)
        )

        triggers = in_reversal_zone & low_vol_ok & vol_ok

        signals: list[FormulaSignal] = []
        for i in np.where(triggers)[0]:
            pc = pct_change[i]
            rs = rel_std[i]
            vr = vol_ratio[i]

            # strength: 跌幅越大 + 波动越低 + 量比越正常 → strength 越高
            # 跌幅 score (跌幅 = -0.15 时 max=1.0, 越接近边界越低)
            mid_pct = (self.pct_change_lo + self.pct_change_hi) / 2
            pct_score = 1.0 - abs(pc - mid_pct) / (
                (self.pct_change_hi - self.pct_change_lo) / 2
            )
            # 波动 score (越低越好, 接近 0 时为 1)
            vol_score = max(0.0, 1.0 - rs / self.rel_std_max)
            # 量比 score (接近 1.0 时最优)
            vr_score = max(0.0, 1.0 - abs(vr - 1.0) / max(1.0, self.vol_ratio_hi - 1.0))

            strength = float(min(1.0, max(0.05,
                                          0.5 * pct_score + 0.3 * vol_score + 0.2 * vr_score)))

            reason_codes = (
                f"reversal_{self.lookback_days}d:{pc:+.2%}",
                f"rel_std_60d:{rs:.3f}",
                f"vol_ratio:{vr:.2f}x",
            )
            signals.append(FormulaSignal(
                stock_code=code,
                date=str(dates[i]),
                formula_id=self.metadata.formula_id,
                formula_variant=self.metadata.formula_id,
                strength=strength,
                state=None,
                reason_codes=reason_codes,
            ))
        return signals


@dataclass(frozen=True)
class Reversal1mMild(_ReversalBase):
    """1 月温和反转: 跌 3-15%, 低波动. 样本多, RankIC 适中."""
    metadata: FormulaMetadata = FormulaMetadata(
        formula_id="reversal_1m_mild",
        name="1 月温和反转",
        tag="RM",
        description="20 日跌 3-15% + 60 日低波 + 量比正常 → 短期反转候选",
        default_horizon_days=10,
        has_variant=True,
    )
    lookback_days: int = int(MILD_CONFIG["lookback_days"])
    pct_change_lo: float = float(MILD_CONFIG["pct_change_lo"])
    pct_change_hi: float = float(MILD_CONFIG["pct_change_hi"])
    rel_std_max: float = float(MILD_CONFIG["rel_std_max"])
    vol_ratio_lo: float = float(MILD_CONFIG["vol_ratio_lo"])
    vol_ratio_hi: float = float(MILD_CONFIG["vol_ratio_hi"])


@dataclass(frozen=True)
class Reversal1mDeep(_ReversalBase):
    """1 月深度反转: 跌 4-30%, 低波动. 样本少, RankIC 高但右尾风险大."""
    metadata: FormulaMetadata = FormulaMetadata(
        formula_id="reversal_1m_deep",
        name="1 月深度反转",
        tag="RD",
        description="20 日跌 4-30% + 60 日低波 + 量比正常 → 深度超跌反转候选",
        default_horizon_days=15,
        has_variant=True,
    )
    lookback_days: int = int(DEEP_CONFIG["lookback_days"])
    pct_change_lo: float = float(DEEP_CONFIG["pct_change_lo"])
    pct_change_hi: float = float(DEEP_CONFIG["pct_change_hi"])
    rel_std_max: float = float(DEEP_CONFIG["rel_std_max"])   # 深跌允许波动稍大
    vol_ratio_lo: float = float(DEEP_CONFIG["vol_ratio_lo"])
    vol_ratio_hi: float = float(DEEP_CONFIG["vol_ratio_hi"])


@dataclass(frozen=True)
class Reversal1w(_ReversalBase):
    """1 周反转: 跌 1-10%, 低波动. 信号高频, 换手大, 适合短线."""
    metadata: FormulaMetadata = FormulaMetadata(
        formula_id="reversal_1w",
        name="1 周反转",
        tag="RW",
        description="5 日跌 1-10% + 60 日低波 + 量比正常 → 周度反转候选",
        default_horizon_days=5,
        has_variant=True,
    )
    lookback_days: int = int(W1_CONFIG["lookback_days"])
    pct_change_lo: float = float(W1_CONFIG["pct_change_lo"])
    pct_change_hi: float = float(W1_CONFIG["pct_change_hi"])
    rel_std_max: float = float(W1_CONFIG["rel_std_max"])
    vol_ratio_lo: float = float(W1_CONFIG["vol_ratio_lo"])
    vol_ratio_hi: float = float(W1_CONFIG["vol_ratio_hi"])


# ── 注册 ────────────────────────────────────────────────
register_formula(Reversal1mMild())
register_formula(Reversal1mDeep())
register_formula(Reversal1w())
