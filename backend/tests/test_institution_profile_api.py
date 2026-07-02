"""institution_profile 读侧 API 单测 (合成 feature_store, mock _ro_conn)。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services import institution_profile as ip


@pytest.fixture
def mem(monkeypatch):
    c = duck_mem()
    c.executescript("""
        CREATE TABLE mart_inst_profile (holder TEXT, holder_type TEXT, n_closed INT,
            median_alpha DOUBLE, avg_alpha DOUBLE, win_rate_alpha DOUBLE, median_ret DOUBLE,
            avg_hold_days DOUBLE, low_sample BOOLEAN);
        CREATE TABLE mart_inst_profile_dim (holder TEXT, dim_type TEXT, dim_value TEXT,
            n_closed INT, median_alpha DOUBLE, win_rate_alpha DOUBLE, low_sample BOOLEAN);
        CREATE TABLE fact_inst_episode (holder TEXT, stock TEXT, open_date TEXT,
            open_notice TEXT, close_date TEXT, status TEXT, ret_c1 DOUBLE, alpha_c1 DOUBLE,
            n_adds INT, n_trims INT, sw_l1_at_open TEXT, seeded BOOLEAN, is_passive BOOLEAN,
            holder_type TEXT);
    """)
    c.execute("INSERT INTO mart_inst_profile VALUES ('牛散A','个人',50,0.15,0.2,0.7,0.12,200,false)")
    c.execute("INSERT INTO mart_inst_profile VALUES ('小散B','个人',3,0.5,0.5,1.0,0.5,100,true)")
    c.execute("INSERT INTO mart_inst_profile_dim VALUES ('牛散A','industry_pit','煤炭',5,0.6,1.0,true)")
    c.execute("INSERT INTO fact_inst_episode VALUES "
              "('牛散A','600000','20990101','20990102',NULL,'holding',NULL,NULL,0,0,'银行',false,false,'个人')")
    monkeypatch.setattr(ip, "_ro_conn", lambda: c)
    # _ro_conn 每函数会 close — 内存库 close 后 fixture 失效, 用 no-op close 包装
    c.close_real = c.close
    c.close = lambda: None
    yield c
    c.close_real()


def test_list_profiles_filters_low_sample(mem):
    out = ip.list_profiles(min_episodes=10)
    assert [p["holder"] for p in out] == ["牛散A"]  # 小散B n=3 被 min_episodes 滤掉


def test_list_profiles_order_by_whitelist(mem):
    with pytest.raises(ValueError, match="order_by"):
        ip.list_profiles(order_by="1; DROP TABLE x")  # SQL 注入防线


def test_get_profile_full_contract(mem):
    p = ip.get_profile("牛散A")
    assert p["n_closed"] == 50 and len(p["dims"]) == 1 and len(p["episodes"]) == 1
    assert ip.get_profile("不存在") is None


def test_recent_signals_star_holder_only(mem):
    sigs = ip.recent_signals(days=36500, min_holder_episodes=10)  # 20990101 远未来, 窗口放大覆盖
    assert len(sigs) == 1 and sigs[0]["holder"] == "牛散A"
    assert sigs[0]["holder_median_alpha"] == pytest.approx(0.15)
