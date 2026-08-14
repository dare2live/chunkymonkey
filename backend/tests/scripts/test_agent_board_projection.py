"""WP2: generated agent board projection + drift gate."""
from __future__ import annotations

import json
from pathlib import Path

from scripts import agent_board_projection as board


REPO = Path(__file__).resolve().parents[3]


def _strip_snapshot(md: str) -> str:
    """剔除现查时刻行 —— 它每次都不同, 不属于「投影内容是否稳定」的判定面。"""
    return "\n".join(
        ln for ln in md.splitlines() if not ln.startswith(board.SNAPSHOT_PREFIX)
    )


def test_collect_cutovers_reflects_live_yaml() -> None:
    """collect() must mirror the live yaml gates exactly (no silent
    override in either direction) — owner opt-in 2026-07-20 flipped both
    gates true; this must not hardcode a stale false expectation."""
    data = board.collect(REPO)
    assert data["enforcement"] == "projection_only_not_truth"
    tier12_yaml = board._load_yaml(REPO / "backend" / "config" / "tier12_publish.yaml")
    expected_tier12 = bool((tier12_yaml.get("consumer_cutover") or {}).get("cutover_allowed", False))
    assert "b_pit_mart" not in data["cutovers"], "已退役的 b_pit cutover 面不得复活"
    assert data["cutovers"]["tier12_consumer"]["cutover_allowed"] == expected_tier12


def _write_legacy_false_repo(tmp_path: Path) -> Path:
    """Minimal fixture repo with both cutover gates explicit false — pins
    the LEGACY (pre-opt-in) path independent of live repo cutover state."""
    cfg = tmp_path / "backend" / "config"
    cfg.mkdir(parents=True)
    (cfg / "tier12_publish.yaml").write_text(
        "consumer_cutover:\n  cutover_allowed: false\n", encoding="utf-8"
    )
    return tmp_path


def test_collect_cutovers_legacy_false_fixture(tmp_path: Path) -> None:
    fixture_repo = _write_legacy_false_repo(tmp_path)
    data = board.collect(fixture_repo)
    assert data["enforcement"] == "projection_only_not_truth"
    assert data["cutovers"]["tier12_consumer"]["cutover_allowed"] is False


def test_phase_e_ladder_projected() -> None:
    data = board.collect(REPO)
    blocks = {row["block"]: row for row in data["phase_e"]["ladder"]}
    assert blocks["b0"]["verdict"] == "reject"
    assert blocks["b0"]["claimable"] is False
    assert blocks["b4"]["verdict"] == "inconclusive"
    assert data["phase_e"]["any_claimable"] is False
    assert data["phase_e"]["strategy_release"] is False
def test_board_stable_across_wall_clock_day_rollover(monkeypatch) -> None:
    """跨天不得产生漂移，否则 agent_board 门会每天堵死所有提交。

    2026-08-10 独立审查抓到的 BLOCKING 缺陷：初版把 wall-clock 当 `as_of` 渲染进
    BOARD.md 正文与 agent_context.json，而两处漂移比较面都只排除顶层
    `generated_at`，于是日期一滚动就必红 —— 与本文件宣称的幂等直接矛盾。
    现改为 `expected_window_end + 1` 的固定探针 + `window_lapsed` 布尔。
    """
    import datetime as _dt

    base = _dt.datetime(2026, 8, 10, 12, 0)

    class _FrozenDT(_dt.datetime):
        offset_days = 0

        @classmethod
        def now(cls, tz=None):  # noqa: D102
            stamp = base + _dt.timedelta(days=cls.offset_days)
            return stamp.replace(tzinfo=tz) if tz else stamp

    monkeypatch.setattr(board.dt, "datetime", _FrozenDT)

    _FrozenDT.offset_days = 0
    d0 = board.collect(REPO)
    md0 = _strip_snapshot(board.render_md(d0))
    _FrozenDT.offset_days = 1
    d1 = board.collect(REPO)
    md1 = _strip_snapshot(board.render_md(d1))

    assert md0 == md1, "投影正文跨天漂移 → boot 每天读到不同的板"
    j0 = {k: v for k, v in d0.items() if k != "generated_at"}
    j1 = {k: v for k, v in d1.items() if k != "generated_at"}
    assert j0 == j1, "投影对象跨天漂移 → 同一状态被渲染成两种说法"

