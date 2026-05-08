"""数据完整性审计 — P0.2 (2026-04-28).

跟现有 audit.py 区别:
- audit.py: 业务层对齐审计 (raw vs holdings vs current_relationship 跨表的口径一致性)
- data_audit.py (本): 单表内在完整性 (PK 重复 / NULL / 缺失天数 / 异常值)

为什么需要:
- 数据问题不在数据获取阶段暴露, 就会传染下游 fact / mart 层 → 模型错 → 评分错
- 现有项目 70 张派生表, 没有单表层面的"PK 是否重复 / 关键列是否 NULL / 时间字段是否
  断档"自动巡检
- 用户启动后端时 / 跑完 sync 后, 应该一眼看见"哪些表有数据问题, 严重度多少"

设计:
- AUDIT_RULES 字典声明每张关键表的检查规则 (PK / not_null / date_field / value_check)
- run_audit_table(name) 单表跑, 返回 {ok, table, n_rows, issues: [{level, msg}]}
- run_audit_all() 跑全部
- 持久化到 mart_data_audit_report 历史表 (跟 mart_audit_snapshot_state 不同, 那个是
  业务层对齐, 这个是表内完整性)

不强求 70 张表都写规则. 先 12 张关键表, 其他可逐步加.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("cm-api.data_audit")


# ===========================================================================
# 审计规则声明
# ===========================================================================

# 每张表:
#   pk: 联合主键, 检查唯一性
#   not_null: 必须非空的列
#   date_field: 时间字段, 检查近 90 天 distinct 天数 (低于阈值告警)
#   date_min_distinct_90d: 近 90 天最少 distinct 日期数 (默认 30)
#   value_checks: list[(SQL bool 表达式, 描述)]
AUDIT_RULES: dict[str, dict] = {
    # ===== raw 层 =====
    "raw_lhb_daily": {
        "pk": ["trade_date", "stock_code", "rank_reason"],
        "not_null": ["trade_date", "stock_code", "rank_reason"],
        "date_field": "trade_date",
        "date_min_distinct_90d": 40,  # 60 交易日的 70%
        "value_checks": [
            ("close_price IS NULL OR close_price > 0", "收盘价应 > 0"),
            ("turnover IS NULL OR turnover >= 0", "成交额应 >= 0"),
        ],
    },
    "raw_qfii_holding_quarterly": {
        "pk": ["report_date", "stock_code", "holder_name"],
        "not_null": ["report_date", "stock_code", "holder_name"],
        "date_field": "report_date",
        "date_min_distinct_90d": 0,  # 季报数据, 90 天不一定有
    },
    "raw_margin_daily": {
        "pk": ["trade_date", "stock_code", "market"],
        "not_null": ["trade_date", "stock_code", "market"],
        "date_field": "trade_date",
        "date_min_distinct_90d": 40,
        "value_checks": [
            ("rz_balance IS NULL OR rz_balance >= 0", "融资余额 >= 0"),
            ("rq_balance IS NULL OR rq_balance >= 0", "融券余额 >= 0"),
        ],
    },
    # P7 (2026-04-28): market_raw_holdings 整表退役.
    # 新 canonical fact_top10_holder_period 由 tdxhub.holders 写入,
    # A/H 拆分 + holder_set + is_exit_row + source_tier; 审计规则相应升级.
    "fact_top10_holder_period": {
        "pk": ["stock_code", "report_date", "holder_set", "source",
               "is_exit_row", "holder_rank", "row_seq", "share_class"],
        "not_null": ["stock_code", "report_date", "holder_set",
                     "holder_rank", "holder_name", "source", "source_tier"],
        "date_field": "report_date",
        "date_min_distinct_90d": 0,  # 季报
        "value_checks": [
            ("holder_rank BETWEEN 1 AND 21", "股东排名 1-21 (21=其他合计)"),
            ("shares_approx IS NULL OR shares_approx >= 0", "持股数 >= 0"),
            ("hold_ratio_float IS NULL OR hold_ratio_float BETWEEN 0 AND 105",
             "占流通股比 0-105% (>100% 仅 A/H 边界容忍)"),
            ("source_tier IN (1, 2, 3)", "source_tier 合法"),
            ("holder_set IN ('free', 'all')", "holder_set 合法"),
        ],
    },
    "raw_tdx_f10_holder_research": {
        "pk": ["stock_code", "raw_hash"],
        "not_null": ["stock_code", "raw_hash", "raw_text", "fetched_at"],
        "date_field": "fetched_at",
        "date_min_distinct_90d": 0,  # raw 层不保证均匀分布
    },
    "raw_gpcw_financial": {
        "pk": ["stock_code", "report_date"],
        "not_null": ["stock_code", "report_date"],
        "date_field": "report_date",
        "date_min_distinct_90d": 0,  # 季报
    },

    # ===== fact 层 =====
    "fact_institution_event": {
        "pk": ["holder_name", "stock_code", "report_date"],
        "not_null": ["holder_name", "stock_code", "report_date", "event_type"],
        "date_field": "notice_date",
        "date_min_distinct_90d": 0,  # 事件型, 集中在披露窗口
    },
    "fact_lhb_event": {
        "pk": ["trade_date", "stock_code"],
        "not_null": ["trade_date", "stock_code"],
        "date_field": "trade_date",
        "date_min_distinct_90d": 30,
    },
    "fact_jgdy_event": {
        "pk": ["stock_code", "notice_date"],
        "not_null": ["stock_code", "notice_date"],
        "date_field": "notice_date",
        "date_min_distinct_90d": 30,
    },

    # ===== mart 层 =====
    "mart_current_relationship": {
        "pk": ["stock_code", "holder_name"],
        "not_null": ["stock_code", "holder_name"],
        "value_checks": [
            ("hold_ratio IS NULL OR hold_ratio >= 0", "持股比例 >= 0"),
        ],
    },
    "mart_institution_profile": {
        "pk": ["holder_name"],
        "not_null": ["holder_name"],
        "value_checks": [
            ("event_count IS NULL OR event_count >= 0", "事件数 >= 0"),
        ],
    },
    "mart_stock_trend": {
        "pk": ["stock_code"],
        "not_null": ["stock_code"],
    },
    "mart_daily_recommendation": {
        "pk": ["trade_date", "stock_code"],
        "not_null": ["trade_date", "stock_code"],
        "date_field": "trade_date",
        "date_min_distinct_90d": 30,
    },

    # ===== dim 层 =====
    "dim_active_a_stock": {
        "pk": ["stock_code"],
        "not_null": ["stock_code"],
    },
    "dim_stock_tdx_industry": {
        "pk": ["stock_code"],
        "not_null": ["stock_code"],
    },
    "dim_trading_calendar": {
        "pk": ["trade_date"],
        "not_null": ["trade_date"],
    },
}


# ===========================================================================
# 单表审计
# ===========================================================================

def _safe_count(conn, sql: str) -> int:
    """跑一条 COUNT 查询, 失败返回 -1."""
    try:
        r = conn.execute(sql).fetchone()
        return int(r[0]) if r else 0
    except Exception as exc:
        logger.debug(f"audit query 失败: {sql} → {exc}")
        return -1


def audit_table(conn, table_name: str) -> dict:
    """单表审计."""
    result = {
        "table": table_name,
        "ok": True,
        "n_rows": 0,
        "issues": [],
    }
    rules = AUDIT_RULES.get(table_name)
    if not rules:
        result["skipped"] = True
        result["issues"].append({"level": "info", "msg": "无审计规则声明"})
        return result

    # 表存在性
    try:
        n_rows = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        result["n_rows"] = n_rows
    except Exception as exc:
        result["ok"] = False
        result["issues"].append({"level": "error", "msg": f"表不存在或不可查: {exc}"})
        return result

    if n_rows == 0:
        result["issues"].append({"level": "warn", "msg": "表为空 (n_rows = 0)"})

    # PK 唯一性
    pk = rules.get("pk") or []
    if pk and n_rows > 0:
        pk_str = ", ".join(pk)
        dup = _safe_count(conn, f"""
            SELECT COUNT(*) FROM (
                SELECT {pk_str} FROM {table_name}
                WHERE { ' AND '.join(f'{c} IS NOT NULL' for c in pk) }
                GROUP BY {pk_str}
                HAVING COUNT(*) > 1
            )
        """)
        if dup > 0:
            result["ok"] = False
            result["issues"].append({
                "level": "error",
                "msg": f"PK 重复 {dup} 组 (键={pk_str})",
            })

    # NOT NULL
    for col in rules.get("not_null") or []:
        if n_rows > 0:
            nulls = _safe_count(conn, f"SELECT COUNT(*) FROM {table_name} WHERE {col} IS NULL")
            if nulls > 0:
                result["ok"] = False
                result["issues"].append({
                    "level": "error",
                    "msg": f"{col} 有 {nulls} 行 NULL",
                })

    # 时间字段缺失天数
    date_field = rules.get("date_field")
    min_distinct = rules.get("date_min_distinct_90d", 0)
    if date_field and min_distinct > 0 and n_rows > 0:
        # raw_*  date 是 'YYYYMMDD' 文本, mart_*  date 是 'YYYY-MM-DD'.
        # 用宽松 LIKE 比较: 取近 90 天对比当前日期 (字符串比较 work for both).
        from datetime import date, timedelta
        cutoff_iso = (date.today() - timedelta(days=90)).isoformat()
        cutoff_compact = cutoff_iso.replace("-", "")
        unique_dates = _safe_count(conn, f"""
            SELECT COUNT(DISTINCT {date_field}) FROM {table_name}
            WHERE {date_field} >= '{cutoff_iso}' OR {date_field} >= '{cutoff_compact}'
        """)
        if unique_dates >= 0 and unique_dates < min_distinct:
            result["issues"].append({
                "level": "warn",
                "msg": f"近 90 天 distinct {date_field} 仅 {unique_dates} 天 (期望 ≥{min_distinct}), 可能有缺漏",
            })

    # value_checks
    for expr, desc in rules.get("value_checks") or []:
        if n_rows > 0:
            bad = _safe_count(conn, f"SELECT COUNT(*) FROM {table_name} WHERE NOT ({expr})")
            if bad > 0:
                result["issues"].append({
                    "level": "warn",
                    "msg": f"{desc}: {bad} 行违反 ({expr})",
                })

    # 总评估
    result["ok"] = not any(i["level"] == "error" for i in result["issues"])
    return result


def audit_all(conn) -> list[dict]:
    out = []
    for table_name in AUDIT_RULES.keys():
        try:
            out.append(audit_table(conn, table_name))
        except Exception as exc:
            out.append({
                "table": table_name,
                "ok": False,
                "n_rows": 0,
                "issues": [{"level": "error", "msg": f"审计跑挂: {exc}"}],
            })
    return out


# ===========================================================================
# 持久化
# ===========================================================================

def ensure_report_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mart_data_audit_report (
            run_id        BIGINT,
            run_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            n_tables      INTEGER,
            n_ok          INTEGER,
            n_warn        INTEGER,
            n_error       INTEGER,
            details_json  VARCHAR
        )
    """)
    # 索引: 拿最近一次
    conn.execute("CREATE INDEX IF NOT EXISTS idx_data_audit_run_at ON mart_data_audit_report(run_at DESC)")
    conn.commit()


