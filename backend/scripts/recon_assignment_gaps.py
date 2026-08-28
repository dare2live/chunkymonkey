#!/usr/bin/env python3
"""Read-only assignment-gap recon: remaining measurable sibling rows.

Does not change primaries. Empty recon is not a match. Product mismatch
is recorded as measured, not as a failed numeric compare.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_access.resolver import db_path  # noqa: E402
from services.data_sources.assignment_gap_recon import (  # noqa: E402
    BLOCK_TRADE,
    DAILY_BASIC,
    DIM_TABLE,
    HOLDERNUMBER,
    INDEX_FACT,
    LIMIT_FACT,
    SHARE_FLOAT,
    SURVEY,
    TOP_INST_FACT,
    TOP_LIST,
    build_report,
    compare_holdernumber_sample,
    compare_index_closes,
    compare_sets,
    compare_valuation_snapshot,
    load_block_keys,
    load_codes_for_day,
    load_dim_active_ts_codes,
    load_holdernumber_sample,
    load_index_closes,
    load_latest_daily_basic,
    load_limit_up_codes,
    load_seat_keys,
    load_share_float_stock_days,
    load_survey_stock_days,
    miaoxiang_block_keys,
    miaoxiang_codes,
    miaoxiang_seat_keys,
    parse_fuyao_index_bars,
    parse_fuyao_lhb_codes,
    parse_fuyao_limit_pool,
    parse_fuyao_tickers,
    parse_miaoxiang_holdernumber,
    shanghai_midnight_ms,
)
from services.data_sources.fina_margin_recon import compact_yyyymmdd  # noqa: E402
from services.data_sources.fuyao_kline_recon import (  # noqa: E402
    compare_events,
    load_fuyao_events,
    load_fuyao_kline,
)
from services.data_sources.sibling_repos import ensure_import_path  # noqa: E402
from services.data_sources.sources.fuyao import (  # noqa: E402
    FuyaoRestError,
    resolve_api_key,
    rest_json,
)
from services.duck_adapter import connect  # noqa: E402

DEFAULT_CODES = ("600519.SH", "000001.SZ")
LHB_DAY = "20260825"
LIFT_DAY = "20260828"
SURVEY_DAY = "20260818"
INDEX_CODE = "000300.SH"


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"wrote {path}")


def _iso_day(compact: str) -> str:
    c = compact_yyyymmdd(compact) or compact
    return f"{c[:4]}-{c[4:6]}-{c[6:8]}"


def _classify(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, FuyaoRestError):
        kind = "http" if exc.http else "code"
        if exc.http == 404:
            kind = "http_404"
        return {"status": "unavailable", "class": kind, "error": str(exc)[:240]}
    return {"status": "unavailable", "class": type(exc).__name__, "error": str(exc)[:240]}


def _fetch_fuyao_tickers(api_key: str, *, timeout: float) -> dict[str, Any]:
    codes: list[str] = []
    offset = 0
    limit = 1000
    pages = 0
    while pages < 20:
        data = rest_json(
            "/api/meta/tickers/list",
            api_key=api_key,
            params={
                "exchange": "SH,SZ",
                "asset_type": "a-share",
                "limit": limit,
                "offset": offset,
            },
            timeout=timeout,
        )
        batch = parse_fuyao_tickers(data)
        codes.extend(batch)
        pages += 1
        n = len((data or {}).get("item") or [])
        if n < limit:
            break
        offset += limit
    return {"pages": pages, "codes": codes, "n": len(set(codes))}


def _miaoxiang_pages(report_name: str, *, extra_filters: list[str] | None = None,
                     secucode: str | None = None, page_size: int = 500,
                     max_pages: int = 8) -> dict[str, Any]:
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
            extra_filters=extra_filters,
            sort_columns=getattr(spec, "sort_columns", "") or "",
            sort_types=getattr(spec, "sort_types", "") or "",
        )
        rows.extend(first.get("data") or [])
        pages = int(first.get("pages") or 1)
        count = int(first.get("count") or 0)
        for page in range(2, min(pages, max_pages) + 1):
            more = client.get_v1(
                spec.name,
                page=page,
                page_size=page_size,
                secucode=secucode,
                extra_filters=extra_filters,
                sort_columns=getattr(spec, "sort_columns", "") or "",
                sort_types=getattr(spec, "sort_types", "") or "",
            )
            rows.extend(more.get("data") or [])
    finally:
        client.close()
    truncated = bool(count) and len(rows) < count
    return {
        "report": report_name,
        "n": len(rows),
        "count": count,
        "truncated": truncated,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "audit" / "historical" / "assignment_gap_recon.json",
    )
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES))
    parser.add_argument("--lhb-day", default=LHB_DAY)
    parser.add_argument("--lift-day", default=LIFT_DAY)
    parser.add_argument("--survey-day", default=SURVEY_DAY)
    parser.add_argument("--skip-fuyao", action="store_true")
    parser.add_argument("--skip-miaoxiang", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--k-parquet",
        type=Path,
        default=ROOT / "data" / "scratch" / "fuyao_dumps" / "daily-k-10d.parquet",
    )
    parser.add_argument(
        "--adj-parquet",
        type=Path,
        default=ROOT / "data" / "scratch" / "fuyao_dumps" / "adjustment-factors.parquet",
    )
    args = parser.parse_args(argv)
    codes = [p.strip().upper() for p in args.codes.split(",") if p.strip()]
    lhb_day = compact_yyyymmdd(args.lhb_day) or LHB_DAY
    lift_day = compact_yyyymmdd(args.lift_day) or LIFT_DAY
    survey_day = compact_yyyymmdd(args.survey_day) or SURVEY_DAY

    ref = connect(str(db_path("reference")), read_only=True)
    raw = connect(str(db_path("tushare_raw")), read_only=True)
    sm = connect(str(db_path("smartmoney")), read_only=True)

    dim_codes = load_dim_active_ts_codes(ref, DIM_TABLE)
    top_list = load_codes_for_day(raw, TOP_LIST, lhb_day, date_col="trade_date")
    seats = load_seat_keys(sm, lhb_day, TOP_INST_FACT)
    block_keys = load_block_keys(raw, lhb_day, BLOCK_TRADE)
    limit_u = load_limit_up_codes(sm, lhb_day, LIMIT_FACT)
    float_codes = load_share_float_stock_days(raw, lift_day, SHARE_FLOAT)
    survey_codes = load_survey_stock_days(raw, survey_day, SURVEY)
    basic = load_latest_daily_basic(raw, codes, DAILY_BASIC)
    idx = load_index_closes(sm, INDEX_CODE, table=INDEX_FACT, limit=8)
    holder_local = load_holdernumber_sample(raw, codes[0], HOLDERNUMBER)

    sections: dict[str, Any] = {
        "sample_codes": codes,
        "lhb_day": lhb_day,
        "lift_day": lift_day,
        "survey_day": survey_day,
        "dim_n": len(dim_codes),
    }

    adj_path = args.adj_parquet
    if adj_path.is_file():
        mem = connect(":memory:")
        escaped = str(Path(db_path("tushare_raw"))).replace("'", "''")
        mem.execute(f"ATTACH '{escaped}' AS tr (READ_ONLY)")
        load_fuyao_events(mem, adj_path)
        sections["adj_events"] = compare_events(
            mem,
            dividend_table="tr.raw_tushare_dividend",
            adj_factor_table="tr.raw_tushare_adj_factor",
        )
        mem.close()
        print(
            "adj matched_ex_date={matched_ex_date} only_fuyao={only_fuyao} "
            "jumps_without={jumps_without_fuyao_event}".format(**sections["adj_events"])
        )
    else:
        sections["adj_events"] = {"status": "missing_parquet", "identity": False}

    fuyao: dict[str, Any] = {}
    k_path = args.k_parquet
    if k_path.is_file():
        mem = connect(":memory:")
        load_fuyao_kline(mem, k_path)
        fy_codes = [
            r[0]
            for r in mem.execute("SELECT DISTINCT ts_code FROM fuyao_k").fetchall()
            if r and r[0]
        ]
        mem.close()
        fuyao["dump_window_codeset"] = compare_sets(
            dim_codes,
            fy_codes,
            grain="traded_in_dump_window_vs_listed",
            left_name="dim_active_a_stock",
            right_name="fuyao_daily_k_10d_distinct",
            same_product=False,
        )
        hs_dump = [c for c in fy_codes if str(c).endswith((".SH", ".SZ"))]
        fuyao["dump_hs_codeset"] = compare_sets(
            dim_codes,
            hs_dump,
            grain="listed_hs_a_in_dump_window",
            left_name="dim_active_a_stock",
            right_name="fuyao_daily_k_10d_hs",
            same_product=True,
        )
        print(
            "dump-window codeset jaccard={jaccard} only_dim={only_left} "
            "only_fuyao={only_right}".format(**fuyao["dump_window_codeset"])
        )
        print(
            "dump HS vs dim identity={identity} only_dim={only_left} "
            "only_fuyao={only_right}".format(**fuyao["dump_hs_codeset"])
        )

    api_key = None if args.skip_fuyao else resolve_api_key()
    if not args.skip_fuyao and not api_key:
        fuyao["rest"] = {"status": "missing_api_key"}
        print("WARN: no Fuyao API key; REST codeset/valuations/limit/index skipped", file=sys.stderr)
    elif api_key:
        try:
            tickers = _fetch_fuyao_tickers(api_key, timeout=args.timeout)
            fuyao["codeset"] = compare_sets(
                dim_codes,
                tickers["codes"],
                grain="listed_hs_a_snapshot",
                left_name="dim_active_a_stock",
                right_name="fuyao_meta_tickers_a_share",
                same_product=True,
            )
            fuyao["codeset"]["fuyao_pages"] = tickers["pages"]
            print(
                "codeset jaccard={jaccard} only_dim={only_left} only_fuyao={only_right} "
                "identity={identity}".format(**fuyao["codeset"])
            )
        except Exception as exc:  # noqa: BLE001
            fuyao["codeset"] = _classify(exc)
            print(f"codeset {fuyao['codeset']}", file=sys.stderr)
        try:
            snap = rest_json(
                "/api/a-share/valuations/snapshot",
                api_key=api_key,
                params={"thscodes": ",".join(codes)},
                timeout=args.timeout,
            )
            items = list((snap or {}).get("item") or [])
            fuyao["valuations"] = compare_valuation_snapshot(items, basic)
            print(
                "valuations match_rows={field_match_rows} identity={identity}".format(
                    **fuyao["valuations"]
                )
            )
        except Exception as exc:  # noqa: BLE001
            fuyao["valuations"] = _classify(exc)
        try:
            lhb = rest_json(
                "/api/a-share/special-data/dragon-tiger-list",
                api_key=api_key,
                params={"board_type": "all", "date": _iso_day(lhb_day)},
                timeout=args.timeout,
            )
            fy_lhb = parse_fuyao_lhb_codes(lhb)
            fuyao["lhb_vs_top_list"] = compare_sets(
                top_list,
                fy_lhb,
                grain="trade_date_x_ts_code",
                left_name="raw_tushare_top_list",
                right_name="fuyao_dragon_tiger_list",
                same_product=True,
            )
            print("lhb fuyao vs top_list identity={identity} jaccard={jaccard}".format(
                **fuyao["lhb_vs_top_list"]
            ))
        except Exception as exc:  # noqa: BLE001
            fuyao["lhb_vs_top_list"] = _classify(exc)
        try:
            day_ms = shanghai_midnight_ms(lhb_day)
            pool = rest_json(
                "/api/a-share/special-data/limit-up-pool",
                api_key=api_key,
                params={"date_ms": day_ms, "page": 1, "size": 200},
                timeout=args.timeout,
            )
            pages = int(((pool or {}).get("pagination") or {}).get("pages") or 1)
            fy_limit = parse_fuyao_limit_pool(pool)
            for page in range(2, min(pages, 8) + 1):
                more = rest_json(
                    "/api/a-share/special-data/limit-up-pool",
                    api_key=api_key,
                    params={"date_ms": day_ms, "page": page, "size": 200},
                    timeout=args.timeout,
                )
                fy_limit.extend(parse_fuyao_limit_pool(more))
            fuyao["limit_up"] = compare_sets(
                limit_u,
                fy_limit,
                grain="trade_date_x_ts_code_limit_up",
                left_name="fact_stock_limit_daily_U",
                right_name="fuyao_limit_up_pool",
                same_product=True,
            )
            print("limit-up identity={identity} jaccard={jaccard}".format(**fuyao["limit_up"]))
        except Exception as exc:  # noqa: BLE001
            fuyao["limit_up"] = _classify(exc)
        try:
            if idx:
                start = min(r["trade_date"] for r in idx)
                end = max(r["trade_date"] for r in idx)
                start_ms = shanghai_midnight_ms(start)
                end_ms = shanghai_midnight_ms(end) + 86_400_000
                hist = rest_json(
                    "/api/a-share-index/prices/historical",
                    api_key=api_key,
                    params={
                        "thscode": INDEX_CODE,
                        "interval": "1d",
                        "start": start_ms,
                        "end": end_ms,
                    },
                    timeout=args.timeout,
                )
                fy_bars = parse_fuyao_index_bars(hist, ts_code=INDEX_CODE)
                fuyao["index_close"] = compare_index_closes(idx, fy_bars)
                print(
                    "index {code} match={close_match} mismatch={close_mismatch} identity={identity}".format(
                        code=INDEX_CODE, **fuyao["index_close"]
                    )
                )
        except Exception as exc:  # noqa: BLE001
            fuyao["index_close"] = _classify(exc)
    elif args.skip_fuyao:
        fuyao["rest"] = {"status": "skipped"}
    sections["fuyao"] = fuyao

    if not args.skip_miaoxiang:
        mx: dict[str, Any] = {}
        iso_lhb = _iso_day(lhb_day)
        try:
            bill = _miaoxiang_pages(
                "RPT_DAILYBILLBOARD_DETAILSNEW",
                extra_filters=[f"(TRADE_DATE='{iso_lhb}')"],
            )
            mx["lhb_vs_top_list"] = compare_sets(
                top_list,
                miaoxiang_codes(bill["rows"]),
                grain="trade_date_x_ts_code",
                left_name="raw_tushare_top_list",
                right_name="RPT_DAILYBILLBOARD_DETAILSNEW",
                same_product=True,
            )
            mx["lhb_vs_top_list"]["miaoxiang_count"] = bill.get("count")
            mx["lhb_vs_top_list"]["truncated"] = bill.get("truncated")
            print("lhb miaoxiang vs top_list identity={identity} jaccard={jaccard}".format(
                **mx["lhb_vs_top_list"]
            ))
        except Exception as exc:  # noqa: BLE001
            mx["lhb_vs_top_list"] = {"status": "unavailable", "error": str(exc)[:240]}
        try:
            dept = _miaoxiang_pages(
                "RPT_OPERATEDEPT_TRADE",
                extra_filters=[f"(TRADE_DATE='{iso_lhb}')"],
                max_pages=4,
            )
            mx["lhb_seats"] = compare_sets(
                seats,
                miaoxiang_seat_keys(dept["rows"]),
                grain="trade_date_x_ts_code_x_seat_x_side",
                left_name="fact_top_inst_seat_daily",
                right_name="RPT_OPERATEDEPT_TRADE",
                same_product=True,
            )
            mx["lhb_seats"]["truncated"] = dept.get("truncated")
            mx["lhb_seats"]["miaoxiang_count"] = dept.get("count")
            print("lhb seats identity={identity} jaccard={jaccard} truncated={truncated}".format(
                **mx["lhb_seats"]
            ))
        except Exception as exc:  # noqa: BLE001
            mx["lhb_seats"] = {"status": "unavailable", "error": str(exc)[:240]}
        try:
            blk = _miaoxiang_pages(
                "RPT_DATA_BLOCKTRADE",
                extra_filters=[f"(TRADE_DATE='{iso_lhb}')"],
            )
            mx["block_trade"] = compare_sets(
                block_keys,
                miaoxiang_block_keys(blk["rows"]),
                grain="trade_date_x_ts_code_x_buyer_x_seller",
                left_name="raw_tushare_block_trade",
                right_name="RPT_DATA_BLOCKTRADE",
                same_product=True,
            )
            mx["block_trade"]["miaoxiang_count"] = blk.get("count")
            print("block identity={identity} jaccard={jaccard}".format(**mx["block_trade"]))
        except Exception as exc:  # noqa: BLE001
            mx["block_trade"] = {"status": "unavailable", "error": str(exc)[:240]}
        try:
            hn = _miaoxiang_pages("RPT_F10_EH_HOLDERNUM", secucode=codes[0], max_pages=1)
            mx["holdernumber"] = compare_holdernumber_sample(
                holder_local,
                parse_miaoxiang_holdernumber(hn["rows"], codes[0]),
            )
            print(
                "holdernumber exact={holder_num_exact} end={end_date_match} ann={ann_notice_match}".format(
                    **{k: mx["holdernumber"].get(k) for k in (
                        "holder_num_exact", "end_date_match", "ann_notice_match"
                    )}
                )
            )
        except Exception as exc:  # noqa: BLE001
            mx["holdernumber"] = {"status": "unavailable", "error": str(exc)[:240]}
        try:
            iso_lift = _iso_day(lift_day)
            lift = _miaoxiang_pages(
                "RPTA_APP_LIFTFUTURE",
                extra_filters=[f"(LIFT_DATE='{iso_lift}')"],
            )
            mx["share_float"] = compare_sets(
                float_codes,
                miaoxiang_codes(lift["rows"]),
                grain="float_date_x_ts_code",
                left_name="raw_tushare_share_float_holder_rows_collapsed",
                right_name="RPTA_APP_LIFTFUTURE",
                same_product=False,
            )
            mx["share_float"]["reason"] = (
                "TuShare is holder-level; 妙想 LIFTFUTURE is stock-day lift aggregate"
            )
            mx["share_float"]["miaoxiang_count"] = lift.get("count")
            print("lift stock-set jaccard={jaccard} identity={identity}".format(
                **mx["share_float"]
            ))
        except Exception as exc:  # noqa: BLE001
            mx["share_float"] = {"status": "unavailable", "error": str(exc)[:240]}
        try:
            iso_surv = _iso_day(survey_day)
            rec = _miaoxiang_pages(
                "RPT_ORG_SURVEYNEW",
                extra_filters=[f"(RECEIVE_START_DATE='{iso_surv}')"],
                max_pages=6,
            )
            notice = _miaoxiang_pages(
                "RPT_ORG_SURVEYNEW",
                extra_filters=[f"(NOTICE_DATE='{iso_surv}')"],
                max_pages=6,
            )
            mx["survey_receive_vs_surv_date"] = compare_sets(
                survey_codes,
                miaoxiang_codes(rec["rows"]),
                grain="visit_date_x_ts_code",
                left_name="raw_tushare_stk_surv.surv_date",
                right_name="RPT_ORG_SURVEYNEW.RECEIVE_START_DATE",
                same_product=False,
            )
            mx["survey_notice_vs_surv_date"] = compare_sets(
                survey_codes,
                miaoxiang_codes(notice["rows"]),
                grain="mixed_pit_axes",
                left_name="raw_tushare_stk_surv.surv_date",
                right_name="RPT_ORG_SURVEYNEW.NOTICE_DATE",
                same_product=False,
            )
            mx["survey_receive_vs_surv_date"]["miaoxiang_count"] = rec.get("count")
            mx["survey_notice_vs_surv_date"]["miaoxiang_count"] = notice.get("count")
            print(
                "survey receive jaccard={jaccard} identity={identity}".format(
                    **mx["survey_receive_vs_surv_date"]
                )
            )
        except Exception as exc:  # noqa: BLE001
            mx["survey"] = {"status": "unavailable", "error": str(exc)[:240]}
        sections["miaoxiang"] = mx
    else:
        sections["miaoxiang"] = {"status": "skipped"}

    report = build_report(sections)
    _write(args.out, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
