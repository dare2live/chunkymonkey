#!/usr/bin/env python3
"""Panel leakage audit — automated pre-train gate.

Background: 2026-05-22 stability retrain promoted via proxy-split-half Phase4 gate
(verdict warn_only_proxy / all_pass=true), BUT true train-log evidence revealed
IS-OOS drop 92.43% BLOCK (sector_*_tdx_l1_rel via dim_stock_tdx_industry non-PIT
retrospective bias). Default Phase4 proxy mode is too lenient. Need automated
schema-level + statistical leakage detection BEFORE every retrain.

5 checks (per CLAUDE.md §4.1 Anti-Leakage):
  1. SOURCE PIT markers — every fact_*/mart_*/dim_* in panel SQL has built_at OR
     effective_from/to OR announce_date / trade_date
  2. PANEL JOIN PIT-strict — every JOIN uses <= signal_date / ASOF / direct date=p.date
  3. FLAT CURRENT-MAPPING — dim_* without PIT markers + used in PARTITION BY aggregates
     (= retrospective bias pattern, CLAUDE.md §4.5 反例 dim_stock_tdx_industry / mart_stock_industry_pit 99.978% fallback)
  4. FALLBACK RATIO — mapping tables fallback (effective_from=1900-01-01) occurrence %
  5. FEATURE TEMPORAL VARIANCE — per-feature std over time, flag near-constant (<5%
     range = current mapping retrospectively applied)

Output:
  - data/reports/leakage_audit/<panel_id>_<timestamp>.json: full findings + risk scores
  - stdout: markdown summary
  - exit code: 0 all clean, 1 HIGH risk found (block), 2 MEDIUM risk (warn)

Integration:
  - Standalone CLI: PYTHONPATH=backend python backend/scripts/audit_panel_leakage.py --panel mart_p0a_feature_label_panel_v4
  - Pre-retrain hook (recommended): retrain_lambdamart_v6.py auto-call before Optuna
  - Pre-commit hook (recommended): rule-compliance extended to call this if panel SQL changed

Usage:
  audit_panel_leakage.py [--panel TABLE] [--db DB] [--strict] [--report-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

# rule-compliance: ok evidence=CLAUDE.md §4.5 known leakage patterns + Phase D 2026-05-22 finding
KNOWN_LEAKY_MAPPINGS = {
    "dim_stock_tdx_industry",  # Phase D finding: flat 5616×1, NON-PIT, used in panel sector PARTITION BY
    "dim_stock_sw_industry",  # likely similar pattern
    "dim_stock_concept",  # likely similar
}

KNOWN_PIT_MARKER_COLS = {
    "built_at", "effective_from", "effective_to",
    "announce_date", "trade_date", "snapshot_date", "report_date",
    "source_available_date", "as_of_date",
}

# Risk thresholds
TEMPORAL_VARIANCE_FLAG_PCT = 0.05  # feature std < 5% of range = near constant = leakage suspect
FALLBACK_RATIO_WARN_PCT = 0.05  # mapping fallback > 5% = warn
FALLBACK_RATIO_BLOCK_PCT = 0.50  # > 50% = block (catastrophic, CLAUDE.md §4.5 反例 99.978%)


def audit_check_1_pit_markers(conn) -> list[dict]:
    """Check 1: Every fact_*/mart_*/dim_* table has PIT marker column."""
    findings = []
    tables = [r[0] for r in conn.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='main'
          AND (table_name LIKE 'fact_%' OR table_name LIKE 'mart_%' OR table_name LIKE 'dim_%')
        ORDER BY table_name
    """).fetchall()]

    for tbl in tables:
        try:
            cols = [c[0] for c in conn.execute(f"SELECT * FROM {tbl} LIMIT 0").description]
            pit_cols = [c for c in cols if c.lower() in {c2.lower() for c2 in KNOWN_PIT_MARKER_COLS}]
            if not pit_cols:
                # Risk depends on table type
                if tbl.startswith("fact_"):
                    risk = "HIGH"
                elif tbl.startswith("dim_"):
                    risk = "HIGH" if tbl in KNOWN_LEAKY_MAPPINGS else "MEDIUM"
                else:
                    risk = "MEDIUM"
                findings.append({
                    "check": "1_pit_markers",
                    "table": tbl,
                    "risk": risk,
                    "reason": f"no PIT marker column found (expected one of {sorted(KNOWN_PIT_MARKER_COLS)})",
                    "cols": cols[:10],
                })
        except Exception as e:
            findings.append({
                "check": "1_pit_markers",
                "table": tbl,
                "risk": "LOW",
                "reason": f"could not introspect: {e}",
            })
    return findings


