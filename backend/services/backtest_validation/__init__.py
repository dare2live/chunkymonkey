"""Backtest validation gates — PBO / DSR / Conservative / IS-OOS.

ChunkyMonkey MSAF Phase 1.5 (Codex R31 design doc).
Module 阻断过拟合/非显著/不保守/IS-OOS 断裂策略 promote 实盘.

4 hard gates (论文 reference):
- gate_pbo: Lopez de Prado CSCV, PBO = Pr(lambda < 0)
- gate_dsr: Bailey & Lopez de Prado DSR, p_conf >= 0.95
- gate_conservative: 滑点 +50% / VWAP+open / 涨跌停 mask 后 ann > 0
- gate_is_oos: IS-OOS gap < 30% relative

Public API:
- run_all_gates(challenger_id) -> dict[str, GateResult]
"""

from services.backtest_validation.gate import run_all_gates, GateResult
from services.backtest_validation.pbo import compute_pbo
from services.backtest_validation.dsr import compute_dsr

__all__ = ["run_all_gates", "GateResult", "compute_pbo", "compute_dsr"]
