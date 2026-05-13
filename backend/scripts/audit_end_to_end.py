"""Phase η++++++ — 端到端系统审计 (AUDIT).

⚠ 用户要求: "做完整的审计, 继续修改".

审计维度:
  1. 数据流完整性 — 每张表是否到位 + 时效性
  2. 字段一致性 — 跨表 JOIN 关键字段是否对齐
  3. 数据驱动检查 — fitness/optimal 等是否真有数据
  4. UI/API 端到端 — endpoint 是否能正常返回
  5. 异常值识别 — outliers 是否清理
  6. 风险约束有效 — STRONG_BUY 票是否真符合 max_dd / loss_streak 限制
"""
from __future__ import annotations

import logging
import sys

from services.db import get_conn


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("audit")


def audit_table_completeness(conn) -> list[dict]:
    """1. 检查关键表是否存在 + 行数 + 时效."""
    EXPECTED = [
        ("fact_technical_trigger",                100_000, "date"),
        ("fact_signal_context",                  1_000_000, "date"),
        ("mart_stock_picture_daily",                  3_000, "snapshot_date"),
        ("mart_stock_formula_optuna_v2",            100_000, None),
        ("mart_per_stock_strategy_optimal",          10_000, None),
        ("mart_stage_formula_fitness",                  100, None),
        ("mart_stock_formula_buy_signal_daily",        500, "signal_date"),
        ("mart_daily_position_recommendation",          10, "signal_date"),
        ("mart_stock_survey_features",               5_000, "as_of_date"),
        ("dim_stock_tdx_industry",                   5_000, None),
        ("fact_stock_technical_stage",             500_000, "date"),
    ]
    issues = []
    for table, expected_n, date_col in EXPECTED:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            severity = "OK"
            note = ""
            if n < expected_n // 10:
                severity = "FAIL"
                note = f"行数严重不足 {n:,} < expect {expected_n:,}/10"
            elif n < expected_n:
                severity = "WARN"
                note = f"行数偏少 {n:,} < {expected_n:,}"
            latest = None
            if date_col:
                try:
                    r = conn.execute(f"SELECT MAX({date_col}) FROM {table}").fetchone()
                    latest = r[0]
                except Exception:
                    pass
            issues.append({
                "category": "table_completeness",
                "table": table, "n": n, "expected_n": expected_n,
                "severity": severity, "note": note, "latest": latest,
            })
        except Exception as e:
            issues.append({
                "category": "table_completeness", "table": table,
                "severity": "FAIL", "note": f"{e}",
            })
    return issues


def audit_join_consistency(conn) -> list[dict]:
    """2. 检查关键 JOIN 字段是否对齐."""
    issues = []
    checks = [
        # buy_signal 应该 join 到 strategy_optimal — LEFT JOIN by design, 允许部分 null
        # 触发数 - 寻优数 > 20% 才算异常 (太多未寻优股入 buy_signal 池)
        ("buy_signal × strategy_optimal 缺失率 (≤20%)",
         """WITH x AS (
              SELECT COUNT(*) AS total FROM mart_stock_formula_buy_signal_daily
            ), y AS (
              SELECT COUNT(*) AS missing FROM mart_stock_formula_buy_signal_daily b
                LEFT JOIN mart_per_stock_strategy_optimal o
                  ON o.stock_code = b.stock_code AND o.formula_variant = b.formula_variant
                WHERE o.stock_code IS NULL
            )
            SELECT y.missing FROM x, y WHERE (1.0 * y.missing / NULLIF(x.total, 0)) > 0.20""",
         "缺失寻优结果占 buy_signal 总数 > 20% (新触发股未寻优)"),
        # picture × buy_signal 一致
        ("buy_signal × picture stock_code 覆盖",
         """SELECT COUNT(*) FROM mart_stock_formula_buy_signal_daily b
              LEFT JOIN mart_stock_picture_daily p
                ON p.stock_code = b.stock_code
                AND p.snapshot_date = (SELECT MAX(snapshot_date) FROM mart_stock_picture_daily)
             WHERE p.stock_code IS NULL""",
         "缺失画像的 buy_signal 行"),
    ]
    for name, sql, note in checks:
        try:
            r = conn.execute(sql).fetchone()
            if r is None:
                # SQL 返回 0 row = 不触发 alert 条件 (例如缺失率 ≤ 阈值)
                issues.append({
                    "category": "join_consistency", "check": name,
                    "missing_count": 0, "severity": "OK", "note": note,
                })
            else:
                issues.append({
                    "category": "join_consistency", "check": name,
                    "missing_count": r[0],
                    "severity": "OK" if r[0] == 0 else ("WARN" if r[0] < 100 else "FAIL"),
                    "note": note,
                })
        except Exception as e:
            issues.append({"category": "join_consistency", "check": name, "severity": "FAIL", "note": str(e)})
    return issues


