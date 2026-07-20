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
