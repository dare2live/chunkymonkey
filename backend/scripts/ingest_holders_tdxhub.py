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
from typing import Any, Callable

import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest-holders")

REPO = Path(__file__).resolve().parent.parent.parent
STOCK_ROOT = REPO.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(STOCK_ROOT / "tdxhub"))  # sibling editable checkout

from services.db import get_conn, init_db  # noqa: E402
from services.holders_resolver import (  # noqa: E402
    HolderResolver,
    TdxhubHolderSource,
    MiaoxiangHolderSource,
    ResolverResult,
)
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402


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
    rows = con.execute(
        "select stock_code, raw_hash from raw_tdx_f10_holder_research"
    ).fetchall()
    return {(row[0], row[1]) for row in rows}


def load_alias_map(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return dict(con.execute(
        "select alias, canonical_name from dim_holder_alias"
    ).fetchall())


def _copy_rows(rows: list[dict] | None) -> list[dict]:
    return [dict(row) for row in (rows or [])]


def _safe_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)


def _seq_rows(rows: list[dict], key_fields: tuple[str, ...], seq_field: str) -> list[dict]:
    counters: dict[tuple, int] = {}
    out = []
    for row in rows:
        item = dict(row)
        key = tuple(item.get(field) for field in key_fields)
        counters[key] = counters.get(key, 0) + 1
        item[seq_field] = counters[key]
        out.append(item)
    return out


def _prepare_holders(
    result: ResolverResult,
    *,
    stock_code: str,
    stock_name: str,
    market: str,
    alias_map: dict[str, str],
) -> list[dict]:
    holders = _copy_rows(result.holders)
    for row in holders:
        row["stock_code"] = row.get("stock_code") or stock_code
        row["stock_name"] = row.get("stock_name") or stock_name
        row["market"] = row.get("market") or market
        row["row_seq"] = row.get("row_seq") or 1
        row["holder_name_norm"] = alias_map.get(row.get("holder_name"), row.get("holder_name"))
        row["share_class"] = row.get("share_class")
        row["is_secondary_class"] = _safe_bool(row.get("is_secondary_class"))
        row["is_exit_row"] = _safe_bool(row.get("is_exit_row"))
        row["hold_amount"] = _safe_float(row.get("shares_approx"))

        holder_set = row.get("holder_set")
        hold_ratio = _safe_float(row.get("hold_ratio"))
        row["hold_ratio_float"] = (
            _safe_float(row.get("hold_ratio_float"))
            if row.get("hold_ratio_float") is not None
            else hold_ratio if holder_set == "free" else None
        )
        row["hold_ratio_total"] = (
            _safe_float(row.get("hold_ratio_total"))
            if row.get("hold_ratio_total") is not None
            else hold_ratio if holder_set == "all" else None
        )
        row["hold_ratio_legacy"] = hold_ratio
        row["hold_change"] = CHANGE_STATUS_TO_LEGACY.get(row.get("change_status") or "", "")
        row["hold_change_num"] = _safe_float(row.get("change_shares_approx"))
        row["hold_market_cap"] = row.get("hold_market_cap")
        holder_type = row.get("holder_type_or_nature") or row.get("holder_type")
        row["holder_type"] = row.get("holder_type") or holder_type
        row["share_nature"] = row.get("share_nature") or holder_type
        row["notice_date"] = row.get("notice_date")
        row["effective_date"] = row.get("effective_date")
        row["created_at"] = row.get("created_at") or row.get("fetched_at") or result.fetched_at
        row["source"] = row.get("source") or result.source
        row["source_tier"] = row.get("source_tier") or result.source_tier
        row["raw_hash"] = row.get("raw_hash") or result.raw_hash
        row["fetched_at"] = row.get("fetched_at") or result.fetched_at
        row["page_update_date"] = row.get("page_update_date") or result.page_update_date
    return holders


def _holder_tuple(row: dict) -> tuple:
    return (
        row.get("stock_code"),
        row.get("stock_name"),
        row.get("market"),
        row.get("report_date"),
        row.get("holder_set"),
        row.get("holder_rank"),
        row.get("row_seq"),
        row.get("holder_name"),
        row.get("holder_name_norm"),
        row.get("share_class"),
        row.get("is_secondary_class"),
        row.get("is_exit_row"),
        row.get("shares_text"),
        row.get("shares_approx"),
        row.get("shares_precision"),
        row.get("hold_amount"),
        row.get("hold_ratio_float"),
        row.get("hold_ratio_total"),
        row.get("hold_ratio_legacy"),
        row.get("hold_market_cap"),
        row.get("holder_type"),
        row.get("share_nature"),
        row.get("change_status"),
        row.get("change_shares_text"),
        row.get("change_shares_approx"),
        row.get("hold_change"),
        row.get("hold_change_num"),
        row.get("notice_date"),
        row.get("effective_date"),
        row.get("page_update_date"),
        row.get("source"),
        row.get("source_tier"),
        row.get("raw_hash"),
        row.get("fetched_at"),
        row.get("created_at"),
    )


