#!/usr/bin/env python3
"""Report generators for ChunkyMonkey.

Default invocation preserves the historical backtest report behavior.
Use ``--format markdown`` to render the daily JSON report into a
mail-friendly Markdown summary.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn, init_db


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "data" / "reports"
NA = "N/A - data unavailable"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _row_value(row: Any, key: str, idx: int | None = None) -> Any:
    if row is None:
        return None
    try:
        keys = row.keys()
        if key in keys:
            return row[key]
    except (AttributeError, TypeError, KeyError):
        # row 可能是 tuple/list (无 .keys), 或 key 不存在 — fall back to idx-based lookup
        pass
    if idx is not None:
        try:
            return row[idx]
        except Exception:  # rule-compliance: ok evidence=helper-fallback-defensive
            return None
    return None


def _table_exists(conn: Any, table_name: str) -> bool:
    try:
        if "." in table_name:
            schema, table = table_name.split(".", 1)
            row = conn.execute(
                """
                SELECT 1
                  FROM information_schema.tables
                 WHERE table_schema = ?
                   AND table_name = ?
                 LIMIT 1
                """,
                [schema, table],
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT 1
                  FROM information_schema.tables
                 WHERE table_name = ?
                 LIMIT 1
                """,
                [table_name],
            ).fetchone()
        return row is not None
    except Exception:  # rule-compliance: ok evidence=helper-fallback-defensive
        return False


def _table_columns(conn: Any, table_name: str) -> list[str]:
    try:
        rows = conn.execute(f"DESCRIBE {table_name}").fetchall()
    except Exception:  # rule-compliance: ok evidence=helper-fallback-defensive
        return []
    cols: list[str] = []
    for row in rows:
        value = _row_value(row, "column_name", 0)
        if value:
            cols.append(str(value))
    return cols


def _pick_column(columns: list[str], candidates: list[str]) -> str | None:
    lower = {col.lower(): col for col in columns}
    for candidate in candidates:
        found = lower.get(candidate.lower())
        if found:
            return found
    return None


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    for fmt, width in (("%Y-%m-%d", 10), ("%Y%m%d", 8), ("%Y-%m-%d %H:%M:%S", 19)):
        try:
            return datetime.strptime(text[:width], fmt)
        except ValueError:
            continue
    return None


def _format_scalar(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4g}"
    text = str(value)
    return text.replace("\n", " ").strip() or "-"


def _format_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _format_scalar(value)
    if abs(number) <= 2:
        return f"{number:.2%}"
    return f"{number:.2f}%"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # rule-compliance: ok evidence=helper-fallback-defensive
        return None


def _find_recommendation_list(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    direct_keys = (
        "top_recommendations",
        "recommendations",
        "top5_recommendations",
        "today_top5_recommendations",
    )
    for key in direct_keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)][:5]

    nested_paths = (
        ("today", "top5_recommendations"),
        ("today", "recommendations"),
        ("selection", "top5"),
        ("selection_board", "recommendations"),
    )
    for path in nested_paths:
        cursor: Any = data
        for part in path:
            cursor = cursor.get(part) if isinstance(cursor, dict) else None
        if isinstance(cursor, list):
            return [item for item in cursor if isinstance(item, dict)][:5]
    return []


