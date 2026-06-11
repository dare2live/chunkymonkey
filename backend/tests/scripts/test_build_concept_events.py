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


def test_field_direction_concept_is_ts_code_member_is_con_code(tmp_path, monkeypatch):
    """字段方向防回退 (Fable-5 复查抓的反向 bug): dc_member 真实形态
    ts_code=BK*.DC (概念板块) / con_code=股票代码 — 反向会把 5521 只股票当概念。
    用真实形态数据走 snapshot 路径, 断言概念键是 BK 形态。"""
    import pandas as pd

    day1 = tmp_path / "20260108"; day1.mkdir()
    day2 = tmp_path / "20260109"; day2.mkdir()
    df1 = pd.DataFrame({"trade_date": ["20260108"] * 2,
                        "ts_code": ["BK0145.DC", "BK0145.DC"],
                        "con_code": ["600503.SH", "603329.SH"]})
    df2 = pd.DataFrame({"trade_date": ["20260109"] * 3,
                        "ts_code": ["BK0145.DC"] * 2 + ["BK0999.DC"],
                        "con_code": ["600503.SH", "300008.SZ", "688213.SH"]})
    df1.to_parquet(day1 / "dc_member.parquet")
    df2.to_parquet(day2 / "dc_member.parquet")
    monkeypatch.setattr(mod, "_SNAPSHOT_ROOT", tmp_path)

    by_day = mod._membership_by_day_from_snapshots()
    assert set(by_day["20260108"]) == {"BK0145.DC"}, "概念键必须是 BK 板块代码, 不是股票"
    assert by_day["20260108"]["BK0145.DC"] == {"600503.SH", "603329.SH"}
    ev = mod._diff_events(by_day, "observed", "dc", None)
    born = [e for e in ev if e[3] == mod.BORN]
    assert [(e[2]) for e in born] == ["BK0999.DC"]  # 新概念 = 新板块, 不是新股票
    adds = [(e[2], e[4]) for e in ev if e[3] == mod.ADD]
    assert ("BK0145.DC", "300008.SZ") in adds
