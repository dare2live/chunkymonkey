from pathlib import Path

import pytest

from services.data_sources.sibling_repos import (
    ensure_import_path,
    load_sibling_repos,
)


def test_sibling_repos_yaml_names_the_four_stock_checkouts():
    catalog = load_sibling_repos()
    assert set(catalog.repos) == {"miaoxiang", "tdxhub", "fuyao", "tushare"}
    assert catalog.require("fuyao").upstream.endswith("Financial-API.git")
    assert catalog.require("fuyao").pythonpath == ("python",)
    assert catalog.require("miaoxiang").required is True
    assert catalog.require("tushare").required is False


def test_sibling_repos_prefers_stock_root_then_nested(tmp_path):
    stock = tmp_path / "stock"
    repo = stock / "chunkymonkey"
    nested = repo / "miaoxiang"
    sibling = stock / "miaoxiang"
    nested.mkdir(parents=True)
    (nested / "aif10_scraper").mkdir()
    (nested / "aif10_scraper" / "client.py").write_text("# nested\n", encoding="utf-8")
    sibling.mkdir()
    (sibling / "aif10_scraper").mkdir()
    (sibling / "aif10_scraper" / "client.py").write_text("# sibling\n", encoding="utf-8")

    catalog = load_sibling_repos(repo_root=repo, stock_root=stock)
    assert catalog.path_for("miaoxiang") == sibling
    assert catalog.is_present("miaoxiang") is True


def test_ensure_import_path_adds_fuyao_python_dir(tmp_path):
    import sys

    stock = tmp_path / "stock"
    repo = stock / "chunkymonkey"
    fuyao = stock / "fuyao"
    dump = fuyao / "python" / "marketdb" / "providers"
    dump.mkdir(parents=True)
    (dump / "dump.py").write_text("# dump\n", encoding="utf-8")
    catalog = load_sibling_repos(repo_root=repo, stock_root=stock)
    before = list(sys.path)
    try:
        root = ensure_import_path("fuyao", repos=catalog, strict=True)
        assert root == fuyao
        assert str(fuyao / "python") in sys.path
    finally:
        sys.path[:] = before


def test_ensure_import_path_raises_when_required_missing(tmp_path):
    stock = tmp_path / "stock"
    repo = stock / "chunkymonkey"
    repo.mkdir(parents=True)
    catalog = load_sibling_repos(repo_root=repo, stock_root=stock)
    with pytest.raises(FileNotFoundError, match="fuyao"):
        ensure_import_path("fuyao", repos=catalog, strict=True)


def test_live_fuyao_sibling_is_the_official_checkout():
    catalog = load_sibling_repos()
    if not catalog.is_present("fuyao"):
        pytest.skip("fuyao sibling not cloned")
    dump = catalog.path_for("fuyao") / "python" / "marketdb" / "providers" / "dump.py"
    assert dump.is_file()
    text = dump.read_text(encoding="utf-8")
    assert "/api/dump/market-dumps/" in text
    assert "daily-k" in text
