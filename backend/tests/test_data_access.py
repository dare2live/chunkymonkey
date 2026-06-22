"""SERVE 读层 (services.data_access) 单测 — 数据模块顶层设计 v2 §4。

覆盖 4 不变量: PIT asof≤t (#1) / read_only 不变 / 单一声明 (未知entity raise) / preflight schema自校验 +
血缘 provenance 信封 + 主键/日期口径归一。in-memory conn 注入 (不碰真库)。
"""
from conftest import duck_mem

from services.data_access import DataAccess, DataResult
from services.data_access.spec import load_registry


def _mem_with_kline():
    c = duck_mem()
    c.executescript(
        "CREATE TABLE price_kline_qfq_tushare "
        "(code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL);"
    )
    c.executemany(
        "INSERT INTO price_kline_qfq_tushare VALUES (?,?,?,?,?,?,?,?)",
        [
            ("600519", "2026-05-04", 10, 11, 9, 10.5, 100, 1050),
            ("600519", "2026-05-05", 11, 12, 10, 11.5, 110, 1265),
            ("600519", "2026-05-06", 12, 13, 11, 12.5, 120, 1500),  # 未来, as_of 之后
            ("000001", "2026-05-05", 20, 21, 19, 20.5, 200, 4100),
        ],
    )
    return c


def _mem_with_moneyflow():
    c = duck_mem()
    c.executescript(
        "CREATE TABLE raw_tushare_moneyflow (ts_code TEXT, trade_date TEXT, net_mf_amount REAL);"
    )
    c.executemany(
        "INSERT INTO raw_tushare_moneyflow VALUES (?,?,?)",
        [("600519.SH", "20260504", 1234.5), ("600519.SH", "20260505", -678.9)],
    )
    return c


def test_pit_asof_cutoff_excludes_future():
    """不变量#1 PIT: as_of=2026-05-05 → 只见 ≤t, 排除 05-06 未来行。"""
    da = DataAccess()
    res = da.get("kline_qfq", codes=["600519"], as_of="2026-05-05", conn=_mem_with_kline())
    dates = [r["date"] for r in res.rows]
    assert dates == ["2026-05-04", "2026-05-05"], dates  # 05-06 被 PIT 门挡掉


def test_code_filter_and_start():
    da = DataAccess()
    res = da.get("kline_qfq", codes=["600519"], start="2026-05-05", as_of="2026-05-06",
                 conn=_mem_with_kline())
    assert {r["code"] for r in res.rows} == {"600519"}
    assert [r["date"] for r in res.rows] == ["2026-05-05", "2026-05-06"]


def test_ts_code_and_date_normalized_on_output():
    """不变量#1 统一主键: ts_code 列 → 输出归一 6 位; YYYYMMDD asof → 输出 ISO。"""
    da = DataAccess()
    res = da.get("moneyflow", codes=["600519"], as_of="2026-05-05", conn=_mem_with_moneyflow())
    assert all(r["ts_code"] == "600519" for r in res.rows)         # 600519.SH → 600519
    assert [r["trade_date"] for r in res.rows] == ["2026-05-04", "2026-05-05"]  # YYYYMMDD → ISO


def test_provenance_envelope():
    """血缘: get 返回带 source/vendor/asof_anchor 信封 (声明链一环)。"""
    da = DataAccess()
    res = da.get("kline_qfq", codes=["600519"], as_of="2026-05-05", conn=_mem_with_kline())
    p = res.provenance
    assert p["source_entity"] == "kline_qfq"
    assert p["source_table"] == "market.price_kline_qfq_tushare"
    assert p["vendor"] == "tushare"
    assert p["asof_anchor"] == "date"
    assert p["as_of"] == "2026-05-05"
    assert p["compute_fn"] is None   # 厂商现成 raw entity, 非派生


def test_unknown_entity_raises():
    """不变量#4 单一声明: 未声明 entity = raise (不静默)。"""
    da = DataAccess()
    try:
        da.get("nonexistent_entity", conn=_mem_with_kline())
        assert False, "未知 entity 应 raise"
    except ValueError as e:
        assert "未知 data_access entity" in str(e)


def test_preflight_schema_drift_raises():
    """preflight 自校验: 声明列不在表 = raise (防 config↔物理 schema 漂移静默失真)。"""
    c = duck_mem()
    # 建缺列的表 (少 amount) → preflight 应抓
    c.executescript("CREATE TABLE price_kline_qfq_tushare (code TEXT, date TEXT, close REAL);")
    da = DataAccess()
    try:
        da.get("kline_qfq", codes=["600519"], as_of="2026-05-05", conn=c)
        assert False, "schema 漂移应 raise"
    except ValueError as e:
        assert "preflight" in str(e) and "kline_qfq" in str(e)


def test_index_ts_passthrough_no_conversion():
    """不变量#1: code_input=ts_passthrough 指数码 000300.SH 直用不转 (code_to_ts_code 股票前缀规则对指数无效)。"""
    c = duck_mem()
    c.executescript("CREATE TABLE raw_tushare_index_daily (ts_code TEXT, trade_date TEXT, close REAL);")
    c.executemany("INSERT INTO raw_tushare_index_daily VALUES (?,?,?)",
                  [("000300.SH", "20260504", 3800.0), ("000300.SH", "20260505", 3820.0)])
    da = DataAccess()
    res = da.get("index_daily", codes=["000300.SH"], as_of="2026-05-05", conn=c)
    assert len(res.rows) == 2
    assert res.rows[0]["ts_code"] == "000300.SH"   # passthrough: 指数码保持不归一 6 位
    assert res.rows[-1]["trade_date"] == "2026-05-05"  # asof 仍归一 ISO


def test_registry_loads_core_entities():
    reg = load_registry()
    for e in ("kline_qfq", "moneyflow", "valuation", "index_daily"):
        assert e in reg.entities, f"{e} 未注册"
