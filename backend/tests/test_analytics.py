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
