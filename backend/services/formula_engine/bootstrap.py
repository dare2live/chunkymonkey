"""Formula engine bootstrap.

Import this module to register the current formula set into REGISTRY.
Keep the import list centralized so new formulas are added in one place.
The default live history pipeline uses ``LIVE_FORMULA_IDS`` below; a small
candidate subset stays registered for experiments but is not rebuilt by
default.
"""
from __future__ import annotations

# Import side effects register every formula into services.formula_engine.REGISTRY.
from services.formula_engine import macd_golden_cross  # noqa: F401
from services.formula_engine import turtle_breakout  # noqa: F401
from services.formula_engine import dynamic_ma_iterative  # noqa: F401
from services.formula_engine import reversal_short_term  # noqa: F401
import services.formula_engine.bc_absorbed_challengers  # noqa: F401
from services.formula_engine import REGISTRY
from services.formula_engine.bc_absorbed_challengers import BANK_HELD_BACK_EXTENSION_FORMULA_IDS

HELD_BACK_FORMULA_IDS = BANK_HELD_BACK_EXTENSION_FORMULA_IDS


LIVE_FORMULA_IDS = tuple(
    formula_id for formula_id in REGISTRY.keys() if formula_id not in BANK_HELD_BACK_EXTENSION_FORMULA_IDS
)


__all__ = ["HELD_BACK_FORMULA_IDS", "LIVE_FORMULA_IDS"]
