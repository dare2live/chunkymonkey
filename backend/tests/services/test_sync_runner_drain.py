"""drain_domain 单测 — 日历 gap 重放契约 (宪法第 1/5/6 条)."""
from __future__ import annotations

from services.data_sources import sync_runner as sr
from services.duck_adapter import connect


def _registry(**domain_overrides):
    return {
        "version": 1,
        "defaults": {
            "execution_policy": {"mode": "enabled", "reason": "active"},
            "fetch_timeout_seconds": 120,
            "retry": {"max_attempts": 2, "backoff_seconds": [0, 0]},
            "zero_row_policy": "fail",
            "target_db": "tushare_raw",
        },
        "domains": {
            "demo": {
                "source": "tushare",
                "api": "demo_api",
                "target_table": "raw_tushare_demo",
                "grain": ["ts_code", "trade_date"],
                "batch_mode": "by_trade_date",
                "pit_anchor": "trade_date; t-1",
                "data_start": "20200101",
                "freshness_sla_trading_days": 1,
                **domain_overrides,
            }
        },
    }


class FakeAdapter:
    def __init__(self, by_date):
        self.by_date = by_date
        self.calls: list[str] = []

    def fetch_raw(self, api_name, **params):
        d = params["trade_date"]
        self.calls.append(d)
        item = self.by_date.get(d, [])
        if isinstance(item, Exception):
            raise item
        return item


def _seed(conn, dates):
    conn.execute("CREATE TABLE raw_tushare_demo (ts_code VARCHAR, trade_date VARCHAR, val DOUBLE, built_at TIMESTAMP)")
    for d in dates:
        conn.execute("INSERT INTO raw_tushare_demo VALUES ('000001.SZ', ?, 1.0, now())", [d])


def test_drain_refills_only_gap_days():
    """缺口 = 应有 − 实有; 已有日期一个 API call 都不浪费."""
    conn = connect(":memory:")
    _seed(conn, ["20200101", "20200103"])
    adapter = FakeAdapter({"20200102": [{"ts_code": "000001.SZ", "trade_date": "20200102", "val": 2.0}]})
    r = sr.drain_domain("demo", registry=_registry(), conn=conn, adapter=adapter,
                        expected_trading_days=["20200101", "20200102", "20200103"], record=False)
    assert adapter.calls == ["20200102"]
    assert r["status"] == "drained" and r["gap_days"] == 1 and r["refilled_days"] == 1
    n = conn.execute("SELECT COUNT(*) FROM raw_tushare_demo WHERE trade_date='20200102'").fetchone()[0]
    assert n == 1


def test_drain_clean_when_no_gap():
    conn = connect(":memory:")
    _seed(conn, ["20200101"])
    r = sr.drain_domain("demo", registry=_registry(), conn=conn, adapter=FakeAdapter({}),
                        expected_trading_days=["20200101"], record=False)
    assert r["status"] == "clean" and r["gap_days"] == 0 and r["refilled_rows"] == 0


def test_drain_uses_the_domain_eligibility_owner_for_expected_days(monkeypatch):
    conn = connect(":memory:")
    _seed(conn, ["20200101"])
    calendar_calls = []
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec: sr.DomainEligibility(
            "20200101", True, "t_plus_one_awaiting_next_trading_day"
        ),
    )
    monkeypatch.setattr(
        sr,
        "trading_days",
        lambda start, end=None: calendar_calls.append((start, end)) or ["20200101"],
    )

    result = sr.drain_domain(
        "demo",
        registry=_registry(available_after="t+1"),
        conn=conn,
        adapter=FakeAdapter({}),
        record=False,
    )

    assert calendar_calls == [("20200101", "20200101")]
    assert result["status"] == "clean"


def test_drain_terminal_failure_stays_listed_not_silent():
    """终败日期显式入 still_failed + status=partial (宪法第 5 条: 不静默)."""
    conn = connect(":memory:")
    _seed(conn, [])
    adapter = FakeAdapter({"20200101": []})  # zero_row_policy=fail → 重试后终败
    r = sr.drain_domain("demo", registry=_registry(), conn=conn, adapter=adapter,
                        expected_trading_days=["20200101"], record=False)
    assert r["status"] == "partial" and r["still_failed"] == ["20200101"]


def test_drain_allow_empty_domain_inapplicable():
    """allow_empty 域空日合法 gap 不可判定 → 显式 drain_inapplicable, 走增量 fallback."""
    r = sr.drain_domain("demo", registry=_registry(allow_empty_batch=True), record=False)
    assert r["status"] == "drain_inapplicable"


