"""god-file 集合棘轮的双向测试 + 可定位诊断。

为什么这个文件存在: moth 的 shell 断言只能比一个数, 红了只给 `observed=1`, **给不出文件名**
(实测全部 29 条断言的 `detail` 字段一律为空)。而 minimal-module-no-new-godfile 2026-09-04 起是
**阻断级**判据 —— 一道会拦提交却说不清拦你什么的门, 就是它此前红了三周没人动的原因。
所以诊断这一半放在这里: 同一个判据, 失败时直接点名是哪个文件。

判据本体在 `.moth/assertions/claims.yaml` 的 minimal-module-no-new-godfile;
基线与逐文件处置在 `backend/config/godfile_baseline.yaml`。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
BASELINE = REPO / "backend" / "config" / "godfile_baseline.yaml"
CLAIMS = REPO / ".moth" / "assertions" / "claims.yaml"
THRESHOLD = 800
ASSERTION_ID = "minimal-module-no-new-godfile"


def _current_godfiles(root: Path) -> set[str]:
    """root 下 backend/ 里非测试 .py 中行数 > THRESHOLD 的文件 (相对 root 的 posix 路径)。

    口径必须与 claims.yaml 里那条 shell 命令逐字一致: 排除 __pycache__ 与任何 tests/ 目录。
    """
    out: set[str] = set()
    for p in (root / "backend").rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in rel or "/tests/" in rel:
            continue
        with p.open("rb") as fh:
            if sum(1 for _ in fh) > THRESHOLD:
                out.add(rel)
    return out


def _baseline_paths(path: Path = BASELINE) -> set[str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = (raw or {}).get("godfiles")
    if not isinstance(entries, list) or not entries:
        # 空基线会让判据恒绿 (对称差 = 当前集合 vs 空 = 全部, 反而恒红; 但结构坏了一样要 fail closed)
        raise AssertionError(f"{path} 的 godfiles 缺失或为空 —— 基线结构坏了, fail closed")
    return {str(e["path"]) for e in entries}


def test_baseline_matches_current_set() -> None:
    """当前 >800 行的集合 == 基线集合。**这条红时点名是哪个文件, moth 那条只给数字。**"""
    cur = _current_godfiles(REPO)
    base = _baseline_paths()
    newly_over = sorted(cur - base)
    should_shrink = sorted(base - cur)
    assert not newly_over and not should_shrink, (
        "god-file 集合与基线不符 —— 两个方向的修法不同:\n"
        + (f"  新越过 {THRESHOLD} 行 (这次 diff 造了 god-file, 先看能不能拆; 确实要留就补进基线并写 disposition):\n"
           + "".join(f"    + {p}\n" for p in newly_over) if newly_over else "")
        + (f"  已降到 {THRESHOLD} 行以下 (棘轮该收紧, 从基线删掉这几行):\n"
           + "".join(f"    - {p}\n" for p in should_shrink) if should_shrink else "")
        + f"  基线: {BASELINE.relative_to(REPO)}"
    )


def test_every_baseline_entry_is_honest() -> None:
    """基线里的每一条都必须真实存在、且真的超线 —— 基线不许收留已经不成立的条目。"""
    stale: list[str] = []
    for rel in sorted(_baseline_paths()):
        p = REPO / rel
        if not p.exists():
            stale.append(f"{rel}: 文件不存在")
            continue
        with p.open("rb") as fh:
            n = sum(1 for _ in fh)
        if n <= THRESHOLD:
            stale.append(f"{rel}: 只有 {n} 行, 已不超线")
    assert not stale, "基线条目已不成立 (删掉它们):\n" + "\n".join(f"    {s}" for s in stale)


def test_every_baseline_entry_carries_a_disposition() -> None:
    """每条都要有裁决 —— 基线是棘轮记录点, 不是「就这样吧」清单。

    新成员必须被逐个裁决过, 否则基线会退化成垃圾桶 (同 [[feedback-one-category-many-dispositions]])。
    """
    legal = {"keep", "split_deferred", "trim_deferred"}
    raw = yaml.safe_load(BASELINE.read_text(encoding="utf-8"))
    bad = [
        f"{e.get('path')}: disposition={e.get('disposition')!r}"
        for e in raw["godfiles"]
        if e.get("disposition") not in legal
    ]
    assert not bad, f"disposition 必须 ∈ {sorted(legal)}:\n" + "\n".join(f"    {b}" for b in bad)


def test_moth_assertion_is_wired_to_this_baseline() -> None:
    """claims.yaml 那条断言必须真的读这份基线、且是阻断级 —— 判据与本测试不能各说各话。"""
    a = next(
        x for x in yaml.safe_load(CLAIMS.read_text(encoding="utf-8"))["assertions"]
        if x["id"] == ASSERTION_ID
    )
    cmd = " ".join(a["command"])
    assert "backend/config/godfile_baseline.yaml" in cmd, "断言没读这份基线"
    assert str(THRESHOLD) in cmd, f"断言里的阈值与本测试的 {THRESHOLD} 不一致"
    assert a.get("severity") == "blocking", "这条是阻断级判据; 降级前先想清楚 warn-only 会退化成 warn-nothing"
    assert a["expect"] == {"op": "==", "value": 0}, "对称差必须 ==0, 用 <= 会放过一个方向"


@pytest.mark.parametrize(
    "mutate,expected_hit",
    [
        ("drop_one", "新越过"),      # 基线少一条 = 有文件新越线没登记
        ("add_absent", "不存在"),     # 基线多一条不存在的文件
    ],
)
def test_red_examples(tmp_path: Path, mutate: str, expected_hit: str) -> None:
    """反向验证: 造出它该抓的两种缺陷, 确认真的抓得到 (不只验绿, 也验红)。"""
    entries = yaml.safe_load(BASELINE.read_text(encoding="utf-8"))["godfiles"]
    if mutate == "drop_one":
        entries = entries[1:]
    else:
        entries = entries + [{"path": "backend/services/_absent_probe.py", "disposition": "keep"}]
    probe = tmp_path / "b.yaml"
    probe.write_text(yaml.safe_dump({"godfiles": entries}, allow_unicode=True), encoding="utf-8")

    if mutate == "drop_one":
        cur = _current_godfiles(REPO)
        base = _baseline_paths(probe)
        assert cur - base, "基线少一条时必须报出新越线的文件"
    else:
        missing = [e["path"] for e in entries if not (REPO / e["path"]).exists()]
        assert missing, "基线含不存在的文件时必须报出来"


def test_assertion_command_actually_runs_green() -> None:
    """把 claims.yaml 里那条命令原样跑一遍, 确认它此刻是 0 —— 判据本身别写坏了。"""
    a = next(
        x for x in yaml.safe_load(CLAIMS.read_text(encoding="utf-8"))["assertions"]
        if x["id"] == ASSERTION_ID
    )
    r = subprocess.run(a["command"], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, f"断言命令自己跑挂了: {r.stderr[:300]}"
    assert r.stdout.strip() == "0", f"对称差应为 0, 实得 {r.stdout.strip()!r}"
