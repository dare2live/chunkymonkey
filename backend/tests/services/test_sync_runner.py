"""sync_runner 单测 — registry 驱动同步器的契约 (宪法 v2 第 6/7/9 条)."""
from __future__ import annotations

import pytest

from services.data_sources import sync_runner as sr


def _registry(**domain_overrides):
    base = {
        "version": 1,
        "defaults": {
            "retry": {"max_attempts": 3, "backoff_seconds": [0, 0, 0]},
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
                "data_start": "20260601",
                "freshness_sla_trading_days": 1,
                "min_rows_per_batch": 2,
                **domain_overrides,
            }
        },
    }
    return base


class FakeAdapter:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def fetch_raw(self, api_name, **params):
        self.calls += 1
        item = self.payloads.pop(0) if self.payloads else []
        if isinstance(item, Exception):
            raise item
        return item


def test_unregistered_domain_rejected():
    """宪法 v2 第 7/9 条: 未注册域 = 不存在."""
    with pytest.raises(KeyError, match="未注册的数据域"):
        sr._domain_spec(sr.load_registry(), "definitely_not_registered")


def test_write_batch_universe_filter_etf_prefix_override():
    """M2: universe_filter_prefixes 覆盖默认A股白名单 → ETF域只留场内ETF前缀(15/51/56/58), 丢A股/LOF."""
    from services.duck_adapter import connect

    conn = connect(":memory:")
    spec = sr._domain_spec(_registry(
        target_table="raw_tushare_fund_daily_test",
        universe_filter=True,
        universe_filter_prefixes=["15", "51", "56", "58"],
    ), "demo")
    rows = [
        {"ts_code": "510300.SH", "trade_date": "20260610", "close": 4.6},  # 场内ETF keep
        {"ts_code": "159915.SZ", "trade_date": "20260610", "close": 2.0},  # 场内ETF keep
        {"ts_code": "600000.SH", "trade_date": "20260610", "close": 9.0},  # A股 drop
        {"ts_code": "160001.SZ", "trade_date": "20260610", "close": 1.0},  # LOF drop
    ]
    n = sr._write_batch(conn, spec, rows)
    kept = {r[0] for r in conn.execute("SELECT ts_code FROM raw_tushare_fund_daily_test").fetchall()}
    assert n == 2 and kept == {"510300.SH", "159915.SZ"}, f"ETF前缀覆盖失败: {kept}"
    conn.close()


def test_write_batch_universe_filter_default_a_share_unchanged():
    """回归保护: 无 universe_filter_prefixes 覆盖 → 默认A股白名单(60/00/30/68)不变, ETF被丢."""
    from services.duck_adapter import connect

    conn = connect(":memory:")
    spec = sr._domain_spec(_registry(
        target_table="raw_tushare_ashare_test",
        universe_filter=True,  # 无 prefixes 覆盖 = 默认 A股
    ), "demo")
    rows = [
        {"ts_code": "600000.SH", "trade_date": "20260610", "close": 9.0},  # A股 keep
        {"ts_code": "510300.SH", "trade_date": "20260610", "close": 4.6},  # ETF drop (默认A股filter)
    ]
    n = sr._write_batch(conn, spec, rows)
    kept = {r[0] for r in conn.execute("SELECT ts_code FROM raw_tushare_ashare_test").fetchall()}
    assert n == 1 and kept == {"600000.SH"}, f"默认A股filter回归: {kept}"
    conn.close()


def test_write_batch_merge_idempotent():
    """grain MERGE: 同批重跑不翻倍 (幂等回填契约)."""
    from services.duck_adapter import connect

    conn = connect(":memory:")
    spec = sr._domain_spec(_registry(), "demo")
    rows = [
        {"ts_code": "000001.SZ", "trade_date": "20260610", "net": 1.0},
        {"ts_code": "000002.SZ", "trade_date": "20260610", "net": 2.0},
    ]
    n1 = sr._write_batch(conn, spec, rows)
    n2 = sr._write_batch(conn, spec, rows)  # 重跑
    total = conn.execute("SELECT COUNT(*) FROM raw_tushare_demo").fetchone()[0]
    assert (n1, n2, total) == (2, 2, 2), "重跑同批必须 MERGE 不翻倍"
    # built_at 必须存在 (PIT 守门列)
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='raw_tushare_demo'"
    ).fetchall()}
    assert "built_at" in cols
    conn.close()


