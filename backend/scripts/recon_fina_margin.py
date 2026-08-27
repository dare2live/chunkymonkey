#!/usr/bin/env python3
"""Read-only finance + margin recon vs canonical exchange and 妙想 F10 sample.

Does not change primaries. Stock-level margin sum is not exchange identity.
Income/balancesheet remain landing orphans (no accepted publication).
PIT is announcement date. gpcw revival is forbidden. Sample only — no
full-market 妙想 scrape.
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

from services.data_access.resolver import db_path  # noqa: E402
from services.data_sources.fina_margin_recon import (  # noqa: E402
    compare_balancesheet_sample,
    compare_income_sample,
    compare_mainfina_sample,
    compare_margin_totals,
    fina_publication_status,
    latest_canonical_margin_day,
    load_exchange_margin,
    load_landing_balancesheet,
    load_landing_fina_indicator,
    load_landing_income,
    load_margin_detail_sum,
)
from services.data_sources.sibling_repos import ensure_import_path  # noqa: E402
from services.duck_adapter import connect  # noqa: E402

DEFAULT_CODES = ("600519.SH", "000001.SZ")
GINCOME = "RPT_F10_FINANCE_GINCOME"
GBALANCE = "RPT_F10_FINANCE_GBALANCE"
MAINFINA = "RPT_F10_FINANCE_MAINFINADATA"


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path}")


def _fetch_miaoxiang_v1(report_name: str, secucode: str, *, page_size: int) -> dict[str, Any]:
    """Live F10 rows via v1.

    Registry labels GINCOME/GBALANCE/MAINFINADATA as v0, but
    ``aif10_scraper.client.get_v0`` reads top-level ``data`` (often null)
    and returns []. Live JSON keeps rows in ``result.data``; v1 unwraps that.
    """
    ensure_import_path("miaoxiang")
    from aif10_scraper.client import AIF10Client  # noqa: E402
    from aif10_scraper.registry import get_report  # noqa: E402

    spec = get_report(report_name)
    client = AIF10Client()
    rows: list[dict[str, Any]] = []
    try:
        first = client.get_v1(
            spec.name,
            page=1,
            page_size=page_size,
            secucode=secucode,
            sort_columns="REPORT_DATE",
            sort_types="-1",
        )
        rows.extend(first.get("data") or [])
        pages = int(first.get("pages") or 0)
        count = int(first.get("count") or 0)
        for page in range(2, pages + 1):
            more = client.get_v1(
                spec.name,
                page=page,
                page_size=page_size,
                secucode=secucode,
                sort_columns="REPORT_DATE",
                sort_types="-1",
            )
            rows.extend(more.get("data") or [])
    finally:
        client.close()
    truncated = bool(count) and len(rows) < count
    return {
        "report": report_name,
        "secucode": secucode,
        "endpoint": "v1",
        "registry_api": getattr(spec, "api", None),
        "n": len(rows),
        "count": count,
        "truncated": truncated,
        "rows": rows,
    }


def _sample_codes(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "audit" / "historical" / "fina_margin_recon.json",
    )
    parser.add_argument("--skip-miaoxiang", action="store_true")
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES))
    parser.add_argument("--page-size", type=int, default=200)
    args = parser.parse_args(argv)

    codes = _sample_codes(args.codes)
    con = connect(str(db_path("tushare_raw")), read_only=True)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),  # rule-compliance: ok evidence=audit metadata, not trade_date
        "primary_cut": False,
        "gpcw_revival": False,
        "sample_codes": codes,
        "publication": {
            "income": fina_publication_status("income"),
            "balancesheet": fina_publication_status("balancesheet"),
            "fina_indicator": fina_publication_status("fina_indicator"),
            "margin_detail": fina_publication_status("margin_detail"),
        },
    }
    try:
        asof = latest_canonical_margin_day(con)
        report["asof"] = asof
        if asof is None:
            report["margin"] = {"status": "empty_recon", "reason": "no canonical margin asof"}
        else:
            exchange = load_exchange_margin(con, asof)
            detail = load_margin_detail_sum(con, asof)
            body = compare_margin_totals(exchange, detail)
            body["exchange"] = exchange
            body["detail"] = detail
            if detail.get("rzrqye_ex_bj") is not None:
                body["vs_detail_ex_bj"] = compare_margin_totals(
                    exchange.get("rzrqye"), detail.get("rzrqye_ex_bj")
                )
            report["margin"] = body

        income_landing = load_landing_income(con, codes)
        balancesheet_landing = load_landing_balancesheet(con, codes)
        fina_landing = load_landing_fina_indicator(con, codes)
        report["income_landing_n"] = len(income_landing)
        report["balancesheet_landing_n"] = len(balancesheet_landing)
        report["fina_indicator_landing_n"] = len(fina_landing)
        report["income_landing_sample"] = sorted(
            income_landing, key=lambda r: r.get("end_date") or "", reverse=True
        )[:8]
    finally:
        con.close()

    if args.skip_miaoxiang:
        report["miaoxiang"] = {"status": "skipped"}
        report["income"] = compare_income_sample(income_landing, [])
        report["balancesheet"] = compare_balancesheet_sample(balancesheet_landing, [])
        report["mainfina"] = compare_mainfina_sample(fina_landing, [])
    else:
        mx: dict[str, Any] = {
            "endpoint": "v1",
            "registry_api": "v0",
            "pit_model": "NOTICE_DATE",
            "note": (
                "get_v0 drops result.data (top-level data is null); "
                "v1 serves the same reportName"
            ),
        }
        gincome_rows: list[dict[str, Any]] = []
        gbalance_rows: list[dict[str, Any]] = []
        mainfina_rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        try:
            for code in codes:
                for report_name, bucket in (
                    (GINCOME, gincome_rows),
                    (GBALANCE, gbalance_rows),
                    (MAINFINA, mainfina_rows),
                ):
                    try:
                        fetched = _fetch_miaoxiang_v1(
                            report_name, code, page_size=args.page_size
                        )
                        bucket.extend(fetched["rows"])
                        mx.setdefault("fetches", []).append(
                            {
                                "report": report_name,
                                "secucode": code,
                                "n": fetched["n"],
                                "count": fetched["count"],
                                "truncated": fetched["truncated"],
                                "endpoint": fetched["endpoint"],
                            }
                        )
                    except Exception as exc:  # rule-compliance: ok evidence=vendor fetch classified, not swallowed as match
                        errors.append(
                            {
                                "report": report_name,
                                "secucode": code,
                                "status": "error",
                                "type": type(exc).__name__,
                                "message": str(exc)[:240],
                            }
                        )
        except Exception as exc:
            mx["status"] = "error"
            mx["type"] = type(exc).__name__
            mx["message"] = str(exc)[:240]
        if errors:
            mx["errors"] = errors
        mx["gincome_n"] = len(gincome_rows)
        mx["gbalance_n"] = len(gbalance_rows)
        mx["mainfina_n"] = len(mainfina_rows)
        report["miaoxiang"] = mx
        report["income"] = compare_income_sample(income_landing, gincome_rows)
        report["balancesheet"] = compare_balancesheet_sample(
            balancesheet_landing, gbalance_rows
        )
        report["mainfina"] = compare_mainfina_sample(fina_landing, mainfina_rows)

    _write(args.out, report)
    margin = report.get("margin") or {}
    print(
        "margin "
        f"status={margin.get('status')} identity={margin.get('identity')} "
        f"abs_diff={margin.get('abs_diff')} asof={report.get('asof')}"
    )
    print(
        "income "
        f"status={report['income'].get('status')} periods={report['income'].get('periods')} "
        f"identity={report['income'].get('identity')} "
        f"primary_cut={report['primary_cut']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