def _delete_existing_fact_rows(
    con: duckdb.DuckDBPyConnection,
    *,
    stock_code: str,
    source: str,
    raw_hash: str | None,
) -> None:
    """Remove canonical rows for a raw payload before replaying parser output."""

    if not raw_hash:
        return
    con.execute(
        "DELETE FROM fact_top10_holder_period WHERE stock_code = ? AND source = ? AND raw_hash = ?",
        (stock_code, source, raw_hash),
    )
    con.execute(
        "DELETE FROM fact_shareholder_plan WHERE stock_code = ? AND source = ? AND raw_hash = ?",
        (stock_code, source, raw_hash),
    )
    con.execute(
        "DELETE FROM fact_shareholder_trade WHERE stock_code = ? AND source = ? AND raw_hash = ?",
        (stock_code, source, raw_hash),
    )
    con.execute(
        "DELETE FROM fact_controlling_shareholder WHERE stock_code = ? AND source = ? AND raw_hash = ?",
        (stock_code, source, raw_hash),
    )


def write_one(con: duckdb.DuckDBPyConnection, *, stock_code: str, stock_name: str,
              market: str, result: ResolverResult,
              alias_map: dict[str, str], lock: threading.Lock,
              replace_facts: bool = False) -> dict:
    """Persist a ResolverResult into raw + 4 fact tables under a connection lock.

    raw_text + 段 1/2/3 仅 tdxhub 路径有 (result.source_tier=1).
    fallback 源 (miaoxiang/akshare) 只填 fact_top10_holder_period.
    """

    raw_hash = result.raw_hash
    fetched_at = result.fetched_at
    holders = _prepare_holders(
        result,
        stock_code=stock_code,
        stock_name=stock_name,
        market=market,
        alias_map=alias_map,
    )
    periods = result.periods
    ctrl = result.controlling
    plans = _seq_rows(_copy_rows(result.plans), ("stock_code", "raw_hash"), "plan_seq")
    trades = _seq_rows(_copy_rows(result.trades), ("stock_code", "raw_hash"), "trade_seq")
    for row in plans:
        row["stock_code"] = row.get("stock_code") or stock_code
        row["stock_name"] = row.get("stock_name") or stock_name
        row["market"] = row.get("market") or market
        row["source"] = row.get("source") or result.source
        row["source_tier"] = result.source_tier
        row["raw_hash"] = row.get("raw_hash") or result.raw_hash
        row["fetched_at"] = row.get("fetched_at") or result.fetched_at
    for row in trades:
        row["stock_code"] = row.get("stock_code") or stock_code
        row["stock_name"] = row.get("stock_name") or stock_name
        row["market"] = row.get("market") or market
        row["source"] = row.get("source") or result.source
        row["source_tier"] = result.source_tier
        row["raw_hash"] = row.get("raw_hash") or result.raw_hash
        row["fetched_at"] = row.get("fetched_at") or result.fetched_at
        row["holder_name_norm"] = alias_map.get(row.get("holder_name"), row.get("holder_name"))

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
        raw_row = {
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
        }

    out = {
        "stock_code": stock_code,
        "raw_hash": raw_hash,
        "source": result.source,
        "source_tier": result.source_tier,
        "n_holders": len(holders),
        "n_periods": len(periods),
        "n_plans": len(plans),
        "n_trades": len(trades),
        "has_controlling": ctrl is not None,
    }

    with lock:
        if replace_facts:
            _delete_existing_fact_rows(
                con,
                stock_code=stock_code,
                source=result.source,
                raw_hash=raw_hash,
            )

        if raw_row is not None:
            con.execute("""
                INSERT OR IGNORE INTO raw_tdx_f10_holder_research(
                  stock_code, stock_name, market, fetched_at, page_update_date,
                  raw_text, raw_hash, bytes_len, server, f10_format, parser_version
                ) VALUES (?, ?, ?, cast(? as timestamp), ?, ?, ?, ?, ?, ?, ?)
            """, (
                raw_row["stock_code"], raw_row["stock_name"], raw_row["market"],
                raw_row["fetched_at"], raw_row["page_update_date"], raw_row["raw_text"],
                raw_row["raw_hash"], raw_row["bytes_len"], raw_row["server"],
                raw_row["f10_format"], raw_row["parser_version"],
            ))

        if holders:
            con.executemany("""
                INSERT OR REPLACE INTO fact_top10_holder_period(
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
                ) VALUES (
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, [_holder_tuple(row) for row in holders])

        if ctrl is not None:
            con.execute("""
                INSERT OR REPLACE INTO fact_controlling_shareholder(
                  stock_code, stock_name, market,
                  primary_label, primary_name, primary_ratio, primary_raw,
                  actual_name, actual_ratio, actual_raw,
                  page_update_date, source, source_tier, raw_hash, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ctrl["stock_code"], ctrl.get("stock_name") or "", ctrl.get("market") or "",
                ctrl["primary_shareholder_label"], ctrl["primary_shareholder_name"],
                ctrl["primary_shareholder_ratio"], ctrl["primary_shareholder_raw"],
                ctrl.get("actual_controller_name"), ctrl.get("actual_controller_ratio"),
                ctrl.get("actual_controller_raw"), ctrl.get("page_update_date"),
                ctrl.get("source", "tdx_f10"), 1, ctrl.get("raw_hash"),
                ctrl.get("fetched_at"),
            ))

        if plans and not (
            raw_hash and con.execute(
                "SELECT 1 FROM fact_shareholder_plan WHERE stock_code = ? AND raw_hash = ? LIMIT 1",
                (stock_code, raw_hash),
            ).fetchone()
        ):
            con.executemany("""
                INSERT INTO fact_shareholder_plan(
                  stock_code, stock_name, market,
                  announce_date, subject, direction, progress,
                  start_date, end_date,
                  target_shares_text, target_shares,
                  target_ratio_text, target_ratio,
                  reason, narrative,
                  page_update_date, source, source_tier, raw_hash, fetched_at, plan_seq
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    row.get("stock_code"), row.get("stock_name"), row.get("market"),
                    row.get("announce_date"), row.get("subject"), row.get("direction"),
                    row.get("progress"), row.get("start_date"), row.get("end_date"),
                    row.get("target_shares_text"), row.get("target_shares"),
                    row.get("target_ratio_text"), row.get("target_ratio"),
                    row.get("reason"), row.get("narrative"), row.get("page_update_date"),
                    row.get("source"), row.get("source_tier"), row.get("raw_hash"),
                    row.get("fetched_at"), row.get("plan_seq"),
                )
                for row in plans
            ])

        if trades and not (
            raw_hash and con.execute(
                "SELECT 1 FROM fact_shareholder_trade WHERE stock_code = ? AND raw_hash = ? LIMIT 1",
                (stock_code, raw_hash),
            ).fetchone()
        ):
            con.executemany("""
                INSERT INTO fact_shareholder_trade(
                  stock_code, stock_name, market,
                  change_date, holder_name, holder_name_norm,
                  shares_before_text, shares_before,
                  shares_change_text, shares_change,
                  shares_after_text, shares_after,
                  ratio_after, change_type,
                  page_update_date, source, source_tier, raw_hash, fetched_at, trade_seq
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    row.get("stock_code"), row.get("stock_name"), row.get("market"),
                    row.get("change_date"), row.get("holder_name"), row.get("holder_name_norm"),
                    row.get("shares_before_text"), row.get("shares_before"),
                    row.get("shares_change_text"), row.get("shares_change"),
                    row.get("shares_after_text"), row.get("shares_after"),
                    row.get("ratio_after"), row.get("change_type"), row.get("page_update_date"),
                    row.get("source"), row.get("source_tier"), row.get("raw_hash"),
                    row.get("fetched_at"), row.get("trade_seq"),
                )
                for row in trades
            ])

    return out


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        return default


