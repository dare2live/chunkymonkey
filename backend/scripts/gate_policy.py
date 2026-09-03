#!/usr/bin/env python3
"""gate_policy — 治理门分布策略的执行面 (owner: backend/config/governance_gates.yaml)。

goal.md「治理体系重构」P1。三个消费场景，一个策略文件：

1. ``--names <group>``           safe_commit 取分组名单 → 阻断 / warn-only / 不跑
2. ``--run-system-health``       system_health 组的运行时自检 (ops/CI 手动入口；
                                 daily_update 里由 services/pipeline/store.py 跑同一份)
3. ``--scaffold-fix``            scaffold 组批量修 (regenerate 的真修，report 的报清单)

外加 ``--check``：把登记表与两处**必然存在的门名副本**对账 ——
``backend/scripts/classify_commit_tier.py`` 的 ALL_GATES_ORDERED (tier 剪枝用) 与
``scripts/safe_commit.sh`` 的 fail-closed 兜底串。三者任一漂移即红。为什么留副本：
那两处是 fail-closed 的最后一道 —— 策略文件本身坏掉时它们必须仍能让**全部门阻断**，
所以不能改成运行时依赖本文件。副本的正确性由本对账机械保证，不靠人记。

fail-closed：``--names`` 在策略不可用时打印空行并 exit 1；safe_commit 据此把
所有门当 diff_correctness 处理 (全阻断)，绝不因配置坏掉而放行。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.governance_gates import (  # noqa: E402
    GatePolicyError,
    RuntimeCheckSpec,
    dead_gate_report,
    stale_object_report,
    load_registry,
)

REPO = Path(__file__).resolve().parents[2]
SAFE_COMMIT = REPO / "scripts" / "safe_commit.sh"


def _print_names(group: str) -> int:
    try:
        names = load_registry().names_in_group(group)
    except GatePolicyError as exc:
        print("", end="")
        print(f"gate_policy: {exc}", file=sys.stderr)
        return 1
    print(" ".join(names))
    return 0


def _print_table() -> int:
    try:
        reg = load_registry()
    except GatePolicyError as exc:
        print(f"gate_policy: {exc}", file=sys.stderr)
        return 1
    for group in ("diff_correctness", "system_health", "scaffold"):
        body = reg.groups[group]
        names = reg.names_in_group(group)
        print(f"\n== {group} ({len(names)}) — 受害者={body['victim']} 何时={body['harm_at']} "
              f"commit={body['commit_behavior']}")
        print(f"   {' '.join(str(body['why']).split())}")
        for name in names:
            spec = next(g for g in reg.gates if g.name == name)
            print(f"   - {name}: {spec.checks}")
    print(f"\n== runtime_checks ({len(reg.runtime_checks)}) — daily_update 逐条跑")
    for spec in reg.runtime_checks:
        origin = f"from_gate={spec.from_gate}" if spec.from_gate else "runtime-native"
        print(f"   - {spec.id} ({origin}): {spec.script}")
    return 0


def _check() -> int:
    """登记表 ↔ classify_commit_tier ↔ safe_commit.sh 三处门名对账 + DEAD_GATE 死亡条件检测。

    退出码 (三态, 严重性递减):
      1 = 三方门名漂移 (策略文件本身坏了) —— fail-closed, 调用方须继续按旧行为全阻断。
      2 = 三方一致, 但至少一道门是 DEAD_GATE (它守的 object 一个路径都不命中) ——
          这不是「这次 diff 错了」, 是「这道门该删了」。safe_commit.sh 据此跳过该门,
          不阻断提交 (R1 原文: 门自己红并要求删门, 而不是拦提交)。
      0 = 全部一致, 零 DEAD_GATE。
    """
    problems: list[str] = []
    try:
        reg = load_registry()
    except GatePolicyError as exc:
        print(f"[gate-policy] FAIL: {exc}", file=sys.stderr)
        return 1
    registry_names = set(reg.gate_names)

    try:
        from scripts.classify_commit_tier import ALL_GATES_ORDERED
    except ImportError:  # 直跑脚本时 backend 在 sys.path，包名是 scripts
        from classify_commit_tier import ALL_GATES_ORDERED  # type: ignore
    classifier_names = set(ALL_GATES_ORDERED)
    if registry_names != classifier_names:
        problems.append(
            "governance_gates.yaml vs classify_commit_tier.ALL_GATES_ORDERED 漂移: "
            f"only_registry={sorted(registry_names - classifier_names)} "
            f"only_classifier={sorted(classifier_names - registry_names)}"
        )

    text = SAFE_COMMIT.read_text(encoding="utf-8")
    match = re.search(r'^COMMIT_TIER_GATES="([^"]*)"', text, re.M)
    if not match:
        problems.append("safe_commit.sh 里找不到 fail-closed 兜底串 COMMIT_TIER_GATES=\"...\"")
    else:
        shell_names = set(match.group(1).split())
        if registry_names != shell_names:
            problems.append(
                "governance_gates.yaml vs safe_commit.sh 兜底串漂移: "
                f"only_registry={sorted(registry_names - shell_names)} "
                f"only_shell={sorted(shell_names - registry_names)}"
            )

    # 门名漂移不 return —— 对象检查照跑。
    # 2026-09-04: 原来这里直接 return 1, 于是三处门名一有漂移, 下面的 DEAD_GATE /
    # STALE_OBJECT 检查**整个不执行**。而门名漂移最常见的场景恰恰是「正在增删门」——
    # 也就是人最需要知道"哪些门的对象已经没了"的那一刻, 这个信息被扣住了。
    # 与今天修的 dead_gate_report 是同一族缺陷: 一个检查因为控制流而静默不跑。
    # 漂移仍然 exit 1 (它是更硬的失败), 只是不再顺手吞掉另一半信息。
    for p in problems:
        print(f"[gate-policy] FAIL: {p}", file=sys.stderr)

    # 三方一致 —— 现在查死亡条件。DEAD_GATE 不是策略文件坏了, 是某道门守的对象已经
    # 不在这个仓库里了; 处置权交给人 (删 YAML 条目 + safe_commit.sh Step + 测试),
    # 机器只负责指出来, 不代为决定, 也不因此拦下这次提交。
    dead = dead_gate_report(reg)
    for name in sorted(dead):
        print(
            f"DEAD_GATE {name}: object 全部条目命中 0 ({', '.join(dead[name])}) → "
            "删这道门（YAML 条目 + safe_commit.sh Step + 测试）"
        )

    # STALE_OBJECT 与 DEAD_GATE 是两回事: 门还活着 (另有 object 命中), 只是清单里有几条
    # 路径已经不存在。处置是删那几条 glob, **不是删门**。前缀故意不叫 DEAD_GATE ——
    # safe_commit.sh:145 只抓 `^DEAD_GATE ` 并把该门整道跳过不跑, 一道活的阻断门绝不能
    # 因为清单里有条过期路径就被静默关掉。
    stale = stale_object_report(reg)
    for name in sorted(stale):
        print(
            f"STALE_OBJECT {name}: object 条目命中 0 ({', '.join(stale[name])}) → "
            "从该门 object 清单删掉这几条（门本身还活着, 照跑）"
        )

    print(
        f"[gate-policy] PASS: {len(registry_names)} 门三处一致 "
        f"(diff_correctness={len(reg.names_in_group('diff_correctness'))} "
        f"system_health={len(reg.names_in_group('system_health'))} "
        f"scaffold={len(reg.names_in_group('scaffold'))}) "
        f"dead_gates={len(dead)} stale_objects={len(stale)}"
    )
    if problems:
        return 1
    return 2 if (dead or stale) else 0


def _subprocess_runner(spec: RuntimeCheckSpec, args: list[str]) -> int:
    cmd = [sys.executable, spec.script, *args]
    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(REPO), text=True, check=False)
    return int(proc.returncode)


def _run_system_health(date: str, json_out: str | None) -> int:
    from services.governance_gates import run_runtime_checks

    try:
        reg = load_registry()
    except GatePolicyError as exc:
        print(f"[system-health] FAIL: {exc}", file=sys.stderr)
        return 1
    rows = run_runtime_checks(_subprocess_runner, date=date, registry=reg)
    failed = [r for r in rows if r["status"] == "fail"]
    report: dict[str, Any] = {
        "kind": "system_health_report",
        "date": date,
        "overall": "FAIL" if failed else "PASS",
        "checks": rows,
    }
    if json_out:
        out = Path(json_out)
        if not out.is_absolute():
            out = REPO / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[system-health] overall={report['overall']} ({len(rows)} checks)")
    for row in rows:
        print(f"  [{row['status']}] {row['id']} exit={row['exit']}")
        if row["status"] == "fail":
            print(f"      {row['degraded_msg']}")
    return 1 if failed else 0


def _scaffold_fix() -> int:
    try:
        reg = load_registry()
    except GatePolicyError as exc:
        print(f"[scaffold-fix] FAIL: {exc}", file=sys.stderr)
        return 1
    regenerated: list[str] = []
    reported: list[str] = []
    for spec in reg.scaffold_fixes:
        cmd = [sys.executable, *spec.command]
        print(f"\n=== {spec.id} ({spec.kind}) — {spec.note}")
        print(f"  $ {' '.join(cmd)}")
        proc = subprocess.run(cmd, cwd=str(REPO), text=True, check=False)
        if spec.kind == "regenerate":
            if proc.returncode == 0:
                regenerated.append(spec.id)
            else:
                print(f"  ERROR: {spec.id} 重生失败 (exit {proc.returncode})", file=sys.stderr)
                return 1
        elif proc.returncode != 0:
            reported.append(spec.id)
    print("\n=== scaffold-fix 汇总")
    print(f"  已重生 (机器修好, 记得 git add): {', '.join(regenerated) or '无'}")
    print(f"  仍需人改 (机器只能报清单)    : {', '.join(reported) or '无'}")
    print("  scaffold 门不阻断提交；这里是它们的批量收口入口。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--names", metavar="GROUP",
                    help="打印该分组的门名 (空格分隔; 供 safe_commit 消费)")
    ap.add_argument("--table", action="store_true", help="人读的分组表")
    ap.add_argument("--check", action="store_true",
                    help="登记表与 classify_commit_tier / safe_commit.sh 门名对账")
    ap.add_argument("--run-system-health", action="store_true",
                    help="跑 system_health 组运行时自检 (daily_update 之外的手动入口)")
    ap.add_argument("--scaffold-fix", action="store_true", help="scaffold 组批量修")
    ap.add_argument("--date", default="manual", help="报告文件名用的日期标签")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    if args.names:
        return _print_names(args.names)
    if args.check:
        return _check()
    if args.run_system_health:
        return _run_system_health(args.date, args.json_out)
    if args.scaffold_fix:
        return _scaffold_fix()
    return _print_table()


if __name__ == "__main__":
    raise SystemExit(main())
