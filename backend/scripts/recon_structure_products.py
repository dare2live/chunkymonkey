#!/usr/bin/env python3
"""Read-only attestation of formulas / rally / follow structure contracts.

Does not change primaries, does not run Optuna, is not StrategyRelease.
Paper is not a product claim.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.structure_product_recon import attest_structure_products  # noqa: E402


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "audit" / "historical" / "structure_product_recon.json",
    )
    args = parser.parse_args(argv)
    body = attest_structure_products()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),  # rule-compliance: ok evidence=audit metadata, not trade_date
        **body,
    }
    _write(args.out, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
