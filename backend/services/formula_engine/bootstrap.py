"""Formula engine bootstrap.

Import this module to register the full current formula set into REGISTRY.
Keep the import list centralized so new formulas are added in one place.
"""
from __future__ import annotations

# Import side effects register every formula into services.formula_engine.REGISTRY.
from services.formula_engine import macd_golden_cross  # noqa: F401
from services.formula_engine import turtle_breakout  # noqa: F401
from services.formula_engine import dynamic_ma_iterative  # noqa: F401
from services.formula_engine import reversal_short_term  # noqa: F401
import services.formula_engine.bc_absorbed_challengers  # noqa: F401
