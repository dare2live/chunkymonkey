from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compute import ComputeEngine


def main() -> None:
    engine = ComputeEngine()
    data = engine.unified_data()
    assert data is not None
    assert "profile_ids" in data
    assert len(data["profile_ids"]) >= 2
    if data.get("ready"):
        assert "stocks" in data
        assert "summary" in data
        if data["stocks"]:
            row = data["stocks"][0]
            assert "strategy_signals" in row
            assert "today_recommend_reason" in row
    else:
        assert "missing_profiles" in data
        assert "computing_profiles" in data
    print("unified_data_smoke: ok")


if __name__ == "__main__":
    main()
