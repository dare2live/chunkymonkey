"""Feature registry helpers for panel generation and model inputs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "feature_registry.yaml"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    group: str
    dtype: str = "real"
    enabled: bool = True
    production_ready: bool = True
    candidate_only: bool = False
    label: bool = False
    model_input: bool = True
    source_tables: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    pit_release_lag_days: int = 0


@dataclass(frozen=True)
class FeatureRegistry:
    features: dict[str, FeatureSpec]
    model_input_excluded: tuple[str, ...] = ()

    def feature_names(self) -> tuple[str, ...]:
        return tuple(self.features)

    def label_columns(self) -> tuple[str, ...]:
        return tuple(name for name, spec in self.features.items() if spec.label)

    def model_input_columns(
        self,
        *,
        include_disabled: bool = False,
        production_ready_only: bool = True,
    ) -> tuple[str, ...]:
        excluded = set(self.model_input_excluded)
        selected: list[str] = []
        for name, spec in self.features.items():
            if name in excluded or spec.label or not spec.model_input:
                continue
            if not include_disabled and not spec.enabled:
                continue
            if production_ready_only and not spec.production_ready:
                continue
            selected.append(name)
        return tuple(selected)

    def group_columns(
        self,
        group: str,
        *,
        include_disabled: bool = False,
    ) -> tuple[str, ...]:
        return tuple(
            name
            for name, spec in self.features.items()
            if spec.group == group and (include_disabled or spec.enabled)
        )

    def group_pit_release_lag_days(self, group: str, default: int = 0) -> int:
        lags = [spec.pit_release_lag_days for spec in self.features.values() if spec.group == group]
        if not lags:
            return int(default)
        return max(int(lag) for lag in lags)


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return (str(value),)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - local runtime has PyYAML.
        raise RuntimeError("PyYAML is required to load feature_registry.yaml") from exc
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _iter_feature_items(raw_features: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(raw_features, dict):
        for name, raw in raw_features.items():
            yield str(name), raw if isinstance(raw, dict) else {}
        return
    if isinstance(raw_features, list):
        for item in raw_features:
            if isinstance(item, str):
                yield item, {}
            elif isinstance(item, dict):
                for name, raw in item.items():
                    yield str(name), raw if isinstance(raw, dict) else {}


def load_feature_registry(path: str | Path | None = None) -> FeatureRegistry:
    config_path = Path(path) if path is not None else CONFIG_PATH
    raw = _load_yaml(config_path)
    groups = raw.get("groups", {}) if isinstance(raw, dict) else {}
    groups = groups if isinstance(groups, dict) else {}
    model_input_excluded = _as_tuple(raw.get("model_input_excluded"))

    features: dict[str, FeatureSpec] = {}
    for group_name, group_raw_any in groups.items():
        group = str(group_name)
        group_raw = group_raw_any if isinstance(group_raw_any, dict) else {}
        group_enabled = bool(group_raw.get("enabled", True))
        group_production_ready = bool(group_raw.get("production_ready", True))
        group_candidate_only = bool(group_raw.get("candidate_only", False))
        group_label = bool(group_raw.get("label", False))
        group_model_input = bool(group_raw.get("model_input", not group_label))
        group_source_tables = _as_tuple(group_raw.get("source_tables"))
        group_required_capabilities = _as_tuple(group_raw.get("required_capabilities"))
        group_lag = max(int(group_raw.get("pit_release_lag_days", 0) or 0), 0)

        for feature_name, feature_raw in _iter_feature_items(group_raw.get("features", [])):
            label = bool(feature_raw.get("label", group_label))
            model_input = bool(feature_raw.get("model_input", group_model_input and not label))
            features[feature_name] = FeatureSpec(
                name=feature_name,
                group=group,
                dtype=str(feature_raw.get("dtype", group_raw.get("dtype", "real"))),
                enabled=bool(feature_raw.get("enabled", group_enabled)),
                production_ready=bool(feature_raw.get("production_ready", group_production_ready)),
                candidate_only=bool(feature_raw.get("candidate_only", group_candidate_only)),
                label=label,
                model_input=model_input,
                source_tables=_as_tuple(feature_raw.get("source_tables", group_source_tables)),
                required_capabilities=_as_tuple(
                    feature_raw.get("required_capabilities", group_required_capabilities)
                ),
                pit_release_lag_days=max(
                    int(feature_raw.get("pit_release_lag_days", group_lag) or 0),
                    0,
                ),
            )
    return FeatureRegistry(features=features, model_input_excluded=model_input_excluded)
