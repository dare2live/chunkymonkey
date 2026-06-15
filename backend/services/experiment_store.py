"""实验留档统一写入器 — 把"实验结果落 L4 experiment_store"固化进流程 (非靠人记/文档)。

缘起 (2026-06-15 用户): Phase B 3 实验各自 print+散落 JSON, 漏进 experiment_store (隔离 L4 实验层)。
根治: 共享写入器, 每个实验 import + 调 → 结果自动落 4 留档表 (fact_consumer_alpha_ic_scan /
fact_experiment_verdict / experiment_pit_audit_log / pipeline_artifact_lineage)。想漏都漏不掉。
schema owner=build_experiment_store.py; 路径走 database_manifest (模块化分层: experiment tier 隔离 live)。

用法 (每个实验 main 末尾):
    from services.experiment_store import open_store, record_ic_cells, record_pit_check, record_verdict, record_artifact
    with open_store() as st:
        record_pit_check(st, run_id=rid, step="pit_selfcheck", check_name="subpattern_classifier", passed=ok, detail=pit)
        record_ic_cells(st, run_id=rid, data_snapshot=snap, cells=cells)   # cells={cid:{oos_rank_ic,ic_ir,n_days}}
        record_verdict(st, run_id=rid, family="conditional_segment", verdict="REAL_EDGE", judges={...})
        record_artifact(st, run_id=rid, artifact_path=out_json)
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from services.duck_adapter import connect as duck_connect

_REPO = Path(__file__).resolve().parents[2]


def store_path() -> Path:
    """experiment_store 路径走 database_manifest (模块化分层真相源); fallback 默认。"""
    try:
        import yaml
        m = yaml.safe_load((_REPO / "backend" / "config" / "database_manifest.yaml").read_text(encoding="utf-8"))
        rel = m["databases"]["experiment_store"]["path"]
        return _REPO / rel
    except Exception:
        return _REPO / "data" / "experiment_store.duckdb"  # rule-compliance: ok evidence=manifest 读失败时 fallback (主路径走 database_manifest)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def open_store(read_only: bool = False) -> Iterator[Any]:
    """打开 experiment_store (中央 duck_adapter, 写完 CHECKPOINT)。隔离 L4 实验层, 不碰 live。"""
    conn = duck_connect(str(store_path()), read_only=read_only)
    try:
        if not read_only:
            conn.execute("SET enable_progress_bar=false")
        yield conn
        if not read_only:
            conn.execute("CHECKPOINT")
    finally:
        conn.close()


def record_ic_cell(conn, *, run_id: str, data_snapshot: str, consumer_id: str,
                   metric: str, value: float | None, n_windows: int | None) -> None:
    if value is None:
        return
    conn.execute(
        "INSERT OR REPLACE INTO fact_consumer_alpha_ic_scan "
        "(data_snapshot, consumer_id, metric, value, n_windows, run_id, built_at) VALUES (?,?,?,?,?,?,?)",
        [data_snapshot, consumer_id, metric, float(value), n_windows, run_id, _utc()])


def record_ic_cells(conn, *, run_id: str, data_snapshot: str, cells: dict[str, dict]) -> int:
    """cells = {consumer_id: {oos_rank_ic, ic_ir, n_days|n_windows}} -> 每 metric 一行。返回写入行数。"""
    n = 0
    for cid, c in cells.items():
        nw = c.get("n_windows", c.get("n_days"))
        for metric in ("oos_rank_ic", "ic_ir"):
            if c.get(metric) is not None:
                record_ic_cell(conn, run_id=run_id, data_snapshot=data_snapshot,
                               consumer_id=cid, metric=metric, value=c[metric], n_windows=nw)
                n += 1
    return n


def record_pit_check(conn, *, run_id: str, step: str, check_name: str, passed: bool, detail: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO experiment_pit_audit_log (log_id, run_id, step, check_name, passed, detail_json, ts) "
        "VALUES (?,?,?,?,?,?,?)",
        [f"{run_id}_{step}", run_id, step, check_name, 1 if passed else 0,
         json.dumps(detail, ensure_ascii=False, default=str), _utc()])


# C-R1 转正章证据键 (judges 顶层或一层嵌套含任一非空 = 看过钱). 防缺陷 N3: 转正纯凭 IC 置换显著性盖章,
# 系统最强章 (confirmed_by_owner) 盖时完全没看过含成本绝对收益。owner=docs/strategy_validation_contract.md C-R1。
_MONEY_EVIDENCE_KEYS = ("cost_aware_net_return", "net_annual_return", "kpi_verdict", "tradability", "kpi_passes")


def _has_money_evidence(judges: dict | None) -> bool:
    """judges 顶层或一层嵌套是否含含成本绝对收益证据 (任一证据键非空)。"""
    if not judges:
        return False
    def _hit(d: dict) -> bool:
        return any(d.get(k) not in (None, {}, "") for k in _MONEY_EVIDENCE_KEYS)
    if _hit(judges):
        return True
    return any(isinstance(v, dict) and _hit(v) for v in judges.values())


def record_verdict(conn, *, run_id: str, family: str, verdict: str, prereg_hash: str = "",
                   judges: dict | None = None, gate_blockers: dict | None = None,
                   confirmed_by_owner: int = 0) -> None:
    # C-R1 死亡条款 (自欺死): 转正章必须看过钱, 不许纯凭 IC 显著性盖 (缺陷 N3)
    if confirmed_by_owner and not _has_money_evidence(judges):
        raise ValueError(
            "C-R1 BLOCK: confirmed_by_owner=1 须带含成本绝对收益证据 (judges 含 "
            f"{'/'.join(_MONEY_EVIDENCE_KEYS)} 任一非空), 不许纯凭 IC 置换显著性转正 (缺陷 N3 / 自欺死)。"
            " IC 真 != 能赚钱: 排序显著的 cohort 可整体崩盘 (Phase B 实证 33σ 仍 gross -34.6%)。")
    conn.execute(
        "INSERT OR REPLACE INTO fact_experiment_verdict "
        "(verdict_id, family, run_id, verdict, ts, prereg_hash, judges_json, gate_blockers_json, confirmed_by_owner) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [run_id, family, run_id, verdict, _utc(), prereg_hash or "",
         json.dumps(judges or {}, ensure_ascii=False, default=str),
         json.dumps(gate_blockers or {}, ensure_ascii=False, default=str), confirmed_by_owner])


def record_artifact(conn, *, run_id: str, artifact_path: str | Path,
                    input_tables_hash: str = "", output_tables_hash: str = "") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO pipeline_artifact_lineage "
        "(artifact_id, run_id, input_tables_hash, output_tables_hash, artifact_path, built_at) VALUES (?,?,?,?,?,?)",
        [f"{run_id}_artifact", run_id, input_tables_hash, output_tables_hash, str(artifact_path), _utc()])