def test_drain_allow_empty_domain_with_cross_check_can_refill_gap():
    """block_trade 类稀疏域有交叉真相门时可 drain，不能永久被 allow_empty 早退。"""
    conn = connect(":memory:")
    _seed(conn, [])
    reg = _registry(allow_empty_batch=True, cross_check_domain="daily")
    reg["domains"]["daily"] = {
        "source": "tushare",
        "api": "daily",
        "target_table": "raw_tushare_daily",
        "grain": ["ts_code", "trade_date"],
        "batch_mode": "by_trade_date",
    }
    adapter = FakeAdapter({
        "20200101": [{"ts_code": "000001.SZ", "trade_date": "20200101", "val": 2.0}]
    })

    result = sr.drain_domain(
        "demo",
        registry=reg,
        conn=conn,
        adapter=adapter,
        expected_trading_days=["20200101"],
        record=False,
    )

    assert result["status"] == "drained" and result["refilled_days"] == 1


def test_drain_respects_custom_date_param():
    """date_param 域 (如 dividend ex_date): gap 扫描列与 fetch 参数都用它."""
    conn = connect(":memory:")
    conn.execute("CREATE TABLE raw_tushare_demo (ts_code VARCHAR, ex_date VARCHAR, val DOUBLE, built_at TIMESTAMP)")
    conn.execute("INSERT INTO raw_tushare_demo VALUES ('000001.SZ', '20200101', 1.0, now())")

    class ParamCapture(FakeAdapter):
        def fetch_raw(self, api_name, **params):
            assert "ex_date" in params and "trade_date" not in params
            return super().fetch_raw(api_name, trade_date=params["ex_date"])

    adapter = ParamCapture({"20200102": [{"ts_code": "000001.SZ", "ex_date": "20200102", "val": 2.0}]})
    reg = _registry(date_param="ex_date", grain=["ts_code", "ex_date"])
    r = sr.drain_domain("demo", registry=reg, conn=conn, adapter=adapter,
                        expected_trading_days=["20200101", "20200102"], record=False)
    assert r["status"] == "drained" and r["refilled_days"] == 1


def test_drain_unsupported_batch_mode_explicit():
    """非 by_trade_date 域显式 unsupported, 不静默跳过."""
    r = sr.drain_domain("demo", registry=_registry(batch_mode="by_ts_code"), record=False)
    assert r["status"] == "unsupported"


def test_drain_treats_below_min_rows_day_as_gap():
    """复审 HIGH: 在表但行数 < min_rows 的残缺日必须算缺口重拉, 不许洗白."""
    conn = connect(":memory:")
    _seed(conn, ["20200101"])  # 表里已有 1 行, 但 min_rows=3 → 残缺日
    full = [{"ts_code": f"00000{i}.SZ", "trade_date": "20200101", "val": 1.0} for i in range(3)]
    adapter = FakeAdapter({"20200101": full})
    r = sr.drain_domain("demo", registry=_registry(min_rows_per_batch=3), conn=conn,
                        adapter=adapter, expected_trading_days=["20200101"], record=False)
    assert adapter.calls == ["20200101"]
    assert r["status"] == "drained" and r["gap_days"] == 1
    n = conn.execute("SELECT COUNT(*) FROM raw_tushare_demo WHERE trade_date='20200101'").fetchone()[0]
    assert n == 3  # MERGE 幂等补齐


def test_drain_refetched_truncated_batch_stays_failed():
    """重拉仍不足 min_rows (vendor 截断) → 整批零写并计 still_failed。"""
    conn = connect(":memory:")
    _seed(conn, [])
    adapter = FakeAdapter({"20200101": [
        {"ts_code": "000001.SZ", "trade_date": "20200101", "val": 1.0},
        {"ts_code": "000002.SZ", "trade_date": "20200101", "val": 2.0},
    ]})
    r = sr.drain_domain("demo", registry=_registry(min_rows_per_batch=3), conn=conn,
                        adapter=adapter, expected_trading_days=["20200101"], record=False)
    assert r["status"] == "partial" and r["still_failed"] == ["20200101"]
    n = conn.execute("SELECT COUNT(*) FROM raw_tushare_demo").fetchone()[0]
    assert n == 0  # 完整性失败批零写入，不能让 partial 覆盖/污染旧快照


