#!/usr/bin/env python3
"""Commit-msg 自检: 查 **Q / Fix / Evidence / Residual 四段结构**, 不查关键词。

owner: docs/engineering_governance.md §14 + AGENTS.md §9。

**为什么从关键词改成结构** (2026-08-11, goal.md P3.1):
旧版匹配 `sharpe|calmar|max_dd|walk-forward|实测…` 这类关键词组。两个问题:
1. **清单必然烂** —— 词表是某个时点的产物, 项目换了词就失效, 而没人会记得回来改;
2. **贴个词就能过** —— 它检验的是「有没有出现某个字符串」, 不是「有没有说清楚」。

结构检查换了个问法: **这四件事你说了吗** ——
  `Q:` 为什么做这一刀(问题/触发)  ·  `Fix:` 做了什么
  `Evidence:` 凭什么说它成立(实测)  ·  `Residual:` 什么没做完 / 留给谁

**它仍然是自述型的, 这一点必须说清楚。** 作者完全可以写 `Evidence: 我觉得可以` 然后过关。
所以它**不是验证, 是清单** —— 价值在于逼作者当场回答这四问, 尤其是最后一问(留了什么坑),
那是最容易被略过、也最伤下一个接手的人的一问。正因为验证不了, 它属 scaffold 组 **warn-only**;
真正的安全由读代码与数据的门提供, 与措辞无关(见 eng_gov §14.1)。

**唯一仍阻断的**: subject 短于 10 字符 —— 那是客观事实, 不是自述。

Bypass: message 体内写 `# commit-msg: minimal`(revert / 紧急修复用; 常规刀不该用)。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

EXEMPT_BODY_MARKERS = ("# commit-msg: minimal", "commit-msg: minimal")
MIN_SUBJECT_LEN = 10

# 四段结构。允许中英文、允许粗体/括号装饰, 只要求它作为一行的起头出现。
# `\**` 出现在名字**两侧**: `**证据**（实测）：` 这种写法里粗体标记跟在名字后面,
# 首版只在前面允许装饰, 于是中文粗体小节名一律漏判 (自带测试当场抓到)。
_DECOR = r"\s*\**\s*"
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("Q", rf"^{_DECOR}(Q|问题|背景){_DECOR}[:：]", "为什么做这一刀 —— 触发它的问题是什么"),
    ("Fix", rf"^{_DECOR}(Fix|修法|做法|改动){_DECOR}[:：]", "做了什么"),
    ("Evidence", rf"^{_DECOR}(Evidence|证据|实测){_DECOR}[\(（:：]", "凭什么说它成立 —— 实测命令与结果"),
    ("Residual", rf"^{_DECOR}(Residual|残留|遗留){_DECOR}[\(（:：]", "什么没做完 / 留给谁 —— 最容易略过的一问"),
)


def missing_sections(body: str) -> list[tuple[str, str]]:
    out = []
    for name, pattern, why in SECTIONS:
        if not re.search(pattern, body, re.M | re.I):
            out.append((name, why))
    return out


def main(msg_path: str) -> int:
    msg = Path(msg_path).read_text(encoding="utf-8")
    if any(m in msg for m in EXEMPT_BODY_MARKERS):
        return 0

    lines = [ln for ln in msg.splitlines() if not ln.lstrip().startswith("#")]
    body = "\n".join(lines)

    subject = next((ln for ln in lines if ln.strip()), "").strip()
    if len(subject) < MIN_SUBJECT_LEN:
        print(
            f"ERROR: commit subject 只有 {len(subject)} 字符 (<{MIN_SUBJECT_LEN})。"
            "写清楚做了什么 —— 长度是客观事实, 这一条阻断。",
            file=sys.stderr,
        )
        return 1

    missing = missing_sections(body)
    if not missing:
        return 0

    print("=" * 78, file=sys.stderr)
    print("NOTE: commit message 缺以下段落（**不阻断**）:", file=sys.stderr)
    for name, why in missing:
        print(f"  - {name}: {why}", file=sys.stderr)
    print(
        "\n本检查只看你说没说, 说不了真假 —— 它是清单不是验证。"
        "\n真正的安全来自读代码与数据的门 (eng_gov §14.1); 措辞影响不了它们。",
        file=sys.stderr,
    )
    print("=" * 78, file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: check_commit_message.py <COMMIT_MSG_FILE>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