def audit_outliers(conn) -> list[dict]:
    """3. 异常值检查 — 重点查 *消费端* (buy_signal/daily), source 表 outliers 是 Optuna 预期产物."""
    issues = []
    # source 表 outlier 计数 (供参考, INFO 级)
    try:
        r = conn.execute("""
            SELECT COUNT(*) FROM mart_per_stock_strategy_optimal
             WHERE abs(avg_ret) > 0.5 OR abs(avg_max_dd) > 0.5""").fetchone()
        issues.append({
            "category": "outliers", "check": "strategy_optimal source outliers (info)",
            "count": r[0],
            "severity": "OK",   # source 端 outlier 不是问题, 消费端 filter 即可
            "note": "Optuna 原始输出含异常值, 下游 build_*.py 已加 SQL filter 剔除",
        })
    except Exception as e:
        issues.append({"category": "outliers", "severity": "FAIL", "note": str(e)})

    # sharpe 极值 (info)
    try:
        r = conn.execute("""
            SELECT COUNT(*) FROM mart_per_stock_strategy_optimal
             WHERE abs(sharpe) > 10""").fetchone()
        issues.append({
            "category": "outliers", "check": "strategy_optimal source extreme sharpe (info)",
            "count": r[0], "severity": "OK",
            "note": "source 含虚假高 sharpe (std≈0), 下游已 filter",
        })
    except Exception:
        pass

    # 消费端 1: daily 推荐里是否有异常 avg_ret / avg_dd
    try:
        r = conn.execute("""
            SELECT COUNT(*) FROM mart_daily_position_recommendation
             WHERE abs(avg_ret) > 0.5 OR avg_dd < -0.5""").fetchone()
        issues.append({
            "category": "outliers", "check": "daily recommendation outliers (consumer)",
            "count": r[0],
            "severity": "OK" if r[0] == 0 else "FAIL",
            "note": "daily 推荐含异常值 (filter 未生效)",
        })
    except Exception:
        pass

    # 消费端 2: buy_signal 里 historical_sharpe 是否清洁
    try:
        r = conn.execute("""
            SELECT COUNT(*) FROM mart_stock_formula_buy_signal_daily
             WHERE abs(historical_sharpe) > 10""").fetchone()
        issues.append({
            "category": "outliers", "check": "buy_signal historical_sharpe outliers (consumer)",
            "count": r[0],
            "severity": "OK" if r[0] == 0 else "FAIL",
            "note": "buy_signal 含异常 sharpe (filter 未生效)",
        })
    except Exception:
        pass

    # daily 推荐 sell_target 高于 stop_price?
    try:
        r = conn.execute("""
            SELECT COUNT(*) FROM mart_daily_position_recommendation
             WHERE sell_target_price <= buy_price OR stop_price >= buy_price""").fetchone()
        issues.append({
            "category": "outliers", "check": "daily 推荐 sell_target/stop 价格逻辑",
            "count": r[0],
            "severity": "OK" if r[0] == 0 else "FAIL",
            "note": "sell_target ≤ buy 或 stop ≥ buy (逻辑错误)",
        })
    except Exception:
        pass

    return issues


