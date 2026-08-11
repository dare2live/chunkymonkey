"""L2 现查入口的契约 (goal.md P2.2)。

锁三件事：
1. **零文件** —— 现查就是现查，不许偷偷落盘或读缓存；
2. **诚实降级** —— 每一段要么给数据要么给 `unavailable` + reason，不许用 0/空冒充；
3. **报事实不做裁决** —— 退出码恒 0，红绿仍归 continuity / SLA / cutover 各自的门。

离线可跑：有库时走真实查询，无库时每段应降级成 unavailable —— 两种情况契约相同。
"""
from __future__ import annotations

from pathlib import Path

from services import project_status as ps


REPO = Path(__file__).resolve().parents[3]
SECTIONS = ("calendar", "accepted_frontier", "source_watermarks", "cutovers", "gates", "alerts")


def _is_typed(section: object) -> bool:
    """每段要么 ok/有裁决字段，要么 unavailable 带 reason —— 不许含糊。"""
    if not isinstance(section, dict):
        return False
    if section.get("status") == "unavailable":
        return bool(section.get("reason"))
    return bool(section.get("status") or section.get("overall"))


def test_every_section_is_typed_or_honestly_unavailable() -> None:
    status = ps.collect_status()
    assert status["kind"] == "project_status"
    for name in SECTIONS:
        assert name in status, f"缺段 {name}"
        assert _is_typed(status[name]), f"{name} 既没给数据也没诚实标 unavailable: {status[name]}"


def test_collect_writes_no_files(tmp_path, monkeypatch) -> None:
    """L2 契约 = 命令现查、零文件。落盘就等于又造了一份会烂的状态。"""
    before = {p for p in (REPO / "data").rglob("*") if p.is_file()} if (REPO / "data").is_dir() else set()
    ps.collect_status()
    after = {p for p in (REPO / "data").rglob("*") if p.is_file()} if (REPO / "data").is_dir() else set()
    assert after == before, f"现查入口落了盘: {sorted(str(p) for p in (after - before))[:5]}"


def test_main_exit_code_is_always_zero(capsys) -> None:
    """状态命令不是门。给它退出码语义 = 又造一套与 continuity/SLA 并行的裁决。"""
    assert ps.main([]) == 0
    assert ps.main(["--json"]) == 0
    assert "project_status" in capsys.readouterr().out


def test_lag_is_counted_in_trading_days_not_calendar_days() -> None:
    """滞后必须按交易日；按自然日会把周末算进去，制造假紧迫。"""
    frontier = ps.collect_status()["accepted_frontier"]
    if frontier.get("status") != "ok":
        import pytest

        pytest.skip(f"库不可达: {frontier.get('reason')}")
    dated = [d for d in frontier["datasets"] if d["frontier_is_date"]]
    assert dated, "至少应有一个日期轴数据集"
    anchor = frontier["anchor_trade_date"]
    for d in dated:
        lag = d["lag_trading_days"]
        if lag is None:
            continue
        assert lag >= 0
        # 交易日数必然 <= 自然日数；相等只在完全无休市日的短区间成立。
        natural = (
            int(anchor[:4]) * 372 + int(anchor[4:6]) * 31 + int(anchor[6:])
        ) - (
            int(d["frontier"][:4]) * 372 + int(d["frontier"][4:6]) * 31 + int(d["frontier"][6:])
        )
        assert lag <= max(natural, 0) + 1, f"{d['dataset_id']} 滞后 {lag} 超过自然日跨度"


def test_unavailable_helper_never_pretends_success() -> None:
    out = ps._unavailable("db_gone")
    assert out == {"status": "unavailable", "reason": "db_gone"}
    assert not _is_typed({"status": "unavailable"}), "unavailable 缺 reason 应视为不合格"


def test_render_text_surfaces_unavailable_reasons() -> None:
    """人读渲染不能把 unavailable 渲染成空白 —— 那等于把「查不了」显示成「没问题」。"""
    fake = {
        "kind": "project_status",
        "generated_at": "2026-08-11T00:00:00Z",
        "contract": "x",
        "calendar": ps._unavailable("calendar_unreachable:OSError"),
        "accepted_frontier": ps._unavailable("no_database_reachable:{}"),
        "source_watermarks": ps._unavailable("smartmoney_unreachable:OSError"),
        "cutovers": ps._unavailable("cutover_check_failed:X"),
        "gates": ps._unavailable("gate_registry_unavailable:Y"),
        "alerts": ps._unavailable("flag_dir_unreadable:Z"),
    }
    text = ps.render_text(fake)
    for reason in (
        "calendar_unreachable",
        "no_database_reachable",
        "smartmoney_unreachable",
        "cutover_check_failed",
        "gate_registry_unavailable",
        "flag_dir_unreadable",
    ):
        assert reason in text, f"渲染吞掉了 {reason}"


def test_date_like_and_calendar_bounds() -> None:
    assert ps._is_date_like("20260804")
    assert not ps._is_date_like("trade_cal:SSE:19901219_20261231")
    assert not ps._is_date_like("")
    assert ps._calendar_bounds("20260804") == "2026-08-04"
    assert ps._calendar_bounds("trade_cal:SSE") == "trade_cal:SSE"