def test_drain_identity_group_contract_refills_missing_exchange_atomically():
    """行数够但缺 BSE 的日期仍是 gap；三交易所逻辑批收齐后才能替换。"""
    conn = connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_tushare_demo "
        "(trade_date VARCHAR, exchange_id VARCHAR, val DOUBLE, built_at TIMESTAMP)"
    )
    conn.executemany(
        "INSERT INTO raw_tushare_demo VALUES (?, ?, 1.0, now())",
        [("20260701", group) for group in ("SSE", "SZSE", "HKEX")],
    )
    reg = _registry(
        grain=["trade_date", "exchange_id"],
        min_rows_per_batch=3,
        split_by={"param": "exchange_id", "values": ["SSE", "SZSE", "BSE"]},
        batch_completeness={
            "group_from": {"column": "exchange_id", "transform": "identity"},
            "required_groups": ["SSE", "SZSE", "BSE"],
        },
    )

    class ExchangeAdapter:
        def __init__(self):
            self.calls = []

        def fetch_raw(self, _api, **params):
            self.calls.append(params["exchange_id"])
            return [{
                "trade_date": params["trade_date"],
                "exchange_id": params["exchange_id"],
                "val": 2.0,
            }]

    adapter = ExchangeAdapter()
    result = sr.drain_domain(
        "demo",
        registry=reg,
        conn=conn,
        adapter=adapter,
        expected_trading_days=["20260701"],
        record=False,
    )

    assert result["status"] == "drained" and result["gap_days"] == 1
    assert adapter.calls == ["SSE", "SZSE", "BSE"]
    groups = {
        row[0]
        for row in conn.execute(
            "SELECT exchange_id FROM raw_tushare_demo WHERE trade_date='20260701'"
        ).fetchall()
    }
    assert groups == {"SSE", "SZSE", "BSE", "HKEX"}


def test_drain_conditional_group_contract_uses_exact_bse_start_boundary():
    """北交所两融启动前一交易日两市场完整；20230213 起缺 BSE 才是 gap。"""
    contract = {
        "group_from": {"column": "exchange_id", "transform": "identity"},
        "required_groups": ["SSE", "SZSE"],
        "required_groups_since": {"BSE": "20230213"},
    }

    before = connect(":memory:")
    before.execute(
        "CREATE TABLE raw_tushare_demo "
        "(trade_date VARCHAR, exchange_id VARCHAR, built_at TIMESTAMP)"
    )
    before.executemany(
        "INSERT INTO raw_tushare_demo VALUES ('20230210', ?, now())",
        [("SSE",), ("SZSE",)],
    )
    before_result = sr.drain_domain(
        "demo",
        registry=_registry(
            grain=["trade_date", "exchange_id"],
            min_rows_per_batch=2,
            batch_completeness=contract,
        ),
        conn=before,
        adapter=FakeAdapter({}),
        expected_trading_days=["20230210"],
        record=False,
    )
    assert before_result["status"] == "clean"

    boundary = connect(":memory:")
    boundary.execute(
        "CREATE TABLE raw_tushare_demo "
        "(trade_date VARCHAR, exchange_id VARCHAR, built_at TIMESTAMP)"
    )
    boundary.executemany(
        "INSERT INTO raw_tushare_demo VALUES ('20230213', ?, now())",
        [("SSE",), ("SZSE",)],
    )
    boundary_result = sr.drain_domain(
        "demo",
        registry=_registry(
            grain=["trade_date", "exchange_id"],
            min_rows_per_batch=2,
            batch_completeness=contract,
        ),
        conn=boundary,
        adapter=FakeAdapter({}),
        expected_trading_days=["20230213"],
        max_dates=0,
        record=False,
    )
    assert boundary_result["gap_days"] == 1
    assert boundary_result["status"] == "partial"


def test_drain_failed_todo_is_not_reported_as_success_watermark_date(monkeypatch):
    """历史完整日和失败待办都不能伪装成本轮成功水位。"""
    conn = connect(":memory:")
    _seed(conn, ["20200101"])
    recorded = {}
    monkeypatch.setattr(sr, "_record_outcome", lambda spec, **kw: recorded.update(kw))
    result = sr.drain_domain(
        "demo",
        registry=_registry(),
        conn=conn,
        adapter=FakeAdapter({"20200102": []}),
        expected_trading_days=["20200101", "20200102"],
        record=True,
    )

    assert result["status"] == "partial"
    assert recorded["last_date"] is None
    assert recorded["ok"] is False


