"""通用妙想 capability 同步 — P1.5 (2026-04-28).

接通 5 个妙想独家 capability 到 sync step:
- holder_count        股东人数 (RPT_F10_EH_HOLDERNUM, 季)
- valuation_quantile  估值分位 (RPT_STOCKVALUATIONTANTILE, 日)
- peer_valuation      同行估值排名 (RPT_PCF10_INDUSTRY_CVALUE, 季)
- forecast_consensus  一致预期 (RPT_HSF10_RES_ORGRATING, 周)
- financial_history_200q 财务 200 期历史 (RPT_F10_FINANCE_MAINFINADATA, 季, v0 接口)

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
    "holder_count": {
        "report_name": "RPT_F10_EH_HOLDERNUM",
        "raw_table": "raw_aif10_holder_count",
        "pk_cols": ("secucode", "end_date"),
        "field_map": {
            "secucode": "SECUCODE",
            "security_code": "SECURITY_CODE",
            "end_date": "END_DATE",
            "holder_total_num": "HOLDER_TOTAL_NUM",
            "total_num_ratio": "TOTAL_NUM_RATIO",  # 较上期变化 %
            "avg_free_shares": "AVG_FREE_SHARES",
            "avg_freeshares_ratio": "AVG_FREESHARES_RATIO",
            "price": "PRICE",
            "avg_hold_amt": "AVG_HOLD_AMT",
            "hold_focus": "HOLD_FOCUS",
            "hold_ratio_total": "HOLD_RATIO_TOTAL",
        },
        "sort_columns": "END_DATE,SECURITY_CODE",
        "sort_types": "-1,1",
    },
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
    "forecast_consensus": {
        "report_name": "RPT_HSF10_RES_ORGRATING",
        "raw_table": "raw_aif10_forecast_consensus",
        "pk_cols": ("secucode", "date_type_code"),
        "field_map": {
            "secucode": "SECUCODE",
            "security_code": "SECURITY_CODE",
            "security_name_abbr": "SECURITY_NAME_ABBR",
            "date_type_code": "DATE_TYPE_CODE",  # 1=1月内 2=3月内 3=半年内 4=6月内
            "date_type": "DATE_TYPE",
            "compre_rating_num": "COMPRE_RATING_NUM",  # 综合评级值
            "compre_rating": "COMPRE_RATING",  # 综合评级 ("买入"等)
            "rating_org_num": "RATING_ORG_NUM",  # 评级机构数
            "rating_buy_num": "RATING_BUY_NUM",
            "rating_add_num": "RATING_ADD_NUM",
            "rating_neutral_num": "RATING_NEUTRAL_NUM",
            "rating_reduce_num": "RATING_REDUCE_NUM",
            "rating_sale_num": "RATING_SALE_NUM",
        },
        "sort_columns": "",  # 这接口不支持自定义 sort, 传空
        "sort_types": "",
    },
    # NOTE: financial_history_200q 走 v0 接口 (RPT_F10_FINANCE_MAINFINADATA),
    # 跟其他 4 个 v1 接口签名不同, 单独 sync 函数实现
    # 因为 v0 用 sty 参数 + p/ps 分页, 不走 fetch_all_pages 默认路径.
    # 在 sync_financial_history_200q 单独处理.
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

            payload_df = pd.DataFrame(normalized, columns=proj_cols)
            conn.register("__aif10_payload", payload_df)
            col_sql = ",".join(proj_cols)
            conn.execute(
                f"INSERT OR REPLACE INTO {cfg['raw_table']} ({col_sql}) "
                f"SELECT {col_sql} FROM __aif10_payload"
            )
            conn.unregister("__aif10_payload")
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

def sync_holder_count() -> dict:
    return sync_capability("holder_count")


def sync_valuation_quantile() -> dict:
    return sync_capability("valuation_quantile")


def sync_peer_valuation() -> dict:
    return sync_capability("peer_valuation")


def sync_forecast_consensus() -> dict:
    return sync_capability("forecast_consensus")


def sync_financial_history_200q(secucodes: list[str] | None = None, limit: int = 50) -> dict:
    """财报长历史: 走 v1 RPT_F10_FINANCE_MAINFINADATA (实测茅台 102 期 / 165 字段).

    按单股拉 (单股 page_size=200 一次拿全). 默认拉前 limit 只活跃股.
    raw_json 列存原始接口返回 165 字段 (避免静态 schema 跟不上字段变化).
    """
    from aif10_scraper import default_client
    from services.db import get_conn

    conn = get_conn()
    if secucodes is None:
        try:
            rows = conn.execute(
                "SELECT stock_code FROM dim_active_a_stock LIMIT ?",
                [limit],
            ).fetchall()
            secucodes = []
            for r in rows:
                code = r[0]
                if code.startswith(("60", "68", "5")):
                    secucodes.append(f"{code}.SH")
                elif code.startswith(("0", "3")):
                    secucodes.append(f"{code}.SZ")
                elif code.startswith(("4", "8")):
                    secucodes.append(f"{code}.BJ")
        except Exception as exc:
            logger.warning(f"[aif10/financial_history] dim_active_a_stock 读失败: {exc}")
            return {"capability": "financial_history_200q", "rows": 0, "error": str(exc)}

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_aif10_financial_history (
            secucode TEXT NOT NULL,
            report_date TEXT NOT NULL,
            report_type TEXT,
            eps DOUBLE,
            roe_jq DOUBLE,
            roa_jq DOUBLE,
            sale_gpr DOUBLE,
            sale_npr DOUBLE,
            asset_liab_ratio DOUBLE,
            tot_or DOUBLE,
            parent_netprofit DOUBLE,
            tot_or_yoy DOUBLE,
            netprofit_yoy DOUBLE,
            raw_json TEXT,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (secucode, report_date)
        )
    """)

    cli = default_client
    total = 0
    t0 = time.time()
    import json
    for secucode in secucodes:
        try:
            result = cli.get_v1(
                "RPT_F10_FINANCE_MAINFINADATA",
                page=1, page_size=200,
                secucode=secucode,
            )
            payload = []
            for r in (result.get("data") or []):
                report_date = (r.get("REPORT_DATE") or "")[:10]
                if not report_date:
                    continue
                payload.append(
                    (
                        secucode, report_date,
                        r.get("REPORT_TYPE"),
                        r.get("EPSJB"),       # 基本 EPS
                        r.get("ROEJQ"),       # ROE 加权
                        r.get("ROAJQ"),
                        r.get("SALEGPR"),     # 销售毛利率
                        r.get("SALENPR"),     # 销售净利率
                        r.get("ASSETLIABRATIO"),
                        r.get("TOTAL_OPERATE_INCOME"),
                        r.get("PARENT_NETPROFIT"),
                        r.get("YOY_TOTAL_OPERATE_INCOME"),
                        r.get("YOY_PARENT_NETPROFIT"),
                        json.dumps(r, ensure_ascii=False, default=str),
                    )
                )
            if payload:
                conn.executemany(
                    """INSERT OR REPLACE INTO raw_aif10_financial_history
                    (secucode, report_date, report_type, eps, roe_jq, roa_jq,
                     sale_gpr, sale_npr, asset_liab_ratio, tot_or, parent_netprofit,
                     tot_or_yoy, netprofit_yoy, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    payload,
                )
            total += len(payload)
        except Exception as exc:
            logger.warning(f"[aif10/financial_history] {secucode} 失败: {exc}")
            continue
    conn.commit()
    conn.close()

    elapsed = time.time() - t0
    return {
        "capability": "financial_history_200q",
        "report_name": "RPT_F10_FINANCE_MAINFINADATA",
        "raw_table": "raw_aif10_financial_history",
        "secucodes": len(secucodes),
        "rows": total,
        "elapsed_s": round(elapsed, 2),
    }


def summary() -> dict:
    return {
        "capabilities": list(CAPABILITY_CONFIG.keys()) + ["financial_history_200q"],
        "n": len(CAPABILITY_CONFIG) + 1,
    }
