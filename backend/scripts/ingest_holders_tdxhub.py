"""每日增量抓取 tdxhub F10 「股东研究」 → 新 canonical 表.

替代旧的 miaoxiang RPT_F10_EH_FREEHOLDERS → market_raw_holdings 路径
(后者将在 P8 删除).

本脚本按 5200 只非北交 A 股遍历, 每只:
  1. fetch_text via tdxhub.holders.HolderFetcher (服务器池自动轮询)
  2. 计算 raw_hash; 若 raw_tdx_f10_holder_research 已有相同 (stock, raw_hash) 则跳过
  3. 否则 parse_research → 写 raw + fact_top10_holder_period +
     fact_controlling_shareholder + fact_shareholder_plan + fact_shareholder_trade
  4. 应用 dim_holder_alias 解析 holder_name_norm

使用:
    # 全量增量 (默认 4 worker)
    python backend/scripts/ingest_holders_tdxhub.py

    # 仅指定股票 (调试)
    python backend/scripts/ingest_holders_tdxhub.py --symbols 600519,000001

    # 限速 / 调小并发
    python backend/scripts/ingest_holders_tdxhub.py --workers 2

每日定时调度建议:
    crontab: 0 9 * * * cd /path && python backend/scripts/ingest_holders_tdxhub.py >> log 2>&1
"""

from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest-holders")

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, "/Users/dp/Documents/M/stock/tdxhub")  # editable install

from services.db import DB_PATH, init_db  # noqa: E402
from tdxhub.holders import HolderFetcher, parse_research, _hash  # noqa: E402


CHANGE_STATUS_TO_LEGACY = {
    "新进": "新进",
    "增持": "加仓",
    "减持": "减仓",
    "不变": "",
    "退出": "退出",
    "未知": "",
}


def load_universe(con: duckdb.DuckDBPyConnection, *, limit: int = 0,
                  explicit: list[str] | None = None) -> list[tuple[str, str, str]]:
    if explicit:
        return [(s, "", "") for s in explicit]
    rows = con.execute(
        """
        select stock_code, coalesce(stock_name,''), coalesce(market,'')
        from dim_active_a_stock
        where market in ('SH','SZ')
          and stock_code not like '83%'
          and stock_code not like '87%'
          and stock_code not like '88%'
          and stock_code not like '92%'
        order by stock_code
        """
    ).fetchall()
    if limit:
        rows = rows[:limit]
    return [(r[0], r[1], r[2]) for r in rows]


def existing_hashes(con: duckdb.DuckDBPyConnection) -> set[tuple[str, str]]:
    return set(
        con.execute(
            "select stock_code, raw_hash from raw_tdx_f10_holder_research"
        ).fetchall()
    )


