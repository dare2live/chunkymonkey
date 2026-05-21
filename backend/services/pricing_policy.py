"""Backward-compatible pricing policy facade."""
from __future__ import annotations

from services.pricing_policy_model import CONFIG_PATH, PricingLabelPolicy, load_pricing_label_policy
from services.pricing_policy_readiness import record_pricing_label_data_readiness_gate
from services.pricing_policy_records import (
    DDL,
    ensure_pricing_policy_table,
    record_pricing_label_policy,
    record_pricing_label_policy_gate,
)

__all__ = [
    "CONFIG_PATH",
    "DDL",
    "PricingLabelPolicy",
    "ensure_pricing_policy_table",
    "load_pricing_label_policy",
    "record_pricing_label_data_readiness_gate",
    "record_pricing_label_policy",
    "record_pricing_label_policy_gate",
]
