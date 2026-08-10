"""WP2: generated agent board projection + drift gate."""
from __future__ import annotations

import json
from pathlib import Path

from scripts import build_agent_board as board


REPO = Path(__file__).resolve().parents[3]


def test_collect_cutovers_reflects_live_yaml() -> None:
    """collect() must mirror the live yaml gates exactly (no silent
    override in either direction) — owner opt-in 2026-07-20 flipped both
    gates true; this must not hardcode a stale false expectation."""
    data = board.collect(REPO)
    assert data["enforcement"] == "projection_only_not_truth"
    b_pit_yaml = board._load_yaml(REPO / "backend" / "config" / "b_pit_mart_cutover.yaml")
    tier12_yaml = board._load_yaml(REPO / "backend" / "config" / "tier12_publish.yaml")
    expected_b_pit = bool((b_pit_yaml.get("mart_cutover") or {}).get("cutover_allowed", False))
    expected_tier12 = bool((tier12_yaml.get("consumer_cutover") or {}).get("cutover_allowed", False))
    assert data["cutovers"]["b_pit_mart"]["cutover_allowed"] == expected_b_pit
    assert data["cutovers"]["tier12_consumer"]["cutover_allowed"] == expected_tier12


def _write_legacy_false_repo(tmp_path: Path) -> Path:
    """Minimal fixture repo with both cutover gates explicit false — pins
    the LEGACY (pre-opt-in) path independent of live repo cutover state."""
    cfg = tmp_path / "backend" / "config"
    cfg.mkdir(parents=True)
    (cfg / "b_pit_mart_cutover.yaml").write_text(
        "mart_cutover:\n  cutover_allowed: false\n", encoding="utf-8"
    )
    (cfg / "tier12_publish.yaml").write_text(
        "consumer_cutover:\n  cutover_allowed: false\n", encoding="utf-8"
    )
    return tmp_path


def test_collect_cutovers_legacy_false_fixture(tmp_path: Path) -> None:
    fixture_repo = _write_legacy_false_repo(tmp_path)
    data = board.collect(fixture_repo)
    assert data["enforcement"] == "projection_only_not_truth"
    assert data["cutovers"]["b_pit_mart"]["cutover_allowed"] is False
    assert data["cutovers"]["tier12_consumer"]["cutover_allowed"] is False


def test_phase_e_ladder_projected() -> None:
    data = board.collect(REPO)
    blocks = {row["block"]: row for row in data["phase_e"]["ladder"]}
    assert blocks["b0"]["verdict"] == "reject"
    assert blocks["b0"]["claimable"] is False
    assert blocks["b4"]["verdict"] == "inconclusive"
    assert data["phase_e"]["any_claimable"] is False
    assert data["phase_e"]["strategy_release"] is False


def test_b_pit_shadow_match_counts() -> None:
    shadow = board.collect(REPO)["cutovers"]["b_pit_mart"]["shadow"]
    assert shadow["match_day_count"] == 120
    assert shadow["diverge_day_count"] == 0
    assert shadow["ratios_match_all"] is True


def test_b_pit_effective_comes_from_resolver_not_yaml_flag() -> None:
    """投影必须给出 resolver 的实际裁决，不能只报 yaml 意图。

    2026-08-10 实测事故：yaml `cutover_allowed=true`，但 shadow 窗口末端
    `20260722` 已过，resolver 自 `20260723` 起每日 fail-closed 回 legacy_mart；
    BOARD 只读 yaml flag（`bool(b_pit_gate.get("cutover_allowed"))`），于是对一个
    已停用 13 个交易日的能力持续报绿。删掉 effective 或改回只读 yaml 即此测试红。
    """
    from services.b_pit_mart_cutover import resolve_b_pit_mart_production_read

    eff = board.collect(REPO)["cutovers"]["b_pit_mart"].get("effective")
    assert eff is not None, "b_pit_mart 必须带 effective（resolver 探针裁决）"
    assert len(eff["probe_day"]) == 8 and eff["probe_day"].isdigit()
    truth = resolve_b_pit_mart_production_read(
        eff["probe_day"],
        config_path=REPO / "backend" / "config" / "b_pit_mart_cutover.yaml",
    )
    assert eff["probe_status"] == truth.status
    assert eff["probe_cutover_allowed"] is bool(truth.cutover_allowed)
    assert eff["probe_source"] == truth.source
    assert isinstance(eff["window_lapsed"], bool)


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
    md0 = board._body(board.render_md(d0))
    _FrozenDT.offset_days = 1
    d1 = board.collect(REPO)
    md1 = board._body(board.render_md(d1))

    assert md0 == md1, "BOARD.md 正文跨天漂移 → agent_board 门次日必红"
    j0 = {k: v for k, v in d0.items() if k != "generated_at"}
    j1 = {k: v for k, v in d1.items() if k != "generated_at"}
    assert j0 == j1, "agent_context.json 跨天漂移 → agent_board 门次日必红"


def test_render_flags_yaml_vs_effective_divergence() -> None:
    """yaml 说 true 而 resolver 说 false 时，渲染必须显式标注背离并指向处置。"""
    data = board.collect(REPO)
    bp = data["cutovers"]["b_pit_mart"]
    text = board.render_md(data)
    assert "resolver 实际裁决" in text
    eff = bp["effective"]
    diverged = (
        bool(bp["cutover_allowed"])
        and bool(eff["window_lapsed"])
        and not bool(eff["probe_cutover_allowed"])
    )
    if diverged:
        assert "yaml 意图已不生效" in text
        assert eff["probe_status"] in text
        assert eff["probe_day"] in text
        assert "owner 裁决" in text
    else:
        assert "yaml 意图已不生效" not in text


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
    assert "勿手改" in md
    assert "Projection only" in md


def test_render_marks_legacy_false_fixture(tmp_path: Path) -> None:
    """render_md must faithfully print whatever collect() resolved — pinned
    via the LEGACY false fixture so this is independent of live cutover state."""
    fixture_repo = _write_legacy_false_repo(tmp_path)
    md = board.render_md(board.collect(fixture_repo))
    assert "cutover_allowed=False" in md or "cutover_allowed=false" in md


def test_check_fresh_after_write(tmp_path: Path) -> None:
    # Isolate outputs under tmp while reading live sources from REPO.
    import scripts.build_agent_board as mod

    old_md, old_json = mod.MD_OUT, mod.JSON_OUT
    try:
        mod.MD_OUT = tmp_path / "BOARD.md"
        mod.JSON_OUT = tmp_path / "data" / "board" / "agent_context.json"
        data = mod.collect(REPO)
        assert mod.write_outputs(data, check=False, quiet=True) == 0
        assert mod.write_outputs(data, check=True, quiet=True) == 0
        # Stale hand edit must turn red.
        mod.MD_OUT.write_text(mod.MD_OUT.read_text(encoding="utf-8") + "\nstale\n", encoding="utf-8")
        assert mod.write_outputs(data, check=True, quiet=True) == 1
    finally:
        mod.MD_OUT, mod.JSON_OUT = old_md, old_json


def test_json_roundtrip_keys() -> None:
    data = board.collect(REPO)
    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    again = json.loads(text)
    assert again["bans"]
    assert again["sources"]
