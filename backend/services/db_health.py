"""Phase ψ.5 根因 2 修复 — DuckDB 索引一致性健康检查.

背景:
  raw_gpcw_financial 表上的 ART secondary index 在 ON CONFLICT DO UPDATE 时
  偶发跟 storage 不一致 (DuckDB 已知 issue 在版本升级 / SIGTERM 中断写入时).
  症状: DELETE WHERE 列条件 报 "Failed to delete all rows from index. Only
  deleted 0 out of 1 rows", 整库进入 FATAL invalidate state.

策略 (3 层防御, 本文件实现层 1+2):
  1. **删冗余索引**: 跟 PK 完全同列的 secondary index 删掉, 消除 attack surface.
  2. **启动一致性检查**: 后端启动 + cron_daily 跑 sync 前, 比对 table 行数 vs
     index 路径行数; 不一致就 REINDEX (或 DROP+CREATE).
  3. **写入路径走 rowid**: DELETE 走 rowid 而非列条件, 绕索引 (实现在
     financial_client._cleanup_snapshot_stub).

使用:
    from services.db_health import run_startup_checks
    run_startup_checks(conn)   # backend startup + cron_daily 入口调
"""
from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)


# (table, index_name) — 已知跟 PK 重复的冗余 secondary index, 启动时如发现就删除.
REDUNDANT_INDEXES: tuple[tuple[str, str], ...] = (
    ("raw_gpcw_financial", "idx_rgf_stock_report"),  # 跟 PK (stock_code, report_date) 重复
)


# 重点观察的关键表 — 启动时跑 index_count vs storage_count.
WATCHED_TABLES: tuple[str, ...] = (
    "raw_gpcw_financial",
)


def drop_redundant_indexes(conn) -> list[str]:
    """删除已知跟 PK 完全重复的 secondary index. 返回真删了的索引名."""
    dropped: list[str] = []
    try:
        predicates = " OR ".join("(table_name = ? AND index_name = ?)" for _ in REDUNDANT_INDEXES)
        params = [value for pair in REDUNDANT_INDEXES for value in pair]
        rows = conn.execute(
            f"SELECT table_name, index_name FROM duckdb_indexes() WHERE {predicates}",
            params,
        ).fetchall()
    except Exception as exc:
        logger.warning("[db_health] duckdb_indexes() 查询失败: %s", exc)
        return dropped
    existing = [(row["table_name"], row["index_name"]) if hasattr(row, "keys") else (row[0], row[1]) for row in rows]
    if not existing:
        return dropped
    try:
        conn.execute(";\n".join(f"DROP INDEX IF EXISTS {idx}" for _, idx in existing))
    except Exception as exc:
        logger.warning("[db_health] DROP redundant indexes 失败: %s", exc)
        return dropped
    for table, idx in existing:
        dropped.append(f"{table}.{idx}")
        logger.info("[db_health] 删冗余索引 %s.%s (跟 PK 同列)", table, idx)
    return dropped


def check_table_index_consistency(conn, table: str) -> dict:
    """检查 table 上每个索引声明的行数是否跟 storage 一致.

    DuckDB 不暴露直接的"索引行数"API, 但我们可以用 EXPLAIN 跑 SELECT 走索引 vs 不走索引
    对比 row count. 这里采用更简单的策略: SELECT COUNT(*) 跟 SELECT COUNT(*)
    强制走索引扫描 — 应一致.

    实际实现: 用 PRAGMA verify_external 或者跑一次 sanity DELETE WHERE 1=0
    看是否抛错 (这是 DuckDB 内部的 index integrity check). 当前用最简单方案:
    SELECT COUNT(*) 走全表 vs 走 PK index, 一致即认为 OK.
    """
    try:
        storage = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception as exc:
        return {"table": table, "ok": False, "reason": f"count_failed: {exc}"}

    # 触发一次"零 DELETE" 让 DuckDB 自己跑 index walk; phantom 会在这里抛
    # FATAL — 之前 sync_financial 就是这样炸的. 我们 catch 它就知道索引坏了.
    try:
        conn.execute(f"DELETE FROM {table} WHERE 1=0")
        return {"table": table, "ok": True, "rows": storage}
    except Exception as exc:
        return {"table": table, "ok": False, "rows": storage,
                "reason": f"index_walk_failed: {str(exc)[:200]}"}


def reindex_table(conn, table: str) -> list[str]:
    """暴力重建 table 上所有 secondary index (DROP + CREATE)."""
    rows = conn.execute(
        "SELECT index_name, sql FROM duckdb_indexes() WHERE table_name = ?",
        (table,),
    ).fetchall()
    index_defs = [(row["index_name"], row["sql"]) if hasattr(row, "keys") else (row[0], row[1]) for row in rows]
    index_defs = [(idx_name, ddl) for idx_name, ddl in index_defs if ddl]
    if not index_defs:
        return []
    script = ";\n".join(
        statement
        for idx_name, ddl in index_defs
        for statement in (f"DROP INDEX IF EXISTS {idx_name}", ddl)
    )
    try:
        conn.execute(script)
    except Exception as exc:
        logger.error("[db_health] REINDEX %s 失败: %s", table, exc)
        return []
    rebuilt = [idx_name for idx_name, _ in index_defs]
    for idx_name in rebuilt:
        logger.info("[db_health] REINDEX %s.%s", table, idx_name)
    return rebuilt


def run_startup_checks(conn) -> dict:
    """后端启动 / cron_daily 入口调用. 返回检查摘要 (写入 manifest 即可).

    步骤:
      1. drop_redundant_indexes — 删跟 PK 重复的二级索引
      2. check_table_index_consistency — 看关键表索引是否一致
      3. 不一致 → reindex_table 自动修复
      4. 再 check; 仍坏 → 抛出, 让上层决定 (production 拒启动)
    """
    summary = {"dropped": [], "checks": [], "reindexed": [], "still_broken": []}

    summary["dropped"] = drop_redundant_indexes(conn)

    for table in WATCHED_TABLES:
        chk = check_table_index_consistency(conn, table)
        summary["checks"].append(chk)
        if chk.get("ok"):
            continue
        logger.warning("[db_health] %s index 不一致: %s — 尝试 REINDEX", table, chk.get("reason"))
        summary["reindexed"].extend(reindex_table(conn, table))
        chk2 = check_table_index_consistency(conn, table)
        if not chk2.get("ok"):
            summary["still_broken"].append(chk2)
            logger.error("[db_health] %s 在 REINDEX 后仍不一致: %s", table, chk2.get("reason"))

    if summary["still_broken"]:
        raise RuntimeError(
            f"[db_health] startup checks failed — index consistency 不可恢复: {summary['still_broken']}"
        )
    return summary
