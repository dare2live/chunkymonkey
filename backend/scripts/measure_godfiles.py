"""god-file 集合的**唯一**计算点 —— moth 断言与 test_godfile_baseline 都调这里。

## 为什么从"总行数"改成"代码行数" (2026-09-06)

原判据数的是 ``wc -l``。实测这个口径与它想守的东西反号 —— 它惩罚"把理由写下来":

    文件                                  总行   代码行   注释/文档
    assignment_gap_recon.py               820     710       8
    check_holders_staging.py              829     494     171
    tdxhub.py                            1094     577     405
    market_pulse.py                      1597     848     178

按 ``wc -l``, 829 行的 check_holders_staging 比 820 行的 assignment_gap_recon 更像
god-file; 按实际代码量它少 216 行。tdxhub 排第五而代码只有 market_pulse 的 68%。
这个项目的纪律就是把证据写进注释和提交信息, 一道按总行数收紧的门是在与之对着干。

## 改口径会不会放宽

不会, 方向相反。**基线成员 = 完全没有上限**: 在册文件从 820 涨到 3000 行, 集合仍然
相等, 门照绿。所以基线越大, 门管的越少。换成代码行数后集合 11 → 4, 离开的 7 个不是
被放行, 是**重新获得了 800 行的天花板** —— 它们此前根本没有上限。

## 口径

代码行 = 用 ``tokenize`` 逐 token 归属行号后, 去掉纯空行、注释行、以及作为独立语句的
字符串字面量 (docstring) 所占的行。一行里既有代码又有尾随注释, 算代码行。
tokenize 失败 (语法错/编码怪) 时退化成"非空行数", 并且**不吞异常** —— 退化是为了
让判据仍然给出一个数, 不是为了让坏文件悄悄通过。
"""

from __future__ import annotations

import argparse
import io
import sys
import tokenize
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "backend" / "config" / "godfile_baseline.yaml"
THRESHOLD = 800

_TRIPLE_QUOTES = ('"""', "'''", 'r"""', "r'''", 'f"""', "f'''")


def code_line_count(source: str) -> int:
    """source 里的代码行数 (排除空行 / 注释 / docstring)。"""

    code: set[int] = set()
    non_code: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                non_code.add(tok.start[0])
            elif tok.type == tokenize.STRING and tok.line.lstrip().startswith(_TRIPLE_QUOTES):
                non_code.update(range(tok.start[0], tok.end[0] + 1))
            elif tok.type not in (
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENDMARKER,
            ):
                code.add(tok.start[0])
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError):
        # 退化: 非空行数。宁可高估也不漏判 —— 坏文件不该因为解析不了就通过。
        return sum(1 for line in source.splitlines() if line.strip())
    return len(code - non_code)


def is_member(rel_posix: str) -> bool:
    """backend 下的非测试 .py。口径与旧 shell 命令一致。"""

    return (
        rel_posix.endswith(".py")
        and "__pycache__" not in rel_posix
        and "/tests/" not in rel_posix
    )


def current_godfiles(root: Path = REPO, threshold: int = THRESHOLD) -> set[str]:
    out: set[str] = set()
    for path in sorted((root / "backend").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if not is_member(rel):
            continue
        if code_line_count(path.read_text(encoding="utf-8", errors="replace")) > threshold:
            out.add(rel)
    return out


def baseline_paths(path: Path = BASELINE) -> set[str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = (raw or {}).get("godfiles")
    if not isinstance(entries, list) or not entries:
        raise AssertionError(f"{path} 的 godfiles 缺失或为空 —— 基线结构坏了, fail closed")
    return {str(e["path"]) for e in entries}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symmetric-diff-count", action="store_true",
                    help="只印一个数: 当前集合与基线的对称差条数 (moth 断言用)")
    ap.add_argument("--list", action="store_true", help="印当前集合, 一行一个")
    ap.add_argument("--measure", metavar="PATH", help="印单个文件的代码行数与总行数")
    args = ap.parse_args(argv)

    if args.measure:
        src = Path(args.measure).read_text(encoding="utf-8", errors="replace")
        print(f"{code_line_count(src)}\t{len(src.splitlines())}\t{args.measure}")
        return 0
    cur = current_godfiles()
    if args.list:
        for p in sorted(cur):
            print(p)
        return 0
    if args.symmetric_diff_count:
        print(len(cur ^ baseline_paths()))
        return 0
    base = baseline_paths()
    for p in sorted(cur - base):
        print(f"新越线\t{p}")
    for p in sorted(base - cur):
        print(f"该从基线删\t{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