def test_write_batch_schema_evolution():
    """api 新增列 → raw 表自动加列 (镜像语义), 不炸不丢."""
    from services.duck_adapter import connect

    conn = connect(":memory:")
    spec = sr._domain_spec(_registry(), "demo")
    sr._write_batch(conn, spec, [{"ts_code": "a", "trade_date": "20260609", "net": 1.0}])
    sr._write_batch(conn, spec, [{"ts_code": "a", "trade_date": "20260610", "net": 1.0, "new_col": "x"}])
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='raw_tushare_demo'"
    ).fetchall()}
    assert "new_col" in cols
    conn.close()


def test_write_batch_missing_grain_raises():
    from services.duck_adapter import connect

    conn = connect(":memory:")
    spec = sr._domain_spec(_registry(), "demo")
    with pytest.raises(ValueError, match="缺 grain 列"):
        sr._write_batch(conn, spec, [{"ts_code": "a", "net": 1.0}])  # 无 trade_date
    conn.close()


def test_zero_rows_retried_then_failed():
    """宪法 v2 第 6 条: 0 行 = 失败重试, 终败返回 None (入 failure_queue 由调用方)."""
    spec = sr._domain_spec(_registry(), "demo")
    fake = FakeAdapter([[], [], []])  # 三次全空
    out = sr._fetch_with_retry(fake, spec, {"trade_date": "20260610"})
    assert out is None and fake.calls == 3


def test_zero_rows_allowed_when_flagged():
    """allow_empty_batch 条目 (suspend_d 无停牌日) 0 行合法且不重试三次."""
    spec = sr._domain_spec(_registry(allow_empty_batch=True), "demo")
    fake = FakeAdapter([[]])
    out = sr._fetch_with_retry(fake, spec, {"trade_date": "20260610"})
    assert out == [] and fake.calls == 1


def test_retry_recovers_from_intermittent_empty():
    """间歇空响应 (vendor gateway 实测模式): 第二次重试拿到数据."""
    spec = sr._domain_spec(_registry(), "demo")
    good = [{"ts_code": "a", "trade_date": "20260610", "net": 1.0},
            {"ts_code": "b", "trade_date": "20260610", "net": 2.0}]
    fake = FakeAdapter([[], good])
    out = sr._fetch_with_retry(fake, spec, {"trade_date": "20260610"})
    assert out == good and fake.calls == 2


def test_quota_wall_detection_string_matching():
    """配额墙识别: 仅明确'当日/账户级'措辞算墙 (2026-06-16 用户纠偏: 瞬态限流≠当日墙, 不停链)."""
    assert sr._is_quota_wall("今日请求已达上限，请明天再试！")
    assert sr._is_quota_wall("请求过多会被系统认为攻击")   # 含'攻击' = 反刷量真墙
    assert not sr._is_quota_wall("zero_rows")
    assert not sr._is_quota_wall("Connection refused")
    assert not sr._is_quota_wall("您没有访问该接口的权限")  # 官方权限错 != 配额墙
    # 瞬态限流 (每分钟/并发级) 不是当日墙 — 旧 taxonomy 把'访问频率/请求过多'误判成墙致误停链 (本次根因)
    assert not sr._is_quota_wall("访问频率过高")
    assert not sr._is_quota_wall("您的并发请求过多（上限 2个），请稍后重试!")


def test_transient_ratelimit_detection_and_retry():
    """瞬态限流: 识别为 transient + 退避重试不停链 (用户 2026-06-16: 每分钟级, 过几分钟重试就好)."""
    assert sr._is_transient_ratelimit("您的并发请求过多（上限 2个），请稍后重试!")
    assert sr._is_transient_ratelimit("访问频率过高")
    assert not sr._is_transient_ratelimit("今日请求已达上限，请明天再试！")
    # 撞瞬态限流 = 不抛 QuotaExhaustedError, 走满重试后入 failure_queue (返 None), 不停全链
    spec = sr._domain_spec(_registry(retry={"max_attempts": 3, "backoff_seconds": [0, 0, 0],
                                            "transient_backoff_seconds": [0, 0, 0]}), "demo")
    ad = FakeAdapter([RuntimeError("您的并发请求过多（上限 2个），请稍后重试!")] * 3)
    out = sr._fetch_with_retry(ad, spec, {"trade_date": "20260610"})
    assert out is None and ad.calls == 3, "瞬态限流应退避重试满 3 次, 不熔断 (对比当日墙首次即抛)"


