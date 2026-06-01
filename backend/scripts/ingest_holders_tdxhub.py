"""每日增量抓取 F10 「股东研究」 → raw 表 → canonical 表.

数据通道走 services.holders_resolver.HolderResolver, 按 source_tier
顺序 fallback:
  tier 1: tdxhub.holders.HolderFetcher (117 服务器池自动轮询)
  tier 2: miaoxiang aif10 RPT_F10_EH_FREEHOLDERS (备源, 仅 tier 1 失败时用)
  tier 3: akshare (兜底, 当前未启用; 见 holders_resolver.AkshareHolderSource)

默认链路分两段:
  1. fetch raw: 只抓 tdxhub F10 原文并写 raw_tdx_f10_holder_research.
  2. parse canonical: 只重放新增/变化 raw_hash 到 fact_top10_holder_period +
     fact_controlling_shareholder + fact_shareholder_plan + fact_shareholder_trade.

fallback 源不写 tdxhub raw 表; 只有当 tdxhub 抓取明确失败时才尝试
miaoxiang canonical fallback, 并通过 source/source_tier 留 lineage。

使用:
    # 全量增量 (默认 4 worker)
    python backend/scripts/ingest_holders_tdxhub.py

    # 仅指定股票 (调试)
    python backend/scripts/ingest_holders_tdxhub.py --symbols 600519,000001

    # 限速 / 调小并发
    python backend/scripts/ingest_holders_tdxhub.py --workers 2

    # 关掉 fallback (仅 tdxhub, 不试 miaoxiang)
    python backend/scripts/ingest_holders_tdxhub.py --no-fallback

    # 只联网抓 raw, 不更新 canonical
    python backend/scripts/ingest_holders_tdxhub.py --fetch-raw-only

    # 不联网, 从 raw 重放 canonical
    python backend/scripts/ingest_holders_tdxhub.py --parse-raw-only

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
from typing import Any, Callable, Sequence

import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest-holders")

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))

from services.db import get_conn, init_db  # noqa: E402
from services.tdx_source import ensure_workspace_tdxhub_path  # noqa: E402
from services.holders_resolver import (  # noqa: E402
    HolderResolver,
    MiaoxiangHolderSource,
    ResolverResult,
)
from services.holder_availability import enrich_holder_rows_with_availability  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402

ensure_workspace_tdxhub_path()


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
        from dim_active_a_stock -- rule-compliance: ok evidence=data-sync-enumeration
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


def _raw_hash(raw_text: str) -> str:
    from tdxhub.holders import _hash  # noqa: WPS433

    return _hash(raw_text)


def _detect_f10_format_label(raw_text: str) -> str:
    from tdxhub.holders import detect_f10_format  # noqa: WPS433

    if detect_f10_format(raw_text) == "b":
        return "b_shsjz"
    if "灵通V9.0" in raw_text:
        return "a_lingtong"
    if "港澳资讯" in raw_text:
        return "a_other"
    return "a_unknown"


def _extract_page_update_date(raw_text: str) -> str | None:
    from tdxhub.holders import PAGE_HEAD_RE  # noqa: WPS433

    head = PAGE_HEAD_RE.search(raw_text or "")
    return head.group("update_date") if head else None


def _fetcher_server(fetcher: Any) -> str | None:
    try:
        return str(fetcher.stats().get("active_server"))
    except Exception:
        return None


def load_alias_map(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return dict(con.execute(
        "select alias, canonical_name from dim_holder_alias"
    ).fetchall())


def _copy_rows(rows: list[dict] | None) -> list[dict]:
    return [dict(row) for row in (rows or [])]


def _build_raw_progress_snapshot(
    progress: dict,
    total: int,
    *,
    code: str | None = None,
    inserted: bool | None = None,
    elapsed: float | None = None,
) -> dict:
    done = int(progress.get("done") or 0)
    elapsed_s = float(progress.get("elapsed_s") or (time.time() - float(progress.get("t0") or time.time())))
    snapshot = {
        "stage": "raw_fetch",
        "status": "running",
        "done": done,
        "total": total,
        "pct": round((done / max(total, 1)) * 100, 1),
        "raw_ok": int(progress.get("raw_ok") or 0),
        "raw_written": int(progress.get("raw_written") or 0),
        "err": int(progress.get("err") or 0),
        "skipped_unchanged": int(progress.get("skipped_unchanged") or 0),
        "skipped_no_f10": int(progress.get("skipped_no_f10") or 0),
        "elapsed_s": round(elapsed_s, 3),
        "message": (
            f"raw_fetch {done}/{total} · "
            f"written={int(progress.get('raw_written') or 0)} · "
            f"err={int(progress.get('err') or 0)}"
        ),
    }
    if code:
        snapshot["last_code"] = code
    if inserted is not None:
        snapshot["last_inserted"] = bool(inserted)
    if elapsed is not None:
        snapshot["last_item_elapsed_s"] = round(elapsed, 3)
    return snapshot


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
    for table in (
        "fact_top10_holder_period",
        "fact_shareholder_plan",
        "fact_shareholder_trade",
        "fact_controlling_shareholder",
    ):
        _delete_rows_by_rowid(
            con,
            table,
            "stock_code = ? AND source = ? AND raw_hash = ?",
            (stock_code, source, raw_hash),
        )


def _delete_rows_by_rowid(
    con: duckdb.DuckDBPyConnection,
    table: str,
    where_sql: str,
    params: tuple,
) -> int:
    """Delete matching rows by rowid so we do not depend on fragile delete-index walks."""

    rowids = [
        r[0]
        for r in con.execute(
            f"SELECT rowid FROM {table} WHERE {where_sql}",
            params,
        ).fetchall()
    ]
    if not rowids:
        return 0
    placeholders = ",".join("?" for _ in rowids)
    con.execute(
        f"DELETE FROM {table} WHERE rowid IN ({placeholders})",
        rowids,
    )
    return len(rowids)


def _update_holder_availability_source(con: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    try:
        for row in rows:
            source = row.get("availability_source")
            if not source:
                continue
            con.execute(
                """
                UPDATE fact_top10_holder_period
                SET availability_source = COALESCE(NULLIF(availability_source, ''), ?)
                WHERE stock_code = ?
                  AND report_date = ?
                  AND holder_set = ?
                  AND source = ?
                  AND is_exit_row = ?
                  AND holder_rank = ?
                  AND row_seq = ?
                  AND COALESCE(share_class, '') = COALESCE(?, '')
                """,
                (
                    source,
                    row.get("stock_code"),
                    row.get("report_date"),
                    row.get("holder_set"),
                    row.get("source"),
                    row.get("is_exit_row"),
                    row.get("holder_rank"),
                    row.get("row_seq"),
                    row.get("share_class"),
                ),
            )
    except Exception:
        return


def write_raw_one(
    con: duckdb.DuckDBPyConnection,
    *,
    stock_code: str,
    stock_name: str,
    market: str,
    raw_text: str,
    raw_hash: str,
    fetched_at: str,
    page_update_date: str | None,
    server: str | None,
    f10_format: str,
    parser_version: str = "v1",
    lock: threading.Lock,
) -> bool:
    """Persist one fetched TDX raw page without touching canonical facts."""

    with lock:
        exists = con.execute(
            """
            SELECT 1
              FROM raw_tdx_f10_holder_research
             WHERE stock_code = ? AND raw_hash = ?
             LIMIT 1
            """,
            (stock_code, raw_hash),
        ).fetchone()
        if exists:
            return False
        con.execute(
            """
            INSERT INTO raw_tdx_f10_holder_research(
              stock_code, stock_name, market, fetched_at, page_update_date,
              raw_text, raw_hash, bytes_len, server, f10_format, parser_version
            ) VALUES (?, ?, ?, cast(? as timestamp), ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stock_code,
                stock_name,
                market,
                fetched_at,
                page_update_date,
                raw_text,
                raw_hash,
                len(raw_text),
                server,
                f10_format,
                parser_version,
            ),
        )
        return True


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
    holders = enrich_holder_rows_with_availability(con, holders)
    periods = result.periods
    ctrl = result.controlling
    plans = _seq_rows(_copy_rows(result.plans), ("stock_code", "raw_hash"), "plan_seq")
    # Root cause for P-1.4 audit FAIL (Rule 5): tdxhub F10 parser 偶尔返回
    # placeholder plan rows (announce_date=None + subject='' + direction='') —
    # 2026-04-28 一次 sync 写入了 7034 行空记录, 拉低 announce_date 非空率到 53%.
    # 过滤掉这类空 stub: 至少 announce_date / subject / direction 之一非空才入库.
    plans = [
        p for p in plans
        if (p.get("announce_date") or "").strip()
        or (p.get("subject") or "").strip()
        or (p.get("direction") or "").strip()
    ]
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
            "f10_format": _detect_f10_format_label(result.raw_text),
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
            # DuckDB INSERT OR REPLACE 不处理 batch 内重复 PK (会 trigger INTERNAL FATAL).
            # PK = UNIQUE(stock_code, report_date, holder_set, source, is_exit_row,
            #             holder_rank, row_seq, share_class). dedup key 完整 8 字段.
            # Codex aaedbc9d MAJOR: 加 duplicate-count log 暴露 parser bug 频率, 不静默吞.
            _dedup_seen: dict[tuple, dict] = {}
            for _row in holders:
                _key = (
                    _row.get("stock_code"), _row.get("report_date"),
                    _row.get("holder_set"), _row.get("source") or "tdx_f10",
                    _row.get("is_exit_row"),
                    _row.get("holder_rank"), _row.get("row_seq"),
                    _row.get("share_class"),
                )
                _dedup_seen[_key] = _row
            holders_for_insert = list(_dedup_seen.values())
            _dup_count = len(holders) - len(holders_for_insert)
            if _dup_count > 0:
                _sample = list(_dedup_seen.keys())[:3]
                logger.warning(
                    "[ingest_holders] batch-dedup: stock=%s dropped %d/%d rows "
                    "(parser duplicate PK; sample keys=%s)",
                    stock_code, _dup_count, len(holders), _sample,
                )
            holder_columns = (
                "stock_code, stock_name, market, report_date, holder_set, "
                "holder_rank, row_seq, holder_name, holder_name_norm, share_class, "
                "is_secondary_class, is_exit_row, "
                "shares_text, shares_approx, shares_precision, hold_amount, "
                "hold_ratio_float, hold_ratio_total, hold_ratio, "
                "hold_market_cap, holder_type, share_nature, "
                "change_status, change_shares_text, change_shares_approx, "
                "hold_change, hold_change_num, "
                "notice_date, effective_date, page_update_date, "
                "source, source_tier, raw_hash, fetched_at, created_at"
            )
            holder_value_count = len(_holder_tuple(holders_for_insert[0]))
            holder_insert_sql = f"INSERT INTO fact_top10_holder_period({holder_columns}) VALUES ({', '.join('?' for _ in range(holder_value_count))})"
            for row in holders_for_insert:
                _delete_rows_by_rowid(
                    con,
                    "fact_top10_holder_period",
                    "stock_code = ? AND report_date = ? AND holder_set = ? "
                    "AND source = ? AND is_exit_row = ? AND holder_rank = ? "
                    "AND row_seq = ? AND COALESCE(share_class, '') = COALESCE(?, '')",
                    (
                        row.get("stock_code"),
                        row.get("report_date"),
                        row.get("holder_set"),
                        row.get("source") or result.source,
                        row.get("is_exit_row"),
                        row.get("holder_rank"),
                        row.get("row_seq"),
                        row.get("share_class"),
                    ),
                )
            con.executemany(holder_insert_sql, [_holder_tuple(row) for row in holders_for_insert])
            _update_holder_availability_source(con, holders_for_insert)

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
    raw_keys: Sequence[tuple[str, str]] | None = None,
) -> list:
    if raw_keys:
        con.execute("DROP TABLE IF EXISTS tmp_holder_raw_replay_keys")
        con.execute(
            "CREATE TEMP TABLE tmp_holder_raw_replay_keys(stock_code TEXT, raw_hash TEXT)"
        )
        con.executemany(
            "INSERT INTO tmp_holder_raw_replay_keys VALUES (?, ?)",
            [(code, hash_value) for code, hash_value in raw_keys],
        )
        limit_sql = "LIMIT ?" if limit else ""
        params: list[Any] = [limit] if limit else []
        try:
            return con.execute(
                f"""
                SELECT r.stock_code, r.stock_name, r.market, r.raw_text, r.raw_hash,
                       r.fetched_at, r.page_update_date, r.server
                  FROM raw_tdx_f10_holder_research r
                  JOIN tmp_holder_raw_replay_keys k
                    ON r.stock_code = k.stock_code AND r.raw_hash = k.raw_hash
                 ORDER BY r.fetched_at DESC, r.stock_code
                 {limit_sql}
                """,
                params,
            ).fetchall()
        finally:
            con.execute("DROP TABLE IF EXISTS tmp_holder_raw_replay_keys")

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
    raw_keys: Sequence[tuple[str, str]] | None = None,
    parser: Callable[..., dict] | None = None,
    hasher: Callable[[str], str] | None = None,
) -> dict:
    """Replay stored TDX raw text into canonical holder fact tables."""

    t0 = time.time()
    rows = _raw_replay_rows(
        con,
        symbols=symbols,
        raw_hash=raw_hash,
        limit=limit,
        raw_keys=raw_keys,
    )
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


