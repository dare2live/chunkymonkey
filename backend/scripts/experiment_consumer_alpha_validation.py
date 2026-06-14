#!/usr/bin/env python3
"""S0 consumer_alpha 验证执行器 (alpha 验证程序, owner=analysis/alpha_validation_program_spec_20260614.md §4-5)。

地基-reset 后重建: 复用对象 (services.optimization 中央层 / plan_validator / walk_forward
runner) 已被 reset 删, 本脚本是 config 驱动的最小脚手架, 不复活 chunkyctl god-dispatcher
(它派发的引擎本身也删了 = 想象的复杂度, architect rule6)。

链路: 读 (数据x消费者) 矩阵配置 (consumer_alpha_matrix.yaml) + experiment_jobs.yaml family
契约 -> 校验 gate (gate-before-run) -> 枚举 cell (data_candidate x mapping 命中的 consumer)
-> S0 跑 dry 空矩阵 (不计算 IC, 不写假数) -> 写 verdict/lineage/pit_audit 留档行入隔离的
experiment_store DB -> 落 verdict JSON 到 analysis/。IC 计算 + walk-forward OOS 留 S3。

死亡条款映射 (spec §2):
  判断死 -> 矩阵轴 + 映射铁律全走 config, executor 不 hardcode 任何轴/阈值。
  谄媚死 -> prereg_hash (criteria 冻结 hash) + --check-prereg 机器逐字对账。
  泄漏死 -> gate-before-run (plan().blocked_reasons) + pit_audit 每步落档 (非仅最终判决)。
  估计死 -> dry 不写假 IC (measured-not-estimated); 真 IC 须 S3 walk-forward OOS。

用法:
  # S0 dry 骨架 (默认; gate 缺失在 dry 下记 deferred 不阻断, 真跑 --execute 才硬阻断):
  python backend/scripts/experiment_consumer_alpha_validation.py \
    --input-snapshot experiment_store.duckdb@20260614 --objective "S0 scaffold" \
    --rollback-plan "wipe experiment_store consumer_alpha rows"
  # prereg 对账 (谄媚死守门):
  python ... --prereg analysis/prereg_consumer_alpha_<date>.md --check-prereg
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.experiment_jobs import load_experiment_job_contract  # noqa: E402

MANIFEST = REPO / "backend" / "config" / "database_manifest.yaml"
MATRIX_PATH = REPO / "backend" / "config" / "experiments" / "consumer_alpha_matrix.yaml"
FAMILY = "consumer_alpha_validation"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store_path() -> Path:
    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return REPO / m["databases"]["experiment_store"]["path"]


def load_matrix(path: Path = MATRIX_PATH) -> dict:
    """读 + 校验 (数据x消费者) 矩阵配置。"""
    m = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("consumers", "mapping_law", "data_candidates"):
        if not m.get(key):
            raise ValueError(f"{path.name}: 缺 {key}")
    consumer_ids = {c["id"] for c in m["consumers"]}
    for dc in m["data_candidates"]:
        for cid in dc.get("consumer_ids", []):
            if cid not in consumer_ids:
                raise ValueError(f"{path.name}: data_candidate {dc['id']} 引用未知 consumer {cid}")
    return m


def enumerate_cells(matrix: dict) -> list[dict]:
    """cell = data_candidate x (显式 consumer_ids 或 mapping_law[landing] 路由命中的 consumer)。"""
    mapping = matrix["mapping_law"]
    by_id = {c["id"]: c for c in matrix["consumers"]}
    cells: list[dict] = []
    for dc in matrix["data_candidates"]:
        cids = dc.get("consumer_ids")
        if not cids:
            routed_kind = mapping.get(dc["landing"])
            if routed_kind is None:
                raise ValueError(f"data_candidate {dc['id']}: landing {dc['landing']!r} 无 mapping_law 路由")
            cids = [c["id"] for c in matrix["consumers"] if c["kind"] == routed_kind]
        for cid in cids:
            consumer = by_id[cid]
            cells.append({
                "data_candidate": dc["id"],
                "priority": dc.get("priority"),
                "consumer_id": cid,
                "consumer_kind": consumer["kind"],
                "metrics": consumer["metrics"],
                "status": dc.get("status", "data_pending"),
            })
    return cells


def prereg_hash(matrix: dict) -> str:
    """冻结判据 hash: 矩阵轴 + 映射铁律 (judges 阈值 S3 定后并入)。canonical json 保序确定性。"""
    criteria = {
        "consumers": matrix["consumers"],
        "mapping_law": matrix["mapping_law"],
        "data_candidates": [
            {k: dc.get(k) for k in ("id", "landing", "consumer_ids", "priority")}
            for dc in matrix["data_candidates"]
        ],
    }
    blob = json.dumps(criteria, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_prereg(prereg_file: Path, expected_hash: str) -> tuple[bool, str]:
    """谄媚死守门: prereg md 里记的 prereg_hash 必须与当前 criteria hash 逐字一致。"""
    if not prereg_file.exists():
        return False, f"prereg 文件不存在: {prereg_file}"
    text = prereg_file.read_text(encoding="utf-8")
    token = "prereg_hash:"
    for line in text.splitlines():
        if token in line:
            recorded = line.split(token, 1)[1].strip().strip("`")
            if recorded == expected_hash:
                return True, "prereg_hash 一致"
            return False, f"prereg_hash 不符 (谄媚死): 记录 {recorded[:12]}.. != 当前 {expected_hash[:12]}.."
    return False, "prereg 文件无 prereg_hash 行"


def run(args: argparse.Namespace) -> int:
    matrix = load_matrix(Path(args.matrix) if args.matrix else MATRIX_PATH)
    cells = enumerate_cells(matrix)
    ph = prereg_hash(matrix)
    run_id = args.run_id or f"{FAMILY}_{args.backend}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    # gate-before-run: 经 family 契约 plan() 校验
    contract = load_experiment_job_contract()
    try:
        plan = contract.plan(
            FAMILY,
            backend_id=args.backend,
            input_snapshot=args.input_snapshot,
            objective=args.objective,
            rollback_plan=args.rollback_plan,
            gate_evidence=tuple(args.gate_evidence or ()),
        )
    except KeyError as exc:
        print(f"[BLOCK] 契约拒绝 (未知 family/backend): {exc}")
        return 2
    gate_blockers = [b for b in plan.blocked_reasons if b.startswith("missing_gate_evidence")]
    hard_blockers = [b for b in plan.blocked_reasons if not b.startswith("missing_gate_evidence")]

    # dry: gate 缺失记 deferred 不阻断 (骨架不计算无 alpha 主张, 无泄漏风险); 真跑硬阻断
    if hard_blockers:
        print(f"[BLOCK] 硬阻断 (非 gate): {hard_blockers}")
        return 2
    if not args.dry_run and gate_blockers:
        print(f"[BLOCK] --execute 真跑要求 gate evidence 齐: {gate_blockers}")
        return 2

    # prereg 对账 (谄媚死)
    prereg_status = "skipped (无 prereg)"
    if args.check_prereg:
        ok, msg = check_prereg(Path(args.prereg), ph) if args.prereg else (False, "未给 --prereg")
        prereg_status = msg
        if not ok:
            print(f"[BLOCK] prereg 对账失败: {msg}")
            return 3

    # PIT audit 步骤 (每步落档, 非仅最终判决)
    pit_steps = [
        ("matrix_axes_declared", "consumers+data_candidates+mapping_law 均声明", True),
        ("mapping_law_resolved", f"{len(cells)} cell 全部路由命中 consumer", True),
        ("no_future_info_dry", "dry 不计算不写 IC, 无 t 后信息引用", bool(args.dry_run)),
    ]
    if args.dry_run and gate_blockers:
        pit_steps.append(("gates_deferred_dry_run", f"dry 骨架延后 gate 至 S3 真跑: {gate_blockers}", True))

    verdict = "DRY_SKELETON" if args.dry_run else "PENDING"
    judges = {"status": "pending", "cells_planned": len(cells), "cells_computed": 0,
              "note": "S0 骨架不出 IC; 真值留 S3 walk-forward OOS"}
    store = Path(args.store) if args.store else _store_path()
    conn = duck_connect(str(store), read_only=False)
    try:
        conn.execute("SET enable_progress_bar=false")
        for step, detail, passed in pit_steps:
            conn.execute(
                "INSERT INTO experiment_pit_audit_log (log_id, run_id, step, check_name, passed, detail_json, ts) "
                "VALUES (?,?,?,?,?,?,?)",
                [f"{run_id}_{step}", run_id, step, step, 1 if passed else 0,
                 json.dumps({"detail": detail}, ensure_ascii=False), _utc_now()],
            )
        conn.execute(
            "INSERT INTO pipeline_artifact_lineage "
            "(artifact_id, run_id, input_tables_hash, output_tables_hash, artifact_path, built_at) "
            "VALUES (?,?,?,?,?,?)",
            [f"{run_id}_lineage", run_id, _file_hash(MATRIX_PATH),
             hashlib.sha256(json.dumps(cells, sort_keys=True).encode()).hexdigest(),
             f"analysis/consumer_alpha_verdict_{run_id}.json", _utc_now()],
        )
        # ic_scan: dry 写 0 行 (measured-not-estimated; 不造假数)
        conn.execute(
            "INSERT INTO fact_experiment_verdict "
            "(verdict_id, family, run_id, verdict, ts, prereg_hash, judges_json, gate_blockers_json, confirmed_by_owner) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [run_id, FAMILY, run_id, verdict, _utc_now(), ph,
             json.dumps(judges, ensure_ascii=False),
             json.dumps({"deferred": gate_blockers, "hard": hard_blockers}, ensure_ascii=False), 0],
        )
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    # 留档链: verdict JSON 落 analysis/ (不落 /tmp, 防原型灭失反例);
    # --store 覆盖 (测试) 时落 store 同目录, 不污染 analysis/
    out_dir = store.parent if args.store else REPO / "analysis"
    out = out_dir / f"consumer_alpha_verdict_{run_id}.json"
    out.write_text(json.dumps({
        "run_id": run_id, "family": FAMILY, "verdict": verdict, "ts": _utc_now(),
        "prereg_hash": ph, "prereg_status": prereg_status,
        "cells_planned": len(cells), "cells": cells,
        "gate_blockers_deferred": gate_blockers, "hard_blockers": hard_blockers,
        "store": store.name,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        out_label = out.relative_to(REPO)
    except ValueError:
        out_label = out  # tmp/测试路径在 REPO 外
    print(f"[OK] {verdict} run_id={run_id}")
    print(f"  cells planned={len(cells)} computed=0 (S0 dry) | prereg_hash={ph[:12]}.. ({prereg_status})")
    print(f"  留档: experiment_store 3 表写入 + {out_label}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="S0 consumer_alpha 验证执行器 (config 驱动矩阵, dry 骨架)")
    p.add_argument("--matrix", help="矩阵配置路径 (默认 consumer_alpha_matrix.yaml)")
    p.add_argument("--backend", default="local", help="local (默认) / modal")
    p.add_argument("--input-snapshot", default="", help="输入数据快照 (death-clause: 必声明)")
    p.add_argument("--objective", default="", help="目标 (为何跑)")
    p.add_argument("--rollback-plan", default="", help="回滚方案")
    p.add_argument("--gate-evidence", action="append", help="gate_id=artifact_path (可重复)")
    p.add_argument("--prereg", help="prereg md 路径 (谄媚死对账)")
    p.add_argument("--check-prereg", action="store_true", help="逐字对账 prereg_hash")
    p.add_argument("--run-id", help="显式 run_id (默认 timestamp; 测试用固定值)")
    p.add_argument("--store", help="experiment_store DB 路径覆盖 (默认 manifest; 测试用临时库)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="dry_run", action="store_true", default=True, help="S0 默认: 空矩阵骨架")
    g.add_argument("--execute", dest="dry_run", action="store_false", help="S3 真跑 (gate 硬阻断)")
    return p


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
