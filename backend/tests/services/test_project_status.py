"""L2 现查入口的契约 (goal.md P2.2)。

锁三件事：
1. **零文件** —— 现查就是现查，不许偷偷落盘或读缓存；
2. **诚实降级** —— 每一段要么给数据要么给 `unavailable` + reason，不许用 0/空冒充；
3. **报事实不做裁决** —— 退出码恒 0，红绿仍归 continuity / SLA 各自的门。

离线可跑：有库时走真实查询，无库时每段应降级成 unavailable —— 两种情况契约相同。
"""
from __future__ import annotations

import pytest

from services import project_status as ps


SECTIONS = (
    "calendar", "accepted_frontier", "source_watermarks",
    "gates", "board", "alerts",
)


def _is_typed(section: object) -> bool:
    """每段要么 ok/有裁决字段，要么 unavailable 带 reason —— 不许含糊。"""
    if not isinstance(section, dict):
        return False
    if section.get("status") == "unavailable":
        return bool(section.get("reason"))
    return bool(section.get("status") or section.get("overall"))


@pytest.mark.live_db_readonly
def test_every_section_is_typed_or_honestly_unavailable() -> None:
    """live_db_readonly: collect_status 契约就是"有库查库/无库 unavailable", 两种情况
    同一断言 (test_isolation_r1.md §2.2B)。"""
    status = ps.collect_status()
    assert status["kind"] == "project_status"
    for name in SECTIONS:
        assert name in status, f"缺段 {name}"
        assert _is_typed(status[name]), f"{name} 既没给数据也没诚实标 unavailable: {status[name]}"


def test_collect_writes_no_files(tmp_path, tmp_manifest) -> None:
    """L2 契约 = 命令现查、零文件。落盘就等于又造了一份会烂的状态。

    不打 live_db_readonly 标记, 改用 tmp_manifest: 原判据 diff 整个 <repo>/data 树,
    2026-09-11 实证任何并发写 data/ 的进程 (交易所证据抓取脚本) 都能伪造它失败
    (test_isolation_r1.md §2.2B) —— 判据的实现比意图宽。这里改 diff tmp_path 树,
    并断言 accepted_frontier 确实探到了 smartmoney (证明走的是真路径, 不是降级)。
    """
    import duckdb

    ref_path = tmp_manifest.path_for("reference")
    ref_conn = duckdb.connect(str(ref_path))
    try:
        ref_conn.execute(
            "CREATE TABLE dim_trading_calendar (trade_date VARCHAR, is_trading BIGINT)"
        )
        ref_conn.executemany(
            "INSERT INTO dim_trading_calendar VALUES (?, 1)",
            [("2026-09-08",), ("2026-09-09",), ("2026-09-10",), ("2026-09-11",)],
        )
    finally:
        ref_conn.close()

    sm_path = tmp_manifest.path_for("smartmoney")
    sm_conn = duckdb.connect(str(sm_path))
    try:
        sm_conn.execute(
            "CREATE TABLE accepted_partition ("
            "dataset_id VARCHAR, partition_value VARCHAR, batch_id VARCHAR, "
            "contract_version VARCHAR, contract_hash VARCHAR, config_hash VARCHAR, "
            "row_count BIGINT, content_hash VARCHAR, observed_at TIMESTAMP, "
            "available_at TIMESTAMP, accepted_at TIMESTAMP)"
        )
        sm_conn.execute(
            "INSERT INTO accepted_partition VALUES "
            "('tier0.market_data.nominal_ohlcv_daily','20260910','b1','v1','ch1','cf1',"
            "100,'hh1',now(),now(),now())"
        )
        sm_conn.execute(
            "CREATE TABLE mart_data_source_watermark ("
            "data_domain VARCHAR, source_name VARCHAR, last_data_date VARCHAR, "
            "consecutive_failures INTEGER, fallback_active BOOLEAN, row_count BIGINT)"
        )
        sm_conn.execute(
            "INSERT INTO mart_data_source_watermark VALUES "
            "('sync:probe_daily','tdxhub','20260910',0,false,10)"
        )
    finally:
        sm_conn.close()

    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    status = ps.collect_status()
    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before, f"现查入口落了盘: {sorted(str(p) for p in (after - before))[:5]}"
    assert "smartmoney" in status["accepted_frontier"]["databases_probed"]


@pytest.mark.live_db_readonly
def test_main_exit_code_is_always_zero(capsys) -> None:
    """状态命令不是门。给它退出码语义 = 又造一套与 continuity/SLA 并行的裁决。"""
    assert ps.main([]) == 0
    assert ps.main(["--json"]) == 0
    assert "project_status" in capsys.readouterr().out


@pytest.mark.live_db_readonly
def test_lag_is_counted_in_trading_days_not_calendar_days() -> None:
    """滞后必须按交易日；按自然日会把周末算进去，制造假紧迫。"""
    frontier = ps.collect_status()["accepted_frontier"]
    if frontier.get("status") != "ok":
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
        "gates": ps._unavailable("gate_registry_unavailable:Y"),
        "board": ps._unavailable("board_projection_failed:W"),
        "alerts": ps._unavailable("flag_dir_unreadable:Z"),
    }
    text = ps.render_text(fake)
    for reason in (
        "calendar_unreachable",
        "no_database_reachable",
        "smartmoney_unreachable",
        "gate_registry_unavailable",
        "board_projection_failed",
        "flag_dir_unreadable",
    ):
        assert reason in text, f"渲染吞掉了 {reason}"


def test_date_like_and_calendar_bounds() -> None:
    assert ps._is_date_like("20260804")
    assert not ps._is_date_like("trade_cal:SSE:19901219_20261231")
    assert not ps._is_date_like("")
    assert ps._calendar_bounds("20260804") == "2026-08-04"
    assert ps._calendar_bounds("trade_cal:SSE") == "trade_cal:SSE"


def test_period_axis_note_replaces_meaningless_lag_with_next_unlock() -> None:
    """C4: 期轴数据集的「落后 69 交易日」是把日轴算术套在期轴上, 只会制造假警报。

    真正该回答的是采集窗 vs completeness vs PIT。前沿 20260430 = Q1 的法定披露截止
    (completeness, 不是 known-at); H1 报告期末即可采集, 不是「截止前不是缺口」。
    """
    note = ps._period_axis_note("tier0.disclosure.org_holding_detail_period", "20260430")
    assert note is not None
    assert "Q1" in note and "2026-08-31" in note and "报告期末即可采集" in note
    assert "completeness" in note
    assert "PIT available_date" not in note
    assert "不构成缺口" not in note

    # 认不出具体期次的期轴数据集: 仍要说明滞后数不作 SLA 判定, 不能沉默。
    assert "不构成 SLA 判定" in ps._period_axis_note(
        "tier0.disclosure.top10_float_holders_period", "20260805"
    )
    # 日轴数据集不加注 —— 它的滞后是真滞后。
    assert ps._period_axis_note("tier0.market_data.nominal_ohlcv_daily", "20260804") is None