def audit_check_2_panel_join_pit(panel_build_sql_path: Path) -> list[dict]:
    """Check 2: Panel build SQL uses PIT-strict JOIN patterns."""
    findings = []
    if not panel_build_sql_path.exists():
        return [{
            "check": "2_panel_join_pit",
            "risk": "LOW",
            "reason": f"panel build script not found: {panel_build_sql_path}",
        }]

    text = panel_build_sql_path.read_text()
    # Pattern: JOIN <table_name> ... missing PIT filter
    join_pattern = re.compile(r"(LEFT|INNER|RIGHT|FULL|CROSS)?\s*JOIN\s+([\w.]+)", re.IGNORECASE)
    pit_predicates = ["<= signal_date", "<= p.date", "<= d.trade_date", "<= panel.date",
                      "ASOF", "<= cutoff", "<= as_of"]
    # Get a window of context around each JOIN
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = join_pattern.search(line)
        if not m:
            continue
        joined_table = m.group(2)
        # Skip CTE / subquery aliases
        if joined_table.startswith("__") or joined_table in ("panel", "panel_dates", "p", "d", "src"):
            continue
        # Check ±10 lines context for PIT predicates
        context = "\n".join(lines[max(0, i-5):min(len(lines), i+10)])
        has_pit = any(pred.lower() in context.lower() for pred in pit_predicates) or \
                  ".date = p.date" in context or "ASOF" in context.upper()
        if not has_pit:
            # check if table is a known mapping table (more risky)
            risk = "HIGH" if joined_table.lower() in KNOWN_LEAKY_MAPPINGS else "MEDIUM"
            findings.append({
                "check": "2_panel_join_pit",
                "line": i + 1,
                "joined_table": joined_table,
                "risk": risk,
                "reason": "JOIN without obvious PIT predicate (<= signal_date / ASOF / date=p.date) within 5+10 lines context",
                "context_excerpt": line.strip()[:200],
            })
    return findings


def audit_check_3_flat_mapping_partition(panel_build_sql_path: Path, conn) -> list[dict]:
    """Check 3: Detect PARTITION BY with flat current-mapping (retrospective bias)."""
    findings = []
    if not panel_build_sql_path.exists():
        return []

    text = panel_build_sql_path.read_text()
    # Pattern: PARTITION BY (date, <mapping_col>)
    partition_pattern = re.compile(
        r"PARTITION\s+BY\s+([^)]+)", re.IGNORECASE
    )
    suspect_mapping_cols = {"tdx_l1", "tdx_l2", "tdx_l3", "sw_l1", "sw_l2", "concept_id", "theme_id"}

    for m in partition_pattern.finditer(text):
        partition_expr = m.group(1)
        for col in suspect_mapping_cols:
            if col in partition_expr.lower():
                # Found PARTITION BY with mapping col. Check if source mapping is flat.
                # Need to inspect dim_* table characteristics.
                found_table = None
                for known in KNOWN_LEAKY_MAPPINGS:
                    if known in text and col[:3] in known:
                        found_table = known
                        break
                # Verify table flatness via DB
                if found_table:
                    try:
                        cols = [c[0] for c in conn.execute(f"SELECT * FROM {found_table} LIMIT 0").description]
                        has_pit = any(p in cols for p in KNOWN_PIT_MARKER_COLS - {"built_at"})
                        if not has_pit:
                            findings.append({
                                "check": "3_flat_mapping_partition",
                                "partition_col": col,
                                "source_table": found_table,
                                "risk": "HIGH",
                                "reason": f"PARTITION BY {col} from {found_table} (flat current-mapping, no PIT marker) = retrospective bias leakage (CLAUDE.md §4.5 反例 pattern)",
                                "context": m.group(0)[:200],
                            })
                    except Exception as exc:
                        # rule-compliance: ok evidence=audit script tolerant of table-introspection errors
                        findings.append({"check": "3_flat_mapping_partition_introspect_fail",
                                         "table": found_table, "risk": "LOW", "reason": str(exc)[:120]})
                break
    return findings