def save_audit_report(conn, results: list[dict]) -> int:
    ensure_report_table(conn)
    n_total = len(results)
    n_ok = sum(1 for r in results if not r.get("issues"))
    n_warn = sum(
        1 for r in results
        if any(i["level"] == "warn" for i in r.get("issues", []))
        and not any(i["level"] == "error" for i in r.get("issues", []))
    )
    n_error = sum(
        1 for r in results
        if any(i["level"] == "error" for i in r.get("issues", []))
    )
    # run_id 用 epoch ms
    import time as _t
    run_id = int(_t.time() * 1000)
    conn.execute("""
        INSERT INTO mart_data_audit_report (run_id, n_tables, n_ok, n_warn, n_error, details_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [
        run_id, n_total, n_ok, n_warn, n_error,
        json.dumps(results, ensure_ascii=False, default=str),
    ])
    conn.commit()
    return run_id


def load_last_audit_report(conn) -> dict | None:
    try:
        row = conn.execute("""
            SELECT run_id, CAST(run_at AS VARCHAR) AS run_at, n_tables, n_ok, n_warn, n_error, details_json
            FROM mart_data_audit_report
            ORDER BY run_at DESC LIMIT 1
        """).fetchone()
        if not row:
            return None
        return {
            "run_id": row[0],
            "run_at": row[1],
            "n_tables": row[2],
            "n_ok": row[3],
            "n_warn": row[4],
            "n_error": row[5],
            "details": json.loads(row[6]) if row[6] else [],
        }
    except Exception as exc:
        logger.debug(f"load_last_audit_report 失败 (表可能未建): {exc}")
        return None


def summary() -> dict:
    return {
        "n_rules": len(AUDIT_RULES),
        "rules_by_layer": {
            "raw": sum(1 for k in AUDIT_RULES if k.startswith("raw_")),
            "fact": sum(1 for k in AUDIT_RULES if k.startswith("fact_")),
            "mart": sum(1 for k in AUDIT_RULES if k.startswith("mart_")),
            "dim": sum(1 for k in AUDIT_RULES if k.startswith("dim_")),
        },
    }
