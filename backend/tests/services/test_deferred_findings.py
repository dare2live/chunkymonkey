"""``backend/config/deferred_findings.yaml`` loader contract — offline only.

Every test (except the two that read the real checked-in file, by design —
see bottom section) injects its own minimal-valid YAML doc via ``tmp_path``
(``feedback-test-must-carry-its-own-fixture``): no live network, no DuckDB.
Rule numbers (R1-R14) match ``backend/services/deferred_findings.py``; each
test isolates exactly one gating condition (project rule: 每个门控条件一个
隔离用例，其它条件全满足只违反它) and asserts on a specific substring of the
``deferred_findings: R#`` message, not just ``ValueError``, so a mutation
that disables the wrong check — or the right check but leaves a *different*
one standing in for it — cannot pass this test by accident.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from services.deferred_findings import DEFAULT_PATH, load_deferred_findings

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _minimal_valid_doc() -> dict:
    """A self-contained, loader-valid doc every R-rule test mutates one field
    of. Uses a synthetic id/asset so tests never depend on (and cannot
    accidentally break by mutating) the real registry contents."""
    return {
        "version": 1,
        "decisions": ["register", "owner_decision"],
        "findings": [
            {
                "id": "synthetic-test-finding",
                "asset": "backend/services/example.py:1",
                "detector": "none",
                "escalates_when": "some observable condition happens",
                "recorded": "2026-09-16",
                "decision": "register",
            }
        ],
    }


def _write(tmp_path: Path, data: dict, name: str = "deferred_findings.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# control case — the fixture itself must be valid
# ---------------------------------------------------------------------------


def test_minimal_valid_doc_loads_successfully(tmp_path):
    path = _write(tmp_path, _minimal_valid_doc())
    cfg = load_deferred_findings(path, repo_root=tmp_path)
    assert cfg.version == 1
    assert cfg.decisions == ("register", "owner_decision")
    assert len(cfg.findings) == 1
    assert cfg.findings[0].id == "synthetic-test-finding"
    assert cfg.findings[0].decision == "register"


# ---------------------------------------------------------------------------
# R1 — top-level key set
# ---------------------------------------------------------------------------


def test_r1_extra_top_level_key_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["extra_top_key"] = True
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R1"):
        load_deferred_findings(path, repo_root=tmp_path)


def test_r1_missing_top_level_key_rejected(tmp_path):
    data = _minimal_valid_doc()
    del data["decisions"]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R1"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# R2 — version
# ---------------------------------------------------------------------------


def test_r2_wrong_version_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["version"] = 2
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R2"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# R3 — decisions word list
# ---------------------------------------------------------------------------


def test_r3_decisions_not_a_list_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["decisions"] = "register"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R3 decisions must be a list"):
        load_deferred_findings(path, repo_root=tmp_path)


def test_r3_decisions_empty_string_entry_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["decisions"] = ["register", ""]
    path = _write(tmp_path, data)
    with pytest.raises(
        ValueError, match=r"deferred_findings: R3 decisions entries must be non-empty strings"
    ):
        load_deferred_findings(path, repo_root=tmp_path)


def test_r3_decisions_duplicate_entry_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["decisions"] = ["register", "register"]
    path = _write(tmp_path, data)
    with pytest.raises(
        ValueError, match=r"deferred_findings: R3 decisions has duplicate entry"
    ):
        load_deferred_findings(path, repo_root=tmp_path)


def test_r3_empty_decisions_word_list_fails_closed_when_findings_reference_it(tmp_path):
    """空词表本身合法读取 (词表可以为空)，但只要有一条 finding 引用了任意
    decision 值，就必然在 R14 处 fail——空词表不会静默放行任何 decision。"""
    data = _minimal_valid_doc()
    data["decisions"] = []
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R14"):
        load_deferred_findings(path, repo_root=tmp_path)


def test_r3_empty_decisions_word_list_with_no_findings_loads_successfully(tmp_path):
    """空词表 + 空 findings 列表：没有条目可被误判，安全通过。"""
    data = _minimal_valid_doc()
    data["decisions"] = []
    data["findings"] = []
    path = _write(tmp_path, data)
    cfg = load_deferred_findings(path, repo_root=tmp_path)
    assert cfg.decisions == ()
    assert cfg.findings == ()


# ---------------------------------------------------------------------------
# R4 — findings must be a list
# ---------------------------------------------------------------------------


def test_r4_findings_not_a_list_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["findings"] = "not-a-list"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R4"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# R5 — each finding entry must be a mapping with exactly the required keys
# ---------------------------------------------------------------------------


def test_r5_finding_entry_not_a_mapping_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["findings"] = ["just-a-string"]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R5"):
        load_deferred_findings(path, repo_root=tmp_path)


def test_r5_finding_entry_unknown_key_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["findings"][0]["unexpected_field"] = "oops"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R5"):
        load_deferred_findings(path, repo_root=tmp_path)


def test_r5_finding_entry_missing_key_rejected(tmp_path):
    data = _minimal_valid_doc()
    del data["findings"][0]["asset"]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R5"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# R6 — id must be a lowercase kebab slug
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", ["Bad_ID", "has space", "trailing-", "-leading", "UPPER"])
def test_r6_id_not_slug_rejected(tmp_path, bad_id):
    data = _minimal_valid_doc()
    data["findings"][0]["id"] = bad_id
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R6"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# R7 — id must be globally unique
# ---------------------------------------------------------------------------


def test_r7_duplicate_id_rejected(tmp_path):
    data = _minimal_valid_doc()
    second = dict(data["findings"][0])
    data["findings"].append(second)  # same id as the first entry
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R7"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# R8 — asset must be a non-empty string
# ---------------------------------------------------------------------------


def test_r8_empty_asset_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["findings"][0]["asset"] = "   "
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R8"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# R9 — detector must be a non-empty string
# ---------------------------------------------------------------------------


def test_r9_empty_detector_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["findings"][0]["detector"] = ""
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R9"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# R10 — detector that looks like a repo path must resolve to a real file
# ---------------------------------------------------------------------------


def test_r10_detector_repo_path_missing_file_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["findings"][0]["detector"] = "backend/scripts/does_not_exist_at_all.py"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R10"):
        load_deferred_findings(path, repo_root=tmp_path)


def test_r10_detector_repo_path_missing_file_with_line_suffix_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["findings"][0]["detector"] = "backend/scripts/does_not_exist_at_all.py:42"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R10"):
        load_deferred_findings(path, repo_root=tmp_path)


def test_r10_detector_repo_path_existing_file_loads_successfully(tmp_path):
    (tmp_path / "backend" / "scripts").mkdir(parents=True)
    (tmp_path / "backend" / "scripts" / "real_check.py").write_text("# fixture\n", encoding="utf-8")
    data = _minimal_valid_doc()
    data["findings"][0]["detector"] = "backend/scripts/real_check.py"
    path = _write(tmp_path, data)
    cfg = load_deferred_findings(path, repo_root=tmp_path)
    assert cfg.findings[0].detector == "backend/scripts/real_check.py"


def test_r10_detector_repo_path_existing_file_with_line_suffix_loads_successfully(tmp_path):
    (tmp_path / "backend" / "scripts").mkdir(parents=True)
    (tmp_path / "backend" / "scripts" / "real_check.py").write_text("# fixture\n", encoding="utf-8")
    data = _minimal_valid_doc()
    data["findings"][0]["detector"] = "backend/scripts/real_check.py:42"
    path = _write(tmp_path, data)
    cfg = load_deferred_findings(path, repo_root=tmp_path)
    assert cfg.findings[0].detector == "backend/scripts/real_check.py:42"


def test_r10_detector_without_slash_bypasses_path_check(tmp_path):
    """detector 没有 '/' 时不是"仓库路径引用" (可能是门 id/裸文件名) —— 即便
    repo_root 下什么都没有也不该被 R10 挡下来。"""
    data = _minimal_valid_doc()
    data["findings"][0]["detector"] = "some_gate_id_without_slash"
    path = _write(tmp_path, data)
    cfg = load_deferred_findings(path, repo_root=tmp_path)
    assert cfg.findings[0].detector == "some_gate_id_without_slash"


# ---------------------------------------------------------------------------
# R11 — escalates_when must be a non-empty string
# ---------------------------------------------------------------------------


def test_r11_empty_escalates_when_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["findings"][0]["escalates_when"] = ""
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R11"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# R12 — recorded must match YYYY-MM-DD
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_date", ["2026/09/16", "16-09-2026", "2026-9-16", "not-a-date"])
def test_r12_recorded_bad_format_rejected(tmp_path, bad_date):
    data = _minimal_valid_doc()
    data["findings"][0]["recorded"] = bad_date
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R12"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# R13 — recorded must be a real calendar date
# ---------------------------------------------------------------------------


def test_r13_recorded_invalid_calendar_date_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["findings"][0]["recorded"] = "2026-02-30"  # format-valid, date does not exist
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R13"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# R14 — decision must be in the decisions word list
# ---------------------------------------------------------------------------


def test_r14_decision_not_in_word_list_rejected(tmp_path):
    data = _minimal_valid_doc()
    data["findings"][0]["decision"] = "not_a_real_decision"
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"deferred_findings: R14"):
        load_deferred_findings(path, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# the real, checked-in file — no monkeypatch, no injected fixture
# ---------------------------------------------------------------------------


def test_real_deferred_findings_yaml_loads_via_default_path():
    """Reads the actual ``backend/config/deferred_findings.yaml`` from disk
    through the loader's own default path/repo_root — proves the checked-in
    file (and every repo-path detector it references) is itself valid, not
    just that the loader's rules work against synthetic fixtures."""
    cfg = load_deferred_findings()
    assert cfg.version == 1
    assert len(cfg.decisions) >= 1
    assert len(cfg.findings) >= 1
    ids = [f.id for f in cfg.findings]
    assert len(ids) == len(set(ids))
    for finding in cfg.findings:
        assert finding.decision in cfg.decisions


def test_default_path_points_at_real_checked_in_file():
    assert DEFAULT_PATH == _REPO_ROOT / "backend" / "config" / "deferred_findings.yaml"
    assert DEFAULT_PATH.exists()
