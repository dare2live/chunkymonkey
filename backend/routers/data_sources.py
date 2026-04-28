"""数据源 registry 的 HTTP 接口.

GET /api/data_sources/list                  → 全部 source + capability 列表
GET /api/data_sources/health                → 全部 source healthcheck (会真请求, 慢)
GET /api/data_sources/{name}/health         → 单 source healthcheck
GET /api/data_sources/capabilities          → capability → source 映射表 (priority chain)
POST /api/data_sources/{cap}/test           → 试用某 capability (调试用)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.data_sources import (
    get_registry,
    healthcheck_all,
    resolve,
)


logger = logging.getLogger("cm-api.data_sources")
router = APIRouter()


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------

@router.get("/list")
def list_data_sources():
    """列出全部 source + 每个的 capability 清单 + 缓存的 health.

    不会触发真实 healthcheck (用 /health 端点).
    """
    reg = get_registry()
    out = []
    for src in reg.list_sources():
        out.append({
            "name": src.name,
            "display_name": src.display_name,
            "priority": src.priority,
            "repo_url": src.repo_url,
            "capabilities": [
                {
                    "name": c.name,
                    "description": c.description,
                    "freshness": c.freshness,
                    "cost": c.cost,
                    "fields": c.fields,
                    "notes": c.notes,
                }
                for c in src.capabilities
            ],
            "health": src._health.to_dict(),
            "telemetry": src.telemetry,
        })
    return {"sources": out, "total": len(out)}


@router.get("/schema_versions")
def list_schema_versions_endpoint():
    """派生层 schema_version 状态 (P0.1).

    返回:
      - 总数 / drift 数量 / 各 layer 分布
      - 每张表的 expected_version / actual_version / rebuilt_at / drift 标记
    """
    from services.db import get_conn
    from services.schema_versions import list_all_versions, summary
    conn = get_conn()
    try:
        versions = list_all_versions(conn)
        drift_count = sum(1 for v in versions if v["drift"])
        return {
            "summary": {
                **summary(),
                "drift_count": drift_count,
            },
            "versions": versions,
        }
    finally:
        conn.close()


@router.post("/schema_versions/record_baseline")
def record_baseline_endpoint():
    """把所有派生表的 actual_version 设为 expected (用户重算后用)."""
    from services.db import get_conn
    from services.schema_versions import record_all_baselines
    conn = get_conn()
    try:
        n = record_all_baselines(conn)
        return {"ok": True, "recorded": n}
    finally:
        conn.close()


@router.post("/data_audit/run")
def run_data_audit():
    """跑表级数据完整性审计 (P0.2). 返回 + 落库 mart_data_audit_report."""
    from services.db import get_conn
    from services.data_audit import audit_all, save_audit_report, summary
    conn = get_conn()
    try:
        results = audit_all(conn)
        run_id = save_audit_report(conn, results)
        n_ok = sum(1 for r in results if not r.get("issues"))
        n_error = sum(1 for r in results if any(i["level"] == "error" for i in r.get("issues", [])))
        n_warn = sum(
            1 for r in results
            if any(i["level"] == "warn" for i in r.get("issues", []))
            and not any(i["level"] == "error" for i in r.get("issues", []))
        )
        return {
            "ok": True,
            "run_id": run_id,
            "summary": {
                **summary(),
                "n_tables": len(results),
                "n_ok": n_ok,
                "n_warn": n_warn,
                "n_error": n_error,
            },
            "results": results,
        }
    finally:
        conn.close()


@router.get("/data_audit/last")
def last_data_audit():
    """最近一次表级审计报告."""
    from services.db import get_conn
    from services.data_audit import load_last_audit_report, summary
    conn = get_conn()
    try:
        last = load_last_audit_report(conn)
        return {
            "summary": summary(),
            "last": last,
        }
    finally:
        conn.close()


@router.get("/data_routes")
def list_data_routes():
    """业务数据 → 实际通道 映射 (从代码反推, 不是 registry 声明).

    每条记录:
      - 业务名 (e.g. "日 K 线")
      - 实际写入表 (e.g. "market.duckdb#price_kline")
      - 实际数据源 + 协议 endpoint
      - 对应 sync step
      - status: connected (跑通) / pending (registry 声明但未接)
    """
    from services.data_sources.data_routes import get_routes, stats
    return {"routes": get_routes(), "stats": stats()}


@router.get("/capabilities")
def list_capabilities():
    """capability → 提供它的 source 列表 (按 priority).

    UI 数据 → 源映射表用此.
    """
    reg = get_registry()
    out = []
    for cap_name, srcs in reg.all_capabilities().items():
        # 取第一个 source 的 capability 元数据 (它们语义应一致)
        first = srcs[0]
        cap = first.get_capability(cap_name)
        out.append({
            "capability": cap_name,
            "description": cap.description if cap else "",
            "freshness": cap.freshness if cap else "unknown",
            "cost": cap.cost if cap else "low",
            "fallback_chain": [s.name for s in srcs],
            "primary_source": srcs[0].name,
        })
    # 按 capability 名排序
    out.sort(key=lambda x: x["capability"])
    return {"capabilities": out, "total": len(out)}


# ---------------------------------------------------------------------------
# 健康
# ---------------------------------------------------------------------------

@router.get("/health")
def healthcheck_all_sources():
    """逐个调用 source.healthcheck() — 会真实联网, 5-15s.

    返回每个 source 的 state + 最近成功时间 + 延迟.
    """
    healths = healthcheck_all()
    return {
        "results": {
            name: h.to_dict() for name, h in healths.items()
        },
    }


@router.get("/{name}/health")
def healthcheck_one(name: str):
    reg = get_registry()
    src = reg.get_source(name)
    if src is None:
        raise HTTPException(404, f"未注册的 source: {name}")
    h = src.healthcheck()
    src._health = h
    import time
    h.last_check_ts = time.time()
    return h.to_dict()


# ---------------------------------------------------------------------------
# 调用 (调试)
# ---------------------------------------------------------------------------

class TestCapabilityIn(BaseModel):
    capability: str
    prefer_source: Optional[str] = None
    kwargs: dict[str, Any] = {}


@router.post("/test")
def test_capability(body: TestCapabilityIn):
    """调试用: 调用某 capability, 返回原始数据 (头几行)."""
    try:
        data, source = resolve(
            body.capability,
            prefer_source=body.prefer_source,
            **body.kwargs,
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}")

    # 简化输出 (DataFrame / list / dict 都能展示)
    preview: Any
    try:
        import pandas as pd
        if isinstance(data, pd.DataFrame):
            preview = {
                "type": "DataFrame",
                "shape": list(data.shape),
                "columns": list(data.columns),
                "head": data.head(5).to_dict(orient="records"),
            }
        elif isinstance(data, list):
            preview = {
                "type": "list",
                "len": len(data),
                "head": data[:5],
            }
        elif isinstance(data, dict):
            preview = {
                "type": "dict",
                "keys": list(data.keys())[:20],
                "head": {k: data[k] for k in list(data.keys())[:5]},
            }
        else:
            preview = {"type": type(data).__name__, "value": str(data)[:300]}
    except Exception as exc:
        preview = {"type": "unknown", "error": str(exc)}

    return {
        "source_used": source,
        "preview": preview,
    }