def _load_holdings(conn: Any) -> list[dict[str, Any]]:
    table = "mart_paper_sim_holdings"
    if conn is None or not _table_exists(conn, table):
        return []
    columns = _table_columns(conn, table)
    if not columns:
        return []

    aliases = {
        "stock_code": ["stock_code", "symbol", "code"],
        "stock_name": ["stock_name", "name"],
        "shares": ["shares", "position_shares", "quantity"],
        "market_value": ["market_value", "position_value", "value", "total_value"],
        "pnl_pct": ["pnl_pct", "return_pct", "unrealized_pnl_pct"],
        "open_date": ["open_date", "entry_date", "buy_date"],
    }
    select_parts: list[str] = []
    for alias, candidates in aliases.items():
        column = _pick_column(columns, candidates)
        if column:
            select_parts.append(f"{_quote_ident(column)} AS {alias}")
        else:
            select_parts.append(f"NULL AS {alias}")

    order_col = _pick_column(columns, aliases["market_value"]) or _pick_column(columns, aliases["stock_code"])
    order_sql = f"ORDER BY {_quote_ident(order_col)} DESC NULLS LAST" if order_col else ""
    sql = f"SELECT {', '.join(select_parts)} FROM {table} {order_sql} LIMIT 20"
    try:
        rows = conn.execute(sql).fetchall()
    except Exception:  # rule-compliance: ok evidence=helper-fallback-defensive
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({
            "stock_code": _row_value(row, "stock_code", 0),
            "stock_name": _row_value(row, "stock_name", 1),
            "shares": _row_value(row, "shares", 2),
            "market_value": _row_value(row, "market_value", 3),
            "pnl_pct": _row_value(row, "pnl_pct", 4),
            "open_date": _row_value(row, "open_date", 5),
        })
    return out


def _watermark_tag(row: Any) -> str:
    failures = _row_value(row, "consecutive_failures", 5) or 0
    fallback_active = bool(_row_value(row, "fallback_active", 6))
    last_data_date = _row_value(row, "last_data_date", 3)
    try:
        failures = int(failures)
    except (TypeError, ValueError):
        failures = 0

    parsed = _parse_date(last_data_date)
    stale_days = None
    if parsed is not None:
        stale_days = (datetime.now().date() - parsed.date()).days

    if failures >= 3 or last_data_date in (None, ""):
        return "RED"
    if failures > 0 or fallback_active or (stale_days is not None and stale_days > 3):
        return "YELLOW"
    return "GREEN"


def _load_watermarks(conn: Any) -> list[dict[str, Any]]:
    table = "mart_data_source_watermark"
    if conn is None or not _table_exists(conn, table):
        return []
    try:
        rows = conn.execute(
            """
            SELECT data_domain,
                   source_name,
                   source_tier,
                   last_data_date,
                   last_success_at,
                   consecutive_failures,
                   fallback_active,
                   updated_at
              FROM mart_data_source_watermark
             ORDER BY data_domain, source_tier, source_name
             LIMIT 50
            """
        ).fetchall()
    except Exception:  # rule-compliance: ok evidence=helper-fallback-defensive
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({
            "tag": _watermark_tag(row),
            "data_domain": _row_value(row, "data_domain", 0),
            "source_name": _row_value(row, "source_name", 1),
            "source_tier": _row_value(row, "source_tier", 2),
            "last_data_date": _row_value(row, "last_data_date", 3),
            "consecutive_failures": _row_value(row, "consecutive_failures", 5),
            "fallback_active": _row_value(row, "fallback_active", 6),
        })
    return out


def _load_latest_kpi(conn: Any) -> dict[str, Any]:
    table = "mart_paper_sim_kpi"
    if conn is None or not _table_exists(conn, table):
        return {}
    columns = _table_columns(conn, table)
    if not columns:
        return {}
    order_col = _pick_column(columns, ["built_at", "created_at", "period_end"])
    order_sql = f" ORDER BY {_quote_ident(order_col)} DESC NULLS LAST" if order_col else ""
    try:
        row = conn.execute(f"SELECT * FROM {table}{order_sql} LIMIT 1").fetchone()
    except Exception:  # rule-compliance: ok evidence=helper-fallback-defensive
        return {}
    if row is None:
        return {}
    if hasattr(row, "keys"):
        try:
            return {key: row[key] for key in row.keys()}
        except (AttributeError, TypeError, KeyError):
            # row 可能不支持 .keys() — fall back to positional column mapping
            pass
    return {col: row[idx] for idx, col in enumerate(columns) if idx < len(row)}


