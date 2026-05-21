"""Pricing and label policy model loading."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pricing_label_policy.yaml"


@dataclass(frozen=True)
class PricingLabelPolicy:
    policy_id: str
    version: int
    event_calc_version: str
    price_adjustment: str
    unknown_notice_time_execution: str
    same_day_execution_allowed: bool
    institution_cost_primary: str
    institution_cost_fallbacks: tuple[str, ...]
    follow_entry_primary: str
    follow_entry_fallbacks: tuple[str, ...]
    follow_volume_unit_guard: bool
    follow_volume_hand_adjustment_allowed: bool
    follow_qfq_factor_adjustment_required_for_hand_volume: bool
    follow_exit_default: str
    follow_exit_needs_definition: bool
    alpha_forward_label_current: str
    alpha_forward_label_needs_migration_review: bool
    transaction_cost_bps: float
    transaction_cost_meaning: str
    stale_on_policy_change: bool
    require_policy_id_in_manifest: bool
    definition_sections: dict[str, Any]

    @property
    def follow_entry_price_mode(self) -> str:
        return self.follow_entry_primary

    @property
    def follow_entry_ref_price_mode(self) -> str:
        fallback = self.follow_entry_fallbacks[0] if self.follow_entry_fallbacks else "none"
        return f"{self.follow_entry_primary}_fallback_{fallback.replace('entry_day_', '').replace('_qfq', '')}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "event_calc_version": self.event_calc_version,
            "price_adjustment": self.price_adjustment,
            "announcement_policy": {
                "unknown_notice_time_execution": self.unknown_notice_time_execution,
                "same_day_execution_allowed": self.same_day_execution_allowed,
            },
            "institution_cost": {
                "primary": self.institution_cost_primary,
                "fallbacks": list(self.institution_cost_fallbacks),
            },
            "follow_entry": {
                "primary": self.follow_entry_primary,
                "fallbacks": list(self.follow_entry_fallbacks),
                "volume_unit_guard": self.follow_volume_unit_guard,
                "volume_hand_adjustment_allowed": self.follow_volume_hand_adjustment_allowed,
                "qfq_factor_adjustment_required_for_hand_volume": (
                    self.follow_qfq_factor_adjustment_required_for_hand_volume
                ),
                "ref_price_mode": self.follow_entry_ref_price_mode,
            },
            "follow_exit": {
                "default": self.follow_exit_default,
                "needs_definition": self.follow_exit_needs_definition,
            },
            "alpha_forward_label": {
                "current": self.alpha_forward_label_current,
                "needs_migration_review": self.alpha_forward_label_needs_migration_review,
            },
            "portfolio_transaction_cost": {
                "default_bps": self.transaction_cost_bps,
                "meaning": self.transaction_cost_meaning,
            },
            "production_rules": {
                "stale_on_policy_change": self.stale_on_policy_change,
                "require_policy_id_in_manifest": self.require_policy_id_in_manifest,
            },
            "definition_sections": self.definition_sections,
        }

    def policy_hash(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def training_blockers(self, *, scope: str = "model_training") -> list[str]:
        blockers: list[str] = []
        if self.follow_exit_needs_definition:
            blockers.append("follow_exit_price_policy_unfrozen")
        if self.alpha_forward_label_needs_migration_review and scope in {
            "model_training",
            "optuna_search",
            "champion_gate",
            "full_research",
        }:
            blockers.append("alpha_forward_label_policy_unreviewed")
        if not self.institution_cost_primary:
            blockers.append("institution_cost_policy_missing")
        if not self.follow_entry_primary:
            blockers.append("follow_entry_policy_missing")
        if self.transaction_cost_meaning != "execution_friction_only_not_entry_price":
            blockers.append("transaction_cost_meaning_ambiguous")
        if self.price_adjustment != "qfq":
            blockers.append("price_adjustment_not_qfq")
        if self.require_policy_id_in_manifest is not True:
            blockers.append("manifest_policy_id_not_required")
        required_sections = (
            "signal_policy",
            "data_source_policy",
            "holding_period",
            "follow_return_label",
            "feature_policy",
            "data_quality_policy",
            "performance_policy",
            "ranking_policy",
            "portfolio_construction",
            "risk_policy",
            "benchmark",
            "evaluation_metrics",
            "validation_split",
            "model_training",
            "model_family_policy",
            "optuna",
            "explainability",
            "promotion_gate",
            "champion_policy",
            "reproducibility_policy",
        )
        for section in required_sections:
            if section not in self.definition_sections:
                blockers.append(f"{section}_definition_missing")
        return blockers

    def training_warnings(self) -> list[str]:
        warnings: list[str] = []
        signal_policy = self.definition_sections.get("signal_policy") or {}
        signal_generation_time = str(signal_policy.get("signal_generation_time") or "")
        if self.same_day_execution_allowed and "after_market_close" in signal_generation_time:
            warnings.append("same_day_execution_allowed_requires_intraday_notice_time")
        if not self.follow_volume_unit_guard:
            warnings.append("follow_vwap_volume_unit_guard_disabled")
        if not self.follow_volume_hand_adjustment_allowed:
            warnings.append("follow_vwap_hand_volume_adjustment_disabled")
        if not self.follow_qfq_factor_adjustment_required_for_hand_volume:
            warnings.append("follow_vwap_qfq_factor_adjustment_disabled")
        return warnings


def load_pricing_label_policy(path: str | Path | None = None) -> PricingLabelPolicy:
    raw = _load_yaml(Path(path) if path is not None else CONFIG_PATH)
    announcement = raw.get("announcement_policy") or {}
    institution = raw.get("institution_cost") or {}
    follow_entry = raw.get("follow_entry") or {}
    follow_exit = raw.get("follow_exit") or {}
    alpha_label = raw.get("alpha_forward_label") or {}
    tx_cost = raw.get("portfolio_transaction_cost") or {}
    rules = raw.get("production_rules") or {}
    definition_sections = {
        key: raw.get(key)
        for key in _DEFINITION_KEYS
        if raw.get(key) is not None
    }
    return PricingLabelPolicy(
        policy_id=str(raw.get("policy_id") or "pricing_label_policy_vwap_follow_v1"),
        version=int(raw.get("version") or 1),
        event_calc_version=str(raw.get("event_calc_version") or "v3_qfq_vwap_entry_dual_cost"),
        price_adjustment=str(raw.get("price_adjustment") or "qfq"),
        unknown_notice_time_execution=str(
            announcement.get("unknown_notice_time_execution") or "signal_day_vwap_when_signal_emitted"
        ),
        same_day_execution_allowed=bool(announcement.get("same_day_execution_allowed", False)),
        institution_cost_primary=str(institution.get("primary") or "report_period_daily_vwap_qfq"),
        institution_cost_fallbacks=_as_tuple(institution.get("fallbacks")),
        follow_entry_primary=str(follow_entry.get("primary") or "entry_day_vwap_qfq"),
        follow_entry_fallbacks=_as_tuple(follow_entry.get("fallbacks") or ("entry_day_open_qfq",)),
        follow_volume_unit_guard=bool(follow_entry.get("volume_unit_guard", True)),
        follow_volume_hand_adjustment_allowed=bool(follow_entry.get("volume_hand_adjustment_allowed", True)),
        follow_qfq_factor_adjustment_required_for_hand_volume=bool(
            follow_entry.get("qfq_factor_adjustment_required_for_hand_volume", True)
        ),
        follow_exit_default=str(follow_exit.get("default") or "horizon_end_close_qfq"),
        follow_exit_needs_definition=bool(follow_exit.get("needs_definition", True)),
        alpha_forward_label_current=str(
            alpha_label.get("current") or "signal_day_close_to_horizon_end_close_qfq"
        ),
        alpha_forward_label_needs_migration_review=bool(alpha_label.get("needs_migration_review", True)),
        transaction_cost_bps=float(tx_cost.get("default_bps", 10.0)),
        transaction_cost_meaning=str(tx_cost.get("meaning") or "execution_friction_only_not_entry_price"),
        stale_on_policy_change=bool(rules.get("stale_on_policy_change", True)),
        require_policy_id_in_manifest=bool(rules.get("require_policy_id_in_manifest", True)),
        definition_sections=definition_sections,
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - local runtime has PyYAML.
        raise RuntimeError("PyYAML is required to load pricing_label_policy.yaml") from exc
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return loaded if isinstance(loaded, dict) else {}


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return (str(value),)


_DEFINITION_KEYS = (
    "announcement_policy",
    "signal_policy",
    "data_source_policy",
    "institution_cost",
    "follow_entry",
    "follow_exit",
    "holding_period",
    "alpha_forward_label",
    "follow_return_label",
    "portfolio_transaction_cost",
    "premium",
    "corporate_action_policy",
    "tradability",
    "universe",
    "feature_policy",
    "data_quality_policy",
    "performance_policy",
    "ranking_policy",
    "portfolio_construction",
    "risk_policy",
    "benchmark",
    "evaluation_metrics",
    "validation_split",
    "model_training",
    "model_family_policy",
    "optuna",
    "explainability",
    "promotion_gate",
    "champion_policy",
    "reproducibility_policy",
    "frontend_policy",
    "production_rules",
)

