"""check_tracked_allowlist.py 的机械锁 (2026-09-04 A2.1 新立)。

**必须自带 fixture, 不许依赖当前仓库的真实 git 状态** (feedback-test-must-carry-its-own-fixture)。
每个用例在 ``tmp_path`` 里建一个全新、自足的 git 仓库 (``git init`` + 配 user.email/user.name),
从不读本仓库真实的 tracked 文件集合。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import check_tracked_allowlist as gate


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _run(["git", "init", "-q", "-b", "main"], root)
    _run(["git", "config", "user.email", "t@t"], root)
    _run(["git", "config", "user.name", "t"], root)
    return root


def _add_file(repo: Path, rel: str, content: str = "x") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _run(["git", "add", rel], repo)


def _add_frozen_bestchoice(repo: Path) -> None:
    """把 BESTCHOICE_ALLOWLIST 里全部 11 个文件原样种进 bestchoice/ 并 git add。"""
    for rel in gate.BESTCHOICE_ALLOWLIST:
        _add_file(repo, f"bestchoice/{rel}")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path)


# ── 1. 恰好等于 7 份白名单 md + 冻结 bestchoice 清单 → 全绿 ──────────────
def test_exact_allowlist_passes(repo: Path) -> None:
    for rel in sorted(gate.MD_ALLOWLIST):
        _add_file(repo, rel)
    _add_frozen_bestchoice(repo)

    r = gate.audit(repo)
    assert r["overall"] == "PASS", r["fails"]
    assert r["extra_md"] == []
    assert r["extra_bestchoice"] == []
    assert r["missing_bestchoice"] == []

    rc = gate.main(["--root", str(repo), "--check"])
    assert rc == 0


# ── 2. 多一份跟踪 md → 红 ─────────────────────────────────────────────
def test_extra_tracked_markdown_fails(repo: Path) -> None:
    for rel in sorted(gate.MD_ALLOWLIST):
        _add_file(repo, rel)
    _add_frozen_bestchoice(repo)
    _add_file(repo, "notes/sneaky_new_doc.md")  # 白名单外新增一份 (注意: 不要用 "docs/" 前缀
    # —— 那会被死引用门当成源码里的真实路径引用, 误判成本仓自己的
    # 悬空引用, 本 fixture 字面量与真实 docs/ 语义无关, 换个不触发那条正则的前缀即可)

    r = gate.audit(repo)
    assert r["overall"] == "FAIL"
    assert "notes/sneaky_new_doc.md" in r["extra_md"]

    rc = gate.main(["--root", str(repo), "--check"])
    assert rc == 1


# ── 3. bestchoice/ 多一个文件 (冻结清单外) → 红 ──────────────────────────
def test_extra_bestchoice_file_fails(repo: Path) -> None:
    for rel in sorted(gate.MD_ALLOWLIST):
        _add_file(repo, rel)
    _add_frozen_bestchoice(repo)
    _add_file(repo, "bestchoice/sneaky_extra_file.py")

    r = gate.audit(repo)
    assert r["overall"] == "FAIL"
    assert "sneaky_extra_file.py" in r["extra_bestchoice"]

    rc = gate.main(["--root", str(repo), "--check"])
    assert rc == 1


# ── 4. bestchoice/ 少一个冻结清单文件 (残缺复活) → 红 ────────────────────
def test_missing_bestchoice_file_fails(repo: Path) -> None:
    for rel in sorted(gate.MD_ALLOWLIST):
        _add_file(repo, rel)
    for rel in gate.BESTCHOICE_ALLOWLIST:
        if rel == "execution_model.py":
            continue  # 故意漏掉一个 (注意: 不能选 FROZEN.md/README.md —— 两者也在 MD_ALLOWLIST 里, 会被上面那个循环重新种回去)
        _add_file(repo, f"bestchoice/{rel}")

    r = gate.audit(repo)
    assert r["overall"] == "FAIL"
    assert "execution_model.py" in r["missing_bestchoice"]


# ── 5. 空仓库 (无任何跟踪文件) → 绿 (⊆ 空集合永真; bestchoice 缺失才会红— ─
#      这里干脆不碰 bestchoice, 只证明"没有 md 越界"这一半判据不会误报) ──
def test_no_markdown_tracked_does_not_flag_extra(repo: Path) -> None:
    _add_file(repo, "backend/services/foo.py", "print(1)\n")

    r = gate.audit(repo)
    assert r["extra_md"] == []


# ── 6. 双向验证: 同一仓库先红后绿 —— 加了白名单外文件红, 拿掉就绿 ────────
def test_red_then_green_after_removing_extra_file(repo: Path) -> None:
    for rel in sorted(gate.MD_ALLOWLIST):
        _add_file(repo, rel)
    _add_frozen_bestchoice(repo)
    extra = repo / "SOME_EXTRA_DOC.md"
    extra.write_text("x", encoding="utf-8")
    _run(["git", "add", "SOME_EXTRA_DOC.md"], repo)

    assert gate.audit(repo)["overall"] == "FAIL"

    _run(["git", "rm", "-q", "-f", "SOME_EXTRA_DOC.md"], repo)
    assert gate.audit(repo)["overall"] == "PASS"
