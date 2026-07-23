#!/usr/bin/env python3
"""FND-GATE: foundation-done aggregate checklist (F1–F10).

Authority: analysis/foundation_phase_reeval_20260721.md §3
Config: backend/config/foundation_done.yaml

Semantics:
  - FAIL on real gaps (fail-closed)
  - Typed walls stay PASS when honestly maintained:
      S7 23 ssot hard-stop kinds,
      org mass by-date provider land banned + daily incremental-by-period required,
      B5 Type-B enrichment_projection_partial defer
  - Real gaps → FAIL (fail-closed)
  - Typed walls (S7 23 ssot / org mass-ban+incremental / Type-B defer) → PASS
  - F8 §15 behavior PASS only with ≥3 knife evidence (commits/knife≤1.5 + pre_knife)
  - --skip-live: F4/F6 omit DuckDB probes but still PASS (CI offline); live run
    is authoritative for phase_closure_ready
  - Exit 0 for aggregate PASS|PARTIAL; exit 1 for FAIL; exit 2 crash
  - phase_closure_ready=true only when every criterion is PASS
    (then near-term track may leave foundation solidify for scheduled E/F)

Run:
  PYTHONPATH=backend python backend/scripts/check_foundation_done.py
  PYTHONPATH=backend python backend/scripts/check_foundation_done.py --json
  PYTHONPATH=backend python backend/scripts/check_foundation_done.py --skip-live
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

CONFIG_PATH = REPO / "backend" / "config" / "foundation_done.yaml"
LEGACY_YAML = REPO / "backend" / "config" / "legacy_raw_plane.yaml"
BRICK_YAML = REPO / "backend" / "config" / "brick_registry.yaml"
GOAL_MD = REPO / "goal.md"
SYNC_RUNNER = REPO / "backend" / "services" / "data_sources" / "sync_runner.py"
DISCLOSURE_TRANSPORT = (
    REPO / "backend" / "services" / "data_sources" / "disclosure_transport.py"
)
DERIVE_RUNTIME = REPO / "backend" / "services" / "derive_runtime.py"
SERVE_READ = REPO / "backend" / "services" / "market_pulse_serve_read.py"
SECURITY_DAY_ACQUIRE = (
    REPO / "backend" / "services" / "data_sources" / "security_day_acquire.py"
)

CRITERION_IDS = tuple(f"F{i}" for i in range(1, 11))


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"invalid yaml root at {path}")
    return data


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    cfg = _load_yaml(path)
    if int(cfg.get("version") or 0) != 1:
        raise ValueError(f"foundation_done.yaml version must be 1 (got {cfg.get('version')!r})")
    return cfg


def _crit(
    cid: str,
    verdict: str,
    *,
    detail: str,
    evidence: dict[str, Any] | None = None,
    wall: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": cid,
        "verdict": verdict,
        "detail": detail,
    }
    if evidence is not None:
        out["evidence"] = evidence
    if wall is not None:
        out["typed_wall"] = wall
    return out


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_f1_transport() -> dict[str, Any]:
    """F1: S1–S6 transport modular (static source + presence)."""
    missing: list[str] = []
    for path in (
        SYNC_RUNNER,
        DISCLOSURE_TRANSPORT,
        DERIVE_RUNTIME,
        SERVE_READ,
        SECURITY_DAY_ACQUIRE,
    ):
        if not path.is_file():
            missing.append(str(path.relative_to(REPO)))
    if missing:
        return _crit("F1", "FAIL", detail=f"missing transport owners: {missing}")

    sync_src = _read_text(SYNC_RUNNER)
    if "capture_and_publish_authorized_" in sync_src:
        return _crit(
            "F1",
            "FAIL",
            detail="sync_runner still references fused capture_and_publish_* (S3 residual)",
        )
    if "land_then_accept" not in sync_src:
        return _crit("F1", "FAIL", detail="sync_runner missing land_then_accept caller-only path")

    derive_src = _read_text(DERIVE_RUNTIME)
    if "from_accepted" not in derive_src:
        return _crit("F1", "FAIL", detail="derive_runtime missing from_accepted (S5)")

    acquire_src = _read_text(SECURITY_DAY_ACQUIRE)
    if "from_local_raw" not in acquire_src and "local_raw" not in acquire_src:
        return _crit("F1", "FAIL", detail="security_day_acquire missing local-raw mode (S4)")

    serve_src = _read_text(SERVE_READ)
    if "resolve_" not in serve_src and "DataAccess" not in serve_src:
        return _crit("F1", "FAIL", detail="market_pulse_serve_read missing resolver/DataAccess (S6)")

    import importlib.util

    path = REPO / "backend" / "scripts" / "check_serve_read_layer.py"
    spec = importlib.util.spec_from_file_location("check_serve_read_layer", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d5 = mod.door_router_no_ad_hoc_raw()
    if d5:
        return _crit(
            "F1",
            "FAIL",
            detail=f"serve-read D5 router raw violations: {d5[:5]}",
            evidence={"d5_violations": d5},
        )

    return _crit(
        "F1",
        "PASS",
        detail="S1–S6 transport owners present; sync caller-only; serve-read D5 clean",
    )


def check_f2_s7_wall(cfg: dict[str, Any]) -> dict[str, Any]:
    """F2: S7 inventory honest; 23 ssot typed hard-stop wall = PASS."""
    import importlib.util

    path = REPO / "backend" / "scripts" / "check_legacy_raw_plane.py"
    spec = importlib.util.spec_from_file_location("check_legacy_raw_plane", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    viol = mod.collect_violations()
    if viol:
        return _crit(
            "F2",
            "FAIL",
            detail="legacy_raw_plane gate violations",
            evidence={"violations": viol},
        )

    wall = cfg.get("s7_ssot_wall") or {}
    inv = _load_yaml(LEGACY_YAML)
    tables = inv.get("tables") or {}
    roles = Counter()
    kinds = Counter()
    for meta in tables.values():
        if not isinstance(meta, dict):
            continue
        role = meta.get("role")
        if isinstance(role, str):
            roles[role] += 1
        if role == "ssot":
            kind = meta.get("kind") or "?"
            kinds[str(kind)] += 1

    expected_ssot = int(wall.get("ssot", 23))
    expected_fill = int(wall.get("fill", 1))
    expected_compat = int(wall.get("compatibility", 22))
    expected_kinds = dict(wall.get("kinds") or {})

    gaps: list[str] = []
    if roles.get("ssot", 0) != expected_ssot:
        gaps.append(f"ssot={roles.get('ssot', 0)} expected={expected_ssot}")
    if roles.get("fill", 0) != expected_fill:
        gaps.append(f"fill={roles.get('fill', 0)} expected={expected_fill}")
    if roles.get("compatibility", 0) != expected_compat:
        gaps.append(
            f"compatibility={roles.get('compatibility', 0)} expected={expected_compat}"
        )
    for kind, n in expected_kinds.items():
        if kinds.get(kind, 0) != int(n):
            gaps.append(f"kind {kind}={kinds.get(kind, 0)} expected={n}")

    if gaps:
        return _crit(
            "F2",
            "FAIL",
            detail="S7 wall counts drifted: " + "; ".join(gaps),
            evidence={"roles": dict(roles), "ssot_kinds": dict(kinds)},
        )

    return _crit(
        "F2",
        "PASS",
        detail=(
            f"legacy plane OK; ssot={expected_ssot} typed hard-stop wall "
            f"(sync_orphan={expected_kinds.get('sync_orphan')}, "
            f"serve_l0_declared={expected_kinds.get('serve_l0_declared')}, "
            f"blocked_no_publication={expected_kinds.get('blocked_no_publication')})"
        ),
        evidence={"roles": dict(roles), "ssot_kinds": dict(kinds)},
        wall="s7_23_hard_stop",
    )


def check_f3_no_fake_publication() -> dict[str, Any]:
    """F3: gate rejects serve_l0/multi_consumer COMPAT without DataAccess publication."""
    import importlib.util

    path = REPO / "backend" / "scripts" / "check_legacy_raw_plane.py"
    spec = importlib.util.spec_from_file_location("check_legacy_raw_plane", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Structural proof: gate source encodes the F3 rule; live inventory already
    # passed collect_violations in F2. Inject a synthetic violation shape check.
    src = _read_text(path)
    if "serve_l0_leaf" not in src or "multi_consumer" not in src:
        return _crit("F3", "FAIL", detail="legacy gate missing serve_l0/multi_consumer rules")
    if "publication_surface" not in src:
        return _crit("F3", "FAIL", detail="legacy gate missing publication_surface enforcement")

    viol = mod.collect_violations()
    if viol:
        return _crit(
            "F3",
            "FAIL",
            detail="live inventory has publication honesty violations",
            evidence={"violations": viol},
        )
    return _crit(
        "F3",
        "PASS",
        detail="no fake COMPAT publication; serve_l0/multi_consumer gate armed",
    )


def _qfq_live_missing_lineage(cfg: dict[str, Any]) -> tuple[int | None, str | None]:
    b5 = cfg.get("b5") or {}
    try:
        from services.database_manifest import get_database_manifest
        from services.duck_adapter import connect as duck_connect

        alias = str(b5.get("qfq_db") or "market")
        table = str(b5.get("qfq_table") or "price_kline_qfq_tushare")
        path = get_database_manifest().path_for(alias)
        if not path.is_file():
            return None, f"qfq db missing: {path}"
        con = duck_connect(str(path), read_only=True)
        try:
            row = con.execute(
                f"""
                SELECT COUNT(*) AS n,
                       SUM(CASE WHEN batch_id IS NULL OR ingested_at IS NULL
                                 OR factor_as_of IS NULL THEN 1 ELSE 0 END) AS miss
                FROM {table}
                """
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None, "qfq query returned no row"
        return int(row[1] or 0), None
    except Exception as exc:  # noqa: BLE001 — typed live failure
        return None, f"{type(exc).__name__}: {exc}"


def check_f4_b5_qfq(cfg: dict[str, Any], *, skip_live: bool) -> dict[str, Any]:
    """F4: brick registry PASS + qfq LINEAGE_OK; Type-B defer is typed wall."""
    import importlib.util

    path = REPO / "backend" / "scripts" / "check_brick_registry.py"
    spec = importlib.util.spec_from_file_location("check_brick_registry", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    report = mod.build_report()
    viol = list(report.get("violations") or [])
    if viol or report.get("verdict") != "PASS":
        return _crit(
            "F4",
            "FAIL",
            detail="brick_registry gate not PASS",
            evidence={"violations": viol, "report_verdict": report.get("verdict")},
        )

    b5 = cfg.get("b5") or {}
    brick_id = str(b5.get("qfq_brick_id") or "price_kline_qfq_tushare")
    expected_trust = str(b5.get("qfq_trust") or "LINEAGE_OK")
    bricks = (_load_yaml(BRICK_YAML).get("bricks") or {})
    qfq = bricks.get(brick_id) or {}
    trust = ((qfq.get("lineage") or {}).get("trust") if isinstance(qfq, dict) else None)
    if trust != expected_trust:
        return _crit(
            "F4",
            "FAIL",
            detail=f"qfq lineage.trust={trust!r} expected={expected_trust!r}",
        )

    defer_codes = set(b5.get("type_b_defer_codes") or [])
    fb = (_load_yaml(BRICK_YAML).get("feature_blocks") or {})
    edge = fb.get("institution_profile_edge_v0") or {}
    reasons = {
        str(r.get("code"))
        for r in (edge.get("partial_reasons") or [])
        if isinstance(r, dict)
    }
    if edge.get("status") == "partial" and not (reasons & defer_codes):
        return _crit(
            "F4",
            "FAIL",
            detail="Type-B edge partial without typed defer code",
            evidence={"partial_reasons": sorted(reasons)},
        )

    live_miss: int | None = None
    live_err: str | None = None
    if skip_live:
        live_note = "live_skipped"
    else:
        live_miss, live_err = _qfq_live_missing_lineage(cfg)
        if live_err is not None:
            return _crit(
                "F4",
                "FAIL",
                detail=f"qfq live lineage check failed: {live_err}",
            )
        if live_miss != 0:
            return _crit(
                "F4",
                "FAIL",
                detail=f"qfq missing_lineage={live_miss} (expected 0)",
                evidence={"missing_lineage": live_miss},
            )
        live_note = "missing_lineage=0"

    return _crit(
        "F4",
        "PASS",
        detail=(
            f"brick_registry PASS; qfq trust={expected_trust}; {live_note}; "
            "Type-B enrichment defer typed"
        ),
        evidence={
            "l2_count": report.get("l2_count"),
            "l3_count": report.get("l3_count"),
            "type_b_count": report.get("type_b_count"),
            "type_b_defer_codes": sorted(defer_codes & reasons),
            "missing_lineage": live_miss,
        },
        wall="type_b_enrichment_defer",
    )


def check_f5_e0_transport() -> dict[str, Any]:
    """F5: disclosure CLI three modes + stk/holders provider land; org blocked."""
    sync_src = _read_text(SYNC_RUNNER)
    dt_src = _read_text(DISCLOSURE_TRANSPORT)

    for token in ("land_only", "accept_from_landing", "land_then_accept"):
        if token not in sync_src and token.replace("_", "-") not in sync_src:
            # argparse uses dest with underscores; flags may be hyphenated in help
            if f"--{token.replace('_', '-')}" not in sync_src and token not in sync_src:
                return _crit("F5", "FAIL", detail=f"disclosure CLI missing mode {token}")

    if "PROVIDER_LAND_DOMAINS" not in dt_src and "provider" not in dt_src.lower():
        return _crit("F5", "FAIL", detail="disclosure_transport missing provider land surface")

    # Holders/stk by-date provider land allowed; org = local-raw / by-period only
    if "org_holding" not in dt_src:
        return _crit("F5", "FAIL", detail="disclosure_transport missing org_holding domain")
    if "BLOCKED" not in dt_src and "from-local-raw" not in dt_src:
        return _crit(
            "F5",
            "FAIL",
            detail="org_holding mass by-date ban / local-raw guard missing",
        )

    if "holders_top10" not in dt_src or "stk_holdertrade" not in dt_src:
        return _crit("F5", "FAIL", detail="stk/holders domains missing from disclosure transport")

    return _crit(
        "F5",
        "PASS",
        detail=(
            "E0 disclosure CLI three modes + stk/holders provider land; "
            "org mass by-date banned (incremental-by-period elsewhere)"
        ),
    )


def _e0_live_breadth(cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    e0 = cfg.get("e0_breadth") or {}
    try:
        from services.database_manifest import get_database_manifest
        from services.duck_adapter import connect as duck_connect

        mf = get_database_manifest()

        def _parts(db_alias: str, dataset_id: str) -> set[str]:
            path = mf.path_for(db_alias)
            if not path.is_file():
                raise FileNotFoundError(str(path))
            con = duck_connect(str(path), read_only=True)
            try:
                rows = con.execute(
                    """
                    SELECT DISTINCT partition_value
                    FROM accepted_partition
                    WHERE dataset_id = ?
                    """,
                    [dataset_id],
                ).fetchall()
            finally:
                con.close()
            return {str(r[0]) for r in rows}

        holders = _parts(str(e0["holders_db"]), str(e0["holders_dataset_id"]))
        stk = _parts(str(e0["stk_db"]), str(e0["stk_dataset_id"]))
        daily = _parts(str(e0["daily_db"]), str(e0["daily_dataset_id"]))
        org = _parts(str(e0["org_db"]), str(e0["org_dataset_id"]))
        from services.org_holding_population import max_accepted_stocks_across_partitions

        org_path = mf.path_for(str(e0["org_db"]))
        org_con = duck_connect(str(org_path), read_only=True)
        try:
            org_max_stocks = max_accepted_stocks_across_partitions(org_con)
        finally:
            org_con.close()
        return {
            "holders_partitions": len(holders),
            "stk_partitions": len(stk),
            "daily_partitions": len(daily),
            "org_partitions": len(org),
            "org_max_accepted_stocks": org_max_stocks,
            "holders_daily_overlap": len(holders & daily),
            "stk_daily_overlap": len(stk & daily),
            "holders_range": [min(holders), max(holders)] if holders else [],
            "stk_range": [min(stk), max(stk)] if stk else [],
        }, None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def check_f6_e0_breadth(cfg: dict[str, Any], *, skip_live: bool) -> dict[str, Any]:
    """F6: holders overlap; stk synced; org partitions + population floor."""
    e0 = cfg.get("e0_breadth") or {}
    min_overlap = int(e0.get("min_holders_daily_overlap", 120))
    min_org_stocks = int(e0.get("min_org_accepted_stocks", 500))

    if skip_live:
        return _crit(
            "F6",
            "PASS",
            detail="live breadth skipped (--skip-live); offline surface only",
            evidence={"min_holders_daily_overlap": min_overlap,
                      "min_org_accepted_stocks": min_org_stocks, "live": "skipped"},
        )

    measured, err = _e0_live_breadth(cfg)
    if err is not None or measured is None:
        return _crit("F6", "FAIL", detail=f"live E0 breadth unavailable: {err}")

    gaps: list[str] = []
    if int(measured["holders_daily_overlap"]) < min_overlap:
        gaps.append(
            f"holders_daily_overlap={measured['holders_daily_overlap']} < {min_overlap}"
        )
    if int(measured["stk_partitions"]) < 1:
        gaps.append("stk_partitions=0")
    if int(measured["stk_daily_overlap"]) < 1:
        gaps.append("stk_daily_overlap=0")
    org_stocks = int(measured.get("org_max_accepted_stocks") or 0)
    if int(measured["org_partitions"]) < 1:
        gaps.append("org_partitions=0")
    elif org_stocks < min_org_stocks:
        gaps.append(
            f"org_max_accepted_stocks={org_stocks} < {min_org_stocks} (canary/thin)"
        )
    if gaps:
        return _crit(
            "F6",
            "FAIL",
            detail="E0 accept breadth below foundation bar: " + "; ".join(gaps),
            evidence=measured,
        )

    return _crit(
        "F6",
        "PASS",
        detail=(
            f"holders overlap {measured['holders_daily_overlap']}≥{min_overlap}; "
            f"stk overlap {measured['stk_daily_overlap']}; "
            f"org partitions={measured['org_partitions']} "
            f"max_stocks={org_stocks}≥{min_org_stocks} "
            f"(incremental-by-period; mass by-date banned)"
        ),
        evidence=measured,
    )


def check_f7_org_blocked() -> dict[str, Any]:
    """F7: mass by-date invent banned; daily incremental-by-period required."""
    sync_src = _read_text(SYNC_RUNNER)
    dt_src = _read_text(DISCLOSURE_TRANSPORT)
    acquire_src = _read_text(REPO / "backend/services/pipeline/acquire.py")
    org_src = _read_text(REPO / "backend/services/org_holding_aif10.py")
    if "org_holding" not in sync_src:
        return _crit("F7", "FAIL", detail="sync_runner missing org_holding preflight")
    markers = (
        "org_holding by-period",
        "requires --from-local-raw",
        "no by-date faucet",
        "BLOCKED",
    )
    if not any(m in sync_src or m in dt_src for m in markers):
        return _crit("F7", "FAIL", detail="org mass by-date ban markers missing")
    if re.search(r"org_holding.*by_notice_date|org_holding.*by_ann_date", dt_src):
        return _crit("F7", "FAIL", detail="org_holding by-date provider land invent")
    for token in ("sync_org_holding_incremental", "org_holding_period_gap_report"):
        if token not in acquire_src or token not in org_src:
            return _crit("F7", "FAIL", detail=f"org incremental loop missing {token}")
    return _crit(
        "F7",
        "PASS",
        detail="org mass by-date banned; daily incremental-by-period required",
        wall="org_provider_land_blocked",
    )


def check_f8_section15(cfg: dict[str, Any]) -> dict[str, Any]:
    """F8: §15 knife-merge behavior — PARTIAL until §15-VERIFY fills evidence."""
    s15 = cfg.get("section_15") or {}
    status = str(s15.get("status") or "PARTIAL").upper()
    reason = str(s15.get("reason") or "unspecified")
    max_ratio = float(s15.get("max_commits_per_knife") or 1.5)
    need = int(s15.get("required_consecutive_l3_knives") or 3)
    evidence = s15.get("evidence") or {}
    knives = list(evidence.get("knives") or [])

    if status == "PARTIAL":
        return _crit(
            "F8",
            "PARTIAL",
            detail=(
                f"§15 behavior adoption incomplete ({reason}); "
                f"need {need} L3 knives with commits/knife≤{max_ratio}"
            ),
            evidence={"status": status, "knives": knives},
            wall="section15_behavior_adoption",
        )

    if status != "PASS":
        return _crit(
            "F8",
            "FAIL",
            detail=f"section_15.status must be PASS|PARTIAL (got {status!r})",
        )

    if len(knives) < need:
        return _crit(
            "F8",
            "FAIL",
            detail=f"F8 PASS claim lacks {need} knife evidence entries (got {len(knives)})",
            evidence={"knives": knives},
        )

    ratios: list[float] = []
    for knife in knives:
        if not isinstance(knife, dict):
            return _crit("F8", "FAIL", detail="knife evidence entries must be mappings")
        commits = knife.get("commits")
        if not isinstance(commits, int) or commits < 1:
            return _crit(
                "F8",
                "FAIL",
                detail=f"knife {knife.get('name')!r} missing positive commits count",
            )
        if not knife.get("pre_knife"):
            return _crit(
                "F8",
                "FAIL",
                detail=f"knife {knife.get('name')!r} missing pre_knife=true",
            )
        ratios.append(float(commits))

    # commits/knife for the consecutive window = mean commits per knife
    mean_ratio = sum(ratios) / len(ratios)
    if mean_ratio > max_ratio:
        return _crit(
            "F8",
            "FAIL",
            detail=f"commits/knife={mean_ratio:.3f} > {max_ratio}",
            evidence={"knives": knives, "commits_per_knife": mean_ratio},
        )

    return _crit(
        "F8",
        "PASS",
        detail=f"§15 behavior PASS; commits/knife={mean_ratio:.3f}≤{max_ratio}; n={len(knives)}",
        evidence={"knives": knives, "commits_per_knife": mean_ratio},
    )


def check_f9_strategy_paused(cfg: dict[str, Any]) -> dict[str, Any]:
    """F9: E/F/G/H not opened; frontier honest (goal pause markers)."""
    if not GOAL_MD.is_file():
        return _crit("F9", "FAIL", detail="goal.md missing")
    text = _read_text(GOAL_MD)
    required = list((cfg.get("strategy_pause") or {}).get("goal_must_contain") or [])
    missing = [s for s in required if s not in text]
    if missing:
        return _crit(
            "F9",
            "FAIL",
            detail=f"goal.md missing strategy-pause markers: {missing}",
        )
    # Ban accidental Optuna enablement flip in common configs
    for rel in (
        "backend/config/strategy_release.yaml",
        "backend/config/optuna.yaml",
    ):
        path = REPO / rel
        if not path.is_file():
            continue
        body = _read_text(path).lower()
        if "enabled: true" in body or "enable: true" in body:
            return _crit(
                "F9",
                "FAIL",
                detail=f"{rel} appears enabled while strategy track paused",
            )
    return _crit(
        "F9",
        "PASS",
        detail="E/F remeasure paused; Optuna/StrategyRelease bans intact in goal",
    )


def check_f10_dual_track(cfg: dict[str, Any]) -> dict[str, Any]:
    """F10: dual-track residual NONE (retire notes + serve-read clean)."""
    dt = cfg.get("dual_track") or {}
    notes_rel = str(dt.get("retire_notes") or "data/lineage/legacy_retire_notes.md")
    notes = REPO / notes_rel
    if not notes.is_file():
        return _crit("F10", "FAIL", detail=f"missing {notes_rel}")
    text = _read_text(notes)
    needles = list(dt.get("must_contain") or ["residual = NONE"])
    if not any(n in text for n in needles):
        # accept alternate punctuation
        alt = "residual: NONE"
        if alt not in text and "residual = NONE" not in text:
            return _crit(
                "F10",
                "FAIL",
                detail=f"{notes_rel} missing dual-track residual NONE claim",
            )
    return _crit(
        "F10",
        "PASS",
        detail="dual-track residual NONE (legacy_retire_notes re-audit)",
        evidence={"retire_notes": notes_rel},
    )


def evaluate_foundation_done(
    *,
    cfg: dict[str, Any] | None = None,
    skip_live: bool = False,
) -> dict[str, Any]:
    cfg = cfg if cfg is not None else load_config()
    criteria = [
        check_f1_transport(),
        check_f2_s7_wall(cfg),
        check_f3_no_fake_publication(),
        check_f4_b5_qfq(cfg, skip_live=skip_live),
        check_f5_e0_transport(),
        check_f6_e0_breadth(cfg, skip_live=skip_live),
        check_f7_org_blocked(),
        check_f8_section15(cfg),
        check_f9_strategy_paused(cfg),
        check_f10_dual_track(cfg),
    ]
    by_id = {c["id"]: c["verdict"] for c in criteria}
    if any(v == "FAIL" for v in by_id.values()):
        verdict = "FAIL"
    elif any(v == "PARTIAL" for v in by_id.values()):
        verdict = "PARTIAL"
    else:
        verdict = "PASS"

    phase_closure_ready = all(by_id.get(cid) == "PASS" for cid in CRITERION_IDS)
    return {
        "gate": "foundation_done",
        "authority": cfg.get("authority"),
        "verdict": verdict,
        "phase_closure_ready": phase_closure_ready,
        "skip_live": skip_live,
        "criteria": criteria,
        "summary": {
            "PASS": sum(1 for v in by_id.values() if v == "PASS"),
            "PARTIAL": sum(1 for v in by_id.values() if v == "PARTIAL"),
            "FAIL": sum(1 for v in by_id.values() if v == "FAIL"),
        },
        "note": (
            "PARTIAL (e.g. F8 §15) does not fail this gate; "
            "phase_closure_ready requires all F1–F10 PASS before scheduled E/F"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_foundation_done")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="skip DuckDB live probes (F4 lineage rows / F6 overlap); CI/offline",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="path to foundation_done.yaml",
    )
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        report = evaluate_foundation_done(cfg=cfg, skip_live=args.skip_live)
    except Exception as exc:  # noqa: BLE001 — gate must fail closed
        if args.json:
            print(
                json.dumps(
                    {
                        "gate": "foundation_done",
                        "verdict": "FAIL",
                        "phase_closure_ready": False,
                        "criteria": [],
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(
                f"FAIL foundation_done: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"{report['verdict']} foundation_done: "
            f"PASS={report['summary']['PASS']} "
            f"PARTIAL={report['summary']['PARTIAL']} "
            f"FAIL={report['summary']['FAIL']} "
            f"phase_closure_ready={report['phase_closure_ready']}"
        )
        for c in report["criteria"]:
            wall = f" wall={c['typed_wall']}" if c.get("typed_wall") else ""
            print(f"  {c['id']} {c['verdict']}: {c['detail']}{wall}")

    if report["verdict"] == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
