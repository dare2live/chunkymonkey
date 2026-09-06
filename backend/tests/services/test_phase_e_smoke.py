"""Phase E smoke: DatasetSnapshot gate + research surface_status only.

Does NOT run institution_follow ablation / B0–B4 search.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from routers import institution_profile as inst_router
from services.data_sources.disclosure_boundaries import (
    attest_disclosure_research_surface,
)
from services.data_sources.disclosure_dataset_snapshot import (
    DISCLOSURE_SNAPSHOT_RELPATH,
    default_snapshot_path,
)
from services.data_sources.disclosure_research_read import (
    build_disclosure_read_policy,
)
from services.data_sources.disclosure_shadow_compare import (
    DisclosureDomainShadowReport,
    DisclosureShadowCompareReport,
)


def _match_domain(name: str, partition: str) -> DisclosureDomainShadowReport:
    return DisclosureDomainShadowReport(
        domain=name,
        partition=partition,
        status="MATCH",
        legacy_row_count=2,
        canonical_row_count=2,
        compared_fields=("stock_code",),
        rows_match=True,
        mismatch_count=0,
        sample_mismatches=(),
        issues=("phase_e_smoke",),
    )


def test_phase_e_smoke_dataset_snapshot_gate_and_surface_status() -> None:
    root = Path(__file__).resolve().parents[3]
    snap_path = root / DISCLOSURE_SNAPSHOT_RELPATH
    assert snap_path == default_snapshot_path()
    assert snap_path.is_file(), f"missing canary DatasetSnapshot at {snap_path}"
    payload = json.loads(snap_path.read_text(encoding="utf-8"))
    assert payload.get("cutover_allowed") is True
    assert payload.get("scope") in {
        "canary_accepted_partitions",
        "bounded_accepted_partitions",
    }
    assert payload.get("phase_e_ablation") in {
        "blocked_canary_scope_only",
        "bounded_scope_wf_paper_still_blocked",
        "bounded_scope_measured_b0_short_window",
    }
    domains = payload.get("domains") or {}
    assert set(domains) >= {
        "holders_top10",
        "org_holding",
        "stk_holdertrade",
    }
    if payload.get("scope") == "bounded_accepted_partitions":
        for name in ("holders_top10", "org_holding", "stk_holdertrade"):
            date_set = domains[name].get("date_set") or []
            assert len(date_set) >= 2, f"{name} needs bounded date_set"
            assert domains[name].get("accepted")

    shadow = DisclosureShadowCompareReport(
        overall_status="MATCH",
        cutover_allowed=True,
        domains=(
            _match_domain("holders_top10", "20260717"),
            _match_domain("org_holding", "20190430"),
            _match_domain("stk_holdertrade", "20260706"),
        ),
        notes=("phase_e_smoke",),
    )
    policy = build_disclosure_read_policy(shadow)
    assert policy.cutover_allowed is True
    assert policy.feature_store_field_status
    report = attest_disclosure_research_surface(policy)
    assert report.cutover_allowed is True
    assert report.e0_phase == "gate_closed_canary"
    assert "phase_e_smoke_eligible_ablation_still_blocked" in report.notes

    envelope = {
        "status": "ok",
        "surface_status": inst_router.SURFACE_STATUS,
        "disclosure_conformity": report.as_dict(),
        "disclosure_read_policy": policy.as_dict(),
        "cutover_allowed": True,
    }
    assert envelope["surface_status"] == "tier3_research_evidence_only"
    assert envelope["cutover_allowed"] is True
    # Full B0→B4 ablation remains blocked; bounded scope unlocks measured B0 only.
    ablation = payload["phase_e_ablation"]
    assert ablation != "eligible_full_b0_b4"
    assert (
        "ablation" in ablation
        or "blocked" in ablation
        or ablation == "bounded_scope_measured_b0_short_window"
    )


def test_phase_e_checkpoint_verdict_artifacts_reject_no_gain() -> None:
    """Committed Phase E ladder artifacts: no claimable / no StrategyRelease."""

    root = Path(__file__).resolve().parents[3]
    manifest_path = root / "data/lineage/phase_e_experiment_verdicts/manifest.json"
    assert manifest_path.is_file(), f"missing Phase E verdict manifest at {manifest_path}"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("overall", {}).get("status") == "measured_reject_no_gain"
    assert manifest.get("overall", {}).get("any_claimable") is False
    assert manifest.get("overall", {}).get("strategy_release") is False
    recorded_hash = manifest.get("snapshot_hash")
    assert recorded_hash

    # ── 为什么这里分两档验 ────────────────────────────────────────────────────
    # `.gitignore:70-72` 明确只版本化 manifest.json 与 b0.json (理由写在 :66 —— manifest 被
    # check_strategy_lab/agent_board_projection 读, b0 的 sha256 进证据链), b1/b2/b4 按体积排除。
    # 原实现无差别读四个 payload, 于是**全新克隆(CI)里必然 FileNotFoundError** —— 判据被放在
    # 答案不可能对的地方跑。2026-09-06 修。
    #
    # 分档原则与 bestchoice 冻结证据同款: 缺席必须是**被声明过的**, 不是"文件不在就跳过"——
    # 后者会把一个真正被误删的版本化文件一起放过。所以下面对缺席文件先断言它确实未被跟踪。
    ladder = {b.get("block"): b for b in (manifest.get("ladder") or [])}
    blocks = manifest.get("blocks") or {}
    checked_payload: list[str] = []
    for name in ("b0", "b1", "b2", "b4"):
        rel = blocks.get(name)
        assert rel, f"manifest missing block {name}"

        # 每个 block 的裁决都从 manifest 断言 —— manifest 是版本化的, 这几条在 CI 里照跑。
        entry = ladder.get(name)
        assert entry, f"manifest.ladder missing block {name}"
        assert entry.get("verdict") in {"reject", "inconclusive"}
        assert entry.get("claimable") is False
        assert entry.get("strategy_release") is False

        path = root / rel
        if not path.is_file():
            tracked = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--error-unmatch", rel],
                capture_output=True,
            ).returncode == 0
            assert not tracked, (
                f"{rel} 被 git 跟踪却不在磁盘上 —— 这是真的丢文件, 不是 .gitignore 的预期缺席"
            )
            continue

        payload = json.loads(path.read_text(encoding="utf-8"))
        # 文件在 ⇒ 照常全量验, 且必须与 manifest 自洽 (两处说法不一致就是证据链断了)。
        assert payload.get("verdict") == entry.get("verdict")
        assert payload.get("claimable") is False
        assert payload.get("strategy_release") is False
        assert payload.get("snapshot_hash") == recorded_hash
        checked_payload.append(name)

    # b0 是版本化的那个 —— 它的 payload 必须在, 必须被验过。这条不许因缺席而跳。
    assert "b0" in checked_payload, "b0.json 是版本化文件, 必须存在且被校验"
    assert ladder["b0"].get("verdict") == "reject"
    assert ladder["b2"].get("verdict") == "reject"
    assert ladder["b4"].get("claimable") is False
    manifest_window = manifest.get("window") or {}
    assert int(manifest_window.get("trading_day_count") or 0) >= 100
    # RX remeasure on the development freeze: strictly before holdout 20250601.
    assert manifest_window.get("start") == "20190102"
    assert manifest_window.get("end") == "20250530"
    assert str(manifest_window.get("end") or "") < "20250601"
