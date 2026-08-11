"""commit message 四段结构自检的边界 (goal.md P3.1)。

这道门**验证不了真假**（作者可以写 `Evidence: 我觉得可以`），所以它是清单不是验证 ——
测试要锁的正是这个定位：**缺段只提示不阻断**，唯一阻断的是 subject 长度这种客观事实。
如果哪天有人把缺段改成阻断，就等于用一道挡不住任何人的门去卡诚实的提交者，
这正是 2026-08-10 拆自述型门时裁掉的东西。
"""
from __future__ import annotations

from pathlib import Path

from scripts import check_commit_message as mod


def _msg(tmp_path: Path, text: str) -> str:
    p = tmp_path / "COMMIT_EDITMSG"
    p.write_text(text, encoding="utf-8")
    return str(p)


FULL = """feat(x): 一条结构完整的消息

Q: 为什么做这一刀。
Fix: 做了什么。
Evidence (实测): 1411 passed。
Residual: 还剩什么。
"""


def test_full_structure_passes_silently(tmp_path: Path, capsys) -> None:
    assert mod.main(_msg(tmp_path, FULL)) == 0
    assert capsys.readouterr().err == ""


def test_missing_sections_warn_but_never_block(tmp_path: Path, capsys) -> None:
    """缺段只提示 —— 它挡不住不写的人，只会卡住诚实的人。"""
    rc = mod.main(_msg(tmp_path, "feat(x): 只有主题和一段\n\nQ: 为什么\n"))
    err = capsys.readouterr().err
    assert rc == 0, "缺段必须不阻断"
    for name in ("Fix", "Evidence", "Residual"):
        assert name in err
    assert "不阻断" in err and "清单不是验证" in err


def test_short_subject_still_blocks(tmp_path: Path) -> None:
    """唯一阻断项：长度是客观事实，不是自述。"""
    assert mod.main(_msg(tmp_path, "x\n\nQ: a\nFix: b\nEvidence: c\nResidual: d\n")) == 1


def test_minimal_marker_bypasses(tmp_path: Path, capsys) -> None:
    assert mod.main(_msg(tmp_path, "revert: 紧急回滚上一刀\n\n# commit-msg: minimal\n")) == 0
    assert capsys.readouterr().err == ""


def test_chinese_and_decorated_headings_count(tmp_path: Path, capsys) -> None:
    """中文小节名与粗体装饰都算 —— 判据是「说没说」，不是「用没用英文冒号」。"""
    text = "fix(y): 中文小节也应识别\n\n**问题**：…\n**修法**：…\n**证据**（实测）：…\n**残留**：…\n"
    assert mod.main(_msg(tmp_path, text)) == 0
    assert capsys.readouterr().err == ""


def test_keyword_stuffing_no_longer_passes_as_structure(tmp_path: Path, capsys) -> None:
    """旧门贴个 `sharpe` 就能过；结构门要求真的分段回答。"""
    text = "perf(z): 塞满旧关键词但没有结构\n\nsharpe calmar max_dd walk-forward 实测 evidence backtest\n"
    assert mod.main(_msg(tmp_path, text)) == 0  # 仍不阻断
    err = capsys.readouterr().err
    assert all(n in err for n in ("Q", "Fix", "Evidence", "Residual")), "关键词堆砌不该被当成有结构"


def test_no_keyword_table_remains(tmp_path: Path) -> None:
    """词表必然烂：不许再在这里维护一张。"""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    # 只断言「不再有词表这个东西」，不断言那些词不出现在正文 ——
    # docstring 需要引用旧词来解释改了什么，那是说明不是判据。
    for gone in ("GROUP_A_KEYWORDS", "GROUP_B_KEYWORDS", "GROUP_C", "POST_FIX_TRIGGER_KEYWORDS"):
        assert gone not in src, f"{gone} 应随关键词门一起退役"
    assert not hasattr(mod, "GROUP_A_KEYWORDS")