def test_shadow_deadline_is_computed_not_hardcoded() -> None:
    """影子期到期必须由起点 + 上限算出，不是写死的状态串。

    回退防线：原实现把 `wp6` 写死为 `POLICY_FIXED_shadow_open`、deadline 写死为
    `10_sessions_or_14d_first`，于是 eng_gov §13 的 14 天上限（2026-08-03）过后，
    看板仍自称 open。本测试自行计算期望值，不写死任何日期结论，故不会随时间失效。
    """
    import datetime as _dt

    t = board.collect(REPO)["track"]
    assert isinstance(t.get("shadow_expired"), bool)
    deadline = (_dt.date(2026, 7, 20) + _dt.timedelta(days=14)).isoformat()
    assert deadline in t["shadow_deadline"], t["shadow_deadline"]
    expected = _dt.datetime.now(_dt.timezone.utc).date() > _dt.date.fromisoformat(deadline)
    assert t["shadow_expired"] is expected
    assert ("EXPIRED" in t["wp6"]) is expected
    assert ("EXPIRED" in t["agent_os"]) is expected


def test_c_accept_row_parity() -> None:
    acc = board.collect(REPO)["cutovers"]["tier12_consumer"]["accept"]
    assert acc["decision_date"] == "20260717"
    assert acc["stock_row_count"] == 4989
    assert acc["universe_membership_size"] == 4989
    assert acc["published"] is True


def test_phase_d_run_projected() -> None:
    data = board.collect(REPO)
    summary = data["phase_d"]["summary"]
    assert summary["claimable"] is False
    assert summary["strategy_release"] is False
    assert summary["fold_protocol"] == "purged_walk_forward"
    assert summary["n_folds"] == 3
    assert summary["fold_ids"] == [
        "purged_fold_0",
        "purged_fold_1",
        "purged_fold_2",
    ]


def test_render_marks_generated_and_non_enforcement() -> None:
    md = board.render_md(board.collect(REPO))
    assert "Projection only" in md
    assert "现查" in md, "必须标明是现查投影而非落盘文件"
    assert "chunkyctl status" in md, "前沿类问题必须被指向 L2 现查入口"


def test_render_marks_legacy_false_fixture(tmp_path: Path) -> None:
    """render_md must faithfully print whatever collect() resolved — pinned
    via the LEGACY false fixture so this is independent of live cutover state."""
    fixture_repo = _write_legacy_false_repo(tmp_path)
    md = board.render_md(board.collect(fixture_repo))
    assert "cutover_allowed=False" in md or "cutover_allowed=false" in md


def test_projection_writes_no_files(tmp_path: Path, monkeypatch, capsys) -> None:
    """P2.3 契约: 投影只打印, 绝不落盘。落盘就是又造一份会烂的 L2 状态。"""
    import scripts.agent_board_projection as mod

    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    assert mod.main(["--root", str(REPO)]) == 0
    assert mod.main(["--json", "--root", str(REPO)]) == 0
    assert set(tmp_path.rglob("*")) == before, "投影落盘了"
    assert "BOARD" in capsys.readouterr().out


def test_no_write_surface_remains() -> None:
    """写盘/--check 能力必须真删干净, 不留半退役的死路径。"""
    import scripts.agent_board_projection as mod

    for attr in ("MD_OUT", "JSON_OUT", "write_outputs", "_body"):
        assert not hasattr(mod, attr), f"{attr} 应随 P2.3 一起退役"
