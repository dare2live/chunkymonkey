"""通用妙想 capability 同步 — P1.5 (2026-04-28).

接通妙想独家 capability 到 sync step (2026-06-19: holder_count → tushare stk_holdernumber 转正退役;
financial_history_200q 50股探针孤儿退役; 留 3 个 LIVE 喂 v3_picture serving):
- valuation_quantile  估值分位 (RPT_STOCKVALUATIONTANTILE, 日)
- peer_valuation      同行估值排名 (RPT_PCF10_INDUSTRY_CVALUE, 季)
  (forecast_consensus 已删 2026-06-28 G5 退役: 0 消费方)

设计:
- 配置式声明 (CAPABILITY_CONFIG): reportName / pk / 字段映射 / schema_sql
- 单一 sync_capability(name) 函数干所有
- 走 aif10_scraper.fetch_all_pages 主源 (P0.3 fallback 暂不接, 这 5 个 ak.X 没替代)
- 写 raw_aif10_<name> 表 (区分项目原有 raw_*)
- 末尾 record_actual_version (P0.1 schema_version 接通)

每个 sync 函数独立, 调用一次拉全市场全历史. 后续可加 since 增量.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso

logger = logging.getLogger("cm-api.aif10_capability")


# ===========================================================================
# 5 capability 配置 (reportName + 字段映射 + 表 schema)
# 字段名来自 docs/p6_probe.json 实测 (茅台 600519 sample row)
# ===========================================================================

CAPABILITY_CONFIG: dict[str, dict] = {
    "valuation_quantile": {
        "report_name": "RPT_STOCKVALUATIONTANTILE",
        "raw_table": "raw_aif10_valuation_quantile",
        "pk_cols": ("secucode", "statistics_cycle", "index_type"),
        "field_map": {
            "secucode": "SECUCODE",
            "security_code": "SECURITY_CODE",
            "statistics_cycle": "STATISTICS_CYCLE",  # 1=1Y/2=3Y/3=5Y/4=10Y
            "index_type": "INDEX_TYPE",  # 1=PE/2=PB/3=PS/4=PEG
            "percentile_thirty": "PERCENTILE_THIRTY",
            "percentile_fifty": "PERCENTILE_FIFTY",
            "percentile_seventy": "PERCENTILE_SEVENTY",
        },
        "sort_columns": "SECURITY_CODE",
        "sort_types": "1",
    },
    "peer_valuation": {
        "report_name": "RPT_PCF10_INDUSTRY_CVALUE",
        "raw_table": "raw_aif10_peer_valuation",
        "pk_cols": ("secucode", "report_date"),
        "field_map": {
            "secucode": "SECUCODE",
            "security_code": "SECURITY_CODE",
            "security_name_abbr": "SECURITY_NAME_ABBR",
            "report_date": "REPORT_DATE",
            "industry_code": "INDUSTRY_CODE",
            "industry_name": "INDUSTRY_NAME",
            "stock_pe": "STOCK_PE",
            "industry_pe_avg": "INDUSTRY_PE_AVG",
            "industry_pe_median": "INDUSTRY_PE_MEDIAN",
            "stock_pe_rank": "STOCK_PE_RANK",
            "stock_peg": "STOCK_PEG",
            "industry_peg_avg": "INDUSTRY_PEG_AVG",
            "stock_peg_rank": "STOCK_PEG_RANK",
            "stock_pb": "STOCK_PB",
            "industry_pb_avg": "INDUSTRY_PB_AVG",
            "stock_pb_rank": "STOCK_PB_RANK",
        },
        "sort_columns": "REPORT_DATE,SECURITY_CODE",
        "sort_types": "-1,1",
    },
    # forecast_consensus 已删 2026-06-28 (G5 退役: 0 live 消费方 + profit_forecast 已退役 + 同步从未接入 acquire 日常流)
}


# ===========================================================================
# 通用 fetch + upsert
# ===========================================================================

def _infer_col_type(proj_col: str) -> str:
    """字段名 → DuckDB 类型. 优先 TEXT (兜底), 命中数值/计数关键字才 DOUBLE/BIGINT."""
    n = proj_col.lower()
    # 显式 string-like (优先)
    if any(k in n for k in (
        "code", "name", "abbr", "date", "explain", "_type", "type_code", "cycle",
        "focus", "industry_name", "rating_explain",
    )):
        return "TEXT"
    # compre_rating 是字符串 ("买入"等), 但 compre_rating_num 是 DOUBLE
    if n.endswith("_rating") or n == "compre_rating":
        return "TEXT"
    # 计数 BIGINT
    if n.endswith("_num") or n.endswith("_count") or n.endswith("_rank") or n in ("total_num",):
        return "BIGINT"
    # 其余数值
    return "DOUBLE"


def ensure_table(conn, capability_name: str) -> None:
    """根据 field_map 自动建表."""
    cfg = CAPABILITY_CONFIG[capability_name]
    cols = []
    for proj_col in cfg["field_map"]:
        cols.append(f"{proj_col} {_infer_col_type(proj_col)}")
    pk_str = ", ".join(cfg["pk_cols"])
    sql = f"""
        CREATE TABLE IF NOT EXISTS {cfg["raw_table"]} (
            {','.join(cols)},
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY ({pk_str})
        )
    """
    conn.execute(sql)
    conn.commit()


def _normalize_row(row: dict, field_map: dict) -> dict:
    """aif10 字段名 → 项目字段名."""
    out = {}
    for proj_col, aif10_col in field_map.items():
        v = row.get(aif10_col)
        # 日期统一转 ISO (aif10 给 '2026-03-31 00:00:00')
        if v and "date" in proj_col and isinstance(v, str):
            v = v[:10]  # 截到 YYYY-MM-DD
        out[proj_col] = v
    return out


def sync_capability(
    capability_name: str,
    *,
    page_size: int = 500,
    max_pages: int = 0,
    concurrent: bool = True,
    concurrency: int = 5,
) -> dict:
    """同步一个 capability. 返回 {capability, rows, elapsed_s}."""
    cfg = CAPABILITY_CONFIG[capability_name]
    from aif10_scraper import fetch_all_pages, fetch_all_pages_concurrent
    from services.db import get_conn

    started_at = utc_now_iso()
    t0 = time.time()
    last_progress_at = 0.0

    def _progress(page: int, total_pages: int, rows_so_far: int) -> None:
        nonlocal last_progress_at
        now = time.time()
        if page <= 1 or page >= total_pages or now - last_progress_at >= 10:
            logger.info(
                "[aif10/%s] page=%s/%s rows=%s elapsed=%.1fs",
                capability_name,
                page,
                total_pages,
                rows_so_far,
                now - t0,
            )
            last_progress_at = now

    fetcher = fetch_all_pages_concurrent if concurrent else fetch_all_pages
    fetch_kwargs = dict(
        report_name=cfg["report_name"],
        page_size=page_size,
        max_pages=max_pages,
        sort_columns=cfg.get("sort_columns", ""),
        sort_types=cfg.get("sort_types", ""),
        progress_callback=_progress,
    )
    if concurrent:
        fetch_kwargs["concurrency"] = concurrency
    rows = fetcher(
        **fetch_kwargs,
    )
    if not rows:
        return {
            "capability": capability_name,
            "rows": 0,
            "fetch_mode": "concurrent" if concurrent else "sync",
            "elapsed_s": round(time.time() - t0, 2),
        }

    # 字段映射
    normalized = [_normalize_row(r, cfg["field_map"]) for r in rows]

    # 上数据库
    conn = get_conn()
    try:
        ensure_table(conn, capability_name)
        proj_cols = list(cfg["field_map"].keys())
        write_t0 = time.time()
        write_method = "duckdb_registered_dataframe"
        try:
            import pandas as pd

            # get_conn() 可能返回 DuckConn 包装器 (无 register/unregister, 只有底层 raw 连接有,
            # 见 source_watermarks.py:180 同款 hasattr 防御) — 此前直接 conn.register 恒 AttributeError,
            # 被下面 except 静默吞掉退化成 executemany (功能对但每次都慢, 2026-07-07 冒烟测试抓到)。
            raw = conn.raw if hasattr(conn, "raw") else conn
            payload_df = pd.DataFrame(normalized, columns=proj_cols)
            raw.register("__aif10_payload", payload_df)
            col_sql = ",".join(proj_cols)
            conn.execute(
                f"INSERT OR REPLACE INTO {cfg['raw_table']} ({col_sql}) "
                f"SELECT {col_sql} FROM __aif10_payload"
            )
            raw.unregister("__aif10_payload")
        except Exception as exc:
            write_method = f"executemany_fallback:{type(exc).__name__}"
            placeholders = ",".join(["?"] * len(proj_cols))
            sql = (
                f"INSERT OR REPLACE INTO {cfg['raw_table']} "
                f"({','.join(proj_cols)}) VALUES ({placeholders})"
            )
            payload = [tuple(r.get(c) for c in proj_cols) for r in normalized]
            conn.executemany(sql, payload)
        conn.commit()
        write_elapsed = time.time() - write_t0

        # P0.1 schema_version: 标记当前是 baseline
        try:
            from services.schema_versions import record_actual_version
            record_actual_version(conn, cfg["raw_table"], "v1")
        except Exception:
            pass
        record_pipeline_run(
            conn,
            run_id=f"sync_aif10_{capability_name}_{int(t0)}",
            pipeline_name=f"sync_aif10_{capability_name}",
            status="success",
            started_at=started_at,
            ended_at=utc_now_iso(),
            duration_s=time.time() - t0,
            commit_sha=git_commit_sha(),
            input_tables=[],
            output_tables=[cfg["raw_table"]],
            perf_summary={
                "capability": capability_name,
                "report_name": cfg["report_name"],
                "raw_table": cfg["raw_table"],
                "rows": len(normalized),
                "page_size": int(page_size),
                "max_pages": int(max_pages),
                "fetch_mode": "concurrent" if concurrent else "sync",
                "concurrency": int(concurrency) if concurrent else 1,
                "write_method": write_method,
                "write_elapsed_s": round(write_elapsed, 3),
            },
        )
    finally:
        conn.close()

    elapsed = time.time() - t0
    logger.info(
        f"[aif10/{capability_name}] {cfg['report_name']}: "
        f"{len(normalized)} 行 / {elapsed:.1f}s → {cfg['raw_table']} "
        f"(write={write_elapsed:.1f}s {write_method})"
    )
    return {
        "capability": capability_name,
        "report_name": cfg["report_name"],
        "raw_table": cfg["raw_table"],
        "rows": len(normalized),
        "fetch_mode": "concurrent" if concurrent else "sync",
        "concurrency": int(concurrency) if concurrent else 1,
        "write_method": write_method,
        "write_elapsed_s": round(write_elapsed, 2),
        "elapsed_s": round(elapsed, 2),
    }


# ===========================================================================
# 5 个 sync_xxx 函数 (updater STEPS 调用)
# ===========================================================================

def sync_valuation_quantile() -> dict:
    return sync_capability("valuation_quantile")


def sync_peer_valuation() -> dict:
    return sync_capability("peer_valuation")


# sync_forecast_consensus 已删 2026-06-28 (G5 退役 forecast_consensus)


def summary() -> dict:
    return {
        "capabilities": list(CAPABILITY_CONFIG.keys()),
        "n": len(CAPABILITY_CONFIG),
    }