def audit_check_4_fallback_ratio(conn) -> list[dict]:
    """Check 4: Mapping tables fallback ratio."""
    findings = []
    # Check known mapping tables
    mapping_audits = [
        ("mart_stock_industry_pit", "confidence_level", "current_label_fallback"),
        # extensible: ("mart_stock_concept_pit", "confidence_level", "current_label_fallback"),
    ]
    for tbl, col, fallback_value in mapping_audits:
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            if total == 0:
                continue
            n_fallback = conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE {col} = ?", [fallback_value]
            ).fetchone()[0]
            ratio = n_fallback / total
            if ratio >= FALLBACK_RATIO_BLOCK_PCT:
                risk = "HIGH"
            elif ratio >= FALLBACK_RATIO_WARN_PCT:
                risk = "MEDIUM"
            else:
                continue
            findings.append({
                "check": "4_fallback_ratio",
                "table": tbl,
                "fallback_value": fallback_value,
                "fallback_ratio": round(ratio, 4),
                "n_fallback": n_fallback,
                "n_total": total,
                "risk": risk,
                "reason": f"{fallback_value} occupies {ratio*100:.2f}% of {tbl} (warn>5% block>50%, CLAUDE.md §4.5 反例 99.978%)",
            })
        except Exception as exc:
            # rule-compliance: ok evidence=audit tolerant of missing mart tables (e.g. mart_stock_concept_pit not yet built)
            findings.append({"check": "4_fallback_ratio_introspect_fail",
                             "table": tbl, "risk": "LOW", "reason": str(exc)[:120]})
    return findings


def audit_check_5_temporal_variance(conn, panel_table: str, sample_rows: int = 50000) -> list[dict]:
    """Check 5: Per-feature temporal variance — flag near-constant features (likely current mapping)."""
    findings = []
    try:
        cols = [c[0] for c in conn.execute(f"SELECT * FROM {panel_table} LIMIT 0").description]
    except Exception as e:
        return [{
            "check": "5_temporal_variance",
            "risk": "LOW",
            "reason": f"panel table {panel_table} not found: {e}",
        }]
    feature_cols = [
        c for c in cols
        if c not in {"stock_code", "signal_date", "built_at", "trade_date_dt", "entry_date"}
        and not c.startswith("fwd_") and c != "feature_version"
    ]
    if not feature_cols:
        return findings

    # Sample data
    col_list = ", ".join(feature_cols + ["signal_date", "stock_code"])
    df = pd.DataFrame(
        conn.execute(
            f"SELECT {col_list} FROM {panel_table} USING SAMPLE {sample_rows} ROWS"
        ).fetchall(),
        columns=feature_cols + ["signal_date", "stock_code"],
    )
    if df.empty:
        return findings

    for col in feature_cols:
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().sum() < 100:
            continue
        # Per-stock temporal variance: how much does this feature change for the same stock over time?
        # If feature is constant-per-stock = potential current mapping
        stock_std = df.groupby("stock_code")[col].apply(lambda x: pd.to_numeric(x, errors="coerce").std())
        n_stocks_constant = (stock_std == 0).sum()
        pct_constant = n_stocks_constant / max(len(stock_std), 1)
        if pct_constant > 0.95:
            findings.append({
                "check": "5_temporal_variance",
                "feature": col,
                "risk": "MEDIUM",
                "reason": f"{pct_constant*100:.1f}% of stocks have 0 temporal std (constant-per-stock = likely current mapping)",
                "n_stocks_constant": int(n_stocks_constant),
                "n_stocks_sampled": int(len(stock_std)),
            })
        # also check overall low variance (cast to float so bool dtype doesn't break arithmetic)
        s_num = s.astype(float)
        full_std = float(s_num.std())
        smax = float(s_num.max()) if pd.notna(s_num.max()) else 0.0
        smin = float(s_num.min()) if pd.notna(s_num.min()) else 0.0
        full_range = smax - smin if smax != smin else 1.0
        cv = full_std / abs(full_range) if full_range else 0
        if cv < TEMPORAL_VARIANCE_FLAG_PCT and s.nunique() < 20:
            findings.append({
                "check": "5_temporal_variance",
                "feature": col,
                "risk": "MEDIUM",
                "reason": f"coefficient of variation {cv:.4f} < {TEMPORAL_VARIANCE_FLAG_PCT} + nunique={s.nunique()} (near-constant, possible mapping/flag)",
                "cv": round(cv, 4),
                "nunique": int(s.nunique()),
            })
    return findings


