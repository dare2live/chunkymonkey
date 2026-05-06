"""Configurable source policies for data capabilities.

The config file is intentionally small and dependency-light. If PyYAML is
available we use it; otherwise a constrained parser handles the repo's simple
``backend/config/data_sources.yaml`` shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "data_sources.yaml"


@dataclass(frozen=True)
class CapabilitySourcePolicy:
    name: str
    primary: str
    fallback: tuple[str, ...]
    canonical_relation: str | None = None
    allow_fallback_for_latest_gap: bool = False
    require_fallback_lineage: bool = False
    max_primary_lag_trading_days: int | None = None


DEFAULT_POLICIES: dict[str, CapabilitySourcePolicy] = {
    "kline_daily": CapabilitySourcePolicy(
        name="kline_daily",
        primary="tdxhub",
        fallback=("akshare_multi_source",),
        canonical_relation="market.v_price_kline_qfq",
        allow_fallback_for_latest_gap=True,
        require_fallback_lineage=True,
        max_primary_lag_trading_days=1,
    )
}


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"null", "none"}:
        return None
    try:
        return int(text)
    except ValueError:
        return text.strip("'\"")


def _load_yaml_subset(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_section: str | None = None
    current_capability: str | None = None
    current_list_key: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0:
            current_capability = None
            current_list_key = None
            if stripped.endswith(":"):
                current_section = stripped[:-1]
                data.setdefault(current_section, {})
            elif ":" in stripped:
                key, value = stripped.split(":", 1)
                data[key.strip()] = _parse_scalar(value)
            continue

        if current_section == "capabilities" and indent == 2 and stripped.endswith(":"):
            current_capability = stripped[:-1]
            current_list_key = None
            data.setdefault("capabilities", {}).setdefault(current_capability, {})
            continue

        if current_section == "capabilities" and current_capability and indent == 4 and ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            target = data["capabilities"][current_capability]
            if value:
                target[key] = _parse_scalar(value)
                current_list_key = None
            else:
                target[key] = []
                current_list_key = key
            continue

        if (
            current_section == "capabilities"
            and current_capability
            and current_list_key
            and indent == 6
            and stripped.startswith("- ")
        ):
            data["capabilities"][current_capability][current_list_key].append(
                _parse_scalar(stripped[2:])
            )

    return data


def load_source_policy_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path is not None else CONFIG_PATH
    if not config_path.exists():
        return {"capabilities": {}}
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return loaded if isinstance(loaded, dict) else {"capabilities": {}}
    except Exception:
        return _load_yaml_subset(config_path)


def _policy_from_raw(name: str, raw: dict[str, Any], base: CapabilitySourcePolicy) -> CapabilitySourcePolicy:
    fallback = raw.get("fallback", base.fallback)
    if isinstance(fallback, str):
        fallback = (fallback,)
    return CapabilitySourcePolicy(
        name=name,
        primary=str(raw.get("primary", base.primary)),
        fallback=tuple(str(item) for item in (fallback or ())),
        canonical_relation=raw.get("canonical_relation", base.canonical_relation),
        allow_fallback_for_latest_gap=bool(
            raw.get("allow_fallback_for_latest_gap", base.allow_fallback_for_latest_gap)
        ),
        require_fallback_lineage=bool(
            raw.get("require_fallback_lineage", base.require_fallback_lineage)
        ),
        max_primary_lag_trading_days=(
            int(raw["max_primary_lag_trading_days"])
            if raw.get("max_primary_lag_trading_days") is not None
            else base.max_primary_lag_trading_days
        ),
    )


def load_source_policies(path: str | Path | None = None) -> dict[str, CapabilitySourcePolicy]:
    config = load_source_policy_config(path)
    raw_capabilities = config.get("capabilities") if isinstance(config, dict) else {}
    raw_capabilities = raw_capabilities if isinstance(raw_capabilities, dict) else {}

    policies = dict(DEFAULT_POLICIES)
    for name, raw in raw_capabilities.items():
        if not isinstance(raw, dict):
            continue
        base = policies.get(
            str(name),
            CapabilitySourcePolicy(name=str(name), primary=str(raw.get("primary", "")), fallback=()),
        )
        policies[str(name)] = _policy_from_raw(str(name), raw, base)
    return policies


def get_capability_policy(name: str, path: str | Path | None = None) -> CapabilitySourcePolicy:
    policies = load_source_policies(path)
    if name not in policies:
        raise KeyError(f"unknown capability source policy: {name}")
    return policies[name]


def normalize_kline_write_source(source: str | None) -> str:
    """Return the canonical source label used when writing K-line rows."""
    raw = str(source or "").strip()
    if not raw:
        return "akshare_unknown"
    if raw == "tdxhub" or raw.startswith("tdxhub_"):
        return raw
    if raw.startswith("akshare_"):
        return raw
    return f"akshare_{raw}"
