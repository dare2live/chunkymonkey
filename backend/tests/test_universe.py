"""Tests for backend/services/universe.py (ST filter added 2026-05-22)."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.universe import (
    is_active_a_share, is_st_stock, sql_where_active_a_share, sql_where_no_st,
    ACTIVE_A_SHARE_PREFIXES,
)


def _valid_policy_mapping() -> dict:
    return {
        "policy": {
            "id": "active_a_share_trading_universe",
            "version": 3,
        },
        "include": {
            "board_prefixes": ["60", "00", "30", "68"],
            "exchange_ids": ["SSE", "SZSE"],
            "venue_by_prefix": {
                "60": {"exchange_id": "SSE", "ts_suffix": "SH"},
                "68": {"exchange_id": "SSE", "ts_suffix": "SH"},
                "00": {"exchange_id": "SZSE", "ts_suffix": "SZ"},
                "30": {"exchange_id": "SZSE", "ts_suffix": "SZ"},
            },
        },
        "eligibility": {
            "rule": "traded_on_observation_date",
            "calendar_exchange_id": "SSE",
        },
        "exclude": {
            "excluded_boards": {
                "8": "北交所/新三板 (8x)",
                "4": "新三板 (4x)",
                "92": "北交所 (92x)",
            },
        },
        "limit_up_pct": {
            "60": 0.10,
            "00": 0.10,
            "30": 0.20,
            "68": 0.20,
        },
        "truth_source": {
            "nominal_kline": "tier0.market_data.nominal_ohlcv_daily",
            "st_membership": "tier0.security_identity.stock_st_daily",
            "trading_calendar": "tier0.reference.sse_trading_calendar_generation",
        },
        "current_enumeration": {
            "identity_source": "dim_active_a_stock",
            "st_name_patterns": ["ST", "*ST"],
            "no_recent_kline_days": 90,
        },
    }


def _write_policy(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_universe_policy_snapshot_is_immutable_and_complete():
    from services.universe import UNIVERSE_POLICY

    assert UNIVERSE_POLICY.policy_id == "active_a_share_trading_universe"
    assert UNIVERSE_POLICY.policy_version == 4
    assert UNIVERSE_POLICY.allowed_board_prefixes == ("60", "00", "30", "68")
    assert UNIVERSE_POLICY.allowed_exchange_ids == ("SSE", "SZSE")
    assert [
        (rule.board_prefix, rule.exchange_id, rule.ts_suffix)
        for rule in UNIVERSE_POLICY.venue_rules
    ] == [
        ("60", "SSE", "SH"),
        ("68", "SSE", "SH"),
        ("00", "SZSE", "SZ"),
        ("30", "SZSE", "SZ"),
    ]
    assert UNIVERSE_POLICY.eligibility_rule == "traded_on_observation_date"
    assert UNIVERSE_POLICY.calendar_exchange_id == "SSE"
    assert UNIVERSE_POLICY.nominal_kline_source == "tier0.market_data.nominal_ohlcv_daily"
    assert UNIVERSE_POLICY.st_membership_source == "tier0.security_identity.stock_st_daily"
    assert (
        UNIVERSE_POLICY.trading_calendar_source
        == "tier0.reference.sse_trading_calendar_generation"
    )
    assert len(UNIVERSE_POLICY.config_hash) == 64

    with pytest.raises(FrozenInstanceError):
        UNIVERSE_POLICY.policy_version = 4


def test_project_exchange_gate_rejects_bse_and_uses_injected_snapshot():
    from services.universe import (
        UNIVERSE_POLICY,
        UniverseContaminationError,
        assert_project_exchange_ids_allowed,
    )

    assert assert_project_exchange_ids_allowed(
        ["SSE", "SZSE"], policy=UNIVERSE_POLICY, context="margin"
    ) is True
    with pytest.raises(UniverseContaminationError, match=r"BSE.*margin"):
        assert_project_exchange_ids_allowed(
            ["SSE", "BSE"], policy=UNIVERSE_POLICY, context="margin"
        )
    for invalid in ([], ["SSE", "SSE"], ["sse"]):
        with pytest.raises(UniverseContaminationError):
            assert_project_exchange_ids_allowed(
                invalid, policy=UNIVERSE_POLICY, context="margin"
            )

    with pytest.raises(TypeError, match="load_universe_policy"):
        type(UNIVERSE_POLICY)()
    with pytest.raises(UniverseContaminationError, match="explicit factory-owned"):
        assert_project_exchange_ids_allowed(["SSE"], policy=None)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda raw: raw["include"].update(
                board_prefixes=["60", "00", "60"]
            ),
            "board_prefixes.*duplicate",
        ),
        (
            lambda raw: raw["include"].update(exchange_ids=[]),
            "exchange_ids.*non-empty",
        ),
        (
            lambda raw: raw["include"].update(exchange_ids=["SSE", "szse"]),
            "exchange_ids.*malformed",
        ),
        (
            lambda raw: raw["current_enumeration"].update(st_name_patterns=["ST", ""]),
            "st_name_patterns.*non-empty",
        ),
        (
            lambda raw: raw["current_enumeration"].update(no_recent_kline_days=0),
            "no_recent_kline_days.*positive integer",
        ),
        (
            lambda raw: raw["eligibility"].update(rule="recent_kline_window"),
            "eligibility.rule.*traded_on_observation_date",
        ),
        (
            lambda raw: raw["exclude"]["excluded_boards"].update({"60": "conflict"}),
            "board_prefixes overlaps.*excluded_boards",
        ),
        (
            lambda raw: raw["include"]["venue_by_prefix"]["60"].update(ts_suffix="SZH"),
            "ts_suffix.*malformed",
        ),
        (
            lambda raw: raw["truth_source"].update(st_membership=""),
            "st_membership.*non-empty",
        ),
        (
            lambda raw: raw["policy"].update(extra="unknown"),
            "policy unknown keys",
        ),
    ],
)
def test_load_universe_policy_rejects_invalid_values(tmp_path, mutate, match):
    from services.universe import UniverseDataError, load_universe_policy

    raw = _valid_policy_mapping()
    mutate(raw)
    path = _write_policy(tmp_path / "universe_rules.yaml", raw)

    with pytest.raises(UniverseDataError, match=match):
        load_universe_policy(path)


def test_universe_policy_hash_is_stable_for_semantically_equal_ordering(tmp_path):
    from services.universe import load_universe_policy

    raw = _valid_policy_mapping()
    first = load_universe_policy(_write_policy(tmp_path / "first.yaml", raw))

    reordered = deepcopy(raw)
    reordered["include"]["board_prefixes"].reverse()
    reordered["include"]["exchange_ids"].reverse()
    reordered["current_enumeration"]["st_name_patterns"].reverse()
    reordered["exclude"]["excluded_boards"] = dict(
        reversed(list(reordered["exclude"]["excluded_boards"].items()))
    )
    reordered["limit_up_pct"] = dict(
        reversed(list(reordered["limit_up_pct"].items()))
    )
    second = load_universe_policy(
        _write_policy(tmp_path / "second.yaml", reordered)
    )

    assert first.config_hash == second.config_hash

    changed_source = deepcopy(raw)
    changed_source["truth_source"]["st_membership"] = (
        "tier0.security_identity.stock_st_daily_v2"
    )
    third = load_universe_policy(
        _write_policy(tmp_path / "third.yaml", changed_source)
    )
    assert third.config_hash != first.config_hash

    changed_current_only = deepcopy(raw)
    changed_current_only["current_enumeration"]["identity_source"] = (
        "dim_active_a_stock_v2"
    )
    fourth = load_universe_policy(
        _write_policy(tmp_path / "fourth.yaml", changed_current_only)
    )
    assert fourth.config_hash == first.config_hash


def test_active_a_share_prefixes_match_spec():
    assert ACTIVE_A_SHARE_PREFIXES == ("60", "00", "30", "68")


def test_is_active_a_share_keep():
    assert is_active_a_share("600000")  # SSE 沪主板
    assert is_active_a_share("000001")  # SZSE 深主板
    assert is_active_a_share("300001")  # SZSE 创业板
    assert is_active_a_share("688001")  # SSE 科创板


def test_is_active_a_share_exclude():
    assert not is_active_a_share("830001")  # 北交所
    assert not is_active_a_share("400001")  # 老三板/新三板
    assert not is_active_a_share("510300")  # ETF
    assert not is_active_a_share("")
    assert not is_active_a_share("X")


def test_is_st_stock():
    assert is_st_stock("ST 股份")
    assert is_st_stock("*ST 退市风险")
    assert not is_st_stock("正常股")
    assert not is_st_stock(None)
    assert not is_st_stock("")
    assert not is_st_stock("XD 除息")


def test_sql_where_active_a_share():
    sql = sql_where_active_a_share("code")
    assert "SUBSTR(code, 1, 2) IN" in sql
    for p in ACTIVE_A_SHARE_PREFIXES:
        assert f"'{p}'" in sql


def test_sql_where_no_st():
    sql = sql_where_no_st("d.stock_name")
    assert "NOT (" in sql
    assert "d.stock_name LIKE 'ST%'" in sql
    assert "d.stock_name LIKE '*ST%'" in sql
    assert "IS NULL" in sql  # tolerate missing JOIN


def test_get_active_universe(tmp_path, monkeypatch):
    """Default universe = 沪深A + recent K；含 ST/*ST；踢 BJ/无K线."""
    import duckdb
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE dim_active_a_stock (stock_code VARCHAR, stock_name VARCHAR)")
    # Insert test stocks
    conn.execute("""
        INSERT INTO dim_active_a_stock VALUES
            ('600001', '正常A'), ('600002', '正常B'),
            ('ST600003', 'ST 测试'), ('600003', 'ST 测试'),
            ('*ST600004', '*ST 测试'), ('600004', '*ST 测试'),
            ('830001', '北交所A'),
            ('000001', '深主板A'), ('300001', '创业板A'), ('688001', '科创A')
    """)

    # 创建模拟 K 线关系, 让退市检查用测试数据 (不依赖真实 market.duckdb)
    conn.execute("CREATE TABLE price_kline_tdxhub (code VARCHAR, freq VARCHAR, date DATE)")
    conn.execute("""
        INSERT INTO price_kline_tdxhub VALUES
            ('600001', 'daily', CURRENT_DATE),
            ('000001', 'daily', CURRENT_DATE),
            ('300001', 'daily', CURRENT_DATE),
            ('688001', 'daily', CURRENT_DATE),
            ('600003', 'daily', CURRENT_DATE),
            ('600004', 'daily', CURRENT_DATE)
    """)
    conn.execute("CREATE VIEW v_price_kline_qfq AS SELECT * FROM price_kline_tdxhub")
    # 600002 没有 K 线 = 真退市

    from services.universe import get_active_universe
    universe = get_active_universe(conn, market_conn=conn)
    # Keep: 正常 + ST/*ST 沪深A；exclude: 无K线、BJ 前缀
    assert "600001" in universe
    assert "000001" in universe
    assert "300001" in universe
    assert "688001" in universe
    assert "600003" in universe  # ST remains 沪深A
    assert "600004" in universe  # *ST remains 沪深A
    assert "600002" not in universe  # delisted (no recent K)
    assert "830001" not in universe  # 北交所
    # Opt-out path still available for strategy-side narrowing
    narrowed = get_active_universe(conn, market_conn=conn, include_st=False)
    assert "600003" not in narrowed
    assert "600004" not in narrowed
    assert "600001" in narrowed
    conn.close()


def test_get_active_universe_excludes_index_not_in_dim_active(tmp_path):
    """2026-06-19 身份真相源交集防回退: K线含指数 benchmark (000300 沪深300, 00 前缀过前缀门)
    但不在 dim_active_a_stock (tushare stock_basic 真股清单) → 必被 universe 剔除。
    旧逻辑 K线∩前缀−ST 会让 000300 漏入 universe (根因; red→green)。"""
    import duckdb
    conn = duckdb.connect(str(tmp_path / "test.duckdb"))
    conn.execute("CREATE TABLE dim_active_a_stock (stock_code VARCHAR, stock_name VARCHAR)")
    conn.execute("INSERT INTO dim_active_a_stock VALUES ('600001', '正常A')")  # 真股清单无 000300 指数
    conn.execute("CREATE TABLE price_kline_tdxhub (code VARCHAR, freq VARCHAR, date DATE)")
    conn.execute("""
        INSERT INTO price_kline_tdxhub VALUES
            ('600001', 'daily', CURRENT_DATE),
            ('000300', 'daily', CURRENT_DATE)
    """)  # K线含真股 + 指数 benchmark (00 前缀)
    conn.execute("CREATE VIEW v_price_kline_qfq AS SELECT * FROM price_kline_tdxhub")
    from services.universe import get_active_universe
    universe = get_active_universe(conn, market_conn=conn)
    assert "600001" in universe
    assert "000300" not in universe  # 指数不在真股清单 → 身份交集剔除 (修复点)
    conn.close()


def test_get_active_universe_requires_market_truth_source(monkeypatch):
    from services.universe import UniverseDataError, get_active_universe

    def fail_market_conn():
        raise RuntimeError("missing market db")

    monkeypatch.setattr("services.market_db.get_market_conn", fail_market_conn)

    with pytest.raises(UniverseDataError, match="K-line market DB"):
        get_active_universe(include_st=True)


def test_get_active_universe_reads_st_mapping_from_reference(tmp_path, monkeypatch):
    """§9 拆库 (2026-06-27): ST/identity mapping 源从 conn(smartmoney) 迁 reference 库 dim_active_a_stock。

    旧契约 "conn 缺 dim → raise" 已变: 现经 security_master.active_codes/active_stock_name_map
    auto-fallback 读 reference (always 可用)。传缺 dim 的 conn 不再 raise — 落 reference 读 ST + identity
    交集 (test code '600001' 不在 reference 真股清单 → 被 identity 交集剔除, 返过滤集非异常)。

    hermetic: 自建 tmp reference 并 monkeypatch resolver.connect_ro, 不依赖真实 data/reference.duckdb
    (CI offline / 空 data 目录下该文件不存在 → 旧版直读真库 IOException)。
    """
    import duckdb
    from services.data_access import resolver
    from services.universe import get_active_universe

    # tmp reference: 真股清单 (含一只真股, 无 600001) → fallback 落它做 identity 交集
    ref_path = tmp_path / "reference.duckdb"
    rc = duckdb.connect(str(ref_path))
    rc.execute("CREATE TABLE dim_active_a_stock (stock_code VARCHAR, stock_name VARCHAR)")
    rc.execute("INSERT INTO dim_active_a_stock VALUES ('600519', '贵州茅台')")
    rc.close()

    def fake_connect_ro(alias):
        assert alias == "reference"  # 本测试只该 fallback reference 库
        return duckdb.connect(str(ref_path), read_only=True)

    monkeypatch.setattr(resolver, "connect_ro", fake_connect_ro)

    conn = duckdb.connect(str(tmp_path / "test.duckdb"))
    conn.execute("CREATE TABLE price_kline_tdxhub (code VARCHAR, freq VARCHAR, date DATE)")
    conn.execute("INSERT INTO price_kline_tdxhub VALUES ('600001', 'daily', CURRENT_DATE)")
    conn.execute("CREATE VIEW v_price_kline_qfq AS SELECT * FROM price_kline_tdxhub")

    # §9: 不再 raise (ST mapping 落 reference); 返 set, 600001 非真股经 identity 交集剔除
    result = get_active_universe(conn, market_conn=conn)
    assert isinstance(result, set)
    assert "600001" not in result

    conn.close()


# test_audit_contamination 2026-07-07 随 audit_strategy_universe_contamination() 一并退役
# (决策见 git log --grep dim_all_ever_listed): 被测函数已删, 见 services/universe.py。


# === 2026-06-17 universe 升交易日历级真相源: 硬验证器 + PIT ST 防回归 ===

def test_classify_exclusion_whitelist_passes():
    from services.universe import classify_exclusion
    for code in ("600000", "000001", "300274", "688981"):
        assert classify_exclusion(code) is None


def test_classify_exclusion_flags_excluded_boards():
    """断言具体排除原因子串(非仅非空)——排除原因是 config 驱动分类(见 universe_rules.yaml
    excluded_boards), 光验非空测不出分类映射本身错位(如误标成别的板块名仍然通过)。"""
    from services.universe import classify_exclusion
    assert "北交所" in classify_exclusion("920819")     # 北交所
    assert "北交所" in classify_exclusion("832000")     # 北交所/三板
    assert "新三板" in classify_exclusion("430139")     # 新三板
    assert "ETF" in classify_exclusion("159915")        # ETF
    assert "ETF" in classify_exclusion("510300")        # ETF


def test_assert_universe_clean_passes_whitelist():
    from services.universe import assert_universe_clean
    assert assert_universe_clean(["600000", "000001", "300274", "688981"]) is True


def test_assert_universe_clean_raises_on_contamination():
    from services.universe import assert_universe_clean, UniverseContaminationError
    with pytest.raises(UniverseContaminationError):
        assert_universe_clean(["600000", "920819"], context="test")
    # 报错应列出污染只数 + 板块
    try:
        assert_universe_clean(["600000", "920819", "159915"])
    except UniverseContaminationError as e:
        msg = str(e)
        assert "2" in msg  # 2 只排除股


def test_is_st_on_pit():
    from services.universe import is_st_on
    cal = {"600519": {"20240101", "20240102"}}
    assert is_st_on("600519", "20240101", cal) is True
    assert is_st_on("600519", "20240601", cal) is False  # PIT: 当日未 ST
    assert is_st_on("000001", "20240101", cal) is False  # 未在日历
