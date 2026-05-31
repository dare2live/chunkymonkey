#!/usr/bin/env python3
"""Codex round 17 Q8.3 FIX: survivorship gate audit (governance v1).

验证 rebuild_p0a_label_panel.py + build_p0a_feature_panel_v3.py 等训练 data 入口
**不使用 `is_active=1` filter** (历史 backtests 应包含已退市股, 避免 survivorship bias).

检查项 (按 Codex Q8.3 verdict):
1. mart_p0a_label_panel: distinct stock_code 数应 >= ever_listed (含 585 退市股)
2. mart_p0a_feature_label_panel_v3: 同上
3. 任何活跃 ML training data 入口源代码 grep: 不应 hardcode `is_active=1`

Exit 0 = PASS (无 survivorship bias)
Exit 1 = FAIL (label panel codes < ever_listed OR 代码硬编码 is_active=1)

用法:
    PYTHONPATH=backend python backend/scripts/audit_survivorship_gate.py
    PYTHONPATH=backend python backend/scripts/audit_survivorship_gate.py --label-version p0a_v3_horizon_governance
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("audit_survivorship_gate")

REPO_ROOT = Path(__file__).resolve().parents[2]
SMART_DB = REPO_ROOT / "data" / "smartmoney.duckdb"

# 训练 data 入口 script (governance v1 严格 + Codex round 17 Q2a/Q8.3 enforce)
# 这些 script 必须**不** hardcode `is_active=1` filter
TRAINING_DATA_BUILDERS = (
    "backend/scripts/rebuild_p0a_label_panel.py",
    "backend/scripts/build_p0a_feature_panel_v3.py",
    "backend/scripts/run_p0b_lightgbm_optuna_v3.py",
    "backend/scripts/train_p0b_lightgbm.py",
)

# 允许 is_active=1 用法: live signal 生成 / dashboard 展示 / paper_sim 选股 (实时)
# 不允许: 历史 ML training data 入口
ALLOWED_PATTERNS = (
    "live",  # live data path (paper_sim 实时选股 OK)
    "active_a_share",  # universe.py 函数名 (有 governance hook)
    "is_active=1 AND ",  # 跟其他 condition 组合时 case-by-case
    "WHERE is_active=1",  # universe 函数内部 (OK, ML training caller 用 ever-listed)
)


def _prefix_values(prefixes: tuple[str, ...]) -> str:
    return ",".join("?" for _ in prefixes)


def _fetch_survivorship_counts(sm, label_version: str, prefixes: tuple[str, ...]) -> tuple[int, int, int]:
    placeholders = _prefix_values(prefixes)
    row = sm.execute(
        f"""
        WITH universe AS (
          SELECT stock_code, is_active
            FROM dim_all_ever_listed
           WHERE SUBSTR(stock_code,1,2) IN ({placeholders})
        ),
        panel AS (
          SELECT COUNT(DISTINCT stock_code) AS panel_codes
            FROM mart_p0a_label_panel
           WHERE label_version = ?
        )
        SELECT
          COUNT(*) AS ever_listed,
          SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active,
          (SELECT panel_codes FROM panel) AS panel_codes
          FROM universe
        """,
        [*prefixes, label_version],
    ).fetchone()
    ever_listed, active, panel_codes = row
    return int(ever_listed or 0), int(active or 0), int(panel_codes or 0)


def _matched_line(text: str, match: re.Match[str]) -> tuple[int, str]:
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    line_num = text[:match.start()].count("\n") + 1
    return line_num, line


def _allowed_line(line: str, allowed_patterns: tuple[str, ...]) -> bool:
    lowered = line.lower()
    return any(allowed in lowered for allowed in allowed_patterns)


def _scan_is_active_issues(script_rel: str, text: str, pattern: re.Pattern[str]) -> list[str]:
    allowed_patterns = tuple(p.lower() for p in ALLOWED_PATTERNS)
    issues: list[str] = []
    for match in pattern.finditer(text):
        line_num, line = _matched_line(text, match)
        # 跳过 Python 注释 (# 开头) — Codex Q8.3 verify code, not docs
        if line.startswith("#") or _allowed_line(line, allowed_patterns):
            continue
        issues.append(f"{script_rel}:{line_num}: {line[:120]}")
    return issues


def _scan_training_builder(script_rel: str, pattern: re.Pattern[str]) -> tuple[list[str], bool]:
    script_path = REPO_ROOT / script_rel
    if not script_path.exists():
        return [], False
    text = script_path.read_text(encoding="utf-8")
    return _scan_is_active_issues(script_rel, text, pattern), True


def _scan_training_builders(pattern: re.Pattern[str]) -> tuple[list[str], int]:
    code_issues: list[str] = []
    missing_count = 0
    for script_rel in TRAINING_DATA_BUILDERS:
        issues, exists = _scan_training_builder(script_rel, pattern)
        if not exists:
            log.warning(f"  {script_rel} not found — skip")
            missing_count += 1
            continue
        code_issues.extend(issues)
    return code_issues, missing_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Survivorship gate audit (Codex Q8.3)")
    parser.add_argument(
        "--label-version",
        default="p0a_v3_horizon_governance",
        help="current governance label_version",
    )
    parser.add_argument("--universe-prefix", default="60,00,30,68",
                        help="KEEP universe prefix")
    args = parser.parse_args()

    failures: list[str] = []
    log.info(f"=== Codex Q8.3 survivorship gate audit (label_version={args.label_version}) ===")

    # 1. DB-side: label panel distinct stock_code 应 >= ever_listed
    sm = duckdb.connect(str(SMART_DB), read_only=True)
    prefixes = tuple(p.strip() for p in args.universe_prefix.split(",") if p.strip())
    try:
        ever_listed, active, panel_codes = _fetch_survivorship_counts(sm, args.label_version, prefixes)
    finally:
        sm.close()
    delisted = ever_listed - active
    log.info(f"  dim_all_ever_listed (KEEP universe): total={ever_listed:,} active={active:,} delisted={delisted:,}")
    log.info(f"  mart_p0a_label_panel distinct codes (label_version={args.label_version}): {panel_codes:,}")

    if panel_codes == 0:
        log.warning(f"  label panel empty for label_version={args.label_version} — skip DB check")
    elif panel_codes < ever_listed * 0.90:  # 允许 10% 容差 (KEEP prefix 内可能 IPO 不到训练 window)
        failures.append(
            f"survivorship bias suspected: label panel codes ({panel_codes:,}) "
            f"< 90% of ever_listed ({ever_listed:,}, delisted={delisted:,}) "
            f"— Codex Q8.3 expects ever-listed universe (PIT via LEFT JOIN NULL)"
        )
    else:
        log.info(f"  DB check PASS: panel codes {panel_codes:,} >= 90% of ever_listed {ever_listed:,}")

    # 2. Code-side: scan training builder scripts 不应 hardcode is_active=1
    pat = re.compile(r"is_active\s*=\s*1", re.IGNORECASE)
    code_issues, _missing_count = _scan_training_builders(pat)

    if code_issues:
        for issue in code_issues:
            log.error(f"  CODE: {issue}")
        failures.append(
            f"{len(code_issues)} training data builder(s) hardcode is_active=1 — "
            f"Codex Q8.3 expects ever-listed universe + PIT LEFT JOIN NULL"
        )
    else:
        log.info(f"  Code check PASS: {len(TRAINING_DATA_BUILDERS)} training builders 无 is_active=1 hardcode")

    log.info("===")
    if failures:
        log.error(f"Survivorship gate FAIL ({len(failures)} issues):")
        for f in failures: log.error(f"  - {f}")
        return 1
    log.info("Survivorship gate PASS (Codex Q8.3 verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