def audit_check_6_null_year_gradient(conn, panel_table: str) -> list[dict]:
    """Check 6: Per-feature NULL ratio by year — flag time-availability leakage.

    2026-05-22 Phase 4 v6 audit 反例: panel v4 cols (inst_holder_cnt 100/100/54/7%,
    beta_60d 100/3/2/18%, etc) showed NULL ratio gradient across years, allowing ML
    to indirectly learn "feature non-NULL = recent period = bull regime" → time leak.

    Flag: gradient max(NULL%) - min(NULL%) > 50% → HIGH; 20-50% → MEDIUM.
    """
    findings = []
    try:
        cols = [c[0] for c in conn.execute(f"SELECT * FROM {panel_table} LIMIT 0").description]
    except Exception as exc:
        return [{"check": "6_null_year_gradient", "risk": "LOW", "reason": f"{panel_table} not found: {exc}"}]

    feature_cols = [
        c for c in cols
        if c not in {"stock_code", "signal_date", "built_at", "trade_date_dt", "entry_date", "feature_version"}
        and not c.startswith("fwd_") and not c.startswith("y_")
    ]
    for col in feature_cols:
        try:
            rows = conn.execute(
                f"""
                SELECT EXTRACT(YEAR FROM signal_date) AS yr,
                       ROUND(100.0 * SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2) AS np
                  FROM {panel_table} GROUP BY yr ORDER BY yr
                """
            ).fetchall()
        except Exception:
            continue
        if len(rows) < 2:
            continue
        nulls = [float(r[1]) for r in rows if r[1] is not None]
        if not nulls or len(nulls) < 2:
            continue
        gradient = max(nulls) - min(nulls)
        # rule-compliance: ok evidence=Phase 4 v6 IS-OOS drop 60% root cause = 4 cols with gradient > 50%
        if gradient > 50:
            findings.append({
                "check": "6_null_year_gradient", "feature": col, "risk": "HIGH",
                "reason": f"NULL gradient {gradient:.1f}% across years (time-availability leak)",
                "yearly_null_pct": {int(r[0]): float(r[1]) for r in rows if r[1] is not None},
            })
        elif gradient > 20:
            findings.append({
                "check": "6_null_year_gradient", "feature": col, "risk": "MEDIUM",
                "reason": f"NULL gradient {gradient:.1f}% across years (mild time-availability bias)",
                "yearly_null_pct": {int(r[0]): float(r[1]) for r in rows if r[1] is not None},
            })
    return findings


def audit_check_7_forward_index_grep(panel_build_sql_path: Path) -> list[dict]:
    """Check 7: AST/grep for forward-index patterns in feature engineering files.

    Pattern 3 in catalog: `bars[sig_i+1:]`, `pd.shift(-N)`, `.iloc[i+N]`, etc.
    These directly access future data → 假 PnL.
    """
    findings = []
    # Scan panel build + related feature service files
    targets = [panel_build_sql_path]
    services_features = Path("backend/services/features")
    if services_features.exists():
        targets.extend(services_features.rglob("*.py"))

    forward_patterns = [
        re.compile(r"\bshift\s*\(\s*-\d+", re.IGNORECASE),  # shift(-N)
        re.compile(r"\biloc\[\s*\w+\s*\+\s*\d+", re.IGNORECASE),  # iloc[i+N]
        re.compile(r"bars\[\s*\w+\s*\+\s*\d+\s*:", re.IGNORECASE),  # bars[sig_i+1:]
        re.compile(r"close_array\[\s*i\s*\+", re.IGNORECASE),
    ]
    for tgt in targets:
        try:
            text = Path(tgt).read_text()
        except Exception:
            continue
        for pat in forward_patterns:
            for m in pat.finditer(text):
                # Get line number
                line_no = text[:m.start()].count("\n") + 1
                # Check if it's in a comment (basic skip)
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_text = text[line_start:text.find("\n", m.start())]
                if line_text.strip().startswith("#"):
                    continue
                findings.append({
                    "check": "7_forward_index", "file": str(tgt),
                    "line": line_no, "match": m.group(0), "risk": "HIGH",
                    "reason": f"forward-index pattern detected: {m.group(0)} (Pattern 3 in catalog)",
                })
    return findings