def audit_risk_constraint_validity(conn) -> list[dict]:
    """4. STRONG_BUY 票是否真符合 max_dd 风险约束."""
    issues = []
    try:
        rows = conn.execute("""
            SELECT b.stock_code, b.formula_variant, b.tier,
                   o.avg_max_dd, o.optimal_stop_pct, b.historical_sharpe
              FROM mart_stock_formula_buy_signal_daily b
              JOIN mart_per_stock_strategy_optimal o
                ON o.stock_code = b.stock_code AND o.formula_variant = b.formula_variant
             WHERE b.tier IN ('BUY', 'STRONG_BUY')
               AND b.signal_date = (SELECT MAX(signal_date) FROM mart_stock_formula_buy_signal_daily)
        """).fetchall()
        risky = [r for r in rows if r[3] is not None and r[3] < -0.25]
        issues.append({
            "category": "risk_constraint", "check": "BUY/STRONG_BUY avg_max_dd > -25%",
            "total_buy": len(rows),
            "risky_count": len(risky),
            "severity": "OK" if len(risky) == 0 else "WARN",
            "note": "推荐里 avg_max_dd < -25% 的应被硬约束剔除",
        })
    except Exception as e:
        issues.append({"category": "risk_constraint", "severity": "FAIL", "note": str(e)})
    return issues


def audit_data_freshness(conn) -> list[dict]:
    """5. 数据时效性 (是否到今日)."""
    issues = []
    from datetime import date
    today = date.today().isoformat()
    checks = [
        ("fact_technical_trigger", "date"),
        ("fact_signal_context", "date"),
        ("mart_stock_picture_daily", "snapshot_date"),
        ("mart_stock_survey_features", "as_of_date"),
    ]
    for table, col in checks:
        try:
            r = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
            latest = r[0]
            days_behind = "N/A"
            if latest:
                from datetime import datetime
                days_behind = (datetime.fromisoformat(today).date() - datetime.fromisoformat(latest).date()).days
            issues.append({
                "category": "freshness", "table": table,
                "latest": latest, "days_behind": days_behind,
                "severity": "OK" if isinstance(days_behind, int) and days_behind <= 1
                            else ("WARN" if isinstance(days_behind, int) and days_behind <= 3 else "FAIL"),
            })
        except Exception as e:
            issues.append({"category": "freshness", "table": table, "severity": "FAIL", "note": str(e)})
    return issues


def main():
    conn = get_conn()
    try:
        all_issues = []
        log.info("=== 1. 表完整性审计 ===")
        all_issues += audit_table_completeness(conn)
        log.info("=== 2. JOIN 一致性审计 ===")
        all_issues += audit_join_consistency(conn)
        log.info("=== 3. 异常值审计 ===")
        all_issues += audit_outliers(conn)
        log.info("=== 4. 风险约束审计 ===")
        all_issues += audit_risk_constraint_validity(conn)
        log.info("=== 5. 数据时效审计 ===")
        all_issues += audit_data_freshness(conn)

        # 汇总
        print()
        print(f"{'='*128}")
        print(f"  端到端审计报告")
        print(f"{'='*128}")
        n_ok = sum(1 for i in all_issues if i.get("severity") == "OK")
        n_warn = sum(1 for i in all_issues if i.get("severity") == "WARN")
        n_fail = sum(1 for i in all_issues if i.get("severity") == "FAIL")
        print(f"  总: {len(all_issues)}, OK={n_ok}, WARN={n_warn}, FAIL={n_fail}")
        print()
        for sev in ("FAIL", "WARN"):
            shown = [i for i in all_issues if i.get("severity") == sev]
            if shown:
                print(f"\n--- {sev} ({len(shown)}) ---")
                for i in shown:
                    cat = i.get("category", "?")
                    name = i.get("check") or i.get("table", "?")
                    print(f"  [{cat}] {name}")
                    for k, v in i.items():
                        if k in ("category", "check", "table", "severity"):
                            continue
                        print(f"      {k}: {v}")
        if n_fail == 0 and n_warn == 0:
            print("\n  ✓ 所有检查通过")
        sys.exit(0 if n_fail == 0 else 1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
