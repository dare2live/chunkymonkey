"""每日增量抓取 F10 「股东研究」 → 新 canonical 表.

数据通道走 services.holders_resolver.HolderResolver, 按 source_tier
顺序 fallback:
  tier 1: tdxhub.holders.HolderFetcher (117 服务器池自动轮询)
  tier 2: miaoxiang aif10 RPT_F10_EH_FREEHOLDERS (备源, 仅 tier 1 失败时用)
  tier 3: akshare (兜底, 当前未启用; 见 holders_resolver.AkshareHolderSource)

每只股票:
  1. resolver.fetch(symbol) → 按 tier 顺序拿数据, 第一个返回非空的 source 中签
  2. 计算 raw_hash; 若 raw_tdx_f10_holder_research 已有相同 (stock, raw_hash) 跳过
  3. 否则写 raw (仅 tdxhub 路径有原文) + fact_top10_holder_period +
     fact_controlling_shareholder + fact_shareholder_plan + fact_shareholder_trade
     (后三表仅 tdxhub 路径填充; fallback 路径为空)
  4. 应用 dim_holder_alias 解析 holder_name_norm

使用:
    # 全量增量 (默认 4 worker)
    python backend/scripts/ingest_holders_tdxhub.py

    # 仅指定股票 (调试)
    python backend/scripts/ingest_holders_tdxhub.py --symbols 600519,000001

    # 限速 / 调小并发
    python backend/scripts/ingest_holders_tdxhub.py --workers 2

    # 关掉 fallback (仅 tdxhub, 不试 miaoxiang)
    python backend/scripts/ingest_holders_tdxhub.py --no-fallback

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
STOCK_ROOT = REPO.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(STOCK_ROOT / "tdxhub"))  # sibling editable checkout

from services.db import DB_PATH, init_db  # noqa: E402
from services.holders_resolver import (  # noqa: E402
    HolderResolver,
    TdxhubHolderSource,
    MiaoxiangHolderSource,
    ResolverResult,
)
from tdxhub.holders import _hash  # noqa: E402


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
              market: str, result: ResolverResult,
              alias_map: dict[str, str], lock: threading.Lock) -> dict:
    """Persist a ResolverResult into raw + 4 fact tables under a connection lock.

    raw_text + 段 1/2/3 仅 tdxhub 路径有 (result.source_tier=1).
    fallback 源 (miaoxiang/akshare) 只填 fact_top10_holder_period.
    """

    raw_hash = result.raw_hash
    fetched_at = result.fetched_at
    holders = result.holders_df.copy() if not result.holders_df.empty else result.holders_df
    periods = result.periods_df
    ctrl = result.controlling
    plans = (result.plans_df.copy() if result.plans_df is not None and not result.plans_df.empty else None)
    trades = (result.trades_df.copy() if result.trades_df is not None and not result.trades_df.empty else None)

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
        holders["source_tier"] = result.source_tier
        holders["holder_name_norm"] = holders["holder_name"].map(
            lambda n: alias_map.get(n, n)
        )
        holders["is_secondary_class"] = holders["is_secondary_class"].astype(bool)
        holders["is_exit_row"] = holders["is_exit_row"].astype(bool)

    if plans is not None and not plans.empty:
        plans["source_tier"] = result.source_tier
        plans["plan_seq"] = plans.groupby(["stock_code", "raw_hash"]).cumcount() + 1
    if trades is not None and not trades.empty:
        trades["source_tier"] = result.source_tier
        trades["holder_name_norm"] = trades["holder_name"].map(
            lambda n: alias_map.get(n, n)
        )
        trades["trade_seq"] = trades.groupby(["stock_code", "raw_hash"]).cumcount() + 1

    # Raw text 仅 tdxhub 路径有; fallback 不写 raw_tdx_f10_holder_research
    raw_row = None
    if result.raw_text and result.raw_hash:
        f10_format = "unknown"
        if "灵通V9.0" in result.raw_text:
            f10_format = "a_lingtong"
        elif "通达信沪深京F10" in result.raw_text:
            f10_format = "b_shsjz"
        elif "港澳资讯" in result.raw_text:
            f10_format = "a_other"
        raw_row = pd.DataFrame([{
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market": market,
            "fetched_at": fetched_at,
            "page_update_date": result.page_update_date,
            "raw_text": result.raw_text,
            "raw_hash": result.raw_hash,
            "bytes_len": len(result.raw_text),
            "server": result.server_or_endpoint,
            "f10_format": f10_format,
            "parser_version": "v1",
        }])

    out = {
        "stock_code": stock_code,
        "raw_hash": raw_hash,
        "source": result.source,
        "source_tier": result.source_tier,
        "n_holders": len(holders),
        "n_periods": len(periods),
        "n_plans": (len(plans) if plans is not None else 0),
        "n_trades": (len(trades) if trades is not None else 0),
        "has_controlling": ctrl is not None,
    }

    with lock:
        if raw_row is not None:
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

        if plans is not None and not plans.empty:
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

        if trades is not None and not trades.empty:
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


def _make_resolver(*, enable_fallback: bool) -> HolderResolver:
    sources = [TdxhubHolderSource(timeout=15, max_attempts_per_call=6)]
    if enable_fallback:
        sources.append(MiaoxiangHolderSource())
    return HolderResolver(sources)


def worker(name: str, job_q: queue.Queue, con: duckdb.DuckDBPyConnection,
           con_lock: threading.Lock, alias_map: dict[str, str],
           progress: dict, progress_lock: threading.Lock,
           seen_hashes: set, seen_lock: threading.Lock,
           *, enable_fallback: bool = True) -> None:
    resolver = _make_resolver(enable_fallback=enable_fallback)
    while True:
        item = job_q.get()
        if item is None:
            break
        idx, total, code, stock_name, market = item
        t0 = time.time()
        try:
            result = resolver.fetch(code, stock_name=stock_name)
            if result is None or not result.has_data():
                with progress_lock:
                    progress["skipped_no_f10"] += 1
                    progress["done"] += 1
                job_q.task_done()
                continue
            # 仅 tdxhub 路径有 raw_hash; fallback 路径没有, 不能跳过
            if result.raw_hash:
                with seen_lock:
                    if (code, result.raw_hash) in seen_hashes:
                        with progress_lock:
                            progress["skipped_unchanged"] += 1
                            progress["done"] += 1
                        job_q.task_done()
                        continue
                    seen_hashes.add((code, result.raw_hash))
            stats = write_one(
                con, stock_code=code, stock_name=stock_name, market=market,
                result=result, alias_map=alias_map, lock=con_lock,
            )
            elapsed = time.time() - t0
            with progress_lock:
                progress["ok"] += 1
                progress["done"] += 1
                progress.setdefault(f"src_{result.source}", 0)
                progress[f"src_{result.source}"] += 1
                if progress["done"] % 50 == 0:
                    rate = progress["done"] / max(time.time() - progress["t0"], 1e-3)
                    log.info(
                        "[%4d/%d] %s %s [%s/tier=%d] rows=%d periods=%d plans=%d trades=%d  %.1fs  rate=%.1f/s",
                        progress["done"], total, code, name,
                        stats["source"], stats["source_tier"],
                        stats["n_holders"], stats["n_periods"], stats["n_plans"],
                        stats["n_trades"], elapsed, rate,
                    )
        except Exception as e:
            with progress_lock:
                progress["err"] += 1
                progress["done"] += 1
                log.warning("[%s] %s ERROR %s: %s", name, code, type(e).__name__, e)
        job_q.task_done()
    resolver.close()


def run(
    *,
    workers: int = 4,
    limit: int = 0,
    symbols: str = "",
    force: bool = False,
    no_fallback: bool = False,
    con: duckdb.DuckDBPyConnection | None = None,
) -> dict:
    """实际执行入口. 可被 in-process 调用 (传 con 不开新连接).

    传 con: 用调用方的 connection (避免 DuckDB 跨进程锁冲突).
    不传 con: 自己 init_db + duckdb.connect (CLI 独立运行模式).
    """
    enable_fallback = not no_fallback

    own_con = con is None
    if own_con:
        init_db()
        con = duckdb.connect(str(DB_PATH))
    con_lock = threading.Lock()

    try:
        explicit = [s.strip() for s in symbols.split(",") if s.strip()] or None
        universe = load_universe(con, limit=limit, explicit=explicit)
        log.info("universe: %d stocks", len(universe))

        alias_map = load_alias_map(con)
        log.info("alias map: %d entries", len(alias_map))

        seen = set() if force else existing_hashes(con)
        seen_lock = threading.Lock()
        log.info("existing raw_hash cache: %d entries (force=%s)", len(seen), force)

        job_q: queue.Queue = queue.Queue()
        for i, (code, name, market) in enumerate(universe, 1):
            job_q.put((i, len(universe), code, name, market))
        for _ in range(workers):
            job_q.put(None)

        progress = {"done": 0, "ok": 0, "err": 0, "skipped_unchanged": 0,
                    "skipped_no_f10": 0, "t0": time.time()}
        progress_lock = threading.Lock()

        ths = []
        for i in range(workers):
            t = threading.Thread(
                target=worker,
                args=(f"w{i+1}", job_q, con, con_lock, alias_map,
                      progress, progress_lock, seen, seen_lock),
                kwargs={"enable_fallback": enable_fallback},
                name=f"holder-worker-{i+1}",
            )
            t.start()
            ths.append(t)
        for t in ths:
            t.join()

        elapsed = time.time() - progress["t0"]
        log.info("=== ingest complete in %.1fs ===", elapsed)
        log.info("  total      : %d", progress["done"])
        log.info("  ok         : %d", progress["ok"])
        log.info("  unchanged  : %d (raw_hash already in DB)", progress["skipped_unchanged"])
        log.info("  no F10     : %d", progress["skipped_no_f10"])
        log.info("  errors     : %d", progress["err"])
        progress["elapsed_s"] = elapsed
        return progress
    finally:
        if own_con:
            con.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--symbols", default="")
    p.add_argument("--force", action="store_true",
                   help="忽略 raw_hash 缓存, 强制重抓所有股票")
    p.add_argument("--no-fallback", action="store_true",
                   help="关闭 miaoxiang fallback (仅试 tdxhub)")
    args = p.parse_args()

    progress = run(
        workers=args.workers, limit=args.limit, symbols=args.symbols,
        force=args.force, no_fallback=args.no_fallback,
    )
    return 0 if progress["err"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
