from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routers import ops_manual_run


def test_daily_update_remains_registered_through_existing_manual_wrapper():
    """Retiring launchd must not retire the frontend/manual execution path."""
    assert "daily_update" in ops_manual_run.MANUAL_JOBS
    assert ops_manual_run.MANUAL_JOBS["daily_update"]["label"] == (
        "数据底座五段手动链 (preflight/获取/清洗/加工/存储)"
    )
    assert ops_manual_run._WRAPPER.name == "manual_job_wrapper.py"
    assert ops_manual_run._WRAPPER.is_file()


def test_run_job_ignores_stale_process_hint_when_writer_lock_is_free(monkeypatch):
    """pgrep 可能撞到旧/无关进程；真实 flock 空闲时不能误拒绝手动任务。"""
    monkeypatch.setattr(
        ops_manual_run,
        "writer_lock_status",
        lambda: SimpleNamespace(busy=False, owner=None, owner_pid=None),
        raising=False,
    )
    monkeypatch.setattr(ops_manual_run, "_is_running", lambda _spec: True)
    monkeypatch.setattr(ops_manual_run, "_spawn", lambda _job, _spec: 4321)

    assert ops_manual_run.run_job("daily_update") == {
        "job": "daily_update",
        "accepted": True,
        "pid": 4321,
    }


def test_run_job_rejects_real_writer_lock_even_when_process_hint_is_false(monkeypatch):
    """flock busy 才是 409 真相；pgrep 假阴性不能放行第二个 writer。"""
    monkeypatch.setattr(
        ops_manual_run,
        "writer_lock_status",
        lambda: SimpleNamespace(busy=True, owner="pipeline", owner_pid=2468),
    )
    monkeypatch.setattr(ops_manual_run, "_is_running", lambda _spec: False)
    monkeypatch.setattr(
        ops_manual_run,
        "_spawn",
        lambda *_args: pytest.fail("busy lock must block before spawn"),
    )

    with pytest.raises(HTTPException) as exc_info:
        ops_manual_run.run_job("daily_update")

    assert exc_info.value.status_code == 409
    assert "owner=pipeline pid=2468" in exc_info.value.detail


def test_status_exposes_writer_lock_as_authority_and_pgrep_as_hint(tmp_path, monkeypatch):
    spec = dict(ops_manual_run.MANUAL_JOBS["daily_update"])
    spec["log"] = str(tmp_path / "missing.log")
    spec["extra_flags"] = []
    monkeypatch.setattr(ops_manual_run, "_FLAG_DIR", tmp_path)
    monkeypatch.setattr(
        ops_manual_run,
        "writer_lock_status",
        lambda: SimpleNamespace(busy=False, owner=None, owner_pid=None),
    )
    monkeypatch.setattr(ops_manual_run, "_is_running", lambda _spec: True)
    monkeypatch.setattr(
        ops_manual_run,
        "_due_plan_preview",
        lambda **_k: {
            "source": "data/audit/watermark_sla_before_20260722.json",
            "as_of": "2026-07-22T01:00:00Z",
            "snapshot_kind": "preflight",
            "label": "跑前预检快照",
            "items": [
                {
                    "domain": "ths_hot",
                    "watermark": "20260720",
                    "days_ago": 2,
                    "will_fetch": True,
                }
            ],
        },
    )

    payload = ops_manual_run._status_payload("daily_update", spec)

    # daily_update: process_hint alone counts as running (chain alive).
    assert payload["running"] is True
    assert payload["writer_busy"] is False
    assert payload["owner"] is None and payload["owner_pid"] is None
    assert payload["process_hint_running"] is True
    assert "current_activity" in payload
    assert payload["current_activity"]["phase"] in {"running", "idle"}
    assert payload["due_plan"]["items"][0]["domain"] == "ths_hot"
    assert payload["due_plan"]["items"][0]["will_fetch"] is True


