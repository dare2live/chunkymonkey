#!/usr/bin/env python3
"""check_cutover_effective — config 声明的 cutover 意图 vs resolver 实际裁决。

owner: backend/config/governance_gates.yaml (runtime_checks.cutover_effective)
+ goal.md「治理体系重构」P1.2。

**为什么需要这道检查** (2026-08-10 治理审计实证)：当时 `b_pit_mart_cutover.yaml` 写着
``cutover_allowed: true``，而 attested shadow 窗口末端是 ``20260722`` —— 之后的任何
trade_date 一律 fail-closed 回 legacy mart。也就是说这个「已切换」的声明**早就不生效
了**，消费方一直走旧路径，而 goal.md / BOARD 仍按 True 叙述。整整 13 个交易日没人发现，
因为唯一会算出这个背离的地方 (BOARD 投影) 挂在 **commit** 路径上 —— 查不查取决于
「有没有人恰好提交相关代码」。受害时刻是每次跑日更，门就该装在日更里。
(b_pit 那套两轨+窗口已于 2026-08-14 整层退役 —— 实测它与生产 mart 逐日全等、
risk_on 翻转 0 次，即「切换」前后是同一个数。本检查保留 tier12 一侧，
立门的理由不变：**声明与实际裁决的背离必须在受害时刻被看见**。)

判据 (不做关键词猜测)：把**最近一个已完成收盘的交易日**送进各自的 production
resolver —— 那正是消费方真实会问的那一天。真相源是交易日历
(``services.calendar.latest_completed_trade_date``)，不是 wall-clock。

分级 (区分「永远修不好」与「今天恰好没有」)：

* ``tier12_consumer`` → **WARN**。它按设计逐日依赖 accepted partition，某天没有
  accepted 就回落 legacy 是**写在 config 注释里的预期行为**；把它当 FAIL 会 cry wolf。
  但「声明 true 而今天实际走 legacy」仍是必须被看见的观测，不能静默。

分级保留「结构性 vs 逐日」两档 —— 结构性背离(policy hash / definition version /
canary scope 冲突)跑多少次日更都不会自愈 → FAIL；逐日回落 → WARN。

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

# tier12 的**逐日**回落原因：当天没有 accepted partition。config 注释写明这属预期，
# 发布一期即自愈 → WARN。其余原因 (definition_version / config_hash 与已发布 payload
# 不一致、canary scope 冲突等) 是**结构性**的，跑多少次日更都不会自愈 → FAIL，与逐日回落
# 同级。2026-08-11 独立审查 finding #3: 首版把所有非生效原因一律判 WARN，等于让一个
# 永久坏掉的 cutover 配置永远顶着「这是预期回落」的标签 —— 恰恰是本检查要消灭的失效模式。
_TIER12_TRANSIENT_REASONS = frozenset({"missing_accept", "no_accepted_partition_for_day"})

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
    reasons = list(decision.reasons)
    transient = bool(reasons) and all(r in _TIER12_TRANSIENT_REASONS for r in reasons)

    if effective:
        status, detail = STATUS_PASS, f"声明与实际一致：{day} → {decision.status}/{decision.source}"
    elif transient:
        status = STATUS_WARN
        detail = (
            f"声明 cutover_allowed=true 但 {day} → {decision.status}/{decision.source}。"
            "原因是当天没有 accepted partition —— 逐日回落是 config 写明的预期行为，"
            "发布一期即自愈，故记 WARN 不记 FAIL；但「声明已切换、实际走 legacy」必须被看见"
        )
    else:
        status = STATUS_FAIL
        detail = (
            f"声明 cutover_allowed=true 但 {day} → {decision.status}/{decision.source}，"
            f"原因 {reasons[:3]} **不是**逐日回落 —— 这是 config 与已发布 payload 的结构性"
            "不一致，跑多少次日更都不会自愈，须 owner 裁决"
        )
    return {
        "check": "tier12_consumer",
        "status": status,
        "declared_cutover_allowed": True,
        "trade_date": day,
        "resolved_status": decision.status,
        "resolved_source": decision.source,
        "reasons": reasons[:5],
        "transient_reason": transient,
        "detail": detail,
    }


_CHECKS = (_tier12_finding,)


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
