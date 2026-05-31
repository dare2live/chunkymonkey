"""Status and plan helpers for the updater router."""

from collections import deque
from datetime import datetime
from typing import Callable, Optional

from routers.updater_infra import _sanitize_step_status_item


# Phase ψ.5 根因 3: static 30s budget 无视 backlog; sync_market_data
# catchup 20 天会 timeout, step_status failed, 但真实 worker 继续写 DB.
STEP_BUDGET_MODEL: dict[str, dict] = {
    "sync_calendar": {"base": 30, "per_day": 0, "max": 30},
    "sync_market_data": {"base": 120, "per_day": 60, "max": 900},
    "sync_financial": {"base": 600, "per_day": 120, "max": 3600},
    "sync_raw": {"base": 60, "per_day": 20, "max": 300},
    "sync_industry": {"base": 60, "per_day": 10, "max": 180},
    "sync_surveys": {"base": 90, "per_day": 15, "max": 240},
    "sync_qfii": {"base": 90, "per_day": 10, "max": 180},
    "sync_lhb": {"base": 90, "per_day": 10, "max": 180},
    "sync_aif10_holder_count": {"base": 90, "per_day": 10, "max": 180},
    "sync_aif10_valuation_quantile": {"base": 90, "per_day": 10, "max": 180},
    "sync_aif10_peer_valuation": {"base": 90, "per_day": 10, "max": 180},
    "sync_aif10_forecast_consensus": {"base": 90, "per_day": 10, "max": 180},
    "sync_aif10_financial_history": {"base": 90, "per_day": 10, "max": 180},
    "calc_financial_derived": {"base": 90, "per_day": 0, "max": 90},
    "build_stage_features": {"base": 90, "per_day": 0, "max": 90},
    "build_external_attention": {"base": 90, "per_day": 0, "max": 90},
    "calc_stock_scores": {"base": 90, "per_day": 0, "max": 90},
    "calc_inst_scores": {"base": 90, "per_day": 0, "max": 90},
    "refresh_today_signals": {"base": 120, "per_day": 0, "max": 120},
}

STEP_BUDGET_SECONDS = {sid: model["base"] for sid, model in STEP_BUDGET_MODEL.items()}

STEP_SOURCE_DOMAINS = {
    "sync_calendar": ("trading_calendar", "akshare_calendar", 3),
    "sync_market_data": ("kline_daily", "tdxhub_quote", 1),
    "sync_financial": ("financial_gpcw_8q", "tdxhub_gpcw", 1),
    "sync_raw": ("holders_top10_float", "tdxhub_holders", 1),
    "sync_industry": ("industry_sw", "tdxhub_block", 1),
    "sync_surveys": ("institution_survey", "aif10_survey", 2),
    "sync_qfii": ("qfii_holding_quarterly", "aif10_qfii", 2),
    "sync_lhb": ("lhb_daily", "aif10_lhb", 2),
    "sync_aif10_holder_count": ("aif10_holder_count", "aif10", 2),
    "sync_aif10_valuation_quantile": ("aif10_valuation_quantile", "aif10", 2),
    "sync_aif10_peer_valuation": ("aif10_peer_valuation", "aif10", 2),
    "sync_aif10_forecast_consensus": ("aif10_forecast_consensus", "aif10", 2),
    "sync_aif10_financial_history": ("aif10_financial_history", "aif10", 2),
}

DAILY_NON_CRITICAL_STEPS = {
    "sync_surveys",
    "calc_financial_derived",
    "build_external_attention",
    "build_stage_features",
    "calc_stock_scores",
    "refresh_today_signals",
}

TERMINAL_STEP_STATUSES = frozenset({"completed", "partial", "failed", "blocked", "skipped", "stopped"})
COUNTED_STEP_STATUSES = TERMINAL_STEP_STATUSES | frozenset({"running", "pending"})


