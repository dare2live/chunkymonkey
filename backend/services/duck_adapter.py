"""DuckDB 轻量 DB-API 适配层

目的: 给业务代码提供统一的 DuckDB 连接、游标和 Row 访问方式。

支持:
  - conn.execute() / executescript() / executemany()
  - .fetchone() / .fetchall() / .fetchmany()
  - row['col_name']  (dict-like)
  - ALTER TABLE ADD COLUMN IF NOT EXISTS (try/except 风格继续工作)
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Sequence

import duckdb

logger = logging.getLogger("cm-api")
_CONNECT_LOCK = threading.Lock()


class Row:
    """dict-like 只读行，支持 r[0] / r['col_name']。"""
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
        """DB-API writers may call .execute(); proxy and refresh columns."""
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


def _attach_mode_to_read_only(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    mode = str(value).strip().lower().replace("-", "_")
    if mode in {"read_only", "readonly", "ro"}:
        return True
    if mode in {"read_write", "readwrite", "rw", "write"}:
        return False
    raise ValueError(f"invalid attach mode: {value!r}")


def _normalize_attach_spec(spec: Any) -> tuple[str, bool]:
    if isinstance(spec, dict):
        db_path = spec.get("path") or spec.get("db_path")
        if db_path is None:
            raise ValueError("attach spec dict must define path or db_path")
        if "read_only" in spec:
            read_only = _attach_mode_to_read_only(spec["read_only"])
        else:
            read_only = _attach_mode_to_read_only(spec.get("mode") or spec.get("attach_mode") or "read_only")
        return str(db_path), read_only
    if isinstance(spec, (list, tuple)) and len(spec) == 2:
        db_path, read_only = spec
        return str(db_path), _attach_mode_to_read_only(read_only)
    if isinstance(spec, Path):
        return str(spec), True
    return str(spec), True


class DuckConn:
    """DuckDB 连接包装器。"""

    def __init__(self, db_path: str, read_only: bool = False, attach: dict = None, timeout: int = 30):
        # FastAPI opens short-lived DuckDB connections from multiple worker
        # threads. Serializing connection creation avoids transient unique
        # file-handle conflicts while keeping each request on its own handle.
        connect_start = time.monotonic()
        mutex_start = connect_start
        with _CONNECT_LOCK:
            self.connect_mutex_wait_s = round(time.monotonic() - mutex_start, 6)
            self._con, self.duckdb_lock_wait_s = self._connect_with_retry(
                db_path,
                read_only=read_only,
                timeout=timeout,
            )
        self.duckdb_connect_wait_s = round(self.connect_mutex_wait_s + self.duckdb_lock_wait_s, 6)
        self.duckdb_connect_elapsed_s = round(time.monotonic() - connect_start, 6)
        self.db_path = str(db_path)
        self.in_transaction = False
        # Optional attached DBs are read-only by default. Use
        # {"path": "...", "read_only": False} only for an explicit write edge.
        if attach:
            for alias, spec in attach.items():
                try:
                    path, attach_read_only = _normalize_attach_spec(spec)
                    attach_with_retry(self._con, alias, path, read_only=attach_read_only, timeout=timeout)
                except Exception as e:
                    logger.warning("attach %s failed: %s", alias, e)

    @staticmethod
    def _connect_with_retry(db_path: str, *, read_only: bool, timeout: int):
        deadline = time.monotonic() + max(float(timeout), 0.0)
        delay = 0.1
        lock_wait_s = 0.0
        while True:
            try:
                return duckdb.connect(db_path, read_only=read_only), round(lock_wait_s, 6)
            except duckdb.IOException as exc:
                message = str(exc)
                lock_conflict = "Could not set lock on file" in message or "Conflicting lock" in message
                if not lock_conflict or time.monotonic() >= deadline:
                    raise
                sleep_s = min(delay, max(deadline - time.monotonic(), 0.0))
                if sleep_s <= 0:
                    raise
                logger.info("DuckDB busy, retrying connection in %.1fs: %s", sleep_s, db_path)
                time.sleep(sleep_s)
                lock_wait_s += sleep_s
                delay = min(delay * 1.5, 1.0)

    def _exec(self, sql: str, params=None) -> DuckCursor:
        try:
            if params is None:
                cur = self._con.execute(sql)
            else:
                # DuckDB 兼容 ? 占位符。
                cur = self._con.execute(sql, params if isinstance(params, (list, tuple)) else (params,))
            return DuckCursor(cur)
        except Exception:
            # 业务代码通常 try/except 包着, 这里保持 raise。
            raise

    def execute(self, sql: str, params=None) -> DuckCursor:
        return self._exec(sql, params)

    def executescript(self, sql: str) -> None:
        """按 ; 分段执行多条 SQL。"""
        # 按 ; 分段, 过滤空语句。
        for stmt in self._split_statements(sql):
            if not stmt.strip():
                continue
            try:
                self._con.execute(stmt)
            except Exception as e:
                # 容忍重复建表/重复加列一类幂等 DDL。
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
        seq = list(seq_of_params)
        if not seq:
            return DuckCursor(self._con.cursor())
        self._con.executemany(sql, seq)
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

    # DB-API compatibility for libraries that probe driver cursor support.
    def cursor(self):
        return DuckCursor(self._con.cursor())

    # DB-API 兼容属性
    @property
    def isolation_level(self):
        return None

    @isolation_level.setter
    def isolation_level(self, v):
        pass  # DuckDB MVCC, 忽略


def attach_with_retry(
    conn: duckdb.DuckDBPyConnection,
    alias: str,
    db_path: str,
    *,
    read_only: bool,
    timeout: int = 30,
) -> float:
    """Attach a DuckDB file with lock retry semantics."""
    deadline = time.monotonic() + max(float(timeout), 0.0)
    delay = 0.1
    lock_wait_s = 0.0
    db_path_escaped = str(db_path).replace("'", "''")
    mode = "READ_ONLY" if read_only else "READ_WRITE"
    while True:
        try:
            conn.execute(f"ATTACH '{db_path_escaped}' AS {alias} ({mode})")
            return round(lock_wait_s, 6)
        except duckdb.IOException as exc:
            message = str(exc)
            lock_conflict = "Could not set lock on file" in message or "Conflicting lock" in message
            if not lock_conflict or time.monotonic() >= deadline:
                raise
            sleep_s = min(delay, max(deadline - time.monotonic(), 0.0))
            if sleep_s <= 0:
                raise
            logger.info("DuckDB busy, retrying ATTACH in %.1fs: %s", sleep_s, db_path)
            time.sleep(sleep_s)
            lock_wait_s += sleep_s
            delay = min(delay * 1.5, 1.0)


def connect(db_path: str, timeout: int = 30, read_only: bool = False, attach: dict = None) -> DuckConn:
    """统一入口: 返回 DuckConn。"""
    return DuckConn(db_path, read_only=read_only, attach=attach, timeout=timeout)
