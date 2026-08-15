#!/usr/bin/env python3
"""把 moth 的一次运行结果分流成两个裁决: 数据不变量(阻断) vs 棘轮/脚手架(提示)。

owner: backend/config/governance_gates.yaml (gates: moth_invariants / moth)
断言与其 severity 的 owner: .moth/assertions/claims.yaml

**为什么需要它** (2026-08-14 审计实证):
moth 是 safe_commit 里的**一道门**, 而它背后是 38 条断言。P1 把 moth 整体归进 scaffold 组
(warn-only) 之后, 38 条断言**按整体继承**了「不阻断」—— 其中却混着真正的数据不变量:
`calendar-floor` 的 claim 自己写着「回退 = 静默 clamp 复发」、`dc-member-truncation-pin`
写着「2026-06-12 发现静默截断」。实测这两条在 pytest 里覆盖为 **0**, 即 moth 是它们唯一的
执法点; 而 CI 完全不跑 moth。结论: 这些不变量当时**在任何地方都没有阻断力**。
这与 warn-only 短路吞掉 coupling 是同一形态 —— **粗粒度分组悄悄改了不该改的东西的强制级别**。

**为什么不是跑两次 moth**: 一次 `moth assert` 实测 8.2s。分流只需要它的 JSON 输出,
把同一次结果读两遍即可 —— 单一计算点, 不为分级付第二次运行成本。

用法 (safe_commit 先产 JSON, 再分别判):
    moth assert --repo . --format json > moth.json
    python backend/scripts/check_moth_invariants.py moth.json            # 只判 blocking
    python backend/scripts/check_moth_invariants.py moth.json --all      # 判全部(scaffold 用)

退出码: 0=通过 / 1=有失败 / 2=输入不可解析(**不当作通过** —— 查不了不等于没问题)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CLAIMS = REPO / ".moth" / "assertions" / "claims.yaml"


def load_blocking_ids(claims_path: Path = CLAIMS) -> set[str]:
    """从 claims.yaml 读哪些断言标了 severity: blocking。

    读不到 = fail-closed: 返回 None 让调用方判 UNVERIFIED, 而不是「没有 blocking 项」。
    """
    import yaml

    raw = yaml.safe_load(claims_path.read_text(encoding="utf-8")) or {}
    out: set[str] = set()
    for a in raw.get("assertions") or []:
        if str(a.get("severity") or "").strip() == "blocking":
            out.add(str(a.get("id")))
    return out


def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pack in payload.get("packs") or []:
        for r in pack.get("results") or []:
            if isinstance(r, dict):
                rows.append(r)
    return rows


def _failed(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or row.get("state") or "").lower()
    if status:
        return status not in {"pass", "ok", "passed"}
    # 无 status 字段时退回 ok 布尔; 两者都没有 = 判失败(不猜"它应该是过的")
    return not bool(row.get("ok", row.get("passed", False)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", help="moth assert --format json 的输出文件")
    ap.add_argument("--all", action="store_true", help="判全部断言 (scaffold 门用); 默认只判 blocking")
    args = ap.parse_args(argv)

    try:
        payload = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[moth-invariants] UNVERIFIED: 读不到/解析不了 moth JSON ({exc})", file=sys.stderr)
        return 2

    rows = _results(payload)
    if not rows:
        print("[moth-invariants] UNVERIFIED: moth JSON 里零条结果 —— 空扫描不算通过", file=sys.stderr)
        return 2

    if args.all:
        bad = [r for r in rows if _failed(r)]
        label = "全部断言"
    else:
        try:
            blocking = load_blocking_ids()
        except Exception as exc:  # noqa: BLE001
            print(f"[moth-invariants] UNVERIFIED: 读不到 severity 声明 ({exc})", file=sys.stderr)
            return 2
        if not blocking:
            print("[moth-invariants] UNVERIFIED: 没有任何断言标 severity: blocking —— "
                  "要么声明丢了, 要么分级被抹平; 不当作通过", file=sys.stderr)
            return 2
        bad = [r for r in rows if str(r.get("id")) in blocking and _failed(r)]
        label = f"{len(blocking)} 条数据不变量"

    if bad:
        print(f"[moth-invariants] FAIL: {label} 里 {len(bad)} 条未闭合:", file=sys.stderr)
        for r in bad:
            print(f"  ✗ {r.get('id')}: {str(r.get('detail') or r.get('claim') or '')[:120]}", file=sys.stderr)
        return 1

    print(f"[moth-invariants] PASS: {label} 全绿 (共扫 {len(rows)} 条)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