def test_drain_partial_refill_watermark_only_uses_successful_todo(monkeypatch):
    """部分补齐时只推进到本轮真实写入日，不能借用历史 actual。"""
    conn = connect(":memory:")
    _seed(conn, ["20200101"])
    recorded = {}
    monkeypatch.setattr(sr, "_record_outcome", lambda spec, **kw: recorded.update(kw))
    result = sr.drain_domain(
        "demo",
        registry=_registry(),
        conn=conn,
        adapter=FakeAdapter({
            "20200102": [
                {"ts_code": "000001.SZ", "trade_date": "20200102", "val": 2.0}
            ],
            "20200103": [],
        }),
        expected_trading_days=["20200101", "20200102", "20200103"],
        record=True,
    )

    assert result["status"] == "partial" and result["refilled_days"] == 1
    assert recorded["last_date"] == "20200102"
    assert recorded["ok"] is False


def test_drain_clean_audit_does_not_refresh_success_watermark(monkeypatch):
    """无需 provider 补写的 clean 扫描可关账，但不得伪造新成功时间。"""
    conn = connect(":memory:")
    _seed(conn, ["20200101"])
    recorded = {}
    monkeypatch.setattr(sr, "_record_outcome", lambda spec, **kw: recorded.update(kw))

    result = sr.drain_domain(
        "demo",
        registry=_registry(),
        conn=conn,
        adapter=FakeAdapter({}),
        expected_trading_days=["20200101"],
        record=True,
    )

    assert result["status"] == "clean"
    assert recorded["last_date"] is None
    assert recorded["ok"] is True and recorded["resolve_failures"] is True


