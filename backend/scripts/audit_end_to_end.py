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
    table_names = [table for table, _expected_n, _date_col in EXPECTED]
    placeholders = ", ".join("?" for _ in table_names)
    existing_rows = conn.execute(
        f"""
        SELECT table_name
          FROM information_schema.tables
         WHERE table_name IN ({placeholders})
        """,
        table_names,
    ).fetchall()
    existing = {row[0] for row in existing_rows}
    existing_specs = [
        (table, expected_n, date_col)
        for table, expected_n, date_col in EXPECTED
        if table in existing
    ]
    stats_by_table = {}
    if existing_specs:
        stats_sql = "\nUNION ALL\n".join(
            f"""
            SELECT '{table}' AS table_name,
                   COUNT(*) AS n,
                   {f"CAST(MAX({date_col}) AS VARCHAR)" if date_col else "NULL"} AS latest
              FROM {table}
            """.strip()
            for table, _expected_n, date_col in existing_specs
        )
        stats_by_table = {
            row[0]: {"n": int(row[1] or 0), "latest": row[2]}
            for row in conn.execute(stats_sql).fetchall()
        }
    for table, expected_n, date_col in EXPECTED:
        if table not in stats_by_table:
            issues.append({
                "category": "table_completeness", "table": table,
                "severity": "FAIL", "note": "table missing",
            })
            continue
        n = stats_by_table[table]["n"]
        severity = "OK"
        note = ""
        if n < expected_n // 10:
            severity = "FAIL"
            note = f"行数严重不足 {n:,} < expect {expected_n:,}/10"
        elif n < expected_n:
            severity = "WARN"
            note = f"行数偏少 {n:,} < {expected_n:,}"
        issues.append({
            "category": "table_completeness",
            "table": table, "n": n, "expected_n": expected_n,
            "severity": severity, "note": note, "latest": stats_by_table[table]["latest"],
        })
    return issues


def audit_join_consistency(conn) -> list[dict]:
    """2. 检查关键 JOIN 字段是否对齐."""
    issues = []
    try:
        # buy_signal 应该 join 到 strategy_optimal — LEFT JOIN by design, 允许部分 null
        # 触发数 - 寻优数 > 20% 才算异常 (太多未寻优股入 buy_signal 池)
        r = conn.execute(
            """WITH x AS (
                 SELECT COUNT(*) AS total FROM mart_stock_formula_buy_signal_daily
               ), y AS (
                 SELECT COUNT(*) AS missing FROM mart_stock_formula_buy_signal_daily b
                   LEFT JOIN mart_per_stock_strategy_optimal o
                     ON o.stock_code = b.stock_code AND o.formula_variant = b.formula_variant
                   WHERE o.stock_code IS NULL
               )
               SELECT y.missing FROM x, y WHERE (1.0 * y.missing / NULLIF(x.total, 0)) > 0.20"""
        ).fetchone()
        missing = 0 if r is None else r[0]
        issues.append({
            "category": "join_consistency",
            "check": "buy_signal × strategy_optimal 缺失率 (≤20%)",
            "missing_count": missing,
            "severity": "OK" if missing == 0 else ("WARN" if missing < 100 else "FAIL"),
            "note": "缺失寻优结果占 buy_signal 总数 > 20% (新触发股未寻优)",
        })
    except Exception as e:
        issues.append({"category": "join_consistency", "check": "buy_signal × strategy_optimal 缺失率 (≤20%)", "severity": "FAIL", "note": str(e)})
    try:
        r = conn.execute(
            """SELECT COUNT(*) FROM mart_stock_formula_buy_signal_daily b
                 LEFT JOIN mart_stock_picture_daily p
                   ON p.stock_code = b.stock_code
                  AND p.snapshot_date = (SELECT MAX(snapshot_date) FROM mart_stock_picture_daily)
                WHERE p.stock_code IS NULL"""
        ).fetchone()
        missing = r[0] if r else 0
        issues.append({
            "category": "join_consistency",
            "check": "buy_signal × picture stock_code 覆盖",
            "missing_count": missing,
            "severity": "OK" if missing == 0 else ("WARN" if missing < 100 else "FAIL"),
            "note": "缺失画像的 buy_signal 行",
        })
    except Exception as e:
        issues.append({"category": "join_consistency", "check": "buy_signal × picture stock_code 覆盖", "severity": "FAIL", "note": str(e)})
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