def audit_check_8_universe_pit(panel_build_sql_path: Path) -> list[dict]:
    """Check 8: Universe selection PIT - grep for retrospective universe filters.

    Pattern 6 in catalog: `WHERE listed_today=1` / `WHERE active=1` / `dim_active_a_stock`
    without `as_of_date <= signal_date`. Causes survivorship bias.
    """
    findings = []
    if not panel_build_sql_path.exists():
        return findings
    text = panel_build_sql_path.read_text()
    retro_patterns = [
        re.compile(r"WHERE\s+listed_today\s*=\s*1", re.IGNORECASE),
        re.compile(r"WHERE\s+active\s*=\s*1\b", re.IGNORECASE),
        re.compile(r"FROM\s+dim_active_a_stock\b", re.IGNORECASE),
        re.compile(r"FROM\s+dim_all_ever_listed\b", re.IGNORECASE),
    ]
    pit_predicates = ["as_of_date", "effective_from", "effective_to", "<= signal_date"]
    lines = text.split("\n")
    for i, line in enumerate(lines):
        for pat in retro_patterns:
            if pat.search(line):
                # Check ±5 lines context for PIT predicates
                context = "\n".join(lines[max(0, i - 3):min(len(lines), i + 8)])
                has_pit = any(p.lower() in context.lower() for p in pit_predicates)
                if not has_pit:
                    findings.append({
                        "check": "8_universe_pit", "line": i + 1, "risk": "HIGH",
                        "reason": "universe filter without PIT predicate (survivorship bias risk)",
                        "context": line.strip()[:200],
                    })
    return findings


