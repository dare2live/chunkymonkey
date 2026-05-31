from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "session_handoff_audit.py"
    spec = importlib.util.spec_from_file_location("session_handoff_audit", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_topics_from_commits_matches_multiple_domains():
    audit = _load_module()
    topics = audit._extract_topics_from_commits(
        [
            {"hash": "abc12345", "subject": "fix 300616 wave plan_validator"},
            {"hash": "def67890", "subject": "front UI data_audit update"},
        ]
    )

    assert "300616 策略" in topics
    assert "计划验证" in topics
    assert "前端改动" in topics
    assert "数据审计" in topics
    assert "数据同步" in topics


def test_extract_numbers_from_commits_keeps_commit_hash_context():
    audit = _load_module()
    numbers = audit._extract_numbers_from_commits(
        [{"hash": "abc12345", "subject": "audit 9 PASS 30 FAIL score=1.23 win=55%"}]
    )

    assert "abc12345: 9 PASS" in numbers
    assert "abc12345: 30 FAIL" in numbers
    assert "abc12345: score=1.23" in numbers
    assert "abc12345: win=55%" in numbers


def test_check_coverage_reports_missing_topics():
    audit = _load_module()
    missing = audit._check_coverage(
        ["数据同步", "前端改动", "交易成本"],
        "本次记录 sync watermark 与 UI 方案。",
    )

    assert missing == ["交易成本"]


def test_check_new_files_documented_uses_path_tokens_and_skips_tests():
    audit = _load_module()
    undocumented = audit._check_new_files_documented(
        [
            "backend/services/new_profile_read.py",
            "backend/services/missing_profile_read.py",
            "backend/tests/test_new_profile_read.py",
        ],
        "已记录 backend/services/new_profile_read.py 作为画像 read model。",
    )

    assert undocumented == ["backend/services/missing_profile_read.py"]