def test_step_job_idle_when_global_writer_busy_elsewhere(tmp_path, monkeypatch):
    """Global flock held by daily_update must NOT paint every step card as running."""
    spec = dict(ops_manual_run.MANUAL_JOBS["pipeline_acquire"])
    spec["log"] = str(tmp_path / "missing_acquire.log")
    spec["extra_flags"] = []
    monkeypatch.setattr(ops_manual_run, "_FLAG_DIR", tmp_path)
    monkeypatch.setattr(
        ops_manual_run,
        "writer_lock_status",
        lambda: SimpleNamespace(busy=True, owner="pipeline.run", owner_pid=16662),
    )
    monkeypatch.setattr(ops_manual_run, "_is_running", lambda _spec: False)

    payload = ops_manual_run._status_payload("pipeline_acquire", spec)
    act = payload["current_activity"]
    assert payload["running"] is False
    assert payload["process_hint_running"] is False
    assert payload["writer_busy"] is True
    assert payload["owner_pid"] == 16662  # lock truth still exposed for 409 UI
    assert act["phase"] == "idle"
    assert "正在" not in act["summary"]
    assert "本 job 未跑" in act["summary"]
    assert "16662" in act["summary"]  # occupancy note, not「正在运行」


def test_live_daily_update_hides_previous_run_outcome(tmp_path, monkeypatch):
    """Mid-run must not paint prior report run_outcome as current (hard_fail flicker)."""
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "daily_20260721.json").write_text(
        __import__("json").dumps(
            {
                "date": "20260721",
                "run_outcome": "hard_fail",
                "run_outcome_label": "硬失败",
                "run_outcome_reason": "hard_tier0",
            }
        ),
        encoding="utf-8",
    )
    spec = dict(ops_manual_run.MANUAL_JOBS["daily_update"])
    log = tmp_path / "daily.log"
    log.write_text(
        "[20:41:18] === ① 获取 ACQUIRE (纯采集 →L0, 不计算) ===\n",
        encoding="utf-8",
    )
    spec["log"] = str(log)
    spec["extra_flags"] = []
    monkeypatch.setattr(ops_manual_run, "_FLAG_DIR", tmp_path)
    monkeypatch.setattr(ops_manual_run, "_REPO", tmp_path)
    monkeypatch.setattr(
        ops_manual_run,
        "writer_lock_status",
        lambda: SimpleNamespace(busy=True, owner="pipeline.run", owner_pid=99),
    )
    monkeypatch.setattr(ops_manual_run, "_is_running", lambda _spec: True)
    monkeypatch.setattr(
        ops_manual_run,
        "_due_plan_preview",
        lambda **_k: {"source": None, "as_of": None, "items": [], "snapshot_kind": None, "label": None},
    )

    payload = ops_manual_run._status_payload("daily_update", spec)
    assert "run_outcome" not in payload or payload.get("run_outcome") is None
    assert "正在" in payload["current_activity"]["summary"]
    assert payload["current_activity"]["phase"] == "acquire"


