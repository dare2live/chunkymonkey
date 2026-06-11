#!/usr/bin/env python3
"""数据迁移状态仪表盘 — "数据基建做好了么"的可执行答案.

为什么存在 (用户原话"数据是一切的基础"): 迁移进度此前只能手敲 SQL 回答 = 状态不可观测。
本工具 registry 驱动, 读 watermark (smartmoney, 不占 raw 写锁), 一眼看清每域落库/新鲜/失败。

口径 (宪法第八条 可回溯): 真相源 = sync_registry.yaml (应有域) + mart_data_source_watermark
(实际落库) + mart_data_source_failure_queue (未决失败) + dim_trading_calendar (新鲜度基准)。
不臆造进度 — 域注册了但无 watermark = NEVER_SYNCED, 显式标注不当已完成。

用法: PYTHONPATH=backend python backend/scripts/data_migration_status.py [--format json|table]
入口: scripts/chunkyctl data-status
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

_REPO = Path(__file__).resolve().parents[2]
_REGISTRY = _REPO / "backend" / "config" / "sync_registry.yaml"


def _smartmoney_conn():
    from services.db import get_conn
    return get_conn()


def collect_status() -> dict[str, Any]:
    reg = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8")) or {}
    domains = reg.get("domains") or {}
    conn = _smartmoney_conn()
    try:
        wm = {
            r[0]: {"last_data_date": r[1], "row_count": r[2]}
            for r in conn.execute(
                "SELECT data_domain, last_data_date, row_count "
                "FROM mart_data_source_watermark WHERE data_domain LIKE 'sync:%'"
            ).fetchall()
        }
        open_fail = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT data_domain, occurrence_count FROM mart_data_source_failure_queue "
                "WHERE data_domain LIKE 'sync:%' AND status='open'"
            ).fetchall()
        }
        try:
            from services.calendar import latest_completed_trade_date
            lc = latest_completed_trade_date(conn)  # canonical 真相源 (含 16:00 截断 + 未来日过滤)
            cal_max = lc.replace("-", "") if lc else None
        except Exception:  # noqa: BLE001 — 日历缺失不挡状态主流程
            cal_max = None
    finally:
        conn.close()

    rows: list[dict[str, Any]] = []
    for name, spec in sorted(domains.items()):
        key = f"sync:{name}"
        w = wm.get(key)
        sla = spec.get("freshness_sla_trading_days")
        ld = str(w["last_data_date"]).replace("-", "") if w and w["last_data_date"] else None
        # 状态: NEVER_SYNCED (注册无 watermark) / STALE (落后 cal_max 超 SL*3) / OK
        if not w:
            status = "NEVER_SYNCED"
        elif spec.get("batch_mode") == "full_refresh":
            status = "OK"  # 日历类无日频新鲜度语义
        elif ld and cal_max and sla is not None:
            # 粗口径: 落后日历最新交易日多少 (字符串日差近似, 仅作 stale 预警非精确交易日数)
            lag_days = (int(cal_max) - int(ld)) if ld.isdigit() and cal_max.isdigit() else 0
            status = "STALE" if lag_days > max(sla * 3, 5) else "OK"
        else:
            status = "LANDED_NO_DATE"
        rows.append({
            "domain": name,
            "status": status,
            "rows": (w["row_count"] if w else 0),
            "last_data_date": ld,
            "sla_days": sla,
            "open_failures": open_fail.get(key, 0),
        })

    landed = [r for r in rows if r["status"] in ("OK", "LANDED_NO_DATE", "STALE")]
    never = [r for r in rows if r["status"] == "NEVER_SYNCED"]
    stale = [r for r in rows if r["status"] == "STALE"]
    with_fail = [r for r in rows if r["open_failures"]]
    verdict = "PASS" if (not never and not stale and not with_fail) else "WARN"
    return {
        "registered_domains": len(rows),
        "landed": len(landed),
        "never_synced": len(never),
        "stale": len(stale),
        "domains_with_open_failures": len(with_fail),
        "total_rows": sum(r["rows"] for r in rows),
        "calendar_max": cal_max,
        "verdict": verdict,
        "domains": rows,
    }


def _render_table(s: dict[str, Any]) -> str:
    L = [
        f"数据迁移状态 (registry 驱动) — verdict={s['verdict']}",
        f"  注册域 {s['registered_domains']} | 已落库 {s['landed']} | "
        f"未同步 {s['never_synced']} | stale {s['stale']} | 有未决失败 {s['domains_with_open_failures']} "
        f"| 总行 {s['total_rows']:,} | 日历最新 {s['calendar_max']}",
        "",
        f"  {'域':22s} {'状态':16s} {'行数':>12s} {'最新日':>9s} {'SLA':>4s} {'失败':>4s}",
    ]
    for r in s["domains"]:
        L.append(f"  {r['domain']:22s} {r['status']:16s} {r['rows']:>12,} "
                 f"{str(r['last_data_date'] or '—'):>9s} {str(r['sla_days'] or '—'):>4s} "
                 f"{r['open_failures']:>4d}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--format", choices=["json", "table"], default="table")
    args = ap.parse_args()
    s = collect_status()
    print(json.dumps(s, ensure_ascii=False, indent=1) if args.format == "json" else _render_table(s))
    return 0 if s["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
