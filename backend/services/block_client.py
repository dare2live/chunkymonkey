"""TDX 板块/概念同步客户端。"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

import pandas as pd

from services.tdx_source import call_tdx_quotes_with_retry


logger = logging.getLogger("cm-api")
BLOCK_FILES: tuple[tuple[str, str], ...] = (
    ("zs", "block_zs.dat"),
    ("fg", "block_fg.dat"),
    ("gn", "block_gn.dat"),
)
_BLOCK_ALLOWED_NAME_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9·()（）\-_/ +]+$")


def _clean_block_name(value: object) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return re.sub(r"\s+", " ", text)


def _is_readable_block_name(value: object) -> bool:
    text = _clean_block_name(value)
    if len(text) < 2:
        return False
    if not _BLOCK_ALLOWED_NAME_RE.fullmatch(text):
        return False
    alpha_like = sum(1 for ch in text if ("\u4e00" <= ch <= "\u9fff") or ch.isalpha())
    digit_count = sum(1 for ch in text if ch.isdigit())
    if alpha_like <= 0:
        return False
    if digit_count > alpha_like * 2:
        return False
    return True


async def fetch_tdx_block_file(block_file: str) -> tuple[pd.DataFrame, str]:
    """从共享 tdxhub 入口抓取单个 block 文件。"""
    frame, source = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: call_tdx_quotes_with_retry(
            lambda client: client.block(tofile=block_file),
            action_name=f"block[{block_file}]",
        ),
    )
    if frame is None or frame.empty:
        raise ValueError(f"{block_file} empty")
    return frame, source


def _normalize_block_frame(frame: pd.DataFrame, *, block_category: str,
                           block_file: str, active_codes: set[str],
                           excluded_codes: set[str] | None = None) -> tuple[list[dict], dict]:
    excluded = excluded_codes or set()
    if frame is None or frame.empty:
        return [], {
            "raw_rows": 0,
            "kept_rows": 0,
            "unique_blocks": 0,
            "skipped_non_active": 0,
            "skipped_excluded": 0,
            "skipped_invalid_name": 0,
        }

    data = frame.loc[:, ["blockname", "block_type", "code_index", "code"]].copy()
    data["stock_code"] = data["code"].astype(str).str.extract(r"(\d{6})", expand=False)
    data["block_name"] = data["blockname"].map(_clean_block_name)
    raw_rows = len(data)

    invalid_code_mask = data["stock_code"].isna() | ~data["stock_code"].isin(active_codes)
    excluded_mask = data["stock_code"].isin(excluded)
    invalid_name_mask = ~data["block_name"].map(_is_readable_block_name)

    kept = data.loc[~invalid_code_mask & ~excluded_mask & ~invalid_name_mask].copy()
    if kept.empty:
        return [], {
            "raw_rows": raw_rows,
            "kept_rows": 0,
            "unique_blocks": 0,
            "skipped_non_active": int(invalid_code_mask.sum()),
            "skipped_excluded": int(excluded_mask.sum()),
            "skipped_invalid_name": int(invalid_name_mask.sum()),
        }

    kept["block_category"] = block_category
    kept["block_file"] = block_file
    kept["block_type"] = pd.to_numeric(kept["block_type"], errors="coerce").fillna(0).astype(int)
    kept["code_index"] = pd.to_numeric(kept["code_index"], errors="coerce").fillna(0).astype(int)
    kept = kept.drop_duplicates(subset=["stock_code", "block_category", "block_name"])

    rows = [
        {
            "stock_code": row.stock_code,
            "block_category": row.block_category,
            "block_name": row.block_name,
            "block_file": row.block_file,
            "block_type": int(row.block_type),
            "code_index": int(row.code_index),
        }
        for row in kept.itertuples(index=False)
    ]
    stats = {
        "raw_rows": raw_rows,
        "kept_rows": len(rows),
        "unique_blocks": kept[["block_category", "block_name"]].drop_duplicates().shape[0],
        "skipped_non_active": int(invalid_code_mask.sum()),
        "skipped_excluded": int(excluded_mask.sum()),
        "skipped_invalid_name": int(invalid_name_mask.sum()),
    }
    return rows, stats


def _build_catalog_rows(member_rows: list[dict], *, source: str, updated_at: str) -> list[dict]:
    if not member_rows:
        return []
    frame = pd.DataFrame(member_rows)
    grouped = (
        frame.groupby(["block_category", "block_name", "block_file", "block_type"], as_index=False)
        .size()
        .rename(columns={"size": "member_count"})
    )
    return [
        {
            "block_category": row.block_category,
            "block_name": row.block_name,
            "block_file": row.block_file,
            "block_type": int(row.block_type),
            "member_count": int(row.member_count),
            "source": source,
            "updated_at": updated_at,
        }
        for row in grouped.itertuples(index=False)
    ]


async def sync_tdx_blocks(conn, *, active_codes: set[str],
                          excluded_codes: set[str] | None = None,
                          should_stop=None) -> dict:
    """全量同步 TDX 板块成员与板块目录。"""
    if not active_codes:
        return {
            "status": "blocked",
            "member_rows": 0,
            "catalog_rows": 0,
            "reason": "dim_active_a_stock 为空",
            "files": {},
        }

    now = datetime.now().isoformat()
    all_member_rows: list[dict] = []
    per_file: dict[str, dict] = {}
    source = "tdxhub"

    for block_category, block_file in BLOCK_FILES:
        if should_stop:
            should_stop()
        frame, fetch_source = await fetch_tdx_block_file(block_file)
        source = fetch_source
        rows, stats = _normalize_block_frame(
            frame,
            block_category=block_category,
            block_file=block_file,
            active_codes=active_codes,
            excluded_codes=excluded_codes,
        )
        per_file[block_category] = {
            "status": "success" if rows else "partial",
            "block_file": block_file,
            **stats,
        }
        all_member_rows.extend(rows)

    catalog_rows = _build_catalog_rows(all_member_rows, source=source, updated_at=now)

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM dim_stock_tdx_block")
        conn.execute("DELETE FROM dim_tdx_block_catalog")
        if all_member_rows:
            conn.executemany(
                """
                INSERT INTO dim_stock_tdx_block
                (stock_code, block_category, block_name, block_file, block_type,
                 code_index, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["stock_code"],
                        row["block_category"],
                        row["block_name"],
                        row["block_file"],
                        row["block_type"],
                        row["code_index"],
                        source,
                        now,
                    )
                    for row in all_member_rows
                ],
            )
        if catalog_rows:
            conn.executemany(
                """
                INSERT INTO dim_tdx_block_catalog
                (block_category, block_name, block_file, block_type, member_count,
                 source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["block_category"],
                        row["block_name"],
                        row["block_file"],
                        row["block_type"],
                        row["member_count"],
                        row["source"],
                        row["updated_at"],
                    )
                    for row in catalog_rows
                ],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "status": "success" if all_member_rows else "partial",
        "member_rows": len(all_member_rows),
        "catalog_rows": len(catalog_rows),
        "source": source,
        "files": per_file,
    }