def test_due_plan_preview_marks_lagged_all_due_domains(tmp_path, monkeypatch):
    audit = tmp_path / "data" / "audit"
    audit.mkdir(parents=True)
    sla = audit / "watermark_sla_before_20260722.json"
    sla.write_text(
        __import__("json").dumps(
            {
                "run_at": "2026-07-22T01:00:00Z",
                "sources": [
                    {
                        "data_domain": "sync:ths_hot",
                        "watermark_date": "20260720",
                        "watermark_days_ago": 2,
                        "sla_days": 2,
                        "status": "NO_PROBE_RULE",
                    },
                    {
                        "data_domain": "sync:daily",
                        "watermark_date": "20260721",
                        "watermark_days_ago": 1,
                        "status": "OK",
                    },
                    {
                        "data_domain": "sync:moneyflow_dc",
                        "watermark_date": "20260722",
                        "watermark_days_ago": 0,
                        "status": "OK",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ops_manual_run, "_REPO", tmp_path)
    monkeypatch.setattr(
        ops_manual_run,
        "_org_holding_due_item",
        lambda: {
            "domain": "org_holding",
            "watermark": "2026-03-31",
            "days_ago": 0,
            "status": "ok",
            "will_fetch": False,
            "kind": "period_incremental",
            "action": "skip_current",
            "detail": "plannable present",
        },
    )
    plan = ops_manual_run._due_plan_preview()
    domains = {row["domain"]: row for row in plan["items"]}
    assert "org_holding" in domains
    assert domains["org_holding"]["kind"] == "period_incremental"
    assert "ths_hot" in domains
    assert domains["ths_hot"]["will_fetch"] is True
    assert domains["ths_hot"]["watermark"] == "20260720"
    assert domains["daily"]["will_fetch"] is False
    assert "moneyflow_dc" not in domains
    assert plan["snapshot_kind"] == "preflight"


def test_org_due_row_from_gap_surfaces_skip_not_forever_blocked():
    row = ops_manual_run._org_due_row_from_gap(
        {
            "action": "skip_current",
            "plannable": "2026-03-31",
            "status": "ok",
            "next_period": "2026-06-30",
            "next_period_unlock": "2026-08-31",
        },
        source="unit",
    )
    assert row["domain"] == "org_holding"
    assert row["will_fetch"] is False
    assert row["kind"] == "period_incremental"
    assert "not forever blocked" in row["detail"]
    assert row["next_period"] == "2026-06-30"


def test_due_plan_prefers_newer_post_acquire_over_before(tmp_path, monkeypatch):
    audit = tmp_path / "data" / "audit"
    audit.mkdir(parents=True)
    before = audit / "watermark_sla_before_20260722.json"
    after = audit / "watermark_sla_20260722.json"
    before.write_text(
        __import__("json").dumps(
            {
                "run_at": "2026-07-22T12:41:00+00:00",
                "sources": [
                    {
                        "data_domain": "sync:express",
                        "watermark_date": "20221231",
                        "watermark_days_ago": 1299,
                        "status": "NO_PROBE_RULE",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    after.write_text(
        __import__("json").dumps(
            {
                "run_at": "2026-07-22T12:48:00+00:00",
                "sources": [
                    {
                        "data_domain": "sync:ths_hot",
                        "watermark_date": "20260721",
                        "watermark_days_ago": 1,
                        "status": "OK",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    # Ensure post file is newer on mtime.
    import os
    import time

    os.utime(before, (time.time() - 100, time.time() - 100))
    os.utime(after, (time.time(), time.time()))
    monkeypatch.setattr(ops_manual_run, "_REPO", tmp_path)
    monkeypatch.setattr(ops_manual_run, "_org_holding_due_item", lambda: None)
    plan = ops_manual_run._due_plan_preview()
    assert plan["snapshot_kind"] == "post_acquire"
    assert plan["as_of"] == "2026-07-22T12:48:00+00:00"
    assert plan["items"][0]["domain"] == "ths_hot"

def test_current_activity_prefers_latest_phase_over_prior_fail(tmp_path, monkeypatch):
    log = tmp_path / "daily.log"
    log.write_text(
        "\n".join(
            [
                "[20:42:13] DEGRADED: PREFLIGHT BLOCK: sync_execution_blocked:margin:scope_blocked",
                "[20:42:13] FAIL rc=4 job=daily_update",
                "[21:53:18] === ChunkyMonkey daily update 20260721 ===",
                "[21:53:19] --- Preflight: watermark SLA 新鲜度检查 ---",
                "[21:53:23] === ① 获取 ACQUIRE (纯采集 →L0, 不计算) ===",
            ]
        ),
        encoding="utf-8",
    )
    flag = tmp_path / "chunkymonkey_ALERT_daily_update.flag"
    flag.write_text(
        "[20:42:13] DEGRADED: PREFLIGHT BLOCK: sync_execution_blocked:margin:scope_blocked\n",
        encoding="utf-8",
    )
    spec = dict(ops_manual_run.MANUAL_JOBS["daily_update"])
    spec["log"] = str(log)
    spec["extra_flags"] = []
    monkeypatch.setattr(ops_manual_run, "_FLAG_DIR", tmp_path)
    monkeypatch.setattr(
        ops_manual_run,
        "writer_lock_status",
        lambda: SimpleNamespace(busy=True, owner="pipeline.run", owner_pid=3299),
    )
    monkeypatch.setattr(ops_manual_run, "_is_running", lambda _spec: True)

    payload = ops_manual_run._status_payload("daily_update", spec)
    act = payload["current_activity"]
    assert act["phase"] == "acquire"
    assert "正在: ① 获取 ACQUIRE" in act["summary"]
    assert payload["alert_summary"] and "PREFLIGHT BLOCK" in payload["alert_summary"]
    assert "ACQUIRE" in (act["progress_line"] or "")


def test_pipeline_stage_jobs_registered_for_capability_e():
    """Independent stage cards must call real chunkyctl pipeline/derive jobs."""
    for job in (
        "pipeline_acquire",
        "pipeline_clean",
        "pipeline_process",
        "pipeline_store",
        "derive_qfq",
    ):
        assert job in ops_manual_run.MANUAL_JOBS
        argv = ops_manual_run.MANUAL_JOBS[job]["argv"]
        assert argv[0].endswith("chunkyctl") or "chunkyctl" in str(argv[0])


def test_pipeline_nodes_catalog_marks_parameterized_s1_s2_disabled(monkeypatch):
    monkeypatch.setattr(
        ops_manual_run,
        "writer_lock_status",
        lambda: SimpleNamespace(busy=False, owner=None, owner_pid=None),
    )
    monkeypatch.setattr(ops_manual_run, "_is_running", lambda _spec: False)

    payload = ops_manual_run.pipeline_nodes()
    assert payload["primary_job"] == "daily_update"
    by_id = {n["id"]: n for n in payload["nodes"]}

    assert by_id["acquire"]["runnable"] is True
    assert by_id["acquire"]["job"] == "pipeline_acquire"
    assert by_id["acquire"]["status"]["job"] == "pipeline_acquire"

    assert by_id["land_accept"]["runnable"] is True
    assert by_id["land_accept"]["parameterized"] is True
    assert by_id["land_accept"]["job"] == "sync_land_accept"
    assert by_id["land_accept"]["status"]["job"] == "sync_land_accept"
    schema = by_id["land_accept"]["params_schema"]
    assert "daily" in schema["domains"]
    assert "land_only" in schema["modes"]

    assert by_id["preflight"]["runnable"] is False
    assert by_id["derive"]["job"] == "derive_qfq"
    assert by_id["store"]["runnable"] is True


def test_build_land_accept_argv_whitelist():
    argv = ops_manual_run.build_land_accept_argv(
        {
            "domain": "daily",
            "mode": "land_then_accept",
            "start": "20260720",
            "end": "20260721",
            "from_local_raw": True,
        }
    )
    assert "--domain" in argv and "daily" in argv
    assert "--land-then-accept" in argv
    assert "--from-local-raw" in argv
    assert argv[argv.index("--start") + 1] == "20260720"

    batch_argv = ops_manual_run.build_land_accept_argv(
        {"domain": "stock_st", "mode": "accept_from_landing", "batch_id": "st:1"}
    )
    assert "--accept-from-landing" in batch_argv
    assert batch_argv[batch_argv.index("--batch-id") + 1] == "st:1"


def test_build_land_accept_argv_rejects_wide_window_and_unknown_domain():
    with pytest.raises(HTTPException) as wide:
        ops_manual_run.build_land_accept_argv(
            {
                "domain": "daily",
                "mode": "land_only",
                "start": "20260101",
                "end": "20260315",
            }
        )
    assert wide.value.status_code == 400

    with pytest.raises(HTTPException) as bad_dom:
        ops_manual_run.build_land_accept_argv(
            {
                "domain": "ths_hot",
                "mode": "land_only",
                "start": "20260721",
                "end": "20260721",
            }
        )
    assert bad_dom.value.status_code == 400


def test_parameterized_job_rejects_bare_run(monkeypatch):
    monkeypatch.setattr(
        ops_manual_run,
        "writer_lock_status",
        lambda: SimpleNamespace(busy=False, owner=None, owner_pid=None),
    )
    with pytest.raises(HTTPException) as exc:
        ops_manual_run.run_job("sync_land_accept")
    assert exc.value.status_code == 400
    assert "land-accept" in str(exc.value.detail)
