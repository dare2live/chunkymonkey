#!/usr/bin/env python3
"""审计 sync_registry.yaml 里四种豁免机制的治理状态 (audit-only, 不阻断)。

**背景**: sync_registry.yaml 有四种让某些数据脱离完整性检查的豁免机制——
known_empty_days(实测源端真空日)、verified_low_days(已向 vendor 核证的真实低值日,
唯一结构上强制带理由的)、gap_tolerance(域级缺口容忍类型)、row_dip_tolerance
(域级行数骤降容忍)。业界标准治理要求豁免带四要素: 具体放宽项 + 补偿控制 + owner +
复核到期日。当前 registry 里所有豁免都没有 owner/到期日字段, 也没有强制写理由的机制
(除 verified_low_days 结构性强制外)——过期无主的豁免本身就是审计发现项, 会越积越多
变成"永久绿"却没人复核。

**为什么是 audit 模式** (仿 OPA Gatekeeper 新策略先 dry-run 的做法): 现有全部豁免都
没有 owner/到期日, 一上来强制会让所有域报红。本脚本只报告不拦截, 给人工先把存量豁免
补齐理由/指定 owner, 再考虑升级 warn/deny。**本脚本不给 registry 加任何必填校验,
不修改任何现有豁免配置** —— 那是下一阶段的事。

**"有无理由"怎么判定**: verified_low_days 结构上就是「日期 -> 理由」的映射, 直接判定
有理由。其余三种字段本身不带理由槽位, 只能退而求其次用简单的行号邻近匹配——在原始
YAML 文本里找到该字段所在行, 取同行尾注 + 下方按缩进续行的注释拼成理由文本, 有内容即算"有理由"。
这是故意简化的启发式(不解析该字段下方或同行的行内注释), 会有漏判/误判, 但审计阶段
"能定位到大部分"已经够用, 不必做到完美。

用法:
    PYTHONPATH=backend python backend/scripts/audit_exemptions.py            # 纯文本报告
    PYTHONPATH=backend python backend/scripts/audit_exemptions.py --json     # 机器可读
    PYTHONPATH=backend python backend/scripts/audit_exemptions.py --registry /path/to/sync_registry.yaml

退出码: 恒为 0 (audit 模式不阻断)。除非脚本自身出错(读不到 YAML / 解析失败)才非零。
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
DEFAULT_REGISTRY_PATH = REPO / "backend" / "config" / "sync_registry.yaml"

# 四种豁免机制, 顺序即报告里的展示顺序。
EXEMPTION_TYPES = ("known_empty_days", "verified_low_days", "gap_tolerance", "row_dip_tolerance")

# domains 段里, 域名固定 2 空格缩进; 域内字段固定 4 空格缩进 (registry 全文件统一约定,
# 实测核对过, 见 sync_registry.yaml 全部 44 个域)。
_DOMAIN_HEADER_RE = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_]*):")
_FIELD_RE = re.compile(
    r"^    (known_empty_days|verified_low_days|gap_tolerance|row_dip_tolerance):"
)


def _inline_comment(line: str) -> str:
    """行内注释文本 (引号感知, 避免把值里的 # 当注释)。"""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[i + 1:].strip()
    return ""


def _reason_for_field(lines: list[str], field_line_idx: int) -> str:
    """字段的理由 = 同行尾注 + 其下方的续行注释; 没有则空串。

    2026-08-23 修正方向 (原实现向**上**收集注释, 实测 4/4 误判):
    本 registry (及 YAML 惯例) 把说明写在字段**右边**并在**下方**按缩进续行, 例如

        known_empty_days: ["20231122"]   # 2026-07-10 撤销之撤销 (owner=...
          # 第四轮节): 2026-07-09 那次"实测vendor现返5075行"的撤销测错域 ...

    向上找会同时造成两种错: (a) 假警报——上面这类写全了理由的被判"无理由"
    (实测 moneyflow_dc / cyq_perf / block_trade 全中); (b) 假绿——把上一个
    字段的注释块尾部"借"给下一个字段 (agent 自陈 stk_surv row_dip_tolerance 一例)。
    续行以**缩进深于字段名**判定, 因此下一字段的独立头注释 (缩进与字段齐平) 不会被并入。
    """
    line = lines[field_line_idx]
    field_indent = len(line) - len(line.lstrip())
    collected: list[str] = []
    head = _inline_comment(line)
    if head:
        collected.append(head)
    for i in range(field_line_idx + 1, len(lines)):
        nxt = lines[i]
        stripped = nxt.strip()
        if not stripped.startswith("#"):
            break
        if len(nxt) - len(nxt.lstrip()) <= field_indent:
            break  # 与字段齐平 = 下一字段的头注释, 不属于本字段
        collected.append(stripped.lstrip("#").strip())
    return " ".join(part for part in collected if part)


def _field_line_index_map(lines: list[str]) -> dict[tuple[str, str], int]:
    """扫描原始文本, 记录每个 (域名, 字段名) 在 domains: 段内第一次出现的行号 (0-based)。"""
    field_lines: dict[tuple[str, str], int] = {}
    in_domains = False
    current_domain: str | None = None
    for idx, line in enumerate(lines):
        if line.startswith("domains:"):
            in_domains = True
            current_domain = None
            continue
        if not in_domains:
            continue
        header = _DOMAIN_HEADER_RE.match(line)
        if header:
            current_domain = header.group(1)
            continue
        if current_domain is None:
            continue
        field = _FIELD_RE.match(line)
        if field:
            field_lines.setdefault((current_domain, field.group(1)), idx)
    return field_lines


def _mk_record(domain: str, exemption_type: str, detail: str, date_count: int | None,
               reason: str) -> dict[str, Any]:
    has_reason = bool(reason and reason.strip())
    return {
        "domain": domain,
        "exemption_type": exemption_type,
        "detail": detail,
        "date_count": date_count,
        "has_reason": has_reason,
        "reason": reason.strip() if has_reason else None,
        "has_owner": False,   # registry 当前没有 owner 字段, 恒为 False
        "has_expiry": False,  # registry 当前没有复核到期日字段, 恒为 False
    }


def scan_exemptions(registry_path: Path) -> list[dict[str, Any]]:
    """读 sync_registry.yaml, 返回全部在用豁免的记录列表 (每条 = 一个域的一种豁免)。"""
    text = registry_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    doc = yaml.safe_load(text) or {}
    domains = doc.get("domains") or {}
    field_lines = _field_line_index_map(lines)

    records: list[dict[str, Any]] = []
    for domain, entry in domains.items():
        if not isinstance(entry, dict):
            continue

        known_empty = entry.get("known_empty_days") or []
        if isinstance(known_empty, list) and len(known_empty) > 0:
            idx = field_lines.get((domain, "known_empty_days"))
            reason = _reason_for_field(lines, idx) if idx is not None else ""
            records.append(_mk_record(
                domain, "known_empty_days", f"{len(known_empty)} 个日期",
                len(known_empty), reason))

        verified_low = entry.get("verified_low_days") or {}
        if isinstance(verified_low, dict) and len(verified_low) > 0:
            # 结构上自带理由: 直接拼各日期的核证理由, 不依赖注释行号匹配。
            reason = "; ".join(
                f"{d}: {r}" for d, r in verified_low.items()
                if isinstance(r, str) and r.strip()
            )
            records.append(_mk_record(
                domain, "verified_low_days", f"{len(verified_low)} 个日期",
                len(verified_low), reason))

        gap_tolerance = entry.get("gap_tolerance")
        if gap_tolerance and gap_tolerance != "none":
            idx = field_lines.get((domain, "gap_tolerance"))
            reason = _reason_for_field(lines, idx) if idx is not None else ""
            records.append(_mk_record(
                domain, "gap_tolerance", str(gap_tolerance), None, reason))

        row_dip_tolerance = entry.get("row_dip_tolerance")
        if row_dip_tolerance is True:
            idx = field_lines.get((domain, "row_dip_tolerance"))
            reason = _reason_for_field(lines, idx) if idx is not None else ""
            records.append(_mk_record(
                domain, "row_dip_tolerance", "true", None, reason))

    return records


def _overview(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    overview: dict[str, dict[str, Any]] = {
        t: {"domains": 0, "dates": 0 if t in ("known_empty_days", "verified_low_days") else None}
        for t in EXEMPTION_TYPES
    }
    for rec in records:
        t = rec["exemption_type"]
        overview[t]["domains"] += 1
        if overview[t]["dates"] is not None and rec["date_count"]:
            overview[t]["dates"] += rec["date_count"]
    return overview


def _risk_sorted(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """无理由且无 owner 排最前 (最可能烂掉); 其余次之。域名/类型再排序保证输出稳定。"""
    return sorted(
        records,
        key=lambda r: (r["has_reason"], r["has_owner"], r["domain"], r["exemption_type"]),
    )


TYPE_LABEL = {
    "known_empty_days": "known_empty_days",
    "verified_low_days": "verified_low_days",
    "gap_tolerance": "gap_tolerance",
    "row_dip_tolerance": "row_dip_tolerance",
}


def render_text(records: list[dict[str, Any]]) -> str:
    overview = _overview(records)
    ordered = _risk_sorted(records)
    total = len(records)
    no_reason = sum(1 for r in records if not r["has_reason"])

    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("豁免审计报告 (audit 模式, 只报告不拦截)")
    lines.append("=" * 78)
    lines.append("")
    lines.append("【总览】豁免类型 -> 使用域数 / 涉及日期数")
    lines.append("-" * 78)
    header = f"{'豁免类型':<22}{'使用域数':<12}{'涉及日期数':<12}"
    lines.append(header)
    for t in EXEMPTION_TYPES:
        o = overview[t]
        dates_col = str(o["dates"]) if o["dates"] is not None else "-"
        lines.append(f"{t:<22}{o['domains']:<12}{dates_col:<12}")
    lines.append("")
    lines.append(f"【逐条明细】共 {total} 条豁免, 按风险排序(无理由且无 owner 排最前)")
    lines.append("-" * 78)
    col = f"{'域名':<20}{'豁免类型':<20}{'具体内容':<14}{'有理由':<8}{'有owner':<10}{'有到期日':<10}"
    lines.append(col)
    for rec in ordered:
        lines.append(
            f"{rec['domain']:<20}{rec['exemption_type']:<20}{rec['detail']:<14}"
            f"{'是' if rec['has_reason'] else '否':<8}"
            f"{'是' if rec['has_owner'] else '否':<10}"
            f"{'是' if rec['has_expiry'] else '否':<10}"
        )
        if rec["has_reason"] and rec["reason"]:
            lines.append(f"    理由: {rec['reason']}")
    lines.append("")
    lines.append("-" * 78)
    lines.append(f"总结: 共 {total} 个豁免, 其中 {no_reason} 个连理由都没有。")
    lines.append("=" * 78)
    return "\n".join(lines)


def render_json(records: list[dict[str, Any]]) -> str:
    overview = _overview(records)
    total = len(records)
    no_reason = sum(1 for r in records if not r["has_reason"])
    payload = {
        "overview": overview,
        "exemptions": _risk_sorted(records),
        "total_exemptions": total,
        "no_reason_count": no_reason,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH,
                     help="sync_registry.yaml 路径 (默认项目正式 registry)")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON 而非纯文本")
    args = ap.parse_args(argv)

    try:
        records = scan_exemptions(args.registry)
    except (OSError, yaml.YAMLError) as exc:
        print(f"审计脚本出错: 读取/解析 {args.registry} 失败: {exc}", file=sys.stderr)
        return 1

    print(render_json(records) if args.json else render_text(records))
    return 0  # audit 模式恒不拦截; 非零只在脚本自身出错时出现 (见上面 except)


if __name__ == "__main__":
    raise SystemExit(main())
