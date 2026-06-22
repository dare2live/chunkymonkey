#!/usr/bin/env python3
"""chunkyctl — 项目操作 CLI (2026-06-22 **最小重建 doctor**)。

背景: reset commit 639e0dfb ("代码层reset到地基删771") 删掉了原 1660 行 chunkyctl.py 连同其依赖
(moth_snapshot / worktree_status / audit_docs_graph / data_lineage/registry + 5/7 个 audit 脚本),
`scripts/chunkyctl doctor` 因此悬空报 "can't open file"。
本最小版只串 **reset 后幸存且实测可跑** 的健康检查, **不复活已删模块** (尊重用户主导的 reset 意图):
  1. tooling_gate  — `moth assert` (二进制; 原 moth_snapshot.py 已删, 改直调 CLI)
  2. alert_flags   — 巡检 /tmp/chunkymonkey_ALERT_*.flag (定时任务失败/降级)
  3. universe      — check_universe_filter.py --all (排除股写入门)
  4. data_health   — data_health_snapshot.py --dry-run (表新鲜度/红黄绿)
其余子命令 (worktree/docs/preflight/audit/data-status) 依赖已删 → 报 retired (当前权威: goal.md / moth / PROJECT_INDEX)。
完整 doctor 全套 = 部分推翻 reset (需恢复 moth_snapshot 等), 须用户决策后再做。owner=主会话 (analysis 调研 a7649a94)。
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run_command(cmd: list[str], *, cwd: Path) -> dict[str, Any]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, check=False)
    except FileNotFoundError as exc:                       # 工具不在 PATH (如 moth 未装) → 不崩, 报缺
        return {"cmd": cmd, "returncode": 127, "stdout": "", "stderr": f"command not found: {exc}"}
    return {"cmd": cmd, "returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr}


def _json_from_stdout(result: dict[str, Any]) -> dict[str, Any] | None:
    try:
        parsed = json.loads(result.get("stdout") or "")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    s: dict[str, Any] = {"cmd": result["cmd"], "returncode": result["returncode"]}
    if result.get("stderr"):
        s["stderr_tail"] = result["stderr"][-800:]
    return s


def _aggregate_verdict(sections: list[dict[str, Any]]) -> str:
    """FAIL 优先 > WARN > PASS (沿用删前 chunkyctl 聚合口径)。"""
    saw_warn = False
    for sec in sections:
        v, rc = sec.get("verdict"), sec.get("returncode")
        if v == "FAIL" or (rc is not None and rc != 0 and v not in ("WARN", "PASS")):
            return "FAIL"
        if v == "WARN":
            saw_warn = True
    return "WARN" if saw_warn else "PASS"


def collect_alert_flags() -> dict[str, Any]:
    """定时任务失败/降级 flag 巡检 — 把 "启动查 /tmp/chunkymonkey_ALERT_*.flag" 约定变成代码 (删前原样)。"""
    flags = []
    for path in sorted(glob.glob("/tmp/chunkymonkey_ALERT_*.flag")):
        p = Path(path)
        try:
            tail = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
        except OSError:
            tail = []
        flags.append({"flag": p.name, "last_lines": tail})
    return {"verdict": "PASS" if not flags else "WARN", "count": len(flags), "flags": flags}


def _moth_gate(repo: Path) -> dict[str, Any]:
    """tooling gate = moth assert (claims-vs-reality)。moth 未装 → WARN (不崩 doctor)。"""
    r = _run_command(["moth", "assert", "--repo", "."], cwd=repo)
    if r["returncode"] == 127:
        return {"name": "tooling_gate", "verdict": "WARN", "note": "moth 不在 PATH (装 moth 后启用)", **_summary(r)}
    verdict = "PASS" if r["returncode"] == 0 else "FAIL"
    for line in (r["stdout"] or "").splitlines():
        if "verdict=" in line:
            tok = line.split("verdict=", 1)[1].split()[0].strip().upper()
            if tok in ("PASS", "WARN", "FAIL"):
                verdict = tok
            break
    return {"name": "tooling_gate", "verdict": verdict, "returncode": r["returncode"]}


def run_doctor(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    sections: list[dict[str, Any]] = []
    sections.append(_moth_gate(repo))                                          # 1. moth tooling gate
    af = collect_alert_flags()                                                 # 2. 告警 flag 巡检
    sections.append({"name": "alert_flags", "verdict": af["verdict"], "count": af["count"], "flags": af["flags"]})
    uni = _run_command([sys.executable, "backend/scripts/check_universe_filter.py", "--all"], cwd=repo)  # 3. universe
    sections.append({"name": "universe", "verdict": "PASS" if uni["returncode"] == 0 else "FAIL", **_summary(uni)})
    dh = _run_command([sys.executable, "backend/scripts/data_health_snapshot.py",                # 4. 数据新鲜度
                       "--dry-run", "--format", "json"], cwd=repo)
    dh_json = _json_from_stdout(dh)
    sections.append({"name": "data_health",
                     "verdict": (dh_json or {}).get("verdict", "PASS" if dh["returncode"] == 0 else "FAIL"),
                     "summary": (dh_json or {}).get("summary") or (dh_json or {}).get("severity_counts"),
                     "returncode": dh["returncode"]})
    verdict = _aggregate_verdict(sections)
    report = {"command": "doctor", "verdict": verdict, "sections": sections,
              "note": "最小重建版 (reset 后幸存 4 gate: moth/alert/universe/data_health); "
                      "完整 doctor 需恢复 moth_snapshot/worktree_status 等已删模块 (见文件头注)"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if verdict != "FAIL" else 1


_RETIRED = ("worktree", "docs", "preflight", "audit", "data-status")


def main() -> int:
    parser = argparse.ArgumentParser(prog="chunkyctl", description="项目操作 CLI (2026-06-22 最小重建 doctor)")
    sub = parser.add_subparsers(dest="command")
    d = sub.add_parser("doctor", help="健康检查 (moth/alert/universe/data_health)")
    d.add_argument("--repo", default=".")
    d.add_argument("--fast", action="store_true")              # wrapper 已 strip; 防直调残留不崩
    d.add_argument("--skip-storage-payload", action="store_true")
    d.add_argument("--fail-on-dirty-worktree", action="store_true")
    for name in _RETIRED:
        sub.add_parser(name)
    args, _unknown = parser.parse_known_args()
    if args.command == "doctor":
        return run_doctor(args)
    if args.command in _RETIRED:
        print(json.dumps({"command": args.command, "status": "retired",
                          "note": f"{args.command} 依赖在 reset commit 639e0dfb 被删, 未重建 "
                                  f"(当前权威: goal.md / moth assert / PROJECT_INDEX)"}, ensure_ascii=False))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
