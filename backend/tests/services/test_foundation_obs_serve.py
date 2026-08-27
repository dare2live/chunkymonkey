"""File-based foundation observation — no DuckDB, no live daily_update."""
from __future__ import annotations

import json
from pathlib import Path

from services.foundation_obs_serve import (
    domain_payload,
    health_payload,
    matrix_payload,
)


def _write_sla(repo: Path, *, today: str = "20260824") -> Path:
    audit = repo / "data" / "audit"
    audit.mkdir(parents=True)
    path = audit / "watermark_sla_20260824.json"
    path.write_text(
        json.dumps(
            {
                "run_at": "2026-08-24T06:00:00Z",
                "today": today,
                "n_alerts": 1,
                "sources": [
                    {
                        "data_domain": "sync:daily",
                        "watermark_date": "20260814",
                        "watermark_days_ago": 9,
                        "sla_days": 1,
                        "status": "DATA_STALE_VS_SLA",
                        "alert": True,
                        "sla_axis": "trade_date",
                        "probe_state": "stale",
                    },
                    {
                        "data_domain": "sync:moneyflow",
                        "watermark_date": "20260820",
                        "watermark_days_ago": 3,
                        "sla_days": 1,
                        "status": "OK",
                        "alert": False,
                        "sla_axis": "trade_date",
                    },
                    {
                        "data_domain": "sync:ths_hot",
                        "watermark_date": "20260820",
                        "watermark_days_ago": 3,
                        "sla_days": 2,
                        "status": "NO_PROBE_RULE",
                        "alert": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_matrix_reads_all_sla_sources_and_maps_lamps(tmp_path: Path):
    _write_sla(tmp_path)
    flags = tmp_path / "flags"
    flags.mkdir()
    (flags / "chunkymonkey_ALERT_continuity.flag").write_text("1", encoding="utf-8")
    out = matrix_payload(repo=tmp_path, flag_dir=flags)
    assert out["status"] == "ok"
    assert out["n_domains"] == 3
    assert out["today"] == "20260824"
    assert out["alert_flags"]["continuity"] is True
    by_name = {
        row["domain"]: row
        for group in out["groups"]
        for row in group["domains"]
    }
    assert by_name["daily"]["lamp"] == "hole"
    assert by_name["daily"]["cn"] == "名义日K"
    assert by_name["moneyflow"]["lamp"] == "ok"
    assert by_name["ths_hot"]["lamp"] == "unk"
    assert "rows" not in by_name["daily"]


def test_domain_lookup_and_empty(tmp_path: Path):
    _write_sla(tmp_path)
    hit = domain_payload("daily", repo=tmp_path, flag_dir=tmp_path)
    assert hit["status"] == "ok"
    assert hit["item"]["domain"] == "daily"
    miss = domain_payload("not_a_domain", repo=tmp_path, flag_dir=tmp_path)
    assert miss["status"] == "empty"
    assert miss["item"] is None


def test_health_uses_flags_and_last_report_without_inventing_green(tmp_path: Path):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "daily_20260821.json").write_text(
        json.dumps(
            {
                "date": "20260821",
                "run_outcome": "integrity_observe",
                "run_outcome_label": "完整性观测",
                "run_outcome_reason": "continuity",
                "run_outcome_classified": [{"cls": "integrity", "msg": "continuity FAIL"}],
            }
        ),
        encoding="utf-8",
    )
    flags = tmp_path / "flags"
    flags.mkdir()
    out = health_payload(repo=tmp_path, flag_dir=flags)
    by_id = {row["id"]: row for row in out["checks"]}
    assert by_id["continuity"]["lamp"] == "unk"
    assert by_id["daily_update"]["state"] == "no_flag"
    assert by_id["last_run"]["lamp"] == "soft"
    assert by_id["last_run"]["date"] == "20260821"
    assert out["classified"][0]["cls"] == "integrity"


def test_empty_repo_is_typed_empty_not_sample(tmp_path: Path):
    out = matrix_payload(repo=tmp_path, flag_dir=tmp_path)
    assert out["status"] == "empty"
    assert out["groups"] == []
    assert out["n_domains"] == 0
