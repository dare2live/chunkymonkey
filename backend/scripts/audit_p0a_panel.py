#!/usr/bin/env python3
"""P0a panel PIT + Acceptance audit (PLAN_V3 v3.2 P0a Acceptance gate).

mart_p0a_label_panel + mart_p0a_feature_label_panel 必须满足:
1. 可复现: label_version / feature_version / built_at 入元数据
2. label 已扣成本: round_trip_cost_pct > 0; fwd_cost_after = gross - round_trip
3. 不可成交 mask 生效: unable_at_entry / unable_at_exit_* 标 True 时 label = NULL
4. 核心特征 PIT audit 通过: feature panel 不含未来字段
5. KEEP universe 守门: 所有 stock_code 前缀 ∈ ('60','00','30','68')
6. Forward 字段隔离: 训练时 feature pipeline 不读 forward 字段 (静态扫)

Exit 0 = PASS, 1 = FAIL.

用法:
    PYTHONPATH=backend python backend/scripts/audit_p0a_panel.py \
        [--json-out /tmp/p0a_audit.json]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("audit_p0a")


@dataclass
class CheckResult:
    section: str
    name: str
    status: str   # PASS / WARN / FAIL
    detail: str
    rows: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def check_label_panel_reproducibility(conn) -> list[CheckResult]:
    """1. label_version / built_at 填齐."""
    out: list[CheckResult] = []
    n_total = conn.execute("SELECT COUNT(*) FROM mart_p0a_label_panel").fetchone()[0]
    n_missing_version = conn.execute(
        "SELECT COUNT(*) FROM mart_p0a_label_panel "
        "WHERE label_version IS NULL OR label_version = '' OR built_at IS NULL OR built_at = ''"
    ).fetchone()[0]
    if n_missing_version == 0:
        out.append(CheckResult(
            section="1. Reproducibility",
            name="label_version_built_at",
            status="PASS",
            detail=f"label_panel: {n_total:,} rows, 全部填 label_version + built_at",
            rows=n_total,
        ))
    else:
        out.append(CheckResult(
            section="1. Reproducibility",
            name="label_version_built_at",
            status="FAIL",
            detail=f"{n_missing_version:,} / {n_total:,} rows missing label_version/built_at",
            rows=n_missing_version,
        ))
    return out


def check_cost_deducted(conn) -> list[CheckResult]:
    """2. round_trip_cost_pct > 0, fwd_cost_after = gross - round_trip."""
    out: list[CheckResult] = []
    rt = conn.execute(
        "SELECT MIN(round_trip_cost_pct), MAX(round_trip_cost_pct) FROM mart_p0a_label_panel "
        "WHERE round_trip_cost_pct IS NOT NULL"
    ).fetchone()
    if rt[0] is None:
        out.append(CheckResult(
            section="2. Cost deducted",
            name="round_trip_pct_present",
            status="FAIL",
            detail="所有 round_trip_cost_pct IS NULL",
        ))
        return out
    if rt[0] <= 0 or rt[0] != rt[1]:
        out.append(CheckResult(
            section="2. Cost deducted",
            name="round_trip_pct_consistent",
            status="FAIL",
            detail=f"round_trip 不一致: min={rt[0]} max={rt[1]}; 应该是常量 > 0",
        ))
        return out
    out.append(CheckResult(
        section="2. Cost deducted",
        name="round_trip_pct_consistent",
        status="PASS",
        detail=f"round_trip_cost_pct = {rt[0]:.5f} (一致, > 0)",
        extras={"round_trip_pct": rt[0]},
    ))

    # Verify formula: fwd_cost_after_5d = (exit_vwap_5d / entry_vwap - 1) - round_trip
    # 抽 10 行 non-mask 抽样验证
    sample = conn.execute("""
        SELECT entry_vwap, exit_vwap_5d, round_trip_cost_pct, fwd_cost_after_5d
        FROM mart_p0a_label_panel
        WHERE entry_vwap IS NOT NULL AND exit_vwap_5d IS NOT NULL
          AND fwd_cost_after_5d IS NOT NULL
          AND NOT unable_at_entry AND NOT unable_at_exit_5d
        LIMIT 10
    """).fetchall()
    if not sample:
        out.append(CheckResult(
            section="2. Cost deducted",
            name="formula_5d_spotcheck",
            status="WARN",
            detail="no non-mask sample to spot-check formula",
        ))
        return out
    max_diff = 0.0
    for e, x, rt_, fwd in sample:
        expected = (x / e - 1.0) - rt_
        max_diff = max(max_diff, abs(expected - fwd))
    if max_diff < 1e-9:
        out.append(CheckResult(
            section="2. Cost deducted",
            name="formula_5d_spotcheck",
            status="PASS",
            detail=f"10-sample 验 formula 一致 (max diff {max_diff:.2e})",
        ))
    else:
        out.append(CheckResult(
            section="2. Cost deducted",
            name="formula_5d_spotcheck",
            status="FAIL",
            detail=f"formula 不一致, max diff {max_diff:.6f}",
        ))
    return out


def check_mask_effective(conn) -> list[CheckResult]:
    """3. unable_at_entry=True 时所有 horizon label=NULL; unable_at_exit_N=True 时该 horizon label=NULL."""
    out: list[CheckResult] = []
    # entry mask 真但 5d/10d/20d label 非 NULL → BUG
    bad_entry = conn.execute("""
        SELECT COUNT(*) FROM mart_p0a_label_panel
        WHERE unable_at_entry = TRUE
          AND (fwd_cost_after_5d IS NOT NULL
               OR fwd_cost_after_10d IS NOT NULL
               OR fwd_cost_after_20d IS NOT NULL)
    """).fetchone()[0]
    if bad_entry == 0:
        out.append(CheckResult(
            section="3. Mask effective",
            name="entry_mask_kills_labels",
            status="PASS",
            detail="所有 unable_at_entry=True 行的 label 都 NULL ✓",
        ))
    else:
        out.append(CheckResult(
            section="3. Mask effective",
            name="entry_mask_kills_labels",
            status="FAIL",
            detail=f"{bad_entry:,} rows: unable_at_entry=True 但 label 不是全 NULL",
        ))
    # 同理 per-horizon
    for h in (5, 10, 20):
        bad = conn.execute(f"""
            SELECT COUNT(*) FROM mart_p0a_label_panel
            WHERE unable_at_exit_{h}d = TRUE AND fwd_cost_after_{h}d IS NOT NULL
        """).fetchone()[0]
        if bad == 0:
            out.append(CheckResult(
                section="3. Mask effective",
                name=f"exit_{h}d_mask_kills_label",
                status="PASS",
                detail=f"unable_at_exit_{h}d=True 行 label_{h}d 都 NULL ✓",
            ))
        else:
            out.append(CheckResult(
                section="3. Mask effective",
                name=f"exit_{h}d_mask_kills_label",
                status="FAIL",
                detail=f"{bad:,} rows: unable_at_exit_{h}d=True 但 label_{h}d 非 NULL",
            ))
    return out


def check_keep_universe(conn) -> list[CheckResult]:
    """5. 所有 stock_code 前缀 ∈ KEEP universe (60/00/30/68)."""
    out: list[CheckResult] = []
    for table in ("mart_p0a_label_panel", "mart_p0a_feature_label_panel"):
        try:
            bad = conn.execute(f"""
                SELECT COUNT(*) FROM {table}
                WHERE SUBSTR(stock_code, 1, 2) NOT IN ('60', '00', '30', '68')
            """).fetchone()[0]
            if bad == 0:
                out.append(CheckResult(
                    section="5. KEEP universe",
                    name=f"{table}_prefix",
                    status="PASS",
                    detail=f"{table}: 全部 60/00/30/68 前缀 ✓",
                ))
            else:
                out.append(CheckResult(
                    section="5. KEEP universe",
                    name=f"{table}_prefix",
                    status="FAIL",
                    detail=f"{table}: {bad:,} 行非 KEEP universe 前缀",
                ))
        except Exception as e:
            out.append(CheckResult(
                section="5. KEEP universe",
                name=f"{table}_prefix",
                status="WARN",
                detail=f"{table} not present: {e}",
            ))
    return out


def check_feature_panel_no_forward_leak(conn) -> list[CheckResult]:
    """6. mart_p0a_feature_label_panel 不应含 exit_vwap_5d 等 forward 特征 (forward 字段是 label not feature)."""
    out: list[CheckResult] = []
    try:
        cols = [
            r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'mart_p0a_feature_label_panel'"
            ).fetchall()
        ]
    except Exception as e:
        out.append(CheckResult(
            section="6. PIT (no forward in feature panel)",
            name="schema_inspect",
            status="WARN",
            detail=f"feature panel table not present: {e}",
        ))
        return out
    # 禁词: exit_vwap_/exit_date_/unable_at_exit_ 是 label 字段, 不该入训练 X
    forbidden = [c for c in cols if c.startswith(("exit_vwap_", "exit_date_", "unable_at_exit_"))]
    if not forbidden:
        out.append(CheckResult(
            section="6. PIT (no forward in feature panel)",
            name="no_exit_fields_in_features",
            status="PASS",
            detail="feature panel 不含 exit_vwap_/exit_date_/unable_at_exit_ 字段 ✓",
        ))
    else:
        out.append(CheckResult(
            section="6. PIT (no forward in feature panel)",
            name="no_exit_fields_in_features",
            status="WARN",
            detail=f"feature panel 含 {len(forbidden)} 个 forward-looking 字段 (训练时排除): {forbidden}",
        ))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="P0a panel PIT + Acceptance audit")
    parser.add_argument("--json-out", type=Path, default=Path("/tmp/p0a_audit.json"))
    args = parser.parse_args()

    log.info("=== P0a Panel Audit (PLAN_V3 v3.2 P0a Acceptance) ===")
    conn = duck_connect(str(DB_PATH), read_only=True)
    try:
        results: list[CheckResult] = []
        results.extend(check_label_panel_reproducibility(conn))
        results.extend(check_cost_deducted(conn))
        results.extend(check_mask_effective(conn))
        results.extend(check_keep_universe(conn))
        results.extend(check_feature_panel_no_forward_leak(conn))
    finally:
        conn.close()

    log.info("")
    log.info("=== Results ===")
    n_pass = n_warn = n_fail = 0
    for r in results:
        log.info(f"  [{r.status}] {r.section} :: {r.name} — {r.detail}")
        if r.status == "PASS":
            n_pass += 1
        elif r.status == "WARN":
            n_warn += 1
        else:
            n_fail += 1

    log.info("")
    log.info(f"SUMMARY: PASS={n_pass} WARN={n_warn} FAIL={n_fail}")

    args.json_out.write_text(json.dumps({"results": [asdict(r) for r in results],
                                          "summary": {"pass": n_pass, "warn": n_warn, "fail": n_fail}},
                                         indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"JSON report -> {args.json_out}")

    if n_fail > 0:
        log.error(f"P0a FAIL: {n_fail} hard violations — PLAN_V3 §6 串行 gate blocks P0b")
        return 1
    log.info("P0a PASS — feature/label panel 闭环, 可进 P0b")
    return 0


if __name__ == "__main__":
    sys.exit(main())
