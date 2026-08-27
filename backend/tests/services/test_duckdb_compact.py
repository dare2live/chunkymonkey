"""feature_store / DuckDB compact helper after DROP rebuilds."""
from __future__ import annotations


def test_maybe_compact_skips_below_threshold(monkeypatch):
    from services import duckdb_compact as dc

    monkeypatch.setattr(dc, "free_block_pct", lambda _alias: 3.0)
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("compact must not run below threshold")

    monkeypatch.setattr(dc, "_load_db_compact", _boom)
    assert dc.maybe_compact_alias("feature_store") == 0
    assert called["n"] == 0


def test_maybe_compact_runs_at_threshold(monkeypatch):
    from services import duckdb_compact as dc

    monkeypatch.setattr(dc, "free_block_pct", lambda _alias: 62.5)
    compact_mod = type(
        "M",
        (),
        {"run": staticmethod(lambda alias, execute=False, drop_bak=False: 0)},
    )
    monkeypatch.setattr(dc, "_load_db_compact", lambda: compact_mod)
    assert dc.maybe_compact_alias("feature_store") == 0


def test_maybe_compact_always_ignores_pct(monkeypatch):
    from services import duckdb_compact as dc

    monkeypatch.setattr(dc, "free_block_pct", lambda _alias: 0.01)
    seen = {}

    def _run(alias, execute=False, drop_bak=False):
        seen["alias"] = alias
        seen["execute"] = execute
        return 0

    monkeypatch.setattr(
        dc, "_load_db_compact", lambda: type("M", (), {"run": staticmethod(_run)})()
    )
    assert dc.maybe_compact_alias("feature_store", always=True) == 0
    assert seen == {"alias": "feature_store", "execute": True}


def test_institution_and_rally_rebuild_call_compact():
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    inst = (backend / "services" / "institution_profile.py").read_text(encoding="utf-8")
    rally = (backend / "services" / "rally_gt.py").read_text(encoding="utf-8")
    assert "maybe_compact_alias" in inst
    assert "maybe_compact_alias" in rally
    assert 'maybe_compact_alias("feature_store"' in inst
    assert 'maybe_compact_alias("feature_store"' in rally