def parse_tdx_raw_row(
    row: Any,
    *,
    parser: Callable[..., dict] | None = None,
    hasher: Callable[[str], str] | None = None,
) -> ResolverResult:
    """Parse one raw_tdx_f10_holder_research row into canonical resolver output."""

    if parser is None or hasher is None:
        from tdxhub.holders import parse_research_records, _hash  # noqa: WPS433
        parser = parser or parse_research_records
        hasher = hasher or _hash

    raw_text = _row_value(row, "raw_text", 3) or ""
    stock_code = _row_value(row, "stock_code", 0) or ""
    stock_name = _row_value(row, "stock_name", 1) or ""
    parsed = parser(raw_text, symbol=stock_code, stock_name=stock_name)
    page = parsed.get("page") or {}
    raw_hash = _row_value(row, "raw_hash", 4) or hasher(raw_text)
    fetched_at = _row_value(row, "fetched_at", 5)
    if hasattr(fetched_at, "isoformat"):
        fetched_at = fetched_at.isoformat(timespec="seconds")
    return ResolverResult(
        holders=[dict(item) for item in (parsed.get("holders") or [])],
        periods=[dict(item) for item in (parsed.get("periods") or [])],
        raw_text=raw_text,
        raw_hash=raw_hash,
        page_update_date=str(
            _row_value(row, "page_update_date", 6)
            or page.get("page_update_date")
            or ""
        ) or None,
        server_or_endpoint=_row_value(row, "server", 7),
        source="tdx_f10",
        source_tier=1,
        fetched_at=str(fetched_at or datetime.utcnow().isoformat(timespec="seconds")),
        controlling=parsed.get("controlling"),
        plans=[dict(item) for item in (parsed.get("plans") or [])],
        trades=[dict(item) for item in (parsed.get("trades") or [])],
    )