def audit_check_10_survivorship_bias(conn) -> list[dict]:
    """Check 10: Verify panel includes delisted stocks until delist_date.

    Pattern 8 in catalog: 只用现存上市股 in training → 实盘 buys 退市股 unmodeled.
    Check: count distinct stocks in panel vs ever-listed in dim_listing_status.
    """
    findings = []
    try:
        r1 = conn.execute(
            "SELECT COUNT(DISTINCT stock_code) FROM mart_p0a_feature_label_panel_v4"
        ).fetchone()
        panel_stocks = r1[0] if r1 else 0
        # Get ever-listed count (if dim_listing_status exists)
        try:
            r2 = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM dim_all_ever_listed").fetchone()
            ever_listed = r2[0] if r2 else 0
        except Exception:
            ever_listed = None

        try:
            r3 = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM dim_active_a_stock").fetchone()
            active = r3[0] if r3 else 0
        except Exception:
            active = None

        if ever_listed and panel_stocks < ever_listed * 0.95:
            findings.append({
                "check": "10_survivorship_bias", "risk": "HIGH",
                "panel_stocks": panel_stocks, "ever_listed": ever_listed,
                "reason": f"panel has {panel_stocks} stocks vs ever_listed {ever_listed} (missing {ever_listed - panel_stocks} delisted; survivorship bias suspect)",
            })
        elif active and panel_stocks <= active:
            # panel ≈ active stocks → potential survivorship
            findings.append({
                "check": "10_survivorship_bias", "risk": "MEDIUM",
                "panel_stocks": panel_stocks, "active": active,
                "reason": f"panel ({panel_stocks}) approx equals active ({active}); verify delisted stocks included in training",
            })
    except Exception as exc:
        findings.append({"check": "10_survivorship_bias", "risk": "LOW", "reason": str(exc)[:120]})
    return findings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--panel", default="mart_p0a_feature_label_panel_v4",
                   help="panel table to audit")
    p.add_argument("--db", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--panel-build-sql",
                   default=str(REPO_ROOT / "backend" / "scripts" / "build_feature_panel_duck.py"),
                   help="panel build script to audit JOIN patterns")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 even on MEDIUM (default: exit 1 only on HIGH)")
    p.add_argument("--report-dir", default=str(REPO_ROOT / "data" / "reports" / "leakage_audit"))
    p.add_argument("--sample-rows", type=int, default=50000)
    args = p.parse_args()

    Path(args.report_dir).mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    out_path = Path(args.report_dir) / f"{args.panel}_{now.strftime('%Y%m%dT%H%M%S')}.json"

    print(f"[audit-leakage] panel={args.panel}, db={args.db}")
    print(f"[audit-leakage] panel build SQL: {args.panel_build_sql}")
    print()

    all_findings = []
    with connect(args.db, read_only=True) as conn:
        print("[1/9] PIT markers on fact_*/mart_*/dim_* tables ...")
        all_findings.extend(audit_check_1_pit_markers(conn))
        print("[2/9] Panel JOIN PIT-strict pattern ...")
        all_findings.extend(audit_check_2_panel_join_pit(Path(args.panel_build_sql)))
        print("[3/9] Flat current-mapping PARTITION BY (retrospective bias) ...")
        all_findings.extend(audit_check_3_flat_mapping_partition(Path(args.panel_build_sql), conn))
        print("[4/9] Mapping table fallback ratio ...")
        all_findings.extend(audit_check_4_fallback_ratio(conn))
        print(f"[5/9] Per-feature temporal variance (sample {args.sample_rows} rows) ...")
        all_findings.extend(audit_check_5_temporal_variance(conn, args.panel, args.sample_rows))
        print(f"[6/9] Per-feature NULL ratio gradient across years ...")
        all_findings.extend(audit_check_6_null_year_gradient(conn, args.panel))
        print(f"[7/9] Forward-index pattern grep (feature code) ...")
        all_findings.extend(audit_check_7_forward_index_grep(Path(args.panel_build_sql)))
        print(f"[8/9] Universe selection PIT predicate ...")
        all_findings.extend(audit_check_8_universe_pit(Path(args.panel_build_sql)))
        print(f"[9/9] Survivorship bias (panel stocks vs ever_listed) ...")
        all_findings.extend(audit_check_10_survivorship_bias(conn))

    n_high = sum(1 for f in all_findings if f.get("risk") == "HIGH")
    n_medium = sum(1 for f in all_findings if f.get("risk") == "MEDIUM")
    n_low = sum(1 for f in all_findings if f.get("risk") == "LOW")

    # Save full report
    report = {
        "panel": args.panel,
        "audit_time": now.isoformat(),
        "n_findings": len(all_findings),
        "n_high": n_high,
        "n_medium": n_medium,
        "n_low": n_low,
        "findings": all_findings,
    }
    out_path.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False))

    print()
    print("=" * 80)
    print(f"AUDIT SUMMARY — panel {args.panel}")
    print("=" * 80)
    print(f"  HIGH:   {n_high}")
    print(f"  MEDIUM: {n_medium}")
    print(f"  LOW:    {n_low}")
    print(f"  full report: {out_path}")
    print()

    # Print top findings per risk level
    for risk in ("HIGH", "MEDIUM"):
        risk_findings = [f for f in all_findings if f.get("risk") == risk]
        if not risk_findings:
            continue
        print(f"--- {risk} findings ---")
        for f in risk_findings[:20]:
            print(f"  [{f['check']}] {f.get('table', f.get('feature', f.get('joined_table', '?')))}: {f['reason']}")
        if len(risk_findings) > 20:
            print(f"  ... +{len(risk_findings) - 20} more")
        print()

    # Exit code
    if n_high > 0:
        print(f"[audit-leakage] BLOCK: {n_high} HIGH-risk findings (e.g. flat current-mapping in panel)")
        return 1
    if args.strict and n_medium > 0:
        print(f"[audit-leakage] BLOCK (strict mode): {n_medium} MEDIUM-risk findings")
        return 1
    if n_medium > 0:
        print(f"[audit-leakage] WARN: {n_medium} MEDIUM-risk findings (use --strict to block)")
        return 2
    print(f"[audit-leakage] OK: no HIGH risk found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
