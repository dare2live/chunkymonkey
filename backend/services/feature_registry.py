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
    source_event_date_column: str = ""
    source_available_date_column: str = ""
    required_capabilities: tuple[str, ...] = ()
    pit_release_lag_days: int = 0
    feature_role: str = "core_model_input"
    availability_cadence: str = "daily"
    panel_density: str = "dense_daily"
    expected_update_frequency: str = "daily"
    null_policy: str = "block_unclassified_null"
    coverage_universe: str = "active_a_stock"
    frontend_visible: bool = True
    notes: str = ""


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


def label_columns_by_prefix(
    prefix: str,
    *,
    registry: FeatureRegistry | None = None,
) -> tuple[str, ...]:
    reg = registry or load_feature_registry()
    return tuple(name for name in reg.label_columns() if name.startswith(prefix))


def forward_return_label_columns(*, registry: FeatureRegistry | None = None) -> tuple[str, ...]:
    return label_columns_by_prefix("forward_ret_", registry=registry)


def follow_return_label_columns(*, registry: FeatureRegistry | None = None) -> tuple[str, ...]:
    return label_columns_by_prefix("follow_net_return_", registry=registry)


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
        group_source_event_date_column = str(group_raw.get("source_event_date_column", ""))
        group_source_available_date_column = str(group_raw.get("source_available_date_column", ""))
        group_required_capabilities = _as_tuple(group_raw.get("required_capabilities"))
        group_lag = max(int(group_raw.get("pit_release_lag_days", 0) or 0), 0)
        group_feature_role = str(group_raw.get("feature_role", "core_model_input"))
        group_availability_cadence = str(group_raw.get("availability_cadence", "daily"))
        group_panel_density = str(group_raw.get("panel_density", "dense_daily"))
        group_expected_update_frequency = str(
            group_raw.get("expected_update_frequency", group_availability_cadence)
        )
        group_null_policy = str(group_raw.get("null_policy", "block_unclassified_null"))
        group_coverage_universe = str(group_raw.get("coverage_universe", "active_a_stock"))
        group_frontend_visible = bool(group_raw.get("frontend_visible", True))
        group_notes = str(group_raw.get("notes", ""))

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
                source_event_date_column=str(
                    feature_raw.get("source_event_date_column", group_source_event_date_column)
                ),
                source_available_date_column=str(
                    feature_raw.get("source_available_date_column", group_source_available_date_column)
                ),
                required_capabilities=_as_tuple(
                    feature_raw.get("required_capabilities", group_required_capabilities)
                ),
                pit_release_lag_days=max(
                    int(feature_raw.get("pit_release_lag_days", group_lag) or 0),
                    0,
                ),
                feature_role=str(feature_raw.get("feature_role", group_feature_role)),
                availability_cadence=str(feature_raw.get("availability_cadence", group_availability_cadence)),
                panel_density=str(feature_raw.get("panel_density", group_panel_density)),
                expected_update_frequency=str(
                    feature_raw.get("expected_update_frequency", group_expected_update_frequency)
                ),
                null_policy=str(feature_raw.get("null_policy", group_null_policy)),
                coverage_universe=str(feature_raw.get("coverage_universe", group_coverage_universe)),
                frontend_visible=bool(feature_raw.get("frontend_visible", group_frontend_visible)),
                notes=str(feature_raw.get("notes", group_notes)),
            )
    return FeatureRegistry(features=features, model_input_excluded=model_input_excluded)