def test_drain_records_write_failure_without_claiming_refill(monkeypatch):
    conn = connect(":memory:")
    _seed(conn, [])
    monkeypatch.setattr(
        sr,
        "_write_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    recorded = {}
    monkeypatch.setattr(sr, "_record_outcome", lambda spec, **kw: recorded.update(kw))
    result = sr.drain_domain(
        "demo",
        registry=_registry(),
        conn=conn,
        adapter=FakeAdapter({
            "20200101": [{"ts_code": "000001.SZ", "trade_date": "20200101", "val": 1.0}]
        }),
        expected_trading_days=["20200101"],
        record=True,
    )

    assert result["status"] == "partial" and result["refilled_days"] == 0
    assert recorded["ok"] is False and recorded["last_date"] is None


def test_drain_truncation_takes_newest_first():
    """复审 MEDIUM: 名额受限时最新日期优先, 昨日数据不许被老缺口挤掉."""
    conn = connect(":memory:")
    _seed(conn, [])
    payload = {d: [{"ts_code": "000001.SZ", "trade_date": d, "val": 1.0}]
               for d in ["20200101", "20200102", "20200103"]}
    adapter = FakeAdapter(payload)
    r = sr.drain_domain("demo", registry=_registry(), conn=conn, adapter=adapter,
                        expected_trading_days=["20200101", "20200102", "20200103"],
                        max_dates=1, record=False)
    assert adapter.calls == ["20200103"]  # 最新优先
    assert r["truncated"] is True


def test_drain_max_dates_truncation_reports_partial():
    """限流截断必须显式 truncated+partial, 不许伪装清完."""
    conn = connect(":memory:")
    _seed(conn, [])
    payload = {d: [{"ts_code": "000001.SZ", "trade_date": d, "val": 1.0}]
               for d in ["20200101", "20200102", "20200103"]}
    r = sr.drain_domain("demo", registry=_registry(), conn=conn, adapter=FakeAdapter(payload),
                        expected_trading_days=["20200101", "20200102", "20200103"],
                        max_dates=2, record=False)
    assert r["truncated"] is True and r["status"] == "partial" and r["refilled_days"] == 2


def test_fetch_paged_concatenates_until_short_page():
    """分页拼接到末页 (< limit 即止), 全量无截断."""
    spec = {"domain": "demo", "api": "x", "page_limit": 2,
            "retry": {"max_attempts": 1, "backoff_seconds": [0]}}

    class PagedAdapter:
        def fetch_raw(self, api, **params):
            offset = params.get("offset", 0)
            data = [{"i": n} for n in range(5)]
            return data[offset: offset + params["limit"]]

    rows = sr._fetch_paged(PagedAdapter(), spec, {"trade_date": "20200101"})
    assert [r["i"] for r in rows] == [0, 1, 2, 3, 4]


def test_fetch_paged_midpage_failure_returns_none_not_partial():
    """中间页终败 → 整批 None (部分页写入会伪装完整日, 比失败更危险)."""
    spec = {"domain": "demo", "api": "x", "page_limit": 2, "allow_empty_batch": False,
            "retry": {"max_attempts": 1, "backoff_seconds": [0]}}

    class FailSecondPage:
        def fetch_raw(self, api, **params):
            if params.get("offset", 0) >= 2:
                raise RuntimeError("gateway timeout")
            return [{"i": 0}, {"i": 1}]

    assert sr._fetch_paged(FailSecondPage(), spec, {"trade_date": "20200101"}) is None


def test_fetch_paged_without_page_limit_passthrough():
    spec = {"domain": "demo", "api": "x",
            "retry": {"max_attempts": 1, "backoff_seconds": [0]}}

    class OneShot:
        def fetch_raw(self, api, **params):
            assert "limit" not in params and "offset" not in params
            return [{"i": 9}]

    assert sr._fetch_paged(OneShot(), spec, {"trade_date": "20200101"}) == [{"i": 9}]


def test_run_domain_ok_strict_any_failure_is_not_ok(monkeypatch):
    """复查 #14: 任一批失败 result['ok'] 必须 False — 旧宽松口径掩盖过 29 批失败."""
    reg = _registry()
    conn = connect(":memory:")
    adapter = FakeAdapter({
        "20200101": [{"ts_code": "000001.SZ", "trade_date": "20200101", "val": 1.0}],
        "20200102": [],  # zero_row_policy=fail → 终败
    })
    recorded = {}
    monkeypatch.setattr(sr, "_adapter", lambda name: adapter)
    monkeypatch.setattr(sr, "_target_conn", lambda spec: conn)
    monkeypatch.setattr(sr, "trading_days", lambda start, end=None: ["20200101", "20200102"])
    monkeypatch.setattr(sr, "_last_watermark_date", lambda d, s: None)
    monkeypatch.setattr(sr, "_record_outcome",
                        lambda spec, **kw: recorded.update(kw))
    r = sr.run_domain("demo", backfill=True, registry=reg)
    assert r["failed_batches"] == 1
    assert r["ok"] is False           # 严格: 不许部分成功伪装 ok
    assert recorded["ok"] is False    # 与 record 判定统一 (消双标)


def test_write_batch_merge_null_safe_on_nullable_grain_column():
    """MERGE-on-grain DELETE 必须 NULL-safe (2026-07-05 grain 门实锤: ths_hot 美股子榜
    ts_code 恒为 NULL, 普通 `=` 对 NULL 永远算 UNKNOWN → 旧行从未被删, 每次重跑都新插一份
    "重复", 实测 430 组 863 行历史累积)。用 IS NOT DISTINCT FROM 后, 同 grain(含 NULL 列)
    重跑必须覆盖旧行, 不能累积。"""
    conn = connect(":memory:")
    spec = {"domain": "ths_hot_like", "target_table": "t_null_grain",
            "grain": ["trade_date", "data_type", "ts_code", "rank_time"]}
    row = {"trade_date": "20250623", "data_type": "美股", "ts_code": None,
           "ts_name": "Circle", "rank_time": "2025-06-23 21:25:43"}
    sr._write_batch(conn, spec, [dict(row)])
    sr._write_batch(conn, spec, [dict(row)])  # 同 grain(含 NULL ts_code) 重跑
    n = conn.execute(
        "SELECT COUNT(*) FROM t_null_grain WHERE trade_date='20250623' AND ts_name='Circle'"
    ).fetchone()[0]
    assert n == 1, f"NULL-safe MERGE 应覆盖旧行, 不应累积 (实得 {n} 行)"


def test_domain_sample_captured_on_first_batch(tmp_path, monkeypatch):
    """根因 A 契约: 首批写入自动存真实样本入 fixtures; 已存在不覆盖 (注册时刻快照)."""
    monkeypatch.setattr(sr, "_SAMPLE_DIR", tmp_path)
    conn = connect(":memory:")
    spec = sr.domain_spec(_registry(), "demo")
    rows = [{"ts_code": "BK0145.DC", "trade_date": "20200101", "val": 1.0}]
    sr._write_batch(conn, spec, rows)
    import json as _json
    sample = _json.loads((tmp_path / "demo.json").read_text())
    assert sample["rows"][0]["ts_code"] == "BK0145.DC"  # 真实形态原样保存
    assert sample["grain"] == ["ts_code", "trade_date"]
    # 第二批不同数据不覆盖首批样本
    sr._write_batch(conn, spec, [{"ts_code": "XXXX", "trade_date": "20200102", "val": 2.0}])
    sample2 = _json.loads((tmp_path / "demo.json").read_text())
    assert sample2["rows"][0]["ts_code"] == "BK0145.DC"
