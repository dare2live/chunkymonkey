"""Read-only compact projection of strategy-lab artifacts for the observation UI.

This is not a second experiment store, not a compute runner, and not a
StrategyRelease path. Partition lists stay on disk; the serve surface never
re-emits them. ``claimable`` is always false.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from scripts.check_strategy_lab import build_status
from services.strategy_spec import StrategySpecError, load_all_strategy_packages


REPO = Path(__file__).resolve().parents[2]
SURFACE_STATUS = "tier3_research_evidence_only"
_VERDICT_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}

_LADDERS: tuple[tuple[str, str, str, Path], ...] = (
    (
        "phase_e",
        "institution_follow_v1",
        "机构跟随 · 消融梯子",
        REPO / "data" / "lineage" / "phase_e_experiment_verdicts",
    ),
    (
        "phase_f",
        "main_rally_v1",
        "主升浪 · setup 消融",
        REPO / "data" / "lineage" / "phase_f_experiment_verdicts",
    ),
)
_BLOCK_LABEL = {
    ("institution_follow_v1", "b0"): "B0 基准",
    ("institution_follow_v1", "b1"): "B1 状态增强",
    ("institution_follow_v1", "b2"): "B2 剥离",
    ("institution_follow_v1", "b4"): "B4 披露事件",
    ("main_rally_v1", "b0"): "B0 基准",
    ("main_rally_v1", "b1"): "B1 全特征",
    ("main_rally_v1", "b2"): "B2 剥离",
}
_PACKAGE_LAYERS = {
    "institution_follow_v1": (
        {
            "layer": "profile_alpha",
            "label": "画像 / episode α",
            "role": "research_input",
            "not": "buy_certificate",
        },
        {
            "layer": "follow_spec_paper",
            "label": "跟随 spec 纸面",
            "role": "this_package",
            "not": "ablation_json",
        },
        {
            "layer": "phase_e_ablation",
            "label": "E B0–B4 消融",
            "role": "ablation_only",
            "not": "this_spec",
        },
    ),
    "main_rally_v1": (
        {
            "layer": "setup_signal",
            "label": "setup 信号 + 短窗纸面",
            "role": "this_package",
            "not": "full_episode",
        },
        {
            "layer": "phase_f_ablation",
            "label": "F B0–B2 消融",
            "role": "ablation_only",
            "not": "rally_hunter",
        },
        {
            "layer": "full_episode",
            "label": "full-episode 猎手",
            "role": "not_implemented",
            "not": "release",
        },
    ),
    "formulas": (
        {
            "layer": "frozen_challenger",
            "label": "冻结公式 hash",
            "role": "this_package",
            "not": "bestchoice_live",
        },
        {
            "layer": "synthetic_smoke",
            "label": "合成烟测 + 单名 pointer",
            "role": "measured_smoke",
            "not": "universe_b5",
        },
        {
            "layer": "absorb",
            "label": "吸收 / 全宇宙 B5",
            "role": "not_implemented",
            "not": "release",
        },
    ),
}


def _envelope(**payload: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "surface_status": SURFACE_STATUS,
        "claimable": False,
        "strategy_release": False,
        **payload,
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_unreadable": str(exc), "path": str(path.relative_to(REPO))}
    if not isinstance(raw, dict):
        return {"_unreadable": "not_a_mapping", "path": str(path.relative_to(REPO))}
    return raw


def _cached_verdict(path: Path) -> dict[str, Any]:
    stat = path.stat()
    key = (str(path), int(stat.st_mtime_ns), int(stat.st_size))
    hit = _VERDICT_CACHE.get(key)
    if hit is not None:
        return hit
    payload = compact_verdict(_load_json(path))
    _VERDICT_CACHE[key] = payload
    if len(_VERDICT_CACHE) > 24:
        oldest = next(iter(_VERDICT_CACHE))
        if oldest != key:
            _VERDICT_CACHE.pop(oldest, None)
    return payload


def _day_window(values: Any) -> tuple[str | None, str | None, int]:
    days = [
        str(item)
        for item in (values or ())
        if isinstance(item, str) and len(item) == 8 and item.isdigit()
    ]
    if not days:
        return None, None, 0
    return days[0], days[-1], len(days)


def compact_coverage(coverage: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(coverage, Mapping):
        return {"partitions_omitted": True}
    start, end, listed = _day_window(coverage.get("accepted_nominal_partitions"))
    count = coverage.get("accepted_nominal_day_count")
    if not isinstance(count, int):
        count = listed
    return {
        "accepted_nominal_day_count": count,
        "window_start": start,
        "window_end": end,
        "status": coverage.get("status"),
        "reason": coverage.get("reason"),
        "sufficient_for_measured_b0": coverage.get("sufficient_for_measured_b0"),
        "partitions_omitted": True,
    }


def compact_metrics(metrics: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metrics, Mapping):
        return None
    skip = {"details", "stability_by_year"}
    out = {key: value for key, value in metrics.items() if key not in skip}
    years = metrics.get("stability_by_year")
    if isinstance(years, Mapping):
        out["stability_year_count"] = len(years)
    return out


def compact_verdict(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Drop run traces and partition dumps; keep the decision surface."""

    if payload.get("_unreadable"):
        return {
            "readable": False,
            "claimable": False,
            "strategy_release": False,
            "reason": payload.get("_unreadable"),
            "path": payload.get("path"),
        }
    summary = payload.get("metrics_summary")
    summary_map = summary if isinstance(summary, Mapping) else {}
    gates = summary_map.get("accept_edge_gates")
    gates_map = gates if isinstance(gates, Mapping) else {}
    checks = gates_map.get("checks") if isinstance(gates_map.get("checks"), Mapping) else {}
    kind = str(payload.get("kind") or "")
    family = "institution_follow_v1" if kind.startswith("phase_e") else (
        "main_rally_v1" if kind.startswith("phase_f") else ""
    )
    block = str(payload.get("block") or "").lower()
    notes = payload.get("notes")
    return {
        "readable": True,
        "family": family,
        "block": block,
        "block_label": _BLOCK_LABEL.get((family, block), block.upper()),
        "kind": kind,
        "role": "ablation_only",
        "not_strategy_spec": True,
        "verdict": payload.get("verdict"),
        "claimable": False,
        "blocked": bool(payload.get("blocked")),
        "strategy_release": False,
        "reason": payload.get("reason"),
        "experiment_id": f"{family}:{block}" if family and block else payload.get("experiment_id"),
        "snapshot_id": payload.get("snapshot_id"),
        "snapshot_hash": payload.get("snapshot_hash"),
        "snapshot_relpath": payload.get("snapshot_relpath"),
        "surface_status": payload.get("surface_status") or SURFACE_STATUS,
        "notes": list(notes) if isinstance(notes, list) else [],
        "edge_gates": {
            "passed": bool(gates_map.get("passed")),
            "reason": gates_map.get("reason"),
            "checks": dict(checks),
        },
        "coverage": compact_coverage(
            summary_map.get("coverage") if isinstance(summary_map.get("coverage"), Mapping) else None
        ),
        "eval_metrics": compact_metrics(
            summary_map.get("metrics") if isinstance(summary_map.get("metrics"), Mapping) else None
        ),
        "holdout_metrics": compact_metrics(
            summary_map.get("holdout_metrics")
            if isinstance(summary_map.get("holdout_metrics"), Mapping)
            else None
        ),
    }