def _infer_daily_json_path(output_path: Path | None) -> Path:
    if output_path is not None:
        match = re.search(r"daily_(\d{8})\.md$", output_path.name)
        if match:
            return output_path.with_name(f"daily_{match.group(1)}.json")
    today_path = REPORTS_DIR / f"daily_{datetime.now().strftime('%Y%m%d')}.json"  # Phase ψ.5 allowlist: daily report filename wall-clock
    if today_path.exists():
        return today_path
    candidates = sorted(REPORTS_DIR.glob("daily_*.json"))
    if candidates:
        return candidates[-1]
    return today_path


def render_daily_markdown(
    report_json_path: str | Path | None = None,
    *,
    conn: Any | None = None,
    connect_db: bool = True,
) -> str:
    """Render the daily report as Markdown.

    Missing input files or source tables are represented as ``N/A`` so the
    report can still be delivered after partial pipeline failures.
    """
    json_path = Path(report_json_path) if report_json_path is not None else _infer_daily_json_path(None)
    daily = _read_json(json_path)
    owns_conn = False
    if conn is None and connect_db:
        try:
            conn = get_conn()
            owns_conn = True
        except Exception:  # rule-compliance: ok evidence=helper-fallback-defensive
            conn = None

    try:
        recommendations = _find_recommendation_list(daily)
        holdings = _load_holdings(conn)
        watermarks = _load_watermarks(conn)
        kpi = _load_latest_kpi(conn)
    finally:
        if owns_conn and conn is not None:
            conn.close()

    report_date = "-"
    if isinstance(daily, dict):
        report_date = str(daily.get("date") or daily.get("run_date") or json_path.stem.replace("daily_", ""))
    elif json_path.exists():
        report_date = json_path.stem.replace("daily_", "")

    lines: list[str] = [
        f"# ChunkyMonkey Daily Report {report_date}",
        "",
        "## Today Top-5 Recommendations",
        "",
    ]

    if recommendations:
        lines.extend([
            "| Rank | Stock | Score | Reason |",
            "|---:|---|---:|---|",
        ])
        for idx, item in enumerate(recommendations, start=1):
            rank = item.get("rank") or item.get("rank_in_date") or idx
            code = item.get("stock_code") or item.get("symbol") or item.get("code") or "-"
            name = item.get("stock_name") or item.get("name") or ""
            score = item.get("score")
            if score is None:
                score = item.get("pred_score")
            if score is None:
                score = item.get("percentile")
            reason = item.get("reason") or item.get("reasoning") or item.get("key_features") or "-"
            stock = f"{code} {name}".strip()
            lines.append(f"| {_format_scalar(rank)} | {_format_scalar(stock)} | {_format_scalar(score)} | {_format_scalar(reason)} |")
    else:
        lines.append(NA)

    lines.extend(["", "## Current Holdings", ""])
    if holdings:
        lines.extend([
            "| Stock | Shares | Market Value | PnL | Open Date |",
            "|---|---:|---:|---:|---|",
        ])
        for item in holdings:
            stock = f"{_format_scalar(item.get('stock_code'))} {_format_scalar(item.get('stock_name'))}".strip()
            lines.append(
                "| "
                f"{stock} | "
                f"{_format_scalar(item.get('shares'))} | "
                f"{_format_scalar(item.get('market_value'))} | "
                f"{_format_pct(item.get('pnl_pct'))} | "
                f"{_format_scalar(item.get('open_date'))} |"
            )
    else:
        lines.append(NA)

    lines.extend(["", "## Data Sync Status", ""])
    if watermarks:
        lines.extend([
            "| Tag | Domain | Source | Tier | Last Data Date | Failures | Fallback |",
            "|---|---|---|---:|---|---:|---|",
        ])
        for item in watermarks:
            lines.append(
                "| "
                f"{_format_scalar(item.get('tag'))} | "
                f"{_format_scalar(item.get('data_domain'))} | "
                f"{_format_scalar(item.get('source_name'))} | "
                f"{_format_scalar(item.get('source_tier'))} | "
                f"{_format_scalar(item.get('last_data_date'))} | "
                f"{_format_scalar(item.get('consecutive_failures'))} | "
                f"{_format_scalar(item.get('fallback_active'))} |"
            )
    else:
        lines.append(NA)

    lines.extend(["", "## Today KPI", ""])
    if kpi:
        metric_map = [
            ("sim_run_id", "Sim Run"),
            ("variant", "Variant"),
            ("period_end", "Period End"),
            ("annual_return", "Annual Return"),
            ("ann_ret", "Annual Return"),
            ("max_dd", "Max Drawdown"),
            ("sharpe", "Sharpe"),
            ("monthly_win_rate", "Monthly Win Rate"),
            ("monthly_win", "Monthly Win Rate"),
            ("excess_vs_hs300", "Excess vs HS300"),
            ("all_kpi_pass", "All KPI Pass"),
        ]
        emitted: set[str] = set()
        lines.extend(["| Metric | Value |", "|---|---|"])
        for key, label in metric_map:
            if label in emitted or key not in kpi:
                continue
            emitted.add(label)
            value = kpi.get(key)
            if key in {"annual_return", "ann_ret", "max_dd", "monthly_win_rate", "monthly_win", "excess_vs_hs300"}:
                rendered = _format_pct(value)
            else:
                rendered = _format_scalar(value)
            lines.append(f"| {label} | {rendered} |")
    else:
        lines.append(NA)

    return "\n".join(lines) + "\n"


