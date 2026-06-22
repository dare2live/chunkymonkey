"""SERVE 读层 P1 门自测 — 防孤儿 + red→green 可证 (mythos §14: 门不会红=废门)。

不依赖真 DB / 真 dossier: 单测每道门函数对 (干净输入→空, 脏输入→非空), 物理保证门能红。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_MOD_PATH = _REPO / "backend" / "scripts" / "check_serve_read_layer.py"
_spec = importlib.util.spec_from_file_location("check_serve_read_layer", _MOD_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_strip_drops_docstring_and_comments():
    src = '"""PIT: ann_date <= as_of 说明."""\nx = 1  # FROM raw_foo\n'
    out = mod._strip_comments_and_docstrings(src)
    assert "ann_date <= as_of" not in out  # docstring 叙述不该残留 → 防误判
    assert "FROM raw_foo" not in out       # 行注释不该残留
    assert "x = 1" in out


def test_lineage_complete_flags_missing_chain(tmp_path, monkeypatch):
    bad = tmp_path / "data_access.yaml"
    bad.write_text(
        "entities:\n  good:\n    db: market\n    table: t\n    layer: L0\n"
        "    vendor: tushare\n    asof_col: trade_date\n    code_col: ts_code\n"
        "  broken:\n    db: market\n    table: t2\n",  # 缺 layer/vendor/asof_col/code_col
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "DATA_ACCESS_YAML", bad)
    viol = mod.door_lineage_complete()
    assert any("broken" in v for v in viol)      # 断链 entity 被抓
    assert not any("'good'" in v for v in viol)  # 完整 entity 不误报


def test_real_data_access_yaml_lineage_complete():
    # 真 config: 21 entity 声明链应齐全 (绿基线)
    assert mod.door_lineage_complete() == []


def test_doors_red_green_on_injection(tmp_path, monkeypatch):
    clean = "import x\nrows = da.get('kline', conn=c)\n"
    dirty = clean + 'q = "SELECT a FROM raw_tushare_moneyflow"\nc.execute(q)\n'
    f = tmp_path / "dossier.py"

    f.write_text(clean, encoding="utf-8")
    monkeypatch.setattr(mod, "DOSSIER", f)
    assert mod.door_read_no_inline_table() == []   # 干净=绿
    assert mod.door_read_no_self_asof() == []

    f.write_text(dirty, encoding="utf-8")
    assert mod.door_read_no_inline_table()         # 注入 FROM raw_ → 红
    assert mod.door_read_no_self_asof()            # 注入 .execute( → 红


def test_preflight_wired_real():
    assert mod.door_preflight_wired() == []  # generic.py 真调 resolver.preflight