def list_experiments(*, repo: Path = REPO) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ladder, family, label, folder in _LADDERS:
        manifest_path = folder / "manifest.json"
        manifest = _load_json(manifest_path) if manifest_path.is_file() else {}
        frozen_at = manifest.get("frozen_at")
        blocks = manifest.get("blocks")
        if not isinstance(blocks, Mapping):
            continue
        for block, rel in sorted(blocks.items()):
            path = repo / str(rel)
            if not path.is_file():
                path = folder / f"{block}.json"
            row = _cached_verdict(path) if path.is_file() else {
                "readable": False,
                "family": family,
                "block": str(block).lower(),
                "claimable": False,
                "reason": "verdict_file_missing",
            }
            row["ladder"] = ladder
            row["ladder_label"] = label
            row["family"] = family
            row["frozen_at"] = frozen_at
            rows.append(row)
    return rows


def get_experiment(family: str, block: str, *, repo: Path = REPO) -> dict[str, Any] | None:
    wanted_family = str(family or "")
    wanted_block = str(block or "").lower()
    for row in list_experiments(repo=repo):
        if row.get("family") == wanted_family and row.get("block") == wanted_block:
            return row
    return None


def list_packages() -> dict[str, Any]:
    try:
        specs = load_all_strategy_packages()
    except StrategySpecError as exc:
        return {
            "loaded": False,
            "claimable": False,
            "reason": str(exc),
            "packages": [],
        }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for spec in specs:
        grouped.setdefault(spec.package_id, []).append(asdict(spec))
    packages = []
    for package_id, items in grouped.items():
        packages.append(
            {
                "package_id": package_id,
                "claimable": False,
                "strategy_release": False,
                "layers": list(_PACKAGE_LAYERS.get(package_id, ())),
                "specs": items,
            }
        )
    packages.sort(key=lambda item: str(item["package_id"]))
    return {
        "loaded": True,
        "claimable": False,
        "packages": packages,
    }


