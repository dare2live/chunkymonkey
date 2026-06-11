"""concept events detector 单测 — 相邻快照 diff 集合逻辑 (PIT/真相源契约)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_concept_events.py"
SPEC = importlib.util.spec_from_file_location("build_concept_events", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


def _by_day():
    return {
        "20260108": {"C1": {"600000", "600001"}},                    # 基线
        "20260109": {"C1": {"600000", "600001", "600002"}},          # +600002 入 C1
        "20260110": {"C1": {"600000", "600002"}, "C2": {"300001"}},  # 600001 出 C1, C2 诞生
        "20260111": {"C2": {"300001"}},                              # C1 消失
    }


def test_member_add_and_drop_detected():
    ev = mod._diff_events(_by_day(), "observed", "dc", after_day=None)
    adds = {(e[0], e[2], e[4]) for e in ev if e[3] == mod.ADD}
    drops = {(e[0], e[2], e[4]) for e in ev if e[3] == mod.DROP}
    assert ("20260109", "C1", "600002") in adds
    assert ("20260110", "C1", "600001") in drops


def test_concept_born_and_dead_detected():
    ev = mod._diff_events(_by_day(), "observed", "dc", after_day=None)
    born = {(e[0], e[2]) for e in ev if e[3] == mod.BORN}
    dead = {(e[0], e[2]) for e in ev if e[3] == mod.DEAD}
    assert ("20260110", "C2") in born   # C2 首现
    assert ("20260111", "C1") in dead   # C1 消失
    # 概念诞生/消失事件 con_code 必为 None
    assert all(e[4] is None for e in ev if e[3] in (mod.BORN, mod.DEAD))


def test_event_date_is_later_snapshot_not_earlier():
    """变更首次可观测日 = 后一个快照日 (PIT: 不能用变更生效前的日期)."""
    ev = mod._diff_events(_by_day(), "observed", "dc", after_day=None)
    add_602 = [e for e in ev if e[3] == mod.ADD and e[4] == "600002"][0]
    assert add_602[0] == "20260109"  # 不是 20260108


def test_incremental_after_day_filters_old_events():
    """增量模式: after_day 之前的事件不重产 (watermark 续传)."""
    ev = mod._diff_events(_by_day(), "observed", "dc", after_day="20260109")
    assert all(e[0] > "20260109" for e in ev)
    assert any(e[0] == "20260110" for e in ev)


def test_as_of_mode_tagged_on_every_event():
    """每条事件必带 as_of_mode (observed/reconstructed 区分 PIT 强弱)."""
    ev = mod._diff_events(_by_day(), "reconstructed", "dc", after_day=None)
    assert ev and all(e[5] == "reconstructed" for e in ev)


def test_single_day_yields_no_events():
    """不足两个快照日无法 diff (诚实返回空, 不臆造)."""
    assert mod._diff_events({"20260108": {"C1": {"600000"}}}, "observed", "dc", None) == []