def _make_raw_fetcher():
    from tdxhub.holders import HolderFetcher  # noqa: WPS433

    return HolderFetcher(timeout=15, max_attempts_per_call=6)


def raw_worker(
    name: str,
    job_q: queue.Queue,
    con: duckdb.DuckDBPyConnection,
    con_lock: threading.Lock,
    progress: dict,
    progress_lock: threading.Lock,
    seen_hashes: set,
    seen_lock: threading.Lock,
    *,
    fetcher_factory: Callable[[], Any] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> None:
    fetcher = fetcher_factory() if fetcher_factory is not None else _make_raw_fetcher()
    while True:
        item = job_q.get()
        if item is None:
            job_q.task_done()
            break
        _idx, total, code, stock_name, market = item
        t0 = time.time()
        snapshot = None
        try:
            raw_text = fetcher.fetch_text(code)
            if not raw_text:
                with progress_lock:
                    progress["skipped_no_f10"] += 1
                    progress["done"] += 1
                    if progress["done"] % 50 == 0:
                        snapshot = _build_raw_progress_snapshot(
                            progress,
                            total,
                            code=code,
                            elapsed=time.time() - t0,
                        )
                continue
            raw_hash = _raw_hash(raw_text)
            key = (code, raw_hash)
            with seen_lock:
                if key in seen_hashes:
                    with progress_lock:
                        progress["raw_ok"] += 1
                        progress["skipped_unchanged"] += 1
                        progress["done"] += 1
                        if progress["done"] % 50 == 0:
                            snapshot = _build_raw_progress_snapshot(
                                progress,
                                total,
                                code=code,
                                elapsed=time.time() - t0,
                            )
                    continue
                seen_hashes.add(key)
            fetched_at = datetime.utcnow().isoformat(timespec="seconds")
            inserted = write_raw_one(
                con,
                stock_code=code,
                stock_name=stock_name,
                market=market,
                raw_text=raw_text,
                raw_hash=raw_hash,
                fetched_at=fetched_at,
                page_update_date=_extract_page_update_date(raw_text),
                server=_fetcher_server(fetcher),
                f10_format=_detect_f10_format_label(raw_text),
                lock=con_lock,
            )
            elapsed = time.time() - t0
            with progress_lock:
                progress["raw_ok"] += 1
                progress["done"] += 1
                if inserted:
                    progress["raw_written"] += 1
                    progress["raw_keys"].append(key)
                else:
                    progress["skipped_unchanged"] += 1
                if progress["done"] % 50 == 0:
                    rate = progress["done"] / max(time.time() - progress["t0"], 1e-3)
                    log.info(
                        "[raw %4d/%d] %s %s inserted=%s %.1fs rate=%.1f/s",
                        progress["done"], total, code, name, inserted, elapsed, rate,
                    )
                    snapshot = _build_raw_progress_snapshot(
                        progress,
                        total,
                        code=code,
                        inserted=inserted,
                        elapsed=elapsed,
                    )
        except Exception as e:
            with progress_lock:
                progress["err"] += 1
                progress["done"] += 1
                progress["failed_items"].append((code, stock_name, market, str(e)))
                log.warning("[raw %s] %s ERROR %s: %s", name, code, type(e).__name__, e)
                if progress["done"] % 50 == 0:
                    snapshot = _build_raw_progress_snapshot(
                        progress,
                        total,
                        code=code,
                        elapsed=time.time() - t0,
                    )
        finally:
            job_q.task_done()
            if snapshot is not None and progress_callback is not None:
                try:
                    progress_callback(snapshot)
                # rule-compliance: ok evidence=defensive-logging progress callback failure must not abort the ingest loop
                except Exception as exc:
                    log.warning("[raw %s] progress callback failed: %s", name, exc)
    try:
        fetcher.close()
    # rule-compliance: ok evidence=defensive-logging fetcher close failure should not hide the written rows
    except Exception as exc:
        log.warning("[raw %s] fetcher.close failed: %s", name, exc)


def fetch_raw_records(
    con: duckdb.DuckDBPyConnection,
    *,
    workers: int = 4,
    limit: int = 0,
    symbols: str = "",
    force: bool = False,
    fetcher_factory: Callable[[], Any] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    """Fetch TDX F10 raw pages only; canonical facts are updated by replay."""

    explicit = [s.strip() for s in symbols.split(",") if s.strip()] or None
    universe = load_universe(con, limit=limit, explicit=explicit)
    log.info("raw fetch universe: %d stocks", len(universe))

    seen = set() if force else existing_hashes(con)
    seen_lock = threading.Lock()
    log.info("existing raw_hash cache: %d entries (force=%s)", len(seen), force)

    job_q: queue.Queue = queue.Queue()
    for i, (code, stock_name, market) in enumerate(universe, 1):
        job_q.put((i, len(universe), code, stock_name, market))
    for _ in range(max(workers, 1)):
        job_q.put(None)

    progress = {
        "done": 0,
        "raw_ok": 0,
        "raw_written": 0,
        "err": 0,
        "skipped_unchanged": 0,
        "skipped_no_f10": 0,
        "raw_keys": [],
        "failed_items": [],
        "t0": time.time(),
    }
    progress_lock = threading.Lock()
    con_lock = threading.Lock()

    ths = []
    for i in range(max(workers, 1)):
        t = threading.Thread(
            target=raw_worker,
            args=(f"raw-w{i+1}", job_q, con, con_lock, progress, progress_lock, seen, seen_lock),
            kwargs={
                "fetcher_factory": fetcher_factory,
                "progress_callback": progress_callback,
            },
            name=f"holder-raw-worker-{i+1}",
        )
        t.start()
        ths.append(t)
    for t in ths:
        t.join()

    try:
        con.commit()
    except Exception:
        pass
    elapsed = time.time() - progress["t0"]
    progress["elapsed_s"] = elapsed
    progress["ok"] = progress["raw_written"]
    if progress_callback is not None:
        try:
            progress_callback(
                _build_raw_progress_snapshot(
                    progress,
                    len(universe),
                )
            )
        # rule-compliance: ok evidence=defensive-logging final snapshot emission must not fail the raw fetch result
        except Exception as exc:
            log.warning("[raw fetch] final progress callback failed: %s", exc)
    log.info("=== raw fetch complete in %.1fs ===", elapsed)
    log.info("  total      : %d", progress["done"])
    log.info("  raw ok     : %d", progress["raw_ok"])
    log.info("  raw written: %d", progress["raw_written"])
    log.info("  unchanged  : %d", progress["skipped_unchanged"])
    log.info("  no F10     : %d", progress["skipped_no_f10"])
    log.info("  errors     : %d", progress["err"])
    return progress


def _fallback_records(
    con: duckdb.DuckDBPyConnection,
    *,
    failed_items: Sequence[tuple[str, str, str, str]],
    alias_map: dict[str, str],
    lock: threading.Lock,
) -> dict:
    stats = {
        "attempted": len(failed_items),
        "ok": 0,
        "no_data": 0,
        "err": 0,
        "errors": [],
    }
    if not failed_items:
        return stats

    resolver = HolderResolver([MiaoxiangHolderSource()])
    try:
        for code, stock_name, market, tdx_error in failed_items:
            try:
                result = resolver.fetch(code, stock_name=stock_name)
                if result is None or not result.has_data():
                    stats["no_data"] += 1
                    continue
                write_one(
                    con,
                    stock_code=code,
                    stock_name=stock_name,
                    market=market,
                    result=result,
                    alias_map=alias_map,
                    lock=lock,
                )
                stats["ok"] += 1
                log.warning(
                    "[fallback] %s used miaoxiang after tdxhub error: %s",
                    code,
                    tdx_error,
                )
            except Exception as exc:
                stats["err"] += 1
                stats["errors"].append(f"{code}: {type(exc).__name__}: {exc}")
                log.warning(
                    "[fallback] %s ERROR %s: %s",
                    code,
                    type(exc).__name__,
                    exc,
                )
    finally:
        resolver.close()
    return stats


def run(
    *,
    workers: int = 4,
    limit: int = 0,
    symbols: str = "",
    force: bool = False,
    no_fallback: bool = False,
    con: duckdb.DuckDBPyConnection | None = None,
    fetcher_factory: Callable[[], Any] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    """实际执行入口: fetch raw → replay canonical, 可被 in-process 调用.

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
        alias_map = load_alias_map(con)
        log.info("alias map: %d entries", len(alias_map))

        t0 = time.time()
        raw_stats = fetch_raw_records(
            con,
            workers=workers,
            limit=limit,
            symbols=symbols,
            force=force,
            fetcher_factory=fetcher_factory,
            progress_callback=progress_callback,
        )
        raw_keys = list(raw_stats.get("raw_keys") or [])
        if raw_keys:
            parse_stats = parse_raw_records(con, raw_keys=raw_keys)
        else:
            parse_stats = {
                "raw_rows": 0,
                "parsed": 0,
                "no_data": 0,
                "errors": 0,
                "replace_facts": False,
                "elapsed_s": 0.0,
            }

        fallback_stats = {
            "attempted": 0,
            "ok": 0,
            "no_data": 0,
            "err": 0,
            "errors": [],
        }
        if enable_fallback:
            fallback_stats = _fallback_records(
                con,
                failed_items=raw_stats.get("failed_items") or [],
                alias_map=alias_map,
                lock=con_lock,
            )
            try:
                con.commit()
            except Exception:
                pass

        unresolved_tdx_errors = max(
            0,
            int(raw_stats.get("err") or 0) - int(fallback_stats.get("ok") or 0),
        )
        elapsed = time.time() - t0
        progress = {
            "done": int(raw_stats.get("done") or 0),
            "ok": int(parse_stats.get("parsed") or 0) + int(fallback_stats.get("ok") or 0),
            "err": unresolved_tdx_errors
            + int(parse_stats.get("errors") or 0)
            + int(fallback_stats.get("err") or 0),
            "skipped_unchanged": int(raw_stats.get("skipped_unchanged") or 0),
            "skipped_no_f10": int(raw_stats.get("skipped_no_f10") or 0),
            "raw_written": int(raw_stats.get("raw_written") or 0),
            "raw_ok": int(raw_stats.get("raw_ok") or 0),
            "parsed": int(parse_stats.get("parsed") or 0),
            "parse_errors": int(parse_stats.get("errors") or 0),
            "tdx_err": int(raw_stats.get("err") or 0),
            "fallback_ok": int(fallback_stats.get("ok") or 0),
            "fallback_err": int(fallback_stats.get("err") or 0),
            "elapsed_s": elapsed,
            "raw_fetch": {
                key: value
                for key, value in raw_stats.items()
                if key not in {"raw_keys", "failed_items", "t0"}
            },
            "parse": parse_stats,
            "fallback": fallback_stats,
        }
        log.info("=== raw+parse ingest complete in %.1fs ===", elapsed)
        log.info("  total      : %d", progress["done"])
        log.info("  raw written: %d", progress["raw_written"])
        log.info("  parsed     : %d", progress["parsed"])
        log.info("  fallback ok: %d", progress["fallback_ok"])
        log.info("  unchanged  : %d (raw_hash already in DB)", progress["skipped_unchanged"])
        log.info("  no F10     : %d", progress["skipped_no_f10"])
        log.info("  errors     : %d", progress["err"])
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
    p.add_argument("--fetch-raw-only", action="store_true",
                   help="只联网抓取 raw_tdx_f10_holder_research, 不写 canonical fact 表")
    p.add_argument("--parse-raw-only", action="store_true",
                   help="不联网抓取, 只重放 raw_tdx_f10_holder_research 到 canonical fact 表")
    p.add_argument("--raw-hash", default="",
                   help="配合 --parse-raw-only, 只重放指定 raw_hash")
    p.add_argument("--replace-facts", action="store_true",
                   help="配合 --parse-raw-only, 先删除同 stock/source/raw_hash 的旧 canonical 行再写入")
    args = p.parse_args()

    if args.fetch_raw_only and args.parse_raw_only:
        p.error("--fetch-raw-only and --parse-raw-only cannot be used together")

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

    if args.fetch_raw_only:
        started_at = utc_now_iso()
        init_db()
        con = get_conn()
        try:
            stats = fetch_raw_records(
                con,
                workers=args.workers,
                limit=args.limit,
                symbols=args.symbols,
                force=args.force,
            )
            record_pipeline_run(
                con,
                run_id=f"fetch_holders_raw_{started_at.replace(':', '').replace('-', '')[:15]}",
                pipeline_name="fetch_holders_raw",
                status="success" if stats["err"] == 0 else "failed",
                started_at=started_at,
                ended_at=utc_now_iso(),
                duration_s=stats["elapsed_s"],
                commit_sha=git_commit_sha(REPO),
                input_tables=["dim_active_a_stock", "raw_tdx_f10_holder_research"],  # rule-compliance: ok evidence=lineage-metadata
                output_tables=["raw_tdx_f10_holder_research"],
                blockers=[] if stats["err"] == 0 else ["fetch_errors"],
                perf_summary={
                    key: value
                    for key, value in stats.items()
                    if key not in {"raw_keys", "failed_items", "t0"}
                },
            )
            log.info("fetch raw complete: %s", {
                key: value
                for key, value in stats.items()
                if key not in {"raw_keys", "failed_items", "t0"}
            })
            return 0 if stats["err"] == 0 else 1
        finally:
            con.close()

    started_at = utc_now_iso()
    progress = run(
        workers=args.workers, limit=args.limit, symbols=args.symbols,
        force=args.force, no_fallback=args.no_fallback,
    )
    init_db()
    con = get_conn()
    try:
        record_pipeline_run(
            con,
            run_id=f"ingest_holders_{started_at.replace(':', '').replace('-', '')[:15]}",
            pipeline_name="ingest_holders_tdxhub",
            status="success" if progress["err"] == 0 else "failed",
            started_at=started_at,
            ended_at=utc_now_iso(),
            duration_s=progress["elapsed_s"],
            commit_sha=git_commit_sha(REPO),
            input_tables=[
                "dim_active_a_stock",  # rule-compliance: ok evidence=lineage-metadata
                "dim_holder_alias",
                "raw_tdx_f10_holder_research",
            ],
            output_tables=[
                "raw_tdx_f10_holder_research",
                "fact_top10_holder_period",
                "fact_controlling_shareholder",
                "fact_shareholder_plan",
                "fact_shareholder_trade",
            ],
            blockers=[] if progress["err"] == 0 else ["holder_ingest_errors"],
            perf_summary=progress,
        )
    finally:
        con.close()
    return 0 if progress["err"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