def snapshot_cards(*, repo: Path = REPO) -> dict[str, Any]:
    disclosure = _load_json(repo / "data" / "lineage" / "disclosure_dataset_snapshot.json")
    rally = _load_json(
        repo / "data" / "lineage" / "main_rally_dataset_snapshot" / "snapshot.json"
    )
    seal = _load_json(repo / "data" / "lineage" / "holdout_seal.json")
    cards = [
        {
            "kind": "disclosure_snapshot",
            "snapshot_id": disclosure.get("snapshot_id"),
            "frozen_at": disclosure.get("frozen_at"),
            "scope": disclosure.get("scope"),
            "shadow_overall_status": disclosure.get("shadow_overall_status"),
            "cutover_allowed": disclosure.get("cutover_allowed"),
            "notes": disclosure.get("notes") or [],
            "relpath": "data/lineage/disclosure_dataset_snapshot.json",
        },
        {
            "kind": "main_rally_snapshot",
            "snapshot_id": rally.get("snapshot_id"),
            "frozen_at": rally.get("frozen_at"),
            "scope": rally.get("scope"),
            "strategy_package": rally.get("strategy_package"),
            "notes": rally.get("notes") or [],
            "relpath": "data/lineage/main_rally_dataset_snapshot/snapshot.json",
        },
        {
            "kind": "holdout_seal",
            "holdout_start": seal.get("holdout_start"),
            "opaque": bool(seal.get("opaque")),
            "partitions_omitted": bool(seal.get("partitions_omitted")),
            "seal_hash": seal.get("seal_hash"),
            "policy_hash": seal.get("policy_hash"),
            "relpath": "data/lineage/holdout_seal.json",
        },
    ]
    return {"cards": cards, "claimable": False}


def _gate(state: str, gate_id: str, label: str, detail: str) -> dict[str, str]:
    return {"id": gate_id, "label": label, "state": state, "detail": detail}


def release_projection(
    status: Mapping[str, Any],
    experiments: list[Mapping[str, Any]],
    snapshots: Mapping[str, Any],
) -> dict[str, Any]:
    n_accept = sum(1 for row in experiments if row.get("verdict") == "accept")
    n_reject = sum(1 for row in experiments if row.get("verdict") == "reject")
    n_inc = sum(1 for row in experiments if row.get("verdict") == "inconclusive")
    live = status.get("live_inputs") if isinstance(status.get("live_inputs"), Mapping) else {}
    freeze_ok = bool(status.get("framework_ready"))
    seal = next(
        (
            card
            for card in snapshots.get("cards") or ()
            if isinstance(card, Mapping) and card.get("kind") == "holdout_seal"
        ),
        {},
    )
    holdout_ok = bool(seal.get("opaque") and seal.get("partitions_omitted") and seal.get("seal_hash"))
    paper = status.get("follow_spec_paper") if isinstance(status.get("follow_spec_paper"), Mapping) else {}
    rally_paper = status.get("rally_setup_paper") if isinstance(status.get("rally_setup_paper"), Mapping) else {}
    formula = status.get("formula_challenge") if isinstance(status.get("formula_challenge"), Mapping) else {}
    artifacts_ok = bool(experiments) and all(row.get("readable") for row in experiments)
    gates = [
        _gate(
            "pass" if freeze_ok else "fail",
            "freeze",
            "1 · 冻结快照",
            "development freeze 早于 holdout"
            if freeze_ok
            else str((live.get("snapshots") or {})),
        ),
        _gate(
            "unknown",
            "pit",
            "2 · PIT 截断",
            "观察面不重放 pit-audit；不把未核验写成通过",
        ),
        _gate(
            "pass" if holdout_ok else "fail",
            "holdout",
            "3 · holdout 单触",
            f"opaque seal {str(seal.get('holdout_start') or '')} · partitions omitted",
        ),
        _gate(
            "partial",
            "paper",
            "4 · 纸面执行",
            "follow={paper} · rally={rally} · formula={formula} —— smoke/setup，不是完整 paper execution".format(
                paper=paper.get("status"),
                rally=rally_paper.get("status"),
                formula=formula.get("status"),
            ),
        ),
        _gate(
            "pass" if artifacts_ok else "fail",
            "ablation",
            "5 · 逐层消融",
            f"E/F JSON 在场 · reject={n_reject} inconclusive={n_inc} · 消融 ≠ spec 纸面",
        ),
        _gate(
            "unknown",
            "leakage",
            "6 · 泄漏反证",
            "观察面不重放泄漏审计；核不到的门标 unknown",
        ),
        _gate(
            "pass" if artifacts_ok else "fail",
            "artifact",
            "7 · artifact 存档",
            "lineage verdict JSON + freeze + opaque holdout seal",
        ),
        _gate(
            "fail",
            "accept",
            "8 · accept verdict",
            f"{len(experiments)} 实验 · accept={n_accept} · claimable=false",
        ),
        _gate(
            "blocked",
            "monitor",
            "9 · 监控冻结",
            "未进入 · 依赖第 8 门",
        ),
    ]
    return {
        "claimable": False,
        "strategy_release": False,
        "n_experiments": len(experiments),
        "n_accept": n_accept,
        "n_reject": n_reject,
        "n_inconclusive": n_inc,
        "any_accept": False,
        "gates": gates,
        "contract": "retired paradigm — git log --grep strategy_lab_serve",
    }


def status_payload() -> dict[str, Any]:
    return dict(build_status())


def overview_payload() -> dict[str, Any]:
    status = status_payload()
    packages = list_packages()
    experiments = list_experiments()
    snapshots = snapshot_cards()
    release = release_projection(status, experiments, snapshots)
    return _envelope(
        framework=status,
        packages=packages,
        experiments=experiments,
        snapshots=snapshots,
        release=release,
    )