def _raw_replay_rows(
    con: duckdb.DuckDBPyConnection,
    *,
    symbols: str = "",
    raw_hash: str = "",
    limit: int = 0,
) -> list:
    clauses = []
    params: list[Any] = []
    explicit = [item.strip() for item in symbols.split(",") if item.strip()]
    if explicit:
        clauses.append(f"stock_code IN ({','.join('?' for _ in explicit)})")
        params.extend(explicit)
    if raw_hash:
        clauses.append("raw_hash = ?")
        params.append(raw_hash)
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    limit_sql = "LIMIT ?" if limit else ""
    if limit:
        params.append(limit)
    return con.execute(
        f"""
        SELECT stock_code, stock_name, market, raw_text, raw_hash,
               fetched_at, page_update_date, server
          FROM raw_tdx_f10_holder_research
          {where_sql}
         ORDER BY fetched_at DESC, stock_code
         {limit_sql}
        """,
        params,
    ).fetchall()


def parse_raw_records(
    con: duckdb.DuckDBPyConnection,
    *,
    symbols: str = "",
    raw_hash: str = "",
    limit: int = 0,
    replace_facts: bool = False,
    parser: Callable[..., dict] | None = None,
    hasher: Callable[[str], str] | None = None,
) -> dict:
    """Replay stored TDX raw text into canonical holder fact tables."""

    t0 = time.time()
    rows = _raw_replay_rows(con, symbols=symbols, raw_hash=raw_hash, limit=limit)
    alias_map = load_alias_map(con)
    lock = threading.Lock()
    stats = {
        "raw_rows": len(rows),
        "parsed": 0,
        "no_data": 0,
        "errors": 0,
        "replace_facts": replace_facts,
    }
    for row in rows:
        stock_code = _row_value(row, "stock_code", 0) or ""
        try:
            result = parse_tdx_raw_row(row, parser=parser, hasher=hasher)
            if not result.has_data():
                stats["no_data"] += 1
                continue
            write_one(
                con,
                stock_code=stock_code,
                stock_name=_row_value(row, "stock_name", 1) or "",
                market=_row_value(row, "market", 2) or "",
                result=result,
                alias_map=alias_map,
                lock=lock,
                replace_facts=replace_facts,
            )
            stats["parsed"] += 1
        except Exception as exc:
            stats["errors"] += 1
            log.warning("[parse-raw] %s ERROR %s: %s", stock_code, type(exc).__name__, exc)
    con.commit()
    stats["elapsed_s"] = time.time() - t0
    return stats


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
        con = get_conn()
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
    p.add_argument("--parse-raw-only", action="store_true",
                   help="不联网抓取, 只重放 raw_tdx_f10_holder_research 到 canonical fact 表")
    p.add_argument("--raw-hash", default="",
                   help="配合 --parse-raw-only, 只重放指定 raw_hash")
    p.add_argument("--replace-facts", action="store_true",
                   help="配合 --parse-raw-only, 先删除同 stock/source/raw_hash 的旧 canonical 行再写入")
    args = p.parse_args()

    if args.parse_raw_only:
        started_at = utc_now_iso()
        init_db()
        con = get_conn()
        try:
            stats = parse_raw_records(
                con,
                symbols=args.symbols,
                raw_hash=args.raw_hash,
                limit=args.limit,
                replace_facts=args.replace_facts,
            )
            record_pipeline_run(
                con,
                run_id=f"parse_holders_raw_{started_at.replace(':', '').replace('-', '')[:15]}",
                pipeline_name="parse_holders_raw",
                status="success" if stats["errors"] == 0 else "failed",
                started_at=started_at,
                ended_at=utc_now_iso(),
                duration_s=stats["elapsed_s"],
                commit_sha=git_commit_sha(REPO),
                input_tables=["raw_tdx_f10_holder_research", "dim_holder_alias"],
                output_tables=[
                    "fact_top10_holder_period",
                    "fact_controlling_shareholder",
                    "fact_shareholder_plan",
                    "fact_shareholder_trade",
                ],
                blockers=[] if stats["errors"] == 0 else ["parse_errors"],
                perf_summary=stats,
            )
            log.info("parse raw complete: %s", stats)
            return 0 if stats["errors"] == 0 else 1
        finally:
            con.close()

    progress = run(
        workers=args.workers, limit=args.limit, symbols=args.symbols,
        force=args.force, no_fallback=args.no_fallback,
    )
    return 0 if progress["err"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