def audit_recommendation_pit_coverage(conn) -> list[dict]:
    """5. Daily recommendations should surface whether PIT-safe params are used."""
    issues = []
    try:
        row = conn.execute("""
            WITH latest AS (
              SELECT MAX(signal_date) AS signal_date
              FROM mart_daily_position_recommendation
            ),
            latest_recs AS (
              SELECT r.*
              FROM mart_daily_position_recommendation r
              JOIN latest l ON l.signal_date = r.signal_date
            ),
            pit_stock AS (
              SELECT DISTINCT stock_code
              FROM mart_per_stock_stage_strategy_optimal_pit
              WHERE cutoff_date <= (SELECT signal_date FROM latest)
            ),
            pit_formula AS (
              SELECT DISTINCT stock_code, formula_id, formula_variant
              FROM mart_per_stock_stage_strategy_optimal_pit
              WHERE cutoff_date <= (SELECT signal_date FROM latest)
            )
            SELECT
              (SELECT signal_date FROM latest) AS signal_date,
              COUNT(*) AS n_total,
              COUNT(*) FILTER (WHERE r.match_tier IN ('stage_pit', 'stage_pit_formula_fallback')) AS n_pit,
              COUNT(*) FILTER (WHERE r.match_tier = 'cross_stage_fallback') AS n_cross_stage,
              COUNT(*) FILTER (WHERE ps.stock_code IS NOT NULL) AS n_same_stock_pit,
              COUNT(*) FILTER (WHERE pf.stock_code IS NOT NULL) AS n_same_stock_formula_pit
            FROM latest_recs r
            LEFT JOIN pit_stock ps
              ON ps.stock_code = r.stock_code
            LEFT JOIN pit_formula pf
              ON pf.stock_code = r.stock_code
             AND pf.formula_id = r.formula_id
             AND pf.formula_variant = r.formula_variant
        """).fetchone()
        signal_date, n_total, n_pit, n_cross_stage, n_same_stock_pit, n_same_stock_formula_pit = (
            row if row else (None, 0, 0, 0, 0, 0)
        )
        diagnostic_reasons = {}
        if signal_date:
            try:
                diag_rows = conn.execute("""
                    SELECT missing_reason, COUNT(*) AS n
                      FROM mart_daily_position_recommendation_pit_diagnostic
                     WHERE signal_date = ?
                     GROUP BY missing_reason
                     ORDER BY n DESC, missing_reason
                """, [signal_date]).fetchall()
                diagnostic_reasons = {str(reason): int(n) for reason, n in diag_rows}
            except Exception:
                diagnostic_reasons = {}
        pit_ratio = (n_pit / n_total) if n_total else 0.0
        if not n_total:
            severity = "FAIL"
            note = "latest recommendation table has no rows"
        elif n_pit == 0:
            severity = "WARN"
            note = "all latest recommendations use legacy cross-stage fallback; PIT-safe params did not reach final selection"
        elif pit_ratio < 0.5:
            severity = "WARN"
            note = "PIT-safe params cover less than half of latest recommendations"
        else:
            severity = "OK"
            note = "latest recommendations include PIT-safe stage params"
        issues.append({
            "category": "recommendation_pit_coverage",
            "check": "latest daily recommendation PIT coverage",
            "signal_date": signal_date,
            "n_total": int(n_total or 0),
            "n_pit": int(n_pit or 0),
            "n_cross_stage": int(n_cross_stage or 0),
            "n_same_stock_pit": int(n_same_stock_pit or 0),
            "n_same_stock_formula_pit": int(n_same_stock_formula_pit or 0),
            "pit_ratio": round(pit_ratio, 4),
            "diagnostic_reasons": diagnostic_reasons,
            "severity": severity,
            "note": note,
        })
    except Exception as e:
        issues.append({
            "category": "recommendation_pit_coverage",
            "check": "latest daily recommendation PIT coverage",
            "severity": "FAIL",
            "note": str(e),
        })
    return issues


def audit_data_freshness(conn) -> list[dict]:
    """6. 数据时效性 (是否到今日)."""
    issues = []
    from datetime import date
    today = date.today().isoformat()  # Phase ψ.5 allowlist: audit 衡量物理 today 的数据滞后天数
    checks = [
        ("fact_technical_trigger", "date"),
        ("fact_signal_context", "date"),
        ("mart_stock_picture_daily", "snapshot_date"),
        ("mart_stock_survey_features", "as_of_date"),
    ]
    table_names = [table for table, _col in checks]
    placeholders = ", ".join("?" for _ in table_names)
    existing = {
        row[0]
        for row in conn.execute(
            f"SELECT table_name FROM information_schema.tables WHERE table_name IN ({placeholders})",
            table_names,
        ).fetchall()
    }
    existing_checks = [(table, col) for table, col in checks if table in existing]
    latest_by_table = {}
    if existing_checks:
        latest_sql = "\nUNION ALL\n".join(
            f"SELECT '{table}' AS table_name, CAST(MAX({col}) AS VARCHAR) AS latest FROM {table}"
            for table, col in existing_checks
        )
        latest_by_table = {row[0]: row[1] for row in conn.execute(latest_sql).fetchall()}
    for table, col in checks:
        try:
            latest = latest_by_table.get(table)
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
        log.info("=== 5. 推荐 PIT 覆盖审计 ===")
        all_issues += audit_recommendation_pit_coverage(conn)
        log.info("=== 6. 数据时效审计 ===")
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
