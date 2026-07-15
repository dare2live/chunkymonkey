#!/usr/bin/env python3
"""chunkyctl — 项目操作 CLI (2026-06-22 **最小重建 doctor**)。

背景: reset commit 639e0dfb ("代码层reset到地基删771") 删掉了原 1660 行 chunkyctl.py 连同其依赖
(moth_snapshot / worktree_status / audit_docs_graph / data_lineage/registry + 5/7 个 audit 脚本),
`scripts/chunkyctl doctor` 因此悬空报 "can't open file"。
本最小版只串 **reset 后幸存且实测可跑** 的健康检查, **不复活已删模块** (尊重用户主导的 reset 意图):
  1. tooling_gate  — `moth assert` (二进制; 原 moth_snapshot.py 已删, 改直调 CLI)
  2. automation_surface — manual-only 下阻断数据写 cron/launchd 残留
  3. alert_flags   — 巡检 /tmp/chunkymonkey_ALERT_*.flag (手动任务失败/降级)
  4. universe      — check_universe_filter.py --all (排除股写入门)
  5. data_health   — data_health_snapshot.py --dry-run (表新鲜度/红黄绿)
其余子命令 (worktree/docs/preflight/audit/data-status) 依赖已删 → 报 retired (当前权威: goal.md / moth / PROJECT_INDEX)。
完整 doctor 全套 = 部分推翻 reset (需恢复 moth_snapshot 等), 须用户决策后再做。owner=主会话 (analysis 调研 a7649a94)。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


_CRONTAB_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*=.*")
_CRON_ENV_REFERENCE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


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
    """手动任务失败/降级 flag 巡检 — 把启动检查约定变成代码。"""
    flags = []
    for path in sorted(glob.glob("/tmp/chunkymonkey_ALERT_*.flag")):
        p = Path(path)
        try:
            tail = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
        except OSError:
            tail = []
        flags.append({"flag": p.name, "last_lines": tail})
    return {"verdict": "PASS" if not flags else "WARN", "count": len(flags), "flags": flags}


def _scheduled_plist(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("StartCalendarInterval")
        or payload.get("StartInterval")
        or payload.get("KeepAlive")
        or payload.get("RunAtLoad") is True
        or payload.get("WatchPaths")
        or payload.get("QueueDirectories")
        or payload.get("StartOnMount") is True
        or payload.get("LaunchEvents")
    )


def _expand_cron_environment(command: str, env: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return env.get(name, match.group(0))

    return _CRON_ENV_REFERENCE.sub(_replace, command)


def _scan_cron_text(
    text: str,
    *,
    source: str,
    repo: Path,
    patterns: list[re.Pattern[str]],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    env = {key: value for key, value in os.environ.items() if isinstance(value, str)}
    for line_no, line in enumerate(text.splitlines(), 1):
        command = line.strip()
        if not command or command.startswith("#"):
            continue
        if _CRONTAB_ENV_ASSIGNMENT.fullmatch(command):
            name, value = command.split("=", 1)
            env[name.strip()] = value.strip().strip("'\"")
            continue
        expanded = _expand_cron_environment(command, env)
        reason = _automation_match_reason(expanded, repo, patterns)
        if reason:
            findings.append({"source": f"{source}:{line_no}", "reason": reason})
    return findings


def _automation_match_reason(
    text: str, repo: Path, patterns: list[re.Pattern[str]]
) -> str | None:
    lowered = text.lower()
    if "chunkymonkey" in lowered:
        return "project identity: chunkymonkey"
    if str(repo) in text:
        return f"project identity: {repo}"
    matched = next((pattern.pattern for pattern in patterns if pattern.search(text)), None)
    return f"matched {matched}" if matched else None


def audit_automation_surface(
    repo: Path,
    *,
    home: Path | None = None,
    system_launchd_dirs: tuple[Path, ...] | None = None,
    system_cron_files: tuple[Path, ...] | None = None,
    crontab_text: str | None = None,
    launchctl_text: str | None = None,
) -> dict[str, Any]:
    """Enforce the config-owned manual-only contract across cron and launchd."""
    policy_path = repo / "backend" / "config" / "automation_policy.yaml"
    try:
        policy = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
        data_jobs = policy["data_jobs"]
        mode = data_jobs["mode"]
        if policy.get("version") != 1:
            raise ValueError(f"unsupported policy version: {policy.get('version')!r}")
        if mode != "manual_only":
            raise ValueError(f"unsupported mode: {mode!r}")
        raw_patterns = data_jobs["forbidden_command_patterns"]
        if not isinstance(raw_patterns, list) or not raw_patterns:
            raise ValueError("forbidden_command_patterns must be a non-empty list")
        patterns = [re.compile(str(p)) for p in raw_patterns]
    except Exception as exc:  # noqa: BLE001 - missing/bad policy cannot silently permit automation
        return {
            "name": "automation_surface",
            "verdict": "FAIL",
            "mode": "unknown",
            "findings": [{"source": str(policy_path), "reason": f"invalid policy: {exc}"}],
        }

    findings: list[dict[str, str]] = []
    home = home or Path.home()
    if system_launchd_dirs is None:
        system_launchd_dirs = (Path("/Library/LaunchAgents"), Path("/Library/LaunchDaemons"))
    plist_sources = [
        ("repo", repo / "configs" / "launchd"),
        ("repo", repo / "backend" / "scripts" / "launchd"),
        ("installed", home / "Library" / "LaunchAgents"),
    ]
    plist_sources.extend(("system", path) for path in system_launchd_dirs)
    for source_kind, directory in plist_sources:
        if not directory.exists():
            continue
        try:
            plist_paths = sorted(directory.glob("*.plist"))
        except OSError as exc:
            findings.append({
                "source": f"{source_kind}:{directory}",
                "reason": f"cannot audit directory: {type(exc).__name__}",
            })
            continue
        for path in plist_paths:
            try:
                payload = plistlib.loads(path.read_bytes())
            except Exception as exc:  # noqa: BLE001
                if source_kind == "repo" or "chunkymonkey" in path.name.lower():
                    findings.append(
                        {"source": f"{source_kind}:{path}", "reason": f"unparseable plist: {exc}"}
                    )
                continue
            if not _scheduled_plist(payload):
                continue
            if source_kind == "repo":
                findings.append(
                    {
                        "source": f"repo:{path.relative_to(repo)}",
                        "reason": "scheduled plist forbidden by manual_only",
                    }
                )
                continue
            command = " ".join(
                [
                    str(payload.get("Label") or ""),
                    str(payload.get("Program") or ""),
                    *map(str, payload.get("ProgramArguments") or []),
                ]
            )
            plist_env = {
                **{key: value for key, value in os.environ.items() if isinstance(value, str)},
                **{
                    str(key): str(value)
                    for key, value in (payload.get("EnvironmentVariables") or {}).items()
                },
            }
            command = _expand_cron_environment(command, plist_env)
            reason = _automation_match_reason(command, repo, patterns)
            if reason:
                display = path.relative_to(repo) if source_kind == "repo" else path
                findings.append(
                    {"source": f"{source_kind}:{display}", "reason": reason}
                )

    if crontab_text is None:
        cron_result = _run_command(["crontab", "-l"], cwd=repo)
        crontab_text = cron_result.get("stdout") or ""
        cron_error = (cron_result.get("stderr") or "").lower()
        if cron_result.get("returncode") not in (0, 1) or (
            cron_result.get("returncode") == 1 and "no crontab" not in cron_error
        ):
            findings.append({
                "source": "crontab",
                "reason": f"audit command failed rc={cron_result.get('returncode')}",
            })
    findings.extend(_scan_cron_text(
        crontab_text, source="crontab", repo=repo, patterns=patterns
    ))

    if system_cron_files is None:
        cron_d = Path("/etc/cron.d")
        discovered: list[Path] = [Path("/etc/crontab")]
        if cron_d.exists():
            try:
                discovered.extend(sorted(path for path in cron_d.iterdir() if path.is_file()))
            except OSError as exc:
                findings.append({
                    "source": f"system-cron:{cron_d}",
                    "reason": f"cannot audit directory: {type(exc).__name__}",
                })
        system_cron_files = tuple(discovered)
    for cron_path in system_cron_files:
        if not cron_path.exists():
            continue
        try:
            cron_text = cron_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append({
                "source": f"system-cron:{cron_path}",
                "reason": f"cannot audit: {type(exc).__name__}",
            })
            continue
        findings.extend(_scan_cron_text(
            cron_text, source=f"system-cron:{cron_path}", repo=repo, patterns=patterns
        ))

    if launchctl_text is None:
        launch_result = _run_command(["launchctl", "list"], cwd=repo)
        launchctl_text = launch_result.get("stdout") or ""
        if launch_result.get("returncode") != 0:
            findings.append({
                "source": "launchctl",
                "reason": f"audit command failed rc={launch_result.get('returncode')}",
            })
    for line in launchctl_text.splitlines():
        reason = _automation_match_reason(line, repo, patterns)
        if reason:
            findings.append({"source": "launchctl", "reason": reason})

    verdict = "FAIL" if mode == "manual_only" and findings else "PASS"
    return {
        "name": "automation_surface",
        "verdict": verdict,
        "mode": mode,
        "findings": findings,
    }


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
    sections.append(audit_automation_surface(repo))                            # 2. manual-only 调度面
    af = collect_alert_flags()                                                 # 3. 告警 flag 巡检
    sections.append({"name": "alert_flags", "verdict": af["verdict"], "count": af["count"], "flags": af["flags"]})
    uni = _run_command([sys.executable, "backend/scripts/check_universe_filter.py", "--all"], cwd=repo)  # 4. universe
    sections.append({"name": "universe", "verdict": "PASS" if uni["returncode"] == 0 else "FAIL", **_summary(uni)})
    dh = _run_command([sys.executable, "backend/scripts/data_health_snapshot.py",                # 5. 数据新鲜度
                       "--dry-run", "--format", "json"], cwd=repo)
    dh_json = _json_from_stdout(dh)
    sections.append({"name": "data_health",
                     "verdict": (dh_json or {}).get("verdict", "PASS" if dh["returncode"] == 0 else "FAIL"),
                     "summary": (dh_json or {}).get("summary") or (dh_json or {}).get("severity_counts"),
                     "returncode": dh["returncode"]})
    verdict = _aggregate_verdict(sections)
    report = {"command": "doctor", "verdict": verdict, "sections": sections,
              "note": "最小重建版 (5 gate: moth/automation/alert/universe/data_health); "
                      "完整 doctor 需恢复 moth_snapshot/worktree_status 等已删模块 (见文件头注)"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if verdict != "FAIL" else 1


_RETIRED = ("worktree", "docs", "preflight", "audit", "data-status", "jobs")
# jobs 2026-07-07 全库死代码普查补入: services.experiment_jobs 随 2026-06-28 重建物删后
# jobs 子命令从未重实现也未加入 _RETIRED, 撞了就是裸 argparse "invalid choice" 报错而非本
# 模块统一的 retired JSON 提示, 见 analysis/dead_code_sweep_20260707.md 簇10。


def main() -> int:
    parser = argparse.ArgumentParser(prog="chunkyctl", description="项目操作 CLI (2026-06-22 最小重建 doctor)")
    sub = parser.add_subparsers(dest="command")
    d = sub.add_parser("doctor", help="健康检查 (moth/automation/alert/universe/data_health)")
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
