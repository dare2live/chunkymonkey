from __future__ import annotations

from pathlib import Path
import re

from services.holder_capital_role import classify_capital_role, load_holder_capital_role
from services.holder_research_class import classify_holder_name
from services.research_identity import annotate_holder, annotate_seat
from services.seat_research_class import classify_seat_name, load_seat_research_class


def test_foreign_own_funds_not_partnership_or_insurer() -> None:
    hit = classify_capital_role("挪威中央银行-自有资金")
    assert "foreign_own_funds" in hit.tags
    assert "own_funds_account" in hit.tags
    assert "domestic_insurer_own" not in hit.tags
    assert "foreign_discretionary" in hit.presets


def test_taiwan_insurer_qfii_is_foreign_own() -> None:
    hit = classify_capital_role("南山人寿保险股份有限公司-自有资金")
    assert "foreign_own_funds" in hit.tags
    assert "domestic_insurer_own" not in hit.tags


def test_mainland_insurer_own_is_not_foreign() -> None:
    hit = classify_capital_role("中国平安人寿保险股份有限公司-自有资金")
    assert "own_funds_account" in hit.tags
    assert "domestic_insurer_own" in hit.tags
    assert "foreign_own_funds" not in hit.tags
    assert hit.presets == ()


def test_partnership_own_funds_name_is_untagged() -> None:
    hit = classify_capital_role("平阳心悦自有资金投资合伙企业(有限合伙)")
    assert hit.tags == ()


def test_client_funds_suffix() -> None:
    hit = classify_capital_role("中国国际金融香港资产管理有限公司-客户资金")
    assert hit.tags == ("client_funds_account",)
    assert "foreign_own_funds" not in hit.tags


def test_bnp_without_hyphen_is_foreign_own() -> None:
    hit = classify_capital_role("法国巴黎银行自有资金")
    assert "foreign_own_funds" in hit.tags


def test_anonymous_and_connect_seats() -> None:
    inst = classify_seat_name("机构专用")
    assert inst.tags == ("inst_anonymous",)
    sh = classify_seat_name("沪股通专用")
    sz = classify_seat_name("深股通专用")
    assert sh.tags == ("connect_northbound",)
    assert sz.tags == ("connect_northbound",)
    assert "trend_seat_daily" in sh.presets


def test_hot_money_folk_alias() -> None:
    hit = classify_seat_name("国盛证券有限责任公司宁波桑田路证券营业部")
    assert "hot_money" in hit.tags
    assert hit.alias == "章盟主"
    assert hit.alias_kind == "folk"
    zhao = classify_seat_name("东方财富证券股份有限公司拉萨团结路第二证券营业部")
    assert zhao.alias == "赵老哥"


def test_broker_branch_is_not_auto_hot_money() -> None:
    hit = classify_seat_name("中国国际金融股份有限公司上海分公司")
    assert hit.tags == ()
    assert hit.alias is None


def test_annotate_holder_keeps_layers_named() -> None:
    overlay = annotate_holder("中央汇金投资有限责任公司")
    assert overlay["trend_layers"]["national_team_stabilizer"] is True
    assert overlay["trend_layers"]["foreign_own_funds"] is False
    nssf = annotate_holder("全国社保基金四一三组合")
    assert nssf["trend_layers"]["national_team_stabilizer"] is False
    assert classify_holder_name("全国社保基金四一三组合").presets == ("national_team_wind",)


def test_annotate_seat_display() -> None:
    overlay = annotate_seat("机构专用")
    seat = overlay["seat_research_class"]
    assert seat["tags"] == ["inst_anonymous"]
    assert seat["namespace"] == "seat_research_class"


def test_matcher_sources_have_no_cjk() -> None:
    repo = Path(__file__).resolve().parents[3]
    files = [
        repo / "backend/services/research_facet.py",
        repo / "backend/services/holder_capital_role.py",
        repo / "backend/services/seat_research_class.py",
        repo / "backend/services/research_identity.py",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert re.search(r"[\u4e00-\u9fff]", text) is None, path


def test_capital_and_seat_policies_load() -> None:
    capital = load_holder_capital_role()
    seat = load_seat_research_class()
    assert capital.namespace == "holder_capital_role"
    assert seat.namespace == "seat_research_class"
    assert "foreign_own_funds" in capital.tag_ids()
