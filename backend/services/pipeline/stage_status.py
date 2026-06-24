"""§8 切片 b/c-lite: 阶段状态 (复用 mart_pipeline_run_manifest, 不新建中间表)。

grill 裁决 (analysis/stage_status_design_20260625.md): 蓝图字面要新表 pipeline_stage_status, 但 manifest
已是 run 级状态账本(run_id/pipeline_name/status/started_at/gate_result)且 pipeline 步已在写它 → 复用 manifest
(pipeline_name=`pipeline.stage.<s>`), 不新建第二个状态中间表(单一真相源, 同 universe 由 K线派生不建 dim 表 /
survivorship 真相在 K线不建枚举表 的教训)。"stale"(上游重跑→下游过时)= 派生计算(upstream.started_at >
this.started_at), 不存 flag。

本模块 = 纯 conn-参数化读写函数 (件1, 单测覆盖)。run.py/stage_runner 接线 + chunkyctl upstream-gate = 件2/3。
前端阶段卡片(状态机真消费方)= 产品面 defer (范围 escalate 用户)。
"""
from __future__ import annotations

from typing import Any

from services.pipeline_manifest import (
    ensure_pipeline_manifest_schema,
    record_pipeline_run,
    utc_now_iso,
)

# 固定线性序 (blueprint §8.3 不上通用 DAG); 与 stage_runner.STAGES 同序
STAGE_ORDER: tuple[str, ...] = ("acquire", "clean", "process", "store")
_PIPELINE_PREFIX = "pipeline.stage."

# 阶段验收门状态 (蓝图 §8.1 status 集的落地子集; manifest.status 是自由 TEXT, 按 pipeline_name 命名空间隔离)
STATUS_CHECK_PASS = "check_pass"
STATUS_CHECK_FAIL = "check_fail"
STATUS_RUNNING = "running"


def _pipeline_name(stage: str) -> str:
    return f"{_PIPELINE_PREFIX}{stage}"


def record_stage(
    conn,
    stage: str,
    status: str,
    *,
    gate_result: str | None = None,
    started_at: str | None = None,
    run_id: str | None = None,
    blockers: list[str] | None = None,
) -> None:
    """记一行阶段状态到 manifest (pipeline_name=pipeline.stage.<stage>)。复用 record_pipeline_run。

    run_id 唯一(按 stage+时间), 故 INSERT OR REPLACE 不覆盖历史 — 每次跑一行, 查最新即当前状态。
    """
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage '{stage}' (choices: {', '.join(STAGE_ORDER)})")
    ts = started_at or utc_now_iso()
    record_pipeline_run(
        conn,
        run_id=run_id or f"stage.{stage}.{ts}",
        pipeline_name=_pipeline_name(stage),
        status=status,
        started_at=ts,
        ended_at=ts,
        gate_result=gate_result,
        blockers=blockers,
    )


def _latest_per_stage(conn) -> dict[str, dict[str, Any]]:
    """各 stage 最新 manifest 行 (started_at DESC, created_at tie-break)。"""
    ensure_pipeline_manifest_schema(conn)
    out: dict[str, dict[str, Any]] = {}
    for stage in STAGE_ORDER:
        row = conn.execute(
            """
            SELECT status, started_at, gate_result
              FROM mart_pipeline_run_manifest
             WHERE pipeline_name = ?
             ORDER BY started_at DESC NULLS LAST, created_at DESC
             LIMIT 1
            """,
            [_pipeline_name(stage)],
        ).fetchone()
        if row is None:
            out[stage] = {"status": "not_run", "started_at": None, "gate_result": None}
        else:
            out[stage] = {"status": row[0], "started_at": row[1], "gate_result": row[2]}
    return out


def get_stage_status(conn) -> dict[str, dict[str, Any]]:
    """各 stage 当前状态 + 派生 stale (任一上游 started_at > 本阶段 started_at = 上游已重跑, 本阶段过时)。"""
    latest = _latest_per_stage(conn)
    result: dict[str, dict[str, Any]] = {}
    for i, stage in enumerate(STAGE_ORDER):
        info = dict(latest[stage])
        stale = False
        my_ts = info.get("started_at")
        if my_ts is not None:
            for up in STAGE_ORDER[:i]:
                up_ts = latest[up].get("started_at")
                if up_ts is not None and str(up_ts) > str(my_ts):
                    stale = True
                    break
        info["stale"] = stale
        result[stage] = info
    return result


def upstream_status(conn, stage: str) -> dict[str, Any] | None:
    """紧邻上游 stage 的当前状态 dict; stage 是首阶段(acquire)返 None(无上游)。"""
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage '{stage}'")
    idx = STAGE_ORDER.index(stage)
    if idx == 0:
        return None
    return get_stage_status(conn)[STAGE_ORDER[idx - 1]]


def upstream_ok(conn, stage: str) -> bool:
    """上游是否 check_pass (首阶段恒 True; refuse-if-upstream-not-pass 门用)。"""
    up = upstream_status(conn, stage)
    if up is None:
        return True
    return up.get("status") == STATUS_CHECK_PASS
