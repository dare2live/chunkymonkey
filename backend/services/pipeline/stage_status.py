"""§8 切片 b/c-lite: 阶段状态 (复用 mart_pipeline_run_manifest, 不新建中间表)。

架构裁决 (docs/MASTER_TOPLEVEL_DESIGN.md §5.2): 不为阶段状态新建第二张表；现有 manifest
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


# ── 件2: 阶段执行 + best-effort 记状态 (run.py 全链 / stage_runner 单跑 共用) ──

def _clean_gate_result() -> str | None:
    """clean 阶段的 gate_result = data_audit overall (data/reports/data_audit_latest.json)。读不到返 None。"""
    try:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parents[3] / "data" / "reports" / "data_audit_latest.json"
        if p.exists():
            return str(json.loads(p.read_text(encoding="utf-8")).get("overall") or "") or None
    except Exception:  # noqa: BLE001 — gate_result 是附加信息, 读失败不影响状态记录
        return None
    return None


def _record_stage_best_effort(ctx, stage: str, status: str, *, gate_result: str | None = None) -> None:
    """开 smartmoney conn 记一行阶段状态。**best-effort**: 写失败 try/except 不破链 (阶段已跑完,
    状态记录是附加观测); dry-run 跳过 (不写 DB)。阶段跑完后才写 = 不与阶段并发写 smartmoney。"""
    if getattr(ctx, "dry", False):
        ctx.log(f"  [stage_status] DRY: 跳记状态 {stage}={status}")
        return
    try:
        from services.db_connection import get_conn
        conn = get_conn()
        try:
            record_stage(conn, stage, status, gate_result=gate_result)
        finally:
            conn.close()
        ctx.log(f"  [stage_status] {stage} → {status}" + (f" (gate={gate_result})" if gate_result else ""))
    except Exception as e:  # noqa: BLE001 — 记状态失败绝不破链 (阶段本身已成功)
        ctx.log(f"  [stage_status] 记状态失败(不破链, 阶段已跑完): {stage}={status} ({e})")


def run_and_record(ctx, stage: str, fn) -> bool:
    """跑阶段 fn(ctx) + best-effort 记 check_pass/check_fail。返回 True=本阶段无新 degraded。

    check_pass/fail 判定 = 本阶段是否新增 ctx.degraded_msgs (与 run.py/context degraded 续跑模型一致,
    不改变阶段失败行为, 只附加状态记录)。
    CX-1: also records wall-clock seconds onto ``ctx.stage_timing_s[stage]``.
    """
    import time

    before = len(ctx.degraded_msgs)
    t0 = time.perf_counter()
    try:
        fn(ctx)
    except Exception:
        # 硬崩 (非 degrade 续跑路径) 也留状态痕迹再抛 — 否则 manifest 缺该阶段行, 崩溃在
        # 状态面不可见 (全栈审计LOW)。gate_result 不附: 崩溃时 clean gate 产物可能是上轮残留。
        elapsed = round(time.perf_counter() - t0, 3)
        try:
            timings = getattr(ctx, "stage_timing_s", None)
            if timings is None:
                ctx.stage_timing_s = {}
                timings = ctx.stage_timing_s
            timings[stage] = elapsed
        except Exception:  # noqa: BLE001
            pass
        _record_stage_best_effort(ctx, stage, STATUS_CHECK_FAIL)
        raise
    elapsed = round(time.perf_counter() - t0, 3)
    try:
        timings = getattr(ctx, "stage_timing_s", None)
        if timings is None:
            ctx.stage_timing_s = {}
            timings = ctx.stage_timing_s
        timings[stage] = elapsed
        # Exclude prior "total" key so multi-stage sums do not compound.
        timings["total"] = round(
            sum(
                float(v)
                for k, v in timings.items()
                if k != "total" and v is not None
            ),
            3,
        )
    except Exception:  # noqa: BLE001 — timing is observational
        pass
    passed = len(ctx.degraded_msgs) == before
    status = STATUS_CHECK_PASS if passed else STATUS_CHECK_FAIL
    gate = _clean_gate_result() if stage == "clean" else None
    _record_stage_best_effort(ctx, stage, status, gate_result=gate)
    return passed
