"""并入本仓的第三方包必须解析到仓内, 不许被机器上任何别的拷贝遮掉。

这三条断言守的是同一件事, 也是三次并入真正要解决的那件事: **同名包有第二份拷贝时,
拿到哪份取决于 sys.path 顺序, 而两条路径都不报错**。

实测过的两个形态:
  - tdxhub: 用户 site 里躺着一份 pip 装的, 比 sibling 落后 22 个 commit。裸 import 拿旧的、
    走 ensure_import_path 拿新的 —— 同一次修复在两条路径上结果不同且无任何信号。
  - aif10_scraper: pip install -e 指向 sibling, PYTHONPATH=backend 指向仓内。同样两个答案。

并入 backend/ 并卸掉那些安装后, 结构上只剩一份。这三条断言防的是它们被重新装回来。

本文件替代原 test_sibling_repos.py —— 三个包全部并入后 sibling_repos 机制没有生产
消费方了, 与它的 config 一并退役。
"""
import importlib
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _code_only(path: pathlib.Path) -> str:
    """去掉注释后的源码。

    这三条断言都是"某个写法不许出现在代码里"。第一版直接对整个文件做子串匹配, 结果被
    **断言自己的解释性注释**判红 —— 注释里引用了那个写法。检查问的是"文件里有没有这串
    字符", 想守的是"代码里还写不写这件事", 两者不是一回事。所以先剥注释再匹配。
    """
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        head = line.split("#", 1)[0]
        if head.strip():
            out.append(head)
    return "\n".join(out)


VENDORED = [
    ("marketdb", "marketdb.providers.dump"),
    ("aif10_scraper", "aif10_scraper.registry"),
    ("tdxhub", "tdxhub.protocol.parser.setup_commands"),
]


@pytest.mark.parametrize("pkg,submodule", VENDORED, ids=[v[0] for v in VENDORED])
def test_vendored_package_resolves_in_repo(pkg, submodule):
    mod = importlib.import_module(pkg)
    importlib.import_module(submodule)  # 子模块也得从同一棵树来
    resolved = pathlib.Path(mod.__file__).resolve()
    expected = (REPO_ROOT / "backend" / pkg / "__init__.py").resolve()
    assert resolved == expected, f"{pkg} 解析到了仓外: {resolved}"


def test_vendored_tdxhub_is_import_complete():
    """并入的是 tdxhub 整包减 utils/demjson.py —— 这条证明减掉的那个不影响任何 import。

    为什么需要这条: 这个包的边界**静态算不出来**, 当天连错三次, 每次都是静默的 ——
      (1) 真跑一次 import 取 sys.modules 差集, 漏掉 reader.py 方法体里的 6 个惰性 import;
      (2) AST 遍历, 漏掉 __init__.py 里 importlib.import_module('tdxhub.capabilities') 这种
          字符串形态;
      (3) 再遍历一次, 漏掉 protocol/reader/__init__.py 的 __getattr__ 懒加载 —— 而
          contrib/compat.py 顶层就 import 了它暴露的 TdxDailyBarReader。
    所以边界不靠推断靠这条: 逐个真 import, 少一个就红。
    """
    root = REPO_ROOT / "backend" / "tdxhub"
    modules = sorted(
        p for p in root.rglob("*.py") if p.name != "__main__.py"
    )
    assert len(modules) >= 80, f"并入的模块数看起来不对: {len(modules)}"
    failed = []
    for path in modules:
        name = str(path.relative_to(root.parent))[:-3].replace("/", ".")
        if name.endswith(".__init__"):
            name = name[: -len(".__init__")]
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — 要的就是把每一个失败都收齐再报
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failed, "并入的 tdxhub 不是 import-complete:\n" + "\n".join(failed)


def test_vendored_tdxhub_has_no_wall_clock_kline_path():
    """并入时删掉的那条 K 线路径不许回来。

    上游 quotes.py 的 ohlc() -> k() -> get_k_data() 用**本机当前时间**到目标日期的天数
    算 K 线 offset, 再按「非交易日大概是全年的 1/3」这两个常数(2.8 / 3.5)把日历天折成
    交易天。这撞两条红线: 交易日只从 services.calendar 取, 且不许拿比例猜节假日。
    本仓的未复权日 K 走 tdxhub_kline_recon.fetch_unadjusted_bars, count 是按协议算的精确值。

    这条断言防的是重新同步上游时把它带回来 —— 那种回归不会有任何运行时信号, 因为
    本仓不调它, 它只会静静躺在那儿等下一个人调。
    """
    quotes = _code_only(REPO_ROOT / "backend" / "tdxhub" / "quotes.py")
    for banned in ("def get_k_data", "def _date_distance", "def ohlc("):
        assert banned not in quotes, f"wall-clock K 线路径回来了: {banned}"


def test_vendored_tdxhub_ext_tick_parser_does_not_invent_a_date():
    """扩展市场分笔解析器不许拿本机今天给报文补日期。

    TDX 分笔报文只带 HH:MM:SS。上游用 ``datetime.combine(date.today(), ...)`` 补齐,
    于是每条历史分笔都被盖上运行当天的日期 —— 拿不知道的东西编了个值, 撞
    「缺失只能传播为缺失」。并入时改成 None, 时间三字段原样保留。
    """
    src = _code_only(
        REPO_ROOT / "backend" / "tdxhub" / "protocol" / "parser" / "ext"
        / "ex_get_transaction_data.py"
    )
    assert "datetime.date.today()" not in src
    assert "date = None" in src


def test_vendored_tdxhub_market_apis_have_no_hardcoded_default_symbol_or_date():
    """行情方法不许给 symbol / date 留字面量默认值。

    上游签名里有 ``symbol: str = '000001'``、``date: str = '20170209'``、
    ``date: str = '20191023'`` 这类默认值: 调用方漏传一个参数, 拿回来的是另一只股票、
    另一天的数据, 且没有任何报错。并入时全部改成必填(必要处用 ``*`` 变成关键字必填)。
    """
    quotes = _code_only(REPO_ROOT / "backend" / "tdxhub" / "quotes.py")
    for banned in ("symbol: str = '", "date: str = '"):
        assert banned not in quotes, f"行情方法又有了字面量默认值: {banned}"