def build_run_context(
    mode: str,
    step_id: Optional[str] = None,
    step_name: Optional[str] = None,
    step_ids=None,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """Build the persisted in-memory context for one updater run."""

    ts = (now or datetime.now()).isoformat()
    return {
        "mode": mode,
        "step_id": step_id,
        "step_name": step_name,
        "step_ids": list(step_ids) if step_ids else None,
        "started_at": ts,
        "heartbeat_at": ts,
    }


def touch_run_context_heartbeat(
    run_context: Optional[dict],
    step_id: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Refresh a run context heartbeat in place and return it for tests/callers."""

    if not run_context:
        return None
    run_context["heartbeat_at"] = (now or datetime.now()).isoformat()
    if step_id:
        run_context["step_id"] = step_id
    return run_context


def build_noop_run_context(mode: str, message: str, *, now: Optional[datetime] = None) -> dict:
    """Build a terminal run context for smart-update no-op responses."""

    ts = (now or datetime.now()).isoformat()
    return {
        "mode": mode,
        "step_id": None,
        "step_name": None,
        "step_ids": [],
        "started_at": ts,
        "finished_at": ts,
        "noop": True,
        "message": message,
    }


def build_finished_run_context(
    run_context: Optional[dict],
    extra: Optional[dict] = None,
    *,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Build the terminal copy of a running updater context."""

    if not run_context:
        return None
    ctx = dict(run_context)
    ctx["finished_at"] = (now or datetime.now()).isoformat()
    if extra:
        ctx.update(extra)
    return ctx


def _watermark_lag_days(conn, step_id: str) -> int:
    """Return wall-clock lag days for a step watermark."""
    domain_info = STEP_SOURCE_DOMAINS.get(step_id)
    if not domain_info:
        return 0
    domain, source, _tier = domain_info
    try:
        row = conn.execute(
            "SELECT updated_at FROM mart_data_source_watermark WHERE data_domain=? AND source_name=?",
            (domain, source),
        ).fetchone()
    except Exception:
        return 0
    if not row or not row[0]:
        return 0
    try:
        wm_text = str(row[0])[:19]
        wm = datetime.fromisoformat(wm_text.replace(" ", "T"))
        return max(0, (datetime.now() - wm).days)
    except Exception:
        return 0


def _step_budget_seconds(step_id: str, *, conn=None) -> Optional[int]:
    """Compute dynamic step budget from base + watermark lag."""
    model = STEP_BUDGET_MODEL.get(step_id)
    if not model:
        return STEP_BUDGET_SECONDS.get(step_id)
    base = int(model["base"])
    per_day = int(model.get("per_day", 0))
    cap = int(model.get("max", base))
    if per_day == 0 or conn is None:
        return min(base, cap)
    lag = _watermark_lag_days(conn, step_id)
    return min(base + lag * per_day, cap)


def _plan_with_budgets(plan: dict) -> dict:
    out = dict(plan or {})
    steps = list(out.get("steps") or [])
    budgets = {step_id: STEP_BUDGET_SECONDS[step_id] for step_id in steps if step_id in STEP_BUDGET_SECONDS}
    out["budgets"] = budgets
    out["estimated_budget_s"] = sum(int(value or 0) for value in budgets.values())
    return out


def _critical_daily_plan(plan: dict) -> dict:
    out = dict(plan or {})
    steps = list(out.get("steps") or [])
    kept = [step for step in steps if step not in DAILY_NON_CRITICAL_STEPS]
    removed = [step for step in steps if step in DAILY_NON_CRITICAL_STEPS]
    out["steps"] = kept
    skip_reasons = dict(out.get("skip_reasons") or {})
    for step in removed:
        skip_reasons[step] = "daily critical sync skips non-critical dashboard/research step; run full smart or nightly research"
    out["skip_reasons"] = skip_reasons
    if removed:
        reasons = list(out.get("reason") or [])
        reasons.append("daily critical_only filtered: " + ", ".join(removed))
        out["reason"] = reasons
        out["critical_only_removed_steps"] = removed
    return _plan_with_budgets(out)


def build_smart_plan_response(
    *,
    get_conn: Callable,
    build_smart_plan: Callable,
    critical_only: bool,
) -> dict:
    """Build the read-only `/update/smart-plan` response and close its DB connection."""
    conn = get_conn()
    try:
        plan = build_smart_plan(conn)
        plan = _critical_daily_plan(plan) if critical_only else _plan_with_budgets(plan)
        return {"ok": True, "plan": plan}
    finally:
        conn.close()


async def prepare_smart_update_plan(
    conn,
    *,
    critical_only: bool,
    sync_calendar: Callable,
    build_smart_plan: Callable,
    ensure_calendar_step_for_data_fetch: Callable[[list[str]], list[str]],
) -> dict:
    """Build the executable smart-update plan after calendar preflight."""

    calendar_preflight = await sync_calendar(conn)
    if calendar_preflight.get("status") == "failed":
        return {
            "ok": False,
            "message": calendar_preflight.get("error") or "交易日历不可用",
            "calendar_preflight": calendar_preflight,
            "steps_to_run": [],
        }

    # Production cron must not reuse a stale audit snapshot: a cached raw
    # freshness miss can incorrectly expand the daily plan into a long
    # full-source sync.
    raw_plan = build_smart_plan(conn, use_cache=False)
    plan = _critical_daily_plan(raw_plan) if critical_only else _plan_with_budgets(raw_plan)

    steps_to_run = ensure_calendar_step_for_data_fetch(list(plan["steps"]))
    if steps_to_run != plan["steps"]:
        plan = dict(plan)
        plan["steps"] = steps_to_run
        plan = _plan_with_budgets(plan)

    if not steps_to_run:
        return {
            "ok": True,
            "noop": True,
            "message": "数据已是最新，无需更新",
            "plan": plan,
            "steps_to_run": [],
        }

    return {
        "ok": True,
        "noop": False,
        "plan": plan,
        "steps_to_run": steps_to_run,
    }


async def build_smart_update_plan(
    *,
    get_conn: Callable,
    critical_only: bool,
    sync_calendar: Callable,
    build_smart_plan: Callable,
    ensure_calendar_step_for_data_fetch: Callable[[list[str]], list[str]],
) -> dict:
    """Build the executable smart-update plan while owning the DB connection."""
    conn = get_conn()
    try:
        return await prepare_smart_update_plan(
            conn,
            critical_only=critical_only,
            sync_calendar=sync_calendar,
            build_smart_plan=build_smart_plan,
            ensure_calendar_step_for_data_fetch=ensure_calendar_step_for_data_fetch,
        )
    finally:
        conn.close()


def _parse_sync_time(value: str):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _scope_rows(rows, context: Optional[dict]):
    if not rows:
        return []
    if context:
        step_ids = context.get("step_ids") or []
        if step_ids:
            selected = set(step_ids)
            scoped = [row for row in rows if row.get("step_id") in selected]
            if scoped:
                return scoped
        step_id = context.get("step_id")
        if step_id:
            scoped = [row for row in rows if row.get("step_id") == step_id]
            if scoped:
                return scoped
    return [
        row for row in rows
        if (row.get("status") and row.get("status") != "idle")
        or row.get("started_at") or row.get("finished_at") or row.get("records")
    ]


def _summarize_rows(rows):
    summary = {
        "total": len(rows),
        "done": 0,
        "completed": 0,
        "partial": 0,
        "failed": 0,
        "blocked": 0,
        "skipped": 0,
        "stopped": 0,
        "running": 0,
        "pending": 0,
        "latest_at": "",
    }
    latest_ms = 0
    for row in rows:
        status = row.get("status")
        if status in TERMINAL_STEP_STATUSES:
            summary["done"] += 1
        if status in COUNTED_STEP_STATUSES:
            summary[status] += 1
        ts = row.get("finished_at") or row.get("started_at") or ""
        parsed = _parse_sync_time(ts)
        if parsed:
            ms = parsed.timestamp()
            if ms >= latest_ms:
                latest_ms = ms
                summary["latest_at"] = ts
    summary["pct"] = round(summary["done"] / summary["total"] * 100) if summary["total"] else 0
    return summary


def _mode_label(mode: Optional[str]) -> str:
    return {
        "smart": "智能更新",
        "single": "单步更新",
        "all": "全量更新",
    }.get(mode or "", "更新")


def _format_done_counts(stat: dict) -> str:
    parts = [f"{stat.get('completed', 0)}成功"]
    if stat.get("partial", 0):
        parts.append(f"{stat.get('partial', 0)}有缺口")
    parts.append(f"{stat.get('failed', 0)}失败")
    if stat.get("blocked", 0):
        parts.append(f"{stat.get('blocked', 0)}阻断")
    if stat.get("skipped", 0):
        parts.append(f"{stat.get('skipped', 0)}已最新")
    else:
        parts.append("0已最新")
    return " · ".join(parts)


def _latest_time_pair(row) -> tuple[float, str]:
    finished = row.get("finished_at") or ""
    finished_at = _parse_sync_time(finished)
    started = row.get("started_at") or ""
    started_at = _parse_sync_time(started)
    finished_pair = (finished_at.timestamp(), finished) if finished_at else (0, "")
    started_pair = (started_at.timestamp(), started) if started_at else (0, "")
    return finished_pair if finished_pair[0] >= started_pair[0] else started_pair


def _activity_meta(items):
    active = [row for row in items if row.get("status") == "running"]
    active_names = [
        row.get("step_name") or row.get("step_id")
        for row in active
        if (row.get("step_name") or row.get("step_id"))
    ]
    latest_at = ""
    latest_ms = 0
    for row in items:
        ms, ts = _latest_time_pair(row)
        if ms >= latest_ms:
            latest_ms = ms
            latest_at = ts
    return {
        "active_step_ids": [row.get("step_id") for row in active if row.get("step_id")],
        "active_step_names": active_names,
        "latest_status_at": latest_at,
    }


def _build_status_summary(rows, running: bool, stop_requested: bool,
                          run_context: Optional[dict], last_run_context: Optional[dict]):
    if running and run_context:
        scoped = _scope_rows(rows, run_context)
        stat = _summarize_rows(scoped)
        activity = _activity_meta(scoped)
        mode = run_context.get("mode")
        label = _mode_label(mode)
        if mode == "single":
            scope_name = run_context.get("step_name") or label
            if stat["total"] > 1:
                message = f"{scope_name}续跑链路 · {stat['done']}/{stat['total']} · {stat['pct']}%"
            else:
                message = f"{scope_name} · {stat['done']}/{stat['total']} · {stat['pct']}%"
        else:
            message = f"{label} · {stat['done']}/{stat['total']} · {stat['pct']}%"
        if stop_requested:
            message = "停止中 · " + message
        if activity["active_step_names"]:
            message += " · 当前：" + " / ".join(activity["active_step_names"][:2])
        return {
            "kind": "running",
            "mode": mode,
            "show_progress": True,
            "pct": stat["pct"],
            "message": message,
            "counts": stat,
            **activity,
        }

    if last_run_context and last_run_context.get("noop"):
        return {
            "kind": "noop",
            "mode": last_run_context.get("mode"),
            "show_progress": False,
            "pct": 0,
            "message": last_run_context.get("message") or "数据已是最新，无需更新",
            "counts": {
                "total": 0,
                "done": 0,
                "completed": 0,
                "partial": 0,
                "failed": 0,
                "blocked": 0,
                "skipped": 0,
                "stopped": 0,
                "latest_at": last_run_context.get("finished_at") or "",
                "pct": 0,
            },
        }

    context = last_run_context
    scoped = _scope_rows(rows, context)
    if scoped:
        stat = _summarize_rows(scoped)
        activity = _activity_meta(scoped)
        mode = (context or {}).get("mode")
        label = _mode_label(mode)
        if mode == "single":
            title = (context or {}).get("step_name") or label
            if stat["total"] > 1:
                message = f"上次续跑 {title} · {_format_done_counts(stat)}"
            else:
                message = f"上次单步 {title} · {_format_done_counts(stat)}"
        else:
            message = f"上次{label} {_format_done_counts(stat)}"
        if stat["stopped"]:
            message += f" · {stat['stopped']}停止"
        return {
            "kind": "last",
            "mode": mode,
            "show_progress": True,
            "pct": stat["pct"],
            "message": message,
            "counts": stat,
            **activity,
        }

    return {
        "kind": "idle",
        "mode": None,
        "show_progress": False,
        "pct": 0,
        "message": "暂无更新记录",
        "counts": {
            "total": 0,
            "done": 0,
            "completed": 0,
            "partial": 0,
            "failed": 0,
            "blocked": 0,
            "skipped": 0,
            "stopped": 0,
            "running": 0,
            "pending": 0,
            "latest_at": "",
            "pct": 0,
        },
        "active_step_ids": [],
        "active_step_names": [],
        "latest_status_at": "",
    }


def build_update_status_payload(
    conn,
    *,
    running: bool,
    stop_requested: bool,
    run_context: Optional[dict],
    last_run_context: Optional[dict],
    ui_logs: list,
    last_exception: Optional[dict],
    server_time: Optional[str] = None,
) -> dict:
    """Build the `/update/status` API payload from persisted step_status rows."""

    rows = conn.execute("SELECT * FROM step_status ORDER BY step_order").fetchall()
    steps = [_sanitize_step_status_item(dict(row)) for row in rows]
    summary = _build_status_summary(steps, running, stop_requested, run_context, last_run_context)
    return {
        "running": running,
        "stop_requested": stop_requested,
        "run_context": dict(run_context) if run_context else None,
        "last_run_context": dict(last_run_context) if last_run_context else None,
        "summary": summary,
        "steps": steps,
        "logs": ui_logs,
        "last_exception": dict(last_exception) if last_exception else None,
        "server_time": server_time or datetime.now().isoformat(),
    }


def build_update_status_response(
    *,
    get_conn: Callable,
    sync_step_status_catalog: Callable,
    running: bool,
    stop_requested: bool,
    run_context: Optional[dict],
    last_run_context: Optional[dict],
    ui_logs: list,
    last_exception: Optional[dict],
) -> dict:
    """Build `/update/status` response while owning the read connection lifecycle."""
    conn = get_conn()
    try:
        sync_step_status_catalog(conn)
        return build_update_status_payload(
            conn,
            running=running,
            stop_requested=stop_requested,
            run_context=run_context,
            last_run_context=last_run_context,
            ui_logs=ui_logs,
            last_exception=last_exception,
        )
    finally:
        conn.close()


def _dependency_edges(valid_ids: set, *dependency_maps: dict):
    return [
        (child, dep)
        for mapping in dependency_maps
        for child, deps in mapping.items()
        if child in valid_ids
        for dep in deps
        if dep in valid_ids
    ]


def _collect_downstream_steps(start_step_id, steps, hard_deps, soft_deps, manual_only_steps):
    """Return affected downstream steps in DAG order, including the start step."""
    valid_ids = {step["id"] for step in steps}
    reverse = {sid: set() for sid in valid_ids}
    for child, dep in _dependency_edges(valid_ids, hard_deps, soft_deps):
        reverse[dep].add(child)

    seen = {start_step_id}
    queue = deque([start_step_id])
    while queue:
        current = queue.popleft()
        unseen = reverse.get(current, set()) - seen
        seen.update(unseen)
        queue.extend(unseen)

    if start_step_id not in manual_only_steps:
        seen -= manual_only_steps
    return [step["id"] for step in steps if step["id"] in seen]
