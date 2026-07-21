"""pre-knife audit wrapper — thin moth+codegraph checklist (eng_gov §15)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _load_mod():
    path = REPO / "backend" / "scripts" / "pre_knife_audit.py"
    spec = importlib.util.spec_from_file_location("pre_knife_audit", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pre_knife_rejects_empty_name() -> None:
    mod = _load_mod()
    assert mod.main([]) == 2
    assert mod.main([""]) == 2


def test_pre_knife_runs_moth_then_codegraph(monkeypatch) -> None:
    mod = _load_mod()
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str]) -> int:
        calls.append(cmd)
        return 0

    monkeypatch.setattr(mod, "_run", _fake_run)
    assert mod.main(["raw_tushare_stock_basic"]) == 0
    assert calls == [
        ["moth", "coupling", "--repo", ".", "--impact", "raw_tushare_stock_basic"],
        ["codegraph", "explore", "raw_tushare_stock_basic callers"],
    ]


def test_pre_knife_fails_closed_on_tool_error(monkeypatch) -> None:
    mod = _load_mod()

    def _fake_run(cmd: list[str]) -> int:
        return 1 if cmd[0] == "codegraph" else 0

    monkeypatch.setattr(mod, "_run", _fake_run)
    assert mod.main(["widget"]) == 1
