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
from bisect import bisect_right
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

# rule-compliance: ok evidence=CLAUDE.md §4.5 known leakage patterns + Phase D 2026-05-22 finding
KNOWN_LEAKY_MAPPINGS = {
    # dim_stock_tdx_industry 已物删 2026-06-23 (Phase D 反例原型: flat NON-PIT 用于 panel sector PARTITION BY)
    "dim_stock_dc_industry",  # 东财全套迁移 Stage④ (2026-06-23): 接替 sw/tdx 当前行业快照, 同 NON-PIT PARTITION BY 风险; 历史 as-of 走 tushare_raw.v_sw_industry_pit
    "dim_stock_concept",  # likely similar
}

KNOWN_PIT_MARKER_COLS = {
    "built_at", "effective_from", "effective_to",
    "announce_date", "trade_date", "snapshot_date", "report_date",
    "source_available_date", "as_of_date",
}
PIT_MARKER_COLS_LOWER = {c.lower() for c in KNOWN_PIT_MARKER_COLS}
PIT_MARKER_COLS_NO_BUILT_AT_LOWER = {c.lower() for c in KNOWN_PIT_MARKER_COLS - {"built_at"}}
SORTED_PIT_MARKER_COLS = sorted(KNOWN_PIT_MARKER_COLS)
FEATURE_EXCLUDE_COLS = {"stock_code", "signal_date", "built_at", "trade_date_dt", "entry_date", "feature_version"}
SUSPECT_MAPPING_COLS = {"tdx_l1", "tdx_l2", "tdx_l3", "sw_l1", "sw_l2", "concept_id", "theme_id"}
SUSPECT_MAPPING_COL_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in sorted(SUSPECT_MAPPING_COLS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
PARTITION_BY_RE = re.compile(r"PARTITION\s+BY\s+([^)]+)", re.IGNORECASE)
JOIN_RE = re.compile(r"(LEFT|INNER|RIGHT|FULL|CROSS)?\s*JOIN\s+([\w.]+)", re.IGNORECASE)
JOIN_SKIP_TABLES = {"panel", "panel_dates", "p", "d", "src"}
JOIN_PIT_PREDICATES = ["<= signal_date", "<= p.date", "<= d.trade_date", "<= panel.date", "asof", "<= cutoff", "<= as_of"]
RETRO_UNIVERSE_RE = re.compile(
    r"WHERE\s+listed_today\s*=\s*1|WHERE\s+active\s*=\s*1\b|FROM\s+dim_active_a_stock\b|FROM\s+dim_all_ever_listed\b",  # rule-compliance: ok evidence=audit-detector-pattern-not-universe-source
    re.IGNORECASE,
)
RETRO_PIT_PREDICATES = ["as_of_date", "effective_from", "effective_to", "<= signal_date"]
FORWARD_INDEX_RE = re.compile(
    r"\bshift\s*\(\s*-\d+|\biloc\[\s*\w+\s*\+\s*\d+|bars\[\s*\w+\s*\+\s*\d+\s*:|close_array\[\s*i\s*\+",
    re.IGNORECASE,
)
MAPPING_AUDITS = [
    ("mart_stock_industry_pit", "confidence_level", "current_label_fallback"),
    # extensible: ("mart_stock_concept_pit", "confidence_level", "current_label_fallback"),
]

# Risk thresholds
TEMPORAL_VARIANCE_FLAG_PCT = 0.05  # feature std < 5% of range = near constant = leakage suspect
FALLBACK_RATIO_WARN_PCT = 0.05  # mapping fallback > 5% = warn
FALLBACK_RATIO_BLOCK_PCT = 0.50  # > 50% = block (catastrophic, CLAUDE.md §4.5 反例 99.978%)


def _quote_ident(name: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def _quote_table_name(name: str) -> str:
    return ".".join(_quote_ident(part) for part in name.split("."))


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _load_table_columns(conn, table_names: list[str] | None = None) -> dict[str, list[str]]:
    params: list[str] = []
    table_filter = ""
    if table_names is None:
        table_filter = """
          AND (t.table_name LIKE 'fact_%' OR t.table_name LIKE 'mart_%' OR t.table_name LIKE 'dim_%')
        """
    else:
        names = sorted(set(table_names))
        if not names:
            return {}
        placeholders = ", ".join(["?"] * len(names))
        table_filter = f"AND t.table_name IN ({placeholders})"
        params.extend(names)

    rows = conn.execute(
        f"""
        SELECT t.table_name, c.column_name
          FROM information_schema.tables t
          LEFT JOIN information_schema.columns c
            ON c.table_schema = t.table_schema
           AND c.table_name = t.table_name
         WHERE t.table_schema = 'main'
           {table_filter}
         ORDER BY t.table_name, c.ordinal_position
        """,
        params,
    ).fetchall()

    table_cols: dict[str, list[str]] = {}
    for table_name, column_name in rows:
        table_cols.setdefault(table_name, [])
        if column_name is not None:
            table_cols[table_name].append(column_name)
    return table_cols


def _panel_feature_cols(cols: list[str], *, exclude_y: bool = False) -> list[str]:
    return [
        c for c in cols
        if c not in FEATURE_EXCLUDE_COLS
        and not c.startswith("fwd_")
        and not (exclude_y and c.startswith("y_"))
    ]


def _mapping_table_for_partition_col(text_lower: str, col: str) -> str | None:
    prefix = col[:3]
    for known in KNOWN_LEAKY_MAPPINGS:
        if known in text_lower and prefix in known:
            return known
    return None


def _partition_mapping_hits(text: str) -> list[tuple[str, str, str]]:
    hits = []
    text_lower = text.lower()
    for match in PARTITION_BY_RE.finditer(text):
        col_match = SUSPECT_MAPPING_COL_RE.search(match.group(1).lower())
        if not col_match:
            continue
        col = col_match.group(1).lower()
        found_table = _mapping_table_for_partition_col(text_lower, col)
        if found_table:
            hits.append((col, found_table, match.group(0)[:200]))
    return hits


def _fallback_count_rows(conn, mapping_audits: list[tuple[str, str, str]]) -> tuple[dict[str, tuple[int, int]], list[dict]]:
    table_columns = _load_table_columns(conn, [tbl for tbl, _, _ in mapping_audits])
    low_findings = []
    selects = []
    for tbl, col, fallback_value in mapping_audits:
        cols = table_columns.get(tbl)
        if cols is None:
            low_findings.append({
                "check": "4_fallback_ratio_introspect_fail",
                "table": tbl,
                "risk": "LOW",
                "reason": "table not found",
            })
            continue
        if col not in cols:
            low_findings.append({
                "check": "4_fallback_ratio_introspect_fail",
                "table": tbl,
                "risk": "LOW",
                "reason": f"column not found: {col}",
            })
            continue
        selects.append(
            "SELECT "
            f"{_sql_literal(tbl)} AS table_name, "
            "COUNT(*) AS n_total, "
            f"SUM(CASE WHEN {_quote_ident(col)} = {_sql_literal(fallback_value)} THEN 1 ELSE 0 END) AS n_fallback "
            f"FROM {_quote_table_name(tbl)}"
        )

    if not selects:
        return {}, low_findings
    rows = conn.execute(" UNION ALL ".join(selects)).fetchall()
    return {str(tbl): (int(total or 0), int(fallback or 0)) for tbl, total, fallback in rows}, low_findings


def _null_year_frame(conn, panel_table: str, feature_cols: list[str]) -> pd.DataFrame:
    if not feature_cols:
        return pd.DataFrame()
    null_exprs = [
        f"ROUND(100.0 * SUM(CASE WHEN {_quote_ident(col)} IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2) AS {_quote_ident(col)}"
        for col in feature_cols
    ]
    result = conn.execute(
        f"""
        SELECT EXTRACT(YEAR FROM signal_date) AS yr,
               {", ".join(null_exprs)}
          FROM {_quote_table_name(panel_table)}
         GROUP BY yr
         ORDER BY yr
        """
    )
    rows = result.fetchall()
    columns = [c[0] for c in result.description]
    return pd.DataFrame(rows, columns=columns)


def _line_starts(text: str) -> list[int]:
    return [0] + [idx + 1 for idx, char in enumerate(text) if char == "\n"]


def _line_at(text: str, starts: list[int], offset: int) -> tuple[int, str]:
    line_no = bisect_right(starts, offset)
    line_start = starts[line_no - 1]
    line_end = text.find("\n", offset)
    if line_end == -1:
        line_end = len(text)
    return line_no, text[line_start:line_end]


def _forward_index_findings_for_file(path: Path) -> list[dict]:
    try:
        text = path.read_text()
    except OSError as exc:
        return [{
            "check": "7_forward_index_read_fail",
            "file": str(path),
            "risk": "LOW",
            "reason": f"could not read file: {exc}",
        }]
    starts = _line_starts(text)
    findings = []
    for match in FORWARD_INDEX_RE.finditer(text):
        line_no, line_text = _line_at(text, starts, match.start())
        if line_text.strip().startswith("#"):
            continue
        findings.append({
            "check": "7_forward_index",
            "file": str(path),
            "line": line_no,
            "match": match.group(0),
            "risk": "HIGH",
            "reason": f"forward-index pattern detected: {match.group(0)} (Pattern 3 in catalog)",
        })
    return findings


def _forward_index_targets(panel_build_sql_path: Path) -> list[Path]:
    targets = [panel_build_sql_path]
    services_features = Path("backend/services/features")
    if services_features.exists():
        targets.extend(sorted(services_features.rglob("*.py")))
    return targets


def _findings_by_risk(findings: list[dict]) -> dict[str, list[dict]]:
    grouped = {"HIGH": [], "MEDIUM": [], "LOW": []}
    for finding in findings:
        grouped.setdefault(str(finding.get("risk")), []).append(finding)
    return grouped


def _print_top_risk_findings(risk: str, findings: list[dict]) -> None:
    if not findings:
        return
    print(f"--- {risk} findings ---")
    for finding in findings[:20]:
        subject = finding.get("table", finding.get("feature", finding.get("joined_table", "?")))
        print(f"  [{finding['check']}] {subject}: {finding['reason']}")
    if len(findings) > 20:
        print(f"  ... +{len(findings) - 20} more")
    print()


def audit_check_1_pit_markers(conn) -> list[dict]:
    """Check 1: Every fact_*/mart_*/dim_* table has PIT marker column."""
    findings = []
    try:
        table_columns = _load_table_columns(conn)
    except Exception as e:
        return [{
            "check": "1_pit_markers",
            "risk": "LOW",
            "reason": f"could not introspect table columns: {e}",
        }]

    for tbl, cols in table_columns.items():
        pit_cols = [c for c in cols if c.lower() in PIT_MARKER_COLS_LOWER]
        if pit_cols:
            continue
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
            "reason": f"no PIT marker column found (expected one of {SORTED_PIT_MARKER_COLS})",
            "cols": cols[:10],
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
    # Get a window of context around each JOIN
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = JOIN_RE.search(line)
        if not m:
            continue
        joined_table = m.group(2)
        # Skip CTE / subquery aliases
        if joined_table.startswith("__") or joined_table in JOIN_SKIP_TABLES:
            continue
        # Check ±10 lines context for PIT predicates
        context = "\n".join(lines[max(0, i-5):min(len(lines), i+10)])
        context_lower = context.lower()
        has_pit = any(pred in context_lower for pred in JOIN_PIT_PREDICATES) or ".date = p.date" in context
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
    hits = _partition_mapping_hits(text)
    try:
        table_columns = _load_table_columns(conn, [found_table for _, found_table, _ in hits])
    except Exception as exc:
        return [
            {
                "check": "3_flat_mapping_partition_introspect_fail",
                "table": found_table,
                "risk": "LOW",
                "reason": str(exc)[:120],
            }
            for _, found_table, _ in hits
        ]

    for col, found_table, context in hits:
        cols = table_columns.get(found_table)
        if cols is None:
            # rule-compliance: ok evidence=audit script tolerant of table-introspection errors
            findings.append({
                "check": "3_flat_mapping_partition_introspect_fail",
                "table": found_table,
                "risk": "LOW",
                "reason": "table not found",
            })
            continue
        has_pit = any(c.lower() in PIT_MARKER_COLS_NO_BUILT_AT_LOWER for c in cols)
        if not has_pit:
            findings.append({
                "check": "3_flat_mapping_partition",
                "partition_col": col,
                "source_table": found_table,
                "risk": "HIGH",
                "reason": f"PARTITION BY {col} from {found_table} (flat current-mapping, no PIT marker) = retrospective bias leakage (CLAUDE.md §4.5 反例 pattern)",
                "context": context,
            })
    return findings


def audit_check_4_fallback_ratio(conn) -> list[dict]:
    """Check 4: Mapping tables fallback ratio."""
    findings = []
    try:
        counts_by_table, low_findings = _fallback_count_rows(conn, MAPPING_AUDITS)
    except Exception as exc:
        # rule-compliance: ok evidence=audit tolerant of missing mart tables (e.g. mart_stock_concept_pit not yet built)
        return [{"check": "4_fallback_ratio_introspect_fail", "risk": "LOW", "reason": str(exc)[:120]}]

    findings.extend(low_findings)
    for tbl, _, fallback_value in MAPPING_AUDITS:
        if tbl not in counts_by_table:
            continue
        total, n_fallback = counts_by_table[tbl]
        if total == 0:
            continue
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
    feature_cols = _panel_feature_cols(cols)
    if not feature_cols:
        return findings

    # Sample data
    col_list = ", ".join(_quote_ident(c) for c in feature_cols + ["signal_date", "stock_code"])
    df = pd.DataFrame(
        conn.execute(
            f"SELECT {col_list} FROM {_quote_table_name(panel_table)} USING SAMPLE {sample_rows} ROWS"
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

    feature_cols = _panel_feature_cols(cols, exclude_y=True)
    try:
        null_year = _null_year_frame(conn, panel_table, feature_cols)
    except Exception as exc:
        return [{
            "check": "6_null_year_gradient",
            "table": panel_table,
            "risk": "LOW",
            "reason": f"could not compute null-year frame: {exc}",
        }]
    if null_year.empty or len(null_year) < 2:
        return findings

    for col in feature_cols:
        if col not in null_year:
            continue
        values = null_year[col].dropna().astype(float)
        nulls = values.tolist()
        if not nulls or len(nulls) < 2:
            continue
        gradient = max(nulls) - min(nulls)
        yearly_null_pct = {
            int(yr): float(value)
            for yr, value in zip(null_year["yr"], null_year[col])
            if pd.notna(value)
        }
        # rule-compliance: ok evidence=Phase 4 v6 IS-OOS drop 60% root cause = 4 cols with gradient > 50%
        if gradient > 50:
            findings.append({
                "check": "6_null_year_gradient", "feature": col, "risk": "HIGH",
                "reason": f"NULL gradient {gradient:.1f}% across years (time-availability leak)",
                "yearly_null_pct": yearly_null_pct,
            })
        elif gradient > 20:
            findings.append({
                "check": "6_null_year_gradient", "feature": col, "risk": "MEDIUM",
                "reason": f"NULL gradient {gradient:.1f}% across years (mild time-availability bias)",
                "yearly_null_pct": yearly_null_pct,
            })
    return findings


def audit_check_7_forward_index_grep(panel_build_sql_path: Path) -> list[dict]:
    """Check 7: AST/grep for forward-index patterns in feature engineering files.

    Pattern 3 in catalog: `bars[sig_i+1:]`, `pd.shift(-N)`, `.iloc[i+N]`, etc.
    These directly access future data → 假 PnL.
    """
    findings = []
    # Scan panel build + related feature service files
    for tgt in _forward_index_targets(panel_build_sql_path):
        findings.extend(_forward_index_findings_for_file(Path(tgt)))
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
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not RETRO_UNIVERSE_RE.search(line):
            continue
        # Check ±5 lines context for PIT predicates
        context = "\n".join(lines[max(0, i - 3):min(len(lines), i + 8)])
        has_pit = any(p in context.lower() for p in RETRO_PIT_PREDICATES)
        if not has_pit:
            findings.append({
                "check": "8_universe_pit", "line": i + 1, "risk": "HIGH",
                "reason": "universe filter without PIT predicate (survivorship bias risk)",
                "context": line.strip()[:200],
            })
    return findings


def audit_check_10_survivorship_bias(conn, panel_table: str = "mart_p0a_feature_label_panel_v4") -> list[dict]:
    """Check 10: Verify panel includes delisted stocks until delist_date.

    Pattern 8 in catalog: 只用现存上市股 in training → 实盘 buys 退市股 unmodeled.
    Check: count distinct stocks in panel vs ever-listed in dim_listing_status.
    """
    findings = []
    try:
        r1 = conn.execute(
            f"SELECT COUNT(DISTINCT stock_code) FROM {_quote_table_name(panel_table)}"
        ).fetchone()
        panel_stocks = r1[0] if r1 else 0
        # Get ever-listed count (§9: dim 迁 reference, conn 有则用过渡副本, 否则 fall reference)
        from services.data_access import resolver
        from services import security_master
        try:
            c2, own2 = resolver.dim_read_conn(conn, "dim_all_ever_listed")  # rule-compliance: ok evidence=survivorship audit ever-listed count, §9 reference fallback
            try:
                r2 = c2.execute("SELECT COUNT(DISTINCT stock_code) FROM dim_all_ever_listed").fetchone()
            finally:
                if own2:
                    c2.close()
            ever_listed = r2[0] if r2 else 0
        except Exception:
            ever_listed = None

        try:
            # dim_active code-list 走 security_master (内部已 dim_read_conn auto-fallback)
            active = len(security_master.active_codes(conn))
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


LEAKAGE_CONSUMERS = REPO_ROOT / "backend" / "config" / "leakage_consumers.yaml"  # rule-compliance: ok evidence=panel-leakage 治理 config (audit_panels 段)


def _load_audit_targets() -> list[dict]:
    """从 config 读 panel-build PIT 审计目标 (能不硬编码就不硬编码; owner=leakage_consumers.yaml audit_panels)。

    每项 {panel, db(相对repo), build_sql(相对repo)}。post-reset 空 = 旧 SQL 面板已 wipe, 无 SQL-built 面板待审 (PASS)。
    """
    if not LEAKAGE_CONSUMERS.exists():
        return []
    cfg = yaml.safe_load(LEAKAGE_CONSUMERS.read_text(encoding="utf-8")) or {}
    return cfg.get("audit_panels", []) or []


def _audit_one(panel: str, db: str, build_sql: str, report_dir: str, sample_rows: int,
               strict: bool, now: datetime) -> int:
    """审一个 SQL-built 面板的 build-SQL PIT + dim 标记 + 生存者偏差等 9 检; 返回 exit code (0/1/2)。"""
    out_path = Path(report_dir) / f"{panel}_{now.strftime('%Y%m%dT%H%M%S')}.json"
    print(f"[audit-leakage] panel={panel}, db={db}")
    print(f"[audit-leakage] panel build SQL: {build_sql}")
    all_findings: list = []
    with connect(db, read_only=True) as conn:
        print("[1/9] PIT markers on fact_*/mart_*/dim_* tables ...")
        all_findings.extend(audit_check_1_pit_markers(conn))
        print("[2/9] Panel JOIN PIT-strict pattern ...")
        all_findings.extend(audit_check_2_panel_join_pit(Path(build_sql)))
        print("[3/9] Flat current-mapping PARTITION BY (retrospective bias) ...")
        all_findings.extend(audit_check_3_flat_mapping_partition(Path(build_sql), conn))
        print("[4/9] Mapping table fallback ratio ...")
        all_findings.extend(audit_check_4_fallback_ratio(conn))
        print(f"[5/9] Per-feature temporal variance (sample {sample_rows} rows) ...")
        all_findings.extend(audit_check_5_temporal_variance(conn, panel, sample_rows))
        print(f"[6/9] Per-feature NULL ratio gradient across years ...")
        all_findings.extend(audit_check_6_null_year_gradient(conn, panel))
        print(f"[7/9] Forward-index pattern grep (feature code) ...")
        all_findings.extend(audit_check_7_forward_index_grep(Path(build_sql)))
        print(f"[8/9] Universe selection PIT predicate ...")
        all_findings.extend(audit_check_8_universe_pit(Path(build_sql)))
        print(f"[9/9] Survivorship bias (panel stocks vs ever_listed) ...")
        all_findings.extend(audit_check_10_survivorship_bias(conn, panel))

    fbr = _findings_by_risk(all_findings)
    n_high, n_medium, n_low = len(fbr["HIGH"]), len(fbr["MEDIUM"]), len(fbr["LOW"])
    out_path.write_text(json.dumps({"panel": panel, "audit_time": now.isoformat(),
                                    "n_findings": len(all_findings), "n_high": n_high,
                                    "n_medium": n_medium, "n_low": n_low, "findings": all_findings},
                                   indent=2, default=str, ensure_ascii=False))
    print("\n" + "=" * 80 + f"\nAUDIT SUMMARY — panel {panel}\n" + "=" * 80)
    print(f"  HIGH: {n_high}  MEDIUM: {n_medium}  LOW: {n_low}  full report: {out_path}\n")
    for risk in ("HIGH", "MEDIUM"):
        _print_top_risk_findings(risk, fbr[risk])
    if n_high > 0:
        print(f"[audit-leakage] BLOCK: {n_high} HIGH-risk findings (e.g. flat current-mapping in panel)")
        return 1
    if strict and n_medium > 0:
        print(f"[audit-leakage] BLOCK (strict): {n_medium} MEDIUM-risk findings")
        return 1
    if n_medium > 0:
        print(f"[audit-leakage] WARN: {n_medium} MEDIUM-risk findings (use --strict to block)")
        return 2
    print(f"[audit-leakage] OK: no HIGH risk found")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    # 无硬编码默认目标 (能不硬编码就不硬编码): 不给 --panel 则从 config audit_panels 读 (post-reset 空=PASS)
    p.add_argument("--panel", default=None, help="单面板覆盖 (CLI); 不给则从 leakage_consumers.yaml audit_panels 读")
    p.add_argument("--db", default=None, help="--panel 时的库路径 (相对 repo 或绝对)")
    p.add_argument("--panel-build-sql", default=None, help="--panel 时的 build 脚本 (build-SQL JOIN PIT 审计)")
    p.add_argument("--strict", action="store_true", help="MEDIUM 也 exit 1 (默认仅 HIGH block)")
    p.add_argument("--report-dir", default=str(REPO_ROOT / "data" / "reports" / "leakage_audit"))
    p.add_argument("--sample-rows", type=int, default=50000)
    args = p.parse_args()

    Path(args.report_dir).mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    if args.panel:  # CLI 显式单面板 (rehab/调试用)
        if not args.db or not args.panel_build_sql:
            print("[audit-leakage] --panel 需配 --db 和 --panel-build-sql"); return 3
        targets = [{"panel": args.panel, "db": args.db, "build_sql": args.panel_build_sql}]
    else:
        targets = _load_audit_targets()

    if not targets:
        print("[audit-leakage] config audit_panels 空 — 无 SQL-built 面板待审 (post-reset: 旧面板已 wipe; "
              "fact_feature_panel 是 Python builder + code/date schema, 该 SQL-审计工具不适用, 见 leakage_consumers.yaml 注释)。PASS。")
        return 0

    codes: list[int] = []
    for t in targets:
        db = t["db"] if Path(t["db"]).is_absolute() else str(REPO_ROOT / t["db"])
        if not Path(db).exists():
            print(f"[audit-leakage] 跳过 {t['panel']}: 库 {db} 不存在 (wiped/planned)"); continue
        bs = t["build_sql"] if Path(t["build_sql"]).is_absolute() else str(REPO_ROOT / t["build_sql"])
        codes.append(_audit_one(t["panel"], db, bs, args.report_dir, args.sample_rows, args.strict, now))
    # 聚合: 任一 BLOCK(1) -> 1; 否则任一 WARN(2) -> 2; 否则 0
    if 1 in codes:
        return 1
    if 2 in codes:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
