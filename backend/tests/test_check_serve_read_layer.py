"""SERVE 读层门自测 — 防孤儿 + red→green 可证 (mythos §14: 门不会红=废门)。

不依赖真 DB: 单测每道门函数对 (干净输入→空, 脏输入→非空), 物理保证门能红。
2026-07-08 收口: D1(原 D1/D2, dossier 专属伪绿门)改为全量非成员消费者内联裸查扫描,
历史 red→green 结论已固化为本测试，不再依赖单独的 analysis owner 文档。
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
    # 真 config: 全部 entity 声明链应齐全 (绿基线)
    assert mod.door_lineage_complete() == []


def test_no_consumer_bypass_red_green_on_injection(tmp_path, monkeypatch):
    """D1 (2026-07-08 收口, 替代原 dossier 专属 D1/D2): 非成员消费者内联裸查=红, 干净=绿。"""
    services_dir = tmp_path / "services"
    scripts_dir = tmp_path / "scripts"
    services_dir.mkdir()
    scripts_dir.mkdir()
    members_yaml = tmp_path / "data_module_members.yaml"
    members_yaml.write_text("member_service_files: []\nmember_dirs: []\n", encoding="utf-8")

    monkeypatch.setattr(mod, "SERVICES_DIR", services_dir)
    monkeypatch.setattr(mod, "SCRIPTS_DIR", scripts_dir)
    monkeypatch.setattr(mod, "MEMBERS_YAML", members_yaml)
    monkeypatch.setattr(mod, "REPO", tmp_path)

    clean = "import x\nrows = da.get('kline', conn=c)\n"
    dirty = clean + 'q = "SELECT a FROM raw_tushare_moneyflow"\nduckdb.connect(q)\n'
    f = services_dir / "some_consumer.py"

    f.write_text(clean, encoding="utf-8")
    assert mod.door_no_consumer_bypass() == []   # 干净=绿

    f.write_text(dirty, encoding="utf-8")
    viol = mod.door_no_consumer_bypass()
    assert viol and "some_consumer.py" in viol[0]   # 非成员内联裸查 → 红


def test_no_consumer_bypass_exempts_registered_members(tmp_path, monkeypatch):
    """登记进 data_module_members.yaml 的 builder 允许内联裸查, 不误伤(区分 builder vs 消费者是本门核心设计)。"""
    services_dir = tmp_path / "services"
    scripts_dir = tmp_path / "scripts"
    services_dir.mkdir()
    scripts_dir.mkdir()
    members_yaml = tmp_path / "data_module_members.yaml"
    members_yaml.write_text(
        "member_service_files:\n  - some_builder.py\nmember_dirs: []\n", encoding="utf-8"
    )

    monkeypatch.setattr(mod, "SERVICES_DIR", services_dir)
    monkeypatch.setattr(mod, "SCRIPTS_DIR", scripts_dir)
    monkeypatch.setattr(mod, "MEMBERS_YAML", members_yaml)
    monkeypatch.setattr(mod, "REPO", tmp_path)

    f = services_dir / "some_builder.py"
    f.write_text('q = "SELECT a FROM raw_tushare_moneyflow"\nduckdb.connect(q)\n', encoding="utf-8")
    assert mod.door_no_consumer_bypass() == []   # 已登记的 builder 不算违规


def test_preflight_wired_real():
    assert mod.door_preflight_wired() == []  # generic.py 真调 resolver.preflight


def test_router_no_ad_hoc_raw_red_green_on_injection(tmp_path, monkeypatch):
    """D5 (S6): new router inline raw_* = red; clean / serve-exempt = green."""
    routers_dir = tmp_path / "routers"
    routers_dir.mkdir()
    monkeypatch.setattr(mod, "ROUTERS_DIR", routers_dir)
    monkeypatch.setattr(mod, "REPO", tmp_path)

    clean = routers_dir / "clean_api.py"
    clean.write_text(
        "from services.data_access import DataAccess\nrows = DataAccess().get('kline_qfq')\n",
        encoding="utf-8",
    )
    assert mod.door_router_no_ad_hoc_raw() == []

    dirty = routers_dir / "dirty_api.py"
    dirty.write_text(
        'q = "SELECT a FROM tr.raw_tushare_moneyflow WHERE trade_date = ?"\n',
        encoding="utf-8",
    )
    viol = mod.door_router_no_ad_hoc_raw()
    assert viol and "dirty_api.py" in viol[0]

    dirty.write_text(
        "# serve-exempt: tracked residual\n"
        'q = "SELECT a FROM tr.raw_tushare_moneyflow WHERE trade_date = ?"\n',
        encoding="utf-8",
    )
    assert mod.door_router_no_ad_hoc_raw() == []


def test_router_no_ad_hoc_raw_live_surface_green():
    """Live routers: market_pulse grandfathered via serve-exempt; others clean."""
    assert mod.door_router_no_ad_hoc_raw() == []
