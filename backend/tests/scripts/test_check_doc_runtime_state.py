"""文档写死运行时状态对账门的边界 (goal.md P2.1)。

这道门的价值全在**假阳性与假阴性的平衡**上：
- 假阴性 = 又一次「两份手写文档互相矛盾且同时落后两周」而四门全绿；
- 假阳性 = 噪音门，人会学会无视它，等于没门（首版就踩了这个：近八成命中是文件名里的日期）。

所以两个方向都锁死，并用 live 仓库跑一次当回归锁。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts import check_doc_runtime_state as mod


REPO = Path(__file__).resolve().parents[3]


def _policy(tmp_path: Path, *, files, constants=(), exemptions=(), frontier=None) -> dict:
    body = {
        "version": 1,
        "scan_files": list(files),
        "global_constants": [{"value": v, "reason": "test"} for v in constants],
        "file_exemptions": [
            {"file": f, "value": v, "reason": "test"} for f, v in exemptions
        ],
    }
    if frontier:
        body["frontier_reference"] = {"dataset_id": frontier}
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return mod.load_policy(path)


def _doc(tmp_path: Path, name: str, text: str) -> None:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ── 假阴性方向：真正写死的状态必须被抓到 ──────────────────────────────
def test_hardcoded_frontier_is_flagged(tmp_path: Path) -> None:
    _doc(tmp_path, "goal.md", "accepted daily→`20260720` 已 cutover\n")
    report = mod.scan(_policy(tmp_path, files=["goal.md"]), repo=tmp_path)
    assert report["overall"] == "FAIL"
    assert [f["value"] for f in report["findings"]] == ["20260720"]


def test_stale_frontier_is_named_with_the_live_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "live_frontier", lambda policy: ("20260804", "test_source"))
    _doc(tmp_path, "goal.md", "至 `20260721`\n")
    policy = _policy(tmp_path, files=["goal.md"], constants=["20190102"])
    report = mod.scan(policy, repo=tmp_path)
    finding = report["findings"][0]
    assert finding["kind"] == "stale_frontier"
    assert "20260804" in finding["detail"], "必须把真相源当前值摆出来，否则人不知道差多少"


def test_unused_exemption_is_reported_so_the_allowlist_cannot_rot(tmp_path: Path) -> None:
    _doc(tmp_path, "goal.md", "没有日期\n")
    policy = _policy(tmp_path, files=["goal.md"], exemptions=[("goal.md", "20260720")])
    report = mod.scan(policy, repo=tmp_path)
    assert [f["kind"] for f in report["findings"]] == ["stale_exemption"]


def test_missing_scan_target_is_reported_not_skipped(tmp_path: Path) -> None:
    """政策声明要扫却扫不到 = 政策自己烂了，不能静默当通过。"""
    policy = _policy(tmp_path, files=["nope.md"])
    report = mod.scan(policy, repo=tmp_path)
    assert report["findings"][0]["kind"] == "missing_scan_target"


# ── 假阳性方向：这些不是状态，不许报 ──────────────────────────────────
def test_dates_inside_filenames_are_not_state(tmp_path: Path) -> None:
    """首版踩过的坑：`some_cleanup_20260723.md` 这类文件名是引用，不是状态声明。

    样例名不带 `analysis/` 前缀：这是**测试数据**，既不该跟着真实文件的存废走，
    也不该被 doc_drift 当成一条悬空的文档引用。
    """
    _doc(
        tmp_path,
        "goal.md",
        "见 `some_cleanup_20260723.md` 与 `some_plan_20260721.md`\n",
    )
    report = mod.scan(_policy(tmp_path, files=["goal.md"]), repo=tmp_path)
    assert report["overall"] == "PASS", report["findings"]


def test_hyphenated_historical_dates_are_not_state(tmp_path: Path) -> None:
    """本仓约定：历史叙述写 2026-07-24，运行时状态写紧凑格式。"""
    _doc(tmp_path, "goal.md", "2026-07-24 stk_holdernumber RESTORE\n")
    report = mod.scan(_policy(tmp_path, files=["goal.md"]), repo=tmp_path)
    assert report["overall"] == "PASS", report["findings"]


def test_global_constants_and_file_exemptions_silence_the_finding(tmp_path: Path) -> None:
    _doc(tmp_path, "goal.md", "起点 `20190102` / `20220104`；事件日 `20260717`\n")
    policy = _policy(
        tmp_path,
        files=["goal.md"],
        constants=["20190102", "20220104"],
        exemptions=[("goal.md", "20260717")],
    )
    assert mod.scan(policy, repo=tmp_path)["overall"] == "PASS"


def test_exemption_is_scoped_to_its_file(tmp_path: Path) -> None:
    """给 A 文件的豁免不能顺带放行 B 文件 —— 否则豁免会悄悄变成全局开关。"""
    _doc(tmp_path, "goal.md", "`20260717`\n")
    _doc(tmp_path, "AGENTS.md", "`20260717`\n")
    policy = _policy(
        tmp_path, files=["goal.md", "AGENTS.md"], exemptions=[("goal.md", "20260717")]
    )
    report = mod.scan(policy, repo=tmp_path)
    assert [f["file"] for f in report["findings"]] == ["AGENTS.md"]


# ── fail-closed 与 live 回归锁 ───────────────────────────────────────
def test_broken_policy_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("version: 2\n", encoding="utf-8")
    with pytest.raises(mod.PolicyError):
        mod.load_policy(bad)
    with pytest.raises(mod.PolicyError):
        mod.load_policy(tmp_path / "missing.yaml")
    assert mod.main(["--policy", str(bad)]) == 1


def test_exemption_without_reason_is_rejected(tmp_path: Path) -> None:
    """豁免必须写明理由 —— 无理由豁免就是静默放行。"""
    p = tmp_path / "p.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "scan_files": [],
                "global_constants": [],
                "file_exemptions": [{"file": "goal.md", "value": "20260720"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(mod.PolicyError, match="reason"):
        mod.load_policy(p)


def test_live_repo_has_no_undeclared_runtime_state() -> None:
    """回归锁：活文档一旦再被写进陈旧前沿/计数，这条立刻红。"""
    report = mod.scan(mod.load_policy(), repo=REPO)
    assert report["overall"] == "PASS", report["findings"]
