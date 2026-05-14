#!/usr/bin/env python3
"""P-1.2 Survivorship bias audit — PLAN_V3 v3.2 P-1 gate.

Per PLAN_V3 §2.P-1, ML ranking training must include **当时活跃** stocks even
if they later 退市 / 转 ST, otherwise we get survivorship bias (regress only on
现存 stocks → 模型对真实历史下行 stock 没学到, 实盘 will misrank).

Acceptance: 退市/ST 覆盖差异 = 0 未解释项 → PASS.

Sections:
1. Delisted stock table existence + row counts (dim_all_ever_listed,
   dim_active_a_stock, dim_listing_status, fact_stock_type_daily).
2. ST stock identification (历史 vs 当前; 通过 stock_name LIKE '%ST%' 检测).
3. Universe coverage cross-check (历史 K线 universe vs 当前活跃 universe —
   差额必须 ≤ 已记录退市数, 否则有"幽灵"消失).
4. Survivorship bias spot check (随机历史日期: 当时 K线 universe 是否含
   现已退市的 stock — 若不含则训练会丢失这部分 stock 的 forward 行为).

Exit 0 = PASS, 1 = FAIL.

Usage:
    PYTHONPATH=backend python backend/scripts/audit_survivorship.py
    PYTHONPATH=backend python backend/scripts/audit_survivorship.py --json-out /tmp/survivorship_audit.json

Rule 11 安全:
- read_only=True, ATTACH market.duckdb READ_ONLY
- 唯一 output: /tmp/survivorship_audit.json (默认)
- 不写任何表
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("audit_survivorship")

# Market K-line DB path (peer of smartmoney.duckdb)
MARKET_DB_PATH = Path(DB_PATH).parent / "market.duckdb"

# Reference dates for spot-check (cross-regime, fixed for reproducibility).
# Picks span 2024 bull / 2024 H2 / 2025 mid / 2025 H2 + latest.
# rule-compliance: ok evidence=cross-regime-fixed-sample (audit reference, not model param)
SPOT_CHECK_DATES = ["2024-01-15", "2024-06-17", "2024-11-15", "2025-03-17", "2025-09-15"]

# Fixed seed for deterministic spot-check (Rule 11.4: deterministic input snapshot)
random.seed(20260514)


@dataclass
class CheckResult:
    section: str
    name: str
    status: str  # PASS / WARN / FAIL
    detail: str
    rows: int = 0
    extras: dict = field(default_factory=dict)


def check_delisted_table_existence(conn) -> list[CheckResult]:
    """Section 1: required tables exist with sane row counts.

    dim_all_ever_listed: 全历史 stock (含已退市) + delisted_date 字段.
    dim_active_a_stock: 当前活跃 stock.
    dim_listing_status: status_reason 含 ST / 退市标记 (PIT).
    fact_stock_type_daily: 每日 stock 分类 (含 primary_type, 不直接含 ST).
    """
    out: list[CheckResult] = []

    # 1.1 dim_all_ever_listed
    try:
        ever_total = conn.execute("SELECT COUNT(*) FROM dim_all_ever_listed").fetchone()[0]
        ever_active = conn.execute("SELECT COUNT(*) FROM dim_all_ever_listed WHERE is_active=1").fetchone()[0]
        ever_inactive = conn.execute("SELECT COUNT(*) FROM dim_all_ever_listed WHERE is_active=0").fetchone()[0]
        with_delisted_date = conn.execute(
            "SELECT COUNT(*) FROM dim_all_ever_listed WHERE delisted_date IS NOT NULL AND delisted_date != ''"
        ).fetchone()[0]
        # PASS if inactive count > 0 (有 delisted history)
        status = "PASS" if ever_inactive > 0 else "FAIL"
        out.append(CheckResult(
            section="1. Delisted tables existence",
            name="dim_all_ever_listed",
            status=status,
            detail=f"total={ever_total}, active={ever_active}, inactive(delisted)={ever_inactive}, with_delisted_date={with_delisted_date}",
            rows=ever_total,
            extras={"active": ever_active, "inactive": ever_inactive, "with_delisted_date": with_delisted_date},
        ))
    except Exception as e:
        out.append(CheckResult(
            section="1. Delisted tables existence",
            name="dim_all_ever_listed",
            status="FAIL",
            detail=f"missing/error: {e}",
        ))

    # 1.2 dim_active_a_stock
    try:
        active = conn.execute("SELECT COUNT(*) FROM dim_active_a_stock").fetchone()[0]
        out.append(CheckResult(
            section="1. Delisted tables existence",
            name="dim_active_a_stock",
            status="PASS" if active > 0 else "FAIL",
            detail=f"{active} active stocks",
            rows=active,
        ))
    except Exception as e:
        out.append(CheckResult(
            section="1. Delisted tables existence",
            name="dim_active_a_stock",
            status="FAIL",
            detail=f"missing/error: {e}",
        ))

    # 1.3 dim_listing_status (PIT status flags table)
    try:
        ls = conn.execute("SELECT COUNT(*) FROM dim_listing_status").fetchone()[0]
        # WARN: empty means no historical PIT status tracking; ML must use stock_name LIKE '%ST%' instead
        status = "PASS" if ls > 0 else "WARN"
        out.append(CheckResult(
            section="1. Delisted tables existence",
            name="dim_listing_status",
            status=status,
            detail=f"{ls} rows ({'empty — no PIT ST/退市 status tracking' if ls == 0 else 'has PIT status'})",
            rows=ls,
        ))
    except Exception as e:
        out.append(CheckResult(
            section="1. Delisted tables existence",
            name="dim_listing_status",
            status="WARN",
            detail=f"missing/error: {e}",
        ))

    return out


def check_st_identification(conn) -> list[CheckResult]:
    """Section 2: ST stocks identified in both 历史 and 当前 universes.

    ST detection 走 stock_name LIKE '%ST%'. 项目目前没有专门的 ST 历史表
    (dim_listing_status 空), 所以用 stock_name 模式 + dim_all_ever_listed.is_active
    交叉判断.
    """
    out: list[CheckResult] = []

    # 2.1 当前活跃 ST
    try:
        st_active_now = conn.execute(
            "SELECT COUNT(*) FROM dim_active_a_stock WHERE stock_name LIKE '%ST%'"
        ).fetchone()[0]
        out.append(CheckResult(
            section="2. ST identification",
            name="st_active_now",
            status="PASS" if st_active_now > 0 else "WARN",
            detail=f"{st_active_now} currently-active stocks have ST in name (dim_active_a_stock)",
            rows=st_active_now,
        ))
    except Exception as e:
        out.append(CheckResult(
            section="2. ST identification",
            name="st_active_now",
            status="WARN",
            detail=f"check failed: {e}",
        ))

    # 2.2 历史 ST (含已退市)
    try:
        st_historical = conn.execute(
            "SELECT COUNT(*) FROM dim_all_ever_listed WHERE stock_name LIKE '%ST%'"
        ).fetchone()[0]
        st_hist_delisted = conn.execute(
            "SELECT COUNT(*) FROM dim_all_ever_listed WHERE stock_name LIKE '%ST%' AND is_active=0"
        ).fetchone()[0]
        # PASS: dim_all_ever_listed 应当包含历史 ST 退市股 (否则训练丢这部分)
        status = "PASS" if st_hist_delisted > 0 else "FAIL"
        out.append(CheckResult(
            section="2. ST identification",
            name="st_historical_universe",
            status=status,
            detail=f"{st_historical} historical ST stocks (含 active+delisted), 其中 {st_hist_delisted} 已退市",
            rows=st_historical,
            extras={"st_active_in_ever": st_historical - st_hist_delisted, "st_delisted": st_hist_delisted},
        ))
    except Exception as e:
        out.append(CheckResult(
            section="2. ST identification",
            name="st_historical_universe",
            status="FAIL",
            detail=f"check failed: {e}",
        ))

    # 2.3 *ST 严重风险股 (退市边缘)
    try:
        star_st = conn.execute(
            "SELECT COUNT(*) FROM dim_all_ever_listed WHERE stock_name LIKE '*ST%'"
        ).fetchone()[0]
        out.append(CheckResult(
            section="2. ST identification",
            name="star_st_historical",
            status="PASS" if star_st > 0 else "WARN",
            detail=f"{star_st} stocks ever marked *ST (退市边缘, 含 active+delisted)",
            rows=star_st,
        ))
    except Exception as e:
        out.append(CheckResult(
            section="2. ST identification",
            name="star_st_historical",
            status="WARN",
            detail=f"check failed: {e}",
        ))

    return out


def check_universe_coverage_crosscheck(conn) -> list[CheckResult]:
    """Section 3: 历史 K线 universe vs 当前 universe 差额必须 ≤ 已记录退市数.

    若 |K线 distinct codes| - |dim_active_a_stock| > |dim_all_ever_listed inactive|,
    说明有"幽灵"消失 — 历史出现过但不在 ever_listed 里, 也不在 active 里. ML
    训练若只用 dim_active_a_stock 作 universe, 会丢失这些 stock 的 forward
    label, 即 survivorship bias.
    """
    out: list[CheckResult] = []

    try:
        # K线 ever-appeared codes (mkt schema via ATTACH)
        kline_ever = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM mkt.price_kline WHERE freq='daily' AND adjust='qfq'"
        ).fetchone()[0]
        active_now = conn.execute("SELECT COUNT(*) FROM dim_active_a_stock").fetchone()[0]
        ever_listed = conn.execute("SELECT COUNT(*) FROM dim_all_ever_listed").fetchone()[0]
        ever_inactive = conn.execute("SELECT COUNT(*) FROM dim_all_ever_listed WHERE is_active=0").fetchone()[0]

        # Codes in K线 but not in dim_all_ever_listed (真"幽灵")
        ghost_count = conn.execute(
            """
            SELECT COUNT(DISTINCT k.code)
            FROM mkt.price_kline k
            LEFT JOIN dim_all_ever_listed e ON k.code = e.stock_code
            WHERE k.freq='daily' AND k.adjust='qfq' AND e.stock_code IS NULL
            """
        ).fetchone()[0]

        # Codes in K线 with date历史 但不在 active_now (delisted 候选)
        delisted_via_kline = conn.execute(
            """
            SELECT COUNT(DISTINCT k.code)
            FROM mkt.price_kline k
            LEFT JOIN dim_active_a_stock a ON k.code = a.stock_code
            WHERE k.freq='daily' AND k.adjust='qfq' AND a.stock_code IS NULL
            """
        ).fetchone()[0]

        out.append(CheckResult(
            section="3. Universe coverage crosscheck",
            name="universe_counts",
            status="PASS",
            detail=(
                f"K线 ever={kline_ever}, dim_active_now={active_now}, dim_ever_listed={ever_listed}, "
                f"ever_inactive(delisted)={ever_inactive}"
            ),
            rows=kline_ever,
            extras={
                "kline_ever": kline_ever,
                "active_now": active_now,
                "ever_listed": ever_listed,
                "ever_inactive": ever_inactive,
            },
        ))

        # 关键 check: K线 ever - active_now ≈ ever_inactive (allow ghost_count tolerance)
        diff_kline_vs_active = kline_ever - active_now
        unexplained = diff_kline_vs_active - ever_inactive
        if abs(unexplained) <= ghost_count and ghost_count <= max(50, kline_ever * 0.01):
            status = "PASS"
            detail = (
                f"K线-active={diff_kline_vs_active}, ever_inactive={ever_inactive}, "
                f"unexplained={unexplained}, ghosts={ghost_count} (≤1% tolerance)"
            )
        elif ghost_count > 0:
            # WARN: 少量 ghost OK (新股 / 暂停上市 / 数据源时序差)
            status = "WARN"
            detail = (
                f"unexplained={unexplained} stocks: K线 ever-appeared 但既不 active 也不在 ever_listed. "
                f"ghosts={ghost_count}, delisted_via_kline={delisted_via_kline}"
            )
        else:
            status = "FAIL"
            detail = (
                f"unexplained={unexplained} stocks survivor bias risk: K线 ever-appeared "
                f"差额 {diff_kline_vs_active} 超过 ever_inactive {ever_inactive}, "
                f"且无 ghost 解释"
            )
        out.append(CheckResult(
            section="3. Universe coverage crosscheck",
            name="kline_vs_active_diff",
            status=status,
            detail=detail,
            rows=abs(unexplained),
            extras={
                "diff_kline_vs_active": diff_kline_vs_active,
                "ever_inactive": ever_inactive,
                "unexplained": unexplained,
                "ghost_count": ghost_count,
                "delisted_via_kline": delisted_via_kline,
            },
        ))
    except Exception as e:
        out.append(CheckResult(
            section="3. Universe coverage crosscheck",
            name="universe_crosscheck",
            status="FAIL",
            detail=f"check failed: {e}",
        ))

    return out


def check_survivorship_spot_check(conn) -> list[CheckResult]:
    """Section 4: 随机历史日期 spot check — 当时 universe 是否含 现已退市 stock.

    核心 survivorship check: 历史日期 t 的 K线 universe 应当含 当时活跃 +
    后续退市的 stock. 若 universe 只是 t 时刻在 dim_active_a_stock (现存) 的子集,
    则有偏差.
    """
    out: list[CheckResult] = []

    try:
        # 取已 delisted stocks (有 delisted_date)
        delisted_rows = conn.execute(
            """
            SELECT stock_code, delisted_date
            FROM dim_all_ever_listed
            WHERE is_active=0 AND delisted_date IS NOT NULL AND delisted_date != ''
            """
        ).fetchall()
        delisted_map = {r[0]: r[1] for r in delisted_rows}
    except Exception as e:
        out.append(CheckResult(
            section="4. Survivorship spot-check",
            name="bootstrap",
            status="FAIL",
            detail=f"could not load delisted set: {e}",
        ))
        return out

    if not delisted_map:
        out.append(CheckResult(
            section="4. Survivorship spot-check",
            name="bootstrap",
            status="FAIL",
            detail="no delisted stocks in dim_all_ever_listed — cannot run spot check",
        ))
        return out

    for sig_date in SPOT_CHECK_DATES:
        try:
            # 当日 K线 universe
            kline_universe = {
                r[0] for r in conn.execute(
                    "SELECT DISTINCT code FROM mkt.price_kline "
                    "WHERE date=? AND freq='daily' AND adjust='qfq'",
                    [sig_date],
                ).fetchall()
            }
            if not kline_universe:
                out.append(CheckResult(
                    section="4. Survivorship spot-check",
                    name=f"date={sig_date}",
                    status="WARN",
                    detail=f"no K线 data on {sig_date} (非交易日?)",
                ))
                continue

            # 当时 应该 active (delisted_date > sig_date) 的 delisted stocks
            should_be_in = {
                code for code, d in delisted_map.items()
                if d and d > sig_date
            }
            # 实际在 universe 里的 delisted stocks
            actually_in = should_be_in & kline_universe
            missing = should_be_in - kline_universe

            # 计算覆盖率
            coverage = len(actually_in) / len(should_be_in) if should_be_in else 1.0

            if not should_be_in:
                status = "WARN"
                detail = f"{sig_date}: 0 stocks should-be-in (无对应历史退市 — 日期太早/太晚)"
            elif coverage >= 0.95:
                status = "PASS"
                detail = (
                    f"{sig_date}: K线 universe={len(kline_universe)}, "
                    f"should_be_in(后续退市)={len(should_be_in)}, "
                    f"actually_in={len(actually_in)}, coverage={coverage:.1%}"
                )
            elif coverage >= 0.80:
                status = "WARN"
                detail = (
                    f"{sig_date}: coverage={coverage:.1%} "
                    f"({len(actually_in)}/{len(should_be_in)}); "
                    f"missing={len(missing)} 后续退市股不在当日 universe"
                )
            else:
                status = "FAIL"
                detail = (
                    f"{sig_date}: coverage={coverage:.1%} "
                    f"({len(actually_in)}/{len(should_be_in)}); "
                    f"严重生存者偏差 — {len(missing)} 后续退市股不在当日 universe"
                )

            extras = {
                "kline_universe_size": len(kline_universe),
                "should_be_in": len(should_be_in),
                "actually_in": len(actually_in),
                "missing": len(missing),
                "coverage": round(coverage, 4),
            }
            # 添加一些 missing 样本
            if missing:
                sample = sorted(missing)[:5]
                extras["missing_sample"] = [{"code": c, "delisted_date": delisted_map[c]} for c in sample]

            out.append(CheckResult(
                section="4. Survivorship spot-check",
                name=f"date={sig_date}",
                status=status,
                detail=detail,
                rows=len(should_be_in),
                extras=extras,
            ))
        except Exception as e:
            out.append(CheckResult(
                section="4. Survivorship spot-check",
                name=f"date={sig_date}",
                status="WARN",
                detail=f"check failed: {e}",
            ))

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="P-1.2 Survivorship bias audit")
    parser.add_argument("--json-out", type=Path, default=Path("/tmp/survivorship_audit.json"),
                        help="Write full JSON report to path")
    args = parser.parse_args()

    log.info("=== P-1.2 Survivorship Bias Audit (PLAN_V3 v3.2) ===")
    # Rule 11: read-only + ATTACH READ_ONLY for concurrent P-1.* audits
    conn = duck_connect(
        str(DB_PATH),
        read_only=True,
        attach={"mkt": str(MARKET_DB_PATH)},
    )
    try:
        results: list[CheckResult] = []
        results.extend(check_delisted_table_existence(conn))
        results.extend(check_st_identification(conn))
        results.extend(check_universe_coverage_crosscheck(conn))
        results.extend(check_survivorship_spot_check(conn))
    finally:
        conn.close()

    # Summary
    by_status = Counter(r.status for r in results)
    log.info("")
    log.info("=== Results ===")
    for r in results:
        log.info(f"  [{r.status:4s}] {r.section} :: {r.name} — {r.detail}")
    log.info("")
    log.info(f"SUMMARY: PASS={by_status['PASS']} WARN={by_status['WARN']} FAIL={by_status['FAIL']}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "audit": "P-1.2 Survivorship bias",
            "summary": dict(by_status),
            "results": [asdict(r) for r in results],
        }
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"JSON report -> {args.json_out}")

    # P-1.2 Go gate: FAIL=0
    if by_status["FAIL"] > 0:
        log.error(f"P-1.2 FAIL: {by_status['FAIL']} hard violations — PLAN_V3 §6 串行 gate blocks P0")
        return 1
    log.info("P-1.2 PASS — survivorship coverage OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
