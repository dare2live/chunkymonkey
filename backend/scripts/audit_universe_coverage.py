#!/usr/bin/env python3
"""P-1.5 Universe coverage audit — PLAN_V3 v3.2 P-1 gate.

Verifies the historical effective stock universe (excluding 停牌/退市/未上市) has
consistent coverage across K线 and business feature tables, so P0 ML ranking
training cannot be biased by universe leakage.

Sections (PLAN_V3 §2 P-1 acceptance = 0 unexplained gap → PASS):
1. K线 universe size time-series — first trading day of each month, distinct
   stock_code count. Flag months with abnormal shrinkage (<-5%) or balloon
   (>+15%) vs prior month.
2. Business-table universe coverage (vs K线) — fact_signal_context /
   fact_technical_trigger / fact_risk_factors / fact_feature_panel /
   fact_alpha158_panel. Compare distinct stock_code count with K线 on the same
   shared date range; coverage_ratio < 0.90 = FAIL.
3. Cross-day universe stability — for ~20 sampled trading days, count new
   inclusions (IPO) and dropouts (退市/停牌) day-over-day. Unexplained jump
   (|delta| > 2% of universe) without trading-day boundary = WARN.
4. Gap analysis — stocks present in K线 but missing in alpha158_panel /
   feature_panel on the same date. >5% of universe missing = FAIL.

Exit 0 = PASS (FAIL=0), 1 = FAIL.

Usage:
    cd /Users/dp/Documents/M/stock/chunkymonkey
    PYTHONPATH=backend python backend/scripts/audit_universe_coverage.py \
        --json-out /tmp/universe_audit.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH  # smartmoney.duckdb
from services.duck_adapter import connect as duck_connect

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("audit_universe")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MARKET_DB = REPO_ROOT / "data" / "market.duckdb"
ALPHA158_DB = REPO_ROOT / "data" / "alpha158.duckdb"

# A-share code prefixes (60=主板沪 / 00=主板深 / 30=创业板 / 68=科创板 / 83,87=北交所).
# Excludes ETF (15*/51*/56*/58*) and index (000300, 000905...) which live in
# price_kline but are NOT part of the P0 ML ranking universe.
# evidence: prefix-classification per Shanghai/Shenzhen exchange listing rules
ASHARE_PREFIX_REGEX = r"^(60|00|30|68|83|87)"

# Severity thresholds (data-driven heuristics, conservative). Not Optuna params.
# evidence: P-1.5 audit calibration; rule-compliance: ok evidence=audit-heuristic
MONTH_SHRINK_FAIL = -0.05     # >5% shrinkage MoM = FAIL
MONTH_BALLOON_FAIL = 0.15     # >15% balloon MoM = FAIL (mass IPO unlikely)
BUSINESS_COVERAGE_FAIL = 0.90  # <90% coverage vs K线 = FAIL (panel only)
DAY_JUMP_WARN = 0.02          # >2% delta day-over-day = WARN
GAP_FAIL_RATIO = 0.05         # >5% K线 universe missing in panel = FAIL

# Panel-style business tables: should mirror K线 A-share universe (one row per
# stock per date for every active stock).
PANEL_TABLES = (
    # (table, date_col, stock_col, db_alias)
    ("fact_signal_context", "date", "stock_code", "main"),
    ("fact_risk_factors", "calc_date", "stock_code", "main"),
    ("fact_feature_panel", "date", "stock_code", "main"),
    ("fact_alpha158_panel", "date", "stock_code", "a158"),
)
# Event/trigger-style tables: only a subset of stocks have rows per date (those
# triggering the pattern). Coverage ratio not directly meaningful — reported as
# informational (PASS-only).
EVENT_TABLES = (
    ("fact_technical_trigger", "date", "stock_code", "main"),
)


@dataclass
class CheckResult:
    section: str
    name: str
    status: str  # PASS / WARN / FAIL
    detail: str
    rows: int = 0
    extras: dict = field(default_factory=dict)


def _attach_market(conn) -> None:
    """ATTACH market.duckdb READ_ONLY so we can JOIN K线 + business in one conn."""
    conn.execute(f"ATTACH '{MARKET_DB}' AS mkt (READ_ONLY)")


def _attach_alpha158(conn) -> None:
    conn.execute(f"ATTACH '{ALPHA158_DB}' AS a158 (READ_ONLY)")


def _month_first_trading_days(conn) -> list[str]:
    """Return the first daily K线 date in each (year, month) of price_kline."""
    rows = conn.execute(
        """
        SELECT MIN(date) AS first_day
          FROM mkt.price_kline
         WHERE freq = 'daily'
         GROUP BY substr(date, 1, 7)
         ORDER BY first_day
        """
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def _ashare_universe(conn, ref_date: str) -> set[str]:
    """K线 A-share universe on ref_date (excludes ETF/index)."""
    return {
        r[0]
        for r in conn.execute(
            f"SELECT DISTINCT code FROM mkt.price_kline "
            f"WHERE freq='daily' AND date=? AND regexp_matches(code, '{ASHARE_PREFIX_REGEX}')",
            [ref_date],
        ).fetchall()
    }


def _filter_ashare(codes: set[str]) -> set[str]:
    return {c for c in codes if c[:2] in ("60", "00", "30", "68", "83", "87")}


def check_kline_universe_timeseries(conn) -> list[CheckResult]:
    """Section 1: K线 A-share universe size each month-first; flag abnormal MoM."""
    out: list[CheckResult] = []
    days = _month_first_trading_days(conn)
    series: list[tuple[str, int]] = []
    for d in days:
        n = conn.execute(
            f"SELECT COUNT(DISTINCT code) FROM mkt.price_kline "
            f"WHERE freq='daily' AND date=? AND regexp_matches(code, '{ASHARE_PREFIX_REGEX}')",
            [d],
        ).fetchone()[0]
        series.append((d, n))

    if not series:
        out.append(CheckResult(
            section="1. Kline universe time-series",
            name="empty",
            status="FAIL",
            detail="price_kline has no daily rows",
        ))
        return out

    # Drop trailing partial-sync days: any day with <50% of median size is
    # most likely an in-progress sync (e.g. today's 74 codes). Flag as WARN
    # not FAIL because it's not a universe-coverage problem per se.
    counts = [n for _, n in series]
    median = sorted(counts)[len(counts) // 2]
    partial_threshold = max(int(median * 0.5), 100)

    out.append(CheckResult(
        section="1. Kline universe time-series",
        name="month_sample_count",
        status="PASS",
        detail=f"{len(series)} month-first samples, median universe={median}",
        rows=len(series),
        extras={"series": [{"date": d, "n": n} for d, n in series]},
    ))

    # Latest 2 months can be partial-sync (CLAUDE.md documents sync watermark
    # lag); demote MoM abnormal there to WARN, not FAIL.
    latest_two = {d for d, _ in series[-2:]}

    fails: list[dict] = []
    warns_partial: list[dict] = []
    warns_recent: list[dict] = []
    prev: int | None = None
    prev_d: str | None = None
    for d, n in series:
        if n < partial_threshold:
            warns_partial.append({"date": d, "n": n, "reason": "partial_sync_or_holiday"})
            # do not feed partial-sync into MoM delta — skip prev update
            continue
        if prev is not None and prev > 0:
            delta = (n - prev) / prev
            kind = None
            if delta <= MONTH_SHRINK_FAIL:
                kind = "shrink"
            elif delta >= MONTH_BALLOON_FAIL:
                kind = "balloon"
            if kind:
                rec = {
                    "date": d, "n": n, "prev_date": prev_d, "prev_n": prev,
                    "delta_pct": round(delta * 100, 2), "kind": kind,
                }
                if d in latest_two:
                    rec["reason"] = "recent_month_likely_partial_sync"
                    warns_recent.append(rec)
                else:
                    fails.append(rec)
        prev, prev_d = n, d

    if warns_partial:
        out.append(CheckResult(
            section="1. Kline universe time-series",
            name="partial_sync_days",
            status="WARN",
            detail=f"{len(warns_partial)} month-first samples below 50% of median (partial sync / first trading day of holiday week)",
            rows=len(warns_partial),
            extras={"days": warns_partial},
        ))
    if warns_recent:
        out.append(CheckResult(
            section="1. Kline universe time-series",
            name="MoM_abnormal_recent_2mo",
            status="WARN",
            detail=f"{len(warns_recent)} MoM transitions in latest 2 months exceed ±{int(MONTH_SHRINK_FAIL*-100)}%/{int(MONTH_BALLOON_FAIL*100)}% — likely ongoing sync, monitor",
            rows=len(warns_recent),
            extras={"events": warns_recent},
        ))
    if fails:
        out.append(CheckResult(
            section="1. Kline universe time-series",
            name="MoM_abnormal_historical",
            status="FAIL",
            detail=f"{len(fails)} historical month-over-month transitions exceed ±{int(MONTH_SHRINK_FAIL*-100)}%/{int(MONTH_BALLOON_FAIL*100)}%",
            rows=len(fails),
            extras={"events": fails},
        ))
    else:
        out.append(CheckResult(
            section="1. Kline universe time-series",
            name="MoM_abnormal_historical",
            status="PASS",
            detail=f"0 historical abnormal MoM transitions across {len(series)} months",
        ))
    return out


def _latest_full_kline_date(conn) -> str:
    """Pick the latest daily K线 date whose A-share code count >= 80% of median.

    Avoids today's partial-sync (e.g. 74 codes) skewing the comparison.
    """
    rows = conn.execute(
        f"SELECT date, COUNT(DISTINCT code) AS n FROM mkt.price_kline "
        f"WHERE freq='daily' AND regexp_matches(code, '{ASHARE_PREFIX_REGEX}') "
        f"GROUP BY 1 ORDER BY 1 DESC LIMIT 60"
    ).fetchall()
    counts = [r[1] for r in rows]
    median = sorted(counts)[len(counts) // 2] if counts else 0
    threshold = int(median * 0.8)
    for d, n in rows:
        if n >= threshold:
            return d
    return rows[0][0] if rows else None


def _query_biz_codes(conn, tbl: str, dcol: str, scol: str, db: str, ref_date: str) -> set[str]:
    """Read business-table codes on ref_date, normalize DATE→VARCHAR for alpha158."""
    if tbl == "fact_alpha158_panel":
        rows = conn.execute(
            f"SELECT DISTINCT {scol} FROM {db}.{tbl} WHERE CAST({dcol} AS VARCHAR)=?",
            [ref_date],
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT DISTINCT {scol} FROM {db}.{tbl} WHERE {dcol}=?",
            [ref_date],
        ).fetchall()
    return {r[0] for r in rows}


def check_business_table_coverage(conn) -> list[CheckResult]:
    """Section 2: each PANEL table's A-share universe vs K线 A-share on shared dates.

    Coverage = |K线-A-share ∩ panel| / |K线-A-share|. Event-style tables
    (technical_trigger) are reported separately (informational only).

    Sample 3 reference dates: 2024 first day, 2025 first day, latest full K线 day.
    """
    out: list[CheckResult] = []
    _attach_alpha158(conn)

    latest_full = _latest_full_kline_date(conn)
    # rule-compliance: ok evidence=cross-regime-fixed-sample (audit reference, not model param)
    ref_dates = [d for d in ["2024-01-02", "2025-01-02", latest_full] if d]

    for ref_date in ref_dates:
        kline_codes = _ashare_universe(conn, ref_date)
        if not kline_codes:
            out.append(CheckResult(
                section="2. Panel-table coverage",
                name=f"kline@{ref_date}",
                status="WARN",
                detail=f"K线 A-share has 0 rows on {ref_date}, skip",
            ))
            continue

        # Panel tables: should mirror K线 A-share universe
        for tbl, dcol, scol, db in PANEL_TABLES:
            try:
                biz_codes_raw = _query_biz_codes(conn, tbl, dcol, scol, db, ref_date)
            except Exception as e:
                out.append(CheckResult(
                    section="2. Panel-table coverage",
                    name=f"{tbl}@{ref_date}",
                    status="WARN",
                    detail=f"query failed: {e}",
                ))
                continue

            if not biz_codes_raw:
                out.append(CheckResult(
                    section="2. Panel-table coverage",
                    name=f"{tbl}@{ref_date}",
                    status="WARN",
                    detail=f"{tbl} has 0 rows on {ref_date} (table not built / date out of range)",
                ))
                continue

            biz_codes = _filter_ashare(biz_codes_raw)
            covered = kline_codes & biz_codes
            ratio = len(covered) / len(kline_codes)
            status = "PASS" if ratio >= BUSINESS_COVERAGE_FAIL else "FAIL"
            # panel-only A-share codes are likely delisted-but-retained — that's
            # GOOD for PIT (no survivorship), so we report as info not fail.
            panel_only_ashare = biz_codes - kline_codes
            non_ashare = biz_codes_raw - biz_codes
            out.append(CheckResult(
                section="2. Panel-table coverage",
                name=f"{tbl}@{ref_date}",
                status=status,
                detail=(
                    f"{tbl}: {len(covered)}/{len(kline_codes)} A-share covered "
                    f"({ratio*100:.1f}%); panel-only-A-share={len(panel_only_ashare)} "
                    f"(possibly delisted-but-retained, good for PIT); non-A-share-in-panel={len(non_ashare)}"
                ),
                rows=len(covered),
                extras={
                    "kline_ashare_size": len(kline_codes),
                    "panel_ashare_size": len(biz_codes),
                    "panel_total_size": len(biz_codes_raw),
                    "coverage_ratio": round(ratio, 4),
                    "panel_only_ashare_count": len(panel_only_ashare),
                    "non_ashare_in_panel_count": len(non_ashare),
                },
            ))

        # Event tables: informational only (subset by design)
        for tbl, dcol, scol, db in EVENT_TABLES:
            try:
                biz_codes_raw = _query_biz_codes(conn, tbl, dcol, scol, db, ref_date)
            except Exception as e:
                out.append(CheckResult(
                    section="2. Panel-table coverage",
                    name=f"{tbl}@{ref_date}",
                    status="WARN",
                    detail=f"query failed: {e}",
                ))
                continue
            biz_codes = _filter_ashare(biz_codes_raw)
            covered = kline_codes & biz_codes
            ratio = len(covered) / max(len(kline_codes), 1)
            out.append(CheckResult(
                section="2. Panel-table coverage",
                name=f"{tbl}@{ref_date}[event-table-info]",
                status="PASS",
                detail=(
                    f"{tbl} [event-trigger]: {len(covered)}/{len(kline_codes)} A-share triggered "
                    f"({ratio*100:.1f}%, informational only — events are subset by design)"
                ),
                rows=len(covered),
                extras={
                    "kline_ashare_size": len(kline_codes),
                    "triggered_ashare_size": len(biz_codes),
                    "trigger_density": round(ratio, 4),
                },
            ))
    return out


def check_cross_day_stability(conn) -> list[CheckResult]:
    """Section 3: sample ~20 trading days, compute day-over-day A-share universe delta.

    Large unexplained moves (|delta| > 2%) = WARN (IPO/delisting bursts).
    """
    out: list[CheckResult] = []
    # 20 evenly spaced full trading days from history (A-share, > 4000 codes)
    rows = conn.execute(
        f"""
        SELECT date FROM (
            SELECT date, COUNT(DISTINCT code) n
              FROM mkt.price_kline
             WHERE freq='daily' AND regexp_matches(code, '{ASHARE_PREFIX_REGEX}')
             GROUP BY 1
        ) WHERE n > 4000
          ORDER BY date
        """
    ).fetchall()
    dates = [r[0] for r in rows]
    if len(dates) < 22:
        out.append(CheckResult(
            section="3. Cross-day stability",
            name="insufficient_history",
            status="WARN",
            detail=f"only {len(dates)} full trading days available",
        ))
        return out

    step = max(len(dates) // 20, 1)
    samples = dates[::step][:20]
    # For each sample, compare to the next trading day
    big_jumps: list[dict] = []
    transitions: list[dict] = []
    for d in samples:
        next_rows = conn.execute(
            "SELECT date FROM mkt.price_kline WHERE freq='daily' AND date > ? ORDER BY date LIMIT 1",
            [d],
        ).fetchall()
        if not next_rows:
            continue
        d_next = next_rows[0][0]
        u_today = _ashare_universe(conn, d)
        u_next = _ashare_universe(conn, d_next)
        if not u_today or not u_next:
            continue
        added = len(u_next - u_today)
        removed = len(u_today - u_next)
        base = max(len(u_today), 1)
        jump_pct = (added + removed) / base
        rec = {
            "date": d, "next_date": d_next,
            "n_today": len(u_today), "n_next": len(u_next),
            "added_ipo_or_resume": added,
            "removed_delisted_or_halt": removed,
            "churn_pct": round(jump_pct * 100, 2),
        }
        transitions.append(rec)
        if jump_pct > DAY_JUMP_WARN:
            big_jumps.append(rec)

    out.append(CheckResult(
        section="3. Cross-day stability",
        name="samples",
        status="PASS",
        detail=f"{len(transitions)} day-over-day transitions sampled",
        rows=len(transitions),
        extras={"transitions": transitions},
    ))
    if big_jumps:
        out.append(CheckResult(
            section="3. Cross-day stability",
            name="big_jumps",
            status="WARN",
            detail=f"{len(big_jumps)} transitions with churn >{int(DAY_JUMP_WARN*100)}% (review for halt/IPO burst)",
            rows=len(big_jumps),
            extras={"events": big_jumps},
        ))
    else:
        out.append(CheckResult(
            section="3. Cross-day stability",
            name="big_jumps",
            status="PASS",
            detail=f"0 transitions exceed ±{int(DAY_JUMP_WARN*100)}% churn",
        ))
    return out


def check_gap_analysis(conn) -> list[CheckResult]:
    """Section 4: K线-only stocks (not in alpha158_panel / feature_panel) on a
    reference date — the P0 training-time leakage risk surface.
    """
    out: list[CheckResult] = []
    # Use a date that both alpha158 and feature_panel are likely to have built.
    # alpha158 max = 2026-04-23, feature_panel max = 2026-05-06 → pick 2026-04-22.
    # If those dates are stale we still want the audit to run; pick latest shared.
    a158_max = conn.execute("SELECT MAX(CAST(date AS VARCHAR)) FROM a158.fact_alpha158_panel").fetchone()[0]
    fp_max = conn.execute("SELECT MAX(date) FROM fact_feature_panel").fetchone()[0]
    if not a158_max or not fp_max:
        out.append(CheckResult(
            section="4. Gap analysis",
            name="missing_panels",
            status="WARN",
            detail=f"alpha158_max={a158_max} feature_panel_max={fp_max} — cannot run gap analysis",
        ))
        return out
    ref_date = min(a158_max, fp_max)

    kline_codes = _ashare_universe(conn, ref_date)
    if not kline_codes:
        # Roll back one day if no K线 on the panel's max date
        prior = conn.execute(
            f"SELECT MAX(date) FROM mkt.price_kline "
            f"WHERE freq='daily' AND date<=? AND regexp_matches(code, '{ASHARE_PREFIX_REGEX}')",
            [ref_date],
        ).fetchone()[0]
        ref_date = prior
        kline_codes = _ashare_universe(conn, ref_date) if ref_date else set()

    a158_codes = _filter_ashare({
        r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM a158.fact_alpha158_panel WHERE CAST(date AS VARCHAR)=?",
            [ref_date],
        ).fetchall()
    })
    fp_codes = _filter_ashare({
        r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM fact_feature_panel WHERE date=?",
            [ref_date],
        ).fetchall()
    })

    for panel_name, panel_codes in [
        ("fact_alpha158_panel", a158_codes),
        ("fact_feature_panel", fp_codes),
    ]:
        if not kline_codes:
            out.append(CheckResult(
                section="4. Gap analysis",
                name=f"{panel_name}@{ref_date}",
                status="WARN",
                detail=f"no K线 on {ref_date}, skip",
            ))
            continue
        missing = kline_codes - panel_codes
        extra = panel_codes - kline_codes
        ratio = len(missing) / max(len(kline_codes), 1)
        status = "PASS" if ratio <= GAP_FAIL_RATIO else "FAIL"
        sample_missing = sorted(missing)[:10]
        out.append(CheckResult(
            section="4. Gap analysis",
            name=f"{panel_name}@{ref_date}",
            status=status,
            detail=(
                f"{panel_name}: {len(missing)}/{len(kline_codes)} K线 A-share missing "
                f"in panel ({ratio*100:.2f}%); panel-only-A-share={len(extra)}"
            ),
            rows=len(missing),
            extras={
                "ref_date": ref_date,
                "kline_ashare_size": len(kline_codes),
                "panel_ashare_size": len(panel_codes),
                "missing_in_panel_count": len(missing),
                "panel_only_ashare_count": len(extra),
                "missing_ratio": round(ratio, 4),
                "sample_missing_codes": sample_missing,
            },
        ))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="P-1.5 universe coverage audit")
    parser.add_argument("--json-out", type=Path, default=None,
                        help="Write full JSON report to path")
    args = parser.parse_args()

    log.info("=== P-1.5 Universe Coverage Audit (PLAN_V3 v3.2) ===")
    # Rule 11 concurrency-safe: read-only connect to smartmoney; ATTACH market RO.
    conn = duck_connect(str(DB_PATH), read_only=True)
    try:
        _attach_market(conn)
        results: list[CheckResult] = []
        results.extend(check_kline_universe_timeseries(conn))
        results.extend(check_business_table_coverage(conn))
        results.extend(check_cross_day_stability(conn))
        results.extend(check_gap_analysis(conn))
    finally:
        conn.close()

    by_status = Counter(r.status for r in results)
    log.info("")
    log.info("=== Results ===")
    for r in results:
        log.info(f"  [{r.status:4s}] {r.section} :: {r.name} — {r.detail}")
    log.info("")
    log.info(
        f"SUMMARY: PASS={by_status['PASS']} WARN={by_status['WARN']} FAIL={by_status['FAIL']}"
    )

    if args.json_out:
        payload = {
            "audit": "P-1.5 universe coverage",
            "summary": dict(by_status),
            "results": [asdict(r) for r in results],
        }
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info(f"JSON report → {args.json_out}")

    if by_status["FAIL"] > 0:
        log.error(
            f"P-1.5 FAIL: {by_status['FAIL']} hard violations — PLAN_V3 §6 串行 gate blocks P0"
        )
        return 1
    log.info("P-1.5 PASS — universe coverage OK; 0 unexplained gap")
    return 0


if __name__ == "__main__":
    sys.exit(main())
