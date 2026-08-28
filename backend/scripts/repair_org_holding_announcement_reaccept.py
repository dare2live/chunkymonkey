#!/usr/bin/env python3
"""Re-accept org_holding canonical history with PIT = announcement day.

Canonical history was stamped with statutory completeness dates (0430/0831/1031).
This repair reads local raw (no provider fetch), JOINs the company's first
periodic-report announcement (income.f_ann_date else holders notice_date),
KEEPS announced grains, DROPs the rest, and replaces that report_date in
canonical. Raw ``available_date`` is UPDATEd by grain so
``accept_org_holding_partition_from_legacy`` cannot recopy poisoned dates.

Default is dry-run (no writes). ``--execute`` is required to write.

Usage:
    PYTHONPATH=backend .venv/bin/python backend/scripts/repair_org_holding_announcement_reaccept.py
    PYTHONPATH=backend .venv/bin/python backend/scripts/repair_org_holding_announcement_reaccept.py --execute --report-date 20260630
    PYTHONPATH=backend .venv/bin/python backend/scripts/repair_org_holding_announcement_reaccept.py --execute
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))


def _resolve_db_file(alias: str, override: str | None = None) -> Path:
    """Resolve a duckdb file. Fail closed — never silently use another checkout.

    Order: ``--db`` override, else ``database_manifest.path_for``.
    Missing file → FileNotFoundError (tell the operator to pass ``--db``).
    """
    if override:
        path = Path(override).expanduser().resolve()
        if path.is_file():
            return path
        raise FileNotFoundError(
            f"{alias} not found at --db {path}. Pass --db to an existing duckdb."
        )
    tried: list[Path] = []
    from services.database_manifest import get_database_manifest

    manifest_path = get_database_manifest().path_for(alias)
    if manifest_path is not None:
        tried.append(manifest_path)
        if manifest_path.is_file():
            return manifest_path
    shown = ", ".join(str(p) for p in tried) or "no manifest path"
    raise FileNotFoundError(
        f"{alias} database file not found ({shown}). "
        "Worktree has no gitignored DBs; pass --db <path> for org_holding "
        "and place tushare_raw/smartmoney at the database_manifest paths."
    )


def _print_period(result: dict) -> None:
    dates = result.get("distinct_new_available_dates") or []
    print(
        f"report_date={result.get('report_date')} "
        f"raw_rows={result.get('raw_rows')} "
        f"with_announcement={result.get('with_announcement')} "
        f"skipped_no_announcement={result.get('skipped_no_announcement')} "
        f"distinct_new_available_dates={len(dates)} "
        f"current_canonical_rows={result.get('current_canonical_rows')} "
        f"status={result.get('status')}"
        + (f" error={result.get('error')}" if result.get("error") else "")
    )
    if dates:
        preview = ",".join(dates[:12])
        extra = "" if len(dates) <= 12 else f"...(+{len(dates) - 12})"
        print(f"  new_available_dates={preview}{extra}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Write canonical + targeted raw available_date UPDATE. Default is dry-run.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run (default). Refuse if combined with --execute.",
    )
    ap.add_argument(
        "--report-date",
        default="",
        help="Single period YYYYMMDD (e.g. 20260630). Default: every raw report_date.",
    )
    ap.add_argument(
        "--db",
        default="",
        help="Override org_holding database path (worktree has no gitignored copy).",
    )
    args = ap.parse_args(argv)

    if args.execute and args.dry_run:
        print("refuse: --execute and --dry-run both set", file=sys.stderr)
        return 2
    if not args.execute and not args.dry_run:
        # Default implied dry-run.
        dry_run = True
    else:
        dry_run = not args.execute
    if not dry_run and not args.execute:
        print("refuse: writes require --execute (dry-run is default)", file=sys.stderr)
        return 2

    from services.data_sources.org_holding_announcement import (  # noqa: E402
        iso_date,
        load_period_announcement_map,
    )
    from services.duck_adapter import connect as duck_connect  # noqa: E402
    from services.org_holding_aif10 import (  # noqa: E402
        list_raw_org_holding_report_dates,
        reaccept_org_holding_period_announced,
        refresh_org_holding_partition_pointers,
    )

    try:
        org_path = _resolve_db_file("org_holding", args.db or None)
        income_path = _resolve_db_file("tushare_raw")
        holders_path = _resolve_db_file("smartmoney")
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"mode={'dry-run' if dry_run else 'EXECUTE'} "
        f"org_holding={org_path} income={income_path} holders={holders_path}"
    )
    if not dry_run:
        print(
            "writes: canonical replace-by-report_date + raw available_date UPDATE; "
            "no provider fetch"
        )

    org = duck_connect(str(org_path), read_only=dry_run)
    income = duck_connect(str(income_path), read_only=True)
    holders = duck_connect(str(holders_path), read_only=True)
    results: list[dict] = []
    vacated: set[str] = set()
    try:
        if args.report_date:
            iso = iso_date(args.report_date)
            if not iso:
                print(
                    f"refuse: --report-date must be YYYYMMDD, got {args.report_date!r}",
                    file=sys.stderr,
                )
                return 2
            periods = [iso]
        else:
            periods = list_raw_org_holding_report_dates(org)
        if not periods:
            print("no raw report_dates found", file=sys.stderr)
            return 1

        for period in periods:
            amap = load_period_announcement_map(
                period, income_conn=income, holders_conn=holders
            )
            result = reaccept_org_holding_period_announced(
                org,
                period,
                announcement_by_stock=amap,
                dry_run=dry_run,
                refresh_pointers=False,
            )
            _print_period(result)
            results.append(result)
            for pv in result.get("vacated_available_dates") or []:
                vacated.add(pv)
            for pv in result.get("distinct_new_available_dates") or []:
                vacated.discard(pv)
            if result.get("status") == "accept_failed":
                print(json.dumps(result, ensure_ascii=False, default=str), file=sys.stderr)
                return 1

        if not dry_run and vacated:
            refreshed = refresh_org_holding_partition_pointers(org, sorted(vacated))
            print(f"pointer_refresh n={len(refreshed)} vacated={sorted(vacated)}")

        n_ok = sum(
            1
            for item in results
            if item.get("status")
            in {"dry_run", "accepted", "skipped_no_announced_grains"}
        )
        n_block = sum(
            1
            for item in results
            if item.get("status") == "blocked_empty_announcement_map"
        )
        print(
            f"summary periods={len(results)} ok={n_ok} "
            f"blocked_empty_map={n_block} mode={'dry-run' if dry_run else 'EXECUTE'}"
        )
        return 0 if n_block == 0 else 1
    finally:
        org.close()
        income.close()
        holders.close()


if __name__ == "__main__":
    raise SystemExit(main())
