"""Institution-domain runners for the updater pipeline."""

import asyncio
import json
import logging
from datetime import datetime

from routers.updater_runtime import _run_blocking_db_task
from services.gap_queue import (
    mark_current_missing_as,
    reconcile_gap_queue_snapshot,
    summarize_gap_queue,
)

logger = logging.getLogger("cm-api")


def _build_exclusion_set(conn) -> set:
    """构建排除股票代码集合（主数据过滤 + 类别规则 + 手工股票拉黑）"""
    from services.security_master import get_active_a_stock_codes

    excluded = set()
    invalid_master_codes = set()
    manual_rows = conn.execute(
        "SELECT DISTINCT stock_code FROM excluded_stocks WHERE stock_code IS NOT NULL"
    ).fetchall()
    manual_codes = {r["stock_code"] for r in manual_rows if r["stock_code"]}
    excluded.update(manual_codes)

    active_codes = None
    try:
        active_codes = get_active_a_stock_codes(conn)
    except Exception as e:
        logger.warning(f"[排除] 当前A股主数据不可用，回退分类规则: {e}")

    # 加载启用的排除类别
    categories = conn.execute(
        "SELECT category FROM exclusion_categories WHERE enabled = 1"
    ).fetchall()
    enabled_cats = {r["category"] for r in categories}

    # 从 fact_top10_holder_period (canonical, 替代 market_raw_holdings) 获取
    # 所有唯一的 (stock_code, stock_name).
    all_stocks = conn.execute(
        """
        SELECT DISTINCT stock_code, stock_name
          FROM fact_top10_holder_period
         WHERE stock_code IS NOT NULL
           AND holder_set = 'free'
           AND NOT is_secondary_class
           AND NOT is_exit_row
        """
    ).fetchall()

    for row in all_stocks:
        code = row["stock_code"]
        name = row["stock_name"] or ""

        if not code or len(code) != 6 or not code.isdigit():
            invalid_master_codes.add(code)
            excluded.add(code)
            continue

        # 基础有效性：必须出现在当前A股主数据里
        if active_codes is not None and code not in active_codes:
            invalid_master_codes.add(code)
            excluded.add(code)
            continue

        # ST/*ST：按股票名称判断
        if "ST" in enabled_cats and ("ST" in name.upper()):
            excluded.add(code)
            continue

        # 北交所：8/9开头的6位代码
        if "BSE" in enabled_cats and code and len(code) == 6 and code[0] in ("8", "9"):
            excluded.add(code)
            continue

        # 新三板：4开头（包含老三板400开头）
        if code and len(code) == 6 and code[0] == "4":
            if "OTC" in enabled_cats and code.startswith("400"):
                excluded.add(code)
                continue
            if "NEEQ" in enabled_cats:
                excluded.add(code)
                continue

        # B股：200/900开头
        if "B_SHARE" in enabled_cats and code and len(code) == 6:
            if code.startswith("200") or code.startswith("900"):
                excluded.add(code)
                continue

        # 退市股：名称含"退"字
        if "DELISTED" in enabled_cats and "退" in name:
            excluded.add(code)
            continue

    if invalid_master_codes:
        preview = ",".join(sorted(invalid_master_codes)[:10])
        suffix = "..." if len(invalid_master_codes) > 10 else ""
        logger.info(
            f"[排除] 当前A股主数据过滤 {len(invalid_master_codes)} 只无效代码: {preview}{suffix}"
        )

    logger.info(
        f"[排除] 主数据过滤 + 分类规则 + 手工拉黑，共 {len(excluded)} 只股票被排除"
        f"（手工 {len(manual_codes)} 只）"
    )
    return excluded


def _unique_institution_names(inst) -> list:
    inst_id = inst["id"]
    names = [inst["name"]]
    try:
        aliases = json.loads(inst["aliases"] or "[]")
        names.extend([a for a in aliases if a])
    except Exception as e:
        logger.warning(f"[匹配] 机构 {inst_id} 别名解析失败: {e}")

    unique_names = []
    seen_names = set()
    for name in names:
        normalized = str(name or "").strip()
        if not normalized or normalized in seen_names:
            continue
        unique_names.append(normalized)
        seen_names.add(normalized)
    return unique_names


