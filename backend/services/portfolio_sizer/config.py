"""Portfolio sizer profile config loader."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "portfolio_sizer_profiles.yaml"


@dataclass(frozen=True)
class PortfolioSizerProfileSpec:
    label: str
    max_positions: int
    stock_cap_pct: float
    kelly_fraction: float
    holding_days: tuple[int, ...]
    min_wilson_win: float
    min_n_signals: int
    trailing_pct_min: float
    trailing_ratio: float
    exclude_fund_stages: tuple[str, ...] = ()
    exclude_tech_stages: tuple[str, ...] = ()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"portfolio sizer profile config missing: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return (str(value),)


def load_portfolio_sizer_profile_specs(
    path: str | Path | None = None,
) -> dict[str, PortfolioSizerProfileSpec]:
    raw = _load_yaml(Path(path) if path is not None else _CONFIG_PATH)
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("portfolio_sizer_profiles.yaml must contain a non-empty 'profiles' mapping")

    out: dict[str, PortfolioSizerProfileSpec] = {}
    for profile_id, item in profiles.items():
        if not isinstance(item, dict):
            raise ValueError(f"profile {profile_id!r} must be a mapping")
        out[str(profile_id)] = PortfolioSizerProfileSpec(
            label=str(item["label"]),
            max_positions=int(item["max_positions"]),
            stock_cap_pct=float(item["stock_cap_pct"]),
            kelly_fraction=float(item["kelly_fraction"]),
            holding_days=tuple(int(v) for v in item.get("holding_days") or ()),
            min_wilson_win=float(item["min_wilson_win"]),
            min_n_signals=int(item["min_n_signals"]),
            trailing_pct_min=float(item["trailing_pct_min"]),
            trailing_ratio=float(item["trailing_ratio"]),
            exclude_fund_stages=_as_tuple(item.get("exclude_fund_stages")),
            exclude_tech_stages=_as_tuple(item.get("exclude_tech_stages")),
        )
    return out
