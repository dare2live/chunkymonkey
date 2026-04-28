"""通用 API cache — P3.10 (2026-04-28).

设计:
- 内存 LRU + TTL (毫秒级响应)
- 装饰器形式 @cached(ttl_sec=300, key_fn=...)
- 失败/异常不污染 cache (raise 上抛, 不存)
- 跑 updater 时可批量 invalidate

不引 Redis (个人项目, 单进程 / 启动重置可接受).
"""
from __future__ import annotations

import functools
import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("cm-api.cache")


class _Cache:
    def __init__(self, max_entries: int = 1000):
        self._store: dict[Any, tuple[float, Any]] = {}  # key → (expires_at, value)
        self._lock = threading.Lock()
        self._max = max_entries
        self._hits = 0
        self._misses = 0

    def get(self, key) -> tuple[bool, Any]:
        """(hit, value). hit=True 表示缓存命中且未过期."""
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                self._misses += 1
                return False, None
            exp, val = entry
            if time.time() > exp:
                self._store.pop(key, None)
                self._misses += 1
                return False, None
            self._hits += 1
            return True, val

    def set(self, key, value, ttl_sec: float):
        with self._lock:
            if len(self._store) >= self._max:
                # 简单 LRU: 删最早过期的
                if self._store:
                    oldest_key = min(self._store.items(), key=lambda x: x[1][0])[0]
                    self._store.pop(oldest_key, None)
            self._store[key] = (time.time() + ttl_sec, value)

    def invalidate(self, prefix: str | None = None):
        with self._lock:
            if prefix is None:
                self._store.clear()
            else:
                to_del = [k for k in self._store if str(k).startswith(prefix)]
                for k in to_del:
                    self._store.pop(k, None)

    def stats(self) -> dict:
        with self._lock:
            n = len(self._store)
            total = self._hits + self._misses
            hit_rate = self._hits / total if total else 0
        return {
            "entries": n,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 3),
        }


_GLOBAL = _Cache(max_entries=2000)


def cached(ttl_sec: float = 300, key_fn: Callable | None = None):
    """装饰器: 缓存 function 结果.

    key_fn(*args, **kwargs) → hashable, 不传则用 (args, sorted(kwargs)).
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if key_fn:
                key = (fn.__name__, key_fn(*args, **kwargs))
            else:
                key = (fn.__name__, args, tuple(sorted(kwargs.items())))
            hit, val = _GLOBAL.get(key)
            if hit:
                return val
            val = fn(*args, **kwargs)
            _GLOBAL.set(key, val, ttl_sec)
            return val
        wrapper.__cache_invalidate__ = lambda: _GLOBAL.invalidate(prefix=fn.__name__)
        return wrapper
    return deco


def get_cache_stats() -> dict:
    return _GLOBAL.stats()


def invalidate(prefix: str | None = None):
    _GLOBAL.invalidate(prefix)
