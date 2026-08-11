#!/usr/bin/env python3
"""check_cutover_effective — config 声明的 cutover 意图 vs resolver 实际裁决。

owner: backend/config/governance_gates.yaml (runtime_checks.cutover_effective)
+ goal.md「治理体系重构」P1.2。

**为什么需要这道检查** (2026-08-10 治理审计实证)：`b_pit_mart_cutover.yaml` 写着
``cutover_allowed: true``，而 attested shadow 窗口末端是 ``20260722`` —— 之后的任何
trade_date 一律 fail-closed 回 legacy mart。也就是说这个「已切换」的声明**早就不生效
了**，消费方一直走旧路径，而 goal.md / BOARD 仍按 True 叙述。整整 13 个交易日没人发现，
因为唯一会算出这个背离的地方 (BOARD 投影) 挂在 **commit** 路径上 —— 查不查取决于
「有没有人恰好提交相关代码」。受害时刻是每次跑日更，门就该装在日更里。

判据 (不做关键词猜测)：把**最近一个已完成收盘的交易日**送进各自的 production
resolver —— 那正是消费方真实会问的那一天。真相源是交易日历
(``services.calendar.latest_completed_trade_date``)，不是 wall-clock。

分级 (区分「永远修不好」与「今天恰好没有」)：

* ``b_pit_mart`` → **FAIL**。它没有逐日输入依赖：窗口 / policy hash / definition
  version 都是 config 常量，一旦对不上，**再跑多少次日更也不会自愈**，只有 owner
  重新 attest 或把标志改回 false 才能闭合。
* ``tier12_consumer`` → **WARN**。它按设计逐日依赖 accepted partition，某天没有
  accepted 就回落 legacy 是**写在 config 注释里的预期行为**；把它当 FAIL 会 cry wolf。
  但「声明 true 而今天实际走 legacy」仍是必须被看见的观测，不能静默。

退出码：0=PASS / 1=FAIL (结构性背离, 需 owner 裁决) / 2=WARN 或 UNVERIFIED
(逐日回落, 或日历/artifact 不可达导致无法判定 —— 「查不了」不等于「没问题」)。

用法:
    PYTHONPATH=backend python backend/scripts/check_cutover_effective.py
    ... --json / --json-out data/audit/cutover_effective_20260811.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[2]

STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_UNVERIFIED = "unverified"

_OVERALL_BY_RANK = {0: "PASS", 1: "WARN", 2: "FAIL"}
_RANK = {STATUS_PASS: 0, STATUS_WARN: 1, STATUS_UNVERIFIED: 1, STATUS_FAIL: 2}
_EXIT = {"PASS": 0, "WARN": 2, "FAIL": 1}


def _latest_trade_date() -> tuple[str | None, str]:
    """交易日历真相源；返回 (compact_day, detail)。不可达 → (None, reason)。"""
    try:
        from services.calendar import latest_completed_trade_date

        day = latest_completed_trade_date()
    except Exception as exc:  # noqa: BLE001 — 日历不可达是「查不了」，不是「没问题」
        return None, f"calendar_unreachable:{type(exc).__name__}"
    if not day:
        return None, "calendar_returned_empty"
    compact = "".join(ch for ch in str(day) if ch.isdigit())[:8]
    if len(compact) != 8:
        return None, f"calendar_returned_unparseable:{day!r}"
    return compact, str(day)


def _b_pit_finding(day: str) -> dict[str, Any]:
    from services.b_pit_mart_cutover import (
        load_b_pit_mart_cutover_config,
        resolve_b_pit_mart_production_read,
    )

    cfg = load_b_pit_mart_cutover_config()
    declared = bool(cfg.cutover_allowed)
    window_end = str(cfg.expected_window_end or "")
    if not declared:
        return {
            "check": "b_pit_mart",
            "status": STATUS_PASS,
            "declared_cutover_allowed": False,
            "trade_date": day,
            "resolved_status": None,
            "resolved_source": None,
            "reasons": [],
            "detail": "cutover_allowed=false — 没有声明，也就无从背离",
        }
    read = resolve_b_pit_mart_production_read(day)
    effective = read.status == "MART_CUTOVER"
    window_lapsed = bool(window_end) and day > window_end
    if effective:
        detail = f"声明与实际一致：{day} → {read.status}/{read.source}"
    elif window_lapsed:
        detail = (
            f"attested 窗口 {cfg.expected_window_start}–{window_end} 已成过去时；"
            f"{day} → {read.status}/{read.source}。晚于窗末的任何 trade_date 一律 "
            "fail-closed 回 legacy —— 跑日更不会自愈，须 owner 重新 attest 或把 "
            "cutover_allowed 改回 false"
        )
    else:
        detail = (
            f"声明 cutover_allowed=true 但 {day} → {read.status}/{read.source}；"
            "非窗口原因 (definition/hash/shadow 证据不匹配)，同样须 owner 裁决"
        )
    return {
        "check": "b_pit_mart",
        "status": STATUS_PASS if effective else STATUS_FAIL,
        "declared_cutover_allowed": True,
        "trade_date": day,
        "resolved_status": read.status,
        "resolved_source": read.source,
        "window_start": cfg.expected_window_start,
        "window_end": window_end,
        "window_lapsed": window_lapsed,
        "reasons": list(read.reasons)[:5],
        "detail": detail,
    }


def _tier12_finding(day: str) -> dict[str, Any]:
    from services.tier12_consumer_cutover import (
        load_tier12_consumer_cutover_config,
        resolve_tier12_consumer_cutover,
    )

    cfg = load_tier12_consumer_cutover_config()
    declared = bool(cfg.cutover_allowed)
    if not declared:
        return {
            "check": "tier12_consumer",
            "status": STATUS_PASS,
            "declared_cutover_allowed": False,
            "trade_date": day,
            "resolved_status": None,
            "resolved_source": None,
            "reasons": [],
            "detail": "cutover_allowed=false — 没有声明，也就无从背离",
        }
    decision = resolve_tier12_consumer_cutover(day)
    effective = bool(decision.cutover_allowed)
    return {
        "check": "tier12_consumer",
        "status": STATUS_PASS if effective else STATUS_WARN,
        "declared_cutover_allowed": True,
        "trade_date": day,
        "resolved_status": decision.status,
        "resolved_source": decision.source,
        "reasons": list(decision.reasons)[:5],
        "detail": (
            f"声明与实际一致：{day} → {decision.status}/{decision.source}"
            if effective
            else (
                f"声明 cutover_allowed=true 但 {day} → {decision.status}/"
                f"{decision.source}。逐日 accepted partition 依赖是 config 写明的预期"
                "回落，故记 WARN 不记 FAIL —— 但「声明已切换、实际走 legacy」必须被看见"
            )
        ),
    }


_CHECKS = (_b_pit_finding, _tier12_finding)


def evaluate() -> dict[str, Any]:
    day, detail = _latest_trade_date()
    if day is None:
        return {
            "kind": "cutover_effective_report",
            "overall": "WARN",
            "trade_date": None,
            "findings": [
                {
                    "check": "trading_calendar",
                    "status": STATUS_UNVERIFIED,
                    "detail": (
                        f"无法取得最近已完成交易日 ({detail}) —— cutover 生效性未验证。"
                        "「查不了」不等于「没问题」"
                    ),
                    "reasons": [detail],
                }
            ],
            "counts": {STATUS_UNVERIFIED: 1},
        }

    findings: list[dict[str, Any]] = []
    for fn in _CHECKS:
        try:
            findings.append(fn(day))
        except Exception as exc:  # noqa: BLE001 — resolver 异常 = 未验证，不是通过
            findings.append(
                {
                    "check": getattr(fn, "__name__", "unknown").strip("_").replace("_finding", ""),
                    "status": STATUS_UNVERIFIED,
                    "trade_date": day,
                    "reasons": [f"{type(exc).__name__}: {exc}"[:160]],
                    "detail": "resolver 抛异常 —— 无法裁决 cutover 是否生效",
                }
            )

    counts: dict[str, int] = {}
    for row in findings:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    rank = max((_RANK.get(row["status"], 1) for row in findings), default=0)
    return {
        "kind": "cutover_effective_report",
        "overall": _OVERALL_BY_RANK[rank],
        "trade_date": day,
        "trade_date_source": "services.calendar.latest_completed_trade_date",
        "findings": findings,
        "counts": counts,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="cutover 声明 vs resolver 实际裁决")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    ap.add_argument("--json-out", default=None, help="写 JSON 报告到该路径 (repo 相对)")
    args = ap.parse_args(argv)

    report = evaluate()

    if args.json_out:
        out = Path(args.json_out)
        if not out.is_absolute():
            out = REPO / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"[cutover-effective] overall={report['overall']} trade_date={report['trade_date']}")
        for row in report["findings"]:
            print(f"  [{row['status']}] {row['check']}: {row['detail']}")
            if row.get("reasons"):
                print(f"      reasons={row['reasons']}")
    return _EXIT[report["overall"]]


if __name__ == "__main__":
    raise SystemExit(main())
