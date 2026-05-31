"""Audit snapshot refresh helpers for the updater router."""

import asyncio
import logging

from services.db import get_conn


logger = logging.getLogger("cm-api")
_audit_snapshot_refresh_task = None


def _is_audit_snapshot_refreshing() -> bool:
    return bool(_audit_snapshot_refresh_task and not _audit_snapshot_refresh_task.done())


def _refresh_holder_audit_snapshot_sync(source: str):
    from services.audit import refresh_quality_audit_snapshot

    conn = get_conn(timeout=120)
    try:
        refresh_quality_audit_snapshot(conn, source=source)
    finally:
        conn.close()


def _schedule_holder_audit_snapshot_refresh(source: str):
    global _audit_snapshot_refresh_task
    if _is_audit_snapshot_refreshing():
        logger.info("[审计快照] 已有刷新任务在运行，跳过重复触发")
        return

    async def _run():
        try:
            logger.info(f"[审计快照] 开始刷新: {source}")
            await asyncio.to_thread(_refresh_holder_audit_snapshot_sync, source)
            logger.info(f"[审计快照] 刷新完成: {source}")
        except Exception as exc:
            logger.warning(f"[审计快照] 刷新失败: {source}: {exc}")

    _audit_snapshot_refresh_task = asyncio.create_task(_run())


def build_update_audit_payload(force: bool = False) -> dict:
    from services.audit import get_quality_audit

    conn = get_conn()
    try:
        payload = get_quality_audit(conn, force=force)
        payload["snapshot_refreshing"] = _is_audit_snapshot_refreshing()
        return payload
    finally:
        conn.close()
