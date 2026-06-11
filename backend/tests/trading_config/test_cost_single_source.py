"""防回退: registry 成本与 paper_sim_config tx_cost 单一真相源一致.

2026-06-11 体检 MEDIUM 修复防回退:
  - registry sell_stamp_duty_bps 旧 hardcode=10 (印花 0.10%) vs paper_sim
    stamp_duty_sell_pct=0.0005 (0.05%, 2023-08 减半真值) 冲突.
  - registry round_trip 漏算交易所规费 + 证管费, 低估真实成本.
修复: registry.cost 从 paper_sim_config.yaml::tx_cost 派生; round_trip 与
labels.cost_after.compute_round_trip_cost_pct 完全一致; assert_cost_single_source 守门.
"""
from __future__ import annotations

import pytest

from services.labels.cost_after import compute_round_trip_cost_pct
from services.paper_sim.config import load_config
from services.trading_config.registry import (
    DEFAULT_EXECUTION_MODEL,
    assert_cost_single_source,
)


def test_stamp_duty_is_5bps_not_10():
    """印花税 2023-08-28 减半 = 0.05% = 5 bps, 不是旧的 10 bps."""
    assert DEFAULT_EXECUTION_MODEL.cost.sell_stamp_duty_bps == pytest.approx(5.0)


def test_registry_roundtrip_equals_canonical():
    """registry round_trip == labels.cost_after canonical round_trip (单一真相源)."""
    registry_rt = DEFAULT_EXECUTION_MODEL.cost.round_trip_cost_pct()
    canonical_rt = compute_round_trip_cost_pct(load_config().tx_cost)
    assert registry_rt == pytest.approx(canonical_rt, abs=1e-12)


def test_assert_cost_single_source_passes():
    """收敛后断言通过 (不 raise)."""
    assert_cost_single_source()  # 不应 raise


def test_assert_cost_single_source_catches_divergence(monkeypatch):
    """有人改回 registry hardcode / 改 yaml 没同步 → 断言必须 raise (tripwire)."""
    import services.trading_config.registry as reg

    class _Bad:
        def round_trip_cost_pct(self):
            return 0.999  # 故意偏离

    class _Model:
        cost = _Bad()

    monkeypatch.setattr(reg, "DEFAULT_EXECUTION_MODEL", _Model())
    with pytest.raises(AssertionError, match="成本真相源分裂"):
        reg.assert_cost_single_source()


def test_cost_derived_not_hardcoded():
    """commission / impact 也跟 yaml 一致 (证明是派生不是巧合 hardcode)."""
    tx = load_config().tx_cost
    c = DEFAULT_EXECUTION_MODEL.cost
    assert c.buy_commission_bps == pytest.approx(tx.commission_pct * 10000)
    assert c.sell_commission_bps == pytest.approx(tx.commission_pct * 10000)
    assert c.buy_impact_bps == pytest.approx(tx.slippage_pct * 10000)
    assert c.sell_impact_bps == pytest.approx(tx.slippage_pct * 10000)
