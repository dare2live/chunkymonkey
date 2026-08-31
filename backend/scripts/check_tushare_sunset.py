#!/usr/bin/env python3
"""TuShare 授权到期风险门 (tushare_sunset.yaml 与 sync_registry.yaml 对账).

owner: backend/config/tushare_sunset.yaml + docs/engineering_governance.md §14.

**根因**: TuShare 授权 2026-09-10 到期，不续期。tushare.py 在 expires_at <= now 时
直接 raise TuShareAuthorizationError —— 硬停不是降级。此前三月 primitives/seed.py 的
涨跌停规则过时无人发现，正因为它零消费/零执法。**没有门的清单必然烂掉**。

**检查三项**:
1. 覆盖完整性: sync_registry.yaml 里每个 source: tushare 的域，必须在 tushare_sunset.yaml
   里出现（要么有自己的条目，要么列在 undecided_domains）。遗漏 → **FAIL** (真漂移)。

2. 反向自清: tushare_sunset.yaml 里提到的域，若在 registry 里已不是 source: tushare
   （已换源或已退役），应报 **WARN** —— 但 decision: derive/replace 且 status: done
   的条目除外（已完成的记录，正常）。

3. 到期倒计时: 比较 authorization_expires 与当天：
   - 未裁决域数 > 0 且距到期 > 14 天 → WARN (列出全部未裁决域名)
   - 未裁决域数 > 0 且距到期 ≤ 14 天 → WARN 但消息升级为醒目提示
   - 已过期 + 仍有未裁决域 → **FAIL**

**关键设计约束**（不许改）: 未裁决域在到期前**只报 WARN 不 FAIL**。理由: 现在有
24 个未裁决域、距到期 9 天，若设成 FAIL 会立刻阻断所有提交 —— 过度阻断只会卡住诚实的
提交者。但 WARN **必须列出全部未裁决域名** —— 项目教训是「warn-only 会退化成 warn-nothing」。

退出码: 有 fail → 非 0; 只有 warn → 0。门 group: scaffold (warn-only 组)。

用法:
    PYTHONPATH=backend python backend/scripts/check_tushare_sunset.py [--today YYYYMMDD]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SUNSET = REPO / "backend" / "config" / "tushare_sunset.yaml"
DEFAULT_REGISTRY = REPO / "backend" / "config" / "sync_registry.yaml"


class PolicyError(RuntimeError):
    """政策缺失/不可解析 — 门不得在策略坏掉时假装通过。"""


def load_sunset(path: Path) -> dict[str, Any]:
    """加载并校验 tushare_sunset.yaml。"""
    if not path.is_file():
        raise PolicyError(f"missing sunset policy: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"unreadable sunset policy: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise PolicyError("sunset policy root must be a mapping with version: 1")
    if "authorization_expires" not in raw:
        raise PolicyError("sunset policy missing authorization_expires")
    return raw


def load_registry(path: Path) -> dict[str, Any]:
    """加载并校验 sync_registry.yaml。"""
    if not path.is_file():
        raise PolicyError(f"missing registry: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"unreadable registry: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise PolicyError("registry root must be a mapping with version: 1")
    if not isinstance(raw.get("domains"), dict):
        raise PolicyError("registry.domains must be a dict")
    return raw


def extract_tushare_domains(registry: dict[str, Any]) -> set[str]:
    """从 registry 提取所有 source: tushare 的域名。"""
    domains: set[str] = set()
    for domain_name, domain_spec in registry.get("domains", {}).items():
        if isinstance(domain_spec, dict) and domain_spec.get("source") == "tushare":
            domains.add(domain_name)
    return domains


def extract_sunset_domains(sunset: dict[str, Any]) -> dict[str, Any]:
    """从 sunset 提取所有被记录的域 (包括 undecided_domains)。
    返回 dict {domain_name: (decision|"undecided", status_if_any)}.
    """
    domains: dict[str, Any] = {}
    domain_entries = sunset.get("domains", {})
    for domain_name, entry in domain_entries.items():
        if domain_name == "undecided_domains":
            # 这是一个列表，不是一个域条目
            continue
        if isinstance(entry, dict):
            decision = entry.get("decision", "unknown")
            status = entry.get("status")
            domains[domain_name] = (decision, status)

    # 添加 undecided_domains 中的项
    for undecided_name in sunset.get("domains", {}).get("undecided_domains", []):
        if undecided_name not in domains:
            domains[undecided_name] = ("undecided", None)

    return domains


def run(
    sunset_path: Path = DEFAULT_SUNSET,
    registry_path: Path = DEFAULT_REGISTRY,
    today: date | None = None,
) -> tuple[list[str], list[str]]:
    """执行三项检查。返回 (fails, warns)。"""
    if today is None:
        today = date.today()  # rule-compliance: ok evidence=与授权到期日比对的墙钟日期(非交易日判定/非交易决策锚), 墙钟即语义本身

    fails: list[str] = []
    warns: list[str] = []

    sunset = load_sunset(sunset_path)
    registry = load_registry(registry_path)

    registry_tushare = extract_tushare_domains(registry)
    sunset_domains = extract_sunset_domains(sunset)

    # ── 检查 1: 覆盖完整性 ────────────────────────────────────────────
    missing_from_sunset = registry_tushare - set(sunset_domains.keys())
    if missing_from_sunset:
        fails.append(
            f"registry 有 tushare 域但 sunset.yaml 缺：{sorted(missing_from_sunset)}。"
            "每个 tushare 源的域必须在 tushare_sunset.yaml 里有决策记录或列在 undecided_domains。"
        )

    # ── 检查 2: 反向自清 ────────────────────────────────────────────
    for sunset_domain, (decision, status) in sunset_domains.items():
        # 如果这个域是 "undecided"，跳过（这是列表中的项，不是条目）
        if sunset_domain in sunset.get("domains", {}).get("undecided_domains", []):
            continue

        # 如果这个域在 registry 中已不是 tushare 源
        if sunset_domain not in registry_tushare:
            # 如果是 done 状态，说明已完成，不报 warn
            if status == "done":
                continue
            # 否则报 stale 豁免
            warns.append(
                f"sunset.yaml 提到的域 {sunset_domain} 在 registry 中已不是 source: tushare "
                f"(decision={decision}, status={status})。"
                "若已完成迁移，请标记 status: done；否则清理该条目防豁免清单烂掉。"
            )

    # ── 检查 3: 到期倒计时 ────────────────────────────────────────────
    expires_str = sunset.get("authorization_expires", "")
    try:
        expires_date = datetime.strptime(expires_str, "%Y-%m-%d").date()
    except ValueError:
        raise PolicyError(
            f"authorization_expires 格式不合法: {expires_str!r} "
            "(期望 YYYY-MM-DD 格式)"
        ) from None

    undecided = sunset.get("domains", {}).get("undecided_domains", [])
    undecided_list = sorted(undecided) if undecided else []

    if undecided_list:
        days_left = (expires_date - today).days
        if days_left < 0:
            # 已过期
            fails.append(
                f"TuShare 授权已于 {expires_date} 过期（今天 {today}），"
                f"但仍有 {len(undecided_list)} 个未裁决域。"
                "这些域将立刻断流，必须立即决策或删除：\n  "
                + "\n  ".join(f"- {d}" for d in undecided_list)
            )
        elif days_left <= 14:
            # 距到期 ≤ 14 天，报醒目的 WARN
            warns.append(
                f"[紧急] TuShare 授权距到期仅 {days_left} 天（{expires_date}），"
                f"仍有 {len(undecided_list)} 个未裁决域必须立即处理：\n  "
                + "\n  ".join(f"- {d}" for d in undecided_list)
            )
        else:
            # 距到期 > 14 天，普通 WARN
            warns.append(
                f"TuShare 授权距到期 {days_left} 天（{expires_date}），"
                f"仍有 {len(undecided_list)} 个未裁决域待处理：\n  "
                + "\n  ".join(f"- {d}" for d in undecided_list)
            )

    return fails, warns


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sunset", type=Path, default=DEFAULT_SUNSET)
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    ap.add_argument(
        "--today",
        type=str,
        default=None,
        help="当天日期 (YYYYMMDD 格式，用于测试注入; 默认系统日期)",
    )
    args = ap.parse_args(argv)

    today = None
    if args.today:
        try:
            today = datetime.strptime(args.today, "%Y%m%d").date()
        except ValueError:
            print(f"[tushare-sunset] FAIL: --today 格式不合法: {args.today}", file=sys.stderr)
            print("[tushare-sunset] verdict=FAIL fails=1 warns=0")
            return 1

    try:
        fails, warns = run(args.sunset, args.registry, today)
    except PolicyError as exc:
        print(f"[tushare-sunset] FAIL: {exc}", file=sys.stderr)
        print("[tushare-sunset] verdict=FAIL fails=1 warns=0")
        return 1

    for w in warns:
        print(f"[WARN] {w}")
    for f in fails:
        print(f"[FAIL] {f}")

    verdict = "FAIL" if fails else "WARN" if warns else "PASS"
    print(f"[tushare-sunset] verdict={verdict} fails={len(fails)} warns={len(warns)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