def load_alias_map(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return dict(con.execute(
        "select alias, canonical_name from dim_holder_alias"
    ).fetchall())


def write_one(con: duckdb.DuckDBPyConnection, *, stock_code: str, stock_name: str,
              market: str, text: str, server: str | None,
              alias_map: dict[str, str], lock: threading.Lock) -> dict:
    """Parse text and write all 5 tables under a single connection-level lock."""

    raw_hash = _hash(text)
    fetched_at = datetime.utcnow().isoformat(timespec="seconds")
    res = parse_research(text, symbol=stock_code, stock_name=stock_name)
    holders = res["holders"].copy()
    periods = res["periods"]
    ctrl = res["controlling"]
    plans = res["plans"].copy()
    trades = res["trades"].copy()

    # back-compat columns
    if not holders.empty:
        holders["hold_amount"] = holders["shares_approx"].astype("float64")
        holders["hold_ratio_float"] = holders.apply(
            lambda r: r["hold_ratio"] if r["holder_set"] == "free" else None, axis=1
        )
        holders["hold_ratio_total"] = holders.apply(
            lambda r: r["hold_ratio"] if r["holder_set"] == "all" else None, axis=1
        )
        holders["hold_ratio_legacy"] = holders["hold_ratio"]
        holders["hold_change"] = holders["change_status"].map(
            lambda s: CHANGE_STATUS_TO_LEGACY.get(s or "", "")
        )
        holders["hold_change_num"] = holders["change_shares_approx"].astype("float64")
        holders["hold_market_cap"] = None
        holders["holder_type"] = holders["holder_type_or_nature"]
        holders["share_nature"] = holders["holder_type_or_nature"]
        holders["notice_date"] = None
        holders["effective_date"] = None
        holders["created_at"] = holders["fetched_at"]
        holders["source_tier"] = 1
        holders["holder_name_norm"] = holders["holder_name"].map(
            lambda n: alias_map.get(n, n)
        )
        holders["is_secondary_class"] = holders["is_secondary_class"].astype(bool)
        holders["is_exit_row"] = holders["is_exit_row"].astype(bool)

    if not plans.empty:
        plans["source_tier"] = 1
        plans["plan_seq"] = plans.groupby(["stock_code", "raw_hash"]).cumcount() + 1
    if not trades.empty:
        trades["source_tier"] = 1
        trades["holder_name_norm"] = trades["holder_name"].map(
            lambda n: alias_map.get(n, n)
        )
        trades["trade_seq"] = trades.groupby(["stock_code", "raw_hash"]).cumcount() + 1

    f10_format = "unknown"
    if "灵通V9.0" in text:
        f10_format = "a_lingtong"
    elif "通达信沪深京F10" in text:
        f10_format = "b_shsjz"
    elif "港澳资讯" in text:
        f10_format = "a_other"

    raw_row = pd.DataFrame([{
        "stock_code": stock_code,
        "stock_name": stock_name or (res["page"].get("stock_name") or ""),
        "market": market or res["page"].get("market") or "",
        "fetched_at": fetched_at,
        "page_update_date": res["page"].get("page_update_date"),
        "raw_text": text,
        "raw_hash": raw_hash,
        "bytes_len": len(text),
        "server": server,
        "f10_format": f10_format,
        "parser_version": "v1",
    }])

    out = {
        "stock_code": stock_code,
        "raw_hash": raw_hash,
        "n_holders": len(holders),
        "n_periods": len(periods),
        "n_plans": len(plans),
        "n_trades": len(trades),
        "has_controlling": ctrl is not None,
    }

    with lock:
        con.register("raw_in", raw_row)
        con.execute("""
            insert into raw_tdx_f10_holder_research(
              stock_code, stock_name, market, fetched_at, page_update_date,
              raw_text, raw_hash, bytes_len, server, f10_format, parser_version
            )
            select stock_code, stock_name, market,
                   cast(fetched_at as timestamp), page_update_date,
                   raw_text, raw_hash, bytes_len, server, f10_format, parser_version
            from raw_in
            where (stock_code, raw_hash) not in (
              select stock_code, raw_hash from raw_tdx_f10_holder_research
            )
        """)
        con.unregister("raw_in")

        if not holders.empty:
            con.register("h_in", holders)
            con.execute("""
                insert into fact_top10_holder_period(
                  stock_code, stock_name, market, report_date, holder_set,
                  holder_rank, row_seq, holder_name, holder_name_norm, share_class,
                  is_secondary_class, is_exit_row,
                  shares_text, shares_approx, shares_precision, hold_amount,
                  hold_ratio_float, hold_ratio_total, hold_ratio,
                  hold_market_cap, holder_type, share_nature,
                  change_status, change_shares_text, change_shares_approx,
                  hold_change, hold_change_num,
                  notice_date, effective_date, page_update_date,
                  source, source_tier, raw_hash, fetched_at, created_at
                )
                select stock_code, stock_name, market, report_date, holder_set,
                       holder_rank, row_seq, holder_name, holder_name_norm, share_class,
                       is_secondary_class, is_exit_row,
                       shares_text, shares_approx, shares_precision, hold_amount,
                       hold_ratio_float, hold_ratio_total, hold_ratio_legacy,
                       hold_market_cap, holder_type, share_nature,
                       change_status, change_shares_text, change_shares_approx,
                       hold_change, hold_change_num,
                       notice_date, effective_date, page_update_date,
                       source, source_tier, raw_hash, fetched_at, created_at
                from h_in
                where (stock_code, report_date, holder_set, source, is_exit_row,
                       holder_rank, row_seq, share_class)
                  not in (
                    select stock_code, report_date, holder_set, source, is_exit_row,
                           holder_rank, row_seq, share_class
                    from fact_top10_holder_period
                )
            """)
            con.unregister("h_in")

        if ctrl is not None:
            ctrl_row = pd.DataFrame([{
                "stock_code": ctrl["stock_code"],
                "stock_name": ctrl.get("stock_name") or "",
                "market": ctrl.get("market") or "",
                "primary_label": ctrl["primary_shareholder_label"],
                "primary_name": ctrl["primary_shareholder_name"],
                "primary_ratio": ctrl["primary_shareholder_ratio"],
                "primary_raw": ctrl["primary_shareholder_raw"],
                "actual_name": ctrl.get("actual_controller_name"),
                "actual_ratio": ctrl.get("actual_controller_ratio"),
                "actual_raw": ctrl.get("actual_controller_raw"),
                "page_update_date": ctrl.get("page_update_date"),
                "source": ctrl.get("source", "tdx_f10"),
                "source_tier": 1,
                "raw_hash": ctrl.get("raw_hash"),
                "fetched_at": ctrl.get("fetched_at"),
            }])
            con.register("ctrl_in", ctrl_row)
            con.execute("""
                insert into fact_controlling_shareholder(
                  stock_code, stock_name, market,
                  primary_label, primary_name, primary_ratio, primary_raw,
                  actual_name, actual_ratio, actual_raw,
                  page_update_date, source, source_tier, raw_hash, fetched_at
                )
                select stock_code, stock_name, market,
                       primary_label, primary_name, primary_ratio, primary_raw,
                       actual_name, actual_ratio, actual_raw,
                       page_update_date, source, source_tier, raw_hash, fetched_at
                from ctrl_in
                where (stock_code, source) not in (
                  select stock_code, source from fact_controlling_shareholder
                )
            """)
            con.unregister("ctrl_in")

        if not plans.empty:
            con.register("p_in", plans)
            con.execute("""
                insert into fact_shareholder_plan(
                  stock_code, stock_name, market,
                  announce_date, subject, direction, progress,
                  start_date, end_date,
                  target_shares_text, target_shares,
                  target_ratio_text, target_ratio,
                  reason, narrative,
                  page_update_date, source, source_tier, raw_hash, fetched_at, plan_seq
                )
                select stock_code, stock_name, market,
                       announce_date, subject, direction, progress,
                       start_date, end_date,
                       target_shares_text, target_shares,
                       target_ratio_text, target_ratio,
                       reason, narrative,
                       page_update_date, source, source_tier, raw_hash, fetched_at, plan_seq
                from p_in
                where (stock_code, raw_hash) not in (
                  select distinct stock_code, raw_hash from fact_shareholder_plan
                  where raw_hash is not null
                )
            """)
            con.unregister("p_in")

        if not trades.empty:
            con.register("t_in", trades)
            con.execute("""
                insert into fact_shareholder_trade(
                  stock_code, stock_name, market,
                  change_date, holder_name, holder_name_norm,
                  shares_before_text, shares_before,
                  shares_change_text, shares_change,
                  shares_after_text, shares_after,
                  ratio_after, change_type,
                  page_update_date, source, source_tier, raw_hash, fetched_at, trade_seq
                )
                select stock_code, stock_name, market,
                       change_date, holder_name, holder_name_norm,
                       shares_before_text, shares_before,
                       shares_change_text, shares_change,
                       shares_after_text, shares_after,
                       ratio_after, change_type,
                       page_update_date, source, source_tier, raw_hash, fetched_at, trade_seq
                from t_in
                where (stock_code, raw_hash) not in (
                  select distinct stock_code, raw_hash from fact_shareholder_trade
                  where raw_hash is not null
                )
            """)
            con.unregister("t_in")

    return out


def worker(name: str, job_q: queue.Queue, con: duckdb.DuckDBPyConnection,
           con_lock: threading.Lock, alias_map: dict[str, str],
           progress: dict, progress_lock: threading.Lock,
           seen_hashes: set, seen_lock: threading.Lock) -> None:
    fetcher = HolderFetcher(timeout=15, max_attempts_per_call=6)
    while True:
        item = job_q.get()
        if item is None:
            break
        idx, total, code, stock_name, market = item
        t0 = time.time()
        try:
            text = fetcher.fetch_text(code)
            if not text:
                with progress_lock:
                    progress["skipped_no_f10"] += 1
                    progress["done"] += 1
                job_q.task_done()
                continue
            raw_hash = _hash(text)
            with seen_lock:
                if (code, raw_hash) in seen_hashes:
                    with progress_lock:
                        progress["skipped_unchanged"] += 1
                        progress["done"] += 1
                    job_q.task_done()
                    continue
                seen_hashes.add((code, raw_hash))
            server = str(fetcher.stats().get("active_server"))
            stats = write_one(
                con, stock_code=code, stock_name=stock_name, market=market,
                text=text, server=server, alias_map=alias_map, lock=con_lock,
            )
            elapsed = time.time() - t0
            with progress_lock:
                progress["ok"] += 1
                progress["done"] += 1
                if progress["done"] % 50 == 0:
                    rate = progress["done"] / max(time.time() - progress["t0"], 1e-3)
                    log.info(
                        "[%4d/%d] %s %s rows=%d periods=%d plans=%d trades=%d  %.1fs  rate=%.1f/s  server=%s",
                        progress["done"], total, code, name,
                        stats["n_holders"], stats["n_periods"], stats["n_plans"],
                        stats["n_trades"], elapsed, rate, server,
                    )
        except Exception as e:
            with progress_lock:
                progress["err"] += 1
                progress["done"] += 1
                log.warning("[%s] %s ERROR %s: %s", name, code, type(e).__name__, e)
        job_q.task_done()
    fetcher.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--symbols", default="")
    p.add_argument("--force", action="store_true",
                   help="忽略 raw_hash 缓存, 强制重抓所有股票")
    args = p.parse_args()

    init_db()
    con = duckdb.connect(str(DB_PATH))
    con_lock = threading.Lock()

    explicit = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    universe = load_universe(con, limit=args.limit, explicit_symbols=explicit) \
        if False else load_universe(con, limit=args.limit, explicit=explicit)
    log.info("universe: %d stocks", len(universe))

    alias_map = load_alias_map(con)
    log.info("alias map: %d entries", len(alias_map))

    seen = set() if args.force else existing_hashes(con)
    seen_lock = threading.Lock()
    log.info("existing raw_hash cache: %d entries (force=%s)", len(seen), args.force)

    job_q: queue.Queue = queue.Queue()
    for i, (code, name, market) in enumerate(universe, 1):
        job_q.put((i, len(universe), code, name, market))
    for _ in range(args.workers):
        job_q.put(None)

    progress = {"done": 0, "ok": 0, "err": 0, "skipped_unchanged": 0,
                "skipped_no_f10": 0, "t0": time.time()}
    progress_lock = threading.Lock()

    workers = []
    for i in range(args.workers):
        t = threading.Thread(
            target=worker,
            args=(f"w{i+1}", job_q, con, con_lock, alias_map,
                  progress, progress_lock, seen, seen_lock),
            name=f"holder-worker-{i+1}",
        )
        t.start()
        workers.append(t)
    for t in workers:
        t.join()

    elapsed = time.time() - progress["t0"]
    log.info("=== ingest complete in %.1fs ===", elapsed)
    log.info("  total      : %d", progress["done"])
    log.info("  ok         : %d", progress["ok"])
    log.info("  unchanged  : %d (raw_hash already in DB)", progress["skipped_unchanged"])
    log.info("  no F10     : %d", progress["skipped_no_f10"])
    log.info("  errors     : %d", progress["err"])
    con.close()
    return 0 if progress["err"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
