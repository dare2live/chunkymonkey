"""Step-status persistence helpers for the updater router."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Callable, Optional, Sequence

from routers.updater_infra import _coerce_step_record_count, _normalize_step_status


SOURCE_FAILURE_STATUSES = frozenset({"failed", "blocked", "partial"})
SOURCE_RESOLVED_STATUSES = frozenset({"completed", "skipped"})


def _prime_step_status_rows_for_steps(
    conn,
    step_specs: Sequence[dict],
    active_step_ids,
    *,
    inactive_mode: str = "idle",
    skip_reasons: Optional[dict] = None,
):
    """在后台任务真正启动前，先把本轮 step_status 写成 pending/idle/skipped。"""
    valid_ids = [s["id"] for s in step_specs]
    conn.execute(
        "DELETE FROM step_status WHERE step_id NOT IN ({})".format(
            ",".join("?" * len(valid_ids))
        ), list(valid_ids)
    )
    selected = set(active_step_ids or [])
    skip_reasons = skip_reasons or {}
    rows = []
    for s in step_specs:
        sid = s["id"]
        if sid in selected:
            rows.append((sid, s["group"], s["name"], s["order"], "pending", None, None))
        else:
            status = "skipped" if inactive_mode == "skipped" else "idle"
            error = skip_reasons.get(sid, "数据已是最新，无需更新") if status == "skipped" else None
            rows.append((sid, s["group"], s["name"], s["order"], status, error, 0))
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO step_status
            (step_id, group_name, step_name, step_order, status, error, records, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            rows,
        )
    conn.commit()


def prime_run_step_status_for_steps(
    get_conn: Callable,
    step_specs: Sequence[dict],
    active_step_ids,
    *,
    inactive_mode: str = "idle",
    skip_reasons: Optional[dict] = None,
) -> None:
    """Prime one updater run's step_status rows while owning the DB connection."""

    conn_init = get_conn(timeout=120)
    try:
        _prime_step_status_rows_for_steps(
            conn_init,
            step_specs,
            active_step_ids,
            inactive_mode=inactive_mode,
            skip_reasons=skip_reasons,
        )
    finally:
        conn_init.close()


def _sync_step_status_catalog_for_steps(conn, step_specs: Sequence[dict]) -> None:
    """Keep step_status rows aligned with the current DAG without starting a run."""

    valid_ids = [s["id"] for s in step_specs]
    if valid_ids:
        conn.execute(
            "DELETE FROM step_status WHERE step_id NOT IN ({})".format(
                ",".join("?" * len(valid_ids))
            ),
            list(valid_ids),
        )
        conn.commit()

    existing = {r[0] for r in conn.execute("SELECT step_id FROM step_status").fetchall()}
    missing = [s for s in step_specs if s["id"] not in existing]
    if missing:
        conn.executemany(
            """INSERT OR IGNORE INTO step_status
               (step_id, group_name, step_name, step_order, status, error, records, started_at, finished_at)
               VALUES (?, ?, ?, ?, 'idle', NULL, 0, NULL, NULL)""",
            [(spec["id"], spec["group"], spec["name"], spec["order"]) for spec in missing],
        )
        conn.commit()


def _record_step_source_state_for_domains(
    conn,
    step_source_domains: dict,
    step_id: str,
    status: str,
    error_text: Optional[str] = None,
    *,
    logger=None,
) -> None:
    """Update source failure queue state for a completed updater step."""

    spec = step_source_domains.get(step_id)
    if not spec:
        return
    data_domain, source_name, source_tier = spec
    try:
        from services.source_watermarks import record_source_failure, resolve_source_failures

        if status in SOURCE_FAILURE_STATUSES:
            record_source_failure(
                conn,
                data_domain=data_domain,
                source_name=source_name,
                source_tier=source_tier,
                error_type=f"step_{status}",
                last_error=error_text or status,
            )
        elif status in SOURCE_RESOLVED_STATUSES:
            resolve_source_failures(conn, data_domain=data_domain, source_name=source_name)
    except Exception as exc:
        if logger is not None:
            logger.warning("[source_failure_queue] update failed for %s: %s", step_id, exc)


def _mark_stale_running_steps_failed(conn) -> None:
    """Mark step_status rows left running by an older crashed route run."""

    conn.execute("""
        UPDATE step_status SET status = 'failed', error = '上次运行异常中断'
        WHERE status = 'running'
          AND TRY_CAST(started_at AS TIMESTAMP) < CURRENT_TIMESTAMP - INTERVAL 1 HOUR
    """)
    conn.commit()


def _mark_steps_status(
    conn,
    step_ids,
    status: str,
    error: str,
    *,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
):
    # 性能修复 (criteria #8 N+1 audit L1143): 原 for sid: conn.execute(UPDATE WHERE step_id=?)
    # 每个 step_id 单独发 1 条 SQL, step_ids 可 dynamic 任意长度 (调度路径 STEPS 4-30 不等).
    # 改 batch: 单 UPDATE ... WHERE step_id IN (placeholders), 参数仍 parameterized 防 SQL injection.
    # 行为等价: 同 step_id 集合, 同 status/error/started/finished 写值.
    # evidence: test_updater_n_plus_one_fix.py::test_mark_steps_status_uses_single_batch
    if not step_ids:
        return
    ids = [sid for sid in step_ids if sid is not None]
    if not ids:
        return
    now = datetime.now().isoformat()
    started = started_at if started_at is not None else now
    finished = finished_at if finished_at is not None else now
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE step_status SET status=?, error=?, "
        f"started_at=COALESCE(started_at, ?), finished_at=? "
        f"WHERE step_id IN ({placeholders})",
        (status, error, started, finished, *ids),
    )
    conn.commit()


def _fail_unfinished_steps(conn, step_ids, error: str):
    ids = [sid for sid in (step_ids or []) if sid]
    if not ids:
        return
    now = datetime.now().isoformat()
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"""
        UPDATE step_status
        SET status = 'failed',
            error = ?,
            started_at = COALESCE(started_at, ?),
            finished_at = ?
        WHERE step_id IN ({placeholders})
          AND (status IS NULL OR status IN ('pending', 'running'))
        """,
        [str(error)[:200], now, now, *ids],
    )
    conn.commit()


def _update_step(conn, step_id, **kwargs):
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k == "records":
            v = _coerce_step_record_count(v) or 0
        elif k == "error" and isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(step_id)
    conn.execute(f"UPDATE step_status SET {', '.join(sets)} WHERE step_id = ?", vals)
    conn.commit()


def _resolve_step_result(result):
    """规范化 runner 返回值为 (status, count, detail_json_or_skip_text).

    支持三种返回:
    - str  : 旧 skipped 接口, status='skipped', error_text = skip 原因
    - dict : 详细状态. 必含 count; 可含 status (completed/skipped), message, written, skipped, empty, failed
             序列化整体 JSON 写到 error 字段 (作为 detail, 由 _normalize_update_step_detail 解析)
    - int / None : 旧 completed 接口, status='completed', records=int
    """
    if isinstance(result, str):
        return "skipped", 0, result
    if isinstance(result, dict):
        count = int(result.get("count") or 0)
        status = _normalize_step_status(result.get("status") or "completed")
        return status, count, json.dumps(result, ensure_ascii=False)
    return "completed", int(result or 0), None