def write_daily_markdown(input_json: Path | None, output_path: Path | None) -> Path:
    output = output_path or (REPORTS_DIR / f"daily_{datetime.now().strftime('%Y%m%d')}.md")  # Phase ψ.5 allowlist: daily report filename wall-clock
    report_json = input_json or _infer_daily_json_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_daily_markdown(report_json), encoding="utf-8")
    return output


def write_legacy_backtest_report() -> Path:
    init_db()
    conn = get_conn()
    L = []
    a = L.append

    a("# Historical Backtest Report")
    a("")
    a("Date: 2026-04-03 | Range: 2023Q1-2025Q4 (12 quarters)")
    a("Events: 54,232 | Buy events: 29,931 | Institutions: 223 | Industries L1/L2/L3: 31/131/336")
    a("")
    a("---")
    a("")

    a("## 1. Institution Industry Performance (3 levels)")
    a("")
    overview_rows = conn.execute(
        "SELECT industry_level, COUNT(*), COUNT(DISTINCT institution_id), COUNT(DISTINCT industry_name), "
        "AVG(avg_gain_30d), AVG(win_rate_30d) FROM research_inst_industry_performance "
        "WHERE industry_level IN ('L1', 'L2', 'L3') AND buy_event_count>=3 "
        "GROUP BY industry_level"
    ).fetchall()
    overview_by_level = {r[0]: r for r in overview_rows}
    for lv in ["L1", "L2", "L3"]:
        r = overview_by_level.get(lv)
        if r:
            combos, insts, industries, gain30, win_rate30 = r[1], r[2], r[3], r[4] or 0.0, r[5] or 0.0
        else:
            combos = insts = industries = 0
            gain30 = win_rate30 = 0.0
        a(f"**{lv}**: {combos} combos ({insts} insts x {industries} industries), avg 30d gain +{gain30:.2f}%, win rate {win_rate30:.1f}%")

    a("")
    a("### L3 Top Experts (events>=5, by 30d win rate)")
    a("")
    a("| Institution | Type | L3 Industry | Events | 30d WR | 30d Gain | DD | Edge | Low-Prem WR |")
    a("|---|---|---|---|---|---|---|---|---|")
    tops = conn.execute(
        "SELECT inst_name, inst_type, industry_name, buy_event_count, "
        "win_rate_30d, avg_gain_30d, avg_max_drawdown_30d, industry_edge_30d, low_premium_win_rate_30d "
        "FROM research_inst_industry_performance WHERE industry_level='L3' AND buy_event_count>=5 "
        "ORDER BY win_rate_30d DESC, buy_event_count DESC LIMIT 15"
    ).fetchall()
    for t in tops:
        a(f"| {t[0][:15]} | {t[1] or '-'} | {t[2]} | {t[3]} | {t[4]:.0f}% | {t[5]:+.1f}% | {t[6]:.1f}% | {t[7]:+.1f}% | {t[8]:.0f}% |")

    a("")
    a("### Expert Summary (WR>=60%, events>=5)")
    a("")
    expert_rows = conn.execute(
        "SELECT industry_level, COUNT(*), AVG(avg_gain_30d), AVG(win_rate_30d), AVG(avg_max_drawdown_30d) "
        "FROM research_inst_industry_performance "
        "WHERE industry_level IN ('L1', 'L2', 'L3') AND buy_event_count>=5 AND win_rate_30d>=60 "
        "GROUP BY industry_level"
    ).fetchall()
    experts_by_level = {r[0]: r for r in expert_rows}
    for lv in ["L1", "L2", "L3"]:
        r = experts_by_level.get(lv)
        if r:
            combos, gain30, win_rate30, drawdown30 = r[1], r[2] or 0.0, r[3] or 0.0, r[4] or 0.0
        else:
            combos = 0
            gain30 = win_rate30 = drawdown30 = 0.0
        a(f"- **{lv}**: {combos} combos, +{gain30:.1f}% gain, {win_rate30:.1f}% WR, {drawdown30:.1f}% DD")

    a("")
    a("## 2. Holding Chains")
    a("")
    chain_rows = conn.execute(
        "SELECT chain_status, COUNT(*), AVG(chain_days), AVG(event_count) "
        "FROM research_holding_chains WHERE chain_status IN ('closed', 'open') "
        "GROUP BY chain_status"
    ).fetchall()
    chains_by_status = {r[0]: r for r in chain_rows}
    for st in ["closed", "open"]:
        r = chains_by_status.get(st)
        if r:
            chain_count, chain_days, event_count = r[1], r[2], r[3] or 0.0
        else:
            chain_count, chain_days, event_count = 0, None, 0.0
        days = f", avg {chain_days:.0f} days" if chain_days else ""
        a(f"- **{st}**: {chain_count} chains{days}, avg {event_count:.1f} events")

    a("")
    a("### Top Event Sequences (closed)")
    a("")
    seqs = conn.execute(
        "SELECT event_sequence, COUNT(*) as cnt, AVG(chain_days) as days "
        "FROM research_holding_chains WHERE chain_status='closed' "
        "GROUP BY event_sequence ORDER BY cnt DESC LIMIT 10"
    ).fetchall()
    for s in seqs:
        days = f", avg {s[2]:.0f}d" if s[2] else ""
        a(f"- `{s[0]}`: {s[1]} chains{days}")

    a("")
    a("## 3. Cross Factor Analysis")
    a("")
    a("### Inst Type x Industry Top 10 (n>=20)")
    a("")
    a("| Type | Industry | N | 30d Gain | 30d WR | DD | vs Baseline |")
    a("|---|---|---|---|---|---|---|")
    cf1 = conn.execute(
        "SELECT factor_a_value, factor_b_value, sample_count, avg_gain_30d, win_rate_30d, "
        "avg_drawdown_30d, uplift_vs_baseline FROM research_cross_factor "
        "WHERE factor_a='inst_type' AND factor_b='industry_l1' AND sample_count>=20 "
        "ORDER BY avg_gain_30d DESC LIMIT 10"
    ).fetchall()
    for r in cf1:
        a(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:+.2f}% | {r[4]:.1f}% | {r[5]:.1f}% | {r[6]:+.2f}% |")

    a("")
    a("### Consensus x Premium (KEY FINDING)")
    a("")
    a("| Consensus | Premium | N | 30d Gain | 30d WR | DD |")
    a("|---|---|---|---|---|---|")
    cf2 = conn.execute(
        "SELECT factor_a_value, factor_b_value, sample_count, avg_gain_30d, win_rate_30d, avg_drawdown_30d "
        "FROM research_cross_factor WHERE factor_a='consensus' AND factor_b='premium_bucket' "
        "ORDER BY avg_gain_30d DESC"
    ).fetchall()
    for r in cf2:
        a(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:+.2f}% | {r[4]:.1f}% | {r[5]:.1f}% |")

    a("")
    a("### Season x Inst Type Top 10 (n>=20)")
    a("")
    a("| Season | Type | N | 30d Gain | 30d WR | DD |")
    a("|---|---|---|---|---|---|")
    cf3 = conn.execute(
        "SELECT factor_a_value, factor_b_value, sample_count, avg_gain_30d, win_rate_30d, avg_drawdown_30d "
        "FROM research_cross_factor WHERE factor_a='report_season' AND factor_b='inst_type' AND sample_count>=20 "
        "ORDER BY avg_gain_30d DESC LIMIT 10"
    ).fetchall()
    for r in cf3:
        a(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:+.2f}% | {r[4]:.1f}% | {r[5]:.1f}% |")

    a("")
    a("## 4. Signal Transfer Efficiency Top 10")
    a("")
    a("| Institution | Type | L2 Industry | Chains | Follow 30d | Premium |")
    a("|---|---|---|---|---|---|")
    st = conn.execute(
        "SELECT inst_name, inst_type, industry_l2, closed_chain_count, "
        "follow_median_gain_30d, avg_premium_pct FROM research_signal_transfer "
        "WHERE closed_chain_count>=3 AND follow_median_gain_30d IS NOT NULL "
        "ORDER BY follow_median_gain_30d DESC LIMIT 10"
    ).fetchall()
    for r in st:
        a(f"| {r[0][:15]} | {r[1]} | {r[2]} | {r[3]} | {r[4]:+.1f}% | {r[5]:+.1f}% |")

    a("")
    a("---")
    a("")
    a("## Key Findings")
    a("")
    a("### 1. L3 Industry Expertise Has Real Predictive Power")
    a("- L3 experts (WR>=60%, events>=5): 404 combos, +7.2% avg gain, far above baseline +3.2%")
    a("- L3 has ~30% more valid combos than L1 (404 vs 287), confirming finer granularity captures more alpha")
    a("")
    a("### 2. Solo + Low Premium Is The Strongest Signal")
    a("- Solo + negative premium (<=0%) strongest, >20% significantly worse")
    a("- Heavy consensus + negative premium underperforms solo signals")
    a("- Multi-institution crowding does not add value in this slice")
    a("")
    a("### 3. Niusan Have Extreme Alpha in Tech/Light Industry")
    a("- Niusan + Computer: 114 samples, +14.22%, 62.3% WR")
    a("- Niusan + Light Manufacturing: 38 samples, +13.28%, 73.7% WR")
    a("- QFII + Defense/Textile also show significant excess returns")
    a("")
    a("### 4. Exit Event Cost Data Missing")
    a("- All 1,126 exit events have NULL inst_ref_cost")
    a("- Prevents calculating institution cycle returns for closed chains")
    a("- Action: Need to compute exit report-period cost estimates")
    a("")
    a("### 5. Setup A Calibration Recommendations")
    a("- Premium: Negative premium strongest; >20% significantly worse")
    a("- Consensus: Current positive weighting may need reversal")
    a("- Industry level: L3 hits should get higher priority")
    a("- Inst type: QFII and Niusan have significant industry alpha")

    path = REPO_ROOT / "docs" / "BACKTEST_REPORT.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")
    conn.close()
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ChunkyMonkey reports")
    parser.add_argument("--format", choices=["markdown"], default=None, help="Report format for daily output")
    parser.add_argument("--input", dest="input_json", type=Path, default=None, help="Daily JSON report path")
    parser.add_argument("--output", type=Path, default=None, help="Output path")
    args = parser.parse_args()

    if args.format == "markdown":
        path = write_daily_markdown(args.input_json, args.output)
        print(f"Markdown report written to {path}")
        return

    path = write_legacy_backtest_report()
    print(f"Report written to {path}")


if __name__ == "__main__":
    main()