def test_quota_wall_raises_immediately_no_retry():
    """配额墙命中 = 立即抛 QuotaExhaustedError, 不消耗重试 (续戳加重反刷量判定)."""
    spec = sr._domain_spec(_registry(retry={"max_attempts": 3, "backoff_seconds": [0, 0, 0]}), "demo")
    ad = FakeAdapter([RuntimeError("今日请求已达上限，请明天再试！")])
    with pytest.raises(sr.QuotaExhaustedError):
        sr._fetch_with_retry(ad, spec, {"trade_date": "20260610"})
    assert ad.calls == 1, "配额墙必须首次命中即抛, 不能重试 (实测续戳延长冷却)"


def test_normal_error_still_retries():
    """对照: 普通异常仍走满 3 次重试 (熔断不误伤正常退避)."""
    spec = sr._domain_spec(_registry(retry={"max_attempts": 3, "backoff_seconds": [0, 0, 0]}), "demo")
    ad = FakeAdapter([RuntimeError("timeout"), RuntimeError("timeout"), RuntimeError("timeout")])
    out = sr._fetch_with_retry(ad, spec, {"trade_date": "20260610"})
    assert out is None and ad.calls == 3


def test_run_domain_halts_chain_on_quota_wall(monkeypatch, tmp_path):
    """run_domain 撞配额墙 = 停剩余批 + 上抛 (不逐日续戳烧配额); 已写批保留可恢复."""
    import services.data_sources.sync_runner as m
    from services.duck_adapter import connect

    # 第 1 日成功写入, 第 2 日撞墙 → 第 3 日不该再被调用
    ad = FakeAdapter([
        [{"ts_code": "a", "trade_date": "20260601", "net": 1.0},
         {"ts_code": "b", "trade_date": "20260601", "net": 2.0}],
        RuntimeError("今日请求已达上限，请明天再试！"),
        [{"ts_code": "c", "trade_date": "20260603", "net": 3.0}],  # 不该被取到
    ])
    db = str(tmp_path / "halt.duckdb")  # 文件库: run_domain 的 finally close 后数据仍可复读
    monkeypatch.setattr(m, "_adapter", lambda src: ad)
    monkeypatch.setattr(m, "_target_conn", lambda spec: connect(db))
    monkeypatch.setattr(m, "_record_outcome", lambda *a, **k: None)
    monkeypatch.setattr(m, "_trading_days", lambda start, end=None: ["20260601", "20260602", "20260603"])
    with pytest.raises(sr.QuotaExhaustedError):
        m.run_domain("demo", backfill=True, registry=_registry(data_start="20260601", min_rows_per_batch=1))
    # 第 1 日已写入保留 (熔断前的批落盘), 第 3 日 payload 未被消费 (熔断停链证据)
    rconn = connect(db)
    assert rconn.execute("SELECT COUNT(*) FROM raw_tushare_demo").fetchone()[0] == 2
    rconn.close()
    assert len(ad.payloads) == 1, "撞墙后剩余批不该被调用 (熔断生效)"


def test_quarter_ends_generates_report_periods():
    """by_period 报告期生成 (2026-06-14 express_vip 接入): [start,end] 内季末日 YYYYMMDD."""
    # 2023Q1 ~ 2026Q1 = 13 期
    assert sr._quarter_ends("20230101", "20260612") == [
        "20230331", "20230630", "20230930", "20231231",
        "20240331", "20240630", "20240930", "20241231",
        "20250331", "20250630", "20250930", "20251231",
        "20260331",
    ]
    # 边界: 单期含端点 / 空范围 (无季末日落入)
    assert sr._quarter_ends("20251231", "20251231") == ["20251231"]
    assert sr._quarter_ends("20251201", "20251220") == []


class _CapturingAdapter:
    """记录每批 fetch 收到的 params (验 by_code_list end_date 动态注入)."""
    def __init__(self, rows):
        self.rows = list(rows)
        self.seen_params = []

    def fetch_raw(self, api_name, **params):
        self.seen_params.append(dict(params))
        return list(self.rows)


def _by_code_list_registry(fixed_params, code_list=None):
    return _registry(batch_mode="by_code_list", code_param="ts_code",
                     code_list=code_list or ["000300.SH"], fixed_params=fixed_params,
                     min_rows_per_batch=1)


