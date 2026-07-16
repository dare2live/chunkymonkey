"""Deterministic semantic smoke for the frozen five-formula challenger.

This guards accidental drift only. It is not PIT, execution, or strategy
validation evidence.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from formula_engine import FORMULA_DEFINITIONS, compute_formula_signals


EXPECTED_FORMULAS = (
    "gs_pullback_confirm",
    "gs_raw_buy",
    "ma_base_breakout",
    "activity_breakout",
    "volume_base_breakout",
)

EXPECTED_VECTORS = {
    "gs_pullback_confirm": (3, 35, "b5212a23aba3be4481bd3b98f0a24198f22d5d69e130604757b559bf9b07b6d2"),
    "gs_raw_buy": (36, 35, "abf3d794547961b4240e5d687d5bd989d05bfb55fa1807238d2e591f1fcea512"),
    "ma_base_breakout": (2, 446, "1f164411cab00f41857348fce6e96c2bc185e0e13392a6266beac812692936ea"),
    "activity_breakout": (195, 86, "4c19463a7c12d5f7024a8e5c0b48d0e0c0d7f3206c3f3315ed080259531526e6"),
    "volume_base_breakout": (63, 465, "ad8a18a0f7513e207bc2ed93d69fd5470713142c34cbfda174978f40efe5d9a7"),
}


def _fixture() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(10)
    size = 900
    close = 20.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.025, size)))
    open_ = close * np.exp(rng.normal(0.0, 0.008, size))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.002, 0.04, size))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.002, 0.04, size))
    volume = np.exp(rng.normal(np.log(1_000_000.0), 0.65, size))
    return {
        "open_": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": close * volume * 100.0,
    }


def test_formula_registry() -> None:
    assert tuple(FORMULA_DEFINITIONS) == EXPECTED_FORMULAS


def test_frozen_vectors() -> None:
    fixture = _fixture()
    for formula_id, expected in EXPECTED_VECTORS.items():
        result = compute_formula_signals(formula_id, **fixture)
        entry = np.asarray(result["entry"], dtype=np.bool_)
        exit_ = np.asarray(result["exit"], dtype=np.bool_)
        assert entry.shape == exit_.shape == fixture["close"].shape
        digest = hashlib.sha256(
            np.packbits(np.concatenate((entry, exit_))).tobytes()
        ).hexdigest()
        assert (int(entry.sum()), int(exit_.sum()), digest) == expected


def test_unknown_formula_fails_closed() -> None:
    try:
        compute_formula_signals("unknown", **_fixture())
    except ValueError as exc:
        assert "unknown formula_id" in str(exc)
    else:
        raise AssertionError("unknown formula must fail closed")


def main() -> None:
    test_formula_registry()
    test_frozen_vectors()
    test_unknown_formula_fails_closed()
    print("formula_engine_smoke: ok")


if __name__ == "__main__":
    main()
