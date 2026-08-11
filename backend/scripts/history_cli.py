#!/usr/bin/env python3
"""history — 项目历史检索 (goal.md「治理体系重构」P3.2)。

**为什么它能取代 ledger** (2026-08-11 实测, 非推断):

* 169 条 ledger 条目里 58 条引了 commit hash, 逐条比对后 **commit message 是超集** ——
  抽样同一刀 (`16f5c370c`): ledger 记 4 条 Rule10 决定, commit message 记 6 条。
  ledger 是 commit 的中文缩写版, 信息**更少**。
* 「早期 commit 太薄所以只能靠 ledger」不成立: 2026-04~05 期 1133 个 commit 中位
  783 字符 / 20 行, 41% 带证据或残留结构; 反而比中期更长。
* ledger 唯一不能被 `git log` 直接替代的, 只有 **5 条时期叙事**(合计 1785 字符) ——
  它们不是原始记录, 是「这 1133 个 commit 该从哪看起」的导航。该导航已转成
  annotated git tag (`era/*`), 由本命令的 ``--eras`` 列出。

于是历史有两个面, 都在 git 里, 都不需要文件:
  逐刀细节 → ``git log --grep``   (本命令 ``--grep``)
  时期导航 → ``git tag -n99``     (本命令 ``--eras``)

ledger 必然滞后 (实证曾断档 77 个 commit 而 git 一条没丢), 而 git 永不断档 ——
因为它不是副本, 它就是原件。

用法:
    scripts/chunkyctl history --grep b_pit           # 关键词查逐刀细节
    scripts/chunkyctl history --grep cutover --full  # 带完整 message 正文
    scripts/chunkyctl history --eras                 # 时期导航
    scripts/chunkyctl history --since 2026-07-01 --until 2026-08-01
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_SEP = "\x01"
_REC = "\x02"


def _git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(REPO), capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "git failed").strip()[:300])
    return proc.stdout


def search(
    *,
    grep: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 40,
    full: bool = False,
) -> list[dict[str, str]]:
    """按关键词 / 时间窗查 commit。多个 --grep 之间是**或**关系 (git 默认)。"""
    args = ["log", "--all", f"--format=%h{_SEP}%ad{_SEP}%s{_SEP}%b{_REC}", "--date=short"]
    for pattern in grep or []:
        args += ["--grep", pattern]
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")
    args += ["-i", f"-{limit}"]

    out = _git(args)
    rows: list[dict[str, str]] = []
    for record in out.split(_REC):
        if _SEP not in record:
            continue
        parts = record.strip("\n").split(_SEP)
        if len(parts) < 3:
            continue
        body = parts[3] if len(parts) > 3 else ""
        rows.append(
            {
                "hash": parts[0],
                "date": parts[1],
                "subject": parts[2],
                "body": body.strip() if full else "",
            }
        )
    return rows


def eras() -> list[dict[str, str]]:
    """时期导航 = annotated tag。tag message 就是叙事本身, 不是指向文件的链接。"""
    out = _git(["tag", "-l", "-n99", "--sort=creatordate"])
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in out.splitlines():
        if line and not line.startswith(" ") and not line.startswith("\t"):
            name, _, rest = line.partition(" ")
            current = {"tag": name, "text": rest.strip()}
            rows.append(current)
        elif current is not None:
            current["text"] = (current["text"] + "\n" + line.strip()).strip()
    return rows


def _print_search(rows: list[dict[str, str]], *, full: bool) -> None:
    if not rows:
        print("无命中。放宽关键词, 或用 --eras 先看时期导航。")
        return
    for row in rows:
        print(f"{row['hash']}  {row['date']}  {row['subject']}")
        if full and row["body"]:
            for line in row["body"].splitlines():
                print(f"    {line}")
            print()
    print(f"\n[{len(rows)} 条; 逐刀细节在 commit message 本身 —— `git show <hash>` 看全文]")


def _print_eras(rows: list[dict[str, str]]) -> None:
    if not rows:
        print("无 annotated tag。时期导航尚未建立。")
        return
    for row in rows:
        print(f"## {row['tag']}")
        for line in row["text"].splitlines():
            print(f"   {line}")
        print()
    print(f"[{len(rows)} 个时期; 逐刀细节用 `--grep <关键词>`]")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="项目历史检索 (git 是唯一原件, 无独立文件)")
    ap.add_argument("--grep", action="append", metavar="PATTERN",
                    help="关键词 (可重复; 多个之间是或)")
    ap.add_argument("--eras", action="store_true", help="列时期导航 (annotated tag)")
    ap.add_argument("--since", help="起始日期 (YYYY-MM-DD)")
    ap.add_argument("--until", help="截止日期 (YYYY-MM-DD)")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--full", action="store_true", help="带 commit message 正文")
    args = ap.parse_args(argv)

    try:
        if args.eras:
            _print_eras(eras())
            return 0
        if not (args.grep or args.since or args.until):
            ap.error("给个 --grep 关键词, 或 --since/--until 时间窗, 或 --eras")
        rows = search(grep=args.grep, since=args.since, until=args.until,
                      limit=args.limit, full=args.full)
        # 历史有两个面: 逐刀细节在 commit, 时期叙事在 annotated tag。--grep 必须同时搜,
        # 否则查「地基 reset」这种只存在于时期叙事里的词会零命中 —— 实测踩到过。
        hit_eras = [
            e for e in eras()
            if any(p.lower() in (e["tag"] + "\n" + e["text"]).lower() for p in (args.grep or []))
        ] if args.grep else []
        if hit_eras:
            print("=== 时期导航命中 ===")
            _print_eras(hit_eras)
            print("=== 逐刀命中 ===")
        _print_search(rows, full=args.full)
    except RuntimeError as exc:
        print(f"[history] git 查询失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
