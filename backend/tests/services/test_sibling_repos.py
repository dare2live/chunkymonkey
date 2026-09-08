from pathlib import Path

import pytest

from services.data_sources.sibling_repos import (
    ensure_import_path,
    load_sibling_repos,
)


def test_sibling_repos_yaml_names_the_remaining_stock_checkouts():
    """2026-09-08: fuyao 出列 —— 它的使用面(marketdb 6 模块/521 行)已并入 backend/marketdb。

    登记表现在只剩仍在 sibling 形态的三个。这个集合断言是**契约**不是状态: 每并入一个
    就该逼人来改这一行, 否则"登记表里还写着但目录已经没了"会静默存在。
    """
    catalog = load_sibling_repos()
    assert set(catalog.repos) == {"tdxhub", "tushare"}
    assert catalog.require("tdxhub").required is True
    assert catalog.require("tushare").required is False


def test_sibling_repos_prefers_stock_root_then_nested(tmp_path):
    """两处都有同名目录时取 stock 根那份 —— 原用例拿 miaoxiang 当样本, 它已并入。

    换成 tdxhub 不只是换个名字: 这条优先级正是"同一个包有两份拷贝"的来源, 而
    tdxhub 恰好是本机唯一还有第二份的那个(用户 site 里一份 pip 装的)。
    """
    stock = tmp_path / "stock"
    repo = stock / "chunkymonkey"
    nested = repo / "tdxhub"
    sibling = stock / "tdxhub"
    for root, tag in ((nested, "nested"), (sibling, "sibling")):
        (root / "tdxhub").mkdir(parents=True)
        (root / "tdxhub" / "quotes.py").write_text(f"# {tag}\n", encoding="utf-8")

    catalog = load_sibling_repos(repo_root=repo, stock_root=stock)
    assert catalog.path_for("tdxhub") == sibling
    assert catalog.is_present("tdxhub") is True


def test_ensure_import_path_adds_declared_subdirectory(tmp_path):
    """pythonpath 声明的是子目录(而非仓根)时, 加进 sys.path 的必须是那个子目录。

    原用例拿 fuyao 当样本(它的 pythonpath 是 'python')。fuyao 并入后, 在册的三个仓
    pythonpath 全是 '.' —— 于是这条分支**没有活样本可测**。用合成 catalog 保住它:
    测的是机制不是某个具体仓, 本来就不该绑在某个 alias 上。
    """
    import sys

    stock = tmp_path / "stock"
    repo = stock / "chunkymonkey"
    demo = stock / "demo"
    pkg = demo / "src" / "demopkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    cfg = tmp_path / "sibling_repos.yaml"
    cfg.write_text(
        "version: 1\nrepos:\n  demo:\n    dirname: demo\n    role: test_only\n"
        "    required: true\n    pythonpath:\n      - src\n"
        "    required_marker: src/demopkg/__init__.py\n",
        encoding="utf-8",
    )
    catalog = load_sibling_repos(cfg, repo_root=repo, stock_root=stock)
    before = list(sys.path)
    try:
        root = ensure_import_path("demo", repos=catalog, strict=True)
        assert root == demo
        assert str(demo / "src") in sys.path
        assert str(demo) not in sys.path, "声明的是 src, 不该把仓根也加进去"
    finally:
        sys.path[:] = before


def test_ensure_import_path_raises_when_required_missing(tmp_path):
    stock = tmp_path / "stock"
    repo = stock / "chunkymonkey"
    repo.mkdir(parents=True)
    catalog = load_sibling_repos(repo_root=repo, stock_root=stock)
    with pytest.raises(FileNotFoundError, match="tdxhub"):
        ensure_import_path("tdxhub", repos=catalog, strict=True)


def test_vendored_aif10_scraper_resolves_in_repo_not_from_editable_install():
    """aif10_scraper 必须解析到 backend/aif10_scraper。

    2026-09-08 并入前它是 `pip install -e` 装的(指向 sibling), 于是"包在哪"有两个答案。
    并入同批卸掉了 editable 安装 —— 这条断言防的是它被重新装回来: 一旦 editable 复活,
    仓内代码与 sibling 里的旧版本谁生效就又取决于 sys.path 顺序了。
    """
    import aif10_scraper
    from aif10_scraper.registry import get_report  # noqa: F401

    repo_root = Path(__file__).resolve().parents[3]
    resolved = Path(aif10_scraper.__file__).resolve()
    assert resolved == (repo_root / "backend" / "aif10_scraper" / "__init__.py").resolve(), (
        f"aif10_scraper 解析到了仓外: {resolved}"
    )


def test_vendored_marketdb_resolves_in_repo_not_from_site_packages():
    """marketdb 必须解析到 backend/marketdb, 不许被机器上任何别的拷贝遮掉。

    替换掉原来的 test_live_fuyao_sibling_is_the_official_checkout —— 那条测的是
    sibling 目录还在不在, 2026-09-08 并入后主语消失。

    这条守的是并入真正要解决的那件事: 同名包有第二份拷贝时, 拿到哪份取决于代码路径。
    实证(同日, tdxhub): 本机 venv 是 --system-site-packages, 用户 site 里躺着一份 pip 装的
    tdxhub, 比 sibling 落后 22 个 commit; 裸 import 拿到旧的、走 ensure_import_path 拿到新的,
    **同一次修复在两条路径上结果不同且无任何信号**。marketdb 并入 backend/ 后
    PYTHONPATH=backend 先解析, 结构上不会再有第二份 —— 这条断言把它钉住。
    """
    import marketdb
    from marketdb.providers.dump import DumpDownloader  # noqa: F401

    repo_root = Path(__file__).resolve().parents[3]
    resolved = Path(marketdb.__file__).resolve()
    assert resolved == (repo_root / "backend" / "marketdb" / "__init__.py").resolve(), (
        f"marketdb 解析到了仓外: {resolved}"
    )

    dump = repo_root / "backend" / "marketdb" / "providers" / "dump.py"
    text = dump.read_text(encoding="utf-8")
    assert "/api/dump/market-dumps/" in text
    assert "daily-k" in text
