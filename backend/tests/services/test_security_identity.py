"""S0 -- typed loader for backend/config/security_code_changes.yaml
(asof_identity_r1.md §3.1/§9 S0). One isolated case per gating condition:
each fixture satisfies every other condition and violates exactly one.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from services.security_identity import (
    CodeChangeEvent,
    CodeChangeSet,
    load_security_code_changes,
)

_BASE_EVENT = {
    "old_code": "300114.SZ",
    "new_code": "302132.SZ",
    "effective_date": "20250217",
    "exchange": "SZSE",
    "kind": "reorg_rename",
    "source_kind": "announcement",
    "source_ref": "cninfo 2025-02-15 公告编号 2025-028",
    "checked_at": "2026-09-12",
}


def _doc(events: list[dict], version: int = 1) -> dict:
    return {"version": version, "events": events}


def _write(tmp_path: Path, doc: dict) -> Path:
    p = tmp_path / "security_code_changes.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Fail-closed: structural
# ---------------------------------------------------------------------------


def test_extra_top_level_key_rejected(tmp_path):
    doc = _doc([dict(_BASE_EVENT)])
    doc["extra_key"] = "unexpected"
    p = _write(tmp_path, doc)
    with pytest.raises(ValueError, match=r"top-level keys must be exactly"):
        load_security_code_changes(p)


def test_version_other_than_one_rejected(tmp_path):
    """version 是契约版本: 值不是 1 就说明这份登记的形状可能已经不是 loader 认识的
    那一份, 与未知键同样 fail-closed (与 reland_event_domain.load_vendor_gaps 同口径)。
    其它条件全部满足, 只有 version 为假。"""
    p = _write(tmp_path, _doc([dict(_BASE_EVENT)], version=2))
    with pytest.raises(ValueError, match=r"version must be 1"):
        load_security_code_changes(p)


def test_event_missing_source_ref_key_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    del ev["source_ref"]
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(ValueError, match=r"events\[0\] keys must be exactly"):
        load_security_code_changes(p)


# ---------------------------------------------------------------------------
# Fail-closed: per-field
# ---------------------------------------------------------------------------


def test_empty_source_ref_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["source_ref"] = ""
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(ValueError, match=r"events\[0\]\.source_ref must be a non-empty string"):
        load_security_code_changes(p)


def test_unknown_kind_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["kind"] = "not_a_real_kind"
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(ValueError, match=r"events\[0\]\.kind = 'not_a_real_kind' not in"):
        load_security_code_changes(p)


def test_unknown_source_kind_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["source_kind"] = "not_a_real_source_kind"
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(
        ValueError, match=r"events\[0\]\.source_kind = 'not_a_real_source_kind' not in"
    ):
        load_security_code_changes(p)


def test_code_without_exchange_suffix_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["old_code"] = "300114"
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(ValueError, match=r"events\[0\]\.old_code = '300114' does not match"):
        load_security_code_changes(p)


def test_old_equals_new_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["new_code"] = ev["old_code"]
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(ValueError, match=r"events\[0\]\.old_code == events\[0\]\.new_code"):
        load_security_code_changes(p)


def test_effective_date_not_a_real_calendar_day_rejected(tmp_path):
    ev = dict(_BASE_EVENT)
    ev["effective_date"] = "20250230"  # February has no 30th
    p = _write(tmp_path, _doc([ev]))
    with pytest.raises(
        ValueError, match=r"events\[0\]\.effective_date = '20250230' is not a valid calendar date"
    ):
        load_security_code_changes(p)


# ---------------------------------------------------------------------------
# Fail-closed: cross-event (uniqueness / chain / cycle)
# ---------------------------------------------------------------------------


def test_duplicate_new_code_rejected(tmp_path):
    ev0 = dict(_BASE_EVENT)
    ev1 = dict(
        _BASE_EVENT,
        old_code="000043.SZ",
        new_code="302132.SZ",  # collides with ev0's new_code
        effective_date="20260101",
    )
    p = _write(tmp_path, _doc([ev0, ev1]))
    with pytest.raises(
        ValueError, match=r"events\[1\]\.new_code '302132\.SZ' duplicates events\[0\]\.new_code"
    ):
        load_security_code_changes(p)


def test_duplicate_old_code_rejected(tmp_path):
    ev0 = dict(_BASE_EVENT)
    ev1 = dict(
        _BASE_EVENT,
        new_code="000001.SZ",  # ev1.old_code left as ev0's old_code -> collision
        effective_date="20260101",
    )
    p = _write(tmp_path, _doc([ev0, ev1]))
    with pytest.raises(
        ValueError, match=r"events\[1\]\.old_code '300114\.SZ' duplicates events\[0\]\.old_code"
    ):
        load_security_code_changes(p)


def test_chain_effective_date_not_increasing_rejected(tmp_path):
    # A -> B (2025) -> C (2024): the second leg must be strictly after the
    # first, not before it. Acyclic on purpose, so cycle detection cannot
    # be what fires here.
    ev0 = dict(_BASE_EVENT, old_code="100000.SZ", new_code="200000.SZ", effective_date="20250101")
    ev1 = dict(_BASE_EVENT, old_code="200000.SZ", new_code="300000.SZ", effective_date="20240101")
    p = _write(tmp_path, _doc([ev0, ev1]))
    with pytest.raises(ValueError, match=r"must be strictly greater"):
        load_security_code_changes(p)


def test_cycle_rejected(tmp_path):
    # A -> B, B -> A: a cycle regardless of dates (chosen here so that both
    # legs are individually "increasing" in file order -- if cycle detection
    # were removed, only the chain-order arithmetic contradiction would
    # eventually surface, not a dedicated cycle message).
    ev0 = dict(_BASE_EVENT, old_code="100000.SZ", new_code="200000.SZ", effective_date="20240101")
    ev1 = dict(_BASE_EVENT, old_code="200000.SZ", new_code="100000.SZ", effective_date="20250101")
    p = _write(tmp_path, _doc([ev0, ev1]))
    with pytest.raises(ValueError, match=r"code-change cycle detected"):
        load_security_code_changes(p)


# ---------------------------------------------------------------------------
# Should load: the repository's real YAML
# ---------------------------------------------------------------------------


def test_repo_yaml_loads_and_parses_both_events():
    ccs = load_security_code_changes()
    assert isinstance(ccs, CodeChangeSet)
    assert len(ccs.events) == 2

    e0, e1 = ccs.events
    assert e0 == CodeChangeEvent(
        old_code="300114.SZ",
        new_code="302132.SZ",
        effective_date="20250217",
        exchange="SZSE",
        kind="reorg_rename",
        source_kind="announcement",
        source_ref=(
            "cninfo 2025-02-15 static.cninfo.com.cn/finalpage/2025-02-15/1222544408.PDF "
            "公告编号 2025-028"
        ),
        checked_at="2026-09-12",
    )
    assert e1.old_code == "000043.SZ"
    assert e1.new_code == "001914.SZ"
    assert e1.effective_date == "20191216"
    assert e1.exchange == "SZSE"
    assert e1.kind == "reorg_rename"
    assert e1.source_kind == "kline_succession_observed"
    # I7's numbers must be traceable in the source_ref lineage evidence.
    assert "20191213" in e1.source_ref
    assert "20.25" in e1.source_ref
    assert "20191216" in e1.source_ref

    assert ccs.by_old["300114.SZ"] is e0
    assert ccs.by_new["302132.SZ"] is e0
    assert ccs.by_old["000043.SZ"] is e1
    assert ccs.by_new["001914.SZ"] is e1
    assert isinstance(ccs.sha256, str)
    assert len(ccs.sha256) == 64
    int(ccs.sha256, 16)  # must be valid hex
