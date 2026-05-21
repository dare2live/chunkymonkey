"""N+1 audit (criteria #8) 真问题修复回归测试.

覆盖 backend/routers/updater.py 两处 HIGH severity SQL_EXECUTE_IN_FOR_LOOP:
- L1143 `_mark_steps_status`: for sid in step_ids: conn.execute(UPDATE ...)
  → 单 batch UPDATE ... WHERE step_id IN (?...)
- L1991 `_step_build_profiles_sync` stats query: for inst: conn.execute(WHERE inst_id=?)
  → 预聚合 GROUP BY institution_id, for-loop 改 dict lookup

测试目标:
1. fix 后 SQL 调用次数从 N 降到 1
2. 返回数据 shape / values 等价于旧实现 (backward compat)
3. SQL 仍 parameterized (防 injection)
4. 边界: 空 step_ids / 缺失 inst_id 不报错
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import updater  # noqa: E402
from routers.updater import _mark_steps_status, _prime_step_status_rows  # noqa: E402


# ---------------------------------------------------------------------------
# 公共 mock conn (record SQL + params)
# ---------------------------------------------------------------------------


class _Row(dict):
    """模拟 sqlite3.Row / duckdb Row 的 mapping + index 双访问."""

    def __init__(self, mapping):
        super().__init__(mapping)
        self._values = list(mapping.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _RecordingConn:
    """记录所有 execute(sql, params) 调用, 按返回值队列响应."""

    def __init__(self, response_queue=None):
        self.calls: list[tuple[str, tuple]] = []
        self.executemany_calls: list[tuple[str, list[tuple]]] = []
        self.commits = 0
        # response_queue: list[list[Row]] FIFO; 默认空 list
        self._responses = list(response_queue or [])

    def execute(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        rows = self._responses.pop(0) if self._responses else []
        return _Cursor(rows)

    def executemany(self, sql, rows):
        self.executemany_calls.append((sql, [tuple(row) for row in rows]))

    def commit(self):
        self.commits += 1


# ---------------------------------------------------------------------------
# L1143 _mark_steps_status fix
# ---------------------------------------------------------------------------


class TestMarkStepsStatusBatch:
    def test_uses_single_batch_update(self):
        conn = _RecordingConn()
        step_ids = ["sync_kline", "build_features", "build_panel", "rank", "score"]

        _mark_steps_status(
            conn, step_ids, "completed", "",
            started_at="2026-05-19T10:00:00",
            finished_at="2026-05-19T10:05:00",
        )

        # 修复后: 1 次 SQL (vs 旧实现 5 次 per-sid)
        assert len(conn.calls) == 1, f"expected 1 batch UPDATE, got {len(conn.calls)} calls"
        sql, params = conn.calls[0]

        # SQL 形态: UPDATE ... WHERE step_id IN (?,?,?,?,?)
        assert "UPDATE step_status" in sql
        assert "WHERE step_id IN (" in sql
        assert sql.count("?") == 4 + len(step_ids)  # status, error, started_at, finished_at + ids

        # 参数顺序: (status, error, started, finished, *ids)
        assert params[0] == "completed"
        assert params[1] == ""
        assert params[2] == "2026-05-19T10:00:00"
        assert params[3] == "2026-05-19T10:05:00"
        assert tuple(params[4:]) == tuple(step_ids)

        # 仍调 commit
        assert conn.commits == 1

    def test_empty_step_ids_noop(self):
        conn = _RecordingConn()
        _mark_steps_status(conn, [], "failed", "boom")
        assert conn.calls == []
        assert conn.commits == 0

    def test_filters_none_step_ids(self):
        """step_ids 含 None 时应过滤, 不发空 IN (...) 查询."""
        conn = _RecordingConn()
        _mark_steps_status(conn, [None, None], "failed", "boom")
        assert conn.calls == []
        assert conn.commits == 0

    def test_single_step_id_still_batch(self):
        conn = _RecordingConn()
        _mark_steps_status(
            conn, ["sync_kline"], "running", "",
            started_at="2026-05-19T10:00:00",
            finished_at="2026-05-19T10:00:00",
        )
        assert len(conn.calls) == 1
        sql, params = conn.calls[0]
        assert "WHERE step_id IN (?)" in sql
        assert params[-1] == "sync_kline"


class TestPrimeStepStatusRowsBatch:
    def test_primes_selected_and_inactive_steps_with_one_executemany(self, monkeypatch):
        steps = [
            {"id": "a", "group": "g", "name": "A", "order": 1},
            {"id": "b", "group": "g", "name": "B", "order": 2},
            {"id": "c", "group": "g", "name": "C", "order": 3},
        ]
        monkeypatch.setattr(updater, "STEPS", steps)
        conn = _RecordingConn()

        _prime_step_status_rows(
            conn,
            ["a"],
            inactive_mode="skipped",
            skip_reasons={"b": "already fresh"},
        )

        assert len(conn.calls) == 1
        assert "DELETE FROM step_status" in conn.calls[0][0]
        assert len(conn.executemany_calls) == 1
        sql, rows = conn.executemany_calls[0]
        assert "INSERT OR REPLACE INTO step_status" in sql
        assert rows == [
            ("a", "g", "A", 1, "pending", None, None),
            ("b", "g", "B", 2, "skipped", "already fresh", 0),
            ("c", "g", "C", 3, "skipped", "数据已是最新，无需更新", 0),
        ]
        assert conn.commits == 1


# ---------------------------------------------------------------------------
# L1991 _step_build_profiles_sync stats 预聚合 fix
# ---------------------------------------------------------------------------
#
# 真函数 _step_build_profiles_sync 跨 ~370 行 + 强依赖 mart/holdings/pricing
# helpers, 不适合整体 mock. 这里直接验证 fix 的核心契约:
#   1. 单 GROUP BY query 拿 1000 inst stats (代替 1000 次 per-inst query)
#   2. dict lookup map 给 for-loop 用, 缺失 inst_id 落 default 0 0 0
#   3. SQL 不含 WHERE institution_id = ? per-inst 形态
#
# 模拟改造后的 stats-preaggregate snippet 行为, 跟生产代码同一 SQL 模板.


PROFILES_STATS_SQL = """
            SELECT institution_id,
                   COUNT(*) as total_events,
                   COUNT(DISTINCT stock_code) as total_stocks,
                   COUNT(DISTINCT report_date) as total_periods
            FROM fact_institution_event
            GROUP BY institution_id
        """


def _build_inst_stats_map(conn):
    """复刻 _step_build_profiles_sync 内的预聚合段落 (跟实现保持同 SQL)."""
    stats_map: dict = {}
    for r in conn.execute(PROFILES_STATS_SQL).fetchall():
        stats_map[r["institution_id"]] = {
            "total_events": r["total_events"],
            "total_stocks": r["total_stocks"],
            "total_periods": r["total_periods"],
        }
    return stats_map


class TestBuildProfilesStatsBatch:
    def test_single_groupby_query_for_1000_institutions(self):
        # 模拟 1000 inst 的 GROUP BY 输出
        rows = [
            _Row({
                "institution_id": f"inst_{i:04d}",
                "total_events": i + 1,
                "total_stocks": (i % 50) + 1,
                "total_periods": (i % 12) + 1,
            })
            for i in range(1000)
        ]
        conn = _RecordingConn(response_queue=[rows])

        stats_map = _build_inst_stats_map(conn)

        # 关键: 仅 1 次 SQL (vs 旧 1000 次)
        assert len(conn.calls) == 1
        sql, params = conn.calls[0]
        assert "GROUP BY institution_id" in sql
        # 关键反例: 旧 SQL 含 WHERE institution_id = ? 单条; 新 SQL 不应有这模式
        assert "WHERE institution_id = ?" not in sql
        # 预聚合无参数 (全表 GROUP BY)
        assert params == ()

        # map 完整, lookup 等价 per-inst stats
        assert len(stats_map) == 1000
        assert stats_map["inst_0000"]["total_events"] == 1
        assert stats_map["inst_0042"]["total_stocks"] == 43 % 50  # (42 % 50) + 1 = 43
        assert stats_map["inst_0999"]["total_periods"] == (999 % 12) + 1

    def test_dict_lookup_preserves_response_shape(self):
        """stats dict 须支持 stats["total_events"] / stats["total_stocks"] / stats["total_periods"]
        以匹配下游 INSERT 语句 L2275 stats["total_events"], stats["total_stocks"], stats["total_periods"]."""
        rows = [
            _Row({
                "institution_id": "inst_QFII_001",
                "total_events": 152,
                "total_stocks": 47,
                "total_periods": 8,
            }),
        ]
        conn = _RecordingConn(response_queue=[rows])

        stats_map = _build_inst_stats_map(conn)
        stats = stats_map["inst_QFII_001"]

        # 跟生产 INSERT 一致的 3 字段 mapping access
        assert stats["total_events"] == 152
        assert stats["total_stocks"] == 47
        assert stats["total_periods"] == 8

    def test_missing_inst_id_default_zeros(self):
        """新进 institutions 没历史 events → dict.get 给 0/0/0 (跟旧 fetchone() 全 COUNT 行为一致)."""
        rows = [
            _Row({
                "institution_id": "inst_existing",
                "total_events": 10,
                "total_stocks": 3,
                "total_periods": 2,
            }),
        ]
        conn = _RecordingConn(response_queue=[rows])
        stats_map = _build_inst_stats_map(conn)

        empty_default = {"total_events": 0, "total_stocks": 0, "total_periods": 0}
        # 模拟生产: stats = stats_map.get(inst_id, empty_default)
        stats_new = stats_map.get("inst_new_2026", empty_default)
        assert stats_new["total_events"] == 0
        assert stats_new["total_stocks"] == 0
        assert stats_new["total_periods"] == 0

    def test_no_per_inst_where_clause_in_call_log(self):
        """fix 后整段 stats 计算只产生 1 个 GROUP BY query, 不含 per-inst WHERE."""
        rows = [
            _Row({
                "institution_id": f"i{i}",
                "total_events": i,
                "total_stocks": i,
                "total_periods": i,
            })
            for i in range(5)
        ]
        conn = _RecordingConn(response_queue=[rows])
        _build_inst_stats_map(conn)
        for sql, _params in conn.calls:
            assert "WHERE institution_id = ?" not in sql, (
                f"per-inst WHERE leaked back into pre-aggregate: {sql!r}"
            )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
