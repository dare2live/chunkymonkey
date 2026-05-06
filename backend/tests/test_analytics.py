from services import analytics


def test_sql_returns_records(monkeypatch):
    class FakeCursor:
        description = [("code",), ("value",)]

        def fetchall(self):
            return [("000001", 12.5)]

    class FakeConnection:
        def execute(self, query, params):
            assert query == "SELECT code, value FROM sample WHERE code = ?"
            assert params == ("000001",)
            return FakeCursor()

    monkeypatch.setattr(analytics, "get_duck", lambda: FakeConnection())

    assert analytics.sql("SELECT code, value FROM sample WHERE code = ?", ("000001",)) == [
        {"code": "000001", "value": 12.5}
    ]


def test_sql_returns_empty_list_for_statements_without_rows(monkeypatch):
    class FakeCursor:
        description = None

        def fetchall(self):
            raise AssertionError("fetchall should not be called without a result set")

    class FakeConnection:
        def execute(self, query, params):
            assert query == "CREATE TABLE sample (code TEXT)"
            assert params == ()
            return FakeCursor()

    monkeypatch.setattr(analytics, "get_duck", lambda: FakeConnection())

    assert analytics.sql("CREATE TABLE sample (code TEXT)") == []


def test_duck_connection_closes_after_context_exit(monkeypatch):
    class FakeConnection:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    fake = FakeConnection()
    monkeypatch.setattr(analytics, "_open_duck", lambda writable=False: fake)

    with analytics.duck_connection(writable=True) as con:
        assert con is fake
        assert fake.closed is False

    assert fake.closed is True


def test_get_duck_opens_fresh_connection_each_call(monkeypatch):
    created = []

    class FakeConnection:
        pass

    def _fake_open(writable=False):
        con = FakeConnection()
        created.append((writable, con))
        return con

    monkeypatch.setattr(analytics, "_open_duck", _fake_open)

    first = analytics.get_duck(writable=True)
    second = analytics.get_duck(writable=True)

    assert first is not second
    assert [item[0] for item in created] == [True, True]
