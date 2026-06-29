#!/usr/bin/env python3
"""Watermark SLA updater + sync gap auto-alert.

ChunkyMonkey 交付标准 #1 数据管理: watermark 实填 + sync gap auto-alert.

每个 data source 跑 actual max date 跟 watermark.last_data_date 对比:
- actual > watermark → 自动 update watermark
- actual - current_date > SLA threshold → alert (log + JSON report)
- watermark stale > SLA threshold → alert (即使没数据 update)

调用 ways:
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py --dry-run
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py --json-output /tmp/sla.json

被 daily_update.sh Step 1 调用.

SLA per source_tier (Codex R26 architecture audit doc §4.d 设计):
- tier 1 (tdxhub/miaoxiang 主源): SLA 1 trading day
- tier 2 (aif10 二次源): SLA 2 trading days
- tier 3 (akshare 补充): SLA 3 trading days
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.duck_adapter import connect as duck_connect

DEFAULT_SMARTMONEY_DB = REPO_ROOT / "data" / "smartmoney.duckdb"
DEFAULT_MARKET_DB = REPO_ROOT / "data" / "market.duckdb"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "audit" / "watermark_sla_latest.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("watermark_sla")

# SLA threshold by source_tier (trading days)
SLA_DAYS = {1: 1, 2: 2, 3: 3}

# Override: 季度数据 (报告期 quarterly) 单独配置 SLA, 不走 tier
# 季报披露窗: Q1=4/30 / Q2=8/31 / Q3=10/31 / Q4=4/30(年报)
# 真实 publish lag 1-2 month 后, SLA 100d 覆盖最坏情况
# rule-compliance: ok evidence=a-share-disclosure-window
SLA_DAYS_OVERRIDE = {
    # financial_gpcw_8q override 已删 2026-06-28 (fact_financial_derived U4 退役, DATA_SOURCE_QUERIES 条目同删)
    "holders_top10_float": 100,      # top10 股东季报
    "qfii_holding_quarterly": 100,   # qfii 季报
}

# data_domain → actual table + date column
DATA_SOURCE_QUERIES = {
    "kline_daily": {
        "db": "market",
        "query": "SELECT MAX(CAST(date AS VARCHAR)) FROM v_price_kline_qfq WHERE adjust='qfq' AND freq='daily'",
    },
    # xdxr SLA 条目已删 (2026-06-27 通达信全删 单元6: price_kline_tdxhub_adjustment_event 物删; 复权走 tushare adj_factor)
    # financial_gpcw_8q SLA 条目已删 2026-06-28 (fact_financial_derived U4 退役; 财务新鲜度走 tushare
    #   sync:balancesheet/income/fina_indicator 等 by_report_period 域, _sync_registry_queries 自动覆盖)
    "lhb_daily": {
        "db": "smartmoney",
        # 2026-06-28: fact_lhb_event U5 退役 → repoint raw_lhb_daily (aif10 龙虎榜 raw, trade_date)
        "query": "SELECT MAX(CAST(trade_date AS VARCHAR)) FROM raw_lhb_daily",
    },
    # institution_survey 手动 SLA 域已删 2026-06-28 (批2 切 tushare): stk_surv 在 sync_registry 注册,
    #   新鲜度由 sync:stk_surv 自动域 (_sync_registry_queries, db=tushare_raw 查 raw_tushare_stk_surv) 覆盖, 不手维护。
    "holders_top10_float": {
        "db": "smartmoney",
        "query": "SELECT MAX(CAST(report_date AS VARCHAR)) FROM fact_top10_holder_period",
    },
    "industry_dc": {
        # 2026-06-23 单一供应商=东财 (Stage②): 查东财 serving 表 dim_stock_dc_industry.updated_at
        #   (daily_update Step 2.96c build_dc_industry_view 每日刷新; 东财行业=申万对齐)。
        "db": "smartmoney",
        "query": "SELECT CAST(MAX(updated_at) AS VARCHAR) FROM dim_stock_dc_industry",
    },
    # stock_blocks 域已删 (2026-06-23): 原查通达信表, 源退役; 申万行业新鲜度由 industry_sw 域跟踪。
}


def _sync_registry_queries() -> dict[str, dict]:
    """sync:* 域 SLA 查询从 sync_registry.yaml 自动生成 — registry 驱动, 不手维护.

    复审 HIGH (2026-06-11): sync:* 域无 DATA_SOURCE_QUERIES 条目 → actual=None →
    永不告警 = audit 防线对全部新数据域盲区 (静默腐烂同型)。registry 注册即自动入防线。
    """
    import yaml

    out: dict[str, dict] = {}
    reg_path = REPO_ROOT / "backend" / "config" / "sync_registry.yaml"
    try:
        reg = yaml.safe_load(reg_path.read_text(encoding="utf-8")) or {}
        for name, spec in (reg.get("domains") or {}).items():
            mode = spec.get("batch_mode")
            if spec.get("freshness_no_probe"):
                # 季报/事件域 (forecast/income/dividend): ann_date 淡季数周无新数据=正常, 日频 SLA 会
                # 误报疲劳 (mythos§10); 完整性靠 gap drain 覆盖, 不做日频新鲜度探测。
                out[f"sync:{name}"] = {"db": "tushare_raw", "no_probe": spec.get("freshness_no_probe")}
            elif mode in ("by_trade_date", "by_date_range"):
                # 2026-06-22 P0-6: 日期列从 registry 读 (freshness_date_column 缺省 trade_date) —
                # 旧硬编 trade_date 撞 report_rc(report_date)/stk_surv(surv_date) 等无该列域
                # → BinderException 被 try/except 吞 = SLA 永久盲区。
                date_col = spec.get("freshness_date_column", "trade_date")
                out[f"sync:{name}"] = {
                    "db": "tushare_raw",
                    "query": f'SELECT MAX(CAST("{date_col}" AS VARCHAR)) FROM "{spec["target_table"]}"',
                    "sla_days": spec.get("freshness_sla_trading_days"),
                }
            else:
                # 季度 (by_ts_code) / 日历 (full_refresh) 无日频新鲜度语义 — 显式标注, 不静默当 OK
                out[f"sync:{name}"] = {"db": "tushare_raw", "no_probe": f"batch_mode={mode}"}
    except Exception as e:  # noqa: BLE001 — registry 读失败必须可见, 不能让全部 sync 域回到盲区
        log.warning(f"sync_registry SLA 条目生成失败 (sync:* 域回到盲区!): {e}")
    return out


def _query_actual_max_date(conns: dict, queries: dict, data_domain: str) -> str | None:
    spec = queries.get(data_domain)
    if not spec or spec.get("no_probe"):
        return None
    conn = conns.get(spec["db"])
    if conn is None:
        return None  # 库不可达 — 调用方按 DB_LOCKED_UNVERIFIED 显式标注
    try:
        r = conn.execute(spec["query"]).fetchone()
        return r[0] if r and r[0] else None
    except Exception as e:
        log.warning(f"  query failed for {data_domain}: {e}")
        return None


def _days_since(date_str: str | None, today: date) -> int | None:
    if not date_str:
        return None
    s = str(date_str)[:10]
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        try:
            d = datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    return (today - d).days


def main() -> int:
    parser = argparse.ArgumentParser(description="Watermark SLA auto-update + alert")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--smartmoney-db", default=str(DEFAULT_SMARTMONEY_DB))
    parser.add_argument("--market-db", default=str(DEFAULT_MARKET_DB))
    args = parser.parse_args()

    today = date.today()  # rule-compliance: ok evidence=SLA staleness age 度量(对wall-clock计天龄, 非交易决策)
    log.info(f"=== watermark SLA check @ {today} ===")

    smart_conn = duck_connect(args.smartmoney_db, read_only=False)  # need write
    market_conn = duck_connect(args.market_db, read_only=True)
    # tushare_raw: 回填链持写锁时 read_only 也连不上 (DuckDB 排他) — 显式置 None,
    # 对应域标 DB_LOCKED_UNVERIFIED 而非静默 OK
    raw_conn = None
    try:
        from services.database_manifest import get_database_manifest

        raw_conn = duck_connect(str(get_database_manifest().path_for("tushare_raw")), read_only=True)
    except Exception as e:  # noqa: BLE001 — 锁竞争是回填期常态, 显式降级不挡 SLA 主流程
        log.warning(f"tushare_raw 不可达 (回填链占锁?): {e}")
    queries = {**DATA_SOURCE_QUERIES, **_sync_registry_queries()}
    conns = {"market": market_conn, "smartmoney": smart_conn, "tushare_raw": raw_conn}
    try:
        watermark_rows = smart_conn.execute(
            "SELECT data_domain, source_name, source_tier, last_data_date, updated_at "
            "FROM mart_data_source_watermark ORDER BY data_domain, source_name"
        ).fetchall()
        log.info(f"  watermark rows: {len(watermark_rows)}")

        results: list[dict] = []
        n_update = 0
        n_alert = 0
        for row in watermark_rows:
            data_domain, source_name, source_tier, watermark_date, updated_at = row

            actual_date = _query_actual_max_date(conns, queries, data_domain)
            actual_days = _days_since(actual_date, today)
            watermark_days = _days_since(watermark_date, today)
            # SLA 优先序: registry per-domain > 季度 override > tier 默认
            qspec = queries.get(data_domain) or {}
            sla = (qspec.get("sla_days")
                   or SLA_DAYS_OVERRIDE.get(data_domain)
                   or SLA_DAYS.get(source_tier, 3))

            status = "OK"
            alert = False
            if qspec.get("no_probe"):
                status = "NO_PROBE_RULE"  # 季度/日历域: 无日频语义, 显式标注非 OK
            elif not qspec:
                status = "NO_QUERY_MAPPING"  # 既不在手维护表也不在 registry — 防线缺口可见化
            elif qspec.get("db") == "tushare_raw" and raw_conn is None:
                status = "DB_LOCKED_UNVERIFIED"  # 回填期暂态, 不告警但不许伪装 OK

            # 1. watermark out of date vs actual
            if actual_date and watermark_date:
                aw = _days_since(actual_date, today)
                ww = _days_since(watermark_date, today)
                if aw is not None and ww is not None and ww > aw:
                    status = "STALE_WATERMARK"  # auto-fix
                    if not args.dry_run:
                        smart_conn.execute(
                            "UPDATE mart_data_source_watermark "
                            "SET last_data_date = ?, updated_at = CURRENT_TIMESTAMP "
                            "WHERE data_domain = ? AND source_name = ?",
                            [actual_date, data_domain, source_name],
                        )
                        n_update += 1
                        log.info(f"  [UPDATE] {data_domain}/{source_name}: {watermark_date} → {actual_date}")

            # 2. actual data stale vs SLA
            if actual_days is not None and actual_days > sla:
                # 注意: 周末 / 节假日 不 alert. 简单 SLA 不区分.
                if actual_days > sla + 3:  # 3 day buffer for weekend
                    status = "DATA_STALE_VS_SLA"
                    alert = True
                    n_alert += 1
                    log.warning(
                        f"  [ALERT] {data_domain}/{source_name}: actual {actual_date} "
                        f"({actual_days}d ago) > SLA {sla}d (tier {source_tier})"
                    )

            results.append({
                "data_domain": data_domain,
                "source_name": source_name,
                "source_tier": source_tier,
                "watermark_date": str(watermark_date) if watermark_date else None,
                "actual_date": actual_date,
                "actual_days_ago": actual_days,
                "watermark_days_ago": watermark_days,
                "sla_days": sla,
                "status": status,
                "alert": alert,
            })

        # registry 域 ∪ watermark 行: 从未成功 sync 的注册域没有 watermark 行 →
        # 不进上面循环 = 注册后一直没跑成会永远隐形。显式补 NEVER_SYNCED 行。
        seen_domains = {r[0] for r in watermark_rows}
        for qd, qspec in queries.items():
            if qd.startswith("sync:") and qd not in seen_domains:
                results.append({
                    "data_domain": qd, "source_name": "tushare", "source_tier": 2,
                    "watermark_date": None, "actual_date": None, "actual_days_ago": None,
                    "watermark_days_ago": None, "sla_days": qspec.get("sla_days"),
                    "status": "NEVER_SYNCED", "alert": False,
                })

        # Write JSON report
        Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump({
                "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "today": str(today),
                "dry_run": args.dry_run,
                "n_updates": n_update,
                "n_alerts": n_alert,
                "sources": results,
            }, f, ensure_ascii=False, indent=2)
        log.info(f"=== SLA check done: {n_update} watermark updated, {n_alert} alerts ===")
        log.info(f"  Report: {args.json_output}")
        return 2 if n_alert > 0 else 0
    finally:
        try:
            market_conn.close()
        except Exception as e:  # rule-compliance: ok evidence=cleanup-best-effort
            log.warning(f"market_conn close failed: {e}")
        try:
            smart_conn.close()
        except Exception as e:  # rule-compliance: ok evidence=cleanup-best-effort
            log.warning(f"smart_conn close failed: {e}")
        if raw_conn is not None:
            try:
                raw_conn.close()
            except Exception as e:  # rule-compliance: ok evidence=cleanup-best-effort
                log.warning(f"raw_conn close failed: {e}")


if __name__ == "__main__":
    sys.exit(main())
