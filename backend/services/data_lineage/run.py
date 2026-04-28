"""派生 lineage 执行 + 状态写回 mart_lineage.

run_lineage(lineage_id) — 单条执行 (适合 cron / 手动重算)
refresh_all_lineage_state() — 把 LINEAGES 里的所有声明同步到 mart_lineage 表
                              (新增 / 更新 sql_hash; 不改 last_run 状态)
"""
from __future__ import annotations

import importlib
import json
import logging
import time
from typing import Optional

from services.db import get_conn
from .registry import LineageSpec, get_lineage, all_lineages

logger = logging.getLogger("cm-lineage")


def refresh_all_lineage_state() -> int:
    """把 registry 里所有 lineage 同步到 mart_lineage 表 (UPSERT).

    只更新声明性字段 (input_tables / sql_hash / description / owner / version),
    不动 last_run_* 字段 (那是运行时状态).
    """
    upserted = 0
    with get_conn() as conn:
        for spec in all_lineages():
            sql_hash = spec.sql_hash()
            input_json = json.dumps(spec.input_tables, ensure_ascii=False)
            sql_text = spec.sql_text or f"# entry_point: {spec.entry_point}"
            conn.execute("""
                INSERT INTO mart_lineage (
                    lineage_id, output_table, input_tables, sql_text, sql_hash,
                    version, owner, description, last_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', now(), now())
                ON CONFLICT (lineage_id) DO UPDATE SET
                    output_table = EXCLUDED.output_table,
                    input_tables = EXCLUDED.input_tables,
                    sql_text     = EXCLUDED.sql_text,
                    sql_hash     = EXCLUDED.sql_hash,
                    version      = EXCLUDED.version,
                    owner        = EXCLUDED.owner,
                    description  = EXCLUDED.description,
                    updated_at   = now()
            """, (
                spec.lineage_id, spec.output_table, input_json, sql_text, sql_hash,
                spec.version, spec.owner, spec.description,
            ))
            upserted += 1
        conn.commit()
    return upserted


def _resolve_entry_point(entry_point: str):
    """'module.path:func_name' → 实际 callable."""
    module_name, _, func_name = entry_point.partition(":")
    if not module_name or not func_name:
        raise ValueError(f"invalid entry_point: {entry_point}")
    mod = importlib.import_module(module_name)
    fn = getattr(mod, func_name)
    return fn


def run_lineage(lineage_id: str, *, dry_run: bool = False) -> dict:
    """执行一条 lineage. 返回 (status, runtime_s, row_count, error).

    优先 sql_text → 直接 execute (派生 SQL).
    否则 entry_point → 反射调用 (脚本派生).
    """
    spec = get_lineage(lineage_id)
    if spec is None:
        raise KeyError(f"unknown lineage_id: {lineage_id}")

    t0 = time.time()
    status = "ok"
    err: Optional[str] = None
    row_count: Optional[int] = None

    try:
        if spec.sql_text:
            with get_conn() as conn:
                if dry_run:
                    logger.info(f"[lineage:{lineage_id}] dry-run sql ({len(spec.sql_text)} chars)")
                else:
                    conn.execute(spec.sql_text)
                    row_count_row = conn.execute(
                        f"SELECT COUNT(*) FROM {spec.output_table}"
                    ).fetchone()
                    row_count = row_count_row[0] if row_count_row else None
                    conn.commit()
        elif spec.entry_point:
            if dry_run:
                logger.info(f"[lineage:{lineage_id}] dry-run entry_point: {spec.entry_point}")
            else:
                fn = _resolve_entry_point(spec.entry_point)
                fn()
                # 取 row_count (best effort)
                try:
                    with get_conn() as conn:
                        row_count = conn.execute(
                            f"SELECT COUNT(*) FROM {spec.output_table}"
                        ).fetchone()[0]
                except Exception:
                    pass
        else:
            raise ValueError(f"lineage {lineage_id} has neither sql_text nor entry_point")
    except Exception as exc:
        status = "failed"
        err = f"{type(exc).__name__}: {exc}"
        logger.exception(f"[lineage:{lineage_id}] run failed")

    runtime_s = time.time() - t0

    if not dry_run:
        with get_conn() as conn:
            conn.execute("""
                UPDATE mart_lineage
                   SET last_run_at    = now(),
                       last_row_count = ?,
                       last_status    = ?,
                       last_error     = ?,
                       last_runtime_s = ?,
                       updated_at     = now()
                 WHERE lineage_id = ?
            """, (row_count, status, err, runtime_s, lineage_id))
            conn.commit()

    return {
        "lineage_id": lineage_id,
        "status": status,
        "runtime_s": runtime_s,
        "row_count": row_count,
        "error": err,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-only", action="store_true", help="只同步声明, 不跑")
    parser.add_argument("--run", help="跑指定 lineage_id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    n = refresh_all_lineage_state()
    print(f"[OK] mart_lineage upserted {n} entries")

    if args.run:
        result = run_lineage(args.run, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
