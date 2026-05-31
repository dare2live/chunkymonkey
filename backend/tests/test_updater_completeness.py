import routers.updater_completeness as updater_completeness
from routers.updater_completeness import calibrate_data_completeness


class _Result:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class _FakeConn:
    def __init__(self, *, total_events=10, events_with_gain=10):
        self.total_events = total_events
        self.events_with_gain = events_with_gain
        self.statements = []
        self.updates = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if sql.startswith("UPDATE "):
            self.updates.append((sql, params))
            return _Result(None)
        if "return_to_now" in sql:
            return _Result(self.events_with_gain)
        return _Result(self.total_events)

    def commit(self):
        self.commits += 1


class _FakeMarketConn:
    def __init__(self):
        self.closed = False

    def execute(self, sql):
        return _Result("2026-05-26")

    def close(self):
        self.closed = True


class _FakeLogger:
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []

    def info(self, message):
        self.info_messages.append(message)

    def warning(self, message):
        self.warning_messages.append(message)


def _not_blocking(conn, step_id):
    return False


def test_calibrate_data_completeness_ignores_non_target_step(monkeypatch):
    calls = []

    def blocking(conn, step_id):
        calls.append(step_id)
        return False

    monkeypatch.setattr(
        updater_completeness,
        "get_market_conn",
        lambda: (_ for _ in ()).throw(AssertionError("market should not be opened")),
    )
    monkeypatch.setattr(
        updater_completeness,
        "summarize_industry_coverage",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("industry coverage should not be queried")
        ),
    )

    conn = _FakeConn()
    calibrate_data_completeness(
        conn,
        "sync_raw",
        is_blocking_upstream_state=blocking,
        kline_relation="price_kline_daily_qfq",
        logger=_FakeLogger(),
    )

    assert calls == []
    assert conn.statements == []
    assert conn.updates == []


def test_calibrate_data_completeness_marks_profiles_partial_from_return_coverage(monkeypatch):
    market_conn = _FakeMarketConn()
    monkeypatch.setattr(updater_completeness, "get_market_conn", lambda: market_conn)
    monkeypatch.setattr(
        updater_completeness,
        "summarize_industry_coverage",
        lambda *args, **kwargs: {"total_codes": 10, "complete_codes": 10},
    )

    conn = _FakeConn(total_events=10, events_with_gain=4)
    logger = _FakeLogger()

    calibrate_data_completeness(
        conn,
        "build_profiles",
        is_blocking_upstream_state=_not_blocking,
        kline_relation="price_kline_daily_qfq",
        logger=logger,
    )

    assert market_conn.closed
    assert conn.updates == [
        ("UPDATE mart_institution_profile SET data_completeness = ?", ("partial",))
    ]
    assert conn.commits == 1
    assert any("收益覆盖率" in message for message in logger.info_messages)


def test_calibrate_data_completeness_marks_trends_complete_when_coverage_is_enough(monkeypatch):
    monkeypatch.setattr(updater_completeness, "get_market_conn", _FakeMarketConn)
    monkeypatch.setattr(
        updater_completeness,
        "summarize_industry_coverage",
        lambda *args, **kwargs: {"total_codes": 10, "complete_codes": 9},
    )

    conn = _FakeConn(total_events=10, events_with_gain=8)

    calibrate_data_completeness(
        conn,
        "build_trends",
        is_blocking_upstream_state=_not_blocking,
        kline_relation="price_kline_daily_qfq",
        logger=_FakeLogger(),
    )

    assert conn.updates == [
        ("UPDATE mart_stock_trend SET data_completeness = ?", ("complete",))
    ]
    assert conn.commits == 1
