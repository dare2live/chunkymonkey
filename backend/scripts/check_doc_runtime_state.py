#!/usr/bin/env python3
"""check_doc_runtime_state — 人工文档里写死运行时状态的对账门 (goal.md P2.1)。

owner: backend/config/doc_runtime_state.yaml + docs/README.md 文档生命周期节。

**根因** (2026-08-10 → 08-11 两次实测): `check_doc_drift` 报 `stale_count=0` 的同时,
`goal.md` 写 `accepted daily→20260721`、`PROJECT_INDEX` 写 `→20260720`, 而真相源是
`→20260804` —— 两份手写文档互相矛盾且同时落后两周。原因是现有文档门**只查悬空链接与
代码路径, 不查语义与时效**。2026-08-10 那轮手工清理过一次, 两周后同一份 PROJECT_INDEX
仍有三处漏网 + 一处自相矛盾的计数。**靠人扫必漏, 所以要机器扫。**

**判据** (docs/README.md 一句话): 这个值会不会因为系统正常跑一次日更就变?
会变 = 运行时状态, 只能指向真相源; 不会变 = 不变量, 写死是对的。

**机制 — 默认禁止 + 显式豁免, 不做语义猜测**:
扫活文档里的紧凑 8 位日期 (`20260720`)。本仓稳定约定是历史叙述写带连字符的
`2026-07-24`, 故只扫紧凑格式天然避开历史叙述。未豁免的一律报出来, 要么改成指针,
要么在 yaml 里写明为什么它是常量。零假阴性; 假阳性一次性收口, 而写豁免这个动作
本身就强制作者回答「常量还是状态」。

**豁免自清**: 列在 yaml 里但文档中已不存在的豁免会被报成 stale —— 豁免清单不许烂。

**分级**: 未豁免且落在 (契约起点, live frontier) 区间 = `stale_frontier` (几乎肯定是
写死的旧前沿, 附真相源当前值); 其余未豁免 = `undeclared` (要求作者表态)。

退出码: 0=全部已声明 / 1=有未声明或 stale 豁免。本门属 scaffold 组 (受害者是下一个
读文档的人), 在 commit 路径 warn-only; 批量收口走 `scripts/chunkyctl scaffold-fix`。

用法:
    PYTHONPATH=backend python backend/scripts/check_doc_runtime_state.py [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = REPO / "backend" / "config" / "doc_runtime_state.yaml"

# 紧凑 8 位日期, 且**必须独立成词**。
# 词边界不是装饰: 文件名里的日期 (`analysis/DOC_CLEANUP_20260723.md`、
# `foundation_phase_reeval_20260721.md`) 前面是 `_`, 属标识符的一部分, 不是状态声明;
# 真正的状态声明前面是 `→` / 空格 / 反引号 / `=`。首版没排除它, 一跑出来近八成是文件名
# 噪音 —— 噪音门等于没门, 人会直接学会无视它。
_COMPACT_DATE_RE = re.compile(r"(?<![\w-])(20\d{6})(?![\w-])")


class PolicyError(RuntimeError):
    """政策缺失/不合法 —— 门不得在策略坏掉时假装通过。"""


def load_policy(path: Path | None = None) -> dict[str, Any]:
    path = path or DEFAULT_POLICY
    if not path.is_file():
        raise PolicyError(f"missing policy: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"unreadable policy: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise PolicyError("policy root must be a mapping with version: 1")
    for key in ("scan_files", "global_constants", "file_exemptions"):
        if not isinstance(raw.get(key), list):
            raise PolicyError(f"policy.{key} must be a list")
    for group in ("global_constants", "file_exemptions"):
        for i, row in enumerate(raw[group]):
            if not isinstance(row, dict) or not row.get("value") or not row.get("reason"):
                raise PolicyError(f"policy.{group}[{i}] 必须带 value 与 reason")
            if group == "file_exemptions" and not row.get("file"):
                raise PolicyError(f"policy.file_exemptions[{i}] 必须带 file")
    return raw


def live_frontier(policy: dict[str, Any]) -> tuple[str | None, str]:
    """真相源当前前沿；取不到就诚实返回 None，不让门凭空判 stale。"""
    ref = policy.get("frontier_reference") or {}
    dataset_id = ref.get("dataset_id")
    if not dataset_id:
        return None, "policy 未声明 frontier_reference.dataset_id"
    try:
        sys.path.insert(0, str(REPO / "backend"))
        from services.project_status import accepted_frontier, calendar_anchor

        cal = calendar_anchor()
        anchor = cal.get("latest_completed_trade_date") if cal.get("status") == "ok" else None
        fr = accepted_frontier(anchor)
        if fr.get("status") != "ok":
            return None, f"前沿不可查: {fr.get('reason')}"
        for row in fr["datasets"]:
            if row["dataset_id"] == dataset_id:
                return str(row["frontier"]), f"{dataset_id} @ {row['db']}"
        return None, f"真相源无该 dataset: {dataset_id}"
    except Exception as exc:  # noqa: BLE001 — 查不了就标 unknown，不猜
        return None, f"{type(exc).__name__}: {str(exc)[:80]}"


def scan(policy: dict[str, Any], *, repo: Path = REPO) -> dict[str, Any]:
    constants = {str(c["value"]): c["reason"] for c in policy["global_constants"]}
    exemptions: dict[tuple[str, str], str] = {
        (str(e["file"]), str(e["value"])): str(e["reason"]) for e in policy["file_exemptions"]
    }
    frontier, frontier_source = live_frontier(policy)
    contract_start = min(constants) if constants else None

    seen_exemptions: set[tuple[str, str]] = set()
    findings: list[dict[str, Any]] = []
    scanned: list[str] = []

    for rel in policy["scan_files"]:
        path = repo / rel
        if not path.is_file():
            findings.append({
                "file": rel, "line": 0, "value": "", "kind": "missing_scan_target",
                "detail": "policy 声明要扫但文件不存在 —— 政策自己烂了",
            })
            continue
        scanned.append(rel)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for value in _COMPACT_DATE_RE.findall(line):
                if value in constants:
                    continue
                key = (rel, value)
                if key in exemptions:
                    seen_exemptions.add(key)
                    continue
                kind = "undeclared"
                detail = "未声明的紧凑日期 —— 改成指向 `scripts/chunkyctl status`，或在 doc_runtime_state.yaml 写明它为何是常量"
                if (
                    frontier
                    and contract_start
                    and contract_start < value < frontier
                ):
                    kind = "stale_frontier"
                    detail = (
                        f"疑似写死的旧前沿：真相源当前是 {frontier} ({frontier_source})，"
                        f"文档写 {value}，落后 {int(frontier) - int(value)} 个自然日编号"
                    )
                findings.append({
                    "file": rel, "line": lineno, "value": value, "kind": kind,
                    "detail": detail, "excerpt": line.strip()[:120],
                })

    for key, reason in exemptions.items():
        if key not in seen_exemptions:
            findings.append({
                "file": key[0], "line": 0, "value": key[1], "kind": "stale_exemption",
                "detail": f"豁免已失效（文档里不再出现该日期）：{reason}",
            })

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return {
        "kind": "doc_runtime_state_report",
        "overall": "FAIL" if findings else "PASS",
        "scanned_files": scanned,
        "live_frontier": frontier,
        "live_frontier_source": frontier_source,
        "counts": counts,
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="人工文档写死运行时状态的对账门")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--policy", type=Path, default=None)
    args = ap.parse_args(argv)

    try:
        policy = load_policy(args.policy)
    except PolicyError as exc:
        print(f"[doc-runtime-state] FAIL: {exc}", file=sys.stderr)
        return 1
    report = scan(policy)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[doc-runtime-state] {report['overall']} "
              f"扫 {len(report['scanned_files'])} 份活文档; "
              f"真相源前沿={report['live_frontier'] or 'unknown'} ({report['live_frontier_source']})")
        for f in report["findings"]:
            where = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
            print(f"  [{f['kind']}] {where} `{f['value']}` — {f['detail']}")
            if f.get("excerpt"):
                print(f"      {f['excerpt']}")
        if not report["findings"]:
            print("  人工文档零写死运行时状态。")
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
