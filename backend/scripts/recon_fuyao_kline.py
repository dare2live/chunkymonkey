"""Read-only Fuyao dump vs accepted nominal daily K reconciliation CLI.

Does not change data_sources.yaml kline_daily.primary and does not write
accepted partitions. Default dump is daily-k-10d + adjustment-factors.
One dump kind 404 is recorded; other kinds are still signed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_access.resolver import db_path  # noqa: E402
from services.data_sources.fuyao_kline_recon import (  # noqa: E402
    ACCEPTED_K_TABLE,
    DUMP_KIND_VALUES,
    build_report,
    compare_events,
    compare_kline,
    dump_catalog_status,
    load_fuyao_events,
    load_fuyao_kline,
    probe_dump_kinds,
    write_report,
)
from services.data_sources.sources.fuyao import (  # noqa: E402
    dump_downloader,
    dump_kinds,
    resolve_api_key,
)
from services.duck_adapter import connect  # noqa: E402


def _sign_fn(downloader):
    kinds = dump_kinds()

    def sign(kind_value: str):
        kind = kinds(kind_value)
        return downloader._sign(kind)

    return sign


def _fetch_kind(downloader, kind_value: str, cache_dir: Path) -> Path:
    kinds = dump_kinds()
    dumped = downloader.fetch(kinds(kind_value))
    cached = cache_dir / f"{kind_value}.parquet"
    if dumped.path != cached and dumped.path.is_file():
        return Path(dumped.path)
    return cached


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=ROOT / "data" / "scratch" / "fuyao_dumps",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "audit" / "historical" / "fuyao_kline_recon.json",
    )
    parser.add_argument("--k-parquet", type=Path, default=None)
    parser.add_argument("--adj-parquet", type=Path, default=None)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also pull daily-k 10y dump (large). Default is daily-k-10d only.",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    cache_dir = args.cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    probes = []
    artifacts: dict[str, str] = {}
    k_path = args.k_parquet
    adj_path = args.adj_parquet

    if not args.skip_download:
        api_key = resolve_api_key()
        if not api_key:
            print(
                "FAIL: HITHINK_FINANCE_API_KEY / credentials.env missing",
                file=sys.stderr,
            )
            return 2
        downloader = dump_downloader(
            api_key=api_key,
            cache_dir=cache_dir,
            timeout=args.timeout,
            retries=1,
        )
        probes = probe_dump_kinds(_sign_fn(downloader))
        status = dump_catalog_status(probes)
        print(f"dump_catalog_status={status}")
        for probe in probes:
            print(f"  {probe.kind}: {probe.outcome} {probe.message[:80]}")
        prefer = "daily-k" if args.full else "daily-k-10d"
        fallback = "daily-k-10d" if args.full else "daily-k"
        by_kind = {p.kind: p for p in probes}
        chosen = None
        if by_kind.get(prefer) and by_kind[prefer].outcome == "ok":
            chosen = prefer
        elif by_kind.get(fallback) and by_kind[fallback].outcome == "ok":
            chosen = fallback
            print(f"WARN: {prefer} not ok; using {fallback}", file=sys.stderr)
        if chosen and k_path is None:
            print(f"fetch {chosen} ...")
            k_path = _fetch_kind(downloader, chosen, cache_dir)
            artifacts["k_kind"] = chosen
        if (
            adj_path is None
            and by_kind.get("adjustment-factors")
            and by_kind["adjustment-factors"].outcome == "ok"
        ):
            print("fetch adjustment-factors ...")
            adj_path = _fetch_kind(downloader, "adjustment-factors", cache_dir)
        if k_path is None and adj_path is None:
            print("FAIL: no dump kind signed successfully", file=sys.stderr)
            write_report(
                args.out,
                build_report(
                    probes=probes,
                    kline=None,
                    events=None,
                    artifacts={"reason": status},
                ),
            )
            return 3
    else:
        from services.data_sources.fuyao_kline_recon import DumpKindProbe

        probes = [
            DumpKindProbe(kind=k, outcome="skipped_local_parquet")
            for k in DUMP_KIND_VALUES
        ]

    if k_path is None:
        guess = cache_dir / "daily-k-10d.parquet"
        if guess.is_file():
            k_path = guess
        elif (cache_dir / "daily-k.parquet").is_file():
            k_path = cache_dir / "daily-k.parquet"
    if adj_path is None:
        guess = cache_dir / "adjustment-factors.parquet"
        if guess.is_file():
            adj_path = guess

    raw_path = Path(db_path("tushare_raw"))
    con = connect(":memory:")
    escaped = str(raw_path).replace("'", "''")
    con.execute(f"ATTACH '{escaped}' AS tr (READ_ONLY)")
    kline = None
    events = None
    if k_path is not None:
        print(f"compare kline {k_path}")
        load_fuyao_kline(con, k_path)
        kline = compare_kline(con, accepted_table=f"tr.{ACCEPTED_K_TABLE}")
        artifacts["k_parquet"] = str(k_path)
        print(
            "kline window={window} intersection={intersection} "
            "only_fuyao={only_fuyao} only_accepted={only_accepted} "
            "ohlc_mismatch={ohlc_mismatch} vol_h={vol_scale_hypothesis}".format(
                **kline
            )
        )
    if adj_path is not None:
        print(f"compare events {adj_path}")
        load_fuyao_events(con, adj_path)
        events = compare_events(
            con,
            dividend_table="tr.raw_tushare_dividend",
            adj_factor_table="tr.raw_tushare_adj_factor",
        )
        artifacts["adj_parquet"] = str(adj_path)
        print(
            "events matched={matched_ex_date} only_fuyao={only_fuyao} "
            "only_dividend={only_dividend} cash_div_tax_mismatch="
            "{cash_div_tax_mismatch} jumps_without_event="
            "{jumps_without_fuyao_event}".format(**events)
        )
    write_report(
        args.out,
        build_report(
            probes=probes,
            kline=kline,
            events=events,
            artifacts=artifacts,
        ),
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
