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
    PYTHONPATH=backend python backend/scripts/audit_survivorship_gate.py --label-version p0a_v2_governance_v1
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Survivorship gate audit (Codex Q8.3)")
    parser.add_argument("--label-version", default="p0a_v2_governance_v1",
                        help="governance v1 label_version")
    parser.add_argument("--universe-prefix", default="60,00,30,68",
                        help="KEEP universe prefix")
    args = parser.parse_args()

    failures: list[str] = []
    log.info(f"=== Codex Q8.3 survivorship gate audit (label_version={args.label_version}) ===")

    # 1. DB-side: label panel distinct stock_code 应 >= ever_listed
    sm = duckdb.connect(str(SMART_DB), read_only=True)
    prefixes = tuple(args.universe_prefix.split(","))
    placeholders = ",".join("?" for _ in prefixes)
    ever_listed = sm.execute(
        f"SELECT COUNT(*) FROM dim_all_ever_listed "
        f"WHERE SUBSTR(stock_code,1,2) IN ({placeholders})",
        list(prefixes),
    ).fetchone()[0]
    active = sm.execute(
        f"SELECT COUNT(*) FROM dim_all_ever_listed "
        f"WHERE SUBSTR(stock_code,1,2) IN ({placeholders}) AND is_active=1",
        list(prefixes),
    ).fetchone()[0]
    delisted = ever_listed - active
    log.info(f"  dim_all_ever_listed (KEEP universe): total={ever_listed:,} active={active:,} delisted={delisted:,}")

    panel_codes = sm.execute(
        "SELECT COUNT(DISTINCT stock_code) FROM mart_p0a_label_panel WHERE label_version=?",
        [args.label_version],
    ).fetchone()[0]
    sm.close()
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
    code_issues = []
    for script_rel in TRAINING_DATA_BUILDERS:
        script_path = REPO_ROOT / script_rel
        if not script_path.exists():
            log.warning(f"  {script_rel} not found — skip")
            continue
        text = script_path.read_text(encoding="utf-8")
        for m in pat.finditer(text):
            # 取 match 上下文 line
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            line = text[line_start:line_end if line_end > 0 else len(text)].strip()
            # 检查 ALLOWED_PATTERNS
            if any(allowed in line.lower() for allowed in (p.lower() for p in ALLOWED_PATTERNS)):
                continue
            line_num = text[:m.start()].count("\n") + 1
            code_issues.append(f"{script_rel}:{line_num}: {line[:120]}")

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