def test_by_code_list_injects_dynamic_end_date(monkeypatch, tmp_path):
    """ranged by_code_list (有start_date无end_date) → 注入 latest_completed_trade_date 防钉死日期 stale (§4.4 根治)."""
    import services.utils as su
    from services.duck_adapter import connect
    ad = _CapturingAdapter([{"ts_code": "000300.SH", "trade_date": "20260618", "close": 1.0}])
    monkeypatch.setattr(sr, "_adapter", lambda src: ad)
    monkeypatch.setattr(sr, "_target_conn", lambda spec: connect(str(tmp_path / "a.duckdb")))
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: connect(str(tmp_path / "wm.duckdb")))
    monkeypatch.setattr(sr, "_record_outcome", lambda *a, **k: None)
    monkeypatch.setattr(su, "latest_completed_trade_date", lambda conn: "2026-06-18")
    sr.run_domain("demo", registry=_by_code_list_registry({"start_date": "20050101"}))
    assert ad.seen_params[0].get("end_date") == "20260618"   # 动态 end 注入 (横线去掉)
    assert ad.seen_params[0].get("start_date") == "20050101"  # start 保留


def test_by_code_list_no_inject_without_start_date(monkeypatch, tmp_path):
    """无 start_date 的 by_code_list (index_member_all is_new 域) 不被误注入 end_date (防误伤冻结快照域)."""
    from services.duck_adapter import connect
    ad = _CapturingAdapter([{"ts_code": "801010.SI", "trade_date": "20260618", "close": 1.0}])
    monkeypatch.setattr(sr, "_adapter", lambda src: ad)
    monkeypatch.setattr(sr, "_target_conn", lambda spec: connect(str(tmp_path / "b.duckdb")))
    monkeypatch.setattr(sr, "_record_outcome", lambda *a, **k: None)
    sr.run_domain("demo", registry=_by_code_list_registry({"is_new": "Y"}, code_list=["801010.SI"]))
    assert "end_date" not in ad.seen_params[0]                # is_new 域无 start_date → 不注入


def test_by_code_list_end_override(monkeypatch, tmp_path):
    """--end 覆盖优先于 latest_completed_trade_date (回填指定窗口)."""
    import services.utils as su
    from services.duck_adapter import connect
    ad = _CapturingAdapter([{"ts_code": "000300.SH", "trade_date": "20251231", "close": 1.0}])
    monkeypatch.setattr(sr, "_adapter", lambda src: ad)
    monkeypatch.setattr(sr, "_target_conn", lambda spec: connect(str(tmp_path / "c.duckdb")))
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: connect(str(tmp_path / "wm.duckdb")))
    monkeypatch.setattr(sr, "_record_outcome", lambda *a, **k: None)
    monkeypatch.setattr(su, "latest_completed_trade_date", lambda conn: "2026-06-18")
    sr.run_domain("demo", end="20251231", registry=_by_code_list_registry({"start_date": "20050101"}))
    assert ad.seen_params[0].get("end_date") == "20251231"   # --end 覆盖 latest


def test_by_ts_code_backfill_injects_start_date(monkeypatch):
    """mythos §10 data_start 参数口径: by_ts_code 回填必须传 start_date=data_start.

    实测踩坑 (2026-06-24): stk_holdernumber 不传 start_date 只返最近 ~8 期 (600519: 无日期 8 行
    vs start_date=2019 给 38 行全史) → 回填"成功"但 0 净新增, 94min 白跑。回填注入 start_date,
    增量不注入 (拿最近期即可覆盖新季)。
    """
    import services.universe as uni

    class _StubConn:
        def close(self):
            pass

    monkeypatch.setattr(uni, "get_active_universe", lambda conn, include_st=False: {"000001", "600519"})
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: _StubConn())
    spec = {"domain": "stk_holdernumber", "data_start": "20190101", "batch_mode": "by_ts_code"}

    bf = sr._by_ts_code_batches(spec, backfill=True)
    inc = sr._by_ts_code_batches(spec, backfill=False)

    assert bf and all(b.get("start_date") == "20190101" for b in bf)   # 回填注入全史下界
    assert inc and all("start_date" not in b for b in inc)             # 增量不注入 (拿最近期)


def test_by_ts_code_backfill_no_data_start_no_injection(monkeypatch):
    """无 data_start 的 by_ts_code 域回填不被误注入 start_date (防误伤返全史接口)."""
    import services.universe as uni

    class _StubConn:
        def close(self):
            pass

    monkeypatch.setattr(uni, "get_active_universe", lambda conn, include_st=False: {"600519"})
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: _StubConn())
    spec = {"domain": "demo_full_history", "batch_mode": "by_ts_code"}  # 无 data_start
    bf = sr._by_ts_code_batches(spec, backfill=True)
    assert bf and all("start_date" not in b for b in bf)
