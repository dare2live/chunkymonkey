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
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("migrate-holders")

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
from services.db import init_db, DB_PATH  # noqa: E402


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


def _to_legacy_change(row: pd.Series) -> str:
    return CHANGE_STATUS_TO_LEGACY.get(row.get("change_status") or "", "")


def _to_legacy_ratio(row: pd.Series) -> float | None:
    """当 holder_set='free' 用 hold_ratio_float, 否则 total."""

    if row.get("holder_set") == "free":
        return row.get("hold_ratio")  # parser already filled this
    return row.get("hold_ratio")


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
    init_db()
    src = duckdb.connect(args.source, read_only=True)

    # ----- 1. raw_tdx_f10_holder_research -----
    log.info("step 1: raw_tdx_f10_holder_research")
    raw_df = src.execute("""
        select stock_code, stock_name, market, fetched_at, raw_len as bytes_len,
               raw_hash, server, raw_text,
               case
                 when raw_text like '%灵通V9.0%' then 'a_lingtong'
                 when raw_text like '%通达信沪深京F10%' then 'b_shsjz'
                 when raw_text like '%港澳资讯%' then 'a_other'
                 else 'unknown'
               end as f10_format
        from raw_text
    """).fetchdf()
    raw_df["page_update_date"] = None  # could regex out of raw_text later
    raw_df["parser_version"] = "v1"
    if args.limit:
        raw_df = raw_df.head(args.limit)
    log.info("  raw rows: %d", len(raw_df))

    tgt = duckdb.connect(args.target)
    tgt.register("raw_in", raw_df)
    n_before = tgt.execute("select count(*) from raw_tdx_f10_holder_research").fetchone()[0]
    tgt.execute("""
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
    tgt.unregister("raw_in")
    n_after = tgt.execute("select count(*) from raw_tdx_f10_holder_research").fetchone()[0]
    log.info("  inserted %d (total %d)", n_after - n_before, n_after)

    # ----- 2. dim_holder_alias seed -----
    log.info("step 2: dim_holder_alias seed (%d entries)", len(ALIAS_SEED))
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    alias_df = pd.DataFrame(
        [{"alias": a, "canonical_name": c, "category": cat, "note": None,
          "created_at": now_iso} for a, c, cat in ALIAS_SEED]
    )
    tgt.register("alias_in", alias_df)
    tgt.execute("""
        insert into dim_holder_alias(alias, canonical_name, category, note, created_at)
        select alias, canonical_name, category, note, created_at from alias_in
        where alias not in (select alias from dim_holder_alias)
    """)
    tgt.unregister("alias_in")
    alias_count = tgt.execute("select count(*) from dim_holder_alias").fetchone()[0]
    log.info("  dim_holder_alias rows now: %d", alias_count)

    # ----- 3. fact_top10_holder_period -----
    log.info("step 3: fact_top10_holder_period (此步是大头, ~474k 行)")
    holders_df = src.execute("""
        select stock_code, stock_name, market, report_date, holder_set,
               holder_rank, row_seq, holder_name, share_class,
               shares_text, shares_approx, shares_precision, hold_ratio,
               holder_type_or_nature, change_status, change_shares_text,
               change_shares_approx, is_exit_row, is_secondary_class,
               page_update_date, source, raw_hash, fetched_at
        from holders
    """).fetchdf()
    if args.limit:
        holders_df = holders_df.head(args.limit)
    log.info("  source holders rows: %d", len(holders_df))

    # back-compat 列
    holders_df["hold_amount"] = holders_df["shares_approx"].astype("float64")
    holders_df["hold_ratio_float"] = holders_df.apply(
        lambda r: r["hold_ratio"] if r["holder_set"] == "free" else None, axis=1
    )
    holders_df["hold_ratio_total"] = holders_df.apply(
        lambda r: r["hold_ratio"] if r["holder_set"] == "all" else None, axis=1
    )
    holders_df["hold_ratio_legacy"] = holders_df["hold_ratio"]
    holders_df["hold_change"] = holders_df.apply(_to_legacy_change, axis=1)
    holders_df["hold_change_num"] = holders_df["change_shares_approx"].astype("float64")
    holders_df["hold_market_cap"] = None
    holders_df["holder_type"] = holders_df["holder_type_or_nature"]
    holders_df["share_nature"] = holders_df["holder_type_or_nature"]
    holders_df["notice_date"] = None  # tdxhub F10 不直接给; 留空
    holders_df["effective_date"] = None  # 由后续 mart 层补
    holders_df["created_at"] = holders_df["fetched_at"]
    holders_df["source_tier"] = 1
    # alias 解析
    alias_map = dict(tgt.execute(
        "select alias, canonical_name from dim_holder_alias"
    ).fetchall())
    holders_df["holder_name_norm"] = holders_df["holder_name"].map(
        lambda n: alias_map.get(n, n)
    )
    # is_secondary_class 可能是 numpy bool; 转 python bool
    holders_df["is_secondary_class"] = holders_df["is_secondary_class"].astype(bool)
    holders_df["is_exit_row"] = holders_df["is_exit_row"].astype(bool)

    tgt.register("holders_in", holders_df)
    n_before = tgt.execute("select count(*) from fact_top10_holder_period").fetchone()[0]
    tgt.execute("""
        insert into fact_top10_holder_period(
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
        select stock_code, stock_name, market, report_date, holder_set,
               holder_rank, row_seq,
               holder_name, holder_name_norm, share_class,
               is_secondary_class, is_exit_row,
               shares_text, shares_approx, shares_precision, hold_amount,
               hold_ratio_float, hold_ratio_total, hold_ratio_legacy,
               hold_market_cap, holder_type, share_nature,
               change_status, change_shares_text, change_shares_approx,
               hold_change, hold_change_num,
               notice_date, effective_date, page_update_date,
               source, source_tier, raw_hash, fetched_at, created_at
        from holders_in
        where (stock_code, report_date, holder_set, source, is_exit_row, holder_rank, row_seq, share_class)
          not in (
            select stock_code, report_date, holder_set, source, is_exit_row, holder_rank, row_seq, share_class
            from fact_top10_holder_period
        )
    """)
    tgt.unregister("holders_in")
    n_after = tgt.execute("select count(*) from fact_top10_holder_period").fetchone()[0]
    log.info("  fact_top10_holder_period: inserted %d (total %d)", n_after - n_before, n_after)

    # ----- 4. fact_controlling_shareholder -----
    log.info("step 4: fact_controlling_shareholder")
    if "controlling" in [t[0] for t in src.execute("show tables").fetchall()]:
        ctrl_df = src.execute("""
            select stock_code, stock_name, market,
                   primary_shareholder_label as primary_label,
                   primary_shareholder_name as primary_name,
                   primary_shareholder_ratio as primary_ratio,
                   primary_shareholder_raw as primary_raw,
                   actual_controller_name as actual_name,
                   actual_controller_ratio as actual_ratio,
                   actual_controller_raw as actual_raw,
                   page_update_date, source, raw_hash, fetched_at
            from controlling
        """).fetchdf()
        ctrl_df["source_tier"] = 1
        tgt.register("ctrl_in", ctrl_df)
        n_before = tgt.execute("select count(*) from fact_controlling_shareholder").fetchone()[0]
        tgt.execute("""
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
        tgt.unregister("ctrl_in")
        n_after = tgt.execute("select count(*) from fact_controlling_shareholder").fetchone()[0]
        log.info("  inserted %d (total %d)", n_after - n_before, n_after)

    # ----- 5. fact_shareholder_plan -----
    log.info("step 5: fact_shareholder_plan")
    plans_df = src.execute("""
        select stock_code, stock_name, market,
               announce_date, subject, direction, progress,
               start_date, end_date,
               target_shares_text, target_shares,
               target_ratio_text, target_ratio,
               reason, narrative,
               page_update_date, source, raw_hash, fetched_at
        from plans
    """).fetchdf()
    plans_df["source_tier"] = 1
    plans_df["plan_seq"] = (
        plans_df.groupby(["stock_code", "raw_hash"]).cumcount() + 1
    )
    tgt.register("plans_in", plans_df)
    n_before = tgt.execute("select count(*) from fact_shareholder_plan").fetchone()[0]
    # 幂等: 跳过 (stock_code, raw_hash) 已存在的批次
    tgt.execute("""
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
        from plans_in
        where (stock_code, raw_hash) not in (
          select distinct stock_code, raw_hash from fact_shareholder_plan
          where raw_hash is not null
        )
    """)
    tgt.unregister("plans_in")
    n_after = tgt.execute("select count(*) from fact_shareholder_plan").fetchone()[0]
    log.info("  inserted %d (total %d)", n_after - n_before, n_after)

    # ----- 6. fact_shareholder_trade -----
    log.info("step 6: fact_shareholder_trade")
    trades_df = src.execute("""
        select stock_code, stock_name, market,
               change_date, holder_name,
               shares_before_text, shares_before,
               shares_change_text, shares_change,
               shares_after_text, shares_after,
               ratio_after, change_type,
               page_update_date, source, raw_hash, fetched_at
        from trades
    """).fetchdf()
    trades_df["source_tier"] = 1
    trades_df["holder_name_norm"] = trades_df["holder_name"].map(
        lambda n: alias_map.get(n, n)
    )
    trades_df["trade_seq"] = (
        trades_df.groupby(["stock_code", "raw_hash"]).cumcount() + 1
    )
    tgt.register("trades_in", trades_df)
    n_before = tgt.execute("select count(*) from fact_shareholder_trade").fetchone()[0]
    # 幂等: 跳过 (stock_code, raw_hash) 已存在的批次
    tgt.execute("""
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
        from trades_in
        where (stock_code, raw_hash) not in (
          select distinct stock_code, raw_hash from fact_shareholder_trade
          where raw_hash is not null
        )
    """)
    tgt.unregister("trades_in")
    n_after = tgt.execute("select count(*) from fact_shareholder_trade").fetchone()[0]
    log.info("  inserted %d (total %d)", n_after - n_before, n_after)

    # ----- 收尾 -----
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
    src.close()
    tgt.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
