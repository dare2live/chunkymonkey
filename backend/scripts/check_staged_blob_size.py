#!/usr/bin/env python3
"""大 blob 入库硬门 (2026-08-31 事故复盘, 防同类再犯).

事故: `.git` 达 3.9G (size-pack: 2.90 GiB), 根因是可重算的分析/实验产物被提交进版本库
(最大 41.1 MB parquet / 21.0 MB csv / 11.0 MB json)。历史对象不清 (清史代价远超省下的
磁盘), 已做的止血是把这些路径加 .gitignore + git rm --cached。本门堵住同类文件再次进来。

判断逻辑 (阈值 / 白名单) 全部数据化在 backend/config/repo_blob_policy.yaml, 本脚本不
hardcode 任何字节数/路径字面量。

检查范围: **只看已 staged 的新增/修改文件** (`git diff --cached --diff-filter=AM`) ——
删除/改名不查 (删大文件是在减负, 不该被这道门拦)。

取的是 **staged blob 的字节数** (`git cat-file -s $(git rev-parse :<path>)`), 不是
`os.path.getsize` 读工作树 —— 已 `git add` 之后又编辑工作树的场景下两者可能不同, 而
真正要进库的是 index 里那份内容。

白名单自清: repo_blob_policy.yaml 里配置的每条白名单路径, 若当前仓库已不存在, 报 stale
计入 warn (防豁免清单烂掉却无人发现)。

退出码: 有 fail (超 fail_bytes 且不在白名单) → 非 0; 只有 warn (超 warn_bytes 未超
fail_bytes, 或白名单 stale) → 0。

用法: PYTHONPATH=backend python backend/scripts/check_staged_blob_size.py
      [--repo <repo_root>] [--policy <repo_blob_policy.yaml>]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = REPO / "backend" / "config" / "repo_blob_policy.yaml"


class PolicyError(RuntimeError):
    """policy 文件缺失/不可解析/违反硬不变量 — 调用方必须 fail-closed."""


def load_policy(path: Path) -> dict[str, Any]:
    """读 + 校验 repo_blob_policy.yaml。任何不合法都抛 PolicyError, 不静默放行。"""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyError(f"{path} 不可读: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path} 不可解析: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"{path} 顶层必须是 mapping")

    fail_bytes = raw.get("fail_bytes")
    warn_bytes = raw.get("warn_bytes")
    if not isinstance(fail_bytes, int) or isinstance(fail_bytes, bool) or fail_bytes <= 0:
        raise PolicyError(f"{path} fail_bytes 缺失或非正整数: {fail_bytes!r}")
    if not isinstance(warn_bytes, int) or isinstance(warn_bytes, bool) or warn_bytes <= 0:
        raise PolicyError(f"{path} warn_bytes 缺失或非正整数: {warn_bytes!r}")
    if warn_bytes >= fail_bytes:
        raise PolicyError(
            f"{path} warn_bytes ({warn_bytes}) 必须严格小于 fail_bytes ({fail_bytes})"
        )

    whitelist: dict[str, str] = {}
    for entry in raw.get("whitelist") or []:
        if not isinstance(entry, dict) or "path" not in entry or "reason" not in entry:
            raise PolicyError(f"{path} whitelist 条目缺 path/reason 字段: {entry!r}")
        wpath = str(entry["path"])
        reason = str(entry["reason"]).strip()
        if not reason:
            raise PolicyError(f"{path} whitelist {wpath} 的 reason 不能为空")
        whitelist[wpath] = reason

    return {"fail_bytes": fail_bytes, "warn_bytes": warn_bytes, "whitelist": whitelist}


def staged_added_modified(repo: Path) -> list[str]:
    """已 staged 的新增/修改文件路径 (A/M 两类; 删除/改名不查)。"""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def staged_blob_size(repo: Path, path: str) -> int | None:
    """staged blob (git index 里的内容) 的字节数; 取不到返回 None (不误判为 FAIL)。

    刻意不用 os.path.getsize(工作树): `git add` 之后又编辑工作树是本项目已知反复出现的
    漂移场景 (staged_worktree_parity 门专门堵这个) —— 真正要进库的是 index 里那份内容,
    不是此刻磁盘上的内容, 两者在漂移场景下可能不同。
    """
    rev = subprocess.run(
        ["git", "rev-parse", f":{path}"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if rev.returncode != 0:
        return None
    sha = rev.stdout.strip()
    if not sha:
        return None
    size = subprocess.run(
        ["git", "cat-file", "-s", sha],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if size.returncode != 0:
        return None
    try:
        return int(size.stdout.strip())
    except ValueError:
        return None


def _human(n: int) -> str:
    return f"{n:,} bytes ({n / (1024 * 1024):.2f} MiB)"


def run(repo: Path, policy: dict[str, Any]) -> tuple[list[str], list[str]]:
    """返回 (fails, warns)。白名单自清与 staged 文件扫描相互独立, 都跑。"""
    fails: list[str] = []
    warns: list[str] = []
    whitelist: dict[str, str] = policy["whitelist"]
    fail_bytes: int = policy["fail_bytes"]
    warn_bytes: int = policy["warn_bytes"]

    for path in staged_added_modified(repo):
        if path in whitelist:
            continue  # 白名单全豁免 (fail 与 warn 都不查) — 已有 reason 记录审过的理由
        size = staged_blob_size(repo, path)
        if size is None:
            continue  # 取不到 staged blob (罕见: 特殊路径/子模块引用) — 不静默报FAIL, 也不误判
        if size > fail_bytes:
            fails.append(
                f"{path}: staged blob {_human(size)} > fail 阈值 {_human(fail_bytes)}. "
                "正解三选一: (1) 加进 .gitignore (若是可重算的分析/实验产物); "
                "(2) 加进 backend/config/repo_blob_policy.yaml whitelist 并写明 reason "
                "(若确需版本化, 如证据链依赖); (3) 改产出流程不让它入库 "
                "(写到已 gitignore 的目录, 如 data/phase5_exports/)。"
            )
        elif size > warn_bytes:
            warns.append(
                f"{path}: staged blob {_human(size)} > warn 阈值 {_human(warn_bytes)} "
                f"(未超 fail 阈值 {_human(fail_bytes)}, 未阻断; 留意它是否会继续变大)。"
            )

    for wpath, reason in whitelist.items():
        if not (repo / wpath).exists():
            warns.append(
                f"whitelist stale: {wpath} 在 repo_blob_policy.yaml 里配置 (reason={reason[:60]}…) "
                "但当前仓库已不存在此文件 — 删掉该条目, 防豁免清单烂掉。"
            )

    return fails, warns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(REPO), help="仓库根目录 (默认当前项目)")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY), help="repo_blob_policy.yaml 路径")
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    policy_path = Path(args.policy)

    try:
        policy = load_policy(policy_path)
    except PolicyError as exc:
        print(f"[repo-blob-size] FAIL: policy 不可用, fail-closed — {exc}")
        print("[repo-blob-size] verdict=FAIL fails=1 warns=0")
        return 1

    fails, warns = run(repo, policy)
    for w in warns:
        print(f"[WARN] {w}")
    for f in fails:
        print(f"[FAIL] {f}")
    verdict = "FAIL" if fails else "WARN" if warns else "PASS"
    print(f"[repo-blob-size] verdict={verdict} fails={len(fails)} warns={len(warns)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
