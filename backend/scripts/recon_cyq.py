#!/usr/bin/env python3
"""Read-only nominal-K chip overlay vs cyq_perf archive.

Does not change primaries. Overlay is a labeled method, not observed
holdings. cyq_perf is not accepted publication. winner_rate stays out
of formulas. Sample only.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_access.resolver import db_path  # noqa: E402
from services.data_sources.cyq_recon import (  # noqa: E402
    compare_chip_sample,
    compact_yyyymmdd,
    cyq_publication_status,
    join_bars_and_basic,
    latest_canonical_k_day,
    load_cyq_perf,
    load_daily_basic,
    load_nominal_bars,
    overlay_chips,
)
from services.duck_adapter import connect  # noqa: E402

DEFAULT_CODES = ("600519.SH", "000001.SZ")


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path}")


def _sample_codes(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def _lookback_start(end: str, days: int) -> str:
    parsed = datetime.strptime(end, "%Y%m%d")
    return (parsed - timedelta(days=days)).strftime("%Y%m%d")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "audit" / "historical" / "cyq_recon.json",
    )
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES))
    parser.add_argument("--lookback-days", type=int, default=180)
    args = parser.parse_args(argv)

    codes = _sample_codes(args.codes)
    con = connect(str(db_path("tushare_raw")), read_only=True)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),  # rule-compliance: ok evidence=audit metadata, not trade_date
        "primary_cut": False,
        "formula_winner_rate": False,
        "sample_codes": codes,
        "publication": cyq_publication_status(),
        "method": "turnover_overlay_v1",
        "coordinate": "nominal_unadjusted",
    }
    try:
        asof = latest_canonical_k_day(con)
        report["asof"] = asof
        if asof is None:
            report["samples"] = {"status": "empty_recon", "reason": "no canonical k asof"}
            _write(args.out, report)
            return 0
        start = _lookback_start(asof, args.lookback_days)
        samples = []
        for code in codes:
            bars = load_nominal_bars(con, code, start=start, end=asof)
            basic = load_daily_basic(con, code, start=start, end=asof)
            vendor = load_cyq_perf(con, code, start=start, end=asof)
            model = overlay_chips(join_bars_and_basic(bars, basic))
            compared_model = [r for r in model if r.get("status") == "ok"][-20:]
            compared_vendor = [
                r
                for r in vendor
                if compact_yyyymmdd(r.get("trade_date"))
                and compact_yyyymmdd(r.get("trade_date"))
                >= (compared_model[0]["trade_date"] if compared_model else start)
            ]
            body = compare_chip_sample(compared_model, compared_vendor)
            body["ts_code"] = code
            body["bars_n"] = len(bars)
            body["basic_n"] = len(basic)
            body["vendor_n"] = len(vendor)
            body["model_n"] = len(model)
            samples.append(body)
        report["start"] = start
        report["samples"] = samples
        report["identity"] = False
    finally:
        con.close()

    _write(args.out, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