def _step_match_inst_sync(conn) -> int:
    """匹配跟踪机构持仓"""
    _step_match_inst_sync._insert_errors = 0
    institutions = conn.execute(
        "SELECT id, name, aliases FROM inst_institutions WHERE enabled = 1 AND blacklisted = 0 AND merged_into IS NULL"
    ).fetchall()

    if not institutions:
        logger.warning("[匹配] 无跟踪机构")
        return 0

    logger.info(f"[匹配] 加载 {len(institutions)} 个机构")

    # 构建排除集合
    excluded_codes = _build_exclusion_set(conn)

    match_candidates = []
    for inst in institutions:
        inst_id = inst["id"]
        match_candidates.extend((inst_id, name) for name in _unique_institution_names(inst))

    match_rows = []
    global_seen_names = set()
    for inst_id, normalized in match_candidates:
        if normalized in global_seen_names:
            continue
        match_rows.append((len(match_rows), inst_id, normalized))
        global_seen_names.add(normalized)

    if not match_rows:
        logger.warning("[匹配] 无可用机构名称/别名")
        return 0

    conn.execute("DROP TABLE IF EXISTS tmp_inst_match_names")
    conn.execute("""
        CREATE TEMP TABLE tmp_inst_match_names (
            seq INTEGER,
            institution_id TEXT,
            holder_name TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO tmp_inst_match_names VALUES (?, ?, ?)",
        match_rows,
    )

    conn.execute("DROP TABLE IF EXISTS tmp_inst_excluded_codes")
    conn.execute("CREATE TEMP TABLE tmp_inst_excluded_codes (stock_code TEXT)")
    if excluded_codes:
        conn.executemany(
            "INSERT INTO tmp_inst_excluded_codes VALUES (?)",
            [(code,) for code in sorted(excluded_codes)],
        )

    # 清空旧匹配结果并重建（事务保护）
    now = datetime.now().isoformat()

    conn.execute("DROP TABLE IF EXISTS tmp_inst_holdings_rebuild")
    conn.execute("""
        CREATE TEMP TABLE tmp_inst_holdings_rebuild AS
        SELECT institution_id, holder_name, holder_type, stock_code, stock_name,
               report_date, notice_date, notice_date_source, source_notice_date,
               availability_deadline, holder_rank, hold_amount, hold_market_cap,
               hold_ratio, hold_change, hold_change_num, ? AS created_at
        FROM (
            SELECT
                candidate.*,
                ROW_NUMBER() OVER (
                    PARTITION BY holder_name, stock_code, report_date
                    ORDER BY match_seq, notice_source_sort, holder_rank_sort, notice_date DESC, institution_id
                ) AS rn
            FROM (
                SELECT
                    m.seq AS match_seq,
                    m.institution_id,
                    TRIM(r.holder_name) AS holder_name,
                    r.holder_type,
                    TRIM(r.stock_code) AS stock_code,
                    r.stock_name,
                    TRIM(r.report_date) AS report_date,
                    r.notice_date,
                    COALESCE(NULLIF(r.availability_source, ''), 'unknown') AS notice_date_source,
                    CASE WHEN r.availability_source = 'source_notice' THEN r.notice_date ELSE NULL END AS source_notice_date,
                    CASE WHEN r.availability_source = 'regulatory_deadline' THEN r.notice_date ELSE NULL END AS availability_deadline,
                    r.holder_rank,
                    COALESCE(TRY_CAST(r.holder_rank AS INTEGER), 999999) AS holder_rank_sort,
                    CASE
                        WHEN r.availability_source = 'source_notice' THEN 0
                        WHEN r.availability_source = 'page_update_date' THEN 1
                        WHEN r.availability_source = 'fetched_at_observed' THEN 2
                        WHEN r.availability_source = 'regulatory_deadline' THEN 3
                        ELSE 4
                    END AS notice_source_sort,
                    r.hold_amount,
                    r.hold_market_cap,
                    r.hold_ratio,
                    r.hold_change,
                    r.hold_change_num
                FROM fact_top10_holder_period r
                JOIN tmp_inst_match_names m ON TRIM(r.holder_name) = m.holder_name
                LEFT JOIN tmp_inst_excluded_codes x ON TRIM(r.stock_code) = x.stock_code
                WHERE x.stock_code IS NULL
                  AND r.holder_set = 'free'
                  AND NOT r.is_secondary_class
                  AND NOT r.is_exit_row
            ) candidate
        ) deduped
        WHERE rn = 1
    """, (now,))

    total = conn.execute("SELECT COUNT(*) FROM tmp_inst_holdings_rebuild").fetchone()[0]
    if total == 0:
        raise RuntimeError("[匹配] 已尝试写入持仓但重建结果为空")

    duplicate = conn.execute("""
        SELECT holder_name, stock_code, report_date, COUNT(*) AS cnt
        FROM tmp_inst_holdings_rebuild
        GROUP BY holder_name, stock_code, report_date
        HAVING COUNT(*) > 1
        LIMIT 1
    """).fetchone()
    if duplicate:
        raise RuntimeError(
            "[匹配] 重建结果存在重复键: "
            f"{duplicate['holder_name']} {duplicate['stock_code']} {duplicate['report_date']}"
        )

    try:
        conn.execute("DROP INDEX IF EXISTS idx_ih_inst")
        conn.execute("DROP INDEX IF EXISTS idx_ih_stock")
        conn.execute("DROP INDEX IF EXISTS idx_ih_report")
        conn.execute("DROP INDEX IF EXISTS idx_ih_unique_holder_stock_report")
        conn.execute("""
            CREATE OR REPLACE TABLE inst_holdings AS
            SELECT
                CAST(institution_id AS TEXT) AS institution_id,
                CAST(holder_name AS TEXT) AS holder_name,
                CAST(holder_type AS TEXT) AS holder_type,
                CAST(stock_code AS TEXT) AS stock_code,
                CAST(stock_name AS TEXT) AS stock_name,
                CAST(report_date AS TEXT) AS report_date,
                CAST(notice_date AS TEXT) AS notice_date,
                CAST(notice_date_source AS TEXT) AS notice_date_source,
                CAST(source_notice_date AS TEXT) AS source_notice_date,
                CAST(availability_deadline AS TEXT) AS availability_deadline,
                CAST(holder_rank AS INTEGER) AS holder_rank,
                CAST(hold_amount AS DOUBLE) AS hold_amount,
                CAST(hold_market_cap AS DOUBLE) AS hold_market_cap,
                CAST(hold_ratio AS DOUBLE) AS hold_ratio,
                CAST(hold_change AS TEXT) AS hold_change,
                CAST(hold_change_num AS DOUBLE) AS hold_change_num,
                CAST(created_at AS TEXT) AS created_at
            FROM tmp_inst_holdings_rebuild
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ih_inst ON inst_holdings(institution_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ih_stock ON inst_holdings(stock_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ih_report ON inst_holdings(report_date)")
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ih_unique_holder_stock_report
            ON inst_holdings(holder_name, stock_code, report_date)
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info(f"[匹配] 完成: {total} 条持仓记录")
    return total


async def _step_match_inst(conn) -> int:
    """匹配跟踪机构持仓"""
    return await _run_blocking_db_task(_step_match_inst_sync)


async def _step_sync_industry_with_hooks(
    conn,
    *,
    tracked_stock_names,
    should_stop,
    update_step,
    open_conn,
) -> int:
    """通达信行业同步 — 拉取 tdxhy.cfg 并全量 upsert 到 dim_stock_tdx_industry"""
    from services.tdx_industry_client import sync_tdx_industry

    stock_names = tracked_stock_names(conn)
    reconcile_gap_queue_snapshot(conn, stock_names=stock_names, datasets=("industry",), commit=True)

    detail = {
        "industry_sync": {
            "status": "running",
            "updated_rows": 0,
            "source": "",
            "source_degraded": False,
            "before_missing": summarize_gap_queue(conn, datasets=("industry",))["datasets"][0]["unresolved"],
            "after_missing": None,
            "gap_summary": summarize_gap_queue(conn, datasets=("industry",), limit_per_dataset=6)["datasets"][0],
        },
        "block_sync": {
            "status": "pending",
            "member_rows": 0,
            "catalog_rows": 0,
        },
    }

    count = 0

    def _push_progress():
        update_step(
            conn,
            "sync_industry",
            error=json.dumps(detail, ensure_ascii=False),
            records=count,
        )

    should_stop()
    # sync_tdx_industry 是同步函数（TDX 服务器下载 + 本地解析 + executemany），
    # 放到线程池避免阻塞事件循环。DuckDB 连接不跨线程传递，在 executor 内
    # 单独开一个连接，写完后主线程的 conn 无需感知（写入同一个 DB 文件）。
    def _run_in_thread():
        thread_conn = open_conn(timeout=120)
        try:
            return sync_tdx_industry(thread_conn)
        finally:
            thread_conn.close()

    tdx_result = await asyncio.get_event_loop().run_in_executor(None, _run_in_thread)

    count = int(tdx_result.get("rows_upserted") or 0)
    errors = tdx_result.get("errors") or []

    if count == 0:
        mark_current_missing_as(
            conn,
            "industry",
            status="blocked",
            reason="通达信行业源无返回，当前未执行补齐",
            last_error=";".join(errors) or "tdx_industry_source_empty",
            stock_names=stock_names,
            commit=False,
        )
        gap_summary = summarize_gap_queue(conn, datasets=("industry",), limit_per_dataset=6)["datasets"][0]
        detail["industry_sync"] = {
            "status": "blocked",
            "updated_rows": 0,
            "source": tdx_result.get("source", ""),
            "source_degraded": False,
            "before_missing": detail["industry_sync"]["before_missing"],
            "after_missing": gap_summary["unresolved"],
            "reason": "通达信行业源无返回，当前未执行补齐",
            "errors": errors,
            "gap_summary": gap_summary,
        }
        conn.commit()
        _push_progress()
        logger.warning("[通达信行业] 未获取到数据")
        return 0

    reconcile_gap_queue_snapshot(conn, stock_names=stock_names, datasets=("industry",), commit=False)
    gap_summary = summarize_gap_queue(conn, datasets=("industry",), limit_per_dataset=6)["datasets"][0]
    detail["industry_sync"] = {
        "status": "partial" if gap_summary["unresolved"] else "success",
        "updated_rows": count,
        "source": tdx_result.get("source", ""),
        "source_degraded": False,
        "before_missing": detail["industry_sync"]["before_missing"],
        "after_missing": gap_summary["unresolved"],
        "fetched_at": tdx_result.get("fetched_at"),
        "l1_count": tdx_result.get("l1_count"),
        "l2_count": tdx_result.get("l2_count"),
        "l3_count": tdx_result.get("l3_count"),
        "errors": errors,
        "gap_summary": gap_summary,
    }
    conn.commit()
    _push_progress()
    logger.info(
        f"[通达信行业] 完成: {count} 只股票, "
        f"L1={tdx_result.get('l1_count')}/L2={tdx_result.get('l2_count')}/L3={tdx_result.get('l3_count')}"
    )
    return count


def _step_build_industry_stat_sync(conn, should_stop=None) -> int:
    """计算机构在各行业 (TDX 一二三级) 的表现统计。

    [审计 4.4 标注] 口径：**当前行业**
    事件现任所属股票 → dim_stock_tdx_industry 的当前 tdx_l{1,2,3}。
    这意味着：股票被行业重分类时，历史事件会被映射到最新行业，
    机构过去在某一行业积累的真实能力会被后来的行业映射改写。

    Phase 3b-3 之前曾用 fact_institution_event_industry_snapshot 表存事件时点的
    行业快照, 已合并入 fact_institution_event 主表 (sw_level* 字段); 行业重分类
    历史影响请改读 fact_institution_event 内的 sw_level 字段或 dim_stock_tdx_industry.
    前端/解释层请明确标注"当前行业口径".
    """
    now = datetime.now().isoformat()
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM mart_institution_industry_stat")

        if should_stop is not None:
            should_stop()
        rows = conn.execute("""
            WITH event_industry AS (
                SELECT e.institution_id AS institution_id,
                       'level1' AS industry_level,
                       i.tdx_l1 AS tdx_code,
                       i.tdx_l1_name AS industry_name,
                       e.gain_30d, e.gain_60d, e.gain_90d, e.gain_120d,
                       e.max_drawdown_30d, e.max_drawdown_60d
                FROM fact_institution_event e
                INNER JOIN inst_institutions inst ON inst.id = e.institution_id
                INNER JOIN dim_stock_tdx_industry i ON i.stock_code = e.stock_code
                WHERE inst.enabled = 1
                  AND inst.blacklisted = 0
                  AND inst.merged_into IS NULL
                  AND i.tdx_l1 IS NOT NULL AND i.tdx_l1 != ''
                  AND i.tdx_l1_name IS NOT NULL AND i.tdx_l1_name != ''
                UNION ALL
                SELECT e.institution_id AS institution_id,
                       'level2' AS industry_level,
                       i.tdx_l2 AS tdx_code,
                       i.tdx_l2_name AS industry_name,
                       e.gain_30d, e.gain_60d, e.gain_90d, e.gain_120d,
                       e.max_drawdown_30d, e.max_drawdown_60d
                FROM fact_institution_event e
                INNER JOIN inst_institutions inst ON inst.id = e.institution_id
                INNER JOIN dim_stock_tdx_industry i ON i.stock_code = e.stock_code
                WHERE inst.enabled = 1
                  AND inst.blacklisted = 0
                  AND inst.merged_into IS NULL
                  AND i.tdx_l2 IS NOT NULL AND i.tdx_l2 != ''
                  AND i.tdx_l2_name IS NOT NULL AND i.tdx_l2_name != ''
                UNION ALL
                SELECT e.institution_id AS institution_id,
                       'level3' AS industry_level,
                       i.tdx_l3 AS tdx_code,
                       i.tdx_l3_name AS industry_name,
                       e.gain_30d, e.gain_60d, e.gain_90d, e.gain_120d,
                       e.max_drawdown_30d, e.max_drawdown_60d
                FROM fact_institution_event e
                INNER JOIN inst_institutions inst ON inst.id = e.institution_id
                INNER JOIN dim_stock_tdx_industry i ON i.stock_code = e.stock_code
                WHERE inst.enabled = 1
                  AND inst.blacklisted = 0
                  AND inst.merged_into IS NULL
                  AND i.tdx_l3 IS NOT NULL AND i.tdx_l3 != ''
                  AND i.tdx_l3_name IS NOT NULL AND i.tdx_l3_name != ''
            )
            SELECT institution_id,
                   industry_level,
                   tdx_code,
                   industry_name,
                   COUNT(*) as cnt,
                   AVG(gain_30d) as avg30, AVG(gain_60d) as avg60,
                   AVG(gain_90d) as avg90, AVG(gain_120d) as avg120,
                   SUM(CASE WHEN gain_30d > 0 THEN 1 WHEN gain_30d IS NOT NULL THEN 0 ELSE NULL END)
                       * 100.0 / NULLIF(SUM(CASE WHEN gain_30d IS NOT NULL THEN 1 ELSE 0 END), 0) as wr30,
                   SUM(CASE WHEN gain_60d > 0 THEN 1 WHEN gain_60d IS NOT NULL THEN 0 ELSE NULL END)
                       * 100.0 / NULLIF(SUM(CASE WHEN gain_60d IS NOT NULL THEN 1 ELSE 0 END), 0) as wr60,
                   SUM(CASE WHEN gain_90d > 0 THEN 1 WHEN gain_90d IS NOT NULL THEN 0 ELSE NULL END)
                       * 100.0 / NULLIF(SUM(CASE WHEN gain_90d IS NOT NULL THEN 1 ELSE 0 END), 0) as wr90,
                   SUM(CASE WHEN gain_30d > 0 OR gain_60d > 0 THEN 1 WHEN gain_30d IS NOT NULL OR gain_60d IS NOT NULL THEN 0 ELSE NULL END)
                       * 100.0 / NULLIF(SUM(CASE WHEN gain_30d IS NOT NULL OR gain_60d IS NOT NULL THEN 1 ELSE 0 END), 0) as wr_total,
                   AVG(max_drawdown_30d) as dd30, AVG(max_drawdown_60d) as dd60
            FROM event_industry
            GROUP BY institution_id, industry_level, tdx_code, industry_name
            HAVING cnt >= 1
        """).fetchall()

        write_rows = [
            (
                r["institution_id"], r["industry_level"], r["industry_name"], r["tdx_code"], r["cnt"],
                r["avg30"], r["avg60"], r["avg90"], r["avg120"],
                r["wr30"], r["wr60"], r["wr90"], r["wr_total"],
                r["dd30"], r["dd60"], now,
            )
            for r in rows
        ]
        if write_rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO mart_institution_industry_stat
                (institution_id, industry_level, industry_name, tdx_code, sample_events,
                 avg_gain_30d, avg_gain_60d, avg_gain_90d, avg_gain_120d,
                 win_rate_30d, win_rate_60d, win_rate_90d, total_win_rate,
                 max_drawdown_30d, max_drawdown_60d, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                write_rows,
            )
        count = len(write_rows)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info(f"[行业统计] 完成: {count} 条 (基于 dim_stock_tdx_industry)")
    return count
