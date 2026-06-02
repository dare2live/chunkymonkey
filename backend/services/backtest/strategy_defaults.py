"""Phase ε.4 — 策略默认参数 (stop / target / trailing).

⚠ 这些是策略参数 (区别于 execution_model 的执行参数).
⚠ 改 yaml → 重建 mart_stock_formula_optuna_v2 全表都受影响.
⚠ D 路线 (ζ) 会按桶寻优替代这些默认值.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class StrategyDefaults:
    """单一策略的 stop/target/trailing 三连参数."""
    stop_pct: float        # 例 -0.06: stop_price = buy × 0.94
    target_pct: float      # 例 +0.10: target = buy × 1.10 (达到 arm trailing)
    trailing_pct: float    # 例 0.025: 从 high_since_buy 回撤超 2.5% → 卖
    label: str = ""        # 调试用名


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "strategy_defaults.yaml"


def _load_yaml(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


def _load_defaults(path: Path | None = None) -> StrategyDefaults:
    raw_path = path or CONFIG_PATH
    raw = _load_yaml(raw_path)
    try:
        return StrategyDefaults(
            stop_pct=float(raw["stop_pct"]),
            target_pct=float(raw["target_pct"]),
            trailing_pct=float(raw["trailing_pct"]),
            label=str(raw["label"]),
        )
    except KeyError as exc:
        raise ValueError(f"{raw_path.name}: missing strategy default key {exc.args[0]}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{raw_path.name}: strategy defaults must be numeric mappings") from exc


# Phase ε 默认: 偏保守 (止损 6% / 止盈 10% / 移动 2.5%)
# 后续 D 路线会按 (stock × formula × bucket × hp) 寻优
DEFAULT_STRATEGY = _load_defaults()
