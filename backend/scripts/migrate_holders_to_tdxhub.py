"""一次性迁移：把 /tmp/tdxhub_universe.duckdb (tdxhub.holders 跑出的 5179 只 99.6%
覆盖结果) 灌入 chunky-monkey-v2/data/smartmoney.duckdb 的新 fact_* 表.

新表由 backend/services/db.py 已经声明好 schema. 本脚本只负责:

1. 从 /tmp/tdxhub_universe.duckdb 读 (raw_text, holders, periods,
   controlling, plans, trades).
2. 把 raw_text → raw_tdx_f10_holder_research.
3. 把 holders → fact_top10_holder_period (含 back-compat 列 hold_amount /
   hold_ratio / hold_change / hold_change_num / created_at).
4. 把 controlling → fact_controlling_shareholder.
5. 把 plans → fact_shareholder_plan.
6. 把 trades → fact_shareholder_trade.
7. 用 30 条常见简称 seed dim_holder_alias.
8. 给 fact_top10_holder_period.holder_name_norm 应用 alias 字典.

幂等：表内同 (stock_code, raw_hash) 已存在则跳过.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("migrate-holders")

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
from services.db import init_db, DB_PATH  # noqa: E402
from services.holder_availability import backfill_holder_period_availability  # noqa: E402


# 常见股东简称 → 工商全称
ALIAS_SEED: list[tuple[str, str, str | None]] = [
    ("汇金公司", "中央汇金投资有限责任公司", "国家队"),
    ("财政部", "中华人民共和国财政部", "国家队"),
    ("社保基金会", "全国社会保障基金理事会", "国家队"),
    ("社保基金理事会", "全国社会保障基金理事会", "国家队"),
    ("证金公司", "中国证券金融股份有限公司", "国家队"),
    ("中证金融", "中国证券金融股份有限公司", "国家队"),
    ("中央汇金", "中央汇金投资有限责任公司", "国家队"),
    ("汇金资管", "中央汇金资产管理有限责任公司", "国家队"),
    ("中国工商银行", "中国工商银行股份有限公司", None),
    ("工商银行", "中国工商银行股份有限公司", None),
    ("中国建设银行", "中国建设银行股份有限公司", None),
    ("建设银行", "中国建设银行股份有限公司", None),
    ("中国农业银行", "中国农业银行股份有限公司", None),
    ("农业银行", "中国农业银行股份有限公司", None),
    ("中国银行", "中国银行股份有限公司", None),
    ("交通银行", "交通银行股份有限公司", None),
    ("平安银行", "平安银行股份有限公司", None),
    ("招商银行", "招商银行股份有限公司", None),
    ("浦发银行", "上海浦东发展银行股份有限公司", None),
    ("国家集成电路", "国家集成电路产业投资基金股份有限公司", "央企/产业基金"),
    ("大基金", "国家集成电路产业投资基金股份有限公司", "央企/产业基金"),
    ("香港中央结算", "香港中央结算有限公司", "外资/北向"),
    ("北向资金", "香港中央结算有限公司", "外资/北向"),
    ("中央结算代理人", "香港中央结算（代理人）有限公司", "外资/H股"),
    ("HKSCC", "香港中央结算（代理人）有限公司", "外资/H股"),
    ("中信集团", "中国中信集团有限公司", "央企"),
    ("中信金控", "中国中信金融控股有限公司", "央企"),
    ("中石化集团", "中国石油化工集团有限公司", "央企"),
    ("中石油集团", "中国石油天然气集团有限公司", "央企"),
    ("国资委", "国务院国有资产监督管理委员会", "国家队"),
]


CHANGE_STATUS_TO_LEGACY = {
    "新进": "新进",
    "增持": "加仓",
    "减持": "减仓",
    "不变": "",
    "退出": "退出",      # 老 schema 没有, 用 '退出' 字面
    "未知": "",
}


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _limit_clause(limit: int) -> str:
    return f" LIMIT {int(limit)}" if limit and int(limit) > 0 else ""


def _change_status_sql(expr: str = "change_status") -> str:
    cases = " ".join(
        f"WHEN {expr} = {_sql_string(status)} THEN {_sql_string(legacy)}"
        for status, legacy in CHANGE_STATUS_TO_LEGACY.items()
    )
    return f"CASE {cases} ELSE '' END"


def _source_table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    return table_name in {
        row[0]
        for row in conn.execute("SHOW TABLES FROM src_db").fetchall()
    }


def _insert_alias_seed(conn: duckdb.DuckDBPyConnection, now_iso: str) -> int:
    before = conn.execute("SELECT COUNT(*) FROM dim_holder_alias").fetchone()[0]
    conn.executemany(
        """
        INSERT INTO dim_holder_alias(alias, canonical_name, category, note, created_at)
        SELECT ?, ?, ?, ?, ?
        WHERE NOT EXISTS (
            SELECT 1 FROM dim_holder_alias WHERE alias = ?
        )
        """,
        [(alias, canon, cat, None, now_iso, alias) for alias, canon, cat in ALIAS_SEED],
    )
    after = conn.execute("SELECT COUNT(*) FROM dim_holder_alias").fetchone()[0]
    return after - before


def run_migration(source: str, target: str, *, limit: int = 0) -> dict[str, int]:
    init_db()
    tgt = duckdb.connect(target)
    counts: dict[str, int] = {}
    limit_sql = _limit_clause(limit)
    try:
        tgt.execute(f"ATTACH {_sql_string(source)} AS src_db (READ_ONLY)")

        # ----- 1. raw_tdx_f10_holder_research -----
        log.info("step 1: raw_tdx_f10_holder_research")
        n_before = tgt.execute("select count(*) from raw_tdx_f10_holder_research").fetchone()[0]
        tgt.execute(f"""
            INSERT INTO raw_tdx_f10_holder_research(
              stock_code, stock_name, market, fetched_at, page_update_date,
              raw_text, raw_hash, bytes_len, server, f10_format, parser_version
            )
            SELECT stock_code, stock_name, market,
                   cast(fetched_at as timestamp), NULL AS page_update_date,
                   raw_text, raw_hash, bytes_len, server, f10_format, parser_version
            FROM (
              SELECT stock_code, stock_name, market, fetched_at, raw_len as bytes_len,
                     raw_hash, server, raw_text,
                     CASE
                       WHEN raw_text like '%灵通V9.0%' THEN 'a_lingtong'
                       WHEN raw_text like '%通达信沪深京F10%' THEN 'b_shsjz'
                       WHEN raw_text like '%港澳资讯%' THEN 'a_other'
                       ELSE 'unknown'
                     END as f10_format,
                     'v1' as parser_version
              FROM src_db.raw_text
              {limit_sql}
            ) raw_in
            WHERE (stock_code, raw_hash) NOT IN (
              SELECT stock_code, raw_hash FROM raw_tdx_f10_holder_research
            )
        """)
        n_after = tgt.execute("select count(*) from raw_tdx_f10_holder_research").fetchone()[0]
        counts["raw"] = n_after - n_before
        log.info("  inserted %d (total %d)", counts["raw"], n_after)

        # ----- 2. dim_holder_alias seed -----
        log.info("step 2: dim_holder_alias seed (%d entries)", len(ALIAS_SEED))
        now_iso = datetime.utcnow().isoformat(timespec="seconds")
        counts["alias"] = _insert_alias_seed(tgt, now_iso)
        alias_count = tgt.execute("select count(*) from dim_holder_alias").fetchone()[0]
        log.info("  dim_holder_alias rows now: %d", alias_count)

        # ----- 3. fact_top10_holder_period -----
        log.info("step 3: fact_top10_holder_period")
        n_before = tgt.execute("select count(*) from fact_top10_holder_period").fetchone()[0]
        change_sql = _change_status_sql("h.change_status")
        tgt.execute(f"""
            INSERT INTO fact_top10_holder_period(
              stock_code, stock_name, market, report_date, holder_set,
              holder_rank, row_seq,
              holder_name, holder_name_norm, share_class,
              is_secondary_class, is_exit_row,
              shares_text, shares_approx, shares_precision, hold_amount,
              hold_ratio_float, hold_ratio_total, hold_ratio,
              hold_market_cap, holder_type, share_nature,
              change_status, change_shares_text, change_shares_approx,
              hold_change, hold_change_num,
              notice_date, effective_date, page_update_date,
              source, source_tier, raw_hash, fetched_at, created_at
            )
            SELECT h.stock_code, h.stock_name, h.market, h.report_date, h.holder_set,
                   h.holder_rank, h.row_seq,
                   h.holder_name, COALESCE(a.canonical_name, h.holder_name), h.share_class,
                   CAST(h.is_secondary_class AS BOOLEAN), CAST(h.is_exit_row AS BOOLEAN),
                   h.shares_text, h.shares_approx, h.shares_precision,
                   CAST(h.shares_approx AS DOUBLE),
                   CASE WHEN h.holder_set = 'free' THEN h.hold_ratio ELSE NULL END,
                   CASE WHEN h.holder_set = 'all' THEN h.hold_ratio ELSE NULL END,
                   h.hold_ratio,
                   NULL, h.holder_type_or_nature, h.holder_type_or_nature,
                   h.change_status, h.change_shares_text, h.change_shares_approx,
                   {change_sql}, CAST(h.change_shares_approx AS DOUBLE),
                   NULL, NULL, h.page_update_date,
                   h.source, 1, h.raw_hash, h.fetched_at, h.fetched_at
            FROM (
              SELECT * FROM src_db.holders
              {limit_sql}
            ) h
            LEFT JOIN dim_holder_alias a ON a.alias = h.holder_name
            WHERE (
              h.stock_code, h.report_date, h.holder_set, h.source,
              h.is_exit_row, h.holder_rank, h.row_seq, h.share_class
            ) NOT IN (
              SELECT stock_code, report_date, holder_set, source,
                     is_exit_row, holder_rank, row_seq, share_class
              FROM fact_top10_holder_period
            )
        """)
        n_after = tgt.execute("select count(*) from fact_top10_holder_period").fetchone()[0]
        counts["holders"] = n_after - n_before
        log.info("  fact_top10_holder_period: inserted %d (total %d)", counts["holders"], n_after)
        availability = backfill_holder_period_availability(tgt)
        log.info("  holder PIT availability: %s", availability)

        # ----- 4. fact_controlling_shareholder -----
        counts["controlling"] = 0
        if _source_table_exists(tgt, "controlling"):
            log.info("step 4: fact_controlling_shareholder")
            n_before = tgt.execute("select count(*) from fact_controlling_shareholder").fetchone()[0]
            tgt.execute(f"""
                INSERT INTO fact_controlling_shareholder(
                  stock_code, stock_name, market,
                  primary_label, primary_name, primary_ratio, primary_raw,
                  actual_name, actual_ratio, actual_raw,
                  page_update_date, source, source_tier, raw_hash, fetched_at
                )
                SELECT c.stock_code, c.stock_name, c.market,
                       c.primary_shareholder_label, c.primary_shareholder_name,
                       c.primary_shareholder_ratio, c.primary_shareholder_raw,
                       c.actual_controller_name, c.actual_controller_ratio,
                       c.actual_controller_raw,
                       c.page_update_date, c.source, 1, c.raw_hash, c.fetched_at
                FROM (
                  SELECT * FROM src_db.controlling
                  {limit_sql}
                ) c
                WHERE (c.stock_code, c.source) NOT IN (
                  SELECT stock_code, source FROM fact_controlling_shareholder
                )
            """)
            n_after = tgt.execute("select count(*) from fact_controlling_shareholder").fetchone()[0]
            counts["controlling"] = n_after - n_before
            log.info("  inserted %d (total %d)", counts["controlling"], n_after)

        # ----- 5. fact_shareholder_plan -----
        log.info("step 5: fact_shareholder_plan")
        n_before = tgt.execute("select count(*) from fact_shareholder_plan").fetchone()[0]
        tgt.execute(f"""
            INSERT INTO fact_shareholder_plan(
              stock_code, stock_name, market,
              announce_date, subject, direction, progress,
              start_date, end_date,
              target_shares_text, target_shares,
              target_ratio_text, target_ratio,
              reason, narrative,
              page_update_date, source, source_tier, raw_hash, fetched_at, plan_seq
            )
            SELECT p.stock_code, p.stock_name, p.market,
                   p.announce_date, p.subject, p.direction, p.progress,
                   p.start_date, p.end_date,
                   p.target_shares_text, p.target_shares,
                   p.target_ratio_text, p.target_ratio,
                   p.reason, p.narrative,
                   p.page_update_date, p.source, 1, p.raw_hash, p.fetched_at,
                   ROW_NUMBER() OVER (
                     PARTITION BY stock_code, raw_hash
                     ORDER BY announce_date, subject, direction, progress,
                              start_date, end_date, target_shares_text, reason, narrative
                   ) AS plan_seq
            FROM (
              SELECT * FROM src_db.plans
              {limit_sql}
            ) p
            WHERE (p.stock_code, p.raw_hash) NOT IN (
              SELECT DISTINCT stock_code, raw_hash FROM fact_shareholder_plan
              WHERE raw_hash IS NOT NULL
            )
        """)
        n_after = tgt.execute("select count(*) from fact_shareholder_plan").fetchone()[0]
        counts["plans"] = n_after - n_before
        log.info("  inserted %d (total %d)", counts["plans"], n_after)

        # ----- 6. fact_shareholder_trade -----
        log.info("step 6: fact_shareholder_trade")
        n_before = tgt.execute("select count(*) from fact_shareholder_trade").fetchone()[0]
        tgt.execute(f"""
            INSERT INTO fact_shareholder_trade(
              stock_code, stock_name, market,
              change_date, holder_name, holder_name_norm,
              shares_before_text, shares_before,
              shares_change_text, shares_change,
              shares_after_text, shares_after,
              ratio_after, change_type,
              page_update_date, source, source_tier, raw_hash, fetched_at, trade_seq
            )
            SELECT t.stock_code, t.stock_name, t.market,
                   t.change_date, t.holder_name,
                   COALESCE(a.canonical_name, t.holder_name),
                   t.shares_before_text, t.shares_before,
                   t.shares_change_text, t.shares_change,
                   t.shares_after_text, t.shares_after,
                   t.ratio_after, t.change_type,
                   t.page_update_date, t.source, 1, t.raw_hash, t.fetched_at,
                   ROW_NUMBER() OVER (
                     PARTITION BY t.stock_code, t.raw_hash
                     ORDER BY t.change_date, t.holder_name, t.change_type,
                              t.shares_change_text, t.ratio_after
                   ) AS trade_seq
            FROM (
              SELECT * FROM src_db.trades
              {limit_sql}
            ) t
            LEFT JOIN dim_holder_alias a ON a.alias = t.holder_name
            WHERE (t.stock_code, t.raw_hash) NOT IN (
              SELECT DISTINCT stock_code, raw_hash FROM fact_shareholder_trade
              WHERE raw_hash IS NOT NULL
            )
        """)
        n_after = tgt.execute("select count(*) from fact_shareholder_trade").fetchone()[0]
        counts["trades"] = n_after - n_before
        log.info("  inserted %d (total %d)", counts["trades"], n_after)

        # ----- summary -----
        log.info("=== summary ===")
        for tbl in ["raw_tdx_f10_holder_research", "fact_top10_holder_period",
                    "fact_controlling_shareholder", "fact_shareholder_plan",
                    "fact_shareholder_trade", "dim_holder_alias"]:
            n = tgt.execute(f"select count(*) from {tbl}").fetchone()[0]
            n_stocks = tgt.execute(
                f"select count(distinct stock_code) from {tbl}"
                if tbl != "dim_holder_alias"
                else f"select count(*) from {tbl}"
            ).fetchone()[0]
            if tbl == "dim_holder_alias":
                log.info("  %s: %d aliases", tbl, n)
            else:
                log.info("  %s: %d rows / %d stocks", tbl, n, n_stocks)
        tgt.commit()
        return counts
    finally:
        tgt.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source", default="/tmp/tdxhub_universe.duckdb")
    p.add_argument("--target", default=str(DB_PATH))
    p.add_argument("--no-backcompat", action="store_true",
                   help="skip filling back-compat columns hold_amount/hold_ratio/hold_change/...")
    p.add_argument("--limit", type=int, default=0,
                   help="cap rows (debug only)")
    args = p.parse_args()

    log.info("source: %s", args.source)
    log.info("target: %s", args.target)
    run_migration(args.source, args.target, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
