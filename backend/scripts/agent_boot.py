#!/usr/bin/env python3
"""One-page agent boot context (agent-OS WP3).

`scripts/chunkyctl agent-boot` aggregates the session-start evidence an agent
needs on boot — git status summary, Moth snapshot summary, CodeGraph index
status, and the live board projection (现查, 无落盘文件) — into one
page of text or JSON, so a fresh agent does not have to ingest the full Moth
snapshot or replay four commands by hand.

Contract:
  - read-only; never mutates git/Moth/CodeGraph/board state;
  - projection only — not an enforcement input (resolvers/yaml stay truth);
  - fail-closed reporting: a tool that is missing or returns malformed output
    is reported as ``unavailable``/``error``, never silently upgraded to ok;
    any ``error`` section makes the process exit non-zero.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
BOARD_REGEN = "PYTHONPATH=backend python backend/scripts/agent_board_projection.py"

Runner = Callable[[list[str]], dict[str, Any]]


def _run_command(cmd: list[str], *, cwd: Path) -> dict[str, Any]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, check=False)
    except FileNotFoundError as exc:
        return {"cmd": cmd, "returncode": 127, "stdout": "", "stderr": f"command not found: {exc}"}
    except OSError as exc:
        return {"cmd": cmd, "returncode": 126, "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}"}
    return {"cmd": cmd, "returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr}


def git_summary(run: Runner) -> dict[str, Any]:
    """Parse ``git status --porcelain=v2 --branch`` into branch + dirty counts."""
    r = run(["git", "status", "--porcelain=v2", "--branch"])
    if r["returncode"] != 0:
        return {"name": "git", "status": "error",
                "error": (r.get("stderr") or "git status failed").strip()[:300]}
    branch = upstream = None
    ahead = behind = 0
    changed: list[str] = []
    untracked: list[str] = []
    for line in (r["stdout"] or "").splitlines():
        if line.startswith("# branch.head "):
            branch = line.split(" ", 2)[2]
        elif line.startswith("# branch.upstream "):
            upstream = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            parts = line.split()
            ahead, behind = int(parts[2]), abs(int(parts[3]))
        elif line.startswith("1 "):
            # porcelain v2: 1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
            changed.append(line.split(" ", 8)[8])
        elif line.startswith("2 "):
            # 2 ... <X><score> <path>\t<origPath> — keep the current path
            changed.append(line.split(" ", 9)[9].split("\t")[0])
        elif line.startswith("? "):
            untracked.append(line[2:])
    dirty = changed + untracked
    return {
        "name": "git",
        "status": "ok" if not dirty and ahead == 0 and behind == 0 else "warn",
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "changed_count": len(changed),
        "untracked_count": len(untracked),
        "dirty_head": dirty[:10],
    }


def moth_summary(run: Runner) -> dict[str, Any]:
    """Compress a full moth snapshot into status + warnings + assertion counts."""
    r = run(["moth", "snapshot", "--repo", "."])
    if r["returncode"] == 127:
        return {"name": "moth", "status": "unavailable",
                "note": "moth 不在 PATH; 装 moth 后重跑 agent-boot"}
    try:
        snap = json.loads(r["stdout"] or "")
        if not isinstance(snap, dict):
            raise ValueError("snapshot is not a JSON object")
        moth_status = snap["status"]
        if moth_status not in ("PASS", "WARN", "FAIL"):
            raise ValueError(f"invalid moth status {moth_status!r}")
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"name": "moth", "status": "error",
                "error": f"moth snapshot output invalid: {exc}",
                "returncode": r["returncode"]}
    packs = []
    for pack in (snap.get("assertions") or {}).get("packs") or []:
        if isinstance(pack, dict):
            packs.append({k: pack.get(k) for k in ("name", "pass", "fail", "error")})
    dirty = snap.get("dirty_worktree")
    # moth FAIL/WARN are reported facts (warn); only parse/tool failure is error.
    return {
        "name": "moth",
        "status": {"PASS": "ok", "WARN": "warn", "FAIL": "warn"}[moth_status],
        "moth_status": moth_status,
        "warnings": [str(w) for w in snap.get("warnings") or []][:5],
        "issues": [str(i) for i in snap.get("issues") or []][:5],
        "assertion_packs": packs,
        "dirty_worktree": dirty if isinstance(dirty, (int, list, dict)) else None,
    }


def codegraph_summary(run: Runner) -> dict[str, Any]:
    r = run(["codegraph", "status", "."])
    if r["returncode"] == 127:
        return {"name": "codegraph", "status": "unavailable",
                "note": "codegraph 不在 PATH"}
    if r["returncode"] != 0:
        return {"name": "codegraph", "status": "error",
                "error": (r.get("stderr") or r.get("stdout") or "codegraph status failed").strip()[-300:]}
    out = r["stdout"] or ""
    fresh = "up to date" in out.lower()
    return {
        "name": "codegraph",
        "status": "ok" if fresh else "warn",
        "index_fresh": fresh,
        "note": "index up to date" if fresh else "index stale — run `codegraph sync .`",
    }


def board_summary(repo: Path) -> dict[str, Any]:
    """现查投影 (P2.3)：不再读 `data/board/agent_context.json` 那份落盘副本。

    此前 boot 读文件、文件靠一道漂移门保证不烂 —— 于是 agent 每次 session 实际读到的
    是「上次谁记得重生成」的快照。collect() 只读 config/lineage/goal.md，实测 0.3s
    且不连库，现查比维护「文件 + 保证文件不烂的门」便宜得多。
    """
    try:
        from scripts.agent_board_projection import collect as board_collect

        board = board_collect(repo)
        track = board["track"]
        cutovers = board["cutovers"]
        missing = [k for k, ok in (board.get("inputs_present") or {}).items() if not ok]
        if missing:
            # 投影能跑通不等于投影可信：config 缺失时它退化成一份全缺省的空板。
            return {"name": "board", "status": "error",
                    "error": f"board projection inputs missing: {missing}",
                    "fix": BOARD_REGEN}
    except Exception as exc:  # noqa: BLE001 — 投影失败必须显式报错，不许静默空板
        return {"name": "board", "status": "error",
                "error": f"board projection failed: {type(exc).__name__}: {exc}",
                "fix": BOARD_REGEN}
    phase_e = board.get("phase_e") or {}
    return {
        "name": "board",
        "status": "ok",
        "generated_at": board.get("generated_at"),
        "track": track,
        "cutover_allowed": {
            "tier12_consumer": (cutovers.get("tier12_consumer") or {}).get("cutover_allowed"),
        },
        "phase_e_overall": phase_e.get("overall_status"),
        "bans": board.get("bans") or [],
        "next_knives_frozen": board.get("next_knives_frozen") or [],
        "goal_hand_excerpt": board.get("goal_hand_excerpt") or "",
    }


def collect(repo: Path = REPO, run: Runner | None = None) -> dict[str, Any]:
    runner: Runner = run or (lambda cmd: _run_command(cmd, cwd=repo))
    sections = {
        "git": git_summary(runner),
        "moth": moth_summary(runner),
        "codegraph": codegraph_summary(runner),
        "board": board_summary(repo),
    }
    statuses = {s["status"] for s in sections.values()}
    overall = (
        "error" if "error" in statuses
        else "warn" if statuses & {"warn", "unavailable"}
        else "ok"
    )
    return {
        "command": "agent-boot",
        "overall": overall,
        "enforcement": "projection_only_not_truth",
        "sections": sections,
        "read_next": [
            "goal.md (hand-written objective/裁决/禁令/下一步)",
            "scripts/chunkyctl status (L2 运行时状态现查: 前沿/滞后/水位/cutover/告警)",
            "docs/README.md → 按任务读唯一 owner 文档",
            "history: `chunkyctl history --grep <term>` / `--eras` (git 即原件)",
        ],
        # Thin §15 reminder (projection only; not a gate).
        "delivery": [
            "knife-merge: one logical knife = one Rule10 + one safe_commit",
            "async CI: push then continue — no sync `gh run watch`",
            "L3 pre-knife: `chunkyctl pre-knife <name>` (moth+codegraph once)",
            "parallel agents only when moth proves non-overlap; never loosen L3/PIT/≤40d",
        ],
    }


def render_text(d: dict[str, Any]) -> str:
    s = d["sections"]
    L: list[str] = []
    add = L.append
    add(f"# agent-boot — one-page boot context (overall={d['overall']}; projection only)")
    g = s["git"]
    add("")
    add("## git")
    if g["status"] == "error":
        add(f"- ERROR: {g['error']}")
    else:
        track = f"{g['branch']}...{g['upstream']}" if g.get("upstream") else str(g["branch"])
        add(f"- {track} ahead={g['ahead']} behind={g['behind']} "
            f"changed={g['changed_count']} untracked={g['untracked_count']}")
        for f in g.get("dirty_head") or []:
            add(f"  - {f}")
    m = s["moth"]
    add("")
    add("## moth")
    if m["status"] in ("error", "unavailable"):
        add(f"- {m['status'].upper()}: {m.get('error') or m.get('note') or 'unknown'}")
    else:
        add(f"- status={m['moth_status']}")
        for pack in m.get("assertion_packs") or []:
            add(f"- assertions `{pack.get('name')}`: pass={pack.get('pass')} "
                f"fail={pack.get('fail')} error={pack.get('error')}")
        for w in m.get("warnings") or []:
            add(f"- warn: {w}")
        for i in m.get("issues") or []:
            add(f"- issue: {i}")
    c = s["codegraph"]
    add("")
    add("## codegraph")
    add(f"- {c.get('note') or c.get('error')}" if c["status"] != "error"
        else f"- ERROR: {c.get('error')}")
    b = s["board"]
    add("")
    add("## board")
    if b["status"] == "error":
        add(f"- ERROR: {b['error']}")
        add(f"- fix: {b['fix']}")
    else:
        t = b["track"]
        add(f"- snapshot {b.get('generated_at')} | track `{t.get('name')}` | A→H `{t.get('a_to_h')}`")
        ca = b["cutover_allowed"]
        add(f"- cutover_allowed (yaml 意图): "
            f"tier12_consumer={ca.get('tier12_consumer')} | phase_e={b.get('phase_e_overall')}")
        for ban in b.get("bans") or []:
            add(f"- ban: {ban}")
        for item in b.get("next_knives_frozen") or []:
            add(f"- next: {item}")
    add("")
    add("## delivery (§15 knife-merge)")
    for item in d.get("delivery") or []:
        add(f"- {item}")
    add("")
    add("## read next")
    for item in d["read_next"]:
        add(f"- {item}")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chunkyctl agent-boot", description=__doc__)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--repo", default=str(REPO))
    args = parser.parse_args(argv)
    data = collect(Path(args.repo).expanduser().resolve())
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render_text(data))
    return 1 if data["overall"] == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
