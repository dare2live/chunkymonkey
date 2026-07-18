from __future__ import annotations

from pathlib import Path

from scripts import check_calendar_usage as gate


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_calendar_import_does_not_hide_wall_clock_bypass(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    path = _write(
        tmp_path,
        "backend/services/bad_cutoff.py",
        "from services.calendar import latest_closed_or_raise\n"
        "from datetime import date\n"
        "cutoff = date.today()\n",
    )

    findings = gate.check_file(path)

    assert [(item["line"], item["kind"]) for item in findings] == [
        (3, "B1 wall-clock-as-latest")
    ]


def test_explicit_same_line_evidence_is_the_only_local_exemption(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    path = _write(
        tmp_path,
        "backend/services/legal_calendar_age.py",
        "from datetime import date\n"
        "age_anchor = date.today()  # rule-compliance: ok evidence=calendar-age metric, not a trading-session cutoff\n",
    )

    assert gate.check_file(path) == []


def test_empty_tracked_scan_fails_closed(monkeypatch, capsys):
    monkeypatch.setattr(gate, "_all_py", lambda: [])

    assert gate.main(["--strict"]) == 1
    assert "scan is empty" in capsys.readouterr().out
