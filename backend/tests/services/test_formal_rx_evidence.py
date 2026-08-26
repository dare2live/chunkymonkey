"""Formal RX evidence validators: holdout policy alignment + opaque seal."""
from __future__ import annotations

from types import SimpleNamespace

from services.formal_rx_evidence import (
    build_holdout_seal_payload,
    validate_formal_rx_evidence,
    write_holdout_seal,
)


def _bundle(**overrides: object) -> SimpleNamespace:
    payload = {
        "holdout_start": "20250601",
        "available_at_upper": "20250530",
        "read_only": True,
        "snapshot_id": "synthetic-development-freeze",
        "snapshot_content_hash": "development-content",
        "snapshot_config_hash": "snapshot-config",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _seed_policy(repo, holdout_start: str = "20250601") -> None:
    path = repo / "backend" / "config" / "holdout_policy.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(f'holdout_start: "{holdout_start}"\n', encoding="utf-8")


def test_missing_seal_fails_closed(tmp_path) -> None:
    _seed_policy(tmp_path)
    reasons = validate_formal_rx_evidence(_bundle(), repo=tmp_path)
    assert "sealed_holdout_ref_missing" in reasons


def test_holdout_start_mismatch_vs_policy(tmp_path) -> None:
    _seed_policy(tmp_path, holdout_start="20250601")
    write_holdout_seal(repo=tmp_path)
    reasons = validate_formal_rx_evidence(
        _bundle(holdout_start="20250701"),
        repo=tmp_path,
    )
    assert "holdout_start_mismatch_vs_policy" in reasons


def test_seal_must_not_leak_partitions(tmp_path) -> None:
    _seed_policy(tmp_path)
    payload = write_holdout_seal(repo=tmp_path)
    payload["partitions"] = ["20250602"]
    seal_path = tmp_path / "data" / "lineage" / "holdout_seal.json"
    seal_path.write_text(
        __import__("json").dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    reasons = validate_formal_rx_evidence(_bundle(), repo=tmp_path)
    assert "sealed_holdout_leaks_partitions" in reasons


def test_matching_opaque_seal_passes(tmp_path) -> None:
    _seed_policy(tmp_path)
    written = write_holdout_seal(repo=tmp_path)
    expected = build_holdout_seal_payload(repo=tmp_path)
    assert written["seal_hash"] == expected["seal_hash"]
    assert written["opaque"] is True
    assert "partitions" not in written
    assert "date_set" not in written
    reasons = validate_formal_rx_evidence(_bundle(), repo=tmp_path)
    assert reasons == ()
