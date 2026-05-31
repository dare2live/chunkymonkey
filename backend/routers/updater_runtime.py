"""Runtime helpers shared by updater step runner modules."""

import asyncio

from services.db import get_conn


async def _run_blocking_db_task(task_fn, timeout: int = 120):
    """把纯本地重算移到线程里，避免阻塞状态接口轮询。"""

    def _worker():
        worker_conn = get_conn(timeout=timeout)
        try:
            return task_fn(worker_conn)
        finally:
            worker_conn.close()

    return await asyncio.to_thread(_worker)


async def _run_blocking_market_db_task(task_fn, timeout: int = 120):
    """把同时依赖业务库和行情库的本地重算移到线程里。"""
    from services.market_db import get_market_conn

    def _worker():
        worker_conn = get_conn(timeout=timeout)
        worker_mkt_conn = get_market_conn()
        try:
            return task_fn(worker_conn, worker_mkt_conn)
        finally:
            worker_mkt_conn.close()
            worker_conn.close()

    return await asyncio.to_thread(_worker)
