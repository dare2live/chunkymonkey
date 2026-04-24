"""DuckDB 兼容层 — 模仿 sqlite3.Connection 接口

目的: 业务代码 (routers/services) 无需改动, 就能从 SQLite 切到 DuckDB.

支持:
  - conn.execute() / executescript() / executemany()
  - .fetchone() / .fetchall() / .fetchmany()
  - row['col_name']  (dict-like, 类似 sqlite3.Row)
  - PRAGMA 静默吞掉 (DuckDB 不支持 PRAGMA journal_mode/busy_timeout)
  - INTEGER AUTOINCREMENT → 自动转 GENERATED ... IDENTITY (写 DDL 时)
  - ALTER TABLE ADD COLUMN IF NOT EXISTS (try/except 风格继续工作)

不支持:
  - sqlite3.Row 的 .keys() 方法 (若业务有用, 后续补)
  - 复杂 PRAGMA 业务 (默认全部 no-op)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional, Sequence

import duckdb

logger = logging.getLogger("cm-api")


class Row:
    """模仿 sqlite3.Row 的 dict-like 只读行. 支持 r[0] / r['col_name']"""
    __slots__ = ("_values", "_cols")

    def __init__(self, values: Sequence[Any], cols: list[str]):
        self._values = tuple(values)
        self._cols = cols

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        if isinstance(key, str):
            return self._values[self._cols.index(key)]
        raise KeyError(key)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return f"Row({dict(zip(self._cols, self._values))})"

    def keys(self):
        return list(self._cols)

    def get(self, key, default=None):
        try:
            return self[key]
        except Exception:
            return default


class DuckCursor:
    """包装 duckdb cursor, 转 Row 对象"""

    def __init__(self, cursor: duckdb.DuckDBPyConnection):
        self._cur = cursor
        self._cols: list[str] = []
        desc = getattr(cursor, 'description', None)
        if desc:
            self._cols = [d[0] for d in desc]
        self.rowcount = -1
        self.lastrowid = None

    def execute(self, sql, params=None):
        """pandas.to_sql 会在 cursor 上调 .execute(); 透传到 duckdb cursor 并刷新列名"""
        if params is None:
            self._cur = self._cur.execute(sql)
        else:
            self._cur = self._cur.execute(sql, params if isinstance(params, (list, tuple)) else (params,))
        desc = getattr(self._cur, 'description', None)
        self._cols = [d[0] for d in desc] if desc else []
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return Row(row, self._cols)

    def fetchall(self):
        rows = self._cur.fetchall()
        return [Row(r, self._cols) for r in rows]

    def fetchmany(self, size=1):
        rows = self._cur.fetchmany(size)
        return [Row(r, self._cols) for r in rows]

    def __iter__(self):
        for row in self._cur.fetchall():
            yield Row(row, self._cols)

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass

    @property
    def description(self):
        return self._cur.description


# SQLite-only 语法 → DuckDB 改写 / no-op
_PRAGMA_TABLE_INFO_RE = re.compile(r"^\s*PRAGMA\s+table_info\s*\(\s*([\w_]+)\s*\)\s*;?\s*$", re.IGNORECASE)
_PRAGMA_RE = re.compile(r"^\s*PRAGMA\s+", re.IGNORECASE)
# SQLite BEGIN IMMEDIATE / DEFERRED / EXCLUSIVE 在 DuckDB 里统一为 BEGIN
_BEGIN_MODE_RE = re.compile(r"^\s*BEGIN\s+(IMMEDIATE|DEFERRED|EXCLUSIVE)\s*;?\s*$", re.IGNORECASE)
_AUTOINCREMENT_RE = re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE)
_AUTOINCREMENT2_RE = re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE)
_SQLITE_NOW_RE = re.compile(r"datetime\(\s*['\"]now['\"]\s*\)", re.IGNORECASE)
_SQLITE_MASTER_RE = re.compile(r"\bsqlite_master\b", re.IGNORECASE)


def _normalize_sql(sql: str) -> Optional[str]:
    """return None 表示 no-op; 否则返回规范化 SQL (SQLite 语法 → DuckDB).
    特殊: PRAGMA table_info(x) → SELECT * FROM (DESCRIBE x)"""
    stripped = sql.lstrip()
    m = _PRAGMA_TABLE_INFO_RE.match(stripped)
    if m:
        tbl = m.group(1)
        # DuckDB DESCRIBE 返回 column_name/column_type/null/key/default/extra
        # 转成 SQLite PRAGMA table_info 兼容列序: cid, name, type, notnull, dflt_value, pk
        return (
            f"SELECT 0 AS cid, column_name AS name, column_type AS type, "
            f"CASE WHEN \"null\"='YES' THEN 0 ELSE 1 END AS notnull, "
            f"NULL AS dflt_value, "
            f"CASE WHEN key='PRI' THEN 1 ELSE 0 END AS pk "
            f"FROM (DESCRIBE {tbl})"
        )
    if _PRAGMA_RE.match(stripped):
        return None
    if _BEGIN_MODE_RE.match(stripped):
        # SQLite BEGIN IMMEDIATE → DuckDB BEGIN TRANSACTION (MVCC, 模式参数无效)
        return "BEGIN TRANSACTION"
    s = _AUTOINCREMENT_RE.sub("INTEGER PRIMARY KEY", sql)
    s = _AUTOINCREMENT2_RE.sub("", s)
    # datetime('now') → current_timestamp
    s = _SQLITE_NOW_RE.sub("current_timestamp", s)
    # sqlite_master → information_schema.tables
    # 保守: 只在无歧义时替换
    s = _SQLITE_MASTER_RE.sub(
        "(SELECT table_name as name, 'table' as type FROM information_schema.tables)",
        s,
    )
    return s


class DuckConn:
    """模仿 sqlite3.Connection"""

    def __init__(self, db_path: str, read_only: bool = False, attach: dict = None):
        self._con = duckdb.connect(db_path, read_only=read_only)
        self._row_factory = None  # 业务代码会 `conn.row_factory = sqlite3.Row` 我们 ignore
        self.in_transaction = False
        # 可选 ATTACH 其它 DuckDB
        if attach:
            for alias, path in attach.items():
                try:
                    mode = "READ_ONLY" if read_only else "READ_WRITE"
                    self._con.execute(f"ATTACH '{path}' AS {alias} ({mode})")
                except Exception as e:
                    logger.warning("attach %s failed: %s", alias, e)

    # row_factory setter — 业务代码会赋值, 我们 ignore (永远返回 Row)
    @property
    def row_factory(self):
        return self._row_factory

    @row_factory.setter
    def row_factory(self, v):
        self._row_factory = v  # 记下但不用

    def _exec(self, sql: str, params=None) -> DuckCursor:
        norm = _normalize_sql(sql)
        if norm is None:
            # no-op: 返回空 cursor
            return DuckCursor(self._con.cursor())
        try:
            if params is None:
                cur = self._con.execute(norm)
            else:
                # duckdb 兼容 ? 占位符 (和 sqlite3 相同)
                cur = self._con.execute(norm, params if isinstance(params, (list, tuple)) else (params,))
            return DuckCursor(cur)
        except Exception as e:
            # 某些 SQLite-only 语法 (如 ALTER TABLE ADD COLUMN 对已存在列) 会抛错
            # 业务代码通常 try/except 包着, 这里保持 raise
            raise

    def execute(self, sql: str, params=None) -> DuckCursor:
        return self._exec(sql, params)

    def executescript(self, sql: str) -> None:
        """SQLite executescript 按 ; 分段执行多条, DuckDB 原生支持分号分段"""
        # 按 ; 分段, 过滤空 + PRAGMA
        for stmt in self._split_statements(sql):
            if not stmt.strip():
                continue
            norm = _normalize_sql(stmt)
            if norm is None:
                continue
            try:
                self._con.execute(norm)
            except Exception as e:
                # SQLite 的 CREATE TABLE IF NOT EXISTS / ALTER TABLE ADD IF NOT EXISTS 行为模拟
                msg = str(e).lower()
                if 'already exists' in msg or 'duplicate' in msg:
                    continue
                raise

    @staticmethod
    def _split_statements(sql: str) -> list[str]:
        """简单按分号分段 (忽略引号里的分号)"""
        out = []
        current = []
        in_quote = False
        quote_char = ''
        for ch in sql:
            if in_quote:
                current.append(ch)
                if ch == quote_char:
                    in_quote = False
                continue
            if ch in ("'", '"'):
                in_quote = True
                quote_char = ch
                current.append(ch)
                continue
            if ch == ';':
                out.append(''.join(current))
                current = []
                continue
            current.append(ch)
        if current:
            out.append(''.join(current))
        return out

    def executemany(self, sql: str, seq_of_params) -> DuckCursor:
        norm = _normalize_sql(sql)
        if norm is None:
            return DuckCursor(self._con.cursor())
        seq = list(seq_of_params)
        if not seq:
            return DuckCursor(self._con.cursor())
        self._con.executemany(norm, seq)
        return DuckCursor(self._con.cursor())

    def commit(self):
        try:
            self._con.commit()
        except Exception:
            # DuckDB auto-commits; this is best-effort
            pass

    def rollback(self):
        try:
            self._con.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self._con.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # 暴露 raw DuckDB 连接给需要高级功能的模块 (如 analytics.py)
    @property
    def raw(self):
        return self._con

    # pandas df.to_sql 兼容: pandas 调用 conn.execute(...), 返回 Cursor; 我们已实现
    # 但 pandas >= 2.0 会探测 driver, 可能需要 .cursor()
    def cursor(self):
        return DuckCursor(self._con.cursor())

    # sqlite3 兼容属性
    @property
    def isolation_level(self):
        return None

    @isolation_level.setter
    def isolation_level(self, v):
        pass  # DuckDB MVCC, 忽略


def connect(db_path: str, timeout: int = 30, read_only: bool = False, attach: dict = None) -> DuckConn:
    """统一入口: 业务代码继续调 connect(), 返回 DuckConn (鸭子类型兼容 sqlite3.Connection)"""
    return DuckConn(db_path, read_only=read_only, attach=attach)
